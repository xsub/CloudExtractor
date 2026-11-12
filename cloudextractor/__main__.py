import argparse
import fcntl
import os
from pathlib import Path
import sys
from tempfile import TemporaryFile

from cloudextractor.adapters import icloud
from cloudextractor.download_dates import DateRange


ADAPTERS = {"icloud": icloud.prepare}
ARCHIVE = Path("/archive")
STATE = Path("/state")
ARCHIVE_MARKER = ".cloudextractor-archive"


def create_directory(directory: Path) -> None:
    if directory.is_symlink():
        raise ValueError(f"Use a real directory at {directory}.")
    directory.mkdir(mode=0o700, exist_ok=True)


def check_state(state: Path) -> None:
    if not state.is_dir() or state.stat().st_mode & 0o077:
        raise ValueError(f"Use a private state directory at {state} with mode 0700.")
    if not os.access(state, os.R_OK | os.W_OK | os.X_OK):
        raise ValueError(f"State directory {state} must be accessible to the container user.")


def check_archive(root: Path, archive: Path) -> None:
    marker = root / ARCHIVE_MARKER
    if marker.is_symlink() or not marker.is_file():
        raise ValueError(f"Missing {marker}. Check the archive mount on the Linux host.")

    try:
        create_directory(archive)
        with TemporaryFile(dir=archive) as probe:
            probe.write(b"CloudExtractor\n")
    except OSError as error:
        raise ValueError(f"Cannot write to the archive {archive}: {error}") from error


def main() -> int:
    parser = argparse.ArgumentParser(description="Archive cloud photos on a mounted share.")
    parser.add_argument("action", nargs="?", choices=("run", "auth", "check", "scan", "inventory", "ui"), default="run")
    parser.add_argument("--date-from", help="First capture date to download (YYYY-MM-DD)")
    parser.add_argument("--date-to", help="Last capture date to download, inclusive (YYYY-MM-DD)")
    parser.add_argument("--date-zone", help="IANA time zone of the capture date range (default: UTC)")
    args = parser.parse_args()
    date_range = None
    if any(value is not None for value in (args.date_from, args.date_to, args.date_zone)):
        if args.action != "run" or args.date_from is None or args.date_to is None:
            parser.error("--date-from and --date-to are required together and only supported for run")
        date_range = DateRange(args.date_from, args.date_to, args.date_zone or "UTC")

    source = os.environ.get("CLOUDEXTRACTOR_SOURCE", "icloud")
    if source not in ADAPTERS:
        parser.error(f"Unknown source {source!r}. Available sources: {', '.join(ADAPTERS)}")

    os.umask(0o077)
    try:
        check_state(STATE)
        if args.action == "ui":
            from cloudextractor.dashboard import serve

            serve(ARCHIVE, STATE)
            return 0
        if args.action == "auth" and not sys.stdin.isatty():
            raise ValueError("Run authentication from an interactive terminal.")

        archive, state, command = ADAPTERS[source](args.action, ARCHIVE, STATE, date_range)
        if args.action not in ("auth", "inventory"):
            check_archive(ARCHIVE, archive)
        create_directory(state)
        check_state(state)

        if args.action == "check":
            print("Archive is marked and writable. State directory is accessible.")
            return 0

        with (state / ".cloudextractor.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise ValueError("CloudExtractor is already using this state directory.") from error

            if args.action != "auth":
                with open(os.devnull, "rb") as empty_input:
                    os.dup2(empty_input.fileno(), sys.stdin.fileno())

            # Keep the lock until the adapter process exits, including on interruption.
            os.set_inheritable(lock.fileno(), True)
            os.environ["HOME"] = str(state)
            os.execvp(command[0], command)
    except (OSError, ValueError) as error:
        print(f"CloudExtractor: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
