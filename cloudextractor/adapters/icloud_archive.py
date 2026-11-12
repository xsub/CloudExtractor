import base64
import filecmp
import hashlib
import logging
import os
from pathlib import Path
import stat

from icloudpd.download import download_media
from pyicloud_ipd.asset_version import add_suffix_to_filename
from pyicloud_ipd.version_size import AssetVersionSize


# CloudKit's type-1 file checksum. https://icps.steiler.dev/dev/api/#file-checksum
CHECKSUM_SALT = b"com.apple.XattrObjectSalt\0com.apple.DataObjectSalt\0"
LOGGER = logging.getLogger(__name__)


def checksum_matches(path: Path, checksum: bytes) -> bool:
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, lambda: hashlib.sha1(CHECKSUM_SALT))
    return digest.digest() == checksum[1:]


def entry(path: Path) -> os.stat_result | None:
    try:
        return path.lstat()
    except FileNotFoundError:
        return None


class Archive:
    def __init__(self, root: Path, *, match_size: bool = False):
        self.root = root
        self.match_size = match_size
        self.numbered = {}

    def versions(self, target: Path) -> dict[int, Path]:
        directory = target.parent
        if directory not in self.numbered:
            by_name = {}
            if directory.is_dir():
                for path in directory.iterdir():
                    stem, sep, number = path.stem.rpartition("_")
                    if sep and len(number) == 3 and number.isascii() and number.isdecimal() and number != "000":
                        by_name.setdefault(stem + path.suffix, {})[int(number)] = path
            self.numbered[directory] = by_name
        return self.numbered[directory].setdefault(target.name, {})

    def destination(self, target, size, legacy, matches):
        versions = self.versions(target)
        candidates = [target, *versions.values()]
        if legacy:
            candidates.append(Path(add_suffix_to_filename("-original", str(target))))
        occupied = set()
        for path in candidates:
            info = entry(path)
            if info is None:
                continue
            occupied.add(path)
            if stat.S_ISREG(info.st_mode) and info.st_size == size and (self.match_size or matches(path)):
                return None

        if target not in occupied:
            return target
        for number in range(1, 1000):
            candidate = target.with_stem(f"{target.stem}_{number:03}")
            if candidate not in occupied:
                return candidate
        raise ValueError(f"All suffixes _001–_999 are in use for {target}")

    def save(self, client, asset, relative, kind, version, filename_builder) -> bool:
        if relative.is_absolute() or ".." in relative.parts or not relative.name:
            raise ValueError("Invalid media path from iCloud")
        target = self.root / relative
        for parent in relative.parents:
            if (self.root / parent).is_symlink():
                raise ValueError(f"Use a real archive directory at {self.root / parent}")

        if type(version.size) is not int or version.size < 0:
            raise ValueError("Invalid media size from iCloud")
        checksum = base64.b64decode(version.checksum, validate=True)
        if not checksum:
            raise ValueError("Invalid media checksum from iCloud")
        known_checksum = len(checksum) == 21 and checksum[0] == 1
        legacy = kind == AssetVersionSize.ORIGINAL
        checksum_name = base64.b32encode(checksum).decode()
        staged = target.with_name(f".cloudextractor-{checksum_name}.ready.part")

        def matches(path: Path) -> bool:
            if known_checksum:
                return checksum_matches(path, checksum)
            return staged.is_file() and filecmp.cmp(path, staged, shallow=False)

        staged_info = entry(staged)
        if staged_info and not stat.S_ISREG(staged_info.st_mode):
            raise ValueError(f"Invalid staging path at {staged}")
        if staged_info and (staged_info.st_size != version.size or
                            (known_checksum and not checksum_matches(staged, checksum))):
            staged.unlink()

        destination = self.destination(target, version.size, legacy, matches) if known_checksum or self.match_size else target
        if destination is None:
            staged.unlink(missing_ok=True)
            return True

        if not staged.exists():
            partial = entry(target.with_name(f"{checksum_name}.part"))
            if partial and not stat.S_ISREG(partial.st_mode):
                raise ValueError(f"Invalid partial download for {target}")
            LOGGER.debug("Downloading %s...", destination)
            if not download_media(LOGGER, False, client, asset, str(staged), version, kind, filename_builder):
                return False
            if staged.stat().st_size != version.size or (known_checksum and not checksum_matches(staged, checksum)):
                staged.unlink()
                LOGGER.error("Size or checksum mismatch for %s", target)
                return False

        if not known_checksum and not self.match_size:
            destination = self.destination(target, version.size, legacy, matches)
        # Linking a complete file into place publishes it atomically and fails if
        # another process created the destination in the meantime.
        while destination is not None:
            try:
                os.link(staged, destination)
            except FileExistsError:
                self.numbered.pop(target.parent, None)
                destination = self.destination(target, version.size, legacy, matches)
            else:
                if destination != target:
                    number = int(destination.stem.rpartition("_")[2])
                    self.versions(target)[number] = destination
                LOGGER.info("Downloaded %s", destination)
                break
        staged.unlink()
        return True
