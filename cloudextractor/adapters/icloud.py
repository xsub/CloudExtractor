import os
from pathlib import Path


def command(action: str, archive: Path, state: Path) -> list[str]:
    username = os.environ.get("ICLOUD_USERNAME", "").strip()
    if not username:
        raise ValueError("Set ICLOUD_USERNAME to the email address of your Apple Account.")

    adp = os.environ.get("ICLOUD_ADP_WEB_ACCESS", "false").lower()
    if adp not in ("true", "false"):
        raise ValueError("ICLOUD_ADP_WEB_ACCESS must be true or false.")

    # The parameter provider without a password uses only the saved session.
    args = [
        "icloudpd",
        "--log-level", "info",
        "--password-provider", "console" if action == "auth" else "parameter",
        "--username", username,
        "--cookie-directory", str(state),
    ]
    if adp == "true":
        args.append("--adp-web-access")
    if action == "auth":
        return [*args, "--auth-only"]

    args.extend([
        "--directory", str(archive),
        "--size", "original",
        "--size", "alternative",
        "--live-photo-size", "original",
        "--file-match-policy", "name-id7",
        "--folder-structure", "{:%Y/%m}",
    ])
    recent = os.environ.get("ICLOUD_RECENT", "").strip()
    if recent:
        if not recent.isascii() or not recent.isdecimal() or int(recent) < 1:
            raise ValueError("ICLOUD_RECENT must be a positive integer or empty.")
        args.extend(["--recent", recent])
    return args
