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
from cloudextractor.dashboard import create_app
from cloudextractor.dashboard_jobs import Job, Jobs
from cloudextractor.download_dates import DateRange


class DownloadDatesTest(unittest.TestCase):
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
        _, (account,) = parse(command[1:])
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
