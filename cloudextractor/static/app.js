const element = (id) => document.getElementById(id);
const account = element("account");
const limit = element("limit");
const mediaFilter = element("media-filter");
const folderFilter = element("folder-filter");
const pageSize = element("page-size");
const pageNumber = element("page-number");
const groupInterval = element("group-interval");
const gallery = element("items");
const groupViewer = element("group-viewer");
const viewer = element("viewer");
const number = new Intl.NumberFormat("pl-PL");
const date = new Intl.DateTimeFormat("pl-PL", { dateStyle: "short", timeStyle: "short" });
const captureTime = new Intl.DateTimeFormat("pl-PL", { dateStyle: "short", timeStyle: "medium" });
const decimal = new Intl.NumberFormat("pl-PL", { maximumFractionDigits: 2 });
const labels = {
  idle: "Gotowy", running: "Pobieranie", scanning: "Skanowanie", stopping: "Zatrzymywanie",
  stopped: "Zatrzymano", finished: "Zakończono", failed: "Błąd",
};
const problems = {
  auth: "Sesja iCloud wymaga ponownego logowania przez SSH.",
  busy: "Inny proces korzysta z sesji tego konta.",
  configuration: "Sprawdź konfigurację i dostęp do archiwum.",
  cache: "Nie udało się zapisać wyników skanu.",
};

let view = "downloaded";
let page = 1;
let snapshot;
let fingerprint;
let folderFingerprint;
let requestId = 0;
let sending = false;
let defaultLimit = 20;
let openGroupRequest;

function groupingEnabled() {
  return view === "downloaded" && mediaFilter.value !== "video";
}

function suffix(path) {
  return path.split(".").pop().toLowerCase();
}

function bytes(size) {
  if (size === null || size === undefined) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let unit = 0;
  while (size >= 1000 && unit < units.length - 1) {
    size /= 1000;
    unit += 1;
  }
  return `${decimal.format(size)} ${units[unit]}`;
}

function setBytes(target, size, minimum = false) {
  target.textContent = `${minimum && size !== null && size !== undefined ? "≥ " : ""}${bytes(size)}`;
  target.title = size === null || size === undefined ? "" : `${number.format(size)} B`;
}

function mediaUrl(path, kind) {
  return `/api/media?${new URLSearchParams({ account: account.value, path, kind })}`;
}

async function getJson(path, options) {
  const response = await fetch(path, options);
  if (!response.ok) {
    const problem = await response.json().catch(() => ({}));
    throw new Error(problem.error || `Żądanie nie powiodło się (${response.status}).`);
  }
  return response.json();
}

function showError(message) {
  const error = element("error");
  error.textContent = message;
  error.hidden = !message;
}

function updateControls() {
  const status = snapshot?.job.status;
  const active = ["running", "scanning", "stopping"].includes(status);
  const canWork = !sending && !active && snapshot && account.value;
  const canStart = canWork && snapshot.ready;
  limit.disabled = !canStart;
  element("run").disabled = !canStart;
  element("run").textContent = status === "stopped" && snapshot.job.action === "run" ? "Wznów" : "Uruchom";
  element("scan").disabled = !canWork;
  element("stop").disabled = sending || !["running", "scanning"].includes(status);
}

function showActivity(data) {
  const { job, scan, ready } = data;
  const status = element("status");
  status.dataset.state = ready ? job.status : "failed";
  status.textContent = ready ? labels[job.status] : "Archiwum niedostępne";
  element("progress").hidden = !["running", "scanning", "stopping"].includes(job.status);

  let detail = "Wybierz zakres i uruchom pobieranie lub skan iCloud.";
  if (!ready) detail = "Sprawdź montowanie katalogu archiwum.";
  else if (job.status === "scanning") {
    detail = "Odczytywanie rozmiarów z iCloud.";
    if (job.items) detail += ` · ${number.format(job.listed)} / ${number.format(job.items)} pozycji`;
  }
  else if (job.status === "stopping") detail = "Zatrzymywanie procesu.";
  else if (job.status === "failed") detail = problems[job.issue] || "Proces zakończył się błędem. Sprawdź sesję iCloud.";
  else if (job.action === "run") {
    detail = `Pobrane w tym przebiegu: ${number.format(job.downloaded)}`;
    if (job.items) detail += ` · Pozycje do sprawdzenia: ${number.format(job.items)}`;
    if (job.current) detail += ` · Pobierany plik: ${job.current}`;
    else if (job.last) detail += ` · Ostatni plik: ${job.last}`;
  } else if (job.status === "finished" && scan) detail = "Skan zakończony. Lista braków jest dostępna w zakładce Do pobrania.";
  else if (job.status === "stopped") detail = "Możesz ponownie uruchomić skan lub pobieranie.";

  element("activity").textContent = detail;
  element("downloaded-count").textContent = number.format(data.downloaded);
  element("pending-count").textContent = data.pending === null ? "—" : number.format(data.pending);
  element("scope").textContent = view === "downloaded" && data.downloaded
    ? groupingEnabled() && groupInterval.value !== "0" ? "Kliknij grupę, aby zobaczyć zdjęcia" : "Dwuklik otwiera podgląd"
    : "";
}

