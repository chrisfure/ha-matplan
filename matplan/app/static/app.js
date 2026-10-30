"use strict";
// Matplan – frontend. Alle adresser er relative fordi appen kjører bak HA ingress.

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

async function api(path, opts = {}) {
  const init = { method: opts.method || "GET", headers: {} };
  if (opts.body !== undefined) {
    init.method = opts.method || "POST";
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(opts.body);
  }
  const r = await fetch("api/" + path, init);
  let data = null;
  try { data = await r.json(); } catch (_) {}
  if (!r.ok) throw new Error((data && data.error) || `Feil ${r.status}`);
  return data;
}

function toast(msg, ms = 3000) {
  const t = $("#toast");
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => (t.hidden = true), ms);
}

async function runJob(startPromise, box) {
  box.hidden = false;
  box.className = "job";
  box.innerHTML = `<span class="spinner"></span>Starter …`;
  try {
    let job = await startPromise;
    while (job.status === "running") {
      box.innerHTML = `<span class="spinner"></span>${esc(job.progress)} …`;
      await new Promise((r) => setTimeout(r, 1500));
      job = await api("jobs/" + job.id);
    }
    if (job.status === "error") throw new Error(job.error);
    box.hidden = true;
    return job.result;
  } catch (e) {
    box.className = "job err";
    box.textContent = e.message;
    throw e;
  }
}

function fmtDate(iso) {
  if (!iso) return "";
  const d = new Date(iso + "T12:00:00");
  return d.toLocaleDateString("nb-NO", { weekday: "short", day: "numeric", month: "short" });
}
function ago(days) {
  if (days === 0) return "i dag";
  if (days === 1) return "i går";
  return `for ${days} dager siden`;
}

const state = { me: null, settings: {}, days: 3 };

// ---------------------------------------------------------------- faner
function showTab(name) {
  // på mobil ligger Økonomi under Kvitteringer-knappen
  $$(".tabs button").forEach((b) => b.classList.toggle("on", b.dataset.tab === name || (!isWide() && ((name === "okonomi" && b.dataset.tab === "kvitt") || (name === "beredskap" && b.dataset.tab === "lager")))));
  $$(".tab").forEach((t) => t.classList.toggle("active", t.id === "tab-" + name));
  try { localStorage.setItem("matplan-tab", name); } catch (_) {}
  ({ middag: loadMiddag, handle: loadShop, kvitt: loadReceipts, okonomi: loadEconomy, beredskap: loadBered, lager: loadPantry, innst: loadMore })[name]?.();
  window.scrollTo(0, 0);
}
$$(".tabs button").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));
$$(".ecoseg button").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.go)));

// ---------------------------------------------------------------- modal
function modal(html, onMount) {
  const d = $("#modal");
  $("#modal-body").innerHTML = html;
  if (!d.open) d.showModal();
  onMount && onMount($("#modal-body"));
}
function closeModal() { $("#modal").close(); }
$("#modal").addEventListener("click", (e) => { if (e.target.id === "modal") closeModal(); });

// ---------------------------------------------------------------- autofullføring
// Grå tekst viser resten av ordet. Godta med trykk på den grå teksten, Enter, komma (i lister),
// Tab/pil høyre på tastatur, eller trykk på et av forslagene under feltet.
let VOCAB = null;
async function loadVocab(refresh) {
  if (!VOCAB || refresh) {
    try { VOCAB = await api("vocab"); } catch (_) { VOCAB = VOCAB || []; }
  }
  return VOCAB;
}
const lc = (x) => (x || "").toLocaleLowerCase("nb");

function attachAutocomplete(input, opts = {}) {
  loadVocab();
  const wrap = document.createElement("div");
  wrap.className = "ac-wrap";
  input.parentNode.insertBefore(wrap, input);
  wrap.appendChild(input);
  const ghost = document.createElement("div");
  ghost.className = "ac-ghost";
  wrap.appendChild(ghost);
  const chips = document.createElement("div");
  chips.className = "ac-chips";
  (opts.chipsAfter || wrap).after(chips);
  input.setAttribute("autocomplete", "off");
  let completion = null;

  const parts = () => {
    const v = input.value;
    const i = opts.multi ? v.lastIndexOf(",") : -1;
    return { head: i >= 0 ? v.slice(0, i + 1) : "", token: i >= 0 ? v.slice(i + 1) : v };
  };
  const update = () => {
    const { head, token } = parts();
    const t = lc(token.trim());
    completion = null;
    ghost.innerHTML = "";
    chips.innerHTML = "";
    if (t.length < 2 || !VOCAB) return;
    const taken = new Set(input.value.split(",").slice(0, -1).map((x) => lc(x.trim())));
    const starts = VOCAB.filter((v) => lc(v.w).startsWith(t) && !taken.has(lc(v.w)));
    const contains = VOCAB.filter((v) => !lc(v.w).startsWith(t) && lc(v.w).includes(t) && !taken.has(lc(v.w)));
    const best = starts[0];
    if (best && lc(best.w) !== t) {
      completion = best.w;
      if (document.activeElement === input && input.scrollWidth <= input.clientWidth + 1) {
        ghost.innerHTML = `<span class="ac-typed">${esc(head + token)}</span><span class="ac-rest">${esc(best.w.slice(token.trim().length))}</span>`;
      }
    }
    const list = [...starts, ...contains].slice(0, 4);
    if (list.length && !(list.length === 1 && lc(list[0].w) === t)) {
      chips.innerHTML = list.map((v) => `<button type="button" data-w="${esc(v.w)}">${v.own ? "" : ""}${esc(v.w)}<small>${esc(v.c)}</small></button>`).join("");
    }
  };
  const accept = (w, sep = "") => {
    const { head } = parts();
    input.value = (head ? head + " " : "") + w + sep;
    completion = null;
    input.dispatchEvent(new Event("input", { bubbles: true }));
    input.focus();
    input.setSelectionRange(input.value.length, input.value.length);
  };
  const sep = opts.multi ? ", " : "";
  input.addEventListener("input", update);
  input.addEventListener("focus", update);
  input.addEventListener("blur", () => setTimeout(() => { if (document.activeElement !== input) ghost.innerHTML = ""; }, 200));
  input.addEventListener("keydown", (e) => {
    if (!completion) return;
    const atEnd = input.selectionStart === input.value.length;
    if (e.key === "Tab" || (e.key === "ArrowRight" && atEnd)) { e.preventDefault(); accept(completion, sep); }
    else if (e.key === "Enter" && atEnd) accept(completion); // andre Enter-handlinger kjører etterpå
  });
  input.addEventListener("beforeinput", (e) => {
    if (opts.multi && e.data === "," && completion && input.selectionStart === input.value.length) {
      e.preventDefault();
      accept(completion, sep);
    }
  });
  ghost.addEventListener("pointerdown", (e) => {
    if (e.target.closest(".ac-rest") && completion) { e.preventDefault(); accept(completion, sep); }
  });
  chips.addEventListener("pointerdown", (e) => e.preventDefault()); // behold fokus i feltet
  chips.addEventListener("click", (e) => {
    const b = e.target.closest("button[data-w]");
    if (b) accept(b.dataset.w, sep);
  });
  return { update };
}

// ================================================================ MIDDAG
$$("#days-seg button").forEach((b) =>
  b.addEventListener("click", () => {
    $$("#days-seg button").forEach((x) => x.classList.toggle("on", x === b));
    state.days = +b.dataset.days;
    renderGuests();
  })
);

// Gjester: antall voksne/barn ekstra, og hvilke dager
state.guests = { adults: 0, children: 0, days: new Set() };
function renderGuests() {
  const g = state.guests, el = $("#guests");
  const days = [...Array(state.days).keys()].map((i) => { const d = new Date(); d.setDate(d.getDate() + i); return d; });
  const iso = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  [...g.days].forEach((x) => { if (!days.some((d) => iso(d) === x)) g.days.delete(x); });
  const n = g.adults + g.children;
  if (n && !g.days.size && days.length === 1) g.days.add(iso(days[0]));
  el.innerHTML = `<details class="dropdown guests" ${n ? "open" : ""}><summary>👥 Gjester${n ? `: ${n} ${g.days.size ? `(${g.days.size} dag${g.days.size === 1 ? "" : "er"})` : "– velg dag"}` : ""}</summary>
    <div class="dd-body" style="padding:10px 12px">
      <div class="gcount"><span>Voksne</span><div class="stepper"><button data-g="adults" data-d="-1">−</button><span>${g.adults}</span><button data-g="adults" data-d="1">+</button></div></div>
      <div class="gcount"><span>Barn</span><div class="stepper"><button data-g="children" data-d="-1">−</button><span>${g.children}</span><button data-g="children" data-d="1">+</button></div></div>
      <div class="chips flush">${days.map((d) => `<button data-gday="${iso(d)}" class="${g.days.has(iso(d)) ? "sel" : ""}">${d.toLocaleDateString("nb-NO", { weekday: "short", day: "numeric" })}</button>`).join("")}</div>
      <p class="hint" style="margin-bottom:0">Gjestene telles med i porsjonene og det som må kjøpes, og Claude velger retter som passer flere.</p>
    </div></details>`;
}
$("#guests").addEventListener("click", (e) => {
  const st = e.target.closest("[data-g]");
  if (st) { const k = st.dataset.g; state.guests[k] = Math.max(0, Math.min(30, state.guests[k] + +st.dataset.d)); return renderGuests(); }
  const d = e.target.closest("[data-gday]");
  if (d) { const s2 = state.guests.days; s2.has(d.dataset.gday) ? s2.delete(d.dataset.gday) : s2.add(d.dataset.gday); renderGuests(); }
});
renderGuests();

$("#btn-plan").addEventListener("click", async () => {
  const btn = $("#btn-plan");
  btn.disabled = true;
  const g = state.guests;
  const guests = g.adults + g.children ? [...g.days].map((date) => ({ date, adults: g.adults, children: g.children })) : [];
  if (g.adults + g.children && !guests.length) { btn.disabled = false; return toast("Velg hvilke dager dere har gjester"); }
  try {
    const plan = await runJob(api("dinner", { body: { days: state.days, wish: $("#wish").value, guests } }), $("#plan-job"));
    renderPlan(plan);
  } catch (_) {} finally { btn.disabled = false; }
});

async function loadMiddag() {
  try {
    renderPlan(await api("dinner"));
    loadMeals();
  } catch (e) { toast(e.message); }
}

const TID = { kort: ["Dårlig tid", "warn"], normal: ["Normal tid", ""], god: ["God tid", "ok"] };
const TYPE = { fisk: "🐟", kjøtt: "🥩", kylling: "🐔", vegetar: "🥦" };
const typeIcon = (t) => TYPE[(t || "").toLowerCase()] || "";

function planBalance(plan) {
  const counts = { fisk: 0, kjøtt: 0, kylling: 0, vegetar: 0 };
  let n = 0;
  plan.dager.forEach((d) => {
    const f = (d.forslag || []).find((x) => x.planlagt || x.valgt || x.i_kalender);
    if (f && counts[(f.type || "").toLowerCase()] !== undefined) { counts[f.type.toLowerCase()]++; n++; }
  });
  if (!n) return "";
  const days = plan.dager.length;
  const tip = days >= 5 && counts.fisk < 2 ? `<div class="hint">Tips: kostrådene anbefaler fisk minst to ganger i uka.</div>` : "";
  return `<div class="card balance"><div class="name">Valgt og planlagt i perioden</div>
    <div class="meta">${Object.entries(counts).map(([k, v]) => `<span class="chip ${v ? "" : "dim"}">${TYPE[k]} ${k} ${v}</span>`).join("")}</div>${tip}</div>`;
}

function renderPlan(plan) {
  const el = $("#plan");
  if (!plan || !plan.dager || !plan.dager.length) {
    el.innerHTML = `<div class="card empty">Ingen forslag ennå. Trykk «Lag middagsforslag».</div>`;
    return;
  }
  const errs = (plan.kalenderfeil || []).length
    ? `<div class="job err">Noen kalendere kunne ikke leses: ${esc(plan.kalenderfeil.join("; "))}</div>` : "";
  el.innerHTML = errs + planBalance(plan) + plan.dager.map((d) => {
    const [tidTxt, tidCls] = TID[d.tid] || [d.tid || "", ""];
    const ev = (d.avtaler || []).length
      ? `<details class="events"><summary>${d.avtaler.length} avtale${d.avtaler.length > 1 ? "r" : ""} i kalenderen</summary><ul>${d.avtaler.map((a) => `<li>${esc(a)}</li>`).join("")}</ul></details>` : "";
    const written = (d.forslag || []).some((x) => x.i_kalender);
    const chooseBtn = (f, i) => {
      if (f.planlagt) return "";
      if (f.i_kalender) return `<button class="small primary" disabled>📅 I kalenderen</button>`;
      if (written || (d.planlagt && !f.valgt)) return "";
      return `<button class="small ${f.valgt ? "primary" : ""}" data-choose="${esc(d.dato)}|${i}">${f.valgt ? "✓ Valgt" : (state.writeCal ? "📅 Velg" : "Velg")}</button>`;
    };
    const sug = (d.forslag || []).map((f, i) => `
      <div class="sugg click ${f.valgt || f.planlagt || f.i_kalender ? "chosen" : ""}" data-open="${esc(d.dato)}|${i}">
        <div class="t"><span class="tt">${typeIcon(f.type)} ${esc(f.tittel)}${f.egen ? ` <em>egen</em>` : ""}${(f.beredskap || []).length ? ` <span class="bmark" title="Bruker det eldste fra beredskapslageret">🛟</span>` : ""}</span> <span>${f.min ? f.min + " min" : ""} ›</span></div>
        ${f.beskrivelse ? `<p>${esc(f.beskrivelse)}</p>` : ""}
        ${(f.mangler || []).length ? `<p class="miss">Mangler: ${esc(f.mangler.join(", "))}</p>` : ""}
        <div class="acts">
          ${chooseBtn(f, i)}
          ${!f.planlagt && !f.i_kalender && !f.valgt && !written ? `<button class="small" data-replace="${esc(d.dato)}|${i}">🔄 Nytt forslag</button>` : ""}
          ${f.oppskrift ? `<span class="has small-note">📖 Oppskrift</span>` : ""}
        </div>
      </div>`).join("");
    const canCustom = !written && !d.planlagt;
    return `<div class="card day">
      <h3>${esc((d.ukedag || "").replace(/^./, (c) => c.toUpperCase()))} ${esc(fmtDate(d.dato).replace(/^\S+\s/, ""))}</h3>
      <div class="meta">
        ${d.hjemme ? `<span class="chip">👥 ${esc(d.hjemme)} hjemme${d.hvem && d.hvem !== "alle" ? " (" + esc(d.hvem) + ")" : ""}</span>` : ""}
        ${d.planlagt ? `<span class="chip ok">📅 ${esc(d.planlagt)}</span>` : ""}
        ${d.gjester ? `<span class="chip">👥 +${d.gjester} gjest${d.gjester === 1 ? "" : "er"}</span>` : ""}
        ${tidTxt ? `<span class="chip ${tidCls}">⏱ ${esc(tidTxt)}</span>` : ""}
      </div>
      ${d.begrunnelse ? `<div class="why">${esc(d.begrunnelse)}</div>` : ""}
      ${ev}${sug}
      ${canCustom ? `<button class="small ghost custom" data-custom="${esc(d.dato)}">✏️ Egen middag denne dagen</button>` : ""}
    </div>`;
  }).join("") + `<p class="hint">Laget ${esc((plan.generert || "").replace("T", " kl. "))}. Trykk på et forslag for oppskrift og redigering.</p>`;
  el._plan = plan;
}

function planRef(key) {
  const plan = $("#plan")._plan;
  const [dato, idx] = key.split("|");
  const dag = plan.dager.find((d) => d.dato === dato);
  return { plan, dag, dato, index: +idx, f: dag.forslag[+idx] };
}

async function ensureRecipe(r) {
  if (r.f.oppskrift) return r.f.oppskrift;
  const rec = await runJob(api("recipe", { body: { title: r.f.tittel, description: r.f.beskrivelse || "", dato: r.dato, index: r.index } }), $("#plan-job"));
  r.f.oppskrift = rec;
  return rec;
}

async function chooseFlow(r) {
  try {
    if (state.writeCal && !r.f.valgt && !r.f.oppskrift) await ensureRecipe(r);
    const p = await api("dinner/choose", { body: { dato: r.dato, index: r.index } });
    renderPlan(p);
    if (p.melding) toast(p.melding, 4000);
    return p;
  } catch (err) { toast(err.message, 4000); }
}

async function replaceFlow(r) {
  try {
    const p = await runJob(api("dinner/replace", { body: { dato: r.dato, index: r.index } }), $("#plan-job"));
    renderPlan(p);
    toast("Nytt forslag: " + p.dager.find((d) => d.dato === r.dato).forslag[r.index].tittel);
    return p;
  } catch (_) {}
}

$("#plan").addEventListener("click", async (e) => {
  const b = e.target.closest("button");
  if (b) {
    if (b.dataset.choose) return chooseFlow(planRef(b.dataset.choose));
    if (b.dataset.replace) return replaceFlow(planRef(b.dataset.replace));
    if (b.dataset.custom) return customDialog(b.dataset.custom);
    return;
  }
  if (e.target.closest("details")) return;
  const card = e.target.closest("[data-open]");
  if (card) openDish(planRef(card.dataset.open));
});

function customDialog(dato) {
  modal(`
    <h3>Egen middag</h3>
    <label class="field">Hva skal dere spise?<input type="text" id="c-title" placeholder="F.eks. Fiskekaker"></label>
    <label class="field">Beskrivelse (valgfritt)<input type="text" id="c-desc" placeholder="F.eks. med potet og råkost"></label>
    <div class="modal-acts"><button class="ghost" id="m-cancel">Avbryt</button><button class="primary" id="m-save">Legg til</button></div>
  `, (m) => {
    $("#m-cancel", m).onclick = closeModal;
    $("#m-save", m).onclick = async () => {
      const t = $("#c-title", m).value.trim();
      if (!t) return;
      try {
        const p = await api("dinner/custom", { body: { dato, tittel: t, beskrivelse: $("#c-desc", m).value } });
        closeModal();
        renderPlan(p);
        const dag = p.dager.find((d) => d.dato === dato);
        openDish(planRef(`${dato}|${dag.forslag.length - 1}`));
      } catch (err) { toast(err.message); }
    };
    $("#c-title", m).focus();
  });
}

function recipeHtml(rec) {
  if (!rec) return "";
  return `
    <div class="recipe">
      <div class="rhead">Oppskrift${rec.porsjoner ? ` · ${esc(rec.porsjoner)} porsjoner` : ""}${rec.tid ? ` · ca. ${esc(rec.tid)} min` : ""}</div>
      <ul>${(rec.ingredienser || []).map((i) => `<li>${esc(i)}</li>`).join("")}</ul>
      <ol>${(rec.steg || []).map((st) => `<li>${esc(st)}</li>`).join("")}</ol>
      ${rec.tilbehor ? `<p><b>Tilbehør:</b> ${esc(rec.tilbehor)}</p>` : ""}
      ${rec.tips ? `<p class="tip">💡 ${esc(rec.tips)}</p>` : ""}
    </div>`;
}

