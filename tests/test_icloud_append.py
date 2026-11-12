import base64
from datetime import datetime, timezone
import hashlib
from io import BytesIO
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from icloudpd.cli import parse
from pyicloud_ipd.asset_version import AssetVersion
from pyicloud_ipd.file_match import FileMatchPolicy
from pyicloud_ipd.version_size import AssetVersionSize
from requests import Response

from cloudextractor.adapters.icloud import prepare
from cloudextractor.adapters.icloud_archive import Archive, CHECKSUM_SALT


class ICloudAppendTest(unittest.TestCase):
    def test_worker_options_keep_archived_files_and_cloud_library(self):
        for action, limit, progress in (
            ("run", "", ""),
            ("run", "20", "1"),
            ("scan", "20", "1"),
            ("auth", "", ""),
        ):
            with self.subTest(action=action, limit=limit, progress=progress):
                with patch.dict(os.environ, {
                    "ICLOUD_USERNAME": "test@example.com",
                    "ICLOUD_ADP_WEB_ACCESS": "true",
                    "ICLOUD_RECENT": limit,
                    "CLOUDEXTRACTOR_PROGRESS": progress,
                }, clear=True):
                    _, _, command = prepare(action, Path("/archive"), Path("/state"))

                global_config, (account,) = parse(command[3:] if action == "run" else command[1:])
                self.assertFalse(account.auto_delete)
                self.assertFalse(account.prune_orphans)
                self.assertFalse(account.prune_orphans_adopt_existing)
                self.assertFalse(account.delete_after_download)
                self.assertIsNone(account.keep_icloud_recent_days)
                self.assertFalse(account.set_exif_datetime)
                self.assertFalse(account.xmp_sidecar)
                if action != "auth":
                    self.assertEqual(account.file_match_policy, FileMatchPolicy.NAME_ID7)
                self.assertEqual(global_config.only_print_filenames, action == "scan")
                self.assertEqual(account.auth_only, action == "auth")

    def test_same_size_conflicts_append_once_and_leave_existing_files_intact(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            relative = Path("2026/11/IMG_0001.JPG")
            target = root / relative
            target.parent.mkdir(parents=True)
            target.write_bytes(b"first")
            (target.parent / "IMG_0001_001.JPG").write_bytes(b"other")
            data = b"third"
            checksum = base64.b64encode(b"\x01" + hashlib.sha1(CHECKSUM_SALT + data).digest()).decode()
            version = AssetVersion(len(data), "https://unused.invalid", "public.jpeg", checksum)
            response = Response()
            response.status_code = 200
            response.raw = BytesIO(data)
            asset = SimpleNamespace(created=datetime(2026, 11, 1, tzinfo=timezone.utc), download=Mock(return_value=response))
            client = SimpleNamespace(photos=SimpleNamespace(session=object()))

            for _ in range(2):
                self.assertTrue(Archive(root).save(client, asset, relative, AssetVersionSize.ORIGINAL, version,
                                                   lambda _: target.name))
            asset.download.assert_called_once()
            self.assertEqual({path.name: path.read_bytes() for path in target.parent.iterdir()}, {
                "IMG_0001.JPG": b"first", "IMG_0001_001.JPG": b"other", "IMG_0001_002.JPG": data,
            })

    def test_size_matching_skips_existing_content_without_reading_it(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "IMG_0001.JPG"
            target.write_bytes(b"first")
            checksum = base64.b64encode(b"\x01" + hashlib.sha1(CHECKSUM_SALT + b"other").digest()).decode()
            version = AssetVersion(5, "https://unused.invalid", "public.jpeg", checksum)
            with patch("cloudextractor.adapters.icloud_archive.checksum_matches") as hash_file, \
                 patch("cloudextractor.adapters.icloud_archive.download_media") as transfer:
                saved = Archive(root, match_size=True).save(None, None, Path(target.name), AssetVersionSize.ORIGINAL,
                                                            version, lambda _: target.name)
            self.assertTrue(saved)
            hash_file.assert_not_called()
            transfer.assert_not_called()
            self.assertEqual(target.read_bytes(), b"first")


if __name__ == "__main__":
    unittest.main()
