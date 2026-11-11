from functools import lru_cache
from io import BytesIO
import os
from pathlib import Path
import subprocess
from threading import BoundedSemaphore, Lock
from time import monotonic

from PIL import Image, ImageOps
from pillow_heif import register_heif_opener


register_heif_opener()
VIDEOS = {".mov", ".mp4", ".m4v", ".webm", ".avi", ".mts", ".3gp", ".mpeg", ".mpg"}
PHOTOS = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".avif", ".gif", ".webp", ".tif", ".tiff",
          ".bmp", ".dng", ".cr2", ".cr3", ".nef", ".arw", ".raf", ".orf", ".rw2", ".pef"}
DECODERS = BoundedSemaphore(2)


class Gallery:
    def __init__(self, archive: Path):
        self.archive = archive
        self.lock = Lock()
        self.snapshots: dict[str, tuple[float, bool, list[str], set[str]]] = {}

    def items(self, account: str, active: bool) -> tuple[list[str], set[str]]:
        with self.lock:
            cached = self.snapshots.get(account)
            if cached and cached[1] == active and monotonic() - cached[0] < (5 if active else 30):
                return cached[2], cached[3]
            root = self.archive / account
            paths = []
            if root.is_dir() and not root.is_symlink():
                for directory, folders, files in os.walk(root):
                    folders[:] = [name for name in folders if not name.startswith(".")]
                    relative = Path(directory).relative_to(root)
                    paths.extend((relative / name).as_posix() for name in files
                                 if not name.startswith(".") and Path(name).suffix.lower() in PHOTOS | VIDEOS)
            paths.sort(reverse=True)
            present = set(paths)
            self.snapshots[account] = monotonic(), active, paths, present
            return paths, present

    def media(self, account: str, relative: str) -> Path:
        root = self.archive / account
        path = Path(relative)
        if root.is_symlink() or path.is_absolute() or not path.parts or any(
            part.startswith(".") for part in path.parts
        ) or path.suffix.lower() not in PHOTOS | VIDEOS:
            raise FileNotFoundError

        current = root
        for part in path.parts:
            current /= part
            if current.is_symlink():
                raise FileNotFoundError
        if not current.is_file():
            raise FileNotFoundError
        return current


@lru_cache(maxsize=96)
def jpeg_preview(path: Path, modified_ns: int, size: int) -> bytes:
    with DECODERS:
        if path.suffix.lower() in VIDEOS:
            return subprocess.run(
                ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-threads", "1", "-protocol_whitelist", "file,pipe",
                 "-i", str(path), "-frames:v", "1", "-vf", f"scale={size}:{size}:force_original_aspect_ratio=decrease",
                 "-f", "image2pipe", "-vcodec", "mjpeg", "pipe:1"],
                capture_output=True, check=True, timeout=15,
            ).stdout
        with Image.open(path) as image:
            image = ImageOps.exif_transpose(image)
            image.thumbnail((size, size))
            output = BytesIO()
            image.convert("RGB").save(output, "JPEG", quality=85)
            return output.getvalue()
