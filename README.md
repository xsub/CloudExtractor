# CloudExtractor

[Polska dokumentacja](README.pl.md)

CloudExtractor archives original photos and videos on storage you control. The first adapter uses `icloudpd` 1.33.0 in a Linux container. Mount the share from OpenBSD or another server on the Linux host, then use Docker Compose to make the mounted directory available to the container.

The repository includes an `.env.example` template with the Apple account field left blank. Each host uses its own `.env` and keeps its session in `.state/`. Git ignores both.

## Set up on Linux

Install Docker Engine and Docker Compose, then mount the share. Use a dedicated archive directory and give the container user write access. For a share mounted at `/mnt/storage`, run the following commands.

```sh
mountpoint /mnt/storage &&
  mkdir -p /mnt/storage/cloudextractor/icloud &&
  touch /mnt/storage/cloudextractor/icloud/.cloudextractor-archive
```

Create the `.cloudextractor-archive` marker on the mounted share. The Compose bind mount requires its source directory to exist. If the container receives an empty fallback directory, the marker check stops the export before it writes any media.

```sh
cp .env.example .env
install -d -m 0700 .state/icloud
id -u
id -g
```

In `.env`, set `ARCHIVE_HOST_PATH` to the absolute path of the directory containing the marker. Set `ICLOUD_USERNAME` to your Apple account email and set `CLOUDEXTRACTOR_UID` and `CLOUDEXTRACTOR_GID` to match a user with access to the archive and session directories. `STATE_HOST_PATH` points to a private session directory on the Linux host. If you change it, create the new directory with mode `0700`.

If the account uses Advanced Data Protection (ADP), set `ICLOUD_ADP_WEB_ACCESS=true`, enable web access to iCloud data, and approve Apple's request on a trusted device during sign-in. [icloudpd authentication documentation](https://icloud-photos-downloader.github.io/icloud_photos_downloader/authentication.html)

## Test locally on a laptop

When running Linux containers on a laptop, you can store a sample in the ignored `.state/archive` directory.

```sh
install -d -m 0700 .state/icloud .state/archive
touch .state/archive/.cloudextractor-archive
```

In your local `.env`, set `ARCHIVE_HOST_PATH=./.state/archive`, enter your Apple account email, and set the UID and GID to match the directory owner as seen by the container.

## Run the first export

```sh
docker compose build
docker compose run --rm -T extractor check
docker compose run --rm extractor auth
ICLOUD_RECENT=20 docker compose run --rm -T extractor run
```

The `check` command tests the destination and access to the state directory without connecting to Apple. With `auth`, enter your password and complete two-factor authentication in the terminal. The container saves the session in its private state directory. Run `auth` again when the session expires.

After verifying the sample, leave `ICLOUD_RECENT` empty and run the full export.

```sh
docker compose run --rm -T extractor run
```

The archive always runs in **APPEND** mode. The dashboard and CLI retain existing copies after their originals are deleted from iCloud. If a filename already exists, identical content is skipped and different content is saved with a suffix from `_001` to `_999` before the extension.
Select **Skip existing files with the same name and size** or pass `run --match-size` to skip same-size matches by name without comparing their contents.

The archive uses `<Apple ID>/year/month` folders based on UTC and file names that include the iCloud asset identifier. The export includes originals, Live Photo video components, and available alternate representations such as RAW+JPEG pairs. If a run is interrupted, start `run` again with the same settings.

Verify the archive independently before disconnecting iCloud, including coverage of shared collections and edited versions. The `check` command confirms that the destination is writable.

## Multiple Apple accounts

Each run uses the account specified in `ICLOUD_USERNAME`. Accounts have separate archive directories, sessions, and locks. The account subdirectory is named after its lowercase email address. To authenticate and test another account, substitute its email in both commands.

```sh
ICLOUD_USERNAME=second-account@example.com docker compose run --rm extractor auth
ICLOUD_USERNAME=second-account@example.com ICLOUD_RECENT=20 docker compose run --rm -T extractor run
```

When upgrading from the single-account version, stop the container and move the year folders and session files into a subdirectory named after the existing Apple ID, under their respective parent directories. Keep the `.cloudextractor-archive` marker at the archive root.

## Download dashboard and gallery

Run `docker compose up -d --build ui` from the installation directory on the Linux host. Open an SSH tunnel from your laptop using the host's name or SSH alias.

```sh
ssh -N -L 8765:127.0.0.1:8765 host-linux
```

Select the Apple ID from accounts configured in `.env` or previously signed in using `auth`. The language picker in the header remembers your choice of Polish or English. If you set another host port through `CLOUDEXTRACTOR_UI_PORT` in `.env`, use it in the tunnel and dashboard URL. [Open the dashboard](http://127.0.0.1:8765)

The **By date** scope defaults to today. Set **From** and **To**, or use **Today**, **7 days**, or **30 days**. The presets include today. Both dates are inclusive in the browser's time zone. iCloud filters by capture date and may read metadata for the entire library. You can also choose a number of recently added items or **Entire library**. `ICLOUD_RECENT` does not limit a date-based run.

Choose the scope and click **Start download**. A download can start without a scan. The dashboard shows download status while the gallery loads. **Scan iCloud** reads metadata from the account's main library and refreshes storage figures and the list of missing files. Use **Stop scan** to free the account for a download. The gallery loads thumbnails from the selected account's archive directory. Double-click a tile for a full-screen preview. Playback of original videos depends on the formats your browser supports.

The dashboard shows the active worker count for the selected account, the number of files downloaded in the current run, and the job scope. For a download by date, the worker finds the selected period in iCloud's capture date index and counts the items in that period.

The dashboard shows total iCloud and local archive sizes for the selected Apple ID. Apple reports storage used by iCloud Photos. The detailed scan counts files available to export, including Live Photo video components and RAW+JPEG variants. The local total includes partial downloads. File type and folder filters show subtotals alongside both overall totals. The folder table groups storage by `year/month`.

The **Local / iCloud** column compares the two sizes for the selected file type. Right-click a month row or use **⋯**, then choose **Download month** to download photos and videos by capture date for that full month in UTC. To download several months, check their rows and choose **Download selected**. Each account can run one job at a time.

Choose 20, 50, or 100 items per page. Type a page number and press Enter or click **Go**. In the **Downloaded** gallery, group files captured up to 2, 5, 10, 30, 45, 60, 90, or 180 seconds apart. Photos and videos form separate groups based on the capture time that `icloudpd` stores as the file modification time. Each group takes up one tile. Open a group and click a thumbnail to preview a photo or play a video.

Choose **Calendar** to view 1, 3, 6, or 12 months. Each day shows image (I) and video (V) file counts by capture date in the browser's time zone. Click a day to open the gallery filtered to that date. The **Pending** calendar requires an iCloud scan with capture dates.

Choose a sort field and direction in the gallery. Sorting applies to all results after filters, including a **From–To** date range, before pagination. A group uses the date of its newest file and the combined size of its files. Sorting by date in **Pending** requires a scan with capture dates.

## Adapters

Source modules live in `cloudextractor/adapters/`. Each adapter determines the archive and state directories and the arguments for the `run` and `auth` commands. The main command selects an adapter using `CLOUDEXTRACTOR_SOURCE`, checks the archive, locks the account's state against concurrent use, and forwards the process exit code. A future Google Takeout adapter can reuse the destination handling and define its own input settings.

Adapters follow APPEND mode. The iCloud adapter uses `icloudpd` for session handling and resumable transfers. The archive filesystem must support hard links so a complete file can be published without replacing an existing one.
