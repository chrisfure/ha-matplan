"use strict";
const $ = (s) => document.querySelector(s);
const ICON = { ha_med: "🎒", spesiell_dag: "🌲", stengt: "🚫", foreldre: "👪", frist: "⏰", info: "ℹ️" };
const DAYS = ["søndag", "mandag", "tirsdag", "onsdag", "torsdag", "fredag", "lørdag"];
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
  toast.t = setTimeout(() => (t.hidden = true), 3500);
}

const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

function niceDate(iso) {
  const d = new Date(iso + "T12:00:00");
  return `${DAYS[d.getDay()]} ${d.getDate()}. ${MONTHS[d.getMonth()]}`;
}

function kidNames() { return (S.settings.children || []).map((c) => c.name); }

function kidChecks(selected, prefix) {
  return kidNames().map((n) =>
    `<label><input type="checkbox" data-kid="${esc(n)}" class="${prefix}" ${selected.includes(n) ? "checked" : ""}> ${esc(n)}</label>`
  ).join("");
}

// --- Uka ----------------------------------------------------------------------

function itemCard(it) {
  const time = it.start_time ? ` · kl. ${it.start_time}${it.end_time ? "–" + it.end_time : ""}` : "";
  const range = it.end_day ? ` – ${niceDate(it.end_day)}` : "";
  if (editing === it.id) {
    return `<div class="card item" data-id="${it.id}">
      <label>Hva<input type="text" class="e-title" value="${esc(it.title)}"></label>
      <div class="grid2">
        <label>Dato<input type="date" class="e-day" value="${it.day}"></label>
        <label>Til og med (valgfritt)<input type="date" class="e-end" value="${it.end_day || ""}"></label>
        <label>Fra kl. (tom = hele dagen)<input type="time" class="e-start" value="${it.start_time || ""}"></label>
        <label>Til kl.<input type="time" class="e-endtime" value="${it.end_time || ""}"></label>
      </div>
      <label>Ha med (skill med komma)<input type="text" class="e-bring" value="${esc(it.bring.join(", "))}"></label>
      <div class="kids">${kidChecks(it.children, "e-kid")}</div>
      <div class="actions"><button class="primary" data-act="save">Lagre</button><button class="ghost" data-act="cancel">Avbryt</button></div>
    </div>`;
  }
  const who = it.children.map((c) => `<span class="chip who">${esc(c)}</span>`).join("");
  const bring = it.bring.map((b) => `<span class="chip">${esc(b)}</span>`).join("");
  let actions = "";
  if (it.status === "pending") {
    actions = `<div class="actions"><button class="ok" data-act="approve">✓ Godkjenn</button>
      <button data-act="edit">Endre</button><button class="ghost" data-act="reject">Avvis</button></div>`;
  } else if (it.status === "rejected") {
    actions = `<div class="actions"><button class="ghost" data-act="restore">Angre</button></div>`;
  }
  return `<div class="card item ${it.status}" data-id="${it.id}">
    <div class="title">${ICON[it.kind] || "•"} ${esc(it.title)}</div>
    <div class="meta">${niceDate(it.day)}${range}${time}${it.calendar_note ? " · " + esc(it.calendar_note) : ""}</div>
    <div class="chips">${who}${bring ? bring : ""}</div>
    ${it.details ? `<div class="src">${esc(it.details)}</div>` : ""}
    ${actions}
  </div>`;
}

function byDay(list) {
  let html = "", last = null;
  for (const it of list) {
    if (it.day !== last) { html += `<div class="day-head">${niceDate(it.day)}</div>`; last = it.day; }
    html += itemCard(it);
  }
  return html;
}

