from datetime import datetime, timedelta
import logging
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from icloudpd.base import where_builder
from icloudpd.cli import parse

from cloudextractor.adapters.icloud import prepare
from cloudextractor.adapters.icloud_download import month_bounds, rank_windows, window_assets
from cloudextractor.dashboard import create_app
from cloudextractor.dashboard_jobs import Job, Jobs
from cloudextractor.download_dates import DateRange


class DownloadDatesTest(unittest.TestCase):
    def test_selected_months_exclude_the_gap_and_use_utc(self):
        months = ("2026/08", "2026/10")
        with patch.dict(os.environ, {"ICLOUD_USERNAME": "test@example.com", "ICLOUD_RECENT": "20"}, clear=True):
            _, _, command = prepare("run", Path("/archive"), Path("/state"), months=months)
        self.assertEqual(command[2:5], ["cloudextractor.adapters.icloud_download", "--months", "2026/08,2026/10"])
        _, (account,) = parse(command[5:])
        self.assertIsNone(account.recent)
        self.assertFalse(account.auto_delete)
        self.assertFalse(account.delete_after_download)

        dates = [datetime.fromisoformat("2026-11-01T00:00:00+00:00")] * 8000 + [
            datetime.fromisoformat(value) for value in (
                "2026-10-31T23:59:59+00:00", "2026-10-01T00:00:00+00:00",
                "2026-09-01T01:00:00+01:00", "2026-09-01T00:59:59+01:00",
                "2026-08-01T00:00:00+00:00", "2026-07-31T23:59:59+00:00",
            )
        ]

        class Album:
            page_size = 100
            offset = 0
            probes = 0

            def photos_request(self):
                self.probes += 1
                fields = {"assetDate": {"value": int(dates[self.offset].timestamp() * 1000)}}
                return SimpleNamespace(json=lambda: {"records": [{"recordType": "CPLAsset", "fields": fields}]})

            def __iter__(self):
                while self.offset < len(dates):
                    yield SimpleNamespace(has_asset_date=True, created=dates[self.offset])
                    self.offset += 1

        album = Album()
        windows = rank_windows(album, len(dates), month_bounds(months))
        self.assertEqual([(start, end) for start, end, _ in windows], [(8000, 8002), (8003, 8005)])
        self.assertLess(album.probes, 60)
        self.assertEqual([item.created for window in windows for item in window_assets(album, *window)],
                         dates[8000:8002] + dates[8003:8005])

    def test_listing_progress_is_separate_from_file_transfers(self):
        job = Job(Mock(), "run", None, "running")
        Jobs._progress(job, "2026-11-12 02:00:00 DEBUG    Downloading /archive/file.jpg...")
        self.assertEqual(job.current, "file.jpg")
        Jobs._progress(job, "CloudExtractor listing 50/100")
        self.assertIsNone(job.current)
        self.assertEqual((job.listed, job.items, job.downloaded), (50, 100, 0))

        Jobs._progress(job, "2026-11-12 02:00:01 INFO     Downloaded /archive/next.jpg")
        Jobs._progress(job, "CloudExtractor listing 51/100")
        self.assertEqual((job.listed, job.items, job.downloaded), (51, 100, 1))
        self.assertEqual(job.last, "next.jpg")

    def test_icloud_downloads_the_entire_selected_day_across_dst(self):
        dates = DateRange("2026-03-29", "2026-03-29", "Europe/Warsaw")
        with patch.dict(os.environ, {"ICLOUD_USERNAME": "test@example.com", "ICLOUD_RECENT": "20"}, clear=True):
            _, _, command = prepare("run", Path("/archive"), Path("/state"), dates)

        self.assertNotIn("--recent", command)
        _, (account,) = parse(command[3:])
        first = datetime.fromisoformat("2026-03-28T23:00:00+00:00")
        next_day = datetime.fromisoformat("2026-03-29T22:00:00+00:00")
        millisecond = timedelta(milliseconds=1)
        for when, expected in ((first - millisecond, False), (first, True),
                               (next_day - millisecond, True), (next_day, False)):
            with self.subTest(when=when):
                accepted = where_builder(
                    logging.getLogger(__name__), False, account.skip_created_before, account.skip_created_after,
                    False, lambda _photo: "photo.jpg", SimpleNamespace(created=when),
                )
                self.assertEqual(accepted, expected)

    def test_dashboard_passes_date_range_to_worker_and_rejects_invalid_dates(self):
        account = "test@example.com"
        with TemporaryDirectory() as temporary, patch.dict(os.environ, {
            "ICLOUD_USERNAME": account, "ICLOUD_RECENT": "20",
        }, clear=True):
            archive, state = Path(temporary) / "archive", Path(temporary) / "state"
            archive.mkdir()
            state.mkdir()
            (archive / ".cloudextractor-archive").touch()
            client = create_app(archive, state).test_client()
            dates = {"start": "2026-11-01", "end": "2026-11-12", "timezone": "Europe/Warsaw"}
            payload = {"account": account, "action": "run", "date_range": dates}
            headers = {"X-CloudExtractor": "1"}

            with patch("cloudextractor.dashboard_jobs.subprocess.Popen") as worker, \
                 patch("cloudextractor.dashboard_jobs.Thread"):
                for invalid in ({"end": "2026-10-31"}, {"end": "2026-11-31"}, {"timezone": "invalid/zone"}):
                    response = client.post("/api/jobs", json={**payload, "date_range": {**dates, **invalid}},
                                           headers=headers)
                    self.assertEqual(response.status_code, 400)
                response = client.post("/api/jobs", json={**payload, "limit": 20}, headers=headers)
                self.assertEqual(response.status_code, 400)
                worker.assert_not_called()

                self.assertEqual(client.post("/api/jobs", json=payload, headers=headers).status_code, 202)
                worker.assert_called_once()
                self.assertEqual(worker.call_args.args[0][-6:], [
                    "--date-from", "2026-11-01", "--date-to", "2026-11-12", "--date-zone", "Europe/Warsaw",
                ])
                self.assertEqual(worker.call_args.kwargs["env"]["ICLOUD_RECENT"], "")
                response = client.get("/api/jobs", query_string={"account": account})
                self.assertEqual(response.json["job"]["date_range"], dates)

    def test_dashboard_starts_selected_months_in_one_worker(self):
        account = "test@example.com"
        with TemporaryDirectory() as temporary, patch.dict(os.environ, {"ICLOUD_USERNAME": account}, clear=True):
            archive, state = Path(temporary) / "archive", Path(temporary) / "state"
            archive.mkdir()
            state.mkdir()
            (archive / ".cloudextractor-archive").touch()
            client = create_app(archive, state).test_client()
            payload = {"account": account, "action": "run", "match_size": True}
            headers = {"X-CloudExtractor": "1"}
            with patch("cloudextractor.dashboard_jobs.subprocess.Popen") as worker, \
                 patch("cloudextractor.dashboard_jobs.Thread"):
                for invalid in ([], ["2026/00"], ["0000/03"], "2026/03"):
                    self.assertEqual(client.post("/api/jobs", json={**payload, "months": invalid},
                                                 headers=headers).status_code, 400)
                self.assertEqual(client.post("/api/jobs", json={**payload, "months": ["2026/08"], "limit": 20},
                                             headers=headers).status_code, 400)
                worker.assert_not_called()

                response = client.post("/api/jobs", json={**payload, "months": ["2026/10", "2026/08", "2026/10"]},
                                       headers=headers)
                self.assertEqual(response.status_code, 202)
                worker.assert_called_once()
                self.assertEqual(worker.call_args.args[0][-5:], ["--month", "2026/08", "--month", "2026/10", "--match-size"])
                self.assertEqual(worker.call_args.kwargs["env"]["ICLOUD_RECENT"], "")
                response = client.get("/api/jobs", query_string={"account": account})
                self.assertEqual(response.json["job"]["months"], ["2026/08", "2026/10"])
                self.assertTrue(response.json["job"]["match_size"])