// Detaljvisning for en rett. r = planreferanse ({dato,index,f,dag}) eller {f:{tittel}} for en tidligere middag.
function openDish(r) {
  const f = r.f;
  const inPlan = r.dato !== undefined;
  const written = inPlan && r.dag.forslag.some((x) => x.i_kalender);
  const canChoose = inPlan && !f.planlagt && !f.i_kalender && !written && !(r.dag.planlagt && !f.valgt);
  modal(`
    <h3>${typeIcon(f.type)} ${esc(f.tittel)}</h3>
    <div class="meta">
      ${f.type ? `<span class="chip">${esc(f.type)}</span>` : ""}
      ${(f.beredskap || []).length ? `<span class="chip bchip">🛟 Roterer beredskapen: ${esc(f.beredskap.map((b) => b.name.split(" (")[0]).join(", "))}</span>` : ""}
      ${f.min ? `<span class="chip">⏱ ${esc(f.min)} min</span>` : ""}
      ${f.i_kalender || f.planlagt ? `<span class="chip ok">📅 I kalenderen</span>` : ""}
    </div>
    ${f.beskrivelse ? `<p>${esc(f.beskrivelse)}</p>` : ""}
    ${(f.bruker || []).length ? `<p class="has">Har: ${esc(f.bruker.join(", "))}</p>` : ""}
    ${(f.mangler || []).length ? `<p class="miss">Mangler: ${esc(f.mangler.join(", "))}</p>` : ""}
    <div id="d-recipe">${f.oppskrift ? recipeHtml(f.oppskrift) : `<button class="ghost wide" id="d-getrec">📖 Hent oppskrift</button>`}</div>
    <div class="job" id="d-job" hidden></div>
    <div class="dish-acts">
      ${canChoose ? `<button class="primary" id="d-choose">${f.valgt ? "✓ Valgt" : state.writeCal ? "📅 Velg og legg i kalenderen" : "Velg"}</button>` : ""}
      ${(f.mangler || []).length ? `<button id="d-buy">🛒 Kjøp det som mangler</button>` : ""}
      <button id="d-edit">✏️ Rediger</button>
      ${canChoose && !f.valgt ? `<button id="d-replace">🔄 Nytt forslag</button>` : ""}
      ${f.oppskrift ? `<button id="d-newrec">↻ Ny oppskrift</button>` : ""}
      ${inPlan ? `<button id="d-made">Vi lagde denne</button>` : ""}
      <button class="ghost" id="d-close">Lukk</button>
    </div>
    ${f.i_kalender ? `<p class="hint">Endringer her oppdaterer ikke avtalen som allerede står i kalenderen.</p>` : ""}
  `, (m) => {
    const getRec = async (force) => {
      try {
        const job = api("recipe", { body: { title: f.tittel, description: f.beskrivelse || "", dato: r.dato, index: r.index, force } });
        f.oppskrift = await runJob(job, $("#d-job", m));
        if (inPlan) renderPlan($("#plan")._plan);
        openDish(r);
      } catch (_) {}
    };
    $("#d-getrec", m) && ($("#d-getrec", m).onclick = () => getRec(false));
    $("#d-newrec", m) && ($("#d-newrec", m).onclick = () => getRec(true));
    $("#d-close", m).onclick = closeModal;
    $("#d-edit", m).onclick = () => editDish(r);
    $("#d-buy", m) && ($("#d-buy", m).onclick = async () => {
      try {
        const res = await api("shopping/add", { body: { items: f.mangler } });
        toast(res.added.length ? `La til ${res.added.length} på handlelista` : "Alt sto allerede på lista");
      } catch (err) { toast(err.message); }
    });
    $("#d-choose", m) && ($("#d-choose", m).onclick = async () => {
      $("#d-choose", m).disabled = true;
      const p = await chooseFlow(r);
      if (p) openDish(planRef(`${r.dato}|${r.index}`)); else closeModal();
    });
    $("#d-replace", m) && ($("#d-replace", m).onclick = async () => {
      const p = await replaceFlow(r);
      if (p) openDish(planRef(`${r.dato}|${r.index}`));
    });
    $("#d-made", m) && ($("#d-made", m).onclick = () => madeDialog(f.tittel, f.bruker || [], r.dato, f.beredskap || []));
  });
}

const lines = (t) => t.split("\n").map((x) => x.trim()).filter(Boolean);

function editDish(r) {
  const f = r.f;
  const inPlan = r.dato !== undefined;
  const rec = f.oppskrift || {};
  modal(`
    <h3>Rediger</h3>
    <label class="field">Tittel<small>Kort, slik den skal stå i kalenderen.</small><input type="text" id="e-t" value="${esc(f.tittel)}" ${inPlan ? "" : "disabled"}></label>
    ${inPlan ? `
    <label class="field">Beskrivelse<textarea id="e-d" rows="2">${esc(f.beskrivelse || "")}</textarea></label>
    <div class="two">
      <label class="field">Type<select id="e-type">${["", "fisk", "kjøtt", "kylling", "vegetar"].map((t) => `<option value="${t}" ${t === (f.type || "") ? "selected" : ""}>${t ? TYPE[t] + " " + t : "–"}</option>`).join("")}</select></label>
      <label class="field">Minutter<input type="number" id="e-min" value="${esc(f.min ?? "")}" min="0"></label>
    </div>
    <label class="field">Mangler<small>Én vare per linje – brukes av «Kjøp det som mangler».</small><textarea id="e-miss" rows="3">${esc((f.mangler || []).join("\n"))}</textarea></label>` : ""}
    <label class="field">Ingredienser<small>Én per linje.</small><textarea id="e-ing" rows="6">${esc((rec.ingredienser || []).join("\n"))}</textarea></label>
    <label class="field">Slik gjør du<small>Ett steg per linje.</small><textarea id="e-steps" rows="6">${esc((rec.steg || []).join("\n"))}</textarea></label>
    <div class="two">
      <label class="field">Porsjoner<input type="number" id="e-por" value="${esc(rec.porsjoner ?? "")}" min="1"></label>
      <label class="field">Tid (min)<input type="number" id="e-tid" value="${esc(rec.tid ?? "")}" min="0"></label>
    </div>
    <label class="field">Tilbehør<input type="text" id="e-tilb" value="${esc(rec.tilbehor || "")}"></label>
    <label class="field">Tips<input type="text" id="e-tips" value="${esc(rec.tips || "")}"></label>
    <div class="modal-acts"><button class="ghost" id="m-cancel">Avbryt</button><button class="primary" id="m-save">Lagre</button></div>
  `, (m) => {
    $("#m-cancel", m).onclick = () => openDish(r);
    $("#m-save", m).onclick = async () => {
      const ing = lines($("#e-ing", m).value), steps = lines($("#e-steps", m).value);
      const newRec = (ing.length || steps.length) ? {
        ...rec, ingredienser: ing, steg: steps,
        porsjoner: +$("#e-por", m).value || rec.porsjoner, tid: +$("#e-tid", m).value || rec.tid,
        tilbehor: $("#e-tilb", m).value.trim(), tips: $("#e-tips", m).value.trim(),
      } : null;
      try {
        if (inPlan) {
          const body = { dato: r.dato, index: r.index, tittel: $("#e-t", m).value, beskrivelse: $("#e-d", m).value,
            type: $("#e-type", m).value, min: $("#e-min", m).value, mangler: lines($("#e-miss", m).value) };
          if (newRec) body.oppskrift = newRec;
          const p = await api("dinner/edit", { body });
          renderPlan(p);
          if (p.melding) toast(p.melding, 4000); else toast("Lagret");
          openDish(planRef(`${r.dato}|${r.index}`));
        } else {
          if (newRec) await api("recipe/save", { body: { title: f.tittel, recipe: newRec } });
          f.oppskrift = newRec;
          toast("Oppskriften er lagret");
          openDish(r);
        }
      } catch (err) { toast(err.message); }
    };
  });
}

function madeDialog(title, used, day, bered = []) {
  modal(`
    <h3>Vi lagde: ${esc(title)}</h3>
    ${bered.length ? `<p class="hint">🛟 <b>Brukt fra beredskapslageret?</b> Det trekkes fra beredskapen, og det samme legges på handlelista – slik roterer lageret.</p>
    <div class="list">${bered.map((b) => `<label class="row"><input type="checkbox" data-bk="${esc(b.key)}" checked><div class="main">${esc(b.name)}</div><input type="number" class="bamt" data-ba="${esc(b.key)}" value="1" min="0" step="0.5" aria-label="Mengde"><small class="bunit">${esc(b.unit || "")}</small></label>`).join("")}</div>` : ""}
    ${used.length ? `<p class="hint">Hva ble brukt opp? Avkryssede varer markeres som tomme i lageret.</p>
    <div class="list">${used.map((u) => `<label class="row"><input type="checkbox" value="${esc(u)}" checked><div class="main">${esc(u)}</div></label>`).join("")}</div>` : ""}
    <p class="hint">Likte familien den?</p>
    <div class="rate"><button data-r="1">👍 Ja</button><button data-r="0" class="sel">Nøytral</button><button data-r="-1">👎 Nei</button></div>
    <div class="modal-acts"><button class="ghost" id="m-cancel">Avbryt</button><button class="primary" id="m-save">Lagre</button></div>
  `, (m) => {
    let rating = 0;
    $$(".rate button", m).forEach((b) => b.addEventListener("click", () => {
      rating = +b.dataset.r;
      $$(".rate button", m).forEach((x) => x.classList.toggle("sel", x === b));
    }));
    $("#m-cancel", m).onclick = closeModal;
    $("#m-save", m).onclick = async () => {
      const usedSel = $$("input:checked:not([data-bk])", m).map((i) => i.value);
      const beredUsed = $$("input[data-bk]:checked", m).map((i) => ({ key: i.dataset.bk, amount: +$(`input[data-ba="${i.dataset.bk}"]`, m).value || 0 }));
      try {
        const r = await api("meals", { body: { title, day, rating, used: usedSel, bered_used: beredUsed } });
        closeModal();
        toast(r.bered_added && r.bered_added.length ? `Lagret – ${r.bered_added.length} lagt på handlelista for å fylle opp beredskapen` : "Lagret", 4000);
        state.bered = null;
        loadMeals();
      } catch (err) { toast(err.message); }
    };
  });
}

$("#btn-addmeal").addEventListener("click", () => {
  modal(`
    <h3>Registrer middag</h3>
    <input type="text" id="m-title" placeholder="Hva spiste dere?">
    <input type="text" id="m-day" value="${new Date().toISOString().slice(0, 10)}">
    <div class="modal-acts"><button class="ghost" id="m-cancel">Avbryt</button><button class="primary" id="m-save">Neste</button></div>
  `, (m) => {
    $("#m-cancel", m).onclick = closeModal;
    $("#m-save", m).onclick = () => {
      const t = $("#m-title", m).value.trim();
      if (t) madeDialog(t, [], $("#m-day", m).value);
    };
    $("#m-title", m).focus();
  });
});

async function loadMeals() {
  try {
    const [meals, dishes] = await Promise.all([api("meals"), api("dishes")]);
    renderMeals(meals, dishes);
  } catch (e) { toast(e.message); }
}

const rateBtns = (title, rating) => `
  <div class="pill2">
    <button data-dish="${esc(title)}" data-r="${rating > 0 ? 0 : 1}" class="${rating > 0 ? "on-har" : ""}" aria-label="Liker">👍</button>
    <button data-dish="${esc(title)}" data-r="${rating < 0 ? 0 : -1}" class="${rating < 0 ? "on-tomt" : ""}" aria-label="Liker ikke">👎</button>
  </div>`;

function renderMeals(meals, dishes) {
  const el = $("#meals");
  const top = (dishes || []).filter((d) => d.n >= 2).slice(0, 15);
  const max = top.length ? top[0].n : 1;
  const topHtml = top.length ? `
    <div class="row"><div class="main"><div class="name">Mest spist siste år</div></div></div>
    ${top.map((d) => `
      <div class="row">
        <div class="main click" data-mealtitle="${esc(d.title)}"><div class="name">${esc(d.title)}</div>
          <div class="bar"><span style="width:${Math.round((d.n / max) * 100)}%"></span></div>
          <div class="info">${d.n} ganger · sist ${esc(fmtDate(d.last))}</div></div>
        ${rateBtns(d.title, d.rating)}
      </div>`).join("")}` : "";
  const recent = (meals || []).slice(0, 10);
  const recentHtml = recent.length ? `
    <div class="row"><div class="main"><div class="name">Siste middager</div></div></div>
    ${recent.map((m) => `
      <div class="row">
        <div class="main click" data-mealtitle="${esc(m.title)}"><div class="name">${esc(m.title)}</div>
        <div class="info">${esc(fmtDate(m.day))}${m.source === "calendar" ? " · 📅 kalender" : m.by_user ? " · " + esc(m.by_user) : ""}</div></div>
        ${rateBtns(m.title, m.rating)}
      </div>`).join("")}` : "";
  el.innerHTML = (topHtml + recentHtml) || `<div class="empty">Ingen middager ennå. Velg middagskalender under Oppsett, så hentes historikken derfra.</div>`;
}
$("#meals").addEventListener("click", async (e) => {
  const b = e.target.closest("button[data-dish]");
  if (!b) {
    const t = e.target.closest("[data-mealtitle]");
    if (t) {
      const title = t.dataset.mealtitle;
      let rec = null;
      try { const r = await api("recipe", { body: { title } }); if (r.status === "done") rec = r.result; } catch (_) {}
      openDish({ f: { tittel: title, oppskrift: rec } });
    }
    return;
  }
  await api("dishes/rate", { body: { title: b.dataset.dish, rating: +b.dataset.r } });
  loadMeals();
});

$("#btn-calsync").addEventListener("click", async () => {
  try { toast(await runJob(api("calendar/sync", { body: {} }), $("#plan-job")), 4000); loadMeals(); } catch (_) {}
});

// ================================================================ HANDLE
state.shopSort = pref("ssort", "aisle");

function dueLabel(s) {
  if (s.bered_fill) return ["🛟 Beredskap", "soft"];
  if (s.override === "tomt") return ["Tomt", "bad"];
  if (s.due_in < 0) return [`Tomt for ${-s.due_in} d siden`, "bad"];
  if (s.due_in === 0) return ["Går tom i dag", "warn"];
  if (s.due_in === 1) return ["Går tom i morgen", "warn"];
  return [`Om ${s.due_in} dager`, ""];
}

async function loadShop() {
  const el = $("#shop");
  el.innerHTML = `<div class="card empty"><span class="spinner"></span>Henter …</div>`;
  let d;
  try { d = await api("shopping"); } catch (e) { el.innerHTML = `<div class="card job err">${esc(e.message)}</div>`; return; }
  state.shop = d;
  // valgt-tilstand: alle forslag som ikke står på lista er forhåndsvalgt
  state.shopSel = new Map();
  d.suggestions.forEach((s) => { if (!s.on_list && !s.bered_fill) state.shopSel.set(s.name, { cat: s.category, price: s.avg_price }); });
  d.from_plan.forEach((m) => state.shopSel.set(m, { cat: "Til middagene", price: null }));
  state.odaPrices = state.odaPrices || {};
  renderShop();
  loadOdaPrices();
}

// Dagens Oda-pris og tilbud for forslagene og handlelista (bufres på serveren i 6 timer)
async function loadOdaPrices() {
  try {
    state.priceStatus = state.priceStatus || await api("prices/status");
  } catch (_) { return; }
  if (!state.priceStatus.oda || !state.shop) return;
  const names = [...state.shop.suggestions.map((s) => s.name), ...state.shop.current, ...state.shopSel.keys()]
    .filter((n) => !(n in state.odaPrices));
  if (!names.length) return;
  const note = $("#shop-pricenote");
  if (note) { note.hidden = false; note.innerHTML = `<span class="spinner"></span>Henter dagens Oda-priser …`; }
  try {
    const job = await api("prices/oda", { body: { items: [...new Set(names)] } });
    let j = job;
    while (j.status === "running") { await new Promise((r) => setTimeout(r, 1500)); j = await api("jobs/" + j.id); }
    if (j.status === "done") Object.assign(state.odaPrices, j.result);
    names.forEach((n) => { if (!(n in state.odaPrices)) state.odaPrices[n] = null; });
  } catch (_) {}
  if ($("#tab-handle").classList.contains("active")) renderShop();
}

function priceLine(name, usual, prices) {
  const op = state.odaPrices && state.odaPrices[name];
  const basis = prices && prices.unit;
  const unitBased = basis && basis !== "pk" && prices.avg90;
  const bits = [];
  if (op && op.price != null) {
    let t = `Oda nå ${kr2(op.price)}`;
    if (op.unit_price && normUnit(op.unit) && (!unitBased || normUnit(op.unit) === basis)) t += ` (${krU(op.unit_price, op.unit)})`;
    if (op.discount) t += ` · 🏷 ${op.discount.text || "tilbud"}${op.discount.regular ? ` (vanlig ${kr2(op.discount.regular)})` : ""}`;
    bits.push(t);
  }
  if (unitBased) bits.push(`dere betaler vanligvis ${krU(prices.avg90, basis)}`);
  else if (usual) bits.push(`dere betaler vanligvis ${kr2(usual)}`);
  return bits.join(" · ");
}

