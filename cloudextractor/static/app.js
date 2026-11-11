const element = (id) => document.getElementById(id);
const account = element("account");
const limit = element("limit");
const gallery = element("items");
const viewer = element("viewer");
const number = new Intl.NumberFormat("pl-PL");
const date = new Intl.DateTimeFormat("pl-PL", { dateStyle: "short", timeStyle: "short" });
const videos = new Set(["mov", "mp4", "m4v", "webm", "avi", "mts", "3gp", "mpeg", "mpg"]);
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
let requestId = 0;
let sending = false;
let defaultLimit = 20;

function suffix(path) {
  return path.split(".").pop().toLowerCase();
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
  const canStart = !sending && !active && snapshot?.ready && account.value;
  limit.disabled = !canStart;
  element("run").disabled = !canStart;
  element("run").textContent = status === "stopped" && snapshot.job.action === "run" ? "Wznów" : "Uruchom";
  element("scan").disabled = !canStart;
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
  else if (job.status === "scanning") detail = "Odczytywanie listy brakujących plików.";
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
  const scope = scan?.limit ? `${number.format(scan.limit)} ostatnich pozycji` : "cała biblioteka";
  element("scope").textContent = view === "downloaded" && data.downloaded ? "Dwuklik otwiera podgląd"
    : view === "pending" && scan ? `Skan ${date.format(new Date(scan.time))} · ${scope}` : "";
}

function showPreview(path) {
  if (viewer.open) return;
  const video = videos.has(suffix(path));
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
  viewer.showModal();
  viewer.requestFullscreen?.().catch(() => {});
}

function renderAsset(path) {
  const asset = document.createElement("button");
  const thumb = document.createElement("span");
  const image = document.createElement("img");
  const type = document.createElement("span");
  const name = document.createElement("span");
  asset.className = "asset";
  asset.type = "button";
  asset.setAttribute("aria-pressed", "false");
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
  thumb.append(image, type);
  asset.append(thumb, name);
  asset.addEventListener("click", (event) => {
    if (event.detail === 0) {
      showPreview(path);
      return;
    }
    gallery.querySelector('[aria-pressed="true"]')?.setAttribute("aria-pressed", "false");
    asset.setAttribute("aria-pressed", "true");
  });
  asset.addEventListener("dblclick", () => showPreview(path));
  asset.addEventListener("pointerup", (event) => {
    if (event.pointerType === "touch") showPreview(path);
  });
  return asset;
}

function renderPending(path) {
  const row = document.createElement("div");
  const name = document.createElement("span");
  const date = document.createElement("span");
  row.className = "pending-row";
  name.textContent = path.split("/").pop();
  date.textContent = path.split("/").slice(0, -1).join("/");
  row.append(name, date);
  return row;
}

function showItems(data) {
  const pages = Math.max(1, Math.ceil(data.total / data.page_size));
  if (page > pages) {
    page = pages;
    refresh();
    return;
  }
  element("pagination").hidden = pages === 1;
  element("previous").disabled = page === 1;
  element("next").disabled = page === pages;
  element("page").textContent = `${number.format(page)} / ${number.format(pages)}`;

  const key = JSON.stringify([account.value, view, page, data.paths, data.pending === null, data.ready]);
  if (fingerprint === key) return;
  fingerprint = key;
  gallery.classList.toggle("pending", view === "pending");
  gallery.setAttribute("aria-labelledby", `tab-${view}`);
  if (data.paths.length) {
    gallery.replaceChildren(...data.paths.map(view === "pending" ? renderPending : renderAsset));
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
    const data = await getJson(`/api/state?${new URLSearchParams({ account: account.value, view, page })}`);
    if (current !== requestId) return;
    if (!snapshot && data.job.action) selectLimit(data.job.limit);
    snapshot = data;
    showActivity(data);
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
  selectLimit(defaultLimit);
  showError("");
  updateControls();
  refresh();
});
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
