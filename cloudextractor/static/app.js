import { MediaCalendar } from "./calendar.js";
import { locale, localizeDocument, requestError, t } from "./language.js";

const element = (id) => document.getElementById(id);
const account = element("account");
const limit = element("limit");
const dateFrom = element("date-from");
const dateTo = element("date-to");
const datePresets = [...document.querySelectorAll("[data-days]")];
const browserZone = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
const mediaFilter = element("media-filter");
const folderFilter = element("folder-filter");
const layout = element("library-layout");
const sortBy = element("gallery-sort");
const sortOrder = element("gallery-order");
const galleryDateFrom = element("gallery-date-from");
const galleryDateTo = element("gallery-date-to");
const clearRange = element("clear-range");
const pageSize = element("page-size");
const pageNumber = element("page-number");
const groupInterval = element("group-interval");
const monthMenu = element("month-menu");
const monthDownload = element("month-download");
const gallery = element("items");
const groupViewer = element("group-viewer");
const viewer = element("viewer");
const number = new Intl.NumberFormat(locale);
const date = new Intl.DateTimeFormat(locale, { dateStyle: "short", timeStyle: "short" });
const calendarDate = new Intl.DateTimeFormat(locale, { dateStyle: "medium", timeZone: "UTC" });
const captureTime = new Intl.DateTimeFormat(locale, { dateStyle: "short", timeStyle: "medium" });
const decimal = new Intl.NumberFormat(locale, { maximumFractionDigits: 2 });
const folderDecimal = new Intl.NumberFormat(locale, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const percentage = new Intl.NumberFormat(locale, { style: "percent", minimumFractionDigits: 1, maximumFractionDigits: 1 });
const labels = {
  idle: t("Gotowy", "Ready"), running: t("Pobieranie", "Downloading"), scanning: t("Skanowanie", "Scanning"), stopping: t("Zatrzymywanie", "Stopping"),
  stopped: t("Zatrzymano", "Stopped"), finished: t("Zakończono", "Finished"), failed: t("Błąd", "Error"),
};
const problems = {
  auth: t("Sesja iCloud wymaga ponownego logowania przez SSH.", "Sign in to iCloud again over SSH."),
  busy: t("Inny proces korzysta z sesji tego konta.", "Another process is using this account's session."),
  configuration: t("Sprawdź konfigurację i dostęp do archiwum.", "Check the configuration and archive access."),
  cache: t("Nie udało się zapisać wyników skanu.", "Could not save scan results."),
};

let view = "downloaded";
let page = 1;
let operation;
let fingerprint;
let folderFingerprint;
let requestId = 0;
let activityId = 0;
let loadingGallery = false;
let loadingActivity = false;
let sending = false;
let dateZone = browserZone;
let restoreScope = true;
let openGroupRequest;
let monthSelection;
let galleryRange;
const mediaCalendar = new MediaCalendar(element("calendar-panel"), refresh, showDay);

function updateLibraryLayout() {
  const calendar = layout.value === "calendar";
  element("gallery-tools").hidden = calendar;
  gallery.hidden = calendar;
  element("calendar-panel").hidden = !calendar;
  clearRange.hidden = !galleryRange;
  if (galleryRange) {
    const label = calendarDate.formatRange(new Date(galleryRange.date_from), new Date(galleryRange.date_to));
    clearRange.textContent = `${label} ×`;
    clearRange.setAttribute("aria-label", t(`Usuń zakres dat: ${label}`, `Clear date range: ${label}`));
  }
}

function setGalleryRange(start = "", end = "") {
  galleryRange = start && end ? { date_from: start, date_to: end } : undefined;
  galleryDateFrom.value = start;
  galleryDateTo.value = end;
  galleryDateTo.min = start;
  page = 1;
  updateLibraryLayout();
  refresh();
}

function showDay(day) {
  layout.value = "gallery";
  setGalleryRange(day, day);
  clearRange.focus();
}

function galleryQuery() {
  return { sort: sortBy.value, order: sortOrder.value, ...galleryRange };
}

function groupingEnabled() {
  return layout.value === "gallery" && view === "downloaded";
}

function suffix(path) {
  return path.split(".").pop().toLowerCase();
}

function byteParts(size, formatter) {
  const units = ["B", "KB", "MB", "GB", "TB"];
  let unit = 0;
  while (size >= 1000 && unit < units.length - 1) {
    size /= 1000;
    unit += 1;
  }
  return [formatter.format(size), units[unit]];
}

function bytes(size) {
  return size == null ? "—" : byteParts(size, decimal).join(" ");
}

function setBytes(target, size, minimum = false) {
  target.textContent = `${minimum && size !== null && size !== undefined ? "≥ " : ""}${bytes(size)}`;
  target.title = size === null || size === undefined ? "" : `${number.format(size)} B`;
}

function setFolderBytes(target, size) {
  if (size == null) {
    target.textContent = "—";
    target.title = "";
    return;
  }
  const [amount, suffix] = byteParts(size, folderDecimal);
  const unit = document.createElement("span");
  unit.className = "folder-unit";
  unit.textContent = suffix;
  target.replaceChildren(`${amount} `, unit);
  target.title = `${number.format(size)} B`;
}

function updateFolderValues(row, folder) {
  setFolderBytes(row.cells[1], folder.cloud);
  setFolderBytes(row.cells[2], folder.local);
  row.cells[3].textContent = folder.cloud > 0 && folder.local != null ? percentage.format(folder.local / folder.cloud) : "—";
}

function mediaUrl(path, kind) {
  return `/api/media?${new URLSearchParams({ account: account.value, path, kind })}`;
}

async function getJson(path, options) {
  const response = await fetch(path, options);
  if (!response.ok) {
    const problem = await response.json().catch(() => ({}));
    throw new Error(requestError(response.status, problem.error));
  }
  return response.json();
}

function showError(message, target = "error") {
  const error = element(target);
  error.textContent = message;
  error.hidden = !message;
}

function updateDateFields(canStart) {
  const byDate = limit.value === "dates";
  element("date-fields").hidden = !byDate;
  element("date-zone").hidden = !byDate;
  element("date-zone").textContent = `${t("Daty wykonania", "Capture dates")} · ${dateZone}`;
  dateFrom.disabled = dateTo.disabled = !canStart || !byDate;
  dateTo.min = dateFrom.value;
  for (const button of datePresets) button.disabled = !canStart;
}

function localDate(value) {
  return [
    String(value.getFullYear()).padStart(4, "0"),
    String(value.getMonth() + 1).padStart(2, "0"),
    String(value.getDate()).padStart(2, "0"),
  ].join("-");
}

function selectDownloadDates(start, end, timezone = browserZone) {
  dateFrom.value = start;
  dateTo.value = end;
  dateZone = timezone;
  limit.value = "dates";
  updateControls();
}

function chooseRecentDays(days) {
  const today = new Date();
  const first = new Date(today);
  first.setDate(today.getDate() - (days - 1));
  selectDownloadDates(localDate(first), localDate(today));
}

function updateControls() {
  const status = operation?.job.status;
  const active = ["running", "scanning", "stopping"].includes(status);
  const canWork = !sending && !active && operation && account.value;
  const canStart = canWork && operation.ready;
  limit.disabled = !canStart;
  updateDateFields(canStart);
  element("run").disabled = !canStart;
  element("run").textContent = status === "stopped" && operation.job.action === "run" ? t("Wznów pobieranie", "Resume download") : t("Uruchom pobieranie", "Start download");
  element("scan").disabled = !canWork;
  element("stop").disabled = sending || !["running", "scanning"].includes(status);
  element("stop").textContent = status === "scanning" ? t("Zatrzymaj skan", "Stop scan") : t("Zatrzymaj", "Stop");
  updateMonthAction();
}

function monthRange(folder) {
  if (!/^(?!0000)\d{4}\/(0[1-9]|1[0-2])$/.test(folder)) return null;
  const start = `${folder.replace("/", "-")}-01`;
  const last = new Date(start);
  last.setUTCMonth(last.getUTCMonth() + 1, 0);
  return { start, end: last.toISOString().slice(0, 10) };
}

function updateMonthAction() {
  const status = operation?.job.status;
  const active = ["running", "scanning", "stopping"].includes(status);
  let note = "";
  if (sending || !operation) note = t("Panel odczytuje stan zadania.", "Checking job status.");
  else if (!operation.ready) note = t("Archiwum jest niedostępne.", "The archive is unavailable.");
  else if (active) note = t("Dla tego Apple ID trwa zadanie. Pobierz miesiąc po jego zakończeniu.", "This Apple ID has an active job. Download the month once it finishes.");
  monthDownload.disabled = !monthSelection || element("run").disabled;
  const message = element("month-menu-note");
  message.textContent = note;
  message.hidden = !note;
}

function showMonthMenu(month, x, y) {
  monthSelection = monthRange(month);
  if (!monthSelection) return;
  const { start, end } = monthSelection;
  const range = calendarDate.formatRange(new Date(start), new Date(end));
  element("month-menu-range").textContent = `${range} · UTC · ${t("wszystkie typy plików", "all file types")}`;
  updateMonthAction();
  if (!monthMenu.matches(":popover-open")) monthMenu.showPopover();
  monthMenu.style.left = `${Math.max(8, Math.min(x, window.innerWidth - monthMenu.offsetWidth - 8))}px`;
  monthMenu.style.top = `${Math.max(8, Math.min(y, window.innerHeight - monthMenu.offsetHeight - 8))}px`;
  (monthDownload.disabled ? monthMenu : monthDownload).focus();
}

function recentItems(count) {
  const value = number.format(count);
  return t(`${value} ostatnich pozycji`, `${value} most recent ${count === 1 ? "item" : "items"}`);
}

function itemCount(count) {
  const value = number.format(count);
  return t(`${value} pozycji`, `${value} ${count === 1 ? "item" : "items"}`);
}

function showJobStats(job) {
  const active = ["running", "scanning", "stopping"].includes(job?.status);
  const run = job?.action === "run";
  const limited = run && job.limit != null;
  element("progress").dataset.active = String(active);
  element("job-workers").textContent = job ? t(`${Number(active)} z 1`, `${Number(active)} of 1`) : "—";
  element("job-downloaded").textContent = run ? number.format(job.downloaded) : "—";
  element("job-list-label").textContent = limited ? t("Lista do sprawdzenia", "Items to check") : t("Biblioteka iCloud", "iCloud library");
  element("job-list-size").textContent = job?.items != null ? itemCount(job.items) : "—";

  let scope = "—";
  let timezone = "";
  if (job?.date_range) {
    const { start, end } = job.date_range;
    scope = calendarDate.formatRange(new Date(start), new Date(end));
    timezone = job.date_range.timezone;
  } else if (run) {
    scope = limited ? recentItems(job.limit) : t("Cała biblioteka", "Entire library");
  } else if (job?.action === "scan") {
    scope = t("Skan biblioteki", "Library scan");
  }
  const scopeValue = element("job-scope");
  scopeValue.textContent = scope;
  scopeValue.title = timezone ? `${scope} (${timezone})` : scope;

  const filename = job?.current || job?.last || "";
  element("job-file-label").textContent = job?.current ? t("Pobierany plik", "Current file") : job?.last ? t("Ostatni plik", "Last file") : t("Plik", "File");
  const file = element("job-file");
  file.textContent = filename || "—";
  file.title = filename;
}

function showActivity(data) {
  const { job, ready } = data;
  const status = element("status");
  status.dataset.state = ready ? job.status : "failed";
  status.textContent = ready ? labels[job.status] : t("Archiwum niedostępne", "Archive unavailable");
  showJobStats(job);
  let detail = t("Wybierz zakres i uruchom pobieranie. Wcześniejszy skan nie jest potrzebny.", "Choose a range and start the download. You can download without scanning first.");
  if (!ready) detail = t("Sprawdź montowanie katalogu archiwum.", "Check the archive mount.");
  else if (job.status === "scanning") {
    const checked = number.format(job.listed);
    const total = itemCount(job.items);
    detail = job.items == null ? t("Łączenie z iCloud przed skanem.", "Connecting to iCloud for the scan.")
      : t(`Skan iCloud · sprawdzono ${checked} z ${total}.`, `iCloud scan · checked ${checked} of ${total}.`);
  }
  else if (job.status === "stopping") detail = t("Zatrzymywanie procesu.", "Stopping the worker.");
  else if (job.status === "failed") detail = problems[job.issue] || t("Proces zakończył się błędem. Sprawdź sesję iCloud.", "The worker failed. Check your iCloud session.");
  else if (job.status === "finished" && job.action === "run" && !job.downloaded) {
    detail = t("Brak nowych plików w tym zakresie.", "No new files in this range.");
    if (job.limit || job.date_range) detail += t(" Możesz wybrać inny zakres.", " You can choose another range.");
  }
  else if (job.status === "running") {
    if (job.items == null) detail = t("Łączenie z iCloud.", "Connecting to iCloud.");
    else if (job.date_range) detail = t("Sprawdzanie dat w bibliotece i pobieranie braków z wybranego zakresu.", "Checking library dates and downloading missing files in the selected range.");
    else detail = t("Sprawdzanie i pobieranie brakujących plików.", "Checking for and downloading missing files.");
  } else if (job.status === "finished") detail = job.action === "run" ? t("Pobieranie zakończone.", "Download finished.")
    : t("Skan zakończony. Lista braków jest dostępna w zakładce Do pobrania.", "Scan finished. See missing files in the Pending tab.");
  else if (job.status === "stopped") detail = t("Możesz ponownie uruchomić skan lub pobieranie.", "You can start another scan or download.");

  element("activity").textContent = detail;
}

function showLibraryCounts(data) {
  element("downloaded-count").textContent = number.format(data.downloaded);
  element("pending-count").textContent = data.pending === null ? "—" : number.format(data.pending);
  element("scope").textContent = layout.value === "gallery" && view === "downloaded" && data.downloaded
    ? groupingEnabled() && groupInterval.value !== "0" ? t("Kliknij grupę, aby zobaczyć jej pliki", "Click a group to see its files") : t("Dwuklik otwiera podgląd", "Double-click to preview")
    : "";
}

function showStorage(data) {
  const filtered = mediaFilter.value !== "both" || folderFilter.value;
  for (const [key, metrics] of [["cloud", data.cloud], ["local", data.local]]) {
    setBytes(element(`${key}-total`), metrics?.bytes, key === "cloud" && !metrics?.reported_at && data.scan?.unmeasured > 0);
    const info = element(`${key}-filtered`);
    info.hidden = !metrics || (!filtered && (metrics.filtered === null || metrics.bytes === metrics.filtered));
    const label = filtered ? t("Wybrane", "Selected") : key === "cloud" && metrics?.reported_at ? t("Dostępne do eksportu", "Available to export") : t("Obrazy i filmy", "Images and videos");
    if (metrics) info.textContent = `${label}: ${bytes(metrics.filtered)}`;
  }
  const scope = element("cloud-scope");
  const details = [];
  if (data.cloud?.reported_at) details.push(`${t("Zajęte miejsce wg Apple", "Storage reported by Apple")} · ${date.format(new Date(data.cloud.reported_at))}`);
  if (data.scan?.complete) details.push(`${t("Skan biblioteki głównej", "Main library scan")} · ${date.format(new Date(data.scan.time))}`);
  else if (data.job.status === "scanning") details.push(t("Trwa odczyt rozmiarów plików i katalogów", "Scanning file and folder sizes"));
  else details.push(t("Użyj „Skanuj iCloud”, aby odczytać rozmiary plików i katalogów", "Use Scan iCloud to read file and folder sizes"));
  if (data.scan?.unmeasured) details.push(`${t("Brak rozmiaru", "Size unavailable")}: ${itemCount(data.scan.unmeasured)}`);
  scope.textContent = details.join(" · ");

  const signature = JSON.stringify(data.folders);
  if (signature === folderFingerprint) return;
  folderFingerprint = signature;
  const table = element("folder-sizes");
  if (table.rows.length === data.folders.length &&
      data.folders.every((folder, index) => table.rows[index].dataset.folder === folder.name)) {
    data.folders.forEach((folder, index) => updateFolderValues(table.rows[index], folder));
    return;
  }
  const selected = folderFilter.value;
  folderFilter.replaceChildren(new Option(t("Wszystkie katalogi", "All folders"), ""));
  const rows = [];
  for (const folder of data.folders) {
    const name = folder.name === "." ? t("Katalog główny", "Root folder") : folder.name;
    folderFilter.add(new Option(name, folder.name));
    const row = document.createElement("tr");
    row.dataset.folder = folder.name;
    const nameCell = document.createElement("td");
    const pick = document.createElement("button");
    pick.textContent = name;
    pick.title = name;
    pick.addEventListener("click", () => {
      folderFilter.value = folder.name;
      page = 1;
      refresh();
    });
    nameCell.append(pick);
    const cloud = document.createElement("td");
    const local = document.createElement("td");
    const ratio = document.createElement("td");
    ratio.className = "folder-ratio";
    ratio.title = t("Udział lokalnego rozmiaru w rozmiarze iCloud dla wybranego typu plików.", "Local size divided by iCloud size for the selected file type.");
    const actions = document.createElement("td");
    actions.className = "folder-action-cell";
    if (monthRange(folder.name)) {
      row.dataset.month = folder.name;
      const menuButton = document.createElement("button");
      menuButton.type = "button";
      menuButton.className = "folder-actions";
      menuButton.textContent = "⋯";
      menuButton.setAttribute("aria-label", t(`Akcje miesiąca ${folder.name}`, `Actions for ${folder.name}`));
      menuButton.setAttribute("aria-controls", "month-menu");
      menuButton.addEventListener("click", () => {
        const bounds = menuButton.getBoundingClientRect();
        showMonthMenu(folder.name, bounds.left, bounds.bottom);
      });
      actions.append(menuButton);
    }
    row.append(nameCell, cloud, local, ratio, actions);
    updateFolderValues(row, folder);
    rows.push(row);
  }
  if (selected && !data.folders.some((folder) => folder.name === selected)) {
    folderFilter.add(new Option(selected, selected));
  }
  folderFilter.value = selected;
  table.replaceChildren(...rows);
}

function showPreview(path, kind) {
  if (viewer.open) return;
  const video = kind === "video";
  const content = document.createElement(video ? "video" : "img");
  const name = path.split("/").pop();
  if (video) {
    content.controls = true;
    content.autoplay = true;
  } else {
    content.alt = name;
  }
  content.src = mediaUrl(path, "preview");
  content.addEventListener("error", () => {
    if (element("preview").firstChild !== content) return;
    const message = document.createElement("p");
    message.textContent = t("Podgląd tego formatu jest niedostępny. Otwórz oryginał.", "Preview is unavailable for this format. Open the original file.");
    element("preview").replaceChildren(message);
  });
  element("filename").textContent = name;
  element("original").href = mediaUrl(path, "original");
  element("preview").replaceChildren(content);
  element("close-viewer").setAttribute("aria-label", groupViewer.open ? t("Wróć do grupy", "Back to group") : t("Zamknij podgląd", "Close preview"));
  viewer.showModal();
  viewer.requestFullscreen?.().catch(() => {});
}

function renderTime(timestamp) {
  const capture = new Date(timestamp * 1000);
  const time = document.createElement("time");
  time.dateTime = capture.toISOString();
  time.textContent = date.format(capture);
  return time;
}

function renderAsset(path, size, kind, { open, count, timestamp } = {}) {
  const asset = document.createElement("button");
  const thumb = document.createElement("span");
  const image = document.createElement("img");
  const type = document.createElement("span");
  const name = document.createElement("span");
  const metadata = document.createElement("span");
  const fileSize = document.createElement("span");
  asset.className = "asset";
  asset.type = "button";
  asset.title = path;
  thumb.className = "thumb";
  image.alt = "";
  image.loading = "lazy";
  image.src = mediaUrl(path, "thumb");
  image.addEventListener("error", () => image.remove());
  type.className = "type";
  type.textContent = suffix(path).toUpperCase();
  name.className = "asset-name";
  name.textContent = path.split("/").pop();
  metadata.className = "asset-meta";
  setBytes(fileSize, size);
  metadata.append(fileSize);
  if (timestamp) metadata.append(renderTime(timestamp));
  thumb.append(image, type);
  if (count) {
    const badge = document.createElement("span");
    const groupName = kind === "video" ? t("Grupa filmów", "Video group") : t("Grupa zdjęć", "Photo group");
    const groupAction = kind === "video" ? t("Pokaż grupę filmów", "Open video group") : t("Pokaż grupę zdjęć", "Open photo group");
    badge.className = "group-count";
    badge.textContent = `×${number.format(count)}`;
    asset.setAttribute("aria-label", `${groupAction} (${number.format(count)})`);
    name.textContent = groupName;
    thumb.append(badge);
  }
  asset.append(thumb, name, metadata);
  if (open) {
    asset.addEventListener("click", open);
    return asset;
  }
  asset.setAttribute("aria-pressed", "false");
  asset.addEventListener("click", (event) => {
    if (event.detail === 0) {
      showPreview(path, kind);
      return;
    }
    gallery.querySelector('[aria-pressed="true"]')?.setAttribute("aria-pressed", "false");
    asset.setAttribute("aria-pressed", "true");
  });
  asset.addEventListener("dblclick", () => showPreview(path, kind));
  asset.addEventListener("pointerup", (event) => {
    if (event.pointerType === "touch") showPreview(path, kind);
  });
  return asset;
}

function openGroup(path, kind) {
  openGroupRequest = {
    account: account.value, path, media: kind, folder: folderFilter.value,
    group_interval: groupInterval.value, page_size: pageSize.value, page: 1,
    timezone: browserZone, ...galleryQuery(),
  };
  element("group-items").replaceChildren();
  element("group-title").textContent = kind === "video" ? t("Filmy w grupie", "Videos in group") : t("Zdjęcia w grupie", "Photos in group");
  element("group-info").textContent = t("Wczytywanie grupy…", "Loading group…");
  element("group-more").hidden = true;
  groupViewer.showModal();
  loadGroup();
}

async function loadGroup() {
  const request = openGroupRequest;
  if (!request) return;
  const more = element("group-more");
  more.disabled = true;
  try {
    const data = await getJson(`/api/group?${new URLSearchParams(request)}`);
    if (openGroupRequest !== request) return;
    const kind = request.media;
    const label = kind === "video" ? t("Liczba filmów", "Videos") : t("Liczba zdjęć", "Photos");
    element("group-info").textContent = `${label}: ${number.format(data.count)} · ${captureTime.formatRange(data.oldest * 1000, data.newest * 1000)}`;
    element("group-items").append(...data.paths.map((path) => renderAsset(path, data.sizes[path], kind, {
      open: () => showPreview(path, kind),
      timestamp: data.timestamps[path],
    })));
    request.page = data.page + 1;
    more.hidden = data.page >= data.pages;
  } catch (error) {
    if (openGroupRequest !== request) return;
    element("group-info").textContent = error.message;
  } finally {
    if (openGroupRequest === request) more.disabled = false;
  }
}

function renderPending(path, size, timestamp) {
  const row = document.createElement("div");
  const name = document.createElement("span");
  const folder = document.createElement("span");
  const fileSize = document.createElement("span");
  row.className = "pending-row";
  row.title = path;
  name.textContent = path.split("/").pop();
  folder.textContent = path.split("/").slice(0, -1).join("/");
  setBytes(fileSize, size);
  row.append(name, timestamp ? renderTime(timestamp) : folder, fileSize);
  return row;
}

function showItems(data) {
  const { pages } = data;
  page = data.page;
  pageNumber.max = pages;
  if (document.activeElement !== pageNumber) pageNumber.value = page;
  element("previous").disabled = page === 1;
  element("next").disabled = page === pages;
  element("page-count").textContent = `/ ${number.format(pages)}`;
  element("grouping-control").hidden = !groupingEnabled();
  const grouped = groupingEnabled() && groupInterval.value !== "0";
  element("page-size-label").textContent = grouped ? t("Kafle na stronę", "Tiles per page") : t("Pliki na stronę", "Files per page");
  const datesUnavailable = view === "pending" && data.scan && !data.scan.dated;
  const results = element("gallery-result-count");
  results.hidden = !galleryRange || datesUnavailable || (view === "pending" && data.pending === null);
  results.textContent = `${grouped ? t("Kafle", "Tiles") : t("Pliki", "Files")}: ${number.format(data.total)}`;
  const dateNote = element("gallery-date-note");
  dateNote.hidden = !datesUnavailable || (!galleryRange && sortBy.value !== "date");
  dateNote.textContent = t("Ten skan nie zawiera dat wykonania. Użyj „Skanuj iCloud”, aby je odczytać.", "This scan has no capture dates. Use Scan iCloud to fetch them.");

  const key = JSON.stringify([account.value, view, galleryRange, page, data.paths, data.sizes, data.timestamps,
    data.groups, data.pending === null, data.ready, datesUnavailable]);
  if (fingerprint === key) return;
  fingerprint = key;
  gallery.classList.toggle("pending", view === "pending");
  if (data.paths.length) {
    gallery.replaceChildren(...data.paths.map((path) => {
      const timestamp = data.timestamps[path];
      if (view === "pending") return renderPending(path, data.sizes[path], timestamp);
      const kind = data.types[path];
      const group = data.groups[path];
      if (group) return renderAsset(path, group.bytes, kind, {
        timestamp, count: group.count, open: () => openGroup(path, kind),
      });
      return renderAsset(path, data.sizes[path], kind, { timestamp });
    }));
  } else if (galleryRange && datesUnavailable) {
    gallery.replaceChildren();
  } else {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = view === "pending" && data.pending === null ? t("Uruchom skan, aby zobaczyć pliki do pobrania.", "Run a scan to see files available to download.")
      : galleryRange ? t("Brak plików w wybranym zakresie dat.", "No files in the selected date range.")
      : view === "downloaded" ? t("Brak pobranych plików.", "No downloaded files.")
      : t("Skan nie znalazł brakujących plików w wybranym zakresie.", "The scan found no missing files in this range.");
    gallery.replaceChildren(empty);
  }
}

async function refresh() {
  if (!account.value) return;
  const current = ++requestId;
  loadingGallery = true;
  try {
    const calendar = layout.value === "calendar";
    const query = new URLSearchParams({
      account: account.value, view, page, page_size: pageSize.value, media: mediaFilter.value,
      folder: folderFilter.value, group_interval: groupingEnabled() ? groupInterval.value : "0",
      timezone: browserZone,
      ...(calendar ? mediaCalendar.query() : galleryQuery()),
    });
    const data = await getJson(`/api/state?${query}`);
    if (current !== requestId) return;
    showLibraryCounts(data);
    showStorage(data);
    if (calendar) {
      let note = "";
      if (!data.ready) note = t("Archiwum jest niedostępne.", "The archive is unavailable.");
      else if (data.calendar === null) note = t("Użyj „Skanuj iCloud”, aby odczytać daty brakujących plików.", "Use Scan iCloud to fetch capture dates for missing files.");
      mediaCalendar.render(data.calendar, note, mediaFilter.value);
    } else {
      showItems(data);
    }
    showError("", "library-error");
  } catch (error) {
    if (current !== requestId) return;
    showError(t(`Nie udało się odświeżyć biblioteki: ${error.message}`,
      `Could not refresh the library: ${error.message}`), "library-error");
  } finally {
    if (current === requestId) loadingGallery = false;
  }
}

async function refreshActivity() {
  const selected = account.value;
  if (!selected) return;
  const current = ++activityId;
  loadingActivity = true;
  try {
    const query = new URLSearchParams({ account: selected });
    const data = await getJson(`/api/jobs?${query}`, { signal: AbortSignal.timeout(10000) });
    if (current !== activityId) return;
    if (restoreScope) {
      restoreScope = false;
      if (data.job.action === "run") selectJobScope(data.job);
    }
    operation = data;
    showActivity(data);
  } catch (error) {
    if (current !== activityId) return;
    const status = element("status");
    status.dataset.state = "failed";
    status.textContent = t("Brak stanu", "Status unavailable");
    showJobStats();
    element("activity").textContent = t("Nie udało się odczytać stanu pobierania. Panel ponowi próbę.", "Could not read the download status. Retrying.");
    operation = undefined;
  } finally {
    if (current === activityId) {
      loadingActivity = false;
      updateControls();
    }
  }
}

async function control(action) {
  const body = { account: account.value, action };
  if (action === "run") {
    if (limit.value === "dates") {
      if (!dateFrom.reportValidity() || !dateTo.reportValidity()) return;
      body.date_range = { start: dateFrom.value, end: dateTo.value, timezone: dateZone };
    } else {
      body.limit = limit.value ? Number(limit.value) : null;
    }
  }
  sending = true;
  account.disabled = true;
  ++activityId;
  showError("");
  updateControls();
  try {
    await getJson("/api/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CloudExtractor": "1" },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(10000),
    });
  } catch (error) {
    showError(error.message);
  } finally {
    await refreshActivity();
    sending = false;
    account.disabled = false;
    updateControls();
  }
}

