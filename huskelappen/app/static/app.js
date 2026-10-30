"use strict";
const $ = (s) => document.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const ICON = { ha_med: "🎒", spesiell_dag: "🌲", stengt: "🚫", foreldre: "👪", frist: "⏰", info: "ℹ️", fast: "🔁" };
const DAYS = ["søndag", "mandag", "tirsdag", "onsdag", "torsdag", "fredag", "lørdag"];
const WD = ["Man", "Tir", "Ons", "Tor", "Fre", "Lør", "Søn"];            // 0 = mandag (som i appen)
const WD_LONG = ["mandager", "tirsdager", "onsdager", "torsdager", "fredager", "lørdager", "søndager"];
const MONTHS = ["jan", "feb", "mar", "apr", "mai", "jun", "jul", "aug", "sep", "okt", "nov", "des"];
let S = null;      // siste /api/state
let ENT = null;    // entiteter fra HA
let editing = null;

async function api(path, body) {
  const init = body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
  };
  const r = await fetch("api/" + path, init);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || `Feil ${r.status}`);
  return data;
}

function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(toast.t);
  toast.t = setTimeout(() => (t.hidden = true), 4000);
}

const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const csv = (s) => s.split(",").map((x) => x.trim()).filter(Boolean);

function niceDate(iso) {
  const d = new Date(iso + "T12:00:00");
  return `${DAYS[d.getDay()]} ${d.getDate()}. ${MONTHS[d.getMonth()]}`;
}

function weekdaysText(wds) {
  const w = [...new Set(wds)].sort();
  if (w.join() === "0,1,2,3,4") return "alle hverdager";
  if (w.length === 7) return "hver dag";
  const n = w.map((x) => WD_LONG[x]);
  return n.length > 1 ? n.slice(0, -1).join(", ") + " og " + n.at(-1) : n[0] || "";
}

function kidNames() { return (S.settings.children || []).map((c) => c.name).filter(Boolean); }

function kidChecks(selected, cls) {
  return kidNames().map((n) =>
    `<label><input type="checkbox" data-kid="${esc(n)}" class="${cls}" ${selected.includes(n) ? "checked" : ""}> ${esc(n)}</label>`
  ).join("");
}

function dayChecks(selected, cls) {
  return WD.map((d, i) =>
    `<label><input type="checkbox" data-wd="${i}" class="${cls}" ${selected.includes(i) ? "checked" : ""}> ${d}</label>`
  ).join("");
}

const checkedKids = (el, cls) => $$(`.${cls}:checked`, el).map((c) => c.dataset.kid);
const checkedDays = (el, cls) => $$(`.${cls}:checked`, el).map((c) => Number(c.dataset.wd));

// --- Uka ----------------------------------------------------------------------

