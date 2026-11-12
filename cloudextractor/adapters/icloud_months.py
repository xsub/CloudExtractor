from datetime import timezone
import sys

from icloudpd import base
from icloudpd.cli import cli

from cloudextractor.download_dates import parse_months


def run(months: tuple[str, ...], args: list[str]) -> int:
    selected = {(int(month[:4]), int(month[5:])) for month in months}
    original_filter = base.where_builder

    def month_filter(logger, skip_videos, first, last, skip_photos, filename_builder, photo):
        if not photo.has_asset_date:
            return False
        captured = photo.created.astimezone(timezone.utc)
        if (captured.year, captured.month) not in selected:
            return False
        return original_filter(logger, skip_videos, first, last, skip_photos, filename_builder, photo)

    # The pinned icloudpd has a single date interval in its CLI. Its run loop builds
    # the filter here before enumerating the library. Keep its downloader in Copy mode.
    argv = sys.argv
    try:
        base.where_builder = month_filter
        sys.argv = [argv[0], *args]
        return cli()
    finally:
        base.where_builder = original_filter
        sys.argv = argv


if __name__ == "__main__":
    sys.exit(run(parse_months(sys.argv[1].split(",")), sys.argv[2:]))