limit.addEventListener("change", updateControls);
dateFrom.addEventListener("change", updateControls);
for (const button of datePresets) {
  button.addEventListener("click", () => chooseRecentDays(Number(button.dataset.days)));
}
element("run").addEventListener("click", () => control("run"));
element("stop").addEventListener("click", () => control("stop"));
element("scan").addEventListener("click", () => control("scan"));
element("folder-sizes").addEventListener("contextmenu", (event) => {
  const row = event.target.closest("tr[data-month]");
  if (!row) return;
  event.preventDefault();
  const bounds = row.querySelector(".folder-actions").getBoundingClientRect();
  showMonthMenu(row.dataset.month, event.clientX || bounds.left, event.clientY || bounds.bottom);
});
monthDownload.addEventListener("click", () => {
  if (monthDownload.disabled || !monthSelection) return;
  const { start, end } = monthSelection;
  monthMenu.hidePopover();
  selectDownloadDates(start, end, "UTC");
  control("run");
});
element("previous").addEventListener("click", () => { page -= 1; refresh(); });
element("next").addEventListener("click", () => { page += 1; refresh(); });
element("page-jump").addEventListener("submit", (event) => {
  event.preventDefault();
  page = pageNumber.valueAsNumber;
  pageNumber.blur();
  refresh();
});
element("close-group").addEventListener("click", () => groupViewer.close());
element("group-more").addEventListener("click", loadGroup);
groupViewer.addEventListener("close", () => {
  openGroupRequest = undefined;
  element("group-items").replaceChildren();
});
element("close-viewer").addEventListener("click", () => viewer.close());
viewer.addEventListener("close", () => {
  element("preview").replaceChildren();
  if (document.fullscreenElement === viewer) document.exitFullscreen().catch(() => {});
});
document.addEventListener("fullscreenchange", () => {
  if (!document.fullscreenElement && viewer.open) viewer.close();
});
account.addEventListener("change", () => {
  if (monthMenu.matches(":popover-open")) monthMenu.hidePopover();
  monthSelection = undefined;
  page = 1;
  galleryRange = undefined;
  galleryDateFrom.value = galleryDateTo.value = galleryDateTo.min = "";
  updateLibraryLayout();
  operation = undefined;
  restoreScope = true;
  folderFilter.value = "";
  chooseRecentDays(1);
  showError("");
  showError("", "library-error");
  const status = element("status");
  status.dataset.state = "idle";
  status.textContent = t("Łączenie", "Connecting");
  showJobStats();
  element("activity").textContent = "";
  updateControls();
  refreshActivity();
  refresh();
});
for (const filter of [mediaFilter, folderFilter, pageSize, groupInterval, sortBy, sortOrder]) {
  filter.addEventListener("change", () => { page = 1; refresh(); });
}
const tabs = [...document.querySelectorAll("[data-view]")];
for (const tab of tabs) {
  tab.addEventListener("click", () => {
    view = tab.dataset.view;
    page = 1;
    for (const item of tabs) item.setAttribute("aria-selected", String(item === tab));
    element("library-content").setAttribute("aria-labelledby", tab.id);
    updateLibraryLayout();
    refresh();
  });
}
layout.addEventListener("change", () => {
  page = 1;
  updateLibraryLayout();
  refresh();
});
galleryDateFrom.addEventListener("input", () => { galleryDateTo.min = galleryDateFrom.value; });
element("gallery-date-form").addEventListener("submit", (event) => {
  event.preventDefault();
  setGalleryRange(galleryDateFrom.value, galleryDateTo.value);
  element("gallery-date-filter").open = false;
  clearRange.focus();
});
clearRange.addEventListener("click", () => {
  setGalleryRange();
  element("gallery-date-filter").querySelector("summary").focus();
});