function itemCard(it) {
  const fast = it.kind === "fast";
  const time = it.start_time ? ` · kl. ${it.start_time}${it.end_time ? "–" + it.end_time : ""}` : "";
  const range = it.end_day ? ` – ${niceDate(it.end_day)}` : "";
  if (editing === it.id) {
    return `<div class="card item" data-id="${it.id}">
      <label>Hva<input type="text" class="e-title" value="${esc(it.title)}"></label>
      ${fast ? `<div class="kids">${dayChecks(it.weekdays, "e-wd")}</div>` : `
      <div class="grid2">
        <label>Dato<input type="date" class="e-day" value="${it.day}"></label>
        <label>Til og med (valgfritt)<input type="date" class="e-end" value="${it.end_day || ""}"></label>
        <label>Fra kl. (tom = hele dagen)<input type="time" class="e-start" value="${it.start_time || ""}"></label>
        <label>Til kl.<input type="time" class="e-endtime" value="${it.end_time || ""}"></label>
      </div>`}
      <label>Ha med (skill med komma)<input type="text" class="e-bring" value="${esc(it.bring.join(", "))}"></label>
      <div class="kids">${kidChecks(it.children, "e-kid")}</div>
      <div class="actions"><button class="primary" data-act="save">Lagre</button><button class="ghost" data-act="cancel">Avbryt</button></div>
    </div>`;
  }
  const who = it.children.map((c) => `<span class="chip who">${esc(c)}</span>`).join("");
  const bring = it.bring.map((b) => `<span class="chip">${esc(b)}</span>`).join("");
  let actions = "";
  if (it.status === "pending") {
    actions = `<div class="actions"><button class="ok" data-act="approve">✓ ${fast ? "Godkjenn som fast" : "Godkjenn"}</button>
      <button data-act="edit">Endre</button><button class="ghost" data-act="reject">Avvis</button></div>`;
  } else if (it.status === "rejected") {
    actions = `<div class="actions"><button class="ghost" data-act="restore">Angre</button></div>`;
  }
  const when = fast ? `Hver uke: ${weekdaysText(it.weekdays)}` : `${niceDate(it.day)}${range}${time}`;
  const meta = [when, it.source, it.calendar_note].filter(Boolean).map(esc).join(" · ");
  return `<div class="card item ${it.status}" data-id="${it.id}">
    <div class="title">${ICON[it.kind] || "•"} ${esc(it.title)}</div>
    <div class="meta">${meta}</div>
    <div class="chips">${who}${bring}</div>
    ${it.details ? `<div class="src">${esc(it.details)}</div>` : ""}
    ${it.auto_note ? `<div class="auto">🤖 ${esc(it.auto_note)}</div>` : ""}
    ${actions}
  </div>`;
}

function byDay(list) {
  let html = "", last = null;
  for (const it of list) {
    const head = it.kind === "fast" ? "Faste ukedager" : niceDate(it.day);
    if (head !== last) { html += `<div class="day-head">${head}</div>`; last = head; }
    html += itemCard(it);
  }
  return html;
}

function sorted(list) {
  return [...list].sort((a, b) => (a.kind === "fast") - (b.kind === "fast") || a.day.localeCompare(b.day));
}

function renderReview() {
  const pend = sorted(S.items.filter((i) => i.status === "pending"));
  const appr = sorted(S.items.filter((i) => i.status === "approved"));
  const rej = sorted(S.items.filter((i) => i.status === "rejected"));
  $("#badge").hidden = !pend.length;
  $("#badge").textContent = pend.length;
  const nr = new Date(S.next_review);
  const hhmm = `${String(nr.getHours()).padStart(2, "0")}:${String(nr.getMinutes()).padStart(2, "0")}`;
  $("#intro").innerHTML = pend.length
    ? `<b>${pend.length} forslag venter.</b> Sjekk at det stemmer, og godkjenn – da havner det i kalenderen til riktig barn og på veggskjermen kvelden før.
       <div class="actions"><button class="primary" id="btn-all">Godkjenn alle (${pend.length})</button></div>`
    : `Ingenting venter på godkjenning. Neste gjennomgang: ${DAYS[nr.getDay()]} kl. ${hhmm}.`;
  $("#pending").innerHTML = byDay(pend);
  $("#approved").innerHTML = appr.length ? byDay(appr) : `<div class="empty">Ingenting kommende ennå.</div>`;
  $("#rejected-wrap").hidden = !rej.length;
  $("#rejected").innerHTML = byDay(rej);
  $("#add-kids").innerHTML = kidChecks(kidNames(), "add-kid");
  if ($("#btn-all")) $("#btn-all").onclick = approveAll;
}

async function approveAll() {
  if (!confirm("Godkjenne alle forslagene?")) return;
  try {
    const r = await api("approve_all", {});
    toast(r.errors.length ? `${r.approved} godkjent. Feil: ${r.errors.join("; ")}` : `${r.approved} godkjent`);
  } catch (e) { toast(e.message); }
  load();
}