function renderReview() {
  const pend = S.items.filter((i) => i.status === "pending");
  const appr = S.items.filter((i) => i.status === "approved");
  const rej = S.items.filter((i) => i.status === "rejected");
  const badge = $("#badge");
  badge.hidden = !pend.length;
  badge.textContent = pend.length;
  const nr = new Date(S.next_review);
  $("#intro").innerHTML = pend.length
    ? `<b>${pend.length} forslag venter.</b> Sjekk at det stemmer, og godkjenn – da havner det i kalenderen til riktig barn og på veggskjermen kvelden før.
       <div class="actions"><button class="primary" id="btn-all">Godkjenn alle (${pend.length})</button></div>`
    : `Ingenting venter på godkjenning. Neste påminnelse: ${DAYS[nr.getDay()]} kl. ${String(nr.getHours()).padStart(2, "0")}:${String(nr.getMinutes()).padStart(2, "0")}.`;
  $("#pending").innerHTML = byDay(pend);
  $("#approved").innerHTML = appr.length ? byDay(appr) : `<div class="empty">Ingenting kommende i kalenderen ennå.</div>`;
  $("#rejected-wrap").hidden = !rej.length;
  $("#rejected").innerHTML = byDay(rej);
  $("#add-kids").innerHTML = kidChecks(kidNames(), "add-kid");
  const all = $("#btn-all");
  if (all) all.onclick = approveAll;
}

async function approveAll() {
  if (!confirm("Legge alle forslagene i kalenderen?")) return;
  try {
    const r = await api("approve_all", {});
    toast(r.errors.length ? `${r.approved} godkjent. Feil: ${r.errors.join("; ")}` : `${r.approved} lagt i kalenderen`);
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
      await api(`items/${id}`, {
        title: card.querySelector(".e-title").value,
        day: card.querySelector(".e-day").value,
        end_day: card.querySelector(".e-end").value,
        start_time: card.querySelector(".e-start").value,
        end_time: card.querySelector(".e-endtime").value,
        bring: card.querySelector(".e-bring").value.split(",").map((s) => s.trim()).filter(Boolean),
        children: [...card.querySelectorAll(".e-kid:checked")].map((c) => c.dataset.kid),
      });
      editing = null;
    } else if (act === "approve") {
      await api(`items/${id}/approve`, {});
      toast("Lagt i kalenderen");
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
      bring: $("#add-bring").value.split(",").map((s) => s.trim()).filter(Boolean),
      children: [...document.querySelectorAll(".add-kid:checked")].map((c) => c.dataset.kid),
    });
    $("#add-title").value = $("#add-bring").value = "";
    toast("Lagt til – husk å godkjenne");
  } catch (e) { toast(e.message); }
  load();
};

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
    ? `<h3>${esc(sc.label)} i barnehagen – ${esc(sc.day_text)}</h3>` + sc.lines.map((l) => `<p>${md(l)}</p>`).join("")
    : `<p>Ingenting godkjent for ${esc(sc.day_text)} – kortet er skjult.</p>`;
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

function renderSettings() {
  const s = S.settings;
  const cals = ENT ? ENT.calendar : [];
  $("#s-kg").value = s.kindergarten || "";
  $("#kids-edit").innerHTML = (s.children || []).length ? "" : `<p class="muted">Ingen barn lagt inn ennå.</p>`;
  $("#kids-edit").innerHTML += (s.children || []).map((c, i) => `<div class="kid-edit" data-i="${i}">
    <div class="grid2">
      <label>Navn<input type="text" class="k-name" value="${esc(c.name)}"></label>
      <label>Skrives som i ukeplanen<input type="text" class="k-label" value="${esc(c.label || "")}"></label>
      <label>Avdeling og grupper (komma)<input type="text" class="k-groups" value="${esc((c.groups || []).join(", "))}"></label>
      <label>Kalender<select class="k-cal">${options(cals, c.calendar, "– velg –")}</select></label>
    </div><button class="ghost" data-del-kid="${i}">Fjern</button></div>`).join("");
  $("#s-notify").innerHTML = options(ENT ? ENT.notify : [], s.notify_service, "– ingen –");
  $("#s-ai").innerHTML = options(ENT ? ENT.ai_task : [], s.ai_entity, "Standard");
  $("#s-review-day").value = String(s.review_weekday);
  $("#s-review-time").value = s.review_time;
  $("#s-evening").value = s.evening_time;
  $("#s-hide").value = s.hide_time;
  $("#s-evening-echo").textContent = s.evening_time;
  $("#s-evening-notify").checked = !!s.evening_notify;
  $("#s-urgent").checked = !!s.urgent_notify;
  $("#s-ignore").value = (s.ignore_subjects || []).join("\n");
  const m = S.mail;
  $("#mail-status").textContent = !m.enabled
    ? "E-posthenting er ikke slått på. Fyll inn e-post og app-passord under Innstillinger → Apper → Huskelappen → Konfigurasjon."
    : m.error ? `Feil: ${m.error}` : `Sist sjekket ${m.last_run || "–"}: ${m.last_result || "venter"}`;
  $("#mails").innerHTML = S.mails.map((x) => `<li><b>${esc(x.subject)}</b> (${x.received}) – ${esc(x.result)}</li>`).join("");
}

