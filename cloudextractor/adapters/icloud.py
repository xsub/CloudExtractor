import os
from pathlib import Path
import re
import sys

from cloudextractor.download_dates import DateRange


def account_name(value: str) -> str:
    username = value.strip().lower()
    if username.startswith("-") or not re.fullmatch(r"[^@/\\\s\x00-\x1f\x7f]+@[^@/\\\s\x00-\x1f\x7f]+", username):
        raise ValueError("Set ICLOUD_USERNAME to an Apple Account email usable as a directory name.")
    return username


def prepare(
    action: str, archive: Path, state: Path, date_range: DateRange | None = None,
) -> tuple[Path, Path, list[str]]:
    configured_account = os.environ.get("ICLOUD_USERNAME", "")
    if action == "check" and not configured_account.strip():
        return archive, state, []
    username = account_name(configured_account)

    archive = archive / username
    state = state / username
    if action == "check":
        return archive, state, []

    adp = os.environ.get("ICLOUD_ADP_WEB_ACCESS", "false").lower()
    if adp not in ("true", "false"):
        raise ValueError("ICLOUD_ADP_WEB_ACCESS must be true or false.")

    # Keep the fixed arguments in icloudpd's Copy mode. The adapter accepts no raw flags.
    # The parameter provider without a password uses only the saved session.
    args = [
        "icloudpd",
        "--log-level", "debug" if action == "run" and os.environ.get("CLOUDEXTRACTOR_PROGRESS") == "1" else "info",
        "--password-provider", "console" if action == "auth" else "parameter",
        "--username", username,
        "--cookie-directory", str(state),
    ]
    if adp == "true":
        args.append("--adp-web-access")
    if action == "auth":
        return archive, state, [*args, "--auth-only"]
    if action == "scan":
        args.append("--only-print-filenames")

    args.extend([
        "--directory", str(archive),
        "--size", "original",
        "--size", "alternative",
        "--live-photo-size", "original",
        "--file-match-policy", "name-id7",
        "--folder-structure", "{:%Y/%m}",
    ])
    if action == "inventory":
        return archive, state, [sys.executable, "-m", "cloudextractor.adapters.icloud_inventory", *args[1:]]

    if date_range is not None:
        first, last = date_range.utc_bounds()
        args.extend(["--skip-created-before", first.isoformat(), "--skip-created-after", last.isoformat()])
        return archive, state, args

    recent = os.environ.get("ICLOUD_RECENT", "").strip()
    if recent:
        if not recent.isascii() or not recent.isdecimal() or int(recent) < 1:
            raise ValueError("ICLOUD_RECENT must be a positive integer or empty.")
        args.extend(["--recent", recent])
    return archive, state, args