$("#tab-review").addEventListener("click", async (ev) => {
  const btn = ev.target.closest("button[data-act]");
  if (!btn) return;
  const card = btn.closest("[data-id]");
  const id = Number(card.dataset.id);
  const act = btn.dataset.act;
  try {
    if (act === "edit") { editing = id; return renderReview(); }
    if (act === "cancel") { editing = null; return renderReview(); }
    btn.disabled = true;
    if (act === "save") {
      const body = {
        title: card.querySelector(".e-title").value,
        bring: csv(card.querySelector(".e-bring").value),
        children: checkedKids(card, "e-kid"),
      };
      if (card.querySelector(".e-wd")) {
        body.weekdays = checkedDays(card, "e-wd");
      } else {
        Object.assign(body, {
          day: card.querySelector(".e-day").value,
          end_day: card.querySelector(".e-end").value,
          start_time: card.querySelector(".e-start").value,
          end_time: card.querySelector(".e-endtime").value,
        });
      }
      await api(`items/${id}`, body);
      editing = null;
    } else if (act === "approve") {
      const r = await api(`items/${id}/approve`, {});
      toast(r.calendar_note || "Godkjent");
    } else {
      await api(`items/${id}/${act}`, {});
    }
  } catch (e) { toast(e.message); }
  load();
});

$("#btn-add").onclick = async () => {
  try {
    await api("items", {
      day: $("#add-day").value,
      title: $("#add-title").value,
      bring: csv($("#add-bring").value),
      children: checkedKids(document, "add-kid"),
    });
    $("#add-title").value = $("#add-bring").value = "";
    toast("Lagt til – husk å godkjenne");
  } catch (e) { toast(e.message); }
  load();
};

async function waitJob(job, label) {
  toast(label);
  for (;;) {
    await new Promise((r) => setTimeout(r, 2000));
    const j = await api(`jobs/${job.id}`);
    if (j.status !== "running") { toast(j.error ? `Feil: ${j.error}` : j.result); break; }
  }
  load();
}

function readFile(f) {
  return new Promise((res, rej) => {
    const r = new FileReader();
    r.onload = () => res(String(r.result).split(",")[1]);
    r.onerror = () => rej(new Error("Kunne ikke lese filen"));
    r.readAsDataURL(f);
  });
}

// Bilder skaleres ned (og HEIC blir JPEG der nettleseren kan lese det) før opplasting.
async function shrinkImage(f, max = 2000) {
  try {
    const bmp = await createImageBitmap(f);
    const k = Math.min(1, max / Math.max(bmp.width, bmp.height));
    const c = document.createElement("canvas");
    c.width = Math.round(bmp.width * k);
    c.height = Math.round(bmp.height * k);
    c.getContext("2d").drawImage(bmp, 0, 0, c.width, c.height);
    const blob = await new Promise((r) => c.toBlob(r, "image/jpeg", 0.85));
    return new File([blob], "lapp.jpg", { type: "image/jpeg" });
  } catch { return f; }
}

async function upload(ev) {
  let f = ev.target.files[0];
  ev.target.value = "";
  if (!f) return;
  const isPdf = f.type === "application/pdf" || f.name.toLowerCase().endsWith(".pdf");
  if (!isPdf) f = await shrinkImage(f);
  try {
    const job = await api("upload", { filename: f.name, data: await readFile(f) });
    await waitJob(job, isPdf ? "Leser skrivet … (kan ta et halvt minutt)" : "Leser lappen … (kan ta et halvt minutt)");
  } catch (e) { toast(e.message); }
}
$("#photo").onchange = upload;
$("#file").onchange = upload;

// --- Skjermen -------------------------------------------------------------------

function md(line) {
  return esc(line).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>");
}

function renderScreen() {
  const sc = S.screen;
  $("#screen-when").textContent = sc.showing
    ? "Vises nå på skjermen."
    : `Vises ikke akkurat nå. Forhåndsvisning for ${sc.day_text} (vises fra kl. ${S.settings.evening_time} kvelden før til kl. ${S.settings.hide_time}).`;
  $("#screen-preview").innerHTML = sc.lines.length
    ? `<h3>${esc(sc.label)} – ${esc(sc.day_text)}</h3>` + sc.lines.map((l) => `<p>${md(l)}</p>`).join("")
    : `<p>Ingenting å vise for ${esc(sc.day_text)} – kortet er skjult.</p>`;
  $("#card-yaml").textContent = S.dashboard_card;
}