function readKids() {
  return [...document.querySelectorAll(".kid-edit")].map((el) => ({
    name: el.querySelector(".k-name").value.trim(),
    label: el.querySelector(".k-label").value.trim(),
    groups: el.querySelector(".k-groups").value.split(",").map((s) => s.trim()).filter(Boolean),
    calendar: el.querySelector(".k-cal").value,
  }));
}

$("#btn-add-kid").onclick = () => {
  S.settings.children = [...readKids(), { name: "", label: "", groups: [], calendar: "" }];
  renderSettings();
};

$("#kids-edit").addEventListener("click", (ev) => {
  const b = ev.target.closest("[data-del-kid]");
  if (!b) return;
  const kids = readKids();
  kids.splice(Number(b.dataset.delKid), 1);
  S.settings.children = kids;
  renderSettings();
});

$("#btn-save").onclick = async () => {
  const children = readKids().filter((c) => c.name);
  try {
    await api("settings", {
      kindergarten: $("#s-kg").value.trim(),
      children,
      notify_service: $("#s-notify").value,
      ai_entity: $("#s-ai").value,
      review_weekday: Number($("#s-review-day").value),
      review_time: $("#s-review-time").value || "18:00",
      evening_time: $("#s-evening").value || "19:00",
      hide_time: $("#s-hide").value || "09:00",
      evening_notify: $("#s-evening-notify").checked,
      urgent_notify: $("#s-urgent").checked,
      ignore_subjects: $("#s-ignore").value.split("\n").map((s) => s.trim()).filter(Boolean),
    });
    toast("Lagret");
  } catch (e) { toast(e.message); }
  load();
};

$("#btn-test").onclick = async () => {
  try { await api("settings", { notify_service: $("#s-notify").value }); await api("test_notify", {}); toast("Testvarsel sendt"); }
  catch (e) { toast(e.message); }
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

$("#btn-poll").onclick = async () => {
  try { await waitJob(await api("poll", {}), "Henter e-post …"); } catch (e) { toast(e.message); }
};

$("#file").onchange = async (ev) => {
  const f = ev.target.files[0];
  if (!f) return;
  const b64 = await new Promise((res) => {
    const r = new FileReader();
    r.onload = () => res(String(r.result).split(",")[1]);
    r.readAsDataURL(f);
  });
  ev.target.value = "";
  try { await waitJob(await api("upload", { filename: f.name, data: b64 }), "Leser ukeplanen … (kan ta et halvt minutt)"); }
  catch (e) { toast(e.message); }
};

// --- Oppstart -------------------------------------------------------------------

document.querySelectorAll(".tabs button").forEach((b) => (b.onclick = () => {
  document.querySelectorAll(".tabs button").forEach((x) => x.classList.toggle("on", x === b));
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.id === "tab-" + b.dataset.tab));
  if (b.dataset.tab === "settings" && !ENT) loadEntities();
}));

async function loadEntities() {
  try { ENT = await api("entities"); } catch (e) { toast(e.message); ENT = { calendar: [], notify: [], ai_task: [] }; }
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
