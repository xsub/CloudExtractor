#!/usr/bin/env bash
set -eu

if [[ "${1:-}" == "--help" ]]; then
    printf 'Usage: %s [APPLE_ID]\n' "$0"
    exit 0
fi

if [[ "$#" -gt 1 ]]; then
    printf 'Usage: %s [APPLE_ID]\n' "$0" >&2
    exit 2
fi

if [[ ! -t 0 ]]; then
    printf 'Run this script in an interactive terminal. Use ssh -t for a remote host.\n' >&2
    exit 1
fi

apple_id="${1:-}"
if [[ -z "$apple_id" ]]; then
    read -r -p 'Apple Account email: ' apple_id
fi
if [[ "$apple_id" != *@* ]]; then
    printf 'Enter the email address of the Apple Account.\n' >&2
    exit 2
fi

cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
docker compose run --rm --no-deps -e "ICLOUD_USERNAME=$apple_id" extractor auth
docker compose run --rm --no-deps -T -e "ICLOUD_USERNAME=$apple_id" extractor check

printf 'Reload the dashboard, select the Apple ID, and choose a download range.\n'
