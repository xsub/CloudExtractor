import { locale, t } from "./language.js";

const monthLabel = new Intl.DateTimeFormat(locale, { month: "long", year: "numeric", timeZone: "UTC" });
const dayLabel = new Intl.DateTimeFormat(locale, { dateStyle: "full", timeZone: "UTC" });
const number = new Intl.NumberFormat(locale);
const weekdays = t(["Pn", "Wt", "Śr", "Cz", "Pt", "So", "Nd"], ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]);

function today() {
  const now = new Date();
  return new Date(Date.UTC(now.getFullYear(), now.getMonth(), now.getDate()));
}

function monthOf(date, offset = 0, day = 1) {
  return new Date(Date.UTC(date.getUTCFullYear(), date.getUTCMonth() + offset, day));
}

function dateKey(date) {
  return date.toISOString().slice(0, 10);
}

export class MediaCalendar {
  constructor(panel, onChange, onSelect) {
    this.grid = panel.querySelector("#calendar-months");
    this.size = panel.querySelector("#calendar-size");
    this.note = panel.querySelector("#calendar-note");
    this.period = panel.querySelector("#calendar-period");
    this.next = panel.querySelector("#calendar-next");
    this.anchor = monthOf(today());
    this.onSelect = onSelect;
    this.size.addEventListener("change", onChange);
    panel.querySelector("#calendar-previous").addEventListener("click", () => {
      this.anchor = monthOf(this.anchor, -Number(this.size.value));
      onChange();
    });
    panel.querySelector("#calendar-today").addEventListener("click", () => {
      this.anchor = monthOf(today());
      onChange();
    });
    this.next.addEventListener("click", () => {
      const next = monthOf(this.anchor, Number(this.size.value));
      this.anchor = next > monthOf(today()) ? monthOf(today()) : next;
      onChange();
    });
  }

  months() {
    return Array.from({ length: Number(this.size.value) }, (_, offset) => monthOf(this.anchor, -offset));
  }

  query() {
    const months = this.months();
    return {
      calendar_from: dateKey(months.at(-1)),
      calendar_to: dateKey(monthOf(this.anchor, 1, 0)),
    };
  }

  render(counts, note, media) {
    const months = this.months();
    const first = monthLabel.format(months.at(-1));
    const last = monthLabel.format(this.anchor);
    const todayKey = dateKey(today());
    this.period.textContent = first === last ? first : `${first} – ${last}`;
    this.next.disabled = this.anchor >= monthOf(today());
    this.note.textContent = note;
    this.note.hidden = !note;
    const signature = JSON.stringify([first, last, todayKey, media, counts]);
    if (this.signature === signature) return;
    this.signature = signature;
    this.grid.replaceChildren(...months.map((month) => this.renderMonth(month, counts, media, todayKey)));
  }

  renderMonth(month, counts, media, todayKey) {
    const card = document.createElement("section");
    const heading = document.createElement("h3");
    const grid = document.createElement("div");
    card.className = "calendar-month";
    heading.textContent = monthLabel.format(month);
    grid.className = "calendar-grid";
    for (const weekday of weekdays) {
      const label = document.createElement("span");
      label.className = "calendar-weekday";
      label.textContent = weekday;
      grid.append(label);
    }
    const padding = (month.getUTCDay() + 6) % 7;
    for (let index = 0; index < padding; index += 1) {
      const blank = document.createElement("span");
      blank.setAttribute("aria-hidden", "true");
      grid.append(blank);
    }
    const lastDay = monthOf(month, 1, 0).getUTCDate();
    for (let day = 1; day <= lastDay; day += 1) {
      const date = monthOf(month, 0, day);
      const key = dateKey(date);
      const daily = counts?.[key];
      const images = daily?.image ?? 0;
      const videos = daily?.video ?? 0;
      const hasFiles = images + videos > 0;
      const cell = document.createElement(hasFiles ? "button" : "div");
      cell.className = "calendar-day";
      if (key === todayKey) cell.setAttribute("aria-current", "date");
      const dayNumber = document.createElement("time");
      dayNumber.dateTime = key;
      dayNumber.textContent = day;
      cell.append(dayNumber);
      const descriptions = [dayLabel.format(date)];
      const types = [["image", "I", t("obrazy", "images"), images], ["video", "V", t("filmy", "videos"), videos]];
      for (const [kind, initial, name, total] of types) {
        if (media !== "both" && media !== kind) continue;
        const count = document.createElement("span");
        count.className = `calendar-count ${kind}`;
        if (total) count.classList.add("has-files");
        const value = counts === null ? "—" : number.format(total);
        count.textContent = `${initial} ${value}`;
        descriptions.push(`${name}: ${value}`);
        cell.append(count);
      }
      if (hasFiles) {
        cell.type = "button";
        cell.setAttribute("aria-label", descriptions.join(", "));
        cell.addEventListener("click", () => this.onSelect(key));
      }
      grid.append(cell);
    }
    card.append(heading, grid);
    return card;
  }
}