function renderShop() {
  const d = state.shop, el = $("#shop");
  const sel = state.shopSel;
  const priceOf = {};
  [...d.suggestions, ...d.regulars].forEach((s) => (priceOf[s.name] = s.avg_price));
  Object.entries(state.odaPrices || {}).forEach(([n, p]) => { if (p && p.price != null) priceOf[n] = p.price; });
  const noTodo = !d.todo_entity
    ? `<div class="card job err">Velg hvilken handleliste i Home Assistant som skal brukes under <b>Oppsett</b>.</div>` : "";
  const todoErr = d.todo_error ? `<div class="card job err">${esc(d.todo_error)}</div>` : "";

  const sRow = (s) => {
    const [lbl, cls] = dueLabel(s);
    return `<label class="row"><input type="checkbox" data-name="${esc(s.name)}" data-cat="${esc(s.category)}" ${s.on_list ? "disabled" : sel.has(s.name) ? "checked" : ""}>
      <div class="main"><div class="name">${esc(s.name)}${hBadge(s)}${s.on_list ? "" : `<span class="badge ${cls === "bad" ? "" : cls === "warn" ? "" : "soft"}">${esc(lbl)}</span>`}${state.odaPrices?.[s.name]?.discount ? `<span class="badge deal">Tilbud</span>` : ""}</div>
      <div class="info">${s.on_list ? "Står allerede på lista" : esc(s.reason)}</div>
      ${s.bered && !s.bered_fill ? `<div class="info bered">🛟 ${esc(s.bered)}</div>` : ""}
      ${s.health && !s.health.cleared && s.health.tags.length ? `<div class="info halt">💡 Bedre: ${esc(s.health.alt)}</div>` : ""}
      ${priceLine(s.name, s.avg_price, s.prices) ? `<div class="info price">${esc(priceLine(s.name, s.avg_price, s.prices))}</div>` : ""}</div>
      ${s.on_list ? "" : `<button type="button" class="x" data-dismiss="${s.id}" data-dname="${esc(s.name)}" aria-label="Fjern forslag">✕</button>`}</label>`;
  };
  let sugHtml;
  const hid = d.hidden_on_list || [];
  if (!d.suggestions.length) {
    sugHtml = `<div class="card list"><div class="empty">${hid.length ? "Alt som trolig går tom, står allerede på handlelista 👍" : "Ingen forslag nå. Mønstrene blir bedre etter noen uker med kvitteringer."}</div></div>`;
  } else if (state.shopSort === "aisle") {
    const groups = {};
    d.suggestions.forEach((s) => (groups[s.category] ||= []).push(s));
    sugHtml = Object.keys(groups).sort((a, b) => aisleIdx(a) - aisleIdx(b)).map((c) =>
      `<div class="cat">${esc(c)}</div><div class="card list">${groups[c].sort((a, b) => a.due_in - b.due_in).map(sRow).join("")}</div>`).join("");
  } else {
    sugHtml = `<div class="card list">${[...d.suggestions].sort((a, b) => a.due_in - b.due_in || a.name.localeCompare(b.name, "nb")).map(sRow).join("")}</div>`;
  }

  const extra = [...sel.keys()].filter((n) => !d.suggestions.some((s) => s.name === n) && !d.from_plan.includes(n));
  el.innerHTML = `${noTodo}${todoErr}
    <div class="shop-grid"><div class="col">
    <div class="card controls">
      <div class="seg" id="shop-sort">
        <button data-s="aisle" class="${state.shopSort === "aisle" ? "on" : ""}">Butikkrekkefølge</button>
        <button data-s="due" class="${state.shopSort === "due" ? "on" : ""}">Mest akutt</button>
      </div>
    </div>
    <div id="shop-pricenote" class="hint" hidden></div>
    <h2 class="sub">Går trolig tom snart</h2>
    ${hid.length && d.suggestions.length ? `<p class="hint onlist-hint">Skjult fordi de står på handlelista: ${hid.map(esc).join(", ")}. De kommer tilbake hvis de fjernes fra lista.</p>` : ""}
    ${sugHtml}
    ${d.from_plan.length ? `<h2 class="sub">Til planlagte og valgte middager</h2><div class="card list">${d.from_plan.map((m) =>
      `<label class="row"><input type="checkbox" data-name="${esc(m)}" data-cat="Til middagene" ${sel.has(m) ? "checked" : ""}><div class="main"><div class="name">${esc(m)}</div><div class="info">Fra middagsplanen</div></div></label>`).join("")}</div>` : ""}
    </div><div class="col">
    <h2 class="sub">Legg til</h2>
    <div class="card list">
      <div class="addrow"><input type="text" id="shop-free" placeholder="Skriv en vare eller søk i vanlige varer"><button id="shop-free-add">Legg til</button></div>
      ${extra.length ? `<div class="chips">${extra.map((n) => `<button class="sel" data-reg="${esc(n)}">${esc(n)} ✕</button>`).join("")}</div>` : ""}
      <div id="shop-regs"></div>
    </div>
    <div class="stickybar">
      <button class="primary wide" id="shop-send"></button>
      <button class="oda wide" id="shop-oda">🛒 Legg i Oda-handlekurven</button>
      <button class="compare wide" id="shop-compare">⚖️ Sammenlign priser i butikkene</button>
    </div>
    ${d.current.length ? `<h2 class="sub">På handlelista nå (${d.current.length})</h2>
      <div class="card list">${d.current.map((c) => `<div class="row"><div class="main">${esc(c)}</div><button type="button" class="x" data-remove="${esc(c)}" aria-label="Fjern fra handlelista">✕</button></div>`).join("")}
        <div class="row"><div class="main info">✕ fjerner varen helt fra handlelista i Home Assistant (og fra Oda-forslaget).</div><button type="button" class="small ghost danger" id="shop-clear">Tøm lista</button></div>
      </div>` : ""}
    </div></div>
  `;

  const renderRegs = () => {
    const q = $("#shop-free").value.trim().toLowerCase();
    const regs = d.regulars.filter((r) => !d.suggestions.some((s) => s.name === r.name) && (!q || r.name.toLowerCase().includes(q)));
    if (!regs.length) { $("#shop-regs").innerHTML = q ? `<div class="info" style="padding:4px 14px 12px;color:var(--muted);font-size:13px">Ingen treff – trykk «Legg til» for å legge til «${esc(q)}».</div>` : ""; return; }
    const groups = {};
    regs.forEach((r) => (groups[r.category] ||= []).push(r));
    $("#shop-regs").innerHTML = `<div class="info" style="padding:4px 14px 0;color:var(--muted);font-size:13px">Vanlige varer – trykk for å velge</div>` +
      Object.keys(groups).sort((a, b) => aisleIdx(a) - aisleIdx(b)).map((c) => `
        <div class="chipcat">${esc(c)}</div>
        <div class="chips">${groups[c].map((r) => `<button data-reg="${esc(r.name)}" data-cat="${esc(r.category)}" class="${sel.has(r.name) ? "sel" : ""} ${r.health && r.health.upf && !r.health.cleared ? "upf" : ""}" ${r.on_list ? "disabled title='Står på lista'" : ""}>${esc(r.name)}</button>`).join("")}</div>`).join("");
  };
  const update = () => {
    const n = sel.size;
    const total = [...sel.entries()].reduce((t, [name, v]) => t + (v.price ?? priceOf[name] ?? 0), 0);
    const btn = $("#shop-send");
    btn.textContent = n ? `Legg ${n} vare${n > 1 ? "r" : ""} på handlelista${total ? ` · ca. ${kr(total)}` : ""}` : "Velg varer";
    btn.disabled = !n || !d.todo_entity;
  };
  renderRegs();
  update();

  $("#shop-sort").onclick = (e) => {
    const b = e.target.closest("button[data-s]"); if (!b) return;
    state.shopSort = b.dataset.s; setPref("ssort", b.dataset.s); renderShop();
  };
  el.onchange = (e) => {
    const cb = e.target.closest("input[type=checkbox][data-name]"); if (!cb) return;
    if (cb.checked) sel.set(cb.dataset.name, { cat: cb.dataset.cat, price: priceOf[cb.dataset.name] });
    else sel.delete(cb.dataset.name);
    update();
  };
  el.onclick = async (e) => {
    const rm = e.target.closest("button[data-remove]");
    if (rm) {
      e.preventDefault();
      try {
        await api("shopping/remove", { body: { items: [rm.dataset.remove] } });
        toast(`Fjernet «${rm.dataset.remove}» fra handlelista`);
        loadShop();
      } catch (err) { toast(err.message); }
      return;
    }
    if (e.target.closest("#shop-clear")) {
      const c = e.target.closest("#shop-clear");
      if (!c.dataset.sure) { c.dataset.sure = 1; c.textContent = "Trykk igjen for å tømme"; return; }
      try {
        const r = await api("shopping/remove", { body: { all: true } });
        toast(`Fjernet ${r.removed.length} varer fra handlelista`);
        loadShop();
      } catch (err) { toast(err.message); }
      return;
    }
    const dm = e.target.closest("button[data-dismiss]");
    if (dm) {
      e.preventDefault();
      return dismissDialog(+dm.dataset.dismiss, dm.dataset.dname);
    }
    const b = e.target.closest("button[data-reg]"); if (!b) return;
    const name = b.dataset.reg;
    if (sel.has(name)) sel.delete(name); else sel.set(name, { cat: b.dataset.cat || "Annet", price: priceOf[name] });
    if (b.closest("#shop-regs")) { b.classList.toggle("sel"); update(); } else renderShop();
  };
  const addFree = async () => {
    const names = $("#shop-free").value.split(",").map((x) => x.trim()).filter(Boolean);
    if (!names.length) return;
    let cats = [];
    try { cats = await api("categorize?" + new URLSearchParams({ names: names.join(",") })); } catch (_) {}
    names.forEach((v, i) => {
      const c = cats[i] || {};
      const name = c.existing || v.replace(/^./, (x) => x.toUpperCase());
      const match = d.regulars.find((r) => r.name === name);
      sel.set(name, { cat: c.category || "Annet", price: match ? match.avg_price : null });
    });
    renderShop();
  };
  attachAutocomplete($("#shop-free"), { multi: true, chipsAfter: $("#shop-free").closest(".addrow") });
  $("#shop-free-add").onclick = addFree;
  $("#shop-free").addEventListener("keydown", (e) => { if (e.key === "Enter") addFree(); });
  $("#shop-free").addEventListener("input", renderRegs);
  $("#shop-compare").onclick = () => {
    const names = [...new Set([...sel.keys(), ...d.current])];
    compareDialog(names);
  };
  $("#shop-oda").onclick = () => {
    const picked = [...sel.keys()];
    const fromList = d.current.filter((c) => !picked.some((p) => p.toLowerCase() === c.toLowerCase()));
    odaDialog([...picked.map((n) => ({ name: n })), ...fromList.map((n) => ({ name: n, from_list: n }))]);
  };
  $("#shop-send").onclick = async () => {
    // Legg inn i butikkrekkefølge, så lista i Home Assistant blir sortert
    const items = [...sel.entries()].sort((a, b) => aisleIdx(a[1].cat) - aisleIdx(b[1].cat)).map(([n]) => n);
    try {
      const r = await api("shopping/add", { body: { items } });
      toast(`La til ${r.added.length} vare${r.added.length === 1 ? "" : "r"}`);
      loadShop();
    } catch (e) { toast(e.message); }
  };
}

function dismissDialog(pid, name) {
  modal(`
    <h3>Fjern «${esc(name)}»</h3>
    <p class="hint">Hva vil du gjøre med forslaget?</p>
    <div class="dish-acts dz">
      <button id="dz-snooze"><span>⏸ Ikke nå</span><small>Skjules til neste gang den går tom</small></button>
      <button id="dz-never"><span>🚫 Aldri foreslå</span><small>Kan angres under Lager</small></button>
      <button class="ghost" id="dz-cancel">Avbryt</button>
    </div>`, (m) => {
    const go = async (mode) => {
      try {
        await api("shopping/dismiss", { body: { product_id: pid, mode } });
        closeModal();
        toast(mode === "never" ? `«${name}» foreslås ikke mer` : `«${name}» er skjult for nå`);
        loadShop();
      } catch (err) { toast(err.message); }
    };
    $("#dz-snooze", m).onclick = () => go("snooze");
    $("#dz-never", m).onclick = () => go("never");
    $("#dz-cancel", m).onclick = closeModal;
  });
}

// ---------------------------------------------------------------- Prissammenligning (Kassalapp)
async function compareDialog(names) {
  if (!names.length) return toast("Velg varer eller legg noe på handlelista først");
  try { state.priceStatus = await api("prices/status"); } catch (e) { return toast(e.message); }
  if (!state.priceStatus.kassal) {
    return modal(`<h3>Koble til Kassalapp</h3>
      <ol><li>Lag en gratis bruker på <b>kassal.app/api</b> og lag en API-nøkkel.</li>
      <li>Lim den inn i <b>Innstillinger → Apper → Matplan → Konfigurasjon → Kassalapp – API-nøkkel</b> i Home Assistant (trykk «Vis ubrukte valgfrie»), og start appen på nytt.</li></ol>
      <div class="modal-acts"><button class="primary" id="m-close">OK</button></div>`, (m) => { $("#m-close", m).onclick = closeModal; });
  }
  modal(`<h3>⚖️ Prissammenligning</h3><div class="job" id="c-job"></div>`, () => {});
  let res;
  try { res = await runJob(api("compare", { body: { items: names } }), $("#c-job")); } catch (_) { return; }
  renderCompare(res, names);
}

function renderCompare(res, names) {
  // Skjul kjeder som bare har noen få av varene – totalen deres er ikke sammenlignbar
  const minCount = Math.max(2, Math.ceil(res.found * 0.3));
  const relevant = res.stores.filter((t) => t.count >= minCount);
  const shown = (state.cmpAll || !relevant.length ? res.stores : relevant).slice(0, isWide() ? 10 : 6);
  const stores = shown;
  const best = [...shown].sort((a, b) => a.missing - b.missing || a.total - b.total)[0];
  const hiddenN = res.stores.length - shown.length;
  modal(`
    <h3>⚖️ Prissammenligning</h3>
    <p class="hint">Dagens priser fra Kassalapp for ${res.found} av ${res.items.length} varer. 🏷 = mer enn 10 % under vanlig pris siste 30 dager (trolig tilbud).
      Kjeder uten nettbutikk-priser i Kassalapp (som Kiwi og Rema 1000) kan ikke vises.
      ${hiddenN > 0 || state.cmpAll ? `<button class="small linkish" id="c-all">${state.cmpAll ? "Vis bare kjeder med mange av varene" : `Vis alle kjeder (+${hiddenN})`}</button>` : ""}</p>
    <div class="store-totals">${stores.map((t) => `
      <div class="st ${best && t.code === best.code ? "best" : ""}">
        <div class="sn">${esc(t.name)}</div>
        <div class="sp">${kr(t.total)}</div>
        <div class="sm">${t.count} varer${t.missing ? ` · mangler ${t.missing}` : ""}${t.deals ? ` · 🏷 ${t.deals}` : ""}</div>
        ${best && t.code === best.code ? `<div class="sb">Billigst</div>` : ""}
      </div>`).join("")}</div>
    <div class="tablewrap"><table class="cmp">
      <thead><tr><th>Vare</th>${stores.map((t) => `<th>${esc(t.name)}</th>`).join("")}<th>Dere betaler vanligvis</th></tr></thead>
      <tbody>${res.items.map((it, i) => `<tr>
        <td class="item"><b>${esc(it.name)}</b>${it.kname ? `<small>${esc(it.kname)}</small>` : `<small class="miss">Ikke funnet hos Kassalapp</small>`}${it.kname && !Object.keys(it.stores).length ? `<small class="miss">Ingen priser hos kjedene nå – prøv «Bytt»</small>` : ""}
          <button class="small linkish" data-kswap="${i}">${it.kname ? "Bytt" : "Søk"}</button></td>
        ${stores.map((t) => {
          const p = it.stores[t.code];
          if (!p) return `<td class="na">–</td>`;
          const cheap = it.cheapest === t.code;
          return `<td class="${cheap ? "cheap" : ""}">${kr2(p.price)}${p.deal ? " 🏷" : ""}${p.unit_price && perU(p.unit) ? `<small>${krU(p.unit_price, p.unit)}</small>` : ""}${p.typical && p.deal ? `<small>vanlig ${kr2(p.typical)}</small>` : ""}</td>`;
        }).join("")}
        <td class="usual">${it.usual_unit && it.basis !== "pk" ? `${krU(it.usual_unit, it.basis)}${it.usual ? `<small>ca. ${kr2(it.usual)} per pakning</small>` : ""}` : it.usual ? kr2(it.usual) : "–"}</td>
      </tr>`).join("")}</tbody>
    </table></div>
    <div id="k-swap"></div>
    <p class="hint">Prisene er hentet fra kjedenes nettbutikker via Kassalapp og kan avvike fra prisen i din butikk. Tilbud i appene (Trumf, Coop-medlem) er ikke med.</p>
    <div class="modal-acts"><button class="primary" id="m-close">Lukk</button></div>
  `, (m) => {
    m.closest("dialog").classList.add("wide-dialog");
    $("#m-close", m).onclick = () => { m.closest("dialog").classList.remove("wide-dialog"); closeModal(); };
    m.onclick = async (e) => {
      if (e.target.closest("#c-all")) { state.cmpAll = !state.cmpAll; renderCompare(res, names); return; }
      const b = e.target.closest("button[data-kswap]");
      if (b) {
        const it = res.items[+b.dataset.kswap];
        const box = $("#k-swap", m);
        box.innerHTML = `<div class="card"><b>Velg riktig vare for «${esc(it.name)}»</b>
          <div class="addrow" style="padding:8px 0"><input type="text" id="ks-q" value="${esc(it.kname || it.name)}"><button class="small" id="ks-go">Søk</button></div><div id="ks-res"></div></div>`;
        box.scrollIntoView({ behavior: "smooth" });
        const go = async () => {
          $("#ks-res", m).innerHTML = `<span class="spinner"></span>Søker …`;
          try {
            const r = await api("kassal/search?" + new URLSearchParams({ q: $("#ks-q", m).value }));
            $("#ks-res", m)._r = r;
            $("#ks-res", m).innerHTML = r.length ? r.map((x, k) => `<button class="pick" data-kpick="${k}">${esc(x.name)}<small>${esc([x.brand, x.store].filter(Boolean).join(" · "))}</small><span>${x.price ? kr2(x.price) : ""}</span></button>`).join("") : "Ingen treff.";
          } catch (err) { $("#ks-res", m).textContent = err.message; }
        };
        $("#ks-go", m).onclick = go;
        $("#ks-q", m).addEventListener("keydown", (ev) => { if (ev.key === "Enter") go(); });
        go();
        box._item = it;
        return;
      }
      const pk = e.target.closest("button[data-kpick]");
      if (pk) {
        const x = $("#ks-res", m)._r[+pk.dataset.kpick];
        await api("kassal/remember", { body: { name: $("#k-swap", m)._item.name, ean: x.ean, kname: x.name } });
        modal(`<h3>⚖️ Prissammenligning</h3><div class="job" id="c-job"></div>`, () => {});
        try { renderCompare(await runJob(api("compare", { body: { items: names } }), $("#c-job")), names); } catch (_) {}
      }
    };
  });
}

// ---------------------------------------------------------------- Oda
// Legger varer i Oda-handlekurven. Matplan bestiller ALDRI – du fullfører selv i Oda-appen.
const HOW = { "valgt før": ["Valgt før", "ok"], "pleier å kjøpe": ["Pleier å kjøpe", "ok"], "har kjøpt før": ["Kjøpt før", "ok"], "forslag": ["Sjekk", "warn"] };

async function odaDialog(items) {
  if (!items.length) return toast("Velg varer eller legg noe på handlelista først");
  let st;
  try { st = await api("oda/status"); } catch (e) { return toast(e.message); }
  if (!st.configured) {
    return modal(`<h3>Koble til Oda</h3>
      <p>Legg inn e-post og passord for Oda under <b>Innstillinger → Apper → Matplan → Konfigurasjon</b> i Home Assistant (trykk «Vis ubrukte valgfrie»), og start appen på nytt.</p>
      <p class="hint">🔒 Matplan bruker bare innloggingen til å legge varer i handlekurven. Den kan ikke bestille, betale eller velge levering.</p>
      <div class="modal-acts"><button class="primary" id="m-close">OK</button></div>`, (m) => { $("#m-close", m).onclick = closeModal; });
  }
  modal(`<h3>Oda-handlekurv</h3><div class="job" id="o-job"></div>`, () => {});
  let rows;
  try {
    rows = await runJob(api("oda/match", { body: { items: items.map((i) => i.name) } }), $("#o-job"));
  } catch (_) { return; }
  rows.forEach((r, i) => { r.from_list = items[i].from_list; r.on = !!r.oda; });
  rows.sort((a, b) => (!!b.oda - !!a.oda) || ((a.how === "forslag") - (b.how === "forslag"))); // usikre og ikke funnet nederst

  const render = () => {
    const n = rows.filter((r) => r.on && r.oda).length;
    const sum = rows.filter((r) => r.on && r.oda && r.oda.price).reduce((t, r) => t + r.oda.price * r.quantity, 0);
    modal(`
      <h3>Oda-handlekurv</h3>
      <p class="hint">Sjekk varene. «Sjekk» betyr at Matplan har gjettet – trykk «Bytt» hvis det er feil vare.</p>
      <div class="list oda-list">${rows.map((r, i) => {
        const [lbl, cls] = HOW[r.how] || ["Ikke funnet", "warn"];
        return `<div class="row oda-row ${r.on ? "" : "off"}">
          <input type="checkbox" data-on="${i}" ${r.on ? "checked" : ""} ${r.oda ? "" : "disabled"}>
          <div class="main">
            <div class="name">${esc(r.name)} <span class="badge ${cls === "ok" ? "okb" : ""}">${lbl}</span></div>
            <div class="info">${r.oda ? `${esc(r.oda.name)}${r.oda.extra ? ", " + esc(r.oda.extra) : ""}${r.oda.price ? " · " + kr2(r.oda.price) : ""}` : esc(r.error || "Fant ingen vare hos Oda")}</div>
            ${r.oda && r.oda.discount ? `<div class="info price">🏷 ${esc(r.oda.discount.text || "Tilbud")}${r.oda.discount.regular ? ` · vanlig ${kr2(r.oda.discount.regular)}` : ""}${r.oda.discount.max_qty ? ` · maks ${r.oda.discount.max_qty} stk` : ""}</div>` : ""}
            <div class="oda-acts">
              <span class="qty"><button data-dec="${i}">−</button><b>${r.quantity}</b><button data-inc="${i}">+</button></span>
              <button class="small" data-swap="${i}">Bytt</button>
              ${r.from_list ? `<button class="small danger" data-del="${i}" title="Fjern fra handlelista">🗑 Fjern fra lista</button>` : `<button class="small" data-skip="${i}">Hopp over</button>`}
            </div>
            <div class="oda-swap" id="swap-${i}" hidden></div>
          </div>
        </div>`;
      }).join("")}</div>
      ${rows.some((r) => r.from_list) ? `<label class="row" style="padding:12px 0 0"><input type="checkbox" id="o-complete"><div class="main"><div class="name">Huk av varene på handlelista i Home Assistant etterpå</div></div></label>` : ""}
      <p class="safe">🔒 Matplan legger bare varer i handlekurven. Ingenting bestilles – du fullfører selv i Oda-appen.</p>
      <div class="modal-acts"><button class="ghost" id="m-cancel">Avbryt</button><button class="oda" id="o-add" ${n ? "" : "disabled"}>Legg ${n} vare${n === 1 ? "" : "r"} i kurven${sum ? ` · ca. ${kr(sum)}` : ""}</button></div>
      <div class="job" id="o-job" hidden></div>
    `, (m) => {
      $("#m-cancel", m).onclick = () => { closeModal(); loadShop(); };
      m.onchange = (e) => { const c = e.target.closest("[data-on]"); if (c) { rows[+c.dataset.on].on = c.checked; render(); } };
      m.onclick = async (e) => {
        const b = e.target.closest("button"); if (!b) return;
        if (b.dataset.del) {
          const r = rows[+b.dataset.del];
          try {
            await api("shopping/remove", { body: { items: [r.from_list] } });
            rows.splice(+b.dataset.del, 1);
            toast(`Fjernet «${r.name}» fra handlelista`);
            render();
          } catch (err) { toast(err.message); }
          return;
        }
        if (b.dataset.skip) { rows.splice(+b.dataset.skip, 1); render(); return; }
        if (b.dataset.inc) { rows[+b.dataset.inc].quantity++; render(); }
        else if (b.dataset.dec) { const r = rows[+b.dataset.dec]; r.quantity = Math.max(1, r.quantity - 1); render(); }
        else if (b.dataset.swap) {
          const i = +b.dataset.swap, box = $("#swap-" + i, m);
          box.hidden = false;
          box.innerHTML = `<div class="addrow" style="padding:6px 0"><input type="text" id="sq-${i}" value="${esc(rows[i].name)}"><button class="small" id="sb-${i}">Søk</button></div><div id="sr-${i}"></div>`;
          const go = async () => {
            $("#sr-" + i, m).innerHTML = `<span class="spinner"></span>Søker …`;
            try {
              const res = await api("oda/search?" + new URLSearchParams({ q: $("#sq-" + i, m).value }));
              $("#sr-" + i, m).innerHTML = res.length ? res.map((x, k) => `<button class="pick ${x.available ? "" : "na"}" data-pick="${i}|${k}">${esc(x.name)}${x.extra ? `<small>${esc(x.extra)}</small>` : ""}<span>${x.price ? kr(x.price) : ""}${x.available ? "" : " · utsolgt"}</span></button>`).join("") : "Ingen treff.";
              $("#sr-" + i, m)._res = res;
            } catch (err) { $("#sr-" + i, m).textContent = err.message; }
          };
          $("#sb-" + i, m).onclick = go;
          $("#sq-" + i, m).addEventListener("keydown", (ev) => { if (ev.key === "Enter") go(); });
          go();
        } else if (b.dataset.pick) {
          const [i, k] = b.dataset.pick.split("|").map(Number);
          rows[i].oda = $("#sr-" + i, m)._res[k];
          rows[i].how = "valgt før";
          rows[i].on = true;
          render();
        } else if (b.id === "o-add") {
          b.disabled = true;
          const chosen = rows.filter((r) => r.on && r.oda);
          try {
            const res = await api("oda/add", { body: {
              items: chosen.map((r) => ({ name: r.name, product_id: r.oda.id, oda_name: r.oda.name, quantity: r.quantity, from_list: r.from_list })),
              complete_ha: !!($("#o-complete", m) && $("#o-complete", m).checked),
            } });
            modal(`
              <h3>✓ Lagt i Oda-handlekurven</h3>
              <p>${res.added} vare${res.added === 1 ? "" : "r"} lagt til. Handlekurven har nå <b>${esc(res.cart.label || res.cart.count + " varer")}</b>${res.cart.total ? ` · ${kr(res.cart.total)}` : ""}.</p>
              ${res.completed_in_ha.length ? `<p class="hint">Huket av ${res.completed_in_ha.length} på handlelista i Home Assistant.</p>` : ""}
              <p class="safe">Ingenting er bestilt. Åpne Oda for å se over kurven, velge levering og bestille.</p>
              <div class="modal-acts"><button class="ghost" id="m-close">Lukk</button><a class="btnlink oda" href="https://oda.com/no/cart/" target="_blank" rel="noopener">Åpne Oda</a></div>`,
              (mm) => { $("#m-close", mm).onclick = () => { closeModal(); loadShop(); }; });
          } catch (err) { toast(err.message, 5000); b.disabled = false; }
        }
      };
    });
  };
  render();
}

