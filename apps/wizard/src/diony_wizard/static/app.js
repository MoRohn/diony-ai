const railEl = document.getElementById("rail");
const formEl = document.getElementById("form");
const errEl = document.getElementById("form-error");
const statusEl = document.getElementById("status");
const producerEl = document.getElementById("producer-line");
const loomEl = document.getElementById("loom");
const learnTitle = document.getElementById("learn-title");
const learnWhy = document.getElementById("learn-why");
const aboutEl = document.getElementById("about");
const aboutBody = document.getElementById("about-body");
const lineIndex = document.getElementById("line-index");
const snippetEl = document.getElementById("snippet");
const fileboxEl = document.getElementById("filebox");
const healthEl = document.getElementById("health");
const notesEl = document.getElementById("notes");
const reviewBanner = document.getElementById("review-banner");
const prepEl = document.getElementById("prep");
const doneEl = document.getElementById("done");
const passBtn = document.getElementById("pass");
const modeLine = document.getElementById("mode-line");
const siteLine = document.getElementById("site-line");
const planEl = document.getElementById("plan-card");

const SITE_LABELS = {
  local: "Local Quad-Spark Cluster",
  dual: "Leased Dual-Spark Cluster",
  b300: "Leased B300",
};

const CHOICE_LABELS = {
  ...SITE_LABELS,
  yes: "Yes",
  no: "No",
  "yes-live": "Live pass",
  "dry-run-recorded": "Dry-run recorded",
};

let state = null;
let viewing = null;
let lesson = null;
let loomOn = { 1: false, 2: false, 3: false, 4: false };

function pad(n) {
  return String(n).padStart(2, "0");
}

function collect() {
  const answers = {};
  if (!formEl) return answers;
  formEl.querySelectorAll("[name]").forEach((el) => {
    answers[el.name] = el.value;
  });
  if ((viewing === "cables" || state?.active_step === "cables") && currentPath() !== "b300") {
    const all = Object.values(loomOn).every(Boolean);
    answers.loom = all ? "yes" : "";
  }
  return answers;
}

function currentPath() {
  return state?.compute_path || "";
}

function hostPrefix() {
  if (currentPath() === "lease") return "node";
  if (currentPath() === "b300") return "b300";
  return "spark";
}

function currentSite() {
  const site = formEl.querySelector('[name="site"]');
  return (site && site.value) || state?.compute_path || "";
}

function whenMatches(need, site) {
  if (!need) return true;
  return need.split(",").map((part) => part.trim()).includes(site);
}

function applyWhen() {
  if (!formEl) return;
  const site = currentSite();
  formEl.querySelectorAll("[data-when]").forEach((el) => {
    const need = el.getAttribute("data-when");
    el.classList.toggle("hidden", Boolean(need) && !whenMatches(need, site));
  });
}

function renderLoom(interactive) {
  if (!loomEl) return;
  const show = (viewing || state?.active_step) === "cables" && currentPath() !== "b300";
  loomEl.hidden = !show;
  loomEl.classList.toggle("hidden", !show);
  if (!show) return;
  const host = hostPrefix();
  loomEl.innerHTML = "";
  [1, 2, 3, 4].forEach((n) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = loomOn[n] ? "on" : "";
    btn.disabled = !interactive;
    btn.textContent = loomOn[n]
      ? `${host}-0${n}  fabric up`
      : `${host}-0${n}  click to land fabric`;
    btn.addEventListener("click", () => {
      loomOn[n] = !loomOn[n];
      renderLoom(true);
      saveQuiet();
    });
    loomEl.appendChild(btn);
  });
}

function fieldLabel(label) {
  return String(label || "").replace(/^check:\s*/i, "");
}

