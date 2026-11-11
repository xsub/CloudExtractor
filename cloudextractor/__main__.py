import argparse
import fcntl
import os
from pathlib import Path
import sys
from tempfile import TemporaryFile

from cloudextractor.adapters import icloud


ADAPTERS = {"icloud": icloud.command}
ARCHIVE = Path("/archive")
STATE = Path("/state")
ARCHIVE_MARKER = ".cloudextractor-archive"


def check_archive(archive: Path) -> None:
    marker = archive / ARCHIVE_MARKER
    if marker.is_symlink() or not marker.is_file():
        raise ValueError(f"Missing {marker}. Check the archive mount on the Linux host.")

    try:
        with TemporaryFile(dir=archive) as probe:
            probe.write(b"CloudExtractor\n")
    except OSError as error:
        raise ValueError(f"Cannot write to the archive {archive}: {error}") from error


def main() -> int:
    parser = argparse.ArgumentParser(description="Archive cloud photos on a mounted share.")
    parser.add_argument("action", nargs="?", choices=("run", "auth", "check"), default="run")
    args = parser.parse_args()

    source = os.environ.get("CLOUDEXTRACTOR_SOURCE", "icloud")
    if source not in ADAPTERS:
        parser.error(f"Unknown source {source!r}. Available sources: {', '.join(ADAPTERS)}")

    os.umask(0o077)
    try:
        if not STATE.is_dir() or STATE.stat().st_mode & 0o077:
            raise ValueError(f"Mount a private state directory at {STATE} with mode 0700.")
        if not os.access(STATE, os.R_OK | os.W_OK | os.X_OK):
            raise ValueError(f"State directory {STATE} must be accessible to the container user.")

        if args.action != "auth":
            check_archive(ARCHIVE)
        if args.action == "check":
            print(f"Archive {ARCHIVE} is marked and writable.")
            return 0

        if args.action == "auth" and not sys.stdin.isatty():
            raise ValueError("Run authentication from an interactive terminal.")

        command = ADAPTERS[source](args.action, ARCHIVE, STATE)
        with (STATE / ".cloudextractor.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise ValueError("CloudExtractor is already using this state directory.") from error

            if args.action == "run":
                with open(os.devnull, "rb") as empty_input:
                    os.dup2(empty_input.fileno(), sys.stdin.fileno())

            # Keep the lock until the adapter process exits, including on interruption.
            os.set_inheritable(lock.fileno(), True)
            os.execvp(command[0], command)
    except (OSError, ValueError) as error:
        print(f"CloudExtractor: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
