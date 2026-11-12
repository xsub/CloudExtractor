import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from cloudextractor.dashboard import create_app


class GalleryNavigationTest(unittest.TestCase):
    def setUp(self):
        self.temporary = TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        archive, state = root / "archive", root / "state"
        self.account = "test@example.com"
        self.media = archive / self.account / "2026/11"
        self.media.mkdir(parents=True)
        state.mkdir()
        (archive / ".cloudextractor-archive").touch()
        env = patch.dict(os.environ, {"ICLOUD_USERNAME": self.account}, clear=True)
        env.start()
        self.addCleanup(env.stop)
        self.client = create_app(archive, state).test_client()

    def write(self, name: str, timestamp: int) -> None:
        path = self.media / name
        path.write_bytes(b"data")
        os.utime(path, (timestamp, timestamp))

    def status(self, **query):
        response = self.client.get("/api/state", query_string={"account": self.account, **query})
        self.assertEqual(response.status_code, 200)
        return response.get_json()

    def test_direct_page_and_page_size(self):
        for number in range(111):
            self.write(f"photo-{number:03}.jpg", 1794438000 + number * 60)

        data = self.status(page=4, page_size=20)
        self.assertEqual((data["page"], data["pages"], data["page_size"], len(data["paths"])), (4, 6, 20, 20))
        self.assertEqual(data["paths"][0], "2026/11/photo-050.jpg")
        self.assertEqual(len(self.status(page_size=50)["paths"]), 50)
        self.assertEqual(len(self.status(page_size=100)["paths"]), 100)
        last = self.status(page=300, page_size=50)
        self.assertEqual((last["page"], len(last["paths"])), (3, 11))

    def test_group_together_before_paging_and_keep_videos_separate(self):
        for name, timestamp in (("last.jpg", 1794438010), ("between.mov", 1794438008),
                                ("first.heic", 1794438006), ("earlier.jpg", 1794438000)):
            self.write(name, timestamp)

        data = self.status(group_interval=5)
        self.assertEqual(data["downloaded"], 4)
        self.assertEqual(data["paths"], ["2026/11/last.jpg", "2026/11/between.mov", "2026/11/earlier.jpg"])
        self.assertEqual(data["groups"], {"2026/11/last.jpg": {"count": 2, "bytes": 8}})
        group = self.client.get("/api/group", query_string={
            "account": self.account, "path": "2026/11/last.jpg", "group_interval": 5,
        }).get_json()
        self.assertEqual(group["paths"], ["2026/11/last.jpg", "2026/11/first.heic"])
        self.assertEqual((group["newest"], group["oldest"]), (1794438010, 1794438006))
        self.assertEqual(self.status(media="video", group_interval=5)["groups"], {})
        self.assertEqual(self.status(group_interval=2)["groups"], {})