// ================================================================ KVITTERINGER
async function fileToPayload(file) {
  const isPdf = file.type === "application/pdf" || /\.pdf$/i.test(file.name);
  if (!isPdf && file.type.startsWith("image/")) {
    try {
      // Skaler ned store mobilbilder før opplasting (raskere og billigere for AI-en)
      const bmp = await createImageBitmap(file);
      const max = 2000;
      const scale = Math.min(1, max / Math.max(bmp.width, bmp.height));
      const c = document.createElement("canvas");
      c.width = Math.round(bmp.width * scale);
      c.height = Math.round(bmp.height * scale);
      c.getContext("2d").drawImage(bmp, 0, 0, c.width, c.height);
      const url = c.toDataURL("image/jpeg", 0.85);
      return { name: file.name.replace(/\.\w+$/, "") + ".jpg", type: "image/jpeg", data: url.split(",")[1] };
    } catch (_) { /* faller tilbake til originalfilen */ }
  }
  const buf = await file.arrayBuffer();
  let bin = "";
  const bytes = new Uint8Array(buf);
  for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  return { name: file.name, type: file.type, data: btoa(bin) };
}

async function upload(files) {
  if (!files.length) return;
  const box = $("#up-job");
  try {
    const payload = [];
    for (const f of files) payload.push(await fileToPayload(f));
    const res = await runJob(api("upload", { body: { files: payload } }), box);
    box.hidden = false;
    box.className = "job";
    box.innerHTML = res.map((r) => `<div>${esc(r.result)}</div>`).join("");
    loadReceipts();
  } catch (_) {}
}
$("#file-cam").addEventListener("change", (e) => { upload([...e.target.files]); e.target.value = ""; });
$("#file-up").addEventListener("change", (e) => { upload([...e.target.files]); e.target.value = ""; });

state.rcSort = pref("rcsort", "date_desc");
state.rcStore = "";

async function loadReceipts() {
  try {
    const st = await api("state");
    state.me = st;
    renderMailbox(st);
    await searchReceipts();
  } catch (e) { toast(e.message); }
}

const MONTHS = ["januar", "februar", "mars", "april", "mai", "juni", "juli", "august", "september", "oktober", "november", "desember"];
const nok = (n) => n == null ? "" : n.toLocaleString("nb-NO", { style: "currency", currency: "NOK" });

async function searchReceipts() {
  const q = $("#rc-q").value.trim();
  const params = new URLSearchParams({ q, store: state.rcStore, sort: state.rcSort });
  const seq = (searchReceipts._seq = (searchReceipts._seq || 0) + 1);
  const [d, eco] = await Promise.all([api("receipts?" + params), api("economy/summary").catch(() => null)]);
  if (seq !== searchReceipts._seq) return; // et nyere søk er på vei
  state.ecoSum = eco && !eco.empty ? eco : null;
  state.rcQuery = q;
  // butikkfilter
  $("#rc-store").innerHTML = `<option value="">Alle butikker</option>` + d.stores.map((s) =>
    `<option value="${esc(s.name)}" ${s.name === state.rcStore ? "selected" : ""}>${esc(s.name)} (${s.count})</option>`).join("");
  $("#rc-sort").value = state.rcSort;

  const el = $("#receipts");
  const sum = $("#rc-summary");
  if (!d.count) {
    sum.textContent = "";
    el.innerHTML = `<div class="card empty">${q ? `Ingen kvitteringer inneholder «${esc(q)}».` : "Ingen kvitteringer ennå."}</div>`;
    return;
  }
  const e = state.ecoSum;
  const pl = (n) => `${n} kvittering${n === 1 ? "" : "er"}`;
  if (q) {
    sum.innerHTML = `<b>${d.count}</b> ${pl(d.count).replace(/^\d+ /, "")} inneholder «${esc(q)}»${d.matched_sum ? ` · ${nok(d.matched_sum)} brukt på treffene` : ""}`;
  } else if (state.rcStore) {
    // snitt per måned for butikken, regnet av hele måneder
    const ms = {};
    d.receipts.forEach((r) => { const m = (r.purchased_on || "").slice(0, 7); if (m) ms[m] = (ms[m] || 0) + (r.total || 0); });
    const ok = Object.keys(ms).filter((m) => e && e.months[m] && !e.months[m].incomplete && m !== e.current.month);
    const avg = ok.length ? ok.reduce((t, m) => t + ms[m], 0) / ok.length : null;
    sum.innerHTML = `<b>${d.count}</b> ${pl(d.count).replace(/^\d+ /, "")} fra ${esc(state.rcStore)}${avg ? ` · snitt <b>${kr(avg)}/mnd</b>` : ""}`;
  } else if (e && e.avg_month) {
    const c = e.current, mn = MONTHS[+c.month.slice(5) - 1].replace(/^./, (x) => x.toUpperCase());
    sum.innerHTML = `<div class="ecoline"><div>Snitt <b>${kr(e.avg_month)}/mnd</b> <span>siste ${e.basis_months} hele måned${e.basis_months === 1 ? "" : "er"}</span></div>
      <div><span>${mn} så langt ${kr(c.so_far)}${c.projection ? ` · ligger an til ca. ${kr(c.projection)}` : ""}${e.budget ? ` · budsjett ${kr(e.budget)}` : ""}</span>
      <a href="#" data-go-eco>Se økonomi ›</a></div></div>`;
  } else {
    sum.innerHTML = `<b>${d.count}</b> ${pl(d.count).replace(/^\d+ /, "")} · snittet per måned kommer når dere har kvitteringer for minst én hel måned`;
  }
  const goEco = $("[data-go-eco]", sum); if (goEco) goEco.onclick = (ev) => { ev.preventDefault(); showTab("okonomi"); };

  // grupper etter måned (datosortering) eller butikk
  const groupKey = (r) => {
    if (state.rcSort === "store") return r.store;
    if (state.rcSort === "total") return "";
    const [y, m] = (r.purchased_on || "").split("-");
    return y ? `${MONTHS[+m - 1].replace(/^./, (c) => c.toUpperCase())} ${y}` : "Uten dato";
  };
  const groups = [];
  d.receipts.forEach((r) => {
    const k = groupKey(r);
    if (!groups.length || groups[groups.length - 1].key !== k) groups.push({ key: k, month: (r.purchased_on || "").slice(0, 7), items: [] });
    groups[groups.length - 1].items.push(r);
  });
  // ved månedsgruppering uten filter: ▲/▼ mot snittet, og merk måneder som trolig mangler kvitteringer
  const vsAvg = (g) => {
    if (q || state.rcStore || state.rcSort === "store" || state.rcSort === "total" || !e || !e.avg_month) return "";
    const m = e.months[g.month];
    if (!m) return "";
    if (g.month === e.current.month) return ` <em>pågår</em>`;
    if (m.incomplete) return ` <em>trolig ufullstendig</em>`;
    const pct = Math.round((m.total - e.avg_month) / e.avg_month * 100);
    return Math.abs(pct) < 3 ? "" : ` <em class="${pct > 0 ? "up" : "down"}">${pct > 0 ? "▲" : "▼"}${Math.abs(pct)} %</em>`;
  };
  el.innerHTML = groups.map((g) => `
    ${g.key ? `<div class="cat">${esc(g.key)} <span>${g.items.length} · ${kr(g.items.reduce((t, r) => t + (r.total || 0), 0))}${vsAvg(g)}</span></div>` : ""}
    <div class="card list">${g.items.map((r) => `
      <div class="row click" data-rid="${r.id}">
        <div class="main"><div class="name">${esc(state.rcSort === "store" ? fmtDate(r.purchased_on) : r.store + " · " + fmtDate(r.purchased_on))}</div>
        <div class="info">${r.items} varer${r.total ? " · " + nok(r.total) : ""} · ${r.source === "email" ? "e-post" : "opplastet"}${r.uploaded_by ? " av " + esc(r.uploaded_by) : ""}</div>
        ${(r.hits || []).length ? `<div class="hits">${r.hits.slice(0, 4).map((h) =>
          `<span>${esc(h.name)}${h.qty && h.qty !== 1 ? " · " + esc(h.qty) + "×" : ""}${h.price != null ? " · " + nok(h.price) : ""}</span>`).join("")}${r.hits.length > 4 ? `<span>+ ${r.hits.length - 4} til</span>` : ""}</div>` : ""}
        </div>
        <span>›</span>
      </div>`).join("")}</div>`).join("");
}

let rcTimer;
$("#rc-q").addEventListener("input", () => { clearTimeout(rcTimer); rcTimer = setTimeout(() => searchReceipts().catch((e) => toast(e.message)), 250); });
$("#rc-store").addEventListener("change", (e) => { state.rcStore = e.target.value; searchReceipts(); });
$("#rc-sort").addEventListener("change", (e) => { state.rcSort = e.target.value; setPref("rcsort", e.target.value); searchReceipts(); });

function renderMailbox(st) {
  const m = st.mail || {};
  const pend = st.counts.pending ? `<p class="hint">${st.counts.pending} varenavn venter på gjenkjenning. <button class="small" id="btn-norm">Prøv nå</button></p>` : "";
  $("#mailbox").innerHTML = m.enabled
    ? `<button class="ghost wide" id="btn-mail">✉️ Hent kvitteringer fra e-post nå</button>
       <p class="hint">${m.last_run ? `Sist hentet ${esc(m.last_run.replace("T", " kl. "))}: ${esc(m.error ? "feil – " + m.error : m.last_result || "")}` : "Hentes automatisk."}</p>${pend}`
    : `<p class="hint">Tips: Slå på e-posthenting i app-innstillingene i Home Assistant, så kommer Oda-kvitteringer og videresendte kvitteringer inn automatisk.</p>${pend}`;
  const b = $("#btn-mail");
  if (b) b.onclick = async () => {
    b.disabled = true;
    try { toast(await runJob(api("mail/poll", { body: {} }), $("#up-job")), 5000); loadReceipts(); } catch (_) {}
    b.disabled = false;
  };
  const n = $("#btn-norm");
  if (n) n.onclick = async () => { try { toast(await runJob(api("normalize", { body: {} }), $("#up-job"))); loadReceipts(); } catch (_) {} };
}

function isHit(i) {
  const q = (state.rcQuery || "").toLowerCase();
  if (!q) return false;
  const hay = `${i.raw_name || ""} ${i.product || ""}`.toLowerCase();
  return q.split(/\s+/).every((t) => hay.includes(t));
}

// Kvitteringsdetaljer: eget felt ved siden av listen på PC, ellers et vindu
$("#receipts").addEventListener("click", async (e) => {
  const r = e.target.closest("[data-rid]");
  if (!r) return;
  const d = await api("receipts/" + r.dataset.rid);
  $$("#receipts [data-rid]").forEach((x) => x.classList.toggle("selected", x === r));
  const html = `
    <h3>${esc(d.store)} · ${esc(fmtDate(d.purchased_on))}</h3>
    <p class="hint">${d.items.length} varer${d.total ? " · " + nok(d.total) : ""}${d.uploaded_by ? " · " + esc(d.uploaded_by) : ""}</p>
    <div class="list">${d.items.map((i) => `<div class="row ${isHit(i) ? "hit" : ""}"><div class="main"><div class="name">${esc(i.raw_name)}</div>
      <div class="info">${i.product_id === 0 ? "ikke en vare" : i.product ? "→ " + esc(i.product) + " · " + esc(i.category || "") : "venter på gjenkjenning"}</div></div>
      <div class="info">${i.qty && i.qty !== 1 ? esc(i.qty) + " × " : ""}${i.price != null ? esc(i.price.toFixed(2).replace(".", ",")) : ""}</div></div>`).join("")}</div>
    <div class="modal-acts"><button class="ghost danger" id="m-del">Slett kvittering</button>${isWide() ? "" : `<button class="primary" id="m-close">Lukk</button>`}</div>`;
  const bind = (m, close) => {
    $("#m-close", m) && ($("#m-close", m).onclick = close);
    $("#m-del", m).onclick = async () => {
      if (!$("#m-del", m).dataset.sure) { $("#m-del", m).dataset.sure = 1; $("#m-del", m).textContent = "Trykk igjen for å slette"; return; }
      await api("receipts/" + d.id, { method: "DELETE" });
      close();
      loadReceipts();
    };
  };
  if (isWide()) {
    const panel = $("#rc-detail");
    panel.hidden = false;
    panel.innerHTML = html;
    bind(panel, () => { panel.hidden = true; panel.innerHTML = ""; });
  } else {
    modal(html, (m) => bind(m, closeModal));
  }
});

// ================================================================ LAGER
// Kategorier i den rekkefølgen man typisk går gjennom en norsk butikk
const AISLE = ["Frukt og grønt", "Brød og bakst", "Kjøtt", "Fisk og sjømat", "Pålegg", "Meieri og egg",
  "Frysevarer", "Tørrvarer", "Hermetikk og sauser", "Snacks og godteri", "Drikke", "Barn",
  "Husholdning", "Hygiene", "Annet"];
const aisleIdx = (c) => { const i = AISLE.indexOf(c); return i < 0 ? 99 : i; };
const kr = (n) => n == null ? "" : Math.round(n).toLocaleString("nb-NO") + " kr";
// pris med øre (29,90 kr)
const kr2 = (n) => n == null ? "" : Number(n).toLocaleString("nb-NO", { minimumFractionDigits: 2, maximumFractionDigits: 2 }) + " kr";
const isWide = () => window.matchMedia("(min-width: 1000px)").matches;
// enhet for sammenligningspris: kg, l, stk – «pk» betyr per pakning
const normUnit = (u) => ({ piece: "stk", st: "stk", kilo: "kg", liter: "l", ltr: "l" }[(u || "").toLowerCase()] || (u || "").toLowerCase());
const perU = (u) => { u = normUnit(u); return u === "kg" ? "/kg" : u === "l" ? "/l" : u === "stk" ? "/stk" : ""; };
const krU = (n, u) => n == null ? "" : kr2(n) + perU(u);

function pref(key, def) { try { return localStorage.getItem("matplan-" + key) || def; } catch (_) { return def; } }
function setPref(key, v) { try { localStorage.setItem("matplan-" + key, v); } catch (_) {} }

// Visning: «status» (hva som trenger oppmerksomhet + faste varer først), «sted» (kjøleskap, fryser …) og «liste» (sorterbar / tabell på PC)
state.pantryView = pref("pview", isWide() ? "list" : "status");
state.pantrySort = pref("psort", "category");
state.pantryLoc = pref("ploc", "Kjøleskap");

const PANTRY_VIEWS = { status: "Status", sted: "Sted i huset", list: "Liste" };
const PANTRY_SORTS = {
  category: ["Kategori", null],
  times: ["Mest kjøpt", (a, b) => b.q90 - a.q90 || b.times - a.times],
  often: ["Oftest kjøpt", (a, b) => (a.interval ?? 9999) - (b.interval ?? 9999)],
  left: ["Snart tomt", (a, b) => (a.in_stock === b.in_stock ? a.days_left - b.days_left : a.in_stock ? -1 : 1)],
  recent: ["Sist kjøpt", (a, b) => a.since - b.since],
  spent: ["Mest penger (3 mnd)", (a, b) => (b.spent_90 || 0) - (a.spent_90 || 0)],
  price: ["Dyrest (per pakning)", (a, b) => (b.avg_price || 0) - (a.avg_price || 0)],
  left_days: ["Dager igjen", (a, b) => (a.in_stock ? a.days_left : -1) - (b.in_stock ? b.days_left : -1)],
  name: ["Navn", (a, b) => a.name.localeCompare(b.name, "nb")],
};

// Hvor i huset en kategori typisk står
const LOCATIONS = {
  "Kjøleskap": ["Kjøtt", "Fisk og sjømat", "Pålegg", "Meieri og egg"],
  "Fryser": ["Frysevarer"],
  "Frukt og brød": ["Frukt og grønt", "Brød og bakst"],
  "Skap": ["Tørrvarer", "Hermetikk og sauser", "Snacks og godteri", "Drikke", "Barn"],
  "Annet": ["Husholdning", "Hygiene", "Annet"],
};
const locationOf = (cat) => Object.keys(LOCATIONS).find((l) => LOCATIONS[l].includes(cat)) || "Annet";

// Hvor ofte varen kjøpes: fast (jevnlig siste halvår), av og til, eller bare én gang
const freqOf = (p) => p.regular ? "fast" : p.times === 1 ? "once" : "occasional";
// Hvor lenge varen trolig varer totalt (samme regel som på serveren)
const lifeOf = (p) => Math.max(1, p.interval ? Math.min(p.shelf_days, p.interval * 1.3) : p.shelf_days);

async function loadPantry() {
  try { state.pantry = await api("pantry"); renderPantryMenu(); renderPantry(); } catch (e) { toast(e.message); }
}

function renderPantryMenu() {
  $("#pantry-view-lbl").textContent = PANTRY_VIEWS[state.pantryView] || "Status";
  $("#pantry-menu-body").innerHTML = `
    <div class="pm-h">Visning</div>
    ${Object.entries(PANTRY_VIEWS).map(([k, l]) => `<button data-view="${k}" class="${state.pantryView === k ? "sel" : ""}">${state.pantryView === k ? "✓ " : ""}${l}<small>${
      { status: "Det som haster først, faste varer øverst", sted: "Kjøleskap, fryser, skap …", list: isWide() ? "Tabell med alle tall" : "Alle varer, valgfri sortering" }[k]}</small></button>`).join("")}`;
}

$("#pantry-menu-body").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-view]"); if (!b) return;
  state.pantryView = b.dataset.view; setPref("pview", b.dataset.view);
  $("#pantry-menu").open = false;
  renderPantryMenu(); renderPantry();
});
$("#pantry-menu-body").addEventListener("change", (e) => {
  if (e.target.id !== "pantry-sort") return;
  state.pantrySort = e.target.value; setPref("psort", e.target.value);
  $("#pantry-menu").open = false; renderPantry();
});
$("#pantry-q").addEventListener("input", () => renderPantry());

const trendTxt = (t) => t == null || Math.abs(t) < 5 ? "" : (t > 0 ? ` ▲${t} %` : ` ▼${-t} %`);

// kort status til høyre i raden
function stockTxt(p) {
  if (!p.in_stock) return "tomt";
  if (p.override === "har") return "har";
  if (p.days_left <= 0) return "snart tom";
  return p.days_left > 60 ? "har" : `~${p.days_left} d`;
}