function showStorage(data) {
  const filtered = mediaFilter.value !== "both" || folderFilter.value;
  for (const [key, metrics] of [["cloud", data.cloud], ["local", data.local]]) {
    setBytes(element(`${key}-total`), metrics?.bytes, key === "cloud" && !metrics?.reported_at && data.scan?.unmeasured > 0);
    const info = element(`${key}-filtered`);
    info.hidden = !metrics || (!filtered && (metrics.filtered === null || metrics.bytes === metrics.filtered));
    const label = filtered ? "Wybrane" : key === "cloud" && metrics?.reported_at ? "Dostępne do eksportu" : "Obrazy i filmy";
    if (metrics) info.textContent = `${label}: ${bytes(metrics.filtered)}`;
  }
  const scope = element("cloud-scope");
  const details = [];
  if (data.cloud?.reported_at) details.push(`Zajęte miejsce wg Apple · ${date.format(new Date(data.cloud.reported_at))}`);
  if (data.scan?.complete) details.push(`Skan biblioteki głównej ${date.format(new Date(data.scan.time))}`);
  else if (data.job.status === "scanning") details.push("Trwa odczyt rozmiarów plików i katalogów");
  else details.push("Użyj „Skanuj iCloud”, aby odczytać rozmiary plików i katalogów");
  if (data.scan?.unmeasured) details.push(`Brak rozmiaru: ${number.format(data.scan.unmeasured)} pozycji`);
  scope.textContent = details.join(" · ");

  const signature = JSON.stringify(data.folders);
  if (signature === folderFingerprint) return;
  folderFingerprint = signature;
  const selected = folderFilter.value;
  folderFilter.replaceChildren(new Option("Wszystkie katalogi", ""));
  const rows = [];
  for (const folder of data.folders) {
    const name = folder.name === "." ? "Katalog główny" : folder.name;
    folderFilter.add(new Option(name, folder.name));
    const row = document.createElement("tr");
    const nameCell = document.createElement("td");
    const pick = document.createElement("button");
    pick.textContent = name;
    pick.addEventListener("click", () => {
      folderFilter.value = folder.name;
      page = 1;
      refresh();
    });
    nameCell.append(pick);
    const cloud = document.createElement("td");
    const local = document.createElement("td");
    setBytes(cloud, folder.cloud);
    setBytes(local, folder.local);
    row.append(nameCell, cloud, local);
    rows.push(row);
  }
  if (selected && !data.folders.some((folder) => folder.name === selected)) {
    folderFilter.add(new Option(selected, selected));
  }
  folderFilter.value = selected;
  element("folder-sizes").replaceChildren(...rows);
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
    message.textContent = "Podgląd tego formatu jest niedostępny. Otwórz oryginał.";
    element("preview").replaceChildren(message);
  });
  element("filename").textContent = name;
  element("original").href = mediaUrl(path, "original");
  element("preview").replaceChildren(content);
  element("close-viewer").setAttribute("aria-label", groupViewer.open ? "Wróć do grupy" : "Zamknij podgląd");
  viewer.showModal();
  viewer.requestFullscreen?.().catch(() => {});
}

