from datetime import datetime
import json
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
        self.cache = state / ".dashboard"
        self.account = "test@example.com"
        self.media = archive / self.account / "2026/11"
        self.media.mkdir(parents=True)
        state.mkdir()
        (archive / ".cloudextractor-archive").touch()
        env = patch.dict(os.environ, {"ICLOUD_USERNAME": self.account}, clear=True)
        env.start()
        self.addCleanup(env.stop)
        self.client = create_app(archive, state).test_client()

    def write(self, name: str, timestamp: int, size: int = 4) -> None:
        path = self.media / name
        path.write_bytes(b"d" * size)
        os.utime(path, (timestamp, timestamp))

    def status(self, **query):
        response = self.client.get("/api/state", query_string={"account": self.account, **query})
        self.assertEqual(response.status_code, 200)
        return response.get_json()

    def test_job_controls_can_read_status_without_gallery_or_inventory(self):
        with patch("cloudextractor.dashboard.Gallery.items", side_effect=OSError("Gallery unavailable")), \
             patch("cloudextractor.dashboard.Jobs.snapshot", side_effect=OSError("Inventory unavailable")):
            response = self.client.get("/api/jobs", query_string={"account": self.account})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json["ready"])
        self.assertEqual(response.json["job"]["status"], "idle")

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

    def test_group_photos_and_videos_by_time_without_mixing_them(self):
        for name, timestamp in (("last.jpg", 1794438010), ("between.mov", 1794438008),
                                ("first.heic", 1794438006), ("clip.mp4", 1794438004),
                                ("earlier.jpg", 1794438000)):
            self.write(name, timestamp)

        data = self.status(group_interval=5)
        self.assertEqual(data["downloaded"], 5)
        self.assertEqual(data["paths"], ["2026/11/last.jpg", "2026/11/between.mov", "2026/11/earlier.jpg"])
        self.assertEqual(data["groups"], {
            "2026/11/last.jpg": {"count": 2, "bytes": 8},
            "2026/11/between.mov": {"count": 2, "bytes": 8},
        })
        group = self.client.get("/api/group", query_string={
            "account": self.account, "path": "2026/11/last.jpg", "group_interval": 5,
        }).get_json()
        self.assertEqual(group["paths"], ["2026/11/last.jpg", "2026/11/first.heic"])
        self.assertEqual((group["newest"], group["oldest"]), (1794438010, 1794438006))
        videos = self.status(media="video", group_interval=5)
        self.assertEqual(videos["paths"], ["2026/11/between.mov"])
        self.assertEqual(videos["groups"], {"2026/11/between.mov": {"count": 2, "bytes": 8}})
        group = self.client.get("/api/group", query_string={
            "account": self.account, "path": "2026/11/between.mov", "group_interval": 5, "media": "video",
        }).get_json()
        self.assertEqual(group["paths"], ["2026/11/between.mov", "2026/11/clip.mp4"])
        self.assertEqual((group["newest"], group["oldest"]), (1794438008, 1794438004))
        self.assertEqual(self.status(group_interval=2)["groups"], {})

    def test_calendar_counts_match_day_gallery_in_browser_timezone(self):
        prior = int(datetime.fromisoformat("2026-11-11T22:59:59+00:00").timestamp())
        self.write("before.jpg", prior)
        self.write("photo.jpg", prior + 1)
        self.write("video.mov", prior + 2)
        self.write("unfinished.mov.part", prior + 2)
        scope = {"calendar_from": "2026-11-01", "calendar_to": "2026-11-30", "timezone": "Europe/Warsaw"}

        self.assertEqual(self.status(**scope)["calendar"], {
            "2026-11-11": {"image": 1}, "2026-11-12": {"image": 1, "video": 1},
        })
        self.assertEqual(self.status(**scope, media="video")["calendar"], {"2026-11-12": {"video": 1}})
        day = self.status(day="2026-11-12", timezone="Europe/Warsaw")
        self.assertEqual(day["paths"], ["2026/11/video.mov", "2026/11/photo.jpg"])
        self.assertEqual(day["downloaded"], 3)

    def test_pending_calendar_requires_dated_inventory_and_skips_local_files(self):
        self.write("photo.jpg", 1794438000)
        snapshot = self.cache / f"{self.account}.json"
        files = {"2026/11/photo.jpg": 4, "2026/11/missing.mov": 10}
        data = {"files": files, "time": "2026-11-12T12:00:00+00:00"}
        snapshot.write_text(json.dumps(data))
        scope = {"view": "pending", "calendar_from": "2026-11-01", "calendar_to": "2026-11-30", "timezone": "UTC"}
        old = self.status(**scope)
        self.assertIsNone(old["calendar"])
        self.assertEqual(old["pending"], 1)

        snapshot.write_text(json.dumps({**data, "timestamps": dict.fromkeys(files, 1794438000)}))
        archive, state = self.cache.parent.parent / "archive", self.cache.parent
        self.client = create_app(archive, state).test_client()
        self.assertEqual(self.status(**scope)["calendar"], {"2026-11-11": {"video": 1}})

    def test_sort_applies_to_the_entire_date_range_before_paging(self):
        start = int(datetime.fromisoformat("2026-11-11T23:00:00+00:00").timestamp())
        for index in range(44):
            self.write(f"photo-{43 - index:02}.jpg", start + index * 3600, index + 1)
        self.write("excluded.jpg", start - 1, 1000)
        scope = {"date_from": "2026-11-12", "date_to": "2026-11-13", "timezone": "Europe/Warsaw"}
        first = self.status(**scope, sort="date", order="asc", page_size=20)
        self.assertEqual(first["paths"][0], "2026/11/photo-43.jpg")
        self.assertEqual(first["total"], 44)
        second = self.status(**scope, sort="size", order="desc", page=2, page_size=20)
        self.assertEqual(second["paths"][0], "2026/11/photo-20.jpg")
        self.assertEqual(list(second["sizes"][path] for path in second["paths"]), list(range(24, 4, -1)))

    def test_group_sort_uses_total_size_and_group_members_keep_their_own_sizes(self):
        self.write("newer.jpg", 1794438010, 9)
        self.write("older.jpg", 1794438006, 12)
        self.write("single.jpg", 1794437990, 16)
        self.assertEqual(self.status(group_interval=5, sort="size")["paths"], [
            "2026/11/newer.jpg", "2026/11/single.jpg",
        ])
        group = self.client.get("/api/group", query_string={
            "account": self.account, "path": "2026/11/newer.jpg", "group_interval": 5, "sort": "size",
        }).get_json()
        self.assertEqual(group["paths"], ["2026/11/older.jpg", "2026/11/newer.jpg"])
        self.assertEqual((group["newest"], group["oldest"]), (1794438010, 1794438006))

    def test_pending_files_with_missing_dates_or_sizes_sort_last(self):
        files = {"2026/11/a.jpg": 5, "2026/11/b.mov": None, "2026/11/c.heic": 10}
        (self.cache / f"{self.account}.json").write_text(json.dumps({
            "files": files, "time": "2026-11-12T12:00:00+00:00",
            "timestamps": {"2026/11/a.jpg": 1794438000, "2026/11/b.mov": 1794438010},
        }))
        self.assertEqual(self.status(view="pending", sort="date", order="desc")["paths"], [
            "2026/11/b.mov", "2026/11/a.jpg", "2026/11/c.heic",
        ])
        self.assertEqual(self.status(view="pending", sort="size", order="asc")["paths"], [
            "2026/11/a.jpg", "2026/11/c.heic", "2026/11/b.mov",
        ])
        self.assertEqual(self.status(view="pending", sort="type", order="desc")["paths"][0], "2026/11/b.mov")
        self.assertEqual(self.status(view="pending", sort="format", order="asc")["paths"][0], "2026/11/c.heic")
