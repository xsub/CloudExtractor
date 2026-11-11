import os
from pathlib import Path
import unittest
from unittest.mock import patch

from icloudpd.cli import parse
from pyicloud_ipd.file_match import FileMatchPolicy

from cloudextractor.adapters.icloud import prepare


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

                global_config, (account,) = parse(command[1:])
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


if __name__ == "__main__":
    unittest.main()