function renderAsset(path, size, kind, { open, count } = {}) {
  const asset = document.createElement("button");
  const thumb = document.createElement("span");
  const image = document.createElement("img");
  const type = document.createElement("span");
  const name = document.createElement("span");
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
  fileSize.className = "asset-size";
  setBytes(fileSize, size);
  thumb.append(image, type);
  if (count) {
    const badge = document.createElement("span");
    badge.className = "group-count";
    badge.textContent = `×${number.format(count)}`;
    asset.setAttribute("aria-label", `Pokaż grupę zdjęć (${number.format(count)})`);
    name.textContent = "Grupa zdjęć";
    thumb.append(badge);
  }
  asset.append(thumb, name, fileSize);
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

function openGroup(path) {
  openGroupRequest = {
    account: account.value, path, folder: folderFilter.value,
    group_interval: groupInterval.value, page_size: pageSize.value, page: 1,
  };
  element("group-items").replaceChildren();
  element("group-info").textContent = "Wczytywanie grupy…";
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
    element("group-info").textContent = `Liczba zdjęć ${number.format(data.count)} · ${captureTime.formatRange(data.oldest * 1000, data.newest * 1000)}`;
    element("group-items").append(...data.paths.map((path) => renderAsset(path, data.sizes[path], "image", {
      open: () => showPreview(path, "image"),
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

function renderPending(path, size) {
  const row = document.createElement("div");
  const name = document.createElement("span");
  const date = document.createElement("span");
  const fileSize = document.createElement("span");
  row.className = "pending-row";
  name.textContent = path.split("/").pop();
  date.textContent = path.split("/").slice(0, -1).join("/");
  setBytes(fileSize, size);
  row.append(name, date, fileSize);
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
  element("page-size-label").textContent = groupingEnabled() && groupInterval.value !== "0" ? "Kafle na stronę" : "Pliki na stronę";

  const key = JSON.stringify([account.value, view, page, data.paths, data.sizes, data.groups, data.pending === null, data.ready]);
  if (fingerprint === key) return;
  fingerprint = key;
  gallery.classList.toggle("pending", view === "pending");
  gallery.setAttribute("aria-labelledby", `tab-${view}`);
  if (data.paths.length) {
    gallery.replaceChildren(...data.paths.map((path) => {
      if (view === "pending") return renderPending(path, data.sizes[path]);
      const group = data.groups[path];
      if (group) return renderAsset(path, group.bytes, data.types[path], { count: group.count, open: () => openGroup(path) });
      return renderAsset(path, data.sizes[path], data.types[path]);
    }));
  } else {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = view === "downloaded" ? "Brak pobranych plików."
      : data.pending === null ? "Uruchom skan, aby zobaczyć pliki do pobrania."
        : "Skan nie znalazł brakujących plików w wybranym zakresie.";
    gallery.replaceChildren(empty);
  }
}

async function refresh() {
  if (!account.value) return;
  const current = ++requestId;
  try {
    const query = new URLSearchParams({
      account: account.value, view, page, page_size: pageSize.value, media: mediaFilter.value,
      folder: folderFilter.value, group_interval: groupingEnabled() ? groupInterval.value : "0",
    });
    const data = await getJson(`/api/state?${query}`);
    if (current !== requestId) return;
    if (!snapshot && data.job.action === "run") selectLimit(data.job.limit);
    snapshot = data;
    showActivity(data);
    showStorage(data);
    showItems(data);
  } catch (error) {
    if (current !== requestId) return;
    snapshot = undefined;
    showError(error.message);
  }
  updateControls();
}

async function control(action) {
  sending = true;
  showError("");
  updateControls();
  try {
    await getJson("/api/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CloudExtractor": "1" },
      body: JSON.stringify({ account: account.value, action, limit: limit.value ? Number(limit.value) : null }),
    });
  } catch (error) {
    showError(error.message);
  } finally {
    sending = false;
    refresh();
  }
}

element("run").addEventListener("click", () => control("run"));
element("stop").addEventListener("click", () => control("stop"));
element("scan").addEventListener("click", () => control("scan"));
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
  page = 1;
  snapshot = undefined;
  folderFilter.value = "";
  selectLimit(defaultLimit);
  showError("");
  updateControls();
  refresh();
});
for (const filter of [mediaFilter, folderFilter, pageSize, groupInterval]) {
  filter.addEventListener("change", () => { page = 1; refresh(); });
}
const tabs = [...document.querySelectorAll("[data-view]")];
for (const tab of tabs) {
  tab.addEventListener("click", () => {
    view = tab.dataset.view;
    page = 1;
    for (const item of tabs) item.setAttribute("aria-selected", String(item === tab));
    refresh();
  });
}

function selectLimit(value) {
  const optionValue = value === null ? "" : String(value);
  if (![...limit.options].some((option) => option.value === optionValue)) {
    limit.add(new Option(`${optionValue} ostatnich pozycji`, optionValue), 0);
  }
  limit.value = optionValue;
}

async function initialize() {
  try {
    const settings = await getJson("/api/accounts");
    for (const name of settings.accounts) account.add(new Option(name, name));
    if (!settings.accounts.length) {
      element("status").textContent = "Brak konta";
      element("activity").textContent = "Skonfiguruj Apple ID i zaloguj się przez SSH.";
      return;
    }
    account.disabled = false;
    defaultLimit = settings.default_limit || 20;
    selectLimit(defaultLimit);
    await refresh();
    setInterval(refresh, 2500);
  } catch (error) {
    showError(error.message);
  }
}

initialize();
