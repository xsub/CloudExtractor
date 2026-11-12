from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
from tempfile import NamedTemporaryFile
from threading import Lock, Thread, Timer

from cloudextractor.catalog import Catalog
from cloudextractor.download_dates import DateRange


LOG_LINE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} (\w+)\s+(.*)$")


@dataclass
class Job:
    process: subprocess.Popen[str]
    action: str
    limit: int | None
    status: str
    date_range: dict[str, str] | None = None
    current: str | None = None
    last: str | None = None
    downloaded: int = 0
    items: int | None = None
    listed: int = 0
    errors: int = 0
    issue: str | None = None


@dataclass(frozen=True)
class Scan:
    catalog: Catalog
    time: str
    limit: int | None
    complete: bool
    unmeasured: int = 0


class Jobs:
    def __init__(self, cache: Path):
        self.cache = cache
        self.lock = Lock()
        self.jobs: dict[str, Job] = {}
        self.scans: dict[str, Scan | None] = {}
        self.usage: dict[str, dict | None] = {}

    def start(self, account: str, action: str, limit: int | None, date_range: DateRange | None = None) -> None:
        with self.lock:
            previous = self.jobs.get(account)
            if previous and previous.status in ("running", "scanning", "stopping"):
                raise ValueError("Dla tego konta trwa już operacja.")

            env = dict(os.environ, ICLOUD_USERNAME=account, ICLOUD_RECENT=str(limit or ""),
                       CLOUDEXTRACTOR_PROGRESS="1", PYTHONUNBUFFERED="1")
            command = [sys.executable, "-m", "cloudextractor", "inventory" if action == "scan" else action]
            if date_range is not None:
                command.extend(["--date-from", date_range.start, "--date-to", date_range.end,
                                "--date-zone", date_range.timezone])
            process = subprocess.Popen(
                command,
                env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, text=True, errors="replace", start_new_session=True,
            )
            job = Job(process, action, limit, "running" if action == "run" else "scanning",
                      date_range=asdict(date_range) if date_range is not None else None)
            self.jobs[account] = job
            Thread(target=self._collect, args=(account, job), daemon=True).start()

    def stop(self, account: str) -> None:
        with self.lock:
            job = self.jobs.get(account)
            if not job or job.status not in ("running", "scanning") or job.process.poll() is not None:
                raise ValueError("Dla tego konta nie ma aktywnej operacji panelu.")
            job.status = "stopping"
            self._signal(job, signal.SIGTERM)
            timer = Timer(8, self._force_stop, args=(job,))
            timer.daemon = True
            timer.start()

    def _signal(self, job: Job, signum: signal.Signals) -> None:
        if job.process.poll() is None:
            try:
                os.killpg(job.process.pid, signum)
            except ProcessLookupError:
                pass

    def _force_stop(self, job: Job) -> None:
        with self.lock:
            if job.status == "stopping":
                self._signal(job, signal.SIGKILL)

    def shutdown(self) -> None:
        with self.lock:
            active = [job for job in self.jobs.values() if job.process.poll() is None]
            for job in active:
                job.status = "stopping"
                self._signal(job, signal.SIGTERM)
        for job in active:
            try:
                job.process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                self._force_stop(job)

    def _status(self, account: str) -> dict:
        job = self.jobs.get(account)
        if job:
            return {key: value for key, value in vars(job).items() if key != "process"}
        return {"status": "idle", "downloaded": 0, "errors": 0}

    def status(self, account: str) -> dict:
        with self.lock:
            return self._status(account)

    def snapshot(self, account: str) -> tuple[dict, Scan | None, dict | None]:
        with self.lock:
            status = self._status(account)
            if account not in self.scans:
                try:
                    data = json.loads(self._scan_file(account).read_text())
                    self.scans[account] = self._load_scan(data)
                except (FileNotFoundError, KeyError, TypeError, ValueError):
                    self.scans[account] = None
            if account not in self.usage:
                try:
                    self.usage[account] = json.loads(self._usage_file(account).read_text())
                except (FileNotFoundError, ValueError):
                    self.usage[account] = None
            return status, self.scans[account], self.usage[account]

    def _scan_file(self, account: str) -> Path:
        return self.cache / f"{account}.json"

    def _usage_file(self, account: str) -> Path:
        return self.cache / f"{account}.usage.json"

    @staticmethod
    def _load_scan(data: dict) -> Scan:
        complete = "files" in data
        files = data["files"] if complete else dict.fromkeys(data["paths"])
        return Scan(Catalog(files), data["time"], data.get("limit"), complete, data.get("unmeasured", 0))

    def _write_snapshot(self, file: Path, values: dict) -> dict:
        snapshot = {**values, "time": datetime.now(timezone.utc).isoformat()}
        with NamedTemporaryFile(mode="w", dir=self.cache, delete=False) as output:
            json.dump(snapshot, output)
        os.replace(output.name, file)
        return snapshot

    def _collect(self, account: str, job: Job) -> None:
        inventory = None
        with job.process.stdout as stdout:
            for line in stdout:
                line = line.rstrip("\r\n")
                if job.action == "scan" and line.startswith('{"files":'):
                    try:
                        inventory = json.loads(line)
                    except ValueError:
                        continue
                elif job.action == "scan" and line.startswith('{"usage":'):
                    try:
                        size = json.loads(line)["usage"]
                        if type(size) is int and size >= 0:
                            with self.lock:
                                self.usage[account] = self._write_snapshot(self._usage_file(account), {"bytes": size})
                    except (ValueError, OSError):
                        continue
                elif line.startswith("CloudExtractor:") or LOG_LINE.match(line):
                    with self.lock:
                        self._progress(job, line)

        code = job.process.wait()
        with self.lock:
            job.current = None
            if job.status == "stopping":
                job.status = "stopped"
            elif code or job.errors or (job.action == "scan" and inventory is None):
                job.status = "failed"
            else:
                try:
                    if job.action == "scan":
                        scan = self._write_snapshot(self._scan_file(account), inventory)
                        self.scans[account] = self._load_scan(scan)
                    job.status = "finished"
                except OSError:
                    job.status = "failed"
                    job.issue = "cache"

    @staticmethod
    def _progress(job: Job, line: str) -> None:
        match = LOG_LINE.match(line)
        level, message = match.groups() if match else ("ERROR", line)
        if "saved session is missing or no longer valid" in message or "authentication is required" in message:
            job.issue = "auth"
        elif "already using this state directory" in message:
            job.issue = "busy"
        elif message.startswith("CloudExtractor:"):
            job.issue = "configuration"

        if level in ("ERROR", "CRITICAL"):
            job.errors += 1
        if level == "DEBUG" and message.startswith("Downloading /"):
            job.current = Path(message.removeprefix("Downloading ").removesuffix("...")).name
        if level == "INFO" and message.startswith("Downloaded /"):
            job.downloaded += 1
            job.last = Path(message.removeprefix("Downloaded ")).name
            job.current = None
        if level == "INFO" and (total := re.match(r"Downloading (\d+) .* photos and videos to ", message)):
            job.items = int(total.group(1))
        if level == "INFO" and (inventory := re.fullmatch(r"Inventory (\d+)/(\d+)", message)):
            job.listed, job.items = map(int, inventory.groups())