$("#btn-copy").onclick = async () => {
  try { await navigator.clipboard.writeText(S.dashboard_card); toast("Kopiert"); }
  catch { toast("Merk teksten og kopier manuelt"); }
};

// --- Innstillinger ----------------------------------------------------------------

function options(list, value, emptyLabel) {
  const opts = [`<option value="">${emptyLabel}</option>`];
  for (const o of list) {
    const id = o.entity_id || o;
    opts.push(`<option value="${esc(id)}" ${id === value ? "selected" : ""}>${esc(o.name || o)}</option>`);
  }
  if (value && !list.some((o) => (o.entity_id || o) === value)) opts.push(`<option value="${esc(value)}" selected>${esc(value)}</option>`);
  return opts.join("");
}

function renderKids(kids) {
  const cals = ENT ? ENT.calendar : [];
  $("#kids-edit").innerHTML = kids.length ? kids.map((c, i) => `<div class="kid-edit">
    <div class="grid2">
      <label>Navn<input type="text" class="k-name" value="${esc(c.name)}"></label>
      <label>Skrives som<input type="text" class="k-label" value="${esc(c.label || "")}"></label>
      <label>Avdeling, klasse og grupper (komma)<input type="text" class="k-groups" value="${esc((c.groups || []).join(", "))}"></label>
      <label>Kalender<select class="k-cal">${options(cals, c.calendar, "– velg –")}</select></label>
    </div><button class="ghost" data-del-kid="${i}">Fjern</button></div>`).join("")
    : `<p class="muted">Ingen barn lagt inn ennå.</p>`;
}

function readKids() {
  return $$(".kid-edit").map((el) => ({
    name: el.querySelector(".k-name").value.trim(),
    label: el.querySelector(".k-label").value.trim(),
    groups: csv(el.querySelector(".k-groups").value),
    calendar: el.querySelector(".k-cal").value,
  }));
}

function renderSources(srcs) {
  $("#sources-edit").innerHTML = srcs.length ? srcs.map((s, i) => `<div class="src-edit">
    <div class="grid2">
      <label>Navn<input type="text" class="s-name" value="${esc(s.name || "")}" placeholder="F.eks. Barnehagen"></label>
      <label>Avsender (domene eller adresse)<input type="text" class="s-sender" value="${esc(s.sender || "")}" placeholder="barnehagen.no"></label>
    </div>
    <div class="kids">${kidChecks(s.children || [], "s-kid")}</div>
    <button class="ghost" data-del-src="${i}">Fjern</button></div>`).join("")
    : `<p class="muted">${S.sources.length
      ? `Bruker avsender fra app-konfigurasjonen (${esc(S.sources[0].sender)}) for alle barna.`
      : "Ingen kilder ennå – ingen e-post leses."}</p>`;
}

function readSources() {
  return $$(".src-edit").map((el) => ({
    name: el.querySelector(".s-name").value.trim(),
    sender: el.querySelector(".s-sender").value.trim(),
    children: checkedKids(el, "s-kid"),
  }));
}

function renderFixed() {
  const names = kidNames();
  $("#fixed-list").innerHTML = S.fixed.length ? S.fixed.map((f) => `<div class="fx ${f.active ? "" : "off"}">
      <div class="grow"><b>🔁 ${esc(f.title)}</b><div class="muted" style="margin:0">${esc(weekdaysText(f.weekdays))}
        · ${esc(f.children.length >= names.length && names.length > 1 ? "alle" : f.children.join(", "))}
        ${f.bring.length ? " · " + esc(f.bring.join(", ")) : ""}</div></div>
      <button class="ghost" data-fx-toggle="${f.id}" data-on="${f.active ? 0 : 1}">${f.active ? "Pause" : "Slå på"}</button>
      <button class="ghost" data-fx-del="${f.id}">Slett</button></div>`).join("")
    : `<p class="muted">Ingen faste påminnelser ennå.</p>`;
  $("#fx-days").innerHTML = dayChecks([], "fx-wd");
  $("#fx-kids").innerHTML = kidChecks(names, "fx-kid");
}

