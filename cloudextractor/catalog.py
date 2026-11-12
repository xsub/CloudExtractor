from collections import Counter, defaultdict
from collections.abc import Iterator
from datetime import datetime
from operator import itemgetter
from posixpath import basename, splitext
from zoneinfo import ZoneInfo


VIDEOS = {".mov", ".mp4", ".m4v", ".webm", ".avi", ".mts", ".m2ts", ".mkv", ".3gp", ".3g2", ".mpeg", ".mpg"}
PHOTOS = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".avif", ".gif", ".webp", ".tif", ".tiff", ".jxl",
          ".bmp", ".dng", ".cr2", ".cr3", ".nef", ".arw", ".raf", ".orf", ".rw2", ".pef"}


def media_type(path: str) -> str:
    suffix = splitext(path)[1].lower()
    if suffix in PHOTOS:
        return "image"
    if suffix in VIDEOS:
        return "video"
    return "other"


def matches_filter(kind: str, media: str) -> bool:
    return kind == media or (media == "both" and kind in ("image", "video"))


def folder_name(path: str) -> str:
    return path.rpartition("/")[0] or "."


class Catalog:
    def __init__(self, files: dict[str, int | None], timestamps: dict[str, int] | None = None):
        self.files = files
        self.timestamps = timestamps if timestamps is not None else {}
        self.types = {path: media_type(path) for path in files}
        self.paths = sorted((path for path, kind in self.types.items() if kind != "other"), reverse=True)
        self.sizes: Counter[str] = Counter()
        self.folders: dict[str, Counter[str]] = defaultdict(Counter)
        for path, size in self.files.items():
            if size is not None:
                kind = self.types[path]
                self.sizes[kind] += size
                self.folders[folder_name(path)][kind] += size

    def filtered(self, media: str, folder: str) -> list[str]:
        return [path for path in self.paths if matches_filter(self.types[path], media)
                and (not folder or folder_name(path) == folder)]

    def dated_paths(self, paths: list[str], start: float, end: float) -> Iterator[tuple[str, int]]:
        for path in paths:
            timestamp = self.timestamps.get(path)
            if timestamp and start <= timestamp <= end:
                yield path, timestamp

    def daily_counts(self, paths: list[str], start: float, end: float, zone: ZoneInfo) -> dict[str, Counter[str]]:
        days = defaultdict(Counter)
        for path, timestamp in self.dated_paths(paths, start, end):
            day = datetime.fromtimestamp(timestamp, zone).date().isoformat()
            days[day][self.types[path]] += 1
        return dict(days)

    def ordered(self, paths: list[str], field: str, descending: bool, group_sizes: dict[str, int] | None = None) -> list[str]:
        if field == "date":
            if not self.timestamps:
                return paths
            key = lambda path: self.timestamps.get(path) or None
        elif field == "size":
            key = lambda path: group_sizes[path] if group_sizes is not None and path in group_sizes else self.files[path]
        elif field == "type":
            key = self.types.__getitem__
        elif field == "format":
            key = lambda path: splitext(path)[1].casefold()
        else:
            key = lambda path: basename(path).casefold()

        measured = []
        missing = []
        for path in paths:
            value = key(path)
            if value is None:
                missing.append(path)
            else:
                measured.append((path, value))
        measured.sort(key=itemgetter(1), reverse=descending)
        return [path for path, _ in measured] + missing

    def grouped(self, paths: list[str], interval: int) -> tuple[list[str], dict[str, list[str]]]:
        timeline = sorted(paths, key=lambda path: (self.timestamps.get(path, float("-inf")), path), reverse=True)
        covers = []
        groups = {}
        previous: dict[str, int] = {}
        members: dict[str, list[str]] = {}
        for path in timeline:
            timestamp = self.timestamps.get(path)
            kind = self.types[path]
            if kind not in ("image", "video") or timestamp is None or timestamp == 0:
                covers.append(path)
                continue

            if kind in previous and previous[kind] - timestamp <= interval:
                group = members[kind]
                group.append(path)
                groups[group[0]] = group
            else:
                covers.append(path)
                members[kind] = [path]
            previous[kind] = timestamp
        return covers, groups

    def summary(self, media: str, folder: str) -> dict[str, int]:
        sizes = self.folders.get(folder, {}) if folder else self.sizes
        return {"bytes": sum(self.sizes.values()), "filtered": self.filtered_size(sizes, media)}

    @staticmethod
    def filtered_size(sizes, media: str) -> int:
        return sum(size for kind, size in sizes.items() if matches_filter(kind, media))
