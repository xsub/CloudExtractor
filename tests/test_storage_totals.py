import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from cloudextractor.adapters import icloud_inventory
from cloudextractor.dashboard import create_app


class StorageTotalsTest(unittest.TestCase):
    def test_filters_keep_both_full_totals_and_compare_folders(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive, state = root / "archive", root / "state"
            account = "test@example.com"
            first = archive / account / "2026/11"
            second = archive / account / "2026/10"
            first.mkdir(parents=True)
            second.mkdir(parents=True)
            (archive / ".cloudextractor-archive").touch()
            (first / "photo.jpg").write_bytes(b"p" * 20)
            (first / "video.mov").write_bytes(b"v" * 50)
            (first / "pending.part").write_bytes(b"p" * 5)
            (second / "photo.heic").write_bytes(b"p" * 10)
            cache = state / ".dashboard"
            cache.mkdir(parents=True)
            (cache / f"{account}.json").write_text(json.dumps({
                "time": "2026-11-12T12:00:00+00:00",
                "files": {"2026/11/photo.jpg": 20, "2026/11/video.mov": 50, "2026/11/live.mov": 80,
                          "2026/10/missing.heic": 100},
                "unmeasured": 0,
            }))
            with patch.dict(os.environ, {"ICLOUD_USERNAME": account}, clear=True):
                client = create_app(archive, state).test_client()
                data = client.get("/api/state", query_string={
                    "account": account, "view": "pending", "media": "video", "folder": "2026/11",
                }).get_json()
                (cache / f"{account}.usage.json").write_text(json.dumps({
                    "bytes": 300, "time": "2026-11-12T12:00:00+00:00",
                }))
                reported = create_app(archive, state).test_client().get("/api/state", query_string={
                    "account": account, "media": "video", "folder": "2026/11",
                }).get_json()

        self.assertEqual(data["cloud"], {"bytes": 250, "filtered": 130})
        self.assertEqual(data["local"], {"bytes": 85, "filtered": 50})
        self.assertEqual(data["folders"][0], {"name": "2026/11", "cloud": 130, "local": 50})
        self.assertEqual(data["paths"], ["2026/11/live.mov"])
        self.assertEqual(data["sizes"], {"2026/11/live.mov": 80})
        self.assertEqual(reported["cloud"]["bytes"], 300)
        self.assertEqual(reported["cloud"]["filtered"], 130)

    def test_two_library_entries_can_share_an_original(self):
        album = [SimpleNamespace(_asset_record={"recordName": name}) for name in ("first", "second")]
        client = SimpleNamespace(photos=SimpleNamespace(all=album))
        with patch.object(icloud_inventory, "asset_files", return_value=({"2026/11/photo.jpg": 20}, True)):
            result = icloud_inventory.inventory(client, SimpleNamespace())

        self.assertEqual(result, {"files": {"2026/11/photo.jpg": 20}, "items": 2, "unmeasured": 0})