function renderSettings() {
  const s = S.settings;
  $("#s-kg").value = s.kindergarten || "";
  renderKids(s.children || []);
  renderSources(s.sources || []);
  renderFixed();
  const notifies = ENT ? ENT.notify : [];
  $("#s-notify").innerHTML = options(notifies, s.notify_service, "– ingen –");
  $("#s-extra").innerHTML = options(notifies, s.notify_extra, "– velg telefon –");
  $("#s-extra-on").checked = !!s.notify_extra_on;
  $("#s-ai").innerHTML = options(ENT ? ENT.ai_task : [], s.ai_entity, "Standard");
  $("#s-weather").innerHTML = options(ENT ? ENT.weather : [], s.weather_entity, "– ikke vis vær –");
  $("#s-todo").innerHTML = options(ENT ? ENT.todo : [], s.todo_entity, "– ikke legg til noe –");
  $("#s-review-day").value = String(s.review_weekday);
  $("#s-review-time").value = s.review_time;
  $("#s-evening").value = s.evening_time;
  $("#s-hide").value = s.hide_time;
  $("#s-evening-echo").textContent = s.evening_time;
  $("#s-evening-notify").checked = !!s.evening_notify;
  $("#s-urgent").checked = !!s.urgent_notify;
  $("#s-weekly").checked = !!s.weekly_summary;
  $("#s-deadline").checked = !!s.deadline_notify;
  $("#s-deadline-days").value = s.deadline_days;
  $("#s-weather-daily").checked = !!s.weather_daily;
  $("#s-lunch-days").value = s.lunch_days_before;
  $("#s-learn-reject").checked = !!s.learn_reject;
  $("#s-learn-approve").checked = !!s.learn_approve;
  $("#s-ignore").value = (s.ignore_subjects || []).join("\n");
  const m = S.mail;
  $("#mail-status").textContent = !m.enabled
    ? "E-posthenting er ikke slått på. Fyll inn e-post og app-passord under Innstillinger → Apper → Huskelappen → Konfigurasjon, og legg inn minst én kilde."
    : m.error ? `Feil: ${m.error}` : `Sist sjekket ${m.last_run || "–"}: ${m.last_result || "venter"}`;
  $("#actions-status").textContent = S.actions.connected
    ? "Knapper i varsler: virker."
    : `Knapper i varsler: ikke tilkoblet${S.actions.error ? ` (${S.actions.error})` : ""}.`;
  $("#mails").innerHTML = S.mails.map((x) => `<li><b>${esc(x.subject)}</b> (${x.received}) – ${esc(x.result)}</li>`).join("");
}

async function weatherPreview() {
  const el = $("#weather-preview");
  if (!S.settings.weather_entity) { el.textContent = ""; return; }
  try {
    const w = await api("weather_preview");
    el.textContent = w.summary ? `I morgen (${w.day}): ${w.summary}. Klær: ${w.clothes}.` : "Fant ikke værvarsel for i morgen.";
  } catch (e) { el.textContent = e.message; }
}

$("#btn-add-kid").onclick = () => {
  S.settings.children = [...readKids(), { name: "", label: "", groups: [], calendar: "" }];
  renderKids(S.settings.children);
};
$("#kids-edit").addEventListener("click", (ev) => {
  const b = ev.target.closest("[data-del-kid]");
  if (!b) return;
  const kids = readKids();
  kids.splice(Number(b.dataset.delKid), 1);
  S.settings.children = kids;
  renderKids(kids);
});
$("#btn-add-source").onclick = () => {
  renderSources([...readSources(), { name: "", sender: "", children: kidNames() }]);
};
$("#sources-edit").addEventListener("click", (ev) => {
  const b = ev.target.closest("[data-del-src]");
  if (!b) return;
  const srcs = readSources();
  srcs.splice(Number(b.dataset.delSrc), 1);
  renderSources(srcs);
});