const stBtn = (p, status, label) => `<button data-p="${p.id}" data-s="${status}">${label}</button>`;

// én linje per vare: navn (+ rytme), status og knappene som passer for gruppa
function pantryLine(p, acts = "", extra = "") {
  return `<div class="pl">
    <div class="pl-n click" data-edit="${p.id}">${esc(p.name)}${p.bered ? `<span class="bmark" title="Del av beredskapslageret">🛟</span>` : ""}${hBadge(p)}${extra ? `<small>${extra}</small>` : ""}${p.ignore ? `<small>· foreslås ikke</small>` : ""}</div>
    <span class="pl-s ${p.in_stock ? "" : "out"}">${stockTxt(p)}</span>
    ${acts ? `<div class="pill2">${acts}</div>` : ""}
  </div>`;
}

function pantryGroup(key, title, list, opts = {}) {
  if (!list.length) return "";
  const open = opts.open ?? pref("pg-" + key, opts.defOpen ? "1" : "0") === "1";
  const rows = list.map((p) => pantryLine(p, opts.acts ? opts.acts(p) : "", opts.extra ? opts.extra(p) : "")).join("");
  if (opts.fixed) {
    return `<div class="pg"><div class="pg-h ${opts.tone || ""}"><span>${title}</span><span>${list.length}</span></div><div class="card list">${rows}</div></div>`;
  }
  return `<details class="pg" data-g="${key}" ${open ? "open" : ""}>
    <summary class="pg-h ${opts.tone || ""}"><span>${title}</span><span>${list.length}</span></summary>
    <div class="card list">${rows}</div></details>`;
}

function renderPantry() {
  const el = $("#pantry");
  const all = state.pantry || [];
  if (!all.length) {
    el.innerHTML = `<div class="card empty">Ingen varer ennå – last opp noen kvitteringer.</div>`;
    return;
  }
  const q = $("#pantry-q").value.trim().toLowerCase();
  const byName = (a, b) => a.name.localeCompare(b.name, "nb");
  if (q) {
    const hits = all.filter((p) => p.name.toLowerCase().includes(q)).sort(byName);
    el.innerHTML = hits.length
      ? `<div class="pg"><div class="pg-h"><span>Treff</span><span>${hits.length}</span></div><div class="card list">${hits.map((p) =>
          pantryLine(p, p.in_stock ? stBtn(p, "tomt", "Tomt") : stBtn(p, "har", "Har"), esc(p.category))).join("")}</div></div>`
      : `<div class="card empty">Ingen varer heter «${esc(q)}».</div>`;
    return;
  }
  if (state.pantryView === "sted") return renderPantryPlaces(el, all);
  if (state.pantryView === "list") return renderPantryList(el, all);

  // A + C: det som haster først, så faste varer, så resten sammenslått
  const soon = all.filter((p) => p.use_soon).sort((a, b) => a.days_left - b.days_left);
  const low = all.filter((p) => !p.use_soon && p.regular && p.in_stock && p.override !== "har" && p.days_left <= 2)
    .sort((a, b) => a.days_left - b.days_left);
  const shown = new Set([...soon, ...low].map((p) => p.id));
  const rest = all.filter((p) => !shown.has(p.id));
  const home = rest.filter((p) => p.in_stock && (freqOf(p) === "fast" || p.manual)).sort(byName);
  const empty = rest.filter((p) => !p.in_stock && freqOf(p) === "fast").sort((a, b) => a.since - b.since);
  const occ = rest.filter((p) => freqOf(p) === "occasional" && !p.manual).sort(byName);
  const once = rest.filter((p) => freqOf(p) === "once" && !p.manual).sort((a, b) => a.since - b.since);
  const rh = (p) => p.rhythm && p.rhythm !== "sjelden" ? esc(p.rhythm) : "";

  el.innerHTML = `<div class="pgrid">
    ${pantryGroup("soon", "Bruk snart", soon, { fixed: true, tone: "danger",
      acts: (p) => stBtn(p, "tomt", "Brukt"), extra: (p) => "kjøpt " + esc(ago(p.since)) })}
    ${pantryGroup("low", "Går tom snart", low, { fixed: true, tone: "warn",
      acts: (p) => stBtn(p, "har", "Har mer") + stBtn(p, "tomt", "Tomt"), extra: rh })}
    ${!soon.length && !low.length ? `<div class="card empty">Ingenting haster 👍</div>` : ""}
    ${pantryGroup("home", "Har hjemme", home, { defOpen: true, acts: (p) => stBtn(p, "tomt", "Tomt"), extra: rh })}
    ${pantryGroup("empty", "Tomt – faste varer", empty, { acts: (p) => stBtn(p, "har", "Har"), extra: rh })}
    ${pantryGroup("occ", "Av og til", occ, { acts: (p) => p.in_stock ? stBtn(p, "tomt", "Tomt") : stBtn(p, "har", "Har") })}
    ${pantryGroup("once", "Kjøpt én gang", once, { acts: (p) => p.in_stock ? stBtn(p, "tomt", "Tomt") : stBtn(p, "har", "Har"),
      extra: (p) => esc(ago(p.since)) })}
  </div>
  <p class="hint">Lageret er et anslag ut fra kvitteringer og holdbarhet. Trykk på en vare for priser og detaljer.</p>`;
}

// B: etter sted i huset, med en stripe som viser omtrent hvor mye som er igjen
function renderPantryPlaces(el, all) {
  const byLoc = {};
  Object.keys(LOCATIONS).forEach((l) => (byLoc[l] = []));
  all.filter((p) => p.in_stock || freqOf(p) === "fast").forEach((p) => byLoc[locationOf(p.category)].push(p));
  if (!byLoc[state.pantryLoc]) state.pantryLoc = "Kjøleskap";
  const list = byLoc[state.pantryLoc].sort((a, b) =>
    (b.in_stock - a.in_stock) || (b.use_soon - a.use_soon) || a.name.localeCompare(b.name, "nb"));
  const bar = (p) => {
    if (!p.in_stock) return `<div class="lvl"></div>`;
    const f = p.override === "har" ? 1 : Math.max(0.06, Math.min(1, p.days_left / lifeOf(p)));
    const tone = p.use_soon ? "bad" : f < 0.3 ? "mid" : "ok";
    return `<div class="lvl" title="${stockTxt(p)}"><i class="${tone}" style="width:${Math.round(f * 100)}%"></i></div>`;
  };
  el.innerHTML = `
    <div class="chips flush locs">${Object.keys(LOCATIONS).map((l) => {
      const n = byLoc[l].filter((p) => p.in_stock).length;
      return `<button data-loc="${esc(l)}" class="${l === state.pantryLoc ? "sel" : ""}">${l} <span>${n}</span></button>`; }).join("")}</div>
    ${list.length ? `<div class="card list">${list.map((p) => `<div class="pl ${p.in_stock ? "" : "dim"}">
      <div class="pl-n click" data-edit="${p.id}">${esc(p.name)}${p.use_soon ? `<small class="warn">bruk snart</small>` : ""}${!p.in_stock ? `<small>tomt</small>` : ""}</div>
      ${bar(p)}
      <div class="pill2">${p.in_stock ? stBtn(p, "tomt", "Tomt") : stBtn(p, "har", "Har")}</div>
    </div>`).join("")}</div>` : `<div class="card empty">Ingenting her.</div>`}
    <p class="hint">Stedet følger kategorien. Trykk på en vare for å endre kategori.</p>`;
  $$("button[data-loc]", el).forEach((b) => b.addEventListener("click", () => {
    state.pantryLoc = b.dataset.loc; setPref("ploc", b.dataset.loc); renderPantry();
  }));
}

function pantryInfo(p) {
  const bits = [`Kjøpt ${ago(p.since)}`];
  if (p.in_stock && p.override !== "har") bits.push(p.days_left <= 0 ? "trolig snart tom" : `ca. ${p.days_left} dag${p.days_left === 1 ? "" : "er"} igjen`);
  bits.push(`${p.q90}× per kvartal`);
  const pr = p.prices;
  if (pr && pr.avg90) bits.push(`snitt ${krU(pr.avg90, pr.unit)}${trendTxt(pr.trend)}`);
  if (pr && pr.spent90 && (state.pantrySort === "spent" || state.pantrySort === "price")) bits.push(`${kr(pr.spent90)} siste 3 mnd`);
  if (state.pantrySort !== "category") bits.push(p.category);
  return bits.join(" · ");
}

function pantryRow(p) {
  return `
    <div class="row">
      <div class="main click" data-edit="${p.id}"><div class="name">${esc(p.name)}${p.bered ? `<span class="bmark" title="Del av beredskapslageret">🛟</span>` : ""}${hBadge(p)}${p.use_soon ? `<span class="badge">bruk snart</span>` : ""}${p.rhythm ? `<span class="badge soft">${p.rhythm}</span>` : ""}${p.ignore ? `<span class="badge">foreslås ikke</span>` : ""}</div>
        <div class="info">${esc(pantryInfo(p))}</div></div>
      <div class="pill2">
        <button data-p="${p.id}" data-s="${p.override === "har" ? "" : "har"}" class="${p.override === "har" ? "on-har" : ""}">Har</button>
        <button data-p="${p.id}" data-s="${p.override === "tomt" ? "" : "tomt"}" class="${p.override === "tomt" ? "on-tomt" : ""}">Tomt</button>
      </div>
    </div>`;
}

// Liste: alle varer med valgfri sortering (tabell på PC)
state.pantryRev = pref("prev", "0") === "1";

// sorteringsknapp over lista: velg hva det sorteres på, og snu rekkefølgen med ⇅
function pantrySortBar(n) {
  return `<div class="psort">
    <span>${n} varer</span>
    <label class="psort-sel">Sorter: <select id="psort-inline">${Object.entries(PANTRY_SORTS).map(([k, [l]]) =>
      `<option value="${k}" ${state.pantrySort === k ? "selected" : ""}>${l}</option>`).join("")}</select></label>
    <button id="psort-rev" class="${state.pantryRev ? "on" : ""}" aria-label="Snu rekkefølgen" title="Snu rekkefølgen">${state.pantryRev ? "↑" : "↓"}</button>
  </div>`;
}

function wirePantrySortBar(el) {
  const sel = $("#psort-inline", el), rev = $("#psort-rev", el);
  if (sel) sel.onchange = () => { state.pantrySort = sel.value; setPref("psort", sel.value); state.pantryRev = false; setPref("prev", "0"); renderPantryMenu(); renderPantry(); };
  if (rev) rev.onclick = () => { state.pantryRev = !state.pantryRev; setPref("prev", state.pantryRev ? "1" : "0"); renderPantry(); };
}

function renderPantryList(el, list) {
  const sorter = PANTRY_SORTS[state.pantrySort]?.[1];
  const rv = (arr) => state.pantryRev ? arr.reverse() : arr;
  if (isWide()) return renderPantryTable(el, rv(sorter ? [...list].sort(sorter) : [...list].sort((a, b) => aisleIdx(a.category) - aisleIdx(b.category) || a.name.localeCompare(b.name, "nb"))));
  if (!sorter) {
    const groups = {};
    list.forEach((p) => (groups[p.category] ||= []).push(p));
    el.innerHTML = pantrySortBar(list.length) + rv(Object.keys(groups).sort((a, b) => aisleIdx(a) - aisleIdx(b))).map((cat) => `
      <div class="cat">${esc(cat)} <span>${groups[cat].length}</span></div>
      <div class="card list">${groups[cat].sort((a, b) => a.name.localeCompare(b.name, "nb")).map(pantryRow).join("")}</div>`).join("");
  } else {
    el.innerHTML = pantrySortBar(list.length) + `<div class="card list">${rv([...list].sort(sorter)).map(pantryRow).join("")}</div>`;
  }
  wirePantrySortBar(el);
}

// PC: én oversiktlig tabell, sorter ved å klikke på kolonnetitlene (klikk igjen for å snu)
function renderPantryTable(el, list) {
  const col = (key, label, cls = "") => `<th class="${cls} ${state.pantrySort === key ? "sorted" : ""}" data-sort="${key}">${label}${state.pantrySort === key ? (state.pantryRev ? " ▴" : " ▾") : ""}</th>`;
  el.innerHTML = `${pantrySortBar(list.length)}
  <div class="card tablewrap"><table class="ptable">
    <thead><tr>${col("name", "Vare")}${col("category", "Kategori")}${col("times", "Per kvartal", "num")}${col("price", "Snittpris 3 mnd (kg/l/stk)", "num")}${col("spent", "Brukt 3 mnd", "num")}${col("recent", "Sist kjøpt", "num")}${col("left_days", "Dager igjen", "num")}<th>Status</th></tr></thead>
    <tbody>${list.map((p) => {
      const pr = p.prices || {};
      return `<tr>
        <td class="click" data-edit="${p.id}"><b>${esc(p.name)}</b>${p.bered ? `<span class="bmark" title="Del av beredskapslageret">🛟</span>` : ""}${hBadge(p)}${p.use_soon ? `<span class="badge">bruk snart</span>` : ""}${p.rhythm ? `<span class="badge soft">${p.rhythm}</span>` : ""}${p.ignore ? `<span class="badge">foreslås ikke</span>` : ""}</td>
        <td>${esc(p.category)}</td>
        <td class="num">${p.q90}×</td>
        <td class="num">${pr.avg90 ? krU(pr.avg90, pr.unit) : (p.avg_price ? kr2(p.avg_price) : "–")}<small>${trendTxt(pr.trend)}</small></td>
        <td class="num">${pr.spent90 ? kr(pr.spent90) : "–"}</td>
        <td class="num">${ago(p.since)}</td>
        <td class="num">${p.in_stock ? (p.override === "har" ? "har" : p.days_left) : "tomt"}</td>
        <td><div class="pill2">
          <button data-p="${p.id}" data-s="${p.override === "har" ? "" : "har"}" class="${p.override === "har" ? "on-har" : ""}">Har</button>
          <button data-p="${p.id}" data-s="${p.override === "tomt" ? "" : "tomt"}" class="${p.override === "tomt" ? "on-tomt" : ""}">Tomt</button>
        </div></td></tr>`;
    }).join("")}</tbody></table></div>`;
  $$("th[data-sort]", el).forEach((th) => th.addEventListener("click", () => {
    if (state.pantrySort === th.dataset.sort) state.pantryRev = !state.pantryRev;
    else { state.pantrySort = th.dataset.sort; state.pantryRev = false; }
    setPref("psort", state.pantrySort); setPref("prev", state.pantryRev ? "1" : "0");
    renderPantryMenu(); renderPantry();
  }));
  wirePantrySortBar(el);
}

function sparkline(hist) {
  if (!hist || hist.length < 2) return "";
  const ps = hist.map((h) => h.p), lo = Math.min(...ps), hi = Math.max(...ps), w = 260, h = 46;
  const pts = ps.map((v, i) => `${(i / (ps.length - 1)) * w},${hi === lo ? h / 2 : h - 4 - ((v - lo) / (hi - lo)) * (h - 8)}`).join(" ");
  return `<svg class="spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none"><polyline points="${pts}" fill="none" stroke="currentColor" stroke-width="2" vector-effect="non-scaling-stroke"/></svg>`;
}

// Manuell registrering – kategoriseres automatisk uten AI (ordliste + kjente varer)
$("#pantry-add").addEventListener("click", () => {
  modal(`
    <h3>Legg til varer</h3>
    <label class="field">Varer<small>Skill flere varer med komma, f.eks. «melk, egg, grovbrød».</small>
      <input type="text" id="a-names" placeholder="Hva har dere?" autocomplete="off"></label>
    <div id="a-preview" class="list"></div>
    <label class="row" style="padding:12px 0 0"><input type="checkbox" id="a-bought" checked>
      <div class="main"><div class="name">Nettopp kjøpt</div><div class="info">Teller med i kjøpsmønsteret. Skru av for ting dere har fra før.</div></div></label>
    <div class="modal-acts"><button class="ghost" id="m-cancel">Avbryt</button><button class="primary" id="m-save" disabled>Legg til</button></div>
  `, (m) => {
    let rows = [];
    let timer;
    const render = () => {
      $("#a-preview", m).innerHTML = rows.map((r, i) => `
        <div class="row addprev">
          <div class="main"><div class="name">${esc(r.existing || r.name)}</div>
            <div class="info">${r.existing ? (r.existing.toLowerCase() === r.name.toLowerCase() ? "Kjent vare" : `Kobles til kjent vare (du skrev «${esc(r.name)}»)`) : "Ny vare"}</div></div>
          <select data-i="${i}">${AISLE.map((c) => `<option ${c === r.category ? "selected" : ""}>${c}</option>`).join("")}</select>
        </div>`).join("");
      $("#m-save", m).disabled = !rows.length && !$("#a-names", m).value.trim();
      $("#m-save", m).textContent = rows.length > 1 ? `Legg til ${rows.length} varer` : "Legg til";
    };
    // Henter kategorier. Et halvskrevet siste ord (som har et forslag i ordlisten) tas ikke med før lagring.
    const refreshRows = async (includePartial) => {
      const raw = $("#a-names", m).value;
      let names = raw.split(",").map((x) => x.trim()).filter(Boolean);
      const last = raw.split(",").pop().trim();
      if (!includePartial && last && !raw.trim().endsWith(",") && VOCAB &&
          VOCAB.some((v) => lc(v.w).startsWith(lc(last)) && lc(v.w) !== lc(last))) {
        names = names.slice(0, -1);
      }
      if (!names.length) { rows = []; return render(); }
      const res = await api("categorize?" + new URLSearchParams({ names: names.join(",") }));
      const prev = Object.fromEntries(rows.filter((r) => r.touched).map((r) => [r.name.toLowerCase(), r.category]));
      rows = res.map((r) => prev[r.name.toLowerCase()] ? { ...r, category: prev[r.name.toLowerCase()], touched: true } : r);
      render();
      $("#m-save", m).disabled = !$("#a-names", m).value.trim();
    };
    $("#a-names", m).addEventListener("input", () => {
      clearTimeout(timer);
      $("#m-save", m).disabled = !$("#a-names", m).value.trim();
      timer = setTimeout(() => refreshRows(false).catch((e) => toast(e.message)), 250);
    });
    $("#a-preview", m).addEventListener("change", (e) => {
      const sel = e.target.closest("select[data-i]");
      if (sel) { rows[+sel.dataset.i].category = sel.value; rows[+sel.dataset.i].touched = true; }
    });
    $("#m-cancel", m).onclick = closeModal;
    $("#m-save", m).onclick = async () => {
      try {
        clearTimeout(timer);
        await refreshRows(true);
        if (!rows.length) return;
        const r = await api("pantry/add", { body: { items: rows.map((x) => ({ name: x.name, category: x.category })), bought: $("#a-bought", m).checked } });
        closeModal();
        toast(`La til ${r.added.map((a) => a.product).join(", ")}`, 4000);
        loadPantry();
        loadVocab(true);
      } catch (e) { toast(e.message); }
    };
    attachAutocomplete($("#a-names", m), { multi: true });
    $("#a-names", m).focus();
  });
});

$("#pantry").addEventListener("click", async (e) => {
  const b = e.target.closest("button[data-p]");
  if (b) {
    await api("pantry", { body: { product_id: +b.dataset.p, status: b.dataset.s || null } });
    return loadPantry();
  }
  const ed = e.target.closest("[data-edit]");
  if (ed) editProduct(state.pantry.find((p) => p.id === +ed.dataset.edit));
});
// husk hvilke grupper som er åpne
$("#pantry").addEventListener("toggle", (e) => {
  const d = e.target; if (d.matches && d.matches("details.pg[data-g]")) setPref("pg-" + d.dataset.g, d.open ? "1" : "0");
}, true);
// lukk visningsmenyen når man trykker utenfor
document.addEventListener("click", (e) => { const m = $("#pantry-menu"); if (m && m.open && !m.contains(e.target)) m.open = false; });