function escapeHtml(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function htmlList(items, ordered) {
  if (!items?.length) return "";
  const tag = ordered ? "ol" : "ul";
  return `<${tag}>${items.map((item) => `<li>${escapeHtml(item)}</li>`).join("")}</${tag}>`;
}

function section(title, inner, extraClass) {
  const klass = extraClass ? ` about-block ${extraClass}` : " about-block";
  return `<section class="${klass}"><h3>${escapeHtml(title)}</h3>${inner}</section>`;
}

function looksLikeMap(diagram) {
  return Boolean(diagram) && (diagram.includes("\n") || diagram.includes("----"));
}

function looksLikeCommands(cmds) {
  return (cmds || []).some((line) =>
    /^(#|curl |python|pip |vllm |ibstat|huggingface|hostname|ip |touch |diony-)/.test(line.trim())
  );
}

function paintAbout(item) {
  if (!aboutBody || !aboutEl) return;
  const parts = [];
  if (item.teach?.length) {
    parts.push(section("What to do", htmlList(item.teach)));
  }
  if (item.fail_if?.length) {
    parts.push(section("Stop if", htmlList(item.fail_if), "fail"));
  }
  if (item.handoff?.length) {
    parts.push(section("Then", htmlList(item.handoff, true)));
  }
  if (looksLikeMap(item.diagram)) {
    parts.push(section("Map", `<pre class="diagram">${escapeHtml(item.diagram)}</pre>`));
  }
  if (looksLikeCommands(item.commands)) {
    parts.push(
      section("Commands", `<pre class="commands">${escapeHtml(item.commands.join("\n"))}</pre>`)
    );
  }
  aboutBody.innerHTML = parts.join("");
  aboutEl.classList.toggle("hidden", parts.length === 0);
  const summary = aboutEl.querySelector("summary");
  if (summary) {
    summary.textContent = item.step_id === "app" || item.step_id === "producer" ? "How" : "Details";
  }
  aboutEl.open =
    item.step_id === "app" ||
    item.step_id === "connect" ||
    item.step_id === "vault" ||
    item.step_id === "producer";
}

function liveHintText(field, selected) {
  const help = field.option_help || {};
  if (selected && help[selected]) return help[selected];
  return field.hint || "";
}

function refreshFieldContext(wrap, field, selected) {
  const live = wrap.querySelector(".live-hint");
  if (live) live.textContent = liveHintText(field, selected);
}

function appendHelp(wrap, field, selected) {
  const live = document.createElement("p");
  live.className = "live-hint";
  live.textContent = liveHintText(field, selected);
  wrap.appendChild(live);
}

function fillPre(el, text) {
  if (!el) return;
  const on = Boolean(text);
  el.classList.toggle("hidden", !on);
  el.hidden = !on;
  el.textContent = text || "";
}

function setText(el, value) {
  if (el) el.textContent = value;
}

function paintNotes() {
  const open = [snippetEl, fileboxEl, healthEl].some((el) => !el.classList.contains("hidden"));
  notesEl.classList.toggle("hidden", !open);
  if (!open) notesEl.open = false;
  const summary = document.getElementById("notes-summary");
  if (!summary) return;
  if (!fileboxEl.classList.contains("hidden") && viewing === "unbox") {
    summary.textContent = "Cluster profile";
  } else if (!fileboxEl.classList.contains("hidden") && viewing === "inventory") {
    summary.textContent = "Cluster profile";
  } else if (!fileboxEl.classList.contains("hidden") && viewing === "connect") {
    summary.textContent = "Vault mount";
  } else if (!fileboxEl.classList.contains("hidden") && viewing === "app") {
    summary.textContent = "Pack files";
  } else if (!fileboxEl.classList.contains("hidden") && viewing === "vault") {
    summary.textContent = "Vault mount";
  } else if (!fileboxEl.classList.contains("hidden") && viewing === "producer") {
    summary.textContent = "Site pack";
  } else if (!snippetEl.classList.contains("hidden")) {
    summary.textContent = "Switch snippet";
  } else if (!healthEl.classList.contains("hidden")) {
    summary.textContent = "Health probe";
  } else {
    summary.textContent = "Files";
  }
}

function paintPlan() {
  if (!planEl) return;
  const path = currentPath();
  const step = viewing || state?.active_step;
  const pack = state?.model_pack;
  if ((step === "app" || step === "weights") && pack) {
    const rows = [
      ["Pack", pack.label],
      ["Brain", pack.brain],
      ["Camera", pack.camera],
      ["Serving", pack.recipe_label],
      ["Pull", pack.tool],
    ];
    fillPlan(rows);
    return;
  }
  const plan = state?.plan;
  if (step === "producer") {
    const url =
      (state?.workbook?.producer || {}).url ||
      (plan && plan.producer_url) ||
      state?.producer_url ||
      "";
    const rows = [
      ["Floors", (plan && plan.floors) || "Idea, Dailies, Program, Lock"],
      ["Cut", (plan && plan.cut) || "Prompt-to-edit on a take (VACE)"],
      ["Program", (plan && plan.program) || "Overlap stitch, 8–16 frames"],
      ["Producer", url],
    ];
    fillPlan(rows);
    return;
  }
  const lease = path === "lease" || path === "dual" || path === "b300";
  const show = Boolean(plan && lease && step === "connect");
  if (!show) {
    planEl.hidden = true;
    planEl.classList.add("hidden");
    planEl.innerHTML = "";
    return;
  }
  const fabric =
    plan.interconnect === "On-package" ? "On this GPU" : plan.interconnect;
  fillPlan([
    ["Profile", plan.sku],
    ["GPU", plan.gpu_class],
    ["Fabric", fabric],
  ]);
}

function fillPlan(rows) {
  planEl.innerHTML = rows
    .filter(([, value]) => value)
    .map(
      ([key, value]) =>
        `<div><dt>${escapeHtml(key)}</dt><dd>${escapeHtml(value)}</dd></div>`
    )
    .join("");
  planEl.hidden = false;
  planEl.classList.remove("hidden");
}

function paintChoice(field, answers, interactive) {
  const wrap = document.createElement("div");
  wrap.className = "field";
  if (field.when) wrap.dataset.when = field.when;
  const cap = document.createElement("span");
  cap.textContent = fieldLabel(field.label);
  wrap.appendChild(cap);
  const row = document.createElement("div");
  row.className = "choice";
  const input = document.createElement("input");
  input.type = "hidden";
  input.name = field.id;
  input.value = answers[field.id] || "";
  wrap.appendChild(input);
  (field.options || []).forEach((opt) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.textContent = CHOICE_LABELS[opt] || SITE_LABELS[opt] || opt;
    btn.className = input.value === opt ? "on" : "";
    btn.setAttribute("aria-pressed", input.value === opt ? "true" : "false");
    if (field.id === "site") btn.setAttribute("data-site", opt);
    btn.disabled = !interactive;
    btn.addEventListener("click", () => {
      if (errEl) errEl.textContent = "";
      input.value = opt;
      row.querySelectorAll("button").forEach((b) => {
        b.classList.toggle("on", b === btn);
        b.setAttribute("aria-pressed", b === btn ? "true" : "false");
      });
      refreshFieldContext(wrap, field, opt);
      applyWhen();
      saveQuiet();
    });
    row.appendChild(btn);
  });
  wrap.appendChild(row);
  appendHelp(wrap, field, input.value);
  return wrap;
}

function compactOptions(field) {
  return (field.options || []).filter((opt) => String(opt || "").length > 0);
}

function asChoiceField(field) {
  if (field.kind === "choice") return field;
  if (field.kind !== "select") return null;
  const options = compactOptions(field);
  if (!options.length || options.length > 4) return null;
  return { ...field, kind: "choice", options };
}

function paintField(field, answers, interactive) {
  const choice = asChoiceField(field);
  if (choice) return paintChoice(choice, answers, interactive);
  const wrap = document.createElement("div");
  wrap.className = "field";
  if (field.when) wrap.dataset.when = field.when;
  const lab = document.createElement("label");
  const cap = document.createElement("span");
  cap.textContent = fieldLabel(field.label);
  lab.appendChild(cap);
  let input;
  if (field.kind === "select") {
    input = document.createElement("select");
    (field.options || []).forEach((opt) => {
      const o = document.createElement("option");
      o.value = opt;
      o.textContent = opt || "Choose";
      input.appendChild(o);
    });
    input.name = field.id;
    input.value = answers[field.id] || field.default || "";
    input.disabled = !interactive;
    lab.appendChild(input);
    wrap.appendChild(lab);
    appendHelp(wrap, field, input.value);
    input.addEventListener("change", () => {
      refreshFieldContext(wrap, field, input.value);
    });
    return wrap;
  }
  if (field.kind === "textarea") {
    input = document.createElement("textarea");
    input.rows = currentPath() === "b300" ? 2 : 4;
    input.placeholder = field.placeholder || "";
    input.name = field.id;
    input.value = answers[field.id] || field.default || "";
    input.disabled = !interactive;
    lab.appendChild(input);
    wrap.appendChild(lab);
    appendHelp(wrap, field, "");
    return wrap;
  }
  input = document.createElement("input");
  input.type = "text";
  input.placeholder = field.placeholder || "";
  input.name = field.id;
  input.value = answers[field.id] || field.default || "";
  input.disabled = !interactive;
  lab.appendChild(input);
  wrap.appendChild(lab);
  appendHelp(wrap, field, "");
  return wrap;
}

function paintLesson(item, answers, interactive) {
  if (!item || !formEl) return;
  const steps = state?.steps || [];
  const idx = steps.findIndex((s) => s.id === item.step_id) + 1;
  const stepMeta = steps.find((s) => s.id === item.step_id);
  setText(lineIndex, pad(Math.max(idx, 1)));
  setText(learnTitle, (stepMeta && stepMeta.name) || item.step_id || "Site");
  setText(learnWhy, item.why || "");
  try {
    paintAbout(item);
  } catch (err) {
    console.error(err);
  }
  try {
    paintPlan();
  } catch (err) {
    console.error(err);
  }

  try {
    const frag = document.createDocumentFragment();
    (item.fields || []).forEach((field) => {
      if (field.id === "loom") return;
      if (field.auto && field.default) {
        const hidden = document.createElement("input");
        hidden.type = "hidden";
        hidden.name = field.id;
        hidden.value = (answers || {})[field.id] || field.default;
        frag.appendChild(hidden);
        return;
      }
      if (field.id === "pack" && field.default) {
        const hidden = document.createElement("input");
        hidden.type = "hidden";
        hidden.name = "pack";
        hidden.value = (answers || {}).pack || field.default;
        frag.appendChild(hidden);
        return;
      }
      frag.appendChild(paintField(field, answers || {}, interactive));
    });
    if (item.step_id === "app") {
      const note = document.createElement("p");
      note.className = "live-hint";
      note.textContent =
        "Nothing to choose. The pack is pinned. Follow How, then Continue.";
      frag.appendChild(note);
    }
    formEl.replaceChildren(frag);
  } catch (err) {
    console.error(err);
    return;
  }
  applyWhen();
  renderLoom(interactive);
  const sku = formEl.querySelector('[name="sku"]');
  if (sku) {
    sku.addEventListener("change", () => loadSnippet(sku.value));
    loadSnippet(sku.value);
  } else {
    fillPre(snippetEl, "");
    paintNotes();
  }
  loadSideFiles(item.step_id);
}

async function loadSnippet(sku) {
  if (!sku) {
    fillPre(snippetEl, "");
    paintNotes();
    return;
  }
  const res = await fetch(`/api/snippets/${encodeURIComponent(sku)}`);
  if (!res.ok) {
    fillPre(snippetEl, "");
    paintNotes();
    return;
  }
  const body = await res.json();
  fillPre(snippetEl, `${body.path}\n\n${body.text}`);
  paintNotes();
}

async function loadSideFiles(stepId) {
  if (stepId === "vault" || stepId === "connect") {
    const res = await fetch("/api/files/fstab");
    const body = await res.json();
    fillPre(fileboxEl, `${body.path}\n\n${body.text}`);
  } else if (stepId === "app" || stepId === "weights" || stepId === "producer") {
    const files = await Promise.all([
      fetch("/api/files/manifest").then((res) => res.json()),
      state.compute_path
        ? fetch("/api/files/serving").then((res) => res.json())
        : Promise.resolve(null),
    ]);
    const [manifest, serving] = files;
    const extra = serving ? `\n\n${serving.path}\n\n${serving.text}` : "";
    fillPre(fileboxEl, `${manifest.path}\n\n${manifest.text}${extra}`);
  } else if (
    (stepId === "unbox" || stepId === "inventory") &&
    state.compute_path
  ) {
    const res = await fetch("/api/files/profile");
    const body = await res.json();
    fillPre(fileboxEl, `${body.path}\n\n${body.text}`);
  } else {
    fillPre(fileboxEl, "");
  }
  if (stepId === "roce" || state.complete) {
    const res = await fetch("/api/health");
    const body = await res.json();
    fillPre(healthEl, JSON.stringify(body, null, 2));
  } else {
    fillPre(healthEl, "");
  }
  paintNotes();
}

function renderRail() {
  if (!railEl || !state?.steps) return;
  railEl.innerHTML = "";
  state.steps.forEach((step, i) => {
    const li = document.createElement("li");
    const btn = document.createElement("button");
    btn.type = "button";
    const looking = (viewing || state.active_step) === step.id;
    btn.className = `${step.status}${looking ? " active" : ""}`;
    btn.disabled = step.status === "pending";
    btn.innerHTML = `<span>${pad(i + 1)}</span>${step.name}`;
    btn.addEventListener("click", () => openLine(step.id));
    li.appendChild(btn);
    railEl.appendChild(li);
  });
}

async function openLine(stepId) {
  const step = state.steps.find((s) => s.id === stepId);
  if (!step || step.status === "pending") return;
  viewing = stepId;
  const res = await fetch(`/api/lessons/${stepId}`);
  const payload = await res.json();
  const loaded = res.ok && Array.isArray(payload.fields) ? payload : state.lesson;
  if (!loaded || !Array.isArray(loaded.fields) || loaded.step_id !== stepId) {
    errEl.textContent = payload.detail || "Could not load this line.";
    return;
  }
  lesson = loaded;
  const interactive = step.status === "active" || step.status === "failed";
  reviewBanner.classList.toggle("hidden", interactive);
  passBtn.disabled = !interactive;
  paintPass();
  paintLesson(lesson, state.workbook[stepId] || state.answers || {}, interactive);
  renderRail();
  statusEl.textContent = "";
  const well = document.querySelector(".well");
  if (well) well.scrollTop = 0;
}

function lockRows(report) {
  const pack = state?.model_pack || {};
  const wb = report.workbook || {};
  const sitePack = report.pack || {};
  const vault = wb.vault || wb.connect || {};
  const vaultLine = vault.nas_ip
    ? `${vault.nas_ip} → ${vault.export || "/diony/vault"}`
    : sitePack.vault || "";
  return [
    ["Site", SITE_LABELS[report.compute_path] || report.compute_path],
    ["Profile", report.sku],
    ["Pack", pack.label || (wb.weights || {}).label],
    ["Brain", (wb.brain_smoke || {}).recipe],
    ["Host", (sitePack.nodes || []).join(" · ")],
    ["Vault", vaultLine],
    ["Floors", "Idea, Dailies, Program, Lock"],
    ["Producer", state.producer_url],
  ];
}

function showComplete() {
  prepEl.classList.add("hidden");
  passBtn.classList.add("hidden");
  doneEl.classList.remove("hidden");
  doneEl.hidden = false;
  const report = state.report;
  document.getElementById("done-mandate").textContent = state.mandate;
  document.getElementById("done-meta").textContent =
    `${SITE_LABELS[report.compute_path] || report.compute_path || "local"} · ${report.sku} · dry-run`;
  const sheetEl = document.getElementById("done-sheet");
  if (sheetEl) {
    sheetEl.innerHTML = lockRows(report)
      .filter(([, value]) => value)
      .map(
        ([key, value]) =>
          `<div><dt>${escapeHtml(key)}</dt><dd>${escapeHtml(value)}</dd></div>`
      )
      .join("");
  }
  document.getElementById("done-report").textContent = JSON.stringify(report, null, 2);
  const href = state.producer_href || state.producer_url || "";
  document.getElementById("done-producer").innerHTML = href
    ? `<a href="${escapeHtml(href)}">Open Producer</a>`
    : "";
  fetch("/api/report")
    .then((res) => (res.ok ? res.json() : null))
    .then((full) => {
      if (full) {
        document.getElementById("done-report").textContent = JSON.stringify(full, null, 2);
      }
    });
}

function paintSite() {
  if (!siteLine) return;
  if (!state || !state.compute_path) {
    siteLine.textContent = "Unsigned";
    return;
  }
  siteLine.textContent = SITE_LABELS[state.compute_path] || state.compute_path;
}

function isLockStep(stepId) {
  return stepId === "producer";
}

function paintPass() {
  if (!passBtn) return;
  const last = isLockStep(viewing || state?.active_step);
  const stick = passBtn.querySelector(".clap-stick");
  const slate = passBtn.querySelector(".clap-slate");
  passBtn.classList.toggle("clap", last);
  passBtn.classList.toggle("continue", !last);
  passBtn.setAttribute("aria-label", last ? "Sign" : "Continue");
  if (stick) stick.hidden = !last;
  if (slate) slate.textContent = last ? "Sign" : "Continue";
}

function render() {
  try {
    paintSite();
    if (modeLine) {
      modeLine.textContent = state.dry_run
        ? "Dry-run. No fabric mutation."
        : "Live. App pulls the pack and starts Producer on this pair.";
    }
    paintApply(state.apply);
    renderRail();
    if (state.complete) {
      showComplete();
      return;
    }
    prepEl.classList.remove("hidden");
    passBtn.classList.remove("hidden");
    doneEl.classList.add("hidden");
    doneEl.hidden = true;
    if (producerEl) producerEl.innerHTML = "";
    const stepId =
      viewing && state.steps.some((s) => s.id === viewing && s.status !== "pending")
        ? viewing
        : state.active_step;
    viewing = stepId;
    paintPass();
    const cached = state.lesson && state.lesson.step_id === stepId ? state.lesson : null;
    if (cached) {
      const step = state.steps.find((s) => s.id === stepId);
      const interactive = !step || step.status === "active" || step.status === "failed";
      paintLesson(cached, state.workbook[stepId] || state.answers || {}, interactive);
    }
    if (stepId) openLine(stepId);
  } catch (err) {
    console.error(err);
    if (errEl && formEl && !formEl.querySelector("[data-site], [name=site]")) {
      errEl.textContent = "Reload this page.";
    }
  }
}

async function load() {
  try {
    const res = await fetch("/api/state");
    state = await res.json();
    viewing = state.active_step;
    render();
  } catch (err) {
    console.error(err);
    if (errEl) errEl.textContent = "Could not load Wizard state.";
  }
}

async function saveQuiet() {
  if (!state?.active_step) return;
  await fetch(`/api/steps/${state.active_step}/save`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ answers: collect() }),
  });
}