function limitOption(value) {
  const optionValue = value === null ? "" : String(value);
  if (![...limit.options].some((option) => option.value === optionValue)) {
    limit.add(new Option(recentItems(value), optionValue), 0);
  }
  return optionValue;
}

function selectJobScope(job) {
  if (job.date_range) {
    dateFrom.value = job.date_range.start;
    dateTo.value = job.date_range.end;
    dateZone = job.date_range.timezone;
    limit.value = "dates";
  } else {
    limit.value = limitOption(job.limit);
  }
}

async function initialize() {
  localizeDocument();
  chooseRecentDays(1);
  updateLibraryLayout();
  try {
    const settings = await getJson("/api/accounts");
    for (const name of settings.accounts) account.add(new Option(name, name));
    if (!settings.accounts.length) {
      element("status").textContent = t("Brak konta", "No account");
      element("activity").textContent = t("Skonfiguruj Apple ID i zaloguj się przez SSH.", "Configure your Apple ID and sign in over SSH.");
      return;
    }
    account.disabled = false;
    limitOption(settings.default_limit || 20);
    refreshActivity();
    refresh();
    setInterval(() => {
      if (!loadingActivity && !sending) refreshActivity();
      if (!loadingGallery) refresh();
    }, 2500);
  } catch (error) {
    showError(error.message);
  }
}

initialize();