function editProduct(p) {
  modal(`
    <h3>${esc(p.name)}</h3>
    ${p.bered ? `<div class="card job bnote">🛟 <b>Del av beredskapslageret</b> (${esc(p.bered.name)}): hold minst ${numTxt(p.bered.need)} ${esc(p.bered.unit)}, har ${numTxt(p.bered.have)}. Bruk den eldste først, så fornyes beredskapen hver gang dere handler. <a href="#" id="e-bered">Se beredskap ›</a></div>` : ""}
    ${p.health && !p.health.cleared && p.health.tags.length ? `<div class="card job hnote">
      <div><b>⚠️ ${esc(p.health.labels.join(" · "))}</b>${p.health.manual ? " <small>(merket av dere)</small>" : ""}</div>
      <div>${esc(p.health.why)}</div>
      <div class="halt">💡 <b>Bedre alternativ:</b> ${esc(p.health.alt)}</div>
      <div class="hacts"><button class="small ghost" data-upf="0">Ikke ultraprosessert</button>${p.health.manual ? `<button class="small ghost" data-upf="auto">Automatisk</button>` : ""}</div>
    </div>` : `<div class="hacts" style="margin:-4px 0 10px">${p.health && p.health.cleared ? `<small class="hint">Merket som ikke ultraprosessert.</small> <button class="small ghost" data-upf="auto">Automatisk</button>` : `<button class="small ghost" data-upf="1">Merk som ultraprosessert</button>`}</div>`}
    ${p.learned && Math.abs(p.learned - 1) >= 0.1 ? `<p class="hint learned">🧠 Lært av ${p.learn_n} rettelse${p.learn_n === 1 ? "" : "r"}: varer ca. ${Math.round(Math.abs(p.learned - 1) * 100)} % ${p.learned < 1 ? "kortere" : "lenger"} enn kjøpsrytmen tilsier.</p>` : ""}
    ${p.qty_boost ? `<p class="hint learned">🛒 Dere kjøpte mer enn vanlig sist – regnes å vare ca. ${String(p.qty_boost).replace(".", ",")} ganger så lenge.</p>` : ""}
    <div class="stats">
      <div><b>${p.q90}×</b><span>per kvartal</span></div>
      <div><b>${p.interval ? "hver " + Math.round(p.interval) + ". dag" : "–"}</b><span>rytme</span></div>
      <div><b>${p.prices?.avg90 ? krU(p.prices.avg90, p.prices.unit) : p.avg_price != null ? kr2(p.avg_price) : "–"}</b><span>snitt 3 mnd</span></div>
      <div><b>${kr(p.prices?.spent90) || "–"}</b><span>brukt 3 mnd</span></div>
    </div>
    ${p.prices && p.prices.last ? `<div class="pricebox">
      ${sparkline(p.prices.history)}
      <div class="info">Sist: <b>${krU(p.prices.last, p.prices.unit)}</b>${p.prices.last_size ? ` (${esc(p.prices.last_size)})` : ""}${p.prices.last_store ? " · " + esc(p.prices.last_store) : ""}${p.prices.trend != null && Math.abs(p.prices.trend) >= 5 ? ` · ${p.prices.trend > 0 ? "▲" : "▼"} ${Math.abs(p.prices.trend)} % mot snittet` : ""}</div>
      ${["min", "max"].map((k) => { const x = p.prices[k]; return x ? `<div class="info">${k === "min" ? "Billigst" : "Dyrest"}: <b>${krU(x.price, p.prices.unit)}</b>${x.size && p.prices.unit !== "pk" ? ` · ${esc(x.size)} for ${kr2(x.pack)}` : ""} · ${esc(x.store || "")} · ${esc(fmtDate(x.date))}</div>` : ""; }).join("")}
      <div class="info">${p.prices.unit === "pk" ? "Priser per pakning" : `Sammenlignet per ${p.prices.unit === "l" ? "liter" : p.prices.unit}, regnet ut fra pakningsstørrelsen`} – fra kvitteringene.${(p.prices.sizes || []).length > 1 ? ` Størrelser dere har kjøpt: ${esc(p.prices.sizes.join(", "))}.` : ""}</div>
    </div>` : ""}
    <label class="field">Navn<small>Skriv navnet på en annen vare for å slå dem sammen.</small><input type="text" id="e-name" value="${esc(p.name)}"></label>
    <label class="field">Kategori<select id="e-cat">${AISLE.map((c) => `<option ${c === p.category ? "selected" : ""}>${c}</option>`).join("")}</select></label>
    <label class="field">Holder seg ca. (dager)<input type="number" id="e-shelf" value="${p.shelf_days}" min="1"></label>
    <label class="row" style="padding:14px 0 0"><input type="checkbox" id="e-ign" ${p.ignore ? "checked" : ""}><div class="main">Ikke foreslå på handlelista</div></label>
    <div class="modal-acts"><button class="ghost" id="m-cancel">Avbryt</button><button class="primary" id="m-save">Lagre</button></div>
  `, (m) => {
    $$("[data-upf]", m).forEach((b) => b.onclick = async () => {
      const v = b.dataset.upf === "auto" ? null : b.dataset.upf === "1";
      await api("products/" + p.id, { body: { upf: v } });
      closeModal(); await loadPantry();
      const np = state.pantry.find((x) => x.id === p.id); if (np) editProduct(np);
    });
    const eb = $("#e-bered", m); if (eb) eb.onclick = (ev) => { ev.preventDefault(); closeModal(); showTab("beredskap"); };
    $("#m-cancel", m).onclick = closeModal;
    $("#m-save", m).onclick = async () => {
      await api("products/" + p.id, { body: { name: $("#e-name", m).value, category: $("#e-cat", m).value, shelf_days: +$("#e-shelf", m).value, ignore: $("#e-ign", m).checked } });
      closeModal();
      loadPantry();
    };
  });
}

// ================================================================ MER (bruksanvisning + innstillinger)
state.moreView = pref("more", "guide");

function loadMore() {
  $$("#more-seg button").forEach((b) => b.classList.toggle("on", b.dataset.m === state.moreView));
  $("#guide").hidden = state.moreView !== "guide";
  $("#settings").hidden = state.moreView !== "settings";
  $("#storealert").hidden = state.moreView !== "settings";
  if (state.moreView === "guide") renderGuide(); else { loadSettings(); loadStoreAlert(); }
}
$("#more-seg").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-m]");
  if (!b) return;
  state.moreView = b.dataset.m;
  setPref("more", b.dataset.m);
  loadMore();
});

function renderGuide() {
  const mail = (state.me && state.me.settings && state.me.settings.receipt_email) || "";
  const mailTxt = mail ? `<b>${esc(mail)}</b>` : "kvitteringsadressen (legges inn under Innstillinger)";
  const sec = (title, body, open) => `<details class="card guide" ${open ? "open" : ""}><summary>${title}</summary>${body}</details>`;
  $("#guide").innerHTML = [
    sec("Kort fortalt", `
      <p>Matplan leser kvitteringene våre, ser hva vi pleier å kjøpe og spise, og hjelper med <b>middag</b>, <b>handleliste</b> og <b>oversikt over hva vi har</b>.</p>
      <ul>
        <li>🍲 <b>Middag</b> – forslag ut fra kalender, det vi har og det vi liker</li>
        <li>🛒 <b>Handle</b> – det som snart går tom, rett inn i handlelista</li>
        <li>🧾 <b>Kvitteringer</b> – bilde, PDF eller e-post</li>
        <li>🥫 <b>Lager</b> – hva vi trolig har hjemme</li>
      </ul>`, true),
    sec("🍲 Middag", `
      <ol>
        <li>Velg <b>I dag / 3 dager / Uke</b> og trykk <b>Lag middagsforslag</b>. Skriv gjerne et ønske først.</li>
        <li><b>Trykk på et forslag</b> for oppskrift, hva vi har og hva som mangler.</li>
        <li><b>📅 Velg</b> legger middagen med oppskrift inn i Middag-kalenderen.</li>
        <li>Liker du ikke et forslag: <b>🔄 Nytt forslag</b>. Lyst på noe annet: <b>✏️ Egen middag</b>.</li>
        <li><b>🛒 Kjøp det som mangler</b> legger ingrediensene på handlelista.</li>
      </ol>
      <ul>
        <li>Hverdag = enkel og rask. Fredag/lørdag = kos. Søndag = søndagsmiddag.</li>
        <li>Fisk minst 2 ganger i uka, grønt hver dag. 🐟 🥩 🐔 🥦 viser fordelingen.</li>
        <li>Er det travelt (trening, sene dager), foreslås noe raskt.</li>
        <li>👍/👎 på middager vi har hatt gjør forslagene bedre.</li>
        <li>Middager som allerede står i kalenderen, blir stående.</li>
      </ul>`),
    sec("🛒 Handle", `
      <ul>
        <li>Viser varer som etter vanlig rytme <b>snart går tom</b>. Kryss av og trykk <b>Legg på handlelista</b>.</li>
        <li>Varer som <b>allerede står på handlelista</b>, vises ikke som forslag. Fjernes de fra lista (eller lista tømmes), dukker de opp igjen. Avkryssede varer på lista regnes som kjøpt.</li>
        <li>Varene havner i handlelista i Home Assistant, sortert i <b>butikkrekkefølge</b>.</li>
        <li><b>Mest akutt</b> viser det som haster først.</li>
        <li>Skriv egne varer i feltet – appen foreslår mens du skriver (grå tekst). Komma = neste vare.</li>
        <li><b>🛒 Legg i Oda-handlekurven</b> sender valgte varer og handlelista til Oda. Appen velger det vi pleier å kjøpe; «Sjekk» = sjekk varen, «Bytt» for å velge en annen.</li>
        <li>🔒 Matplan <b>bestiller aldri</b> – den legger bare i kurven. Levering og bestilling gjør du selv i Oda-appen.</li>
        <li><b>Priser:</b> «Oda nå» er dagens Oda-pris (🏷 = tilbud, med vanlig pris). «Dere betaler vanligvis» er snittet fra kvitteringene.</li>
        <li><b>⚖️ Sammenlign priser</b> viser hva handlelista koster i Kiwi, Rema, Coop, Meny osv. (via Kassalapp). Billigst er markert.</li>
        <li><b>✕ på handlelista</b> fjerner varen helt (ikke bare huker av), så den ikke havner i Oda. <b>Tøm lista</b> fjerner alt.</li>
        <li><b>✕ på et forslag:</b> «Ikke nå» skjuler det til neste gang, «Aldri foreslå» skjuler det for godt.</li>
        <li>I Oda-vinduet: <b>🗑 Fjern fra lista</b> eller <b>Hopp over</b> for ting du ikke vil ha i kurven.</li>
      </ul>`),
    sec("🧾 Kvitteringer", `
      <ul>
        <li><b>📷 Ta bilde</b> av papirkvittering, eller <b>last opp</b> skjermbilde/PDF fra Trumf- eller Coop-appen.</li>
        <li><b>Oda</b> kommer automatisk på e-post.</li>
        <li>Andre kvitteringer kan <b>videresendes</b> til ${mailTxt}.</li>
        <li><b>Søk</b> f.eks. «melk» for å se alle kvitteringer med melk og hva det har kostet.</li>
        <li>Øverst står <b>snitt per måned</b> (regnet av hele måneder) og hva vi har brukt denne måneden. ▲/▼ ved hver måned viser om den var dyrere eller billigere enn snittet.</li>
      </ul>`),
    sec("📊 Økonomi", `
      <ul>
        <li>Åpnes med <b>Økonomi</b> øverst i Kvitteringer (på PC i menyen).</li>
        <li><b>Snitt</b> per måned, uke og handel. Måneden som pågår og måneder der det trolig mangler kvitteringer, regnes ikke med.</li>
        <li><b>Budsjett:</b> sett et beløp per måned, så ser dere hva som er igjen og hva måneden ligger an til.</li>
        <li><b>Mat og annet:</b> husholdning og hygiene regnes som «annet».</li>
        <li><b>Per butikk</b>, <b>hva pengene går til</b>, <b>mest penger på</b> og varer som har <b>blitt dyrere</b>.</li>
        <li><b>💡 Mulig sparing:</b> varer der vi har betalt mye mindre per kg/l/stk før (annen butikk eller pakningsstørrelse).</li>
        <li>Tallet finnes også i Home Assistant som sensoren «Dagligvarer denne måneden», og kan legges på dashbordet.</li>
        <li>Tallene blir bare så gode som kvitteringene – husk å ta bilde av Kiwi- og Coop-kvitteringene.</li>
      </ul>`),
    sec("🥫 Lager", `
      <ul>
        <li>Et <b>anslag</b> over hva vi har, ut fra kvitteringer og holdbarhet.</li>
        <li>Øverst står det som haster: <b>Bruk snart</b> (trykk <b>Brukt</b> når det er spist) og faste varer som <b>går tom snart</b> (<b>Har mer</b> eller <b>Tomt</b>).</li>
        <li>Under ligger <b>Har hjemme</b>, <b>Tomt</b>, <b>Av og til</b> og <b>Kjøpt én gang</b> – trykk på overskriften for å åpne eller lukke.</li>
        <li><b>☰-menyen</b> bytter visning: <b>Sted i huset</b> (kjøleskap, fryser, skap …, med en stripe som viser omtrent hvor mye som er igjen) eller <b>Liste</b> med alle varer – velg sortering over lista, og snu rekkefølgen med pila (tabell på PC).</li>
        <li><b>+ Legg til varer</b> for ting uten kvittering. Kategori settes automatisk.</li>
        <li>Trykk på en vare for priser (snitt per kg/l/stk, billigst/dyrest, prisgraf), rytme og for å endre navn, kategori eller holdbarhet.</li>
      </ul>`),
    sec("🥦 Ultraprosessert mat", `
      <ul>
        <li>Varer som trolig er <b>ultraprosessert</b> (pølser, chips, brus, kjeks, ferdigpizza, søte frokostblandinger, smaksatt yoghurt o.l.) får merket <b>UP</b> i Lager og på Handle. Bearbeidet kjøtt og mye sukker merkes også.</li>
        <li>Trykk på varen for å se hvorfor og et <b>bedre alternativ</b> etter kostrådene – f.eks. vann i stedet for brus, havregryn i stedet for søt frokostblanding.</li>
        <li>Er merket feil, trykk <b>Ikke ultraprosessert</b> (eller <b>Merk som ultraprosessert</b>).</li>
        <li><b>Økonomi → Helse</b> viser hvor stor del av matpengene som går til ultraprosessert mat.</li>
        <li>Vurderingen er ut fra varenavnet, uten AI. Den bygger på NOVA-systemet; der kostrådene sier noe annet (f.eks. myk margarin og fiskeboller), følger appen kostrådene.</li>
      </ul>`),
    sec("🛟 Beredskap", `
      <ul>
        <li>Åpnes med <b>Beredskap</b> øverst i Lager (på PC i menyen).</li>
        <li>Lista følger <b>DSBs råd</b>: klare oss selv i <b>7 døgn</b> uten strøm, vann og butikker, med 20 liter vann per person. Mengdene er regnet ut for husstanden (trykk <b>Endre</b> for antall voksne, barn, barn med bleie og døgn).</li>
        <li>Trykk <b>+ / −</b> eller <b>Har</b> etter hvert som dere skaffer ting. Prikken blir grønn når det er nok.</li>
        <li>Trykk på en ting for å <b>endre mengden</b> dere vil ha, legge inn <b>best før-dato</b> eller merke den som <b>ikke aktuelt</b> (f.eks. ved hvis dere ikke har peis). Matplan varsler to måneder før noe går ut.</li>
        <li><b>Mat</b> viser omtrent hvor mange døgn maten rekker, regnet i kalorier.</li>
        <li><b>🛒 Legg det som mangler på handlelista</b> – varene merkes «(beredskap)».</li>
        <li>«tips» er ting Matplan foreslår i tillegg til DSBs liste. Dere kan også legge til egne ting nederst.</li>
        <li><b>🛟 Beredskapsmiddag annenhver uke:</b> middagsplanen foreslår da én middag som bruker det eldste fra beredskapen (det som går ut først). Trykker dere «Vi lagde denne», trekkes det brukte fra beredskapen og legges på handlelista – så roterer lageret av seg selv.</li>
        <li><b>🔄 Rullerende lager:</b> havregryn, pasta, ris, hermetikk, knekkebrød, dopapir, bleier og annet dere bruker i hverdagen, kobles automatisk til varene i lageret (🛟 i Lager). Bruk det eldste først – da fornyes beredskapen hver gang dere handler, og ingenting går ut på dato.</li>
        <li>Har dere kjøpt slike varer siden sist dere telte, kan de legges til med <b>+ fra kvitteringer</b>. Går en vare tom i hverdagen, minner handlelista om at den også er beredskap, og mangler det i beredskapen, foreslås den under «Går trolig tom snart» (ikke forhåndsvalgt).</li>
      </ul>`),
    sec("📅 Middag-kalenderen", `
      <ul>
        <li>Matplan bruker kalenderen <b>Middag</b> i Google.</li>
        <li>Det vi har spist før, brukes til «mest spist» og som grunnlag for forslag.</li>
        <li>Legger vi inn middag direkte i Google Kalender, ser appen det.</li>
        <li>Vil du bytte en middag som er valgt: endre eller slett den i Google Kalender.</li>
      </ul>`),
    sec("💡 Tips", `
      <ul>
        <li>Jo flere kvitteringer, jo bedre forslag.</li>
        <li>Trykk <b>Tomt</b> eller <b>Har</b> når anslaget er feil – appen lærer av det og trenger færre rettelser etter hvert.</li>
        <li><b>👥 Gjester</b> på Middag: velg antall voksne og barn og hvilke dager, så blir forslag og handleliste tilpasset.</li>
        <li>Oppskrifter huskes. Redigerer du en, brukes din versjon neste gang.</li>
        <li><b>Butikkvarsel</b> (Mer → Innstillinger): varsel på telefonen når du er i en av butikkene dine og det står noe på handlelista. Trykk på varselet for å åpne lista.</li>
        <li>Appen ligger i sidemenyen i Home Assistant, også i mobilappen. På PC får du bredere oversikt med tabell og flere kolonner.</li>
      </ul>`),
  ].join("");
}

async function loadSettings() {
  const el = $("#settings");
  el.innerHTML = `<span class="spinner"></span>Henter …`;
  let s, ents;
  try { [s, ents] = await Promise.all([api("settings"), api("entities")]); }
  catch (e) { el.innerHTML = `<div class="job err">${esc(e.message)}</div>`; return; }
  const opt = (list, val, emptyLabel) =>
    (emptyLabel !== undefined ? `<option value="">${esc(emptyLabel)}</option>` : "") +
    list.map((x) => `<option value="${esc(x.entity_id)}" ${x.entity_id === val ? "selected" : ""}>${esc(x.name)} (${esc(x.entity_id)})</option>`).join("");
  const cals = s.calendars || [];
  el.innerHTML = `
    <label class="field">AI-modell<small>AI-oppgave-entiteten i Home Assistant (f.eks. Claude). «Standard» bruker den som er valgt i HA.</small>
      <select id="s-ai">${opt(ents.ai_task, s.ai_entity, "Standard")}</select></label>
    <label class="field">Handleliste<small>To-do-listen varer skal legges i.</small>
      <select id="s-todo">${opt(ents.todo, s.todo_entity, "– velg –")}</select></label>
    <label class="field">Middagskalender<small>Tidligere middager hentes herfra, og valgte middager skrives inn her.</small>
      <select id="s-dcal">${opt(ents.calendar, s.dinner_calendar, "Automatisk (kalenderen som heter «Middag»)")}</select></label>
    <label class="row" style="padding:10px 0 0"><input type="checkbox" id="s-wcal" ${s.write_calendar ? "checked" : ""}><div class="main">Skriv valgte middager inn i middagskalenderen</div></label>
    <label class="field">Kalendere for middagsplanlegging<small>Brukes til å se hvem som er hjemme og hvor god tid dere har. Ingen valgt = alle.</small></label>
    <details class="dropdown" id="s-cal-dd">
      <summary id="s-cal-sum"></summary>
      <div class="dd-body">${ents.calendar.map((c) => `<label class="row"><input type="checkbox" class="s-cal" value="${esc(c.entity_id)}" data-name="${esc(c.name)}" ${cals.includes(c.entity_id) ? "checked" : ""}><div class="main">${esc(c.name)}</div></label>`).join("")}</div>
    </details>
    <label class="field">Husstanden<small>Hvem er dere, og hvilken kalender hører til hvem? Hjelper AI-en å forstå hvem som er hjemme.</small>
      <textarea id="s-house" placeholder="F.eks.: Kari og Ola (voksne), Emma og Jonas (barn). Kalenderen «Kari og Ola» er felles.">${esc(s.household)}</textarea></label>
    <label class="field">Preferanser og allergier<small>F.eks. «fisk to ganger i uka», «ingen nøtter», «barna liker ikke sterk mat».</small>
      <textarea id="s-pref">${esc(s.preferences)}</textarea></label>
    <label class="field">Vanlig middagstid<input type="time" id="s-time" value="${esc(s.dinner_time || "17:00")}"></label>
    <label class="field">Adresse for videresending av kvitteringer<small>Vises i bruksanvisningen, f.eks. din Gmail med «+matplan».</small>
      <input type="text" id="s-mail" value="${esc(s.receipt_email || "")}" placeholder="navn+matplan@gmail.com" autocomplete="off"></label>
    <label class="field">Dager til neste handel<small>Varer som trolig går tom innen så mange dager foreslås på handlelista.</small>
      <input type="number" id="s-hor" min="0" max="14" value="${esc(s.shop_horizon ?? 2)}"></label>
    <button class="primary wide" id="s-save">Lagre</button>
  `;
  const calSum = () => {
    const sel = $$(".s-cal:checked").map((c) => c.dataset.name);
    $("#s-cal-sum").textContent = sel.length ? `${sel.length} valgt: ${sel.join(", ")}` : "Alle kalendere";
  };
  calSum();
  $("#s-cal-dd").addEventListener("change", calSum);
  $("#s-save").onclick = async () => {
    try {
      await api("settings", { body: {
        receipt_email: $("#s-mail").value.trim(),
        ai_entity: $("#s-ai").value, todo_entity: $("#s-todo").value,
        calendars: $$(".s-cal:checked").map((c) => c.value),
        household: $("#s-house").value, preferences: $("#s-pref").value,
        dinner_time: $("#s-time").value, shop_horizon: +$("#s-hor").value,
        dinner_calendar: $("#s-dcal").value, write_calendar: $("#s-wcal").checked,
      } });
      state.writeCal = $("#s-wcal").checked;
      state.me = await api("state");
      toast("Lagret");
    } catch (e) { toast(e.message); }
  };
}