let applyTimer = null;

function paintApply(apply) {
  const logEl = document.getElementById("apply-log");
  if (!apply || !logEl) return;
  const lines = apply.log || [];
  const on = Boolean(apply.running || apply.error || (apply.live && lines.length));
  logEl.hidden = !on;
  logEl.classList.toggle("hidden", !on);
  if (on) {
    logEl.textContent = lines.join("\n") || apply.detail || apply.error || "Applying pack…";
  }
  if (apply.running && statusEl) {
    statusEl.textContent = apply.detail || "Pulling the pack onto this pair…";
  }
  if (apply.error && errEl && !errEl.textContent) {
    errEl.textContent = apply.error;
  }
}

async function pollApply() {
  if (applyTimer) return;
  const tick = async () => {
    try {
      const apply = await fetch("/api/apply").then((r) => r.json());
      if (state) state.apply = apply;
      paintApply(apply);
      if (!apply.running) {
        clearInterval(applyTimer);
        applyTimer = null;
        if (apply.ok && state?.active_step === "app") {
          act("pass");
        }
      }
    } catch {
      clearInterval(applyTimer);
      applyTimer = null;
    }
  };
  applyTimer = setInterval(tick, 3000);
  tick();
}

async function act(kind) {
  errEl.textContent = "";
  if (kind === "reset") {
    loomOn = { 1: false, 2: false, 3: false, 4: false };
    viewing = null;
    await fetch("/api/reset", { method: "POST" });
    await load();
    return;
  }
  if (!state?.active_step) return;
  const answers = collect();
  const res = await fetch(`/api/steps/${state.active_step}/pass`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ answers }),
  });
  const body = await res.json();
  if (!res.ok) {
    errEl.textContent = body.detail || (isLockStep(state.active_step) ? "Could not sign." : "Could not continue.");
    return;
  }
  state = body;
  viewing = state.active_step;
  loomOn = { 1: false, 2: false, 3: false, 4: false };
  render();
  if (body.apply?.running) {
    pollApply();
  }
}

function pickSite(site, btn) {
  if (!formEl || !site) return;
  const input = formEl.querySelector('[name="site"]');
  if (input) input.value = site;
  formEl.querySelectorAll("[data-site]").forEach((other) => {
    const on = other === btn || other.getAttribute("data-site") === site;
    other.classList.toggle("on", on);
    other.setAttribute("aria-pressed", on ? "true" : "false");
  });
  applyWhen();
  if (errEl) errEl.textContent = "";
  saveQuiet();
}

formEl?.addEventListener("click", (event) => {
  const btn = event.target.closest("[data-site]");
  if (!btn || !formEl.contains(btn)) return;
  event.preventDefault();
  pickSite(btn.getAttribute("data-site"), btn);
});
formEl?.addEventListener("change", saveQuiet);
formEl?.addEventListener("keydown", (event) => {
  if (event.key !== "Enter" || event.shiftKey) return;
  if (event.target && event.target.tagName === "TEXTAREA") return;
  event.preventDefault();
  act("pass");
});
passBtn?.addEventListener("click", () => act("pass"));
document.getElementById("reset-done")?.addEventListener("click", () => act("reset"));

load();
