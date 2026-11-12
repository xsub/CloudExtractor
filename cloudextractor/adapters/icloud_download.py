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
from cloudextractor.adapters.icloud_inventory import asset_versions, connect
from cloudextractor.download_dates import parse_months


LOGGER = logging.getLogger(__name__)
Bounds = tuple[datetime, datetime]


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
        raise ValueError("iCloud did not return an item at the requested rank")

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
                raise ValueError("iCloud capture date ranks changed while locating the selected dates")
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
            raise ValueError("iCloud capture date ranks changed while reading the selected dates")
        yield asset
        expected += 1
        if expected == end:
            return
    raise ValueError("iCloud returned an incomplete list for the download")


def download_library(client, config, bounds: list[Bounds], progress: bool) -> int:
    album = client.photos.all
    library_count = len(album)
    count = library_count if config.recent is None else min(config.recent, library_count)
    windows = rank_windows(album, count, bounds) if bounds else [(0, count, None)]
    total = sum(end - start for start, end, _ in windows)
    LOGGER.info("Downloading %d selected photos and videos to %s", total, config.directory)

    clean = build_filename_cleaner(config.keep_unicode_in_filenames)

    def filename_builder(asset):
        return build_filename_with_policies(config.file_match_policy, clean, asset)

    archive = Archive(Path(config.directory))
    next_update = monotonic()
    listed = 0
    ok = True
    for start, end, window in windows:
        for asset in window_assets(album, start, end, window):
            try:
                resources = asset_versions(asset, config, filename_builder(asset))
                for path, size, version in resources:
                    ok &= archive.save(client, asset, path, size, version, filename_builder)
            except (KeyError, TypeError, ValueError, OSError, OverflowError) as error:
                LOGGER.error("Could not archive an iCloud item: %s", error)
                ok = False
            listed += 1
            if progress and (listed == total or monotonic() >= next_update):
                print(f"CloudExtractor listing {listed}/{total}", flush=True)
                next_update = monotonic() + 1
    if bounds and len(album) != library_count:
        raise ValueError("The iCloud library changed during the download. Run the selected dates again.")
    return 0 if ok else 1


def main(args: list[str]) -> int:
    months = parse_months(args[1].split(",")) if args[:1] == ["--months"] else ()
    _, (config,) = parse(args[2:] if months else args)
    bounds = month_bounds(months)
    if config.skip_created_before is not None:
        bounds = [(config.skip_created_before, config.skip_created_after)]
    progress = os.environ.get("CLOUDEXTRACTOR_PROGRESS") == "1"
    logging.basicConfig(format="%(asctime)s %(levelname)-8s %(message)s", datefmt="%Y-%m-%d %H:%M:%S",
                        level=logging.DEBUG if progress else logging.INFO)
    # requests logs include signed media URLs at DEBUG.
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    try:
        return download_library(connect(config), config, bounds, progress)
    except PyiCloudFailedLoginException:
        LOGGER.error("iCloud authentication is required")
    except (KeyError, ValueError, OSError, RuntimeError) as error:
        LOGGER.error("iCloud download failed: %s", error)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