// ---------------------------------------------------------------- Butikkvarsel
async function loadStoreAlert() {
  const el = $("#storealert");
  el.innerHTML = `<div class="card"><span class="spinner"></span>Henter butikkvarsel …</div>`;
  let d;
  try { d = await api("storealert"); } catch (e) { el.innerHTML = `<div class="card job err">${esc(e.message)}</div>`; return; }
  const s = d.settings;
  const todo = d.todo_entity || "todo.handleliste";
  const st = d.status;
  const card = `type: todo-list\nentity: ${todo}\ntitle: Handleliste`;
  el.innerHTML = `
    <div class="card form">
      <h3 style="margin:0">🛒 Butikkvarsel</h3>
      <p class="hint">Varsler på telefonen når noen er i en av butikkene og det står noe på handlelista. Trykk på varselet for å åpne handlelista. Bruker posisjonen fra Home Assistant-appen.</p>
      <div class="sa-status ${st.installed ? (st.on ? "on" : "off") : ""}">
        ${st.installed ? (st.on ? "✅ Aktiv – varsler sendes" : "⏸ Opprettet, men avslått – ingen varsler sendes") : "Ikke opprettet ennå"}
      </div>

      <label class="field">Butikker<small>Varsel når man har vært innenfor radiusen i ${esc(s.minutes)} min (så det ikke varsler når man kjører forbi).</small></label>
      <div class="list" id="sa-stores">${(s.stores || []).map((x, i) => `
        <div class="row sa-store" data-i="${i}">
          <div class="main"><input type="text" class="sa-name" value="${esc(x.name)}">
            <div class="info">${esc(Number(x.lat).toFixed(5))}, ${esc(Number(x.lon).toFixed(5))}</div></div>
          <label class="sa-rad"><input type="number" class="sa-radius" value="${esc(x.radius)}" min="50" max="1000" step="10"> m</label>
          <button type="button" class="x" data-sa-del="${i}" aria-label="Fjern butikk">✕</button>
        </div>`).join("")}</div>
      <details class="dropdown"><summary>+ Legg til butikk</summary><div class="dd-body" style="padding:10px 12px">
        <input type="text" id="sa-new-name" placeholder="Navn, f.eks. Kiwi Sentrum">
        <input type="text" id="sa-new-pos" placeholder="Posisjon: 59.91273, 10.74609 (fra Google Maps: høyreklikk → koordinater)">
        <button class="small wide" id="sa-add">Legg til</button>
      </div></details>

      <label class="field">Hvem får varsel<small>Velg telefonen (HA-appen) til hver person.</small></label>
      <div class="list">${d.persons.length ? d.persons.map((p) => {
        const cur = (s.people || []).find((x) => x.person === p.entity_id) || {};
        return `<div class="row"><div class="main"><div class="name">${esc(p.name)}</div><div class="info">${esc(p.entity_id)}</div></div>
          <select class="sa-notify" data-person="${esc(p.entity_id)}"><option value="">Ikke varsle</option>${d.notifies.map((n) => `<option value="${esc(n)}" ${n === cur.notify ? "selected" : ""}>📱 ${esc(n.replace("notify.mobile_app_", "").replace(/_/g, " "))}</option>`).join("")}</select></div>`;
      }).join("") : `<div class="empty">Fant ingen personer i Home Assistant.</div>`}</div>
      <label class="row" style="padding:10px 0 0"><input type="checkbox" id="sa-all" ${s.notify_all ? "checked" : ""}><div class="main"><div class="name">Varsle begge</div><div class="info">Ellers får bare den som er i butikken varselet.</div></div></label>
      <div class="two">
        <label class="field">Fra kl.<input type="time" id="sa-from" value="${esc(s.from)}"></label>
        <label class="field">Til kl.<input type="time" id="sa-to" value="${esc(s.to)}"></label>
      </div>
      <label class="field">Trykk på varselet åpner
        <select id="sa-link"><option value="todo" ${s.link !== "matplan" ? "selected" : ""}>Handlelista i Home Assistant</option><option value="matplan" ${s.link === "matplan" ? "selected" : ""}>Matplan</option></select></label>

      <button class="primary wide" id="sa-install">${st.installed ? "Lagre og oppdater automasjonen" : "Opprett automasjonen (avslått)"}</button>
      ${st.installed ? `<button class="${st.on ? "ghost danger" : "oda"} wide" id="sa-toggle">${st.on ? "Slå av butikkvarsel" : "Slå på butikkvarsel"}</button>` : ""}
      <p class="hint">Automasjonen heter «Matplan – butikkvarsel» i Home Assistant. Den opprettes avslått, så ingen får varsel før du slår den på her.</p>
    </div>
    <div class="card form">
      <h3 style="margin:0">📋 Handlelista på dashbordet</h3>
      <p class="hint">Rediger dashbordet i Home Assistant → <b>+ Legg til kort</b> → <b>Manuell</b> (helt nederst), og lim inn:</p>
      <pre class="code" id="sa-card">${esc(card)}</pre>
      <button class="small" id="sa-copy">Kopier</button>
      <p class="hint">Handlelista finnes også alltid under <b>Gjøremål</b> i sidemenyen i Home Assistant.</p>
    </div>`;

  const collect = () => {
    const stores = $$(".sa-store", el).map((r) => {
      const x = s.stores[+r.dataset.i];
      return { ...x, name: $(".sa-name", r).value.trim() || x.name, radius: +$(".sa-radius", r).value || x.radius };
    });
    return {
      stores,
      people: $$(".sa-notify", el).map((x) => ({ person: x.dataset.person, notify: x.value })).filter((x) => x.notify),
      notify_all: $("#sa-all", el).checked, from: $("#sa-from", el).value, to: $("#sa-to", el).value, link: $("#sa-link", el).value,
    };
  };
  el.onclick = async (e) => {
    const del = e.target.closest("[data-sa-del]");
    if (del) { s.stores = collect().stores.filter((_, i) => i !== +del.dataset.saDel); await api("storealert", { body: { stores: s.stores } }); return loadStoreAlert(); }
    if (e.target.closest("#sa-add")) {
      const m = $("#sa-new-pos", el).value.match(/(-?\d+[.,]\d+)\s*[,; ]\s*(-?\d+[.,]\d+)/);
      const name = $("#sa-new-name", el).value.trim();
      if (!m || !name) return toast("Skriv navn og posisjon (to tall med komma mellom)");
      s.stores = [...collect().stores, { name, lat: +m[1].replace(",", "."), lon: +m[2].replace(",", "."), radius: 120 }];
      await api("storealert", { body: { stores: s.stores } });
      return loadStoreAlert();
    }
    if (e.target.closest("#sa-copy")) {
      try { await navigator.clipboard.writeText(card); toast("Kopiert"); } catch (_) { toast("Marker teksten og kopier manuelt"); }
      return;
    }
    if (e.target.closest("#sa-install")) {
      const b = e.target.closest("#sa-install"); b.disabled = true;
      try {
        await api("storealert", { body: collect() });
        const r = await api("storealert/install", { body: {} });
        toast(r.on ? "Automasjonen er oppdatert (aktiv)" : "Automasjonen er opprettet – avslått, ingen varsler sendes", 4000);
        loadStoreAlert();
      } catch (err) { toast(err.message, 5000); b.disabled = false; }
      return;
    }
    if (e.target.closest("#sa-toggle")) {
      try { const r = await api("storealert/toggle", { body: { on: !st.on } }); toast(r.on ? "Butikkvarsel er slått på" : "Butikkvarsel er slått av"); loadStoreAlert(); }
      catch (err) { toast(err.message); }
    }
  };
}

// ================================================================ ØKONOMI
async function loadEconomy() {
  const el = $("#eco");
  if (!state.eco) el.innerHTML = `<div class="card empty">Regner …</div>`;
  try { state.eco = await api("economy"); renderEconomy(); } catch (e) { toast(e.message); }
}

const MONTHS_LONG = (ym) => MONTHS[+ym.slice(5, 7) - 1].replace(/^./, (x) => x.toUpperCase());
const uLabel = (u) => ({ kg: "kg", l: "liter", stk: "stk" }[normUnit(u)] || "pakning");

function ecoChart(rows, avg) {
  const W = isWide() ? 480 : 360, H = isWide() ? 200 : 190, top = 22, bottom = 26, left = 4, right = 4;
  const max = Math.max(avg || 0, ...rows.map((r) => r.total), 1) * 1.08;
  const bw = (W - left - right) / rows.length;
  const y = (v) => top + (H - top - bottom) * (1 - v / max);
  const bars = rows.map((r, i) => {
    const x = left + i * bw + bw * 0.16, w = bw * 0.68;
    const yf = y(r.food), yt = y(r.total), base = y(0);
    const cls = r.current ? "cur" : r.incomplete ? "inc" : "";
    const lbl = r.total >= 1000 ? (r.total / 1000).toLocaleString("nb-NO", { maximumFractionDigits: 1 }) + "k" : r.total;
    return `<g class="${cls}"><title>${MONTHS_LONG(r.month)} ${r.year}: ${kr(r.total)} (mat ${kr(r.food)}, annet ${kr(r.other)}) · ${r.count} kvitteringer${r.current ? " · pågår" : r.incomplete ? " · trolig ufullstendig" : ""}</title>
      <rect class="food" x="${x}" y="${yf}" width="${w}" height="${Math.max(0, base - yf)}" rx="2"/>
      <rect class="other" x="${x}" y="${yt}" width="${w}" height="${Math.max(0, yf - yt)}" rx="2"/>
      <text class="v" x="${x + w / 2}" y="${yt - 5}">${r.total ? lbl : ""}${r.incomplete && !r.current ? "?" : ""}</text>
      <text class="m" x="${x + w / 2}" y="${H - 8}">${r.label}</text></g>`;
  }).join("");
  const avgLine = avg ? `<line class="avg" x1="${left}" x2="${W - right}" y1="${y(avg)}" y2="${y(avg)}"/>` : "";
  return `<svg class="ecochart" viewBox="0 0 ${W} ${H}" role="img" aria-label="Forbruk per måned">${bars}${avgLine}</svg>`;
}

const shareRow = (label, right, pct, sub = "") => `
  <div class="srow"><div class="srow-t"><span>${label}</span><span>${right}</span></div>
  <div class="sbar"><i style="width:${Math.max(2, Math.min(100, pct))}%"></i></div>${sub ? `<div class="srow-s">${sub}</div>` : ""}</div>`;

function renderEconomy() {
  const e = state.eco, el = $("#eco");
  if (!e || e.empty) {
    el.innerHTML = `<div class="card empty">Ingen kvitteringer ennå. Når dere har lagt inn kvitteringer, ser dere her hva dere bruker på mat og dagligvarer.</div>`;
    return;
  }
  const c = e.current, mn = MONTHS_LONG(c.month);
  const metric = (label, val, sub) => `<div class="metric"><span>${label}</span><b>${val}</b><small>${sub}</small></div>`;
  const noAvg = !e.avg_month;

  // budsjett
  let budget;
  if (e.budget) {
    const used = c.so_far / e.budget, dayPct = c.day / c.days * 100;
    const proj = c.projection, over = proj && proj > e.budget;
    budget = `<div class="card ecocard">
      <div class="eco-h"><h3>Budsjett for ${mn.toLowerCase()}</h3><button class="linkish" id="eco-bedit">Endre</button></div>
      <div class="budget"><i class="${used > 1 ? "over" : over ? "warn" : ""}" style="width:${Math.min(100, used * 100)}%"></i><span class="today" style="left:${dayPct}%" title="I dag"></span></div>
      <div class="budget-t"><span><b>${kr(c.so_far)}</b> av ${kr(e.budget)}</span><span class="${e.budget_left < 0 ? "bad" : ""}">${e.budget_left >= 0 ? kr(e.budget_left) + " igjen" : kr(-e.budget_left) + " over"}</span></div>
      <p class="hint">${proj ? `Ligger an til ca. <b>${kr(proj)}</b> – ${over ? `${kr(proj - e.budget)} over budsjett` : `${kr(e.budget - proj)} under budsjett`}. ` : ""}Streken viser hvor langt ut i måneden vi er.</p>
    </div>`;
  } else {
    budget = `<div class="card ecocard"><div class="eco-h"><h3>Månedsbudsjett</h3></div>
      <p class="hint" style="margin-top:0">Sett et budsjett for dagligvarer, så ser dere hvor mye som er igjen denne måneden – her og på dashbordet.${e.avg_month ? ` Snittet deres er ${kr(e.avg_month)}.` : ""}</p>
      <button class="ghost wide" id="eco-bedit">Sett budsjett</button></div>`;
  }

  el.innerHTML = `
    <div class="metrics">
      ${metric("Snitt per måned", noAvg ? "–" : kr(e.avg_month), noAvg ? "trenger én hel måned" : `mat ${kr(e.avg_food)} · annet ${kr(e.avg_other)}`)}
      ${metric(`${mn} så langt`, kr(c.so_far), c.projection ? `ligger an til ca. ${kr(c.projection)}` : `${c.count} kvitteringer`)}
      ${metric("Per uke", noAvg ? "–" : kr(e.avg_week), "snitt")}
      ${metric("Per handel", e.per_trip ? kr(e.per_trip) : "–", e.trips_month ? `ca. ${e.trips_month} handler i måneden` : "")}
    </div>
    <div class="ecogrid">
        ${budget}
        <div class="card ecocard">
          <div class="eco-h"><h3>Per måned</h3></div>
          ${ecoChart(e.months, e.avg_month)}
          <div class="legend"><span><i class="food"></i>Mat</span><span><i class="other"></i>Annet</span>${e.avg_month ? `<span><i class="avg"></i>Snitt</span>` : ""}</div>
          <p class="hint">Snittet er regnet av ${e.basis_months ? `de siste ${e.basis_months} hele månedene` : "hele måneder"}. Måneden som pågår (blek) og måneder med uvanlig få kvitteringer (?) er holdt utenfor. «Annet» er husholdning og hygiene.</p>
        </div>
        <div class="card ecocard">
          <div class="eco-h"><h3>Per butikk</h3><small>snitt per måned</small></div>
          ${e.stores.map((s) => shareRow(esc(s.store), `${kr(s.per_month)} · ${s.share} %`, s.share,
            `ca. ${String(s.trips_per_month).replace(".", ",")} handler i måneden${s.per_trip ? ` · ${kr(s.per_trip)} per handel` : ""}`)).join("")}
        </div>
        ${e.savings.length ? `<div class="card ecocard">
          <div class="eco-h"><h3>💡 Mulig sparing</h3></div>
          ${e.savings.map((t) => `<div class="tip"><b>${esc(t.name)}</b>: ${krU(t.best, t.unit)} da dere kjøpte ${t.size && t.unit !== "pk" ? esc(t.size) + " " : ""}på ${esc(t.store || "?")} – ${t.pct} % under snittet (${krU(t.avg, t.unit)}).
            <span>Kan spare ca. ${kr(t.per_month)} i måneden.</span></div>`).join("")}
          <p class="hint">Sammenlignet per ${"kg, liter eller stk"} når pakningsstørrelsen står på kvitteringen. Tilbud kan forklare noen av de lave prisene.</p>
        </div>` : ""}
        ${e.health && e.health.food_spent ? `<div class="card ecocard">
          <div class="eco-h"><h3>🥦 Helse</h3><small>siste 3 mnd</small></div>
          ${shareRow("Ultraprosessert mat", `${kr(e.health.upf_spent)} · ${e.health.upf_share} %`, e.health.upf_share, "av det dere har brukt på mat")}
          ${e.health.meat_spent ? `<div class="srow-s" style="margin-bottom:6px">Bearbeidet kjøtt: ${kr(e.health.meat_spent)}</div>` : ""}
          ${e.health.top.length ? `<div class="toplist">${e.health.top.map((t) => `<div class="htop"><span><b>${esc(t.name)}</b> <small>${esc(t.labels.join(", "))}</small><br><span class="halt">💡 ${esc(t.alt)}</span></span><span>${kr(t.spent)}</span></div>`).join("")}</div>` : `<p class="hint">Ingen ultraprosesserte varer funnet 👏</p>`}
          <p class="hint">Merket etter NOVA-systemet. Alternativene følger de norske kostrådene og WHO: mer grønnsaker, frukt, belgfrukter, fisk og grove kornvarer, mindre bearbeidet kjøtt, sukker og salt, og vann som tørstedrikk.</p>
        </div>` : ""}
        <div class="card ecocard">
          <div class="eco-h"><h3>Hva pengene går til</h3><small>siste 3 mnd</small></div>
          ${e.categories.slice(0, 10).map((x) => shareRow(esc(x.category), `${kr(x.sum)} · ${x.share} %`, x.share)).join("")}
        </div>
        <div class="card ecocard">
          <div class="eco-h"><h3>Mest penger på</h3><small>siste 3 mnd</small></div>
          <div class="toplist">${e.top.map((t, i) => `<div><span>${i + 1}. ${esc(t.name)}${t.trend != null && Math.abs(t.trend) >= 5 ? ` <em class="${t.trend > 0 ? "up" : "down"}">${t.trend > 0 ? "▲" : "▼"}${Math.abs(t.trend)} %</em>` : ""}</span><span>${kr(t.spent)}</span></div>`).join("")}</div>
        </div>
        ${e.pricier.length ? `<div class="card ecocard">
          <div class="eco-h"><h3>Blitt dyrere</h3><small>siste kjøp mot snitt 3 mnd</small></div>
          <div class="toplist">${e.pricier.map((t) => `<div><span>${esc(t.name)} <em class="up">▲${t.trend} %</em></span><span>${krU(t.last, t.unit)}</span></div>`).join("")}</div>
        </div>` : ""}
        <div class="card ecocard">
          <div class="eco-h"><h3>📱 På dashbordet</h3></div>
          <p class="hint" style="margin-top:0">Matplan holder sensoren <code>${e.sensor}</code> oppdatert i Home Assistant med hva som er brukt denne måneden. Snitt, anslag og budsjett ligger som egenskaper. Lim inn dette kortet i dashbordet (Rediger → + Legg til kort → Manuell):</p>
          <pre class="code" id="eco-yaml"></pre>
          <button class="ghost wide" id="eco-copy">Kopier kortet</button>
        </div>
    </div>`;

  const yaml = `type: entities
title: Dagligvarer
entities:
  - entity: ${e.sensor}
    name: Brukt denne måneden
  - type: attribute
    entity: ${e.sensor}
    attribute: anslag_hele_maneden
    name: Ligger an til
    suffix: kr
  - type: attribute
    entity: ${e.sensor}
    attribute: snitt_per_maned
    name: Snitt per måned
    suffix: kr${e.budget ? `
  - type: attribute
    entity: ${e.sensor}
    attribute: igjen_av_budsjett
    name: Igjen av budsjett
    suffix: kr` : ""}`;
  $("#eco-yaml").textContent = yaml;
  $("#eco-copy").onclick = async () => {
    try { await navigator.clipboard.writeText(yaml); toast("Kopiert"); } catch (_) { toast("Marker teksten og kopier manuelt"); }
  };
  $("#eco-bedit").onclick = () => modal(`
    <h3>Månedsbudsjett for dagligvarer</h3>
    <label class="field">Beløp per måned (kr)<small>${e.avg_month ? `Snittet deres er ${kr(e.avg_month)}. ` : ""}Sett 0 for å fjerne budsjettet.</small>
      <input type="number" inputmode="numeric" id="b-amt" min="0" step="100" value="${e.budget || (e.avg_month ? Math.round(e.avg_month / 100) * 100 : "")}"></label>
    <div class="modal-acts"><button class="ghost" id="m-cancel">Avbryt</button><button class="primary" id="m-save">Lagre</button></div>`, (m) => {
    $("#m-cancel", m).onclick = closeModal;
    $("#m-save", m).onclick = async () => {
      try { await api("economy/budget", { body: { budget: +$("#b-amt", m).value || 0 } }); closeModal(); loadEconomy(); }
      catch (err) { toast(err.message); }
    };
  });
}

