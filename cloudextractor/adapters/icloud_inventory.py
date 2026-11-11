import json
import logging
from pathlib import Path
import sys

from icloudpd.base import build_filename_cleaner, build_filename_with_policies, lp_filename_concatinator
from icloudpd.cli import parse
from icloudpd.config import UserConfig
from pyicloud_ipd.asset_version import calculate_version_filename
from pyicloud_ipd.base import PyiCloudService
from pyicloud_ipd.exceptions import PyiCloudFailedLoginException
from pyicloud_ipd.services.photos import PhotoAsset
from pyicloud_ipd.utils import disambiguate_filenames
from pyicloud_ipd.version_size import AssetVersionSize
from tzlocal import get_localzone


LOGGER = logging.getLogger(__name__)


def asset_files(asset: PhotoAsset, config: UserConfig) -> tuple[dict[str, int], bool]:
    name = build_filename_with_policies(config.file_match_policy, build_filename_cleaner(config.keep_unicode_in_filenames), asset)
    versions = asset.versions_with_raw_policy(config.align_raw)
    sizes, overrides = disambiguate_filenames(versions, config.sizes, asset, lp_filename_concatinator, name)
    folder = config.folder_structure.format(asset.created.astimezone(get_localzone()))
    files = {}
    for size, version in sizes.items():
        filename = calculate_version_filename(name, version, size, lp_filename_concatinator, asset.item_type, overrides.get(size))
        files[(Path(folder) / filename).as_posix()] = version.size

    live = versions.get(config.live_photo_size)
    if live and not config.skip_live_photos:
        filename = calculate_version_filename(name, live, config.live_photo_size, lp_filename_concatinator, asset.item_type)
        files[(Path(folder) / filename).as_posix()] = live.size
    return files, AssetVersionSize.ORIGINAL in sizes


def connect(config: UserConfig) -> PyiCloudService:
    client = PyiCloudService("com", config.username, lambda: None, cookie_directory=config.cookie_directory)
    if client.requires_2fa or client.requires_2sa:
        raise PyiCloudFailedLoginException("iCloud authentication is required")
    if config.adp_web_access:
        client.request_pcs_consent("photos")
    return client


def storage_usage(client: PyiCloudService) -> int | None:
    response = client.session.get("https://setup.icloud.com/setup/ws/1/storageUsageInfo", params=client.params)
    response.raise_for_status()
    for category in response.json().get("storageUsageByMedia") or []:
        size = category.get("usageInBytes")
        if category.get("mediaKey") == "photos" and type(size) is int and size >= 0:
            return size
    return None


def inventory(client: PyiCloudService, config: UserConfig) -> dict:
    album = client.photos.all
    expected = len(album)
    LOGGER.info("Inventory 0/%d", expected)
    identifiers = set()
    files = {}
    unmeasured = 0
    for count, asset in enumerate(album, start=1):
        # Separate iCloud entries can share one original (the master record).
        identifier = asset._asset_record["recordName"]
        if identifier in identifiers:
            raise ValueError("iCloud returned a duplicate in the library listing")
        identifiers.add(identifier)
        try:
            assets, has_original = asset_files(asset, config)
            for path, size in assets.items():
                if type(size) is not int or size < 0 or Path(path).is_absolute() or ".." in Path(path).parts:
                    raise ValueError("Invalid asset metadata")
                files.setdefault(path, size)
            unmeasured += not has_original
        except (KeyError, ValueError, TypeError, OSError, OverflowError):
            unmeasured += 1
        if count % 500 == 0:
            LOGGER.info("Inventory %d/%d", count, expected)

    if len(identifiers) != expected or len(album) != expected:
        raise ValueError("The iCloud listing was incomplete or changed during the scan")
    return {"files": files, "items": expected, "unmeasured": unmeasured}


def main() -> int:
    logging.basicConfig(format="%(asctime)s %(levelname)-8s %(message)s", datefmt="%Y-%m-%d %H:%M:%S", level=logging.INFO)
    try:
        _, (config,) = parse(sys.argv[1:])
        client = connect(config)
        try:
            usage = storage_usage(client)
            if usage is not None:
                print(json.dumps({"usage": usage}), flush=True)
        except Exception:
            LOGGER.warning("iCloud Photos storage usage is unavailable")
        result = inventory(client, config)
    except PyiCloudFailedLoginException:
        LOGGER.error("iCloud authentication is required")
        return 1
    except Exception:
        LOGGER.error("Could not complete the iCloud inventory")
        return 1
    print(json.dumps(result, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