$("#fixed-list").addEventListener("click", async (ev) => {
  const del = ev.target.closest("[data-fx-del]");
  const tog = ev.target.closest("[data-fx-toggle]");
  try {
    if (del && confirm("Slette den faste påminnelsen?")) await api(`fixed/${del.dataset.fxDel}/delete`, {});
    else if (tog) await api(`fixed/${tog.dataset.fxToggle}/active`, { on: tog.dataset.on === "1" });
    else return;
  } catch (e) { toast(e.message); }
  load();
});

$("#btn-fx-add").onclick = async () => {
  try {
    await api("fixed", {
      title: $("#fx-title").value,
      bring: csv($("#fx-bring").value),
      weekdays: checkedDays(document, "fx-wd"),
      children: checkedKids(document, "fx-kid"),
    });
    $("#fx-title").value = $("#fx-bring").value = "";
    toast("Lagt til");
  } catch (e) { toast(e.message); }
  load();
};

$("#btn-save").onclick = async () => {
  const children = readKids().filter((c) => c.name);
  const sources = readSources().filter((s) => s.sender);
  if ($("#s-extra-on").checked && !$("#s-extra").value) return toast("Velg telefonen som også skal få varsler");
  try {
    await api("settings", {
      kindergarten: $("#s-kg").value.trim(),
      children,
      sources,
      notify_service: $("#s-notify").value,
      notify_extra: $("#s-extra").value,
      notify_extra_on: $("#s-extra-on").checked,
      ai_entity: $("#s-ai").value,
      weather_entity: $("#s-weather").value,
      weather_daily: $("#s-weather-daily").checked,
      todo_entity: $("#s-todo").value,
      lunch_days_before: Math.max(1, Number($("#s-lunch-days").value) || 2),
      review_weekday: Number($("#s-review-day").value),
      review_time: $("#s-review-time").value || "18:00",
      evening_time: $("#s-evening").value || "19:00",
      hide_time: $("#s-hide").value || "09:00",
      evening_notify: $("#s-evening-notify").checked,
      urgent_notify: $("#s-urgent").checked,
      weekly_summary: $("#s-weekly").checked,
      deadline_notify: $("#s-deadline").checked,
      deadline_days: Math.max(2, Number($("#s-deadline-days").value) || 2),
      learn_reject: $("#s-learn-reject").checked,
      learn_approve: $("#s-learn-approve").checked,
      ignore_subjects: $("#s-ignore").value.split("\n").map((s) => s.trim()).filter(Boolean),
    });
    toast("Lagret");
  } catch (e) { toast(e.message); }
  await load();
  weatherPreview();
};

$("#btn-test").onclick = async () => {
  try {
    await api("settings", {
      notify_service: $("#s-notify").value, notify_extra: $("#s-extra").value, notify_extra_on: $("#s-extra-on").checked,
    });
    const r = await api("test_notify", {});
    toast(`Testvarsel sendt til ${r.sent} ${r.sent === 1 ? "telefon" : "telefoner"}`);
  } catch (e) { toast(e.message); }
};

$("#btn-poll").onclick = async () => {
  try { await waitJob(await api("poll", {}), "Henter e-post …"); } catch (e) { toast(e.message); }
};

// --- Oppstart -------------------------------------------------------------------

$$(".tabs button").forEach((b) => (b.onclick = () => {
  $$(".tabs button").forEach((x) => x.classList.toggle("on", x === b));
  $$(".tab").forEach((t) => t.classList.toggle("active", t.id === "tab-" + b.dataset.tab));
  if (b.dataset.tab === "settings") {
    if (!ENT) loadEntities();
    weatherPreview();
  }
}));

async function loadEntities() {
  try { ENT = await api("entities"); } catch (e) { toast(e.message); ENT = { calendar: [], notify: [], ai_task: [], weather: [], todo: [] }; }
  renderSettings();
}

async function load() {
  try {
    S = await api("state");
    renderReview();
    renderScreen();
    renderSettings();
  } catch (e) { toast(e.message); }
}

load();
