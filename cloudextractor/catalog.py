from collections import Counter, defaultdict
from posixpath import splitext


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

    def grouped(self, paths: list[str], interval: int) -> tuple[list[str], dict[str, list[str]]]:
        timeline = sorted(paths, key=lambda path: (self.timestamps.get(path, float("-inf")), path), reverse=True)
        covers = []
        groups = {}
        previous = None
        members = []
        for path in timeline:
            timestamp = self.timestamps.get(path)
            if self.types[path] != "image" or timestamp is None or timestamp == 0:
                covers.append(path)
                continue

            if previous is not None and previous - timestamp <= interval:
                members.append(path)
                groups[members[0]] = members
            else:
                covers.append(path)
                members = [path]
            previous = timestamp
        return covers, groups

    def summary(self, media: str, folder: str) -> dict[str, int]:
        sizes = self.folders.get(folder, {}) if folder else self.sizes
        return {"bytes": sum(self.sizes.values()), "filtered": self.filtered_size(sizes, media)}

    @staticmethod
    def filtered_size(sizes, media: str) -> int:
        return sum(size for kind, size in sizes.items() if matches_filter(kind, media))
