from argparse import ArgumentParser
from calendar import monthrange
from datetime import datetime, time, timedelta, timezone
from functools import lru_cache
import logging
import os
from pathlib import Path
import sys
from time import monotonic

from icloudpd.base import build_filename_cleaner, build_filename_with_policies
from icloudpd.cli import parse
from pyicloud_ipd.exceptions import PyiCloudFailedLoginException

from cloudextractor.adapters.icloud_archive import Archive
from cloudextractor.adapters.icloud_inventory import ICloudTermsRequired, asset_versions, connect
from cloudextractor.download_dates import parse_months


LOGGER = logging.getLogger(__name__)
Bounds = tuple[datetime, datetime]


class RanksChanged(ValueError):
    pass


RANK_ATTEMPTS = 3


def month_bounds(months: tuple[str, ...]) -> list[Bounds]:
    bounds = []
    for value in months:
        year, month = map(int, value.split("/"))
        first = datetime(year, month, 1, tzinfo=timezone.utc)
        last = datetime.combine(first.replace(day=monthrange(year, month)[1]), time.max, timezone.utc)
        if bounds and first - bounds[-1][1] == timedelta(microseconds=1):
            bounds[-1] = (bounds[-1][0], last)
        else:
            bounds.append((first, last))
    return bounds


def rank_windows(album, count: int, bounds: list[Bounds]):
    @lru_cache(maxsize=None)
    def date_at(rank: int) -> datetime | None:
        album.offset = rank
        for record in album.photos_request().json()["records"]:
            if record["recordType"] == "CPLAsset":
                value = record["fields"].get("assetDate", {}).get("value")
                return None if value is None else datetime.fromtimestamp(value / 1000, timezone.utc)
        raise RanksChanged("iCloud did not return an item at the requested rank")

    def find_boundary(date: datetime, *, include_equal: bool) -> int:
        low, high = 0, count
        while low < high:
            middle = (low + high) // 2
            captured = date_at(middle)
            if captured is not None and (captured > date or (not include_equal and captured == date)):
                low = middle + 1
            else:
                high = middle
        return low

    # The main iCloud album has a newest-first capture date index. Probe its ranks
    # to find each window, then read only the pages that intersect those windows.
    original_page_size = album.page_size
    album.page_size = 1
    try:
        windows = []
        for first, last in reversed(bounds):
            start = find_boundary(last, include_equal=True)
            end = find_boundary(first, include_equal=False)
            if start > end or (windows and start < windows[-1][1]):
                raise RanksChanged("iCloud capture date ranks changed while locating the selected dates")
            windows.append((start, end, (first, last)))
        return windows
    finally:
        album.page_size = original_page_size


def window_assets(album, start: int, end: int, bounds: Bounds | None):
    if start == end:
        return
    album.offset = start
    expected = start
    for asset in album:
        if album.offset != expected:
            break
        if bounds and (not asset.has_asset_date or not bounds[0] <= asset.created <= bounds[1]):
            raise RanksChanged("iCloud capture date ranks changed while reading the selected dates")
        yield asset
        expected += 1
        if expected == end:
            return
    raise RanksChanged("iCloud returned an incomplete list for the download")


def read_windows(album, count: int, bounds: list[Bounds]):
    for attempt in range(RANK_ATTEMPTS):
        try:
            return count, rank_windows(album, count, bounds)
        except RanksChanged:
            if attempt == RANK_ATTEMPTS - 1:
                raise
            count = len(album)


def pending_assets(album, windows, seen):
    for (start, end, window), handled in zip(windows, seen, strict=True):
        if window and len(handled) == end - start:
            continue
        for asset in window_assets(album, start, end, window):
            if window:
                identifier = asset._asset_record["recordName"]
                if identifier in handled:
                    continue
                if len(handled) == end - start:
                    raise ValueError("New items appeared in the selected iCloud date range during download.")
                handled.add(identifier)
            yield asset
        if window and len(handled) < end - start:
            raise RanksChanged("iCloud repeated items while reading the selected dates")


def download_library(client, config, bounds: list[Bounds], progress: bool, *, match_size: bool = False) -> int:
    album = client.photos.all
    library_count = len(album)
    count = library_count if config.recent is None else min(config.recent, library_count)
    if bounds:
        library_count, windows = read_windows(album, library_count, bounds)
    else:
        windows = [(0, count, None)]
    window_sizes = [end - start for start, end, _ in windows]
    total = sum(window_sizes)
    LOGGER.info("Downloading %d selected photos and videos to %s", total, config.directory)

    def refresh_windows(library_size: int):
        current_count, refreshed = read_windows(album, library_size, bounds)
        for expected, (start, end, _) in zip(window_sizes, refreshed, strict=True):
            if end - start != expected:
                raise ValueError(
                    f"Selected iCloud date range changed during download ({expected} → {end - start} items)."
                )
        return current_count, refreshed

    clean = build_filename_cleaner(config.keep_unicode_in_filenames)

    def filename_builder(asset):
        return build_filename_with_policies(config.file_match_policy, clean, asset)

    archive = Archive(Path(config.directory), match_size=match_size)
    next_update = monotonic()
    listed = 0
    ok = True
    seen = [set() for _ in windows]
    for attempt in range(RANK_ATTEMPTS):
        try:
            for asset in pending_assets(album, windows, seen):
                try:
                    resources = asset_versions(asset, config, filename_builder(asset))
                    for path, size, version in resources:
                        ok &= archive.save(client, asset, path, size, version, filename_builder)
                except (KeyError, TypeError, ValueError, OSError, OverflowError) as error:
                    LOGGER.error("Could not archive an iCloud item (%s): %s", type(error).__name__, error)
                    ok = False
                listed += 1
                if progress and (listed == total or monotonic() >= next_update):
                    print(f"CloudExtractor listing {listed}/{total}", flush=True)
                    next_update = monotonic() + 1
            break
        except RanksChanged:
            if not bounds or attempt == RANK_ATTEMPTS - 1:
                raise
            # Inserting an item before a window can repeat records across pages.
            # Refresh offsets and process only the records we haven't seen.
            library_count, windows = refresh_windows(len(album))

    if bounds and (current_count := len(album)) != library_count:
        refresh_windows(current_count)
    return 0 if ok else 1


def main(args: list[str]) -> int:
    parser = ArgumentParser(add_help=False, allow_abbrev=False)
    parser.add_argument("--months")
    parser.add_argument("--match-size", action="store_true")
    options, icloud_args = parser.parse_known_args(args)
    months = parse_months(options.months.split(",")) if options.months else ()
    _, (config,) = parse(icloud_args)
    bounds = month_bounds(months)
    if config.skip_created_before is not None:
        bounds = [(config.skip_created_before, config.skip_created_after)]
    progress = os.environ.get("CLOUDEXTRACTOR_PROGRESS") == "1"
    logging.basicConfig(format="%(asctime)s %(levelname)-8s %(message)s", datefmt="%Y-%m-%d %H:%M:%S",
                        level=logging.DEBUG if progress else logging.INFO)
    # requests logs include signed media URLs at DEBUG.
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    try:
        return download_library(connect(config), config, bounds, progress, match_size=options.match_size)
    except ICloudTermsRequired as error:
        LOGGER.error("%s", error)
    except PyiCloudFailedLoginException as error:
        LOGGER.error("iCloud authentication is required: %s", error)
    except Exception as error:
        LOGGER.error("iCloud download failed (%s): %s", type(error).__name__, error)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