// ================================================================ BEREDSKAP
state.beredFilter = pref("bfilter", "all");

async function loadBered() {
  const el = $("#bered");
  if (!state.bered) el.innerHTML = `<div class="card empty">Henter …</div>`;
  try { state.bered = await api("beredskap"); renderBered(); } catch (e) { toast(e.message); }
}

// helse: merke for ultraprosessert / bearbeidet kjøtt / mye sukker
const hBadge = (p) => {
  const h = p && p.health;
  if (!h || h.cleared || !h.tags.length) return "";
  const t = h.upf ? "UP" : h.tags.includes("meat") ? "Bearb. kjøtt" : "Sukker";
  return `<span class="hbadge ${h.upf ? "" : "soft"}" title="${esc(h.labels.join(", "))}">${t}</span>`;
};
const fmtDateY = (d) => d ? new Date(d + "T12:00").toLocaleDateString("nb-NO", { day: "numeric", month: "short", year: "numeric" }) : "";
const numTxt = (n) => String(Math.round(n * 10) / 10).replace(".", ",");
const bStep = (i) => i.unit === "liter" && i.need >= 20 ? 5 : (i.unit === "kg" || i.unit === "liter") ? 0.5 : 1;

function beredRow(i) {
  const dot = i.na ? "na" : i.status;
  const exp = i.expired ? `<span class="badge">utløpt</span>` : i.stale ? `<span class="badge">ikke kjøpt på lenge</span>` : i.expiring ? `<span class="badge">utløper snart</span>` : "";
  const sub = i.na ? (i.auto_na ? "Ingen barn med bleie – endres øverst" : "Ikke aktuelt") :
    i.kind === "check" ? (i.status === "ok" ? "På plass" : "Mangler") :
    `${i.need_custom ? "Vil ha" : "Trenger"} ${numTxt(i.need)} ${esc(i.unit)}${i.have ? ` · har ${numTxt(i.have)}` : ""}`;
  const link = !i.na && i.linked.length ? `<div class="info blink">🔄 I lageret: ${i.linked.slice(0, 2).map((p) => esc(p.name)).join(", ")}${i.linked.length > 2 ? ` +${i.linked.length - 2}` : ""}${i.last_buy ? ` · kjøpt ${esc(ago(i.linked.reduce((a, p) => Math.min(a, p.since), 9999)))}` : ""}</div>` : "";
  const add = i.bought_since > 0 && i.have < i.need ? `<button class="small ghost badd" data-badd="${i.key}">+${numTxt(i.bought_since)} fra kvitteringer</button>` : "";
  const ctrl = i.na ? "" : i.kind === "check"
    ? `<button class="bchk ${i.have ? "on" : ""}" data-bchk="${i.key}" aria-label="Har">${i.have ? "✓ Har" : "Har"}</button>`
    : `<div class="stepper"><button data-bstep="${i.key}" data-d="-1" aria-label="Færre">−</button><span>${numTxt(i.have)}</span><button data-bstep="${i.key}" data-d="1" aria-label="Flere">+</button></div>`;
  return `<div class="brow ${i.na ? "dim" : ""}">
    <span class="bdot ${dot}"></span>
    <div class="bmain click" data-bedit="${i.key}"><div class="bname">${esc(i.name)}${i.src === "tips" ? `<span class="badge soft">tips</span>` : i.src === "egen" ? `<span class="badge soft">egen</span>` : ""}${exp}</div>
      <div class="info">${sub}</div>${link}${add}</div>
    ${ctrl}
  </div>`;
}

function renderBered() {
  const d = state.bered, el = $("#bered"), hh = d.household;
  const filt = state.beredFilter;
  const pct = d.percent;
  const jodN = hh.children + Math.min(hh.under40, hh.adults);
  const cats = d.categories.map((c) => {
    let items = c.items;
    if (filt === "missing") items = items.filter((i) => !i.na && i.status !== "ok");
    if (filt === "expiring") items = items.filter((i) => i.expiring);
    if (!items.length) return "";
    const done = c.total && c.done === c.total;
    const open = filt !== "all" || pref("bc-" + c.cat, done ? "0" : "1") === "1";
    return `<details class="pg bcat" data-bc="${esc(c.cat)}" ${open ? "open" : ""}>
      <summary class="pg-h"><span>${esc(c.cat)}</span><span class="${done ? "okc" : ""}">${done ? "✓ " : ""}${c.done}/${c.total}</span></summary>
      <div class="card list">${items.map((i) => beredRow(i.key === "jod" ? { ...i, note: i.note } : i)).join("")}</div></details>`;
  }).join("");

  el.innerHTML = `
    <div class="card ecocard bhead">
      <div class="eco-h"><h3>Beredskap for ${d.people} personer</h3><button class="linkish" id="b-hh">Endre</button></div>
      <div class="hint" style="margin:-4px 0 10px">${hh.adults} voksne og ${hh.children} barn · ${d.days} døgn${hh.diaper_kids ? ` · ${hh.diaper_kids} med bleie` : ""} – etter DSBs råd om egenberedskap</div>
      <div class="budget"><i class="${pct >= 100 ? "" : pct >= 60 ? "" : "warn"}" style="width:${Math.min(100, pct)}%"></i></div>
      <div class="budget-t"><span><b>${pct} %</b> på plass</span><span>${d.done} av ${d.total} ting er ferdige</span></div>
      <p class="hint" style="margin:10px 0 0">🔄 <b>Rundgang:</b> annenhver uke foreslår middagsplanen en middag som bruker det eldste fra beredskapen (merket 🛟). ${d.rotation && d.rotation.last ? `Sist: ${esc(fmtDateY(d.rotation.last))}. ` : ""}${d.rotation && d.rotation.due ? "Kommer med i neste middagsplan." : d.rotation && d.rotation.next ? `Neste gang fra ${esc(fmtDateY(d.rotation.next))}.` : ""}</p>
    </div>
    <div class="metrics m2">
      <div class="metric"><span>💧 Vann</span><b>${numTxt(d.water.have)} av ${d.water.need} l</b><small>${d.water.have ? `nok til ca. ${numTxt(d.water.days)} døgn` : "20 liter per person"}</small></div>
      <div class="metric"><span>🥫 Mat</span><b>${numTxt(d.food.days)} av ${d.days} døgn</b><small>ca. ${Math.round(d.food.kcal_day / 100) * 100} kcal per døgn for familien</small></div>
    </div>
    ${d.expiring.length ? `<div class="card job warnbox">⏰ <b>Utløper innen 2 måneder:</b> ${d.expiring.map((i) => `${esc(i.name)} (${i.stale ? "ikke kjøpt på lenge" : fmtDateY(i.expiry)})`).join(", ")}. Bruk det i hverdagen og kjøp nytt.</div>` : ""}
    <div class="card controls">
      <div class="seg" id="b-filter">
        <button data-f="all" class="${filt === "all" ? "on" : ""}">Alt</button>
        <button data-f="missing" class="${filt === "missing" ? "on" : ""}">Mangler (${d.missing.length})</button>
        <button data-f="expiring" class="${filt === "expiring" ? "on" : ""}">Utløper (${d.expiring.length})</button>
      </div>
      ${d.missing.length ? `<button class="primary wide" id="b-tolist">🛒 Legg det som mangler på handlelista</button>` : `<div class="hint" style="text-align:center;margin-top:10px">Alt er på plass 👏</div>`}
    </div>
    <div class="pgrid bgrid">${cats || `<div class="card empty">Ingenting her.</div>`}</div>
    <div class="card ecocard">
      <div class="eco-h"><h3>Legg til noe eget</h3></div>
      <div class="addrow"><input type="text" id="b-new" placeholder="F.eks. «Ekstra gassboks» eller «Hundemat»"><input type="number" id="b-new-n" min="1" value="1" style="width:70px"><button id="b-new-add">Legg til</button></div>
    </div>
    <p class="hint">Mengdene følger rådene fra DSB (Direktoratet for samfunnssikkerhet og beredskap): klar deg selv i ${d.days} døgn uten strøm, vann og butikker, med 20 liter vann per person. «tips» er ting Matplan foreslår i tillegg. Jodtabletter: til ${jodN} i familien (barn og voksne under 40). Les mer på <a href="https://www.sikkerhverdag.no/din-beredskap/" target="_blank" rel="noopener">sikkerhverdag.no</a>.</p>`;

  $$("#b-filter button").forEach((b) => b.onclick = () => { state.beredFilter = b.dataset.f; setPref("bfilter", b.dataset.f); renderBered(); });
  $("#b-hh").onclick = beredHousehold;
  const tl = $("#b-tolist"); if (tl) tl.onclick = beredToList;
  $("#b-new-add").onclick = async () => {
    const name = $("#b-new").value.trim();
    if (!name) return toast("Skriv hva du vil legge til");
    try { state.bered = await api("beredskap/custom", { body: { name, need: +$("#b-new-n").value || 1, unit: "stk" } }); renderBered(); toast("Lagt til under «Nyttig å ha»"); }
    catch (e) { toast(e.message); }
  };
}

async function beredSave(key, data) {
  try { state.bered = await api("beredskap/item", { body: { key, ...data } }); renderBered(); } catch (e) { toast(e.message); }
}
const beredItem = (key) => state.bered.categories.flatMap((c) => c.items).find((i) => i.key === key);

$("#bered").addEventListener("click", (e) => {
  const st = e.target.closest("[data-bstep]");
  if (st) {
    const i = beredItem(st.dataset.bstep), step = bStep(i);
    const v = Math.max(0, Math.round((i.have + step * +st.dataset.d) * 10) / 10);
    i.have = v; st.parentElement.querySelector("span").textContent = numTxt(v); // vises med en gang
    clearTimeout(beredSave._t);
    beredSave._t = setTimeout(() => beredSave(i.key, { have: v }), 450);
    return;
  }
  const ba = e.target.closest("[data-badd]");
  if (ba) { const i = beredItem(ba.dataset.badd); return beredSave(i.key, { have: Math.round((i.have + i.bought_since) * 10) / 10 }); }
  const ch = e.target.closest("[data-bchk]");
  if (ch) { const i = beredItem(ch.dataset.bchk); return beredSave(i.key, { have: i.have ? 0 : 1 }); }
  const ed = e.target.closest("[data-bedit]");
  if (ed) beredEdit(beredItem(ed.dataset.bedit));
});
$("#bered").addEventListener("toggle", (e) => {
  const d = e.target; if (d.matches && d.matches("details.bcat") && state.beredFilter === "all") setPref("bc-" + d.dataset.bc, d.open ? "1" : "0");
}, true);

function beredEdit(i) {
  modal(`
    <h3>${esc(i.name)}</h3>
    ${i.note ? `<p class="hint" style="margin-top:0">${esc(i.note)}</p>` : ""}
    <p class="hint">${i.src === "DSB" ? "Fra DSBs råd om egenberedskap." : i.src === "egen" ? "Lagt til av dere." : "Tips fra Matplan i tillegg til DSBs liste."}${i.kind !== "check" && !i.custom ? ` Anbefalt for dere: <b>${numTxt(i.need_default)} ${esc(i.unit)}</b>.` : ""}</p>
    ${i.kind !== "check" ? `<label class="field">Mengde dere vil ha (${esc(i.unit || "antall")})<small>${i.need_custom ? `Endret av dere. <a href="#" id="be-nreset">Tilbake til anbefalt (${numTxt(i.need_default)})</a>` : "Endre hvis dere vil ha mer eller mindre enn anbefalt."}</small><input type="number" id="be-need" min="0" step="${bStep(i)}" value="${i.need}"></label>` : ""}
    ${i.kind === "check"
      ? `<label class="row" style="padding:10px 0"><input type="checkbox" id="be-have" ${i.have ? "checked" : ""}><div class="main">Har dette</div></label>`
      : `<label class="field">Har (${esc(i.unit || "antall")})<input type="number" id="be-have" min="0" step="${bStep(i)}" value="${i.have}"></label>`}
    <label class="field">Går ut / best før<small>${i.expiry_est ? `Anslått til ${esc(fmtDateY(i.expiry))} ut fra siste kjøp – fornyes hver gang dere kjøper varen. Skriv en dato for å overstyre.` : "Valgfritt. Matplan varsler her to måneder før."}</small><input type="date" id="be-exp" value="${i.expiry_est ? "" : (i.expiry || "")}"></label>
    ${i.kind !== "check" ? `<details class="dropdown" id="be-links"><summary>🔄 Koblet til lageret: ${i.linked.length ? esc(i.linked.map((p) => p.name).join(", ")) : "ingen"}</summary>
      <div class="dd-body"><div style="padding:8px 12px"><input type="search" id="be-lq" placeholder="Søk i lageret" style="margin:0"></div>
      <div id="be-ll">${[...state.bered.candidates].sort((a, b) => i.linked.some((p) => p.id === b.id) - i.linked.some((p) => p.id === a.id)).map((c) => `<label class="row" data-n="${esc(c.name.toLowerCase())}"><input type="checkbox" value="${c.id}" ${i.linked.some((p) => p.id === c.id) ? "checked" : ""}><div class="main">${esc(c.name)} <small style="color:var(--muted)">${esc(c.category)}</small></div></label>`).join("")}</div>
      ${i.links_manual ? `<div style="padding:8px 12px"><button class="small ghost" id="be-lauto">Tilbake til automatisk</button></div>` : ""}</div></details>
      <p class="hint">Varer dere bruker i hverdagen, fornyer beredskapen når dere kjøper dem – bruk det eldste først. Kjøp etter at dere telte sist, kan legges til med ett trykk.</p>` : ""}
    <label class="row" style="padding:10px 0 0"><input type="checkbox" id="be-na" ${i.na && !i.auto_na ? "checked" : ""}><div class="main">Ikke aktuelt for oss</div></label>
    <div class="modal-acts">${i.custom ? `<button class="ghost danger" id="be-del">Slett</button>` : ""}<button class="ghost" id="m-cancel">Avbryt</button><button class="primary" id="m-save">Lagre</button></div>
  `, (m) => {
    $("#m-cancel", m).onclick = closeModal;
    $("#m-save", m).onclick = async () => {
      const hv = $("#be-have", m);
      const body = { expiry: $("#be-exp", m).value || null, na: $("#be-na", m).checked };
      const nv = hv.type === "checkbox" ? (hv.checked ? 1 : 0) : (+hv.value || 0);
      if (nv !== i.have) body.have = nv;
      const nd = $("#be-need", m);
      if (nd) { const v = nd.value === "" ? null : +nd.value; if (m._needReset) body.need = null; else if (v !== i.need) body.need = v; }
      const ll = $("#be-ll", m);
      if (ll && m._linksTouched) body.links = m._linksAuto ? null : $$("input:checked", ll).map((x) => +x.value);
      await beredSave(i.key, body);
      closeModal();
    };
    const nr = $("#be-nreset", m);
    if (nr) nr.onclick = (ev) => { ev.preventDefault(); m._needReset = true; $("#be-need", m).value = i.need_default; nr.parentElement.textContent = "Bruker anbefalt mengde når du lagrer."; };
    const ll = $("#be-ll", m);
    if (ll) {
      ll.addEventListener("change", () => { m._linksTouched = true; m._linksAuto = false; });
      $("#be-lq", m).addEventListener("input", (ev) => { const q = ev.target.value.toLowerCase(); $$("label[data-n]", ll).forEach((r) => (r.hidden = q && !r.dataset.n.includes(q))); });
      const la = $("#be-lauto", m); if (la) la.onclick = () => { m._linksTouched = true; m._linksAuto = true; toast("Lagre for å bruke automatisk kobling"); };
    }
    const del = $("#be-del", m);
    if (del) del.onclick = async () => { state.bered = await api("beredskap/custom", { body: { remove: i.key } }); closeModal(); renderBered(); };
  });
}

function beredHousehold() {
  const hh = state.bered.household;
  const f = (id, label, v, small = "") => `<label class="field">${label}${small ? `<small>${small}</small>` : ""}<input type="number" id="${id}" min="0" max="30" value="${v}"></label>`;
  modal(`
    <h3>Husstanden</h3>
    <div class="two">${f("hh-a", "Voksne", hh.adults)}${f("hh-c", "Barn", hh.children)}</div>
    ${f("hh-u", "Voksne under 40 år", hh.under40, "For jodtabletter – DSB anbefaler dem til barn og voksne under 40.")}
    ${f("hh-d", "Barn som bruker bleie", hh.diaper_kids)}
    ${f("hh-days", "Antall døgn", hh.days, "DSB anbefaler å klare seg selv i 7 døgn.")}
    <div class="modal-acts"><button class="ghost" id="m-cancel">Avbryt</button><button class="primary" id="m-save">Lagre</button></div>
  `, (m) => {
    $("#m-cancel", m).onclick = closeModal;
    $("#m-save", m).onclick = async () => {
      try {
        state.bered = await api("beredskap/household", { body: { adults: +$("#hh-a", m).value, children: +$("#hh-c", m).value, under40: +$("#hh-u", m).value, diaper_kids: +$("#hh-d", m).value, days: +$("#hh-days", m).value || 7 } });
        closeModal(); renderBered();
      } catch (e) { toast(e.message); }
    };
  });
}

function beredToList() {
  const miss = state.bered.missing;
  const label = (i) => i.kind === "check" ? esc(i.name) : `${esc(i.name)} <small>– ${numTxt(i.need - i.have)} ${esc(i.unit)}</small>`;
  modal(`
    <h3>Legg på handlelista</h3>
    <p class="hint" style="margin-top:0">Velg hva som skal på handlelista. Varene merkes «(beredskap)» så de ikke blandes med vanlig handel.</p>
    <div class="two" style="margin-bottom:8px"><button class="ghost small" id="bl-all">Velg alle</button><button class="ghost small" id="bl-none">Velg ingen</button></div>
    <div class="card list blist">${miss.map((i) => `<label class="row"><input type="checkbox" data-k="${i.key}" ${i.kind === "check" || i.key === "vann" ? "" : "checked"}><div class="main">${label(i)}</div></label>`).join("")}</div>
    <p class="hint">Vann og ting som ikke kjøpes i butikken (kontanter, telefonliste) er ikke valgt på forhånd.</p>
    <div class="modal-acts"><button class="ghost" id="m-cancel">Avbryt</button><button class="primary" id="m-save">Legg på lista</button></div>
  `, (m) => {
    $("#bl-all", m).onclick = () => $$("input[data-k]", m).forEach((x) => (x.checked = true));
    $("#bl-none", m).onclick = () => $$("input[data-k]", m).forEach((x) => (x.checked = false));
    $("#m-cancel", m).onclick = closeModal;
    $("#m-save", m).onclick = async () => {
      const keys = $$("input[data-k]:checked", m).map((x) => x.dataset.k);
      if (!keys.length) return toast("Velg minst én ting");
      try { const r = await api("beredskap/tolist", { body: { keys } }); closeModal(); toast(`${r.added.length} lagt på handlelista`); }
      catch (e) { toast(e.message); }
    };
  });
}

// Bytt visning når vinduet går mellom mobil- og PC-bredde
window.matchMedia("(min-width: 1000px)").addEventListener("change", () => {
  const active = $(".tab.active");
  if (!active) return;
  if (active.id === "tab-lager") renderPantry();
  if (active.id === "tab-okonomi" && state.eco) renderEconomy();
  if (active.id === "tab-beredskap" && state.bered) renderBered();
  if (active.id === "tab-handle" && state.shop) renderShop();
  if (active.id === "tab-kvitt" && !isWide()) { $("#rc-detail").hidden = true; }
});

// ================================================================ start
(async () => {
  try {
    const st = await api("state");
    state.me = st;
    state.writeCal = !!st.settings.write_calendar;
    if (st.user && st.user.name) $("#who").textContent = "Hei, " + st.user.name.split(" ")[0];
    if (!st.settings.todo_entity) { state.moreView = "settings"; showTab("innst"); toast("Velkommen! Start med å velge handleliste og kalendere.", 5000); return; }
  } catch (e) { toast(e.message, 6000); }
  let tab = "middag";
  try { tab = localStorage.getItem("matplan-tab") || "middag"; } catch (_) {}
  showTab(tab);
})();
