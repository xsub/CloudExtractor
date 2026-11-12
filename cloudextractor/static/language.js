const languageKey = "cloudextractor-language";
const url = new URL(window.location.href);
const supported = ["pl", "en"];
let saved;
try {
  saved = localStorage.getItem(languageKey);
} catch {
  // The URL remains usable if browser storage is unavailable.
}
const requested = url.searchParams.get("lang");
const language = supported.includes(requested) ? requested : supported.includes(saved) ? saved : "pl";

export const locale = language === "en" ? "en-GB" : "pl-PL";
export const t = (polish, english) => language === "en" ? english : polish;

export function localizeDocument() {
  document.documentElement.lang = language;
  if (language === "en") {
    for (const node of document.querySelectorAll("[data-en]")) node.textContent = node.dataset.en;
    for (const node of document.querySelectorAll("[data-en-label]")) node.setAttribute("aria-label", node.dataset.enLabel);
  }
  if (supported.includes(requested)) {
    try {
      localStorage.setItem(languageKey, language);
    } catch {
      // The selected language is still present in the URL.
    }
  }
  const picker = document.getElementById("language");
  picker.value = language;
  picker.addEventListener("change", () => {
    url.searchParams.set("lang", picker.value);
    window.location.assign(url);
  });
}

// The job API returns human-readable Polish errors.
const errors = new Map([
  ["Wybierz zakres dat albo liczbę pozycji.", "Choose a date range or a number of items."],
  ["Podaj poprawne daty i strefę czasową.", "Enter valid dates and a time zone."],
  ["Data końcowa musi być równa lub późniejsza od początkowej.", "The end date must be on or after the start date."],
  ["Podany zakres nie obejmuje żadnej godziny w tej strefie czasowej.", "The selected range contains no time in this time zone."],
  ["Archiwum jest niedostępne. Sprawdź montowanie na hoście.", "The archive is unavailable. Check the mount on the host."],
  ["Dla tego konta trwa już operacja.", "A job is already running for this account."],
  ["Dla tego konta nie ma aktywnej operacji panelu.", "No dashboard job is running for this account."],
  ["Nie udało się uruchomić procesu.", "Could not start the worker."],
]);

export function requestError(status, message) {
  const fallback = t(`Żądanie nie powiodło się (${status}).`, `Request failed (${status}).`);
  return t(message || fallback, errors.get(message) || fallback);
}
