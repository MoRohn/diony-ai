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
const computeConfirm = document.getElementById("compute-confirm");
const provisionPanel = document.getElementById("provision-panel");
const provisionForm = document.getElementById("provision-form");
const provisionError = document.getElementById("provision-error");
const provisionProgress = document.getElementById("provision-progress");
const provisionContinue = document.getElementById("provision-continue");

const SITE_LABELS = {
  local: "DGX Quad-Spark Cluster",
  dual: "Enverge DGX Dual-Spark Cluster",
  b300: "Runpod NVIDIA B300",
  b200: "Runpod NVIDIA B200",
};

const CHOICE_LABELS = {
  ...SITE_LABELS,
  yes: "Yes",
  no: "No",
  "yes-live": "Live pass",
  "dry-run-recorded": "Dry-run recorded",
};

const COMPUTE_META = {
  local: {
    provider: "This studio",
    hardware: "4× DGX Spark",
    handoff: "Continue to the physical crate sequence.",
  },
  dual: {
    provider: "Enverge",
    hardware: "2× DGX Spark · fabric connected",
    handoff: "You approve the provider account and allocation. Diony handles SSH, vault, pack, and Producer.",
  },
  b300: {
    provider: "Runpod",
    hardware: "1× NVIDIA B300 · 1 TB persistent volume",
    handoff: "Diony checks live stock and price before the billing confirmation, then deploys by API.",
  },
  b200: {
    provider: "Runpod",
    hardware: "1× NVIDIA B200 · 1 TB persistent volume",
    handoff: "Diony checks live stock and price before the billing confirmation, then deploys by API.",
  },
};

const SHORT_INSTRUCTIONS = {
  path: "Choose where Diony AI will run. Agent will take it from here.",
  unbox: "Place and power each Spark.",
  inventory: "Confirm the four Sparks.",
  cables: "Connect the Sparks and vault.",
  switch: "Set every fabric port to 200G.",
  sync: "Add the four Spark addresses.",
  roce: "Confirm the fabric is healthy.",
  vault: "Connect the shared vault.",
  connect: "Enter the Site SSH and vault details.",
  app: "Check the Site. Pull only what is missing.",
  prove: "Test the Site before signing.",
  producer: "Sign this Site, then open Producer.",
};

let state = null;
let viewing = null;
let lesson = null;
let loomOn = { 1: false, 2: false, 3: false, 4: false };
let studioTransitionTimer = null;
let provision = null;
let provisionTimer = null;
let provisionAdopting = false;
let existingFinalizeBusy = false;

function triggerStudioTransition(previousStep, nextStep) {
  if (!previousStep || previousStep === nextStep) return;
  const order = (state?.steps || []).map((step) => step.id);
  const previousIndex = order.indexOf(previousStep);
  const nextIndex = order.indexOf(nextStep);
  document.body.dataset.sceneDirection =
    previousIndex >= 0 && nextIndex >= 0 && nextIndex < previousIndex ? "back" : "forward";
  document.body.classList.remove("studio-scene-transition");
  void prepEl?.offsetWidth;
  document.body.classList.add("studio-scene-transition");
  if (studioTransitionTimer) clearTimeout(studioTransitionTimer);
  studioTransitionTimer = window.setTimeout(() => {
    document.body.classList.remove("studio-scene-transition");
  }, 760);
}

function pad(n) {
  return String(n).padStart(2, "0");
}

function clearFieldErrors() {
  formEl?.querySelectorAll(".field.bad").forEach((el) => el.classList.remove("bad"));
}

function markFieldError(detail) {
  clearFieldErrors();
  const text = String(detail || "").toLowerCase();
  if (!formEl || !text) return;
  formEl.querySelectorAll(".field").forEach((wrap) => {
    const cap = wrap.querySelector("span")?.textContent || "";
    if (cap && text.includes(cap.toLowerCase())) wrap.classList.add("bad");
  });
}

function collect() {
  const answers = {};
  if (!formEl) return answers;
  formEl.querySelectorAll("[name]").forEach((el) => {
    answers[el.name] = el.value;
  });
  if ((viewing === "cables" || state?.active_step === "cables") && !isSingleGpu(currentPath())) {
    const all = Object.values(loomOn).every(Boolean);
    answers.loom = all ? "yes" : "";
  }
  return answers;
}

function currentPath() {
  return state?.compute_path || "";
}

function isSingleGpu(path) {
  return path === "b300" || path === "b200";
}

function isWizardLease(path) {
  return path === "dual" || isSingleGpu(path);
}

function hostPrefix() {
  if (currentPath() === "lease") return "node";
  if (currentPath() === "b300") return "b300";
  if (currentPath() === "b200") return "b200";
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
  const show = (viewing || state?.active_step) === "cables" && !isSingleGpu(currentPath());
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

function stepInstruction(item) {
  return SHORT_INSTRUCTIONS[item?.step_id] || item?.why || "Complete this line.";
}

function fieldHelp(item) {
  const rows = (item?.fields || [])
    .filter((field) => !field.auto)
    .map((field) => {
      const details = [];
      if (field.source) details.push(field.source);
      if (field.hint) details.push(field.hint);
      Object.values(field.option_help || {}).forEach((value) => details.push(value));
      if (!details.length) return "";
      return `<dt>${escapeHtml(fieldLabel(field.label))}</dt><dd>${escapeHtml(
        [...new Set(details)].join(" ")
      )}</dd>`;
    })
    .filter(Boolean)
    .join("");
  return rows ? section("Field help", `<dl>${rows}</dl>`) : "";
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
  const hasGuide = Boolean(item.guide && item.guide.length);
  if (item.why && item.why !== stepInstruction(item)) {
    parts.push(section("Why", `<p>${escapeHtml(item.why)}</p>`));
  }
  const help = fieldHelp(item);
  if (help) parts.push(help);
  if (item.teach?.length && !hasGuide) {
    parts.push(section("What to do", htmlList(item.teach)));
  }
  if (item.fail_if?.length) {
    parts.push(section("Stop if", htmlList(item.fail_if), "fail"));
  }
  if (item.handoff?.length) {
    parts.push(section("Then", htmlList(item.handoff, true)));
  }
  if (looksLikeMap(item.diagram) && !hasGuide) {
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
    summary.textContent = "Details";
  }
  aboutEl.open = false;
}

function paintGuide(item) {
  const el = document.getElementById("connect-guide");
  if (!el) return;
  const rows = item?.guide || [];
  const show = item?.step_id === "connect" && rows.length > 0;
  el.hidden = !show;
  el.classList.toggle("hidden", !show);
  if (!show) {
    el.innerHTML = "";
    return;
  }
  const stamp = { use: "Use this", skip: "Skip", check: "Same" };
  const skips = rows.filter((row) => row.use === "skip");
  const rest = rows.filter((row) => row.use !== "skip");
  let html = "";
  if (skips.length) {
    html += `<p class="guide-skip-line">Skip ${escapeHtml(
      skips.map((row) => row.heading).join(" · ")
    )}</p>`;
  }
  let lastTab = "";
  rest.forEach((row) => {
    const tab = row.tab || "Connect";
    if (tab !== lastTab) {
      lastTab = tab;
      html += `<p class="guide-kicker">RunPod → ${escapeHtml(tab)}</p>`;
    }
    const kind = row.use || "skip";
    html += `<article class="guide-block ${escapeHtml(kind)}">`;
    html += `<div class="guide-head"><span class="guide-use">${escapeHtml(
      stamp[kind] || kind
    )}</span><span class="guide-heading">${escapeHtml(row.heading || "")}</span></div>`;
    if (row.body && kind === "use") {
      html += `<p class="guide-body">${escapeHtml(row.body)}</p>`;
    }
    if (row.example) html += `<pre class="guide-example">${escapeHtml(row.example)}</pre>`;
    if (row.take) html += `<p class="guide-take">${escapeHtml(row.take)}</p>`;
    html += "</article>";
  });
  el.innerHTML = html;
}

function connectConfirmFromInputs() {
  const hostInput = formEl?.querySelector('[name="hosts"]');
  if (!hostInput) return null;
  const values = collect();
  const host = String(values.hosts || "").trim().split(/\s+/)[0] || "";
  const user = String(values.ssh_user || "").trim();
  const port = String(values.ssh_port || "").trim();
  const vault = String(values.nas_ip || "").trim();
  const ssh = host
    ? `ssh ${user ? `${user}@` : ""}${host}${port ? ` -p ${port}` : ""}`
    : "";
  return {
    ssh,
    vault: vault ? `${vault} → ${state?.vault || "/diony/vault"}` : "",
    gpu: state?.confirm?.gpu || "",
  };
}

function paintConfirm() {
  const el = document.getElementById("confirm-line");
  if (!el) return;
  const step = viewing || state?.active_step;
  const c = step === "connect" ? connectConfirmFromInputs() || state?.confirm : state?.confirm;
  const show = (step === "connect" || step === "prove") && c && (c.ssh || c.vault);
  el.hidden = !show;
  el.classList.toggle("hidden", !show);
  if (!show) {
    el.textContent = "";
    return;
  }
  el.textContent = [c.ssh, c.vault, c.gpu].filter(Boolean).join("  ·  ");
}

function packRowMissing() {
  const checks = state?.prove?.checks || [];
  return checks.some((row) => row.id === "pack" && !row.ok);
}

function applyElapsed(apply) {
  const raw = apply?.started_at;
  if (!raw) return "";
  const t = Date.parse(raw);
  if (!Number.isFinite(t)) return "";
  const sec = Math.max(0, Math.floor((Date.now() - t) / 1000));
  if (sec < 60) return `${sec}s`;
  const m = Math.floor(sec / 60);
  if (m < 60) return `${m} min`;
  return `${Math.floor(m / 60)}h ${m % 60} min`;
}

function paintPackProgress(apply) {
  const el = document.getElementById("pack-progress");
  const detail = document.getElementById("pack-progress-detail");
  if (!el) return;
  const step = viewing || state?.active_step;
  const here = step === "app" || step === "prove";
  const on = here && Boolean(apply?.running);
  el.hidden = !on;
  el.classList.toggle("hidden", !on);
  el.setAttribute("aria-busy", on ? "true" : "false");
  if (!on) {
    if (detail) detail.textContent = "";
    return;
  }
  const lines = apply.log || [];
  const line =
    apply.detail ||
    (lines.length ? lines[lines.length - 1] : "") ||
    "Hugging Face onto /models. This can take hours.";
  const elapsed = applyElapsed(apply);
  if (detail) detail.textContent = elapsed ? `${line}  ·  ${elapsed}` : line;
}

function paintProve() {
  const card = document.getElementById("prove-card");
  const hint = document.getElementById("ready-hint");
  const actions = document.getElementById("ready-actions");
  const pullBtn = document.getElementById("retry-apply");
  const testBtn = document.getElementById("test-gpu");
  const step = viewing || state?.active_step;
  const onReady = step === "prove";
  const onApp = step === "app";
  if (testBtn) {
    testBtn.hidden = !onReady;
    testBtn.classList.toggle("hidden", !onReady);
    testBtn.disabled = Boolean(state?.apply?.running);
  }
  if (actions) {
    const showPull = Boolean(pullBtn && !pullBtn.hidden);
    const show = onReady || showPull;
    actions.hidden = !show;
    actions.classList.toggle("hidden", !show);
  }
  if (hint) {
    const showHint = onReady;
    hint.hidden = !showHint;
    hint.classList.toggle("hidden", !showHint);
    if (onReady) {
      if (sshLineDead(state?.apply?.error) || sshLineDead(state?.apply?.detail)) {
        hint.textContent = "Resume the pod. Update Connect with its new IP and port.";
      } else if (state?.apply?.error && !state?.apply?.running) {
        hint.textContent = "Pull failed. Open Activity, then retry.";
      } else if (state?.apply?.running) {
        hint.textContent = "Preparing the Site. You can leave this page open.";
      } else if (!state?.hf_token) {
        hint.textContent = "Add a token from huggingface.co/settings/tokens, then pull the missing pack.";
      } else if (packRowMissing()) {
        hint.textContent = "Pack missing. Pull it, then test again.";
      } else if (state?.prove?.ok) {
        hint.textContent = state?.apply?.pull_skipped
          ? "Site already prepared. No pull ran."
          : "Site ready.";
      } else {
        hint.textContent = "Test the connected Site.";
      }
    } else {
      hint.textContent = "";
    }
  }
  if (!card) return;
  card.hidden = !onReady;
  card.classList.toggle("hidden", !onReady);
  if (!onReady) {
    card.innerHTML = "";
    return;
  }
  const checks = state?.prove?.checks || [];
  if (!checks.length) {
    card.innerHTML =
      '<p class="live-hint">Test GPU reads nvidia-smi over the Connect SSH line.</p>';
    return;
  }
  card.innerHTML = checks
    .map(
      (row) =>
        `<div class="prove-row ${row.ok ? "ok" : "bad"}"><span>${escapeHtml(
          row.label || ""
        )}</span><span>${escapeHtml(row.detail || "")}</span></div>`
    )
    .join("");
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
  // Field guidance is grouped under Details to keep the active line quiet.
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
      ["Floors", (plan && plan.floors) || "Idea, Production, Gallery"],
      ["Program", (plan && plan.program) || "Overlap stitch, 8–16 frames"],
      ["Producer", url],
      ["Open", state?.producer_href || ""],
    ];
    fillPlan(rows);
    return;
  }
  const lease = path === "lease" || isWizardLease(path);
  if (step === "prove" && lease) {
    const c = state?.confirm || {};
    const rows = [
      ["SSH", c.ssh],
      ["GPU", c.gpu],
      ["Vault", c.vault],
    ].filter(([, value]) => value);
    if (rows.length) {
      fillPlan(rows);
      return;
    }
  }
  const show = Boolean(plan && lease && step === "connect");
  if (!show) {
    planEl.hidden = true;
    planEl.classList.add("hidden");
    planEl.innerHTML = "";
    return;
  }
  const fabric =
    String(plan.interconnect || "").startsWith("On-package")
      ? "On this GPU"
      : plan.interconnect;
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

function localClusterSketches() {
  return `
    <div class="local-cluster-sketches" aria-label="Local DGX Spark cluster setup sketches">
      <figure class="cluster-sketch">
        <figcaption><span>01</span> CLUSTER FABRIC</figcaption>
        <svg class="cluster-map" viewBox="0 0 620 238" role="img" aria-label="Four DGX Sparks connected from CX-7 port 2 to a MikroTik 200 gigabit fabric switch">
          <g class="cluster-cable cluster-cable-fabric">
            <path d="M140 55 H248 L286 103" />
            <path d="M480 55 H372 L334 103" />
            <path d="M140 183 H248 L286 135" />
            <path d="M480 183 H372 L334 135" />
          </g>
          <g class="spark-box" transform="translate(18 24)">
            <rect width="122" height="62" /><text x="12" y="25">SPARK 01</text><text class="spark-port" x="12" y="45">CX-7 PORT 2</text>
          </g>
          <g class="spark-box" transform="translate(480 24)">
            <rect width="122" height="62" /><text x="12" y="25">SPARK 02</text><text class="spark-port" x="12" y="45">CX-7 PORT 2</text>
          </g>
          <g class="spark-box" transform="translate(18 152)">
            <rect width="122" height="62" /><text x="12" y="25">SPARK 03</text><text class="spark-port" x="12" y="45">CX-7 PORT 2</text>
          </g>
          <g class="spark-box" transform="translate(480 152)">
            <rect width="122" height="62" /><text x="12" y="25">SPARK 04</text><text class="spark-port" x="12" y="45">CX-7 PORT 2</text>
          </g>
          <g class="fabric-box" transform="translate(248 86)">
            <rect width="124" height="66" /><text x="62" y="27">MIKROTIK FABRIC</text><text class="spark-port" x="62" y="47">4 × 200G · ROCE</text>
          </g>
          <text class="cable-label" x="310" y="72">QSFP 200G</text>
        </svg>
      </figure>
      <figure class="cluster-sketch cluster-sketch-wiring">
        <figcaption><span>02</span> TWO SEPARATE NETWORKS</figcaption>
        <div class="connector-map" role="img" aria-label="Each Spark uses CX-7 port 2 for the 200 gigabit fabric and RJ-45 Cat6 for management and vault traffic">
          <div class="connector-source">
            <strong>EACH SPARK</strong>
            <span aria-label="CX-7 PORT 2"><b>CX-7</b><small>PORT 2</small></span>
            <span aria-label="RJ-45 10G"><b>RJ-45</b><small>10G</small></span>
          </div>
          <div class="connector-runs" aria-hidden="true">
            <span class="cluster-cable fabric-run"></span>
            <span class="cluster-cable management-run"></span>
          </div>
          <div class="connector-destinations">
            <span><b>MIKROTIK FABRIC</b><small>QSFP 200G</small></span>
            <span><b>10GBE MANAGEMENT + VAULT</b><small>RJ-45 · CAT6</small></span>
          </div>
        </div>
        <p class="cluster-rule"><b>Never cross the networks.</b> RoCE carries GPU traffic. RJ-45 carries Studio, Sync, and NAS.</p>
      </figure>
    </div>`;
}

function paintComputeChoice(field, answers, interactive) {
  const wrap = document.createElement("div");
  wrap.className = "field compute-field";
  const input = document.createElement("input");
  input.type = "hidden";
  input.name = field.id;
  input.value = answers[field.id] || "";
  wrap.appendChild(input);

  const groups = document.createElement("div");
  groups.className = "compute-groups";
  [
    ["LOCAL", ["local"]],
    ["LEASE", ["dual", "b300", "b200"]],
  ].forEach(([label, options]) => {
    const group = document.createElement("section");
    group.className = "compute-group";
    const cap = document.createElement("p");
    cap.className = "compute-group-label";
    cap.textContent = label;
    group.appendChild(cap);
    const row = document.createElement("div");
    row.className = "choice";
    options.forEach((opt) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.textContent = SITE_LABELS[opt] || opt;
      btn.dataset.site = opt;
      btn.className = input.value === opt ? "on" : "";
      btn.setAttribute("aria-pressed", input.value === opt ? "true" : "false");
      btn.disabled = !interactive;
      row.appendChild(btn);
    });
    group.appendChild(row);
    if (label === "LOCAL") group.insertAdjacentHTML("beforeend", localClusterSketches());
    group.insertAdjacentHTML("beforeend", existingComputeAction(label.toLowerCase()));
    groups.appendChild(group);
  });
  wrap.appendChild(groups);
  return wrap;
}

function existingComputeAction(scope) {
  return `<button type="button" class="existing-compute-action" data-existing-scope="${escapeHtml(scope)}" aria-label="Launch autonomous setup for existing compute"><span class="existing-compute-status" aria-hidden="true"><i></i><small>AGENT READY</small></span><span class="existing-compute-copy"><em>AUTONOMOUS SETUP</em><strong>Connect to Existing Compute</strong><small>Discover · Validate · Open Producer</small></span><b class="existing-compute-arrow" aria-hidden="true">↗</b></button>`;
}

function paintChoice(field, answers, interactive) {
  if (field.id === "site") return paintComputeChoice(field, answers, interactive);
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
  if (field.source) {
    const src = document.createElement("p");
    src.className = "field-source";
    const match = String(field.source).match(/^(https:\/\/\S+)(\s.*)?$/);
    if (match) {
      const link = document.createElement("a");
      link.href = match[1];
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.textContent = match[1].replace(/^https:\/\//, "");
      src.appendChild(link);
      if (match[2]) src.appendChild(document.createTextNode(match[2]));
    } else {
      src.textContent = field.source;
    }
    lab.appendChild(src);
  }
  let input;
  const recorded = answers[field.id];
  const prefill = field.prefill !== false;
  const shown = recorded || (prefill ? field.default || "" : "");
  if (field.kind === "select") {
    input = document.createElement("select");
    (field.options || []).forEach((opt) => {
      const o = document.createElement("option");
      o.value = opt;
      o.textContent = opt || "Choose";
      input.appendChild(o);
    });
    input.name = field.id;
    input.value = shown;
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
    input.rows = isSingleGpu(currentPath()) ? 2 : 4;
    input.placeholder = field.placeholder || "";
    input.name = field.id;
    input.value = shown;
    input.disabled = !interactive;
    lab.appendChild(input);
    wrap.appendChild(lab);
    appendHelp(wrap, field, "");
    return wrap;
  }
  input = document.createElement("input");
  input.type = field.kind === "password" || field.id === "hf_token" ? "password" : "text";
  input.placeholder =
    field.id === "hf_token" && state?.hf_token
      ? "Token is set. Paste a new one to replace."
      : field.placeholder || "";
  input.name = field.id;
  input.value = field.kind === "password" || field.id === "hf_token" ? "" : shown;
  input.autocomplete = field.id === "hf_token" ? "off" : "on";
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
  setText(
    learnTitle,
    item.step_id === "path" ? "COMPUTE" : (stepMeta && stepMeta.name) || item.step_id || "Site"
  );
  setText(learnWhy, stepInstruction(item));
  try {
    paintAbout(item);
  } catch (err) {
    console.error(err);
  }
  try {
    paintGuide(item);
  } catch (err) {
    console.error(err);
  }
  try {
    paintPlan();
  } catch (err) {
    console.error(err);
  }
  try {
    paintConfirm();
  } catch (err) {
    console.error(err);
  }
  try {
    paintProve();
  } catch (err) {
    console.error(err);
  }
  try {
    paintSiteRefresh();
  } catch (err) {
    console.error(err);
  }

  try {
    const frag = document.createDocumentFragment();
    (item.fields || []).forEach((field) => {
      if (item.step_id === "path" && ["provider", "signer"].includes(field.id)) return;
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
      note.textContent = "Diony checks first. It pulls only missing models.";
      frag.appendChild(note);
    }
    formEl.replaceChildren(frag);
    paintConfirm();
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
    const label = step.id === "path" ? "Compute" : step.name;
    btn.innerHTML = `<span>${pad(i + 1)}</span>${label}`;
    btn.addEventListener("click", () => openLine(step.id));
    li.appendChild(btn);
    railEl.appendChild(li);
  });
}

async function openLine(stepId) {
  const step = state.steps.find((s) => s.id === stepId);
  if (!step || step.status === "pending") return;
  viewing = stepId;
  const res = await fetch(`/api/lessons/${stepId}`, { cache: "no-store" });
  const payload = await res.json();
  const loaded = res.ok && Array.isArray(payload.fields) ? payload : state.lesson;
  if (!loaded || !Array.isArray(loaded.fields) || loaded.step_id !== stepId) {
    errEl.textContent = payload.detail || "Could not load this line.";
    return;
  }
  lesson = loaded;
  const interactive = lineInteractive(step);
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
  const vaultLine = state.vault_export
    ? `${state.vault_export} → ${state.vault || "/diony/vault"}`
    : vault.nas_ip
    ? `${vault.nas_ip} → ${vault.export || "/diony/vault"}`
    : sitePack.vault || "/diony/vault";
  return [
    ["Site", SITE_LABELS[report.compute_path] || report.compute_path],
    ["Profile", report.sku],
    ["Pack", pack.label || (wb.weights || {}).label],
    ["Brain", (wb.brain_smoke || {}).recipe],
    ["Host", (sitePack.nodes || report.nodes || []).join(" · ")],
    ["Vault", vaultLine],
    ["Floors", "Idea, Production, Gallery"],
    ["Producer", state.producer_url],
    ["Open", state.producer_href],
  ];
}

function showComplete() {
  prepEl.classList.add("hidden");
  passBtn.classList.add("hidden");
  doneEl.classList.remove("hidden");
  doneEl.hidden = false;
  const report = state.report;
  document.getElementById("done-mandate").textContent = state.mandate;
  const mode = report.dry_run ? "dry-run" : "live";
  document.getElementById("done-meta").textContent =
    `${SITE_LABELS[report.compute_path] || report.compute_path || "local"} · ${report.sku} · ${mode}`;
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
  const note = document.getElementById("done-open-note");
  if (note) {
    note.textContent = report.dry_run
      ? "Producer is http://producer.diony.local. Start diony-producer on this machine if it is not already up."
      : "Producer is http://producer.diony.local. Sign wrote site.json to /diony/vault.";
  }
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
    siteLine.textContent = "";
    return;
  }
  siteLine.textContent = SITE_LABELS[state.compute_path] || state.compute_path;
}

function paintSiteRefresh() {
  const row = document.getElementById("site-refresh-row");
  if (!row) return;
  const onSite = !state?.complete && (viewing || state?.active_step) === "path";
  row.hidden = !onSite;
  row.classList.toggle("hidden", !onSite);
}

async function refreshWizard() {
  stopApplyPoll();
  loomOn = { 1: false, 2: false, 3: false, 4: false };
  wipeSheet();
  paintSiteRefresh();
  if (statusEl) statusEl.textContent = "Refreshing Studio…";
  const btn = document.getElementById("site-refresh");
  if (btn) btn.disabled = true;
  try {
    await fetch("/api/reset", { method: "POST", cache: "no-store" });
  } catch {
    /* reload anyway */
  }
  window.location.replace("/?r=" + Date.now());
}

function isLockStep(stepId) {
  return stepId === "producer";
}

function lineInteractive(step) {
  if (!step || step.status === "pending") return false;
  if (step.status === "active" || step.status === "failed") return true;
  return step.id === "connect";
}

function sshLineDead(text) {
  const blob = String(text || "").toLowerCase();
  return (
    blob.includes("connection refused") ||
    blob.includes("not accepting ssh") ||
    blob.includes("timed out") ||
    blob.includes("no route to host")
  );
}

function paintPass() {
  if (!passBtn) return;
  if (document.body.dataset.provision === "active") {
    passBtn.classList.add("hidden");
    passBtn.hidden = true;
    passBtn.disabled = true;
    return;
  }
  const stepId = viewing || state?.active_step;
  const computeStep = stepId === "path";
  const last = isLockStep(stepId);
  const action = last ? "Sign" : stepId === "app" ? "Check Site" : "Continue";
  const stick = passBtn.querySelector(".clap-stick");
  const slate = passBtn.querySelector(".clap-slate");
  passBtn.classList.toggle("clap", last);
  passBtn.classList.toggle("continue", !last);
  passBtn.classList.toggle("hidden", computeStep);
  passBtn.hidden = computeStep;
  passBtn.setAttribute("aria-label", action);
  if (stick) stick.hidden = !last;
  if (slate) {
    if (stepId === "app") slate.textContent = "Check Site";
    else slate.textContent = last ? "Sign" : "Continue";
  }
  const step = (state?.steps || []).find((s) => s.id === stepId);
  const pulling = Boolean(state?.apply?.running) && stepId === "prove";
  passBtn.disabled = !lineInteractive(step) || pulling;
}

function render() {
  try {
    syncStudioEntry();
    const previousStep = document.body.dataset.step || "";
    const nextStep = state?.complete ? "complete" : state?.active_step || "path";
    document.body.dataset.step = nextStep;
    triggerStudioTransition(previousStep, nextStep);
    paintSite();
    paintSiteRefresh();
    if (modeLine) {
      const ssh = state.confirm?.ssh || "";
      const remote = Boolean(state.prove?.live) || /\d+\.\d+\.\d+\.\d+/.test(ssh);
      if (remote) {
        modeLine.textContent = "SSH to this GPU. Check Site uses this line.";
      } else if (state.dry_run) {
        modeLine.textContent = "Dry-run. No fabric mutation.";
      } else {
        modeLine.textContent = "Live. App checks first and pulls only what is missing.";
      }
    }
    renderRail();
    if (state.complete) {
      stopApplyPoll();
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
    paintApply(state.apply);
    if (state.apply?.running) pollApply();
    paintPass();
    const cached = state.lesson && state.lesson.step_id === stepId ? state.lesson : null;
    if (cached) {
      const step = state.steps.find((s) => s.id === stepId);
      const interactive = lineInteractive(step);
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

function retryStateBtn() {
  return document.getElementById("retry-state");
}

function showStateFail(msg) {
  if (errEl) errEl.textContent = msg || "Could not load Studio state. Retry.";
  if (passBtn) {
    passBtn.classList.add("hidden");
    passBtn.hidden = true;
  }
  const retry = retryStateBtn();
  if (retry) {
    retry.hidden = false;
    retry.classList.remove("hidden");
  }
}

function hideStateFail() {
  const retry = retryStateBtn();
  if (retry) {
    retry.hidden = true;
    retry.classList.add("hidden");
  }
  if (passBtn) {
    passBtn.classList.remove("hidden");
    passBtn.hidden = false;
  }
}

async function load() {
  try {
    const res = await fetch("/api/state", { cache: "no-store" });
    let body = null;
    try {
      body = await res.json();
    } catch {
      body = null;
    }
    if (!res.ok || !body || !Array.isArray(body.steps)) {
      showStateFail((body && body.error) || "Could not load Studio state. Retry.");
      return;
    }
    hideStateFail();
    state = body;
    viewing = state.active_step;
    render();
    try {
      provision = await fetch("/api/provision", { cache: "no-store" }).then((r) => r.json());
      paintProvisioning(provision);
      if (provision?.status === "working") pollProvisioning();
    } catch {
      /* The normal Studio walk remains available if provider state cannot load. */
    }
    if (body.error && statusEl) {
      statusEl.textContent = "This line loaded from a fallback. Continue works.";
    }
  } catch (err) {
    console.error(err);
    showStateFail("Could not load Studio state. Retry.");
  }
}

async function saveQuiet() {
  const stepId = viewing || state?.active_step;
  if (!stepId) return;
  await fetch(`/api/steps/${stepId}/save`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ answers: collect() }),
  });
}

let applyTimer = null;
let applyPollGen = 0;

function stopApplyPoll() {
  applyPollGen += 1;
  if (applyTimer) {
    clearInterval(applyTimer);
    applyTimer = null;
  }
}

function wipeSheet() {
  viewing = null;
  lesson = null;
  if (errEl) errEl.textContent = "";
  if (statusEl) statusEl.textContent = "";
  if (producerEl) producerEl.textContent = "";
  const logEl = document.getElementById("apply-log");
  const activityEl = document.getElementById("activity");
  if (logEl) {
    logEl.textContent = "";
    logEl.hidden = true;
    logEl.classList.add("hidden");
  }
  if (activityEl) {
    activityEl.hidden = true;
    activityEl.classList.add("hidden");
    activityEl.open = false;
  }
  ["confirm-line", "ready-hint"].forEach((id) => {
    const el = document.getElementById(id);
    if (!el) return;
    el.textContent = "";
    el.hidden = true;
    el.classList.add("hidden");
  });
  const progress = document.getElementById("pack-progress");
  if (progress) {
    progress.hidden = true;
    progress.classList.add("hidden");
  }
  const card = document.getElementById("prove-card");
  if (card) {
    card.innerHTML = "";
    card.hidden = true;
    card.classList.add("hidden");
  }
  const actions = document.getElementById("ready-actions");
  if (actions) {
    actions.hidden = true;
    actions.classList.add("hidden");
  }
  if (prepEl) prepEl.classList.remove("hidden");
  if (doneEl) {
    doneEl.classList.add("hidden");
    doneEl.hidden = true;
  }
  if (passBtn) {
    passBtn.classList.remove("hidden");
    passBtn.hidden = false;
  }
}

function paintApply(apply) {
  const logEl = document.getElementById("apply-log");
  const activityEl = document.getElementById("activity");
  const retryBtn = document.getElementById("retry-apply");
  if (!apply || !logEl) return;
  const step = viewing || state?.active_step;
  const here = step === "app" || step === "prove";
  if (!here) {
    logEl.hidden = true;
    logEl.classList.add("hidden");
    if (activityEl) {
      activityEl.hidden = true;
      activityEl.classList.add("hidden");
      activityEl.open = false;
    }
    if (retryBtn) {
      retryBtn.classList.add("hidden");
      retryBtn.hidden = true;
    }
    if (errEl && apply.error && errEl.textContent === apply.error) {
      errEl.textContent = "";
    }
    paintPackProgress(apply);
    return;
  }
  const lines = apply.log || [];
  const on = Boolean(apply.running || apply.error || ((apply.live || step === "prove") && lines.length));
  logEl.hidden = !on;
  logEl.classList.toggle("hidden", !on);
  if (activityEl) {
    activityEl.hidden = !on;
    activityEl.classList.toggle("hidden", !on);
    if (!on) activityEl.open = false;
  }
  if (on) {
    logEl.textContent = lines.join("\n") || apply.detail || apply.error || "Pulling pack…";
  }
  paintPackProgress(apply);
  if (apply.running && statusEl) {
    statusEl.textContent = apply.detail || "Pulling the pack onto this GPU…";
  }
  if (apply.error && errEl && !errEl.textContent) {
    errEl.textContent = apply.error;
  }
  const packGone = packRowMissing();
  const canPull =
    !apply.running &&
    (state?.compute_path === "dual" || isSingleGpu(state?.compute_path)) &&
    (step === "prove" ? packGone || Boolean(apply.error) : step === "app" && Boolean(apply.error));
  if (retryBtn) {
    retryBtn.textContent = step === "prove" ? "Pull pack" : "Retry pack";
    retryBtn.classList.toggle("hidden", !canPull);
    retryBtn.hidden = !canPull;
    retryBtn.disabled = Boolean(apply.running);
  }
}

async function pollApply() {
  if (applyTimer) return;
  const gen = applyPollGen;
  const tick = async () => {
    if (gen !== applyPollGen) return;
    try {
      const apply = await fetch("/api/apply").then((r) => r.json());
      if (gen !== applyPollGen) return;
      if (state) state.apply = apply;
      paintApply(apply);
      paintProve();
      paintPass();
      if (!apply.running) {
        clearInterval(applyTimer);
        applyTimer = null;
        if (apply.ok && state?.active_step === "app") {
          act("pass");
        } else if (apply.ok && state?.active_step === "prove") {
          testGpu();
        } else if (apply.error && statusEl) {
          statusEl.textContent = "";
        }
      }
    } catch {
      if (gen !== applyPollGen) return;
      clearInterval(applyTimer);
      applyTimer = null;
    }
  };
  applyTimer = setInterval(tick, 3000);
  tick();
}

function openComputeConfirmation(site = currentSite()) {
  if (!computeConfirm || !site || !COMPUTE_META[site]) return;
  const meta = COMPUTE_META[site];
  setText(document.getElementById("compute-confirm-choice"), SITE_LABELS[site]);
  const facts = document.getElementById("compute-confirm-facts");
  if (facts) {
    facts.innerHTML = [
      ["Provider", meta.provider],
      ["Compute", meta.hardware],
    ]
      .map(
        ([key, value]) =>
          `<div><dt>${escapeHtml(key)}</dt><dd>${escapeHtml(value)}</dd></div>`
      )
      .join("");
  }
  setText(document.getElementById("compute-confirm-note"), meta.handoff);
  computeConfirm.dataset.site = site;
  if (!computeConfirm.open) computeConfirm.showModal();
}

function closeComputeConfirmation() {
  if (computeConfirm?.open) computeConfirm.close();
}

function provisionField(name) {
  const existingHosts =
    provision?.mode === "existing"
      ? provision.scope === "local"
        ? ["Four SSH hosts", "textarea", "spark-01\nspark-02\nspark-03\nspark-04"]
        : [
            "SSH host(s)",
            "textarea",
            "spark-01 or 198.13.252.68",
            "Manual entry: from root@[host-name] -p [port], enter only [host-name]. Add a second host on a new line.",
          ]
      : ["Two SSH hosts", "textarea", "spark-01.example\nspark-02.example"];
  const config = {
    provider: ["Provider", "text", "Runpod, Enverge, or another site owner"],
    signer: ["Lease owner", "text", "Name responsible for this lease"],
    provider_token: ["Runpod API key", "password", "Pod read/write key"],
    hf_token: ["Hugging Face token", "password", "hf_…"],
    hosts: existingHosts,
    ssh_user: ["SSH user", "text", "root"],
    ssh_port: ["SSH port", "text", "22"],
    nas_ip: ["Private vault override", "text", "nfs://10.0.8.12/diony-vault"],
  }[name];
  if (!config) return "";
  const [label, type, placeholder, hint = ""] = config;
  const wide = ["hosts", "nas_ip"].includes(name) ? " wide" : "";
  const control =
    type === "textarea"
      ? `<textarea name="${escapeHtml(name)}" placeholder="${escapeHtml(placeholder)}" autocomplete="off"></textarea>`
      : `<input type="${escapeHtml(type)}" name="${escapeHtml(name)}" placeholder="${escapeHtml(
          placeholder
        )}" autocomplete="off" />`;
  const guidance = hint
    ? `<small class="provision-field-hint">${escapeHtml(hint)}</small>`
    : "";
  return `<label class="${wide.trim()}"><span>${escapeHtml(label)}</span>${control}${guidance}</label>`;
}

function runpodCommandEntry(next) {
  if (
    next?.mode !== "existing" ||
    next?.scope !== "lease" ||
    next?.checkpoint !== "existing_connection"
  ) {
    return "";
  }
  return `<section class="runpod-command-entry" aria-labelledby="runpod-command-title"><div class="runpod-command-head"><span>STREAMLINED · RUNPOD</span><strong id="runpod-command-title">Runpod quick connect</strong><small>Copy SSH over exposed TCP in Runpod. Paste the full command here.</small></div><div class="runpod-command-control"><b aria-hidden="true">$</b><input id="runpod-ssh-command" type="text" placeholder="ssh root@198.13.252.71 -p 47803 -i ~/.ssh/id_ed25519" autocomplete="off" autocapitalize="none" spellcheck="false" aria-describedby="runpod-command-status" /><button type="button" class="runpod-command-action" data-parse-runpod>Use command</button></div><input type="hidden" name="ssh_identity_file" value="" /><p id="runpod-command-status" class="runpod-command-status" role="status" aria-live="polite"></p></section><p class="runpod-manual-divider"><span>OR ENTER MANUALLY</span></p>`;
}

async function applyRunpodSSHCommand() {
  const input = document.getElementById("runpod-ssh-command");
  const button = provisionForm?.querySelector("[data-parse-runpod]");
  const status = document.getElementById("runpod-command-status");
  const command = String(input?.value || "").trim();
  if (!command) {
    if (status) {
      status.textContent = "Paste SSH command from Runpod first.";
      status.classList.add("is-error");
    }
    input?.focus();
    return;
  }
  if (button) button.disabled = true;
  if (status) {
    status.textContent = "Reading connection…";
    status.classList.remove("is-error");
  }
  try {
    const response = await fetch("/api/ssh/parse", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ command }),
      cache: "no-store",
    });
    const body = await response.json();
    if (!response.ok) throw new Error(body.detail || "Runpod SSH command was not recognized.");
    const values = { provider: "Runpod", ...(body.connection || {}) };
    Object.entries(values).forEach(([name, value]) => {
      const field = provisionForm?.querySelector(`[name="${name}"]`);
      if (field) field.value = String(value || "");
    });
    if (status) {
      const signer = provisionForm?.querySelector('[name="signer"]')?.value.trim();
      status.textContent = signer
        ? "Connection ready. Inspect compute when ready."
        : "Connection ready. Add the lease owner, then inspect compute.";
    }
    provisionForm?.querySelector('[name="signer"]')?.focus();
  } catch (error) {
    if (status) {
      status.textContent = error.message || "Runpod SSH command was not recognized.";
      status.classList.add("is-error");
    }
  } finally {
    if (button) button.disabled = false;
  }
}

function provisionVaultDiscovery(next) {
  if (next?.mode !== "existing") return "";
  const source = String(next?.connection?.nas_ip || next?.inspection?.vault_source || "").trim();
  const stateLabel = source ? "FOUND" : "AUTO";
  const headline = source ? source : "Identified during read-only inspection";
  const detail = source
    ? "Private storage is attached to this Studio connection."
    : "Diony identifies private storage through this SSH connection. No vault address is required.";
  return `<aside class="vault-discovery-card${source ? " is-found" : ""}" role="status" aria-live="polite"><span class="vault-discovery-signal" aria-hidden="true"><i></i><small>${stateLabel}</small></span><span class="vault-discovery-copy"><em>Vault auto-discovery</em><strong>${escapeHtml(headline)}</strong><small>${escapeHtml(detail)}</small></span><span class="vault-discovery-order" aria-hidden="true"><b>/diony/vault</b><b>NFS</b><b>/workspace</b></span></aside>`;
}

function paintProvisioning(next) {
  provision = next;
  const setupInProgress = next.status === "working" || next.status === "ready";
  const setupComplete = next.status === "complete" && next.mode === "existing";
  const active = next && (setupComplete || !["idle", "complete"].includes(next.status));
  if (!provisionPanel) return;
  provisionPanel.hidden = !active;
  provisionPanel.classList.toggle("hidden", !active);
  document.body.dataset.provision = active ? "active" : "idle";
  if (prepEl && active) prepEl.classList.remove("hidden");
  if (doneEl && active) {
    doneEl.hidden = true;
    doneEl.classList.add("hidden");
  }
  if (formEl && active) {
    formEl.hidden = true;
    formEl.classList.add("hidden");
  }
  if (passBtn && active) {
    passBtn.hidden = true;
    passBtn.classList.add("hidden");
  }
  const refresh = document.getElementById("site-refresh-row");
  if (refresh && active) {
    refresh.hidden = true;
    refresh.classList.add("hidden");
  }
  if (!active) return;
  const well = document.querySelector(".well");
  if (well) well.scrollTop = 0;

  setText(document.getElementById("provision-provider"), next.provider || "Lease");
  setText(document.getElementById("provision-title"), next.headline || "Preparing compute");
  let detail = next.detail || "";
  if (next.quote?.hourly != null) {
    detail += ` Live price: $${Number(next.quote.hourly).toFixed(2)}/hour · ${next.quote.stock || "Unknown"} stock.`;
  }
  setText(document.getElementById("provision-detail"), detail);
  if (provisionError) provisionError.textContent = next.error || "";

  const timeline = document.getElementById("provision-timeline");
  if (timeline) {
    timeline.innerHTML = (next.events || [])
      .map(
        (event) =>
          `<li class="${escapeHtml(event.status || "")}"><strong>${escapeHtml(
            event.label || ""
          )}</strong><span>${escapeHtml(event.detail || "")}</span></li>`
      )
      .join("");
  }
  if (provisionForm) {
    provisionForm.innerHTML =
      runpodCommandEntry(next) +
      (next.required_inputs || []).map(provisionField).join("") +
      provisionVaultDiscovery(next);
  }
  const link = document.getElementById("provision-provider-link");
  if (link) {
    link.hidden = !next.action_url;
    link.href = next.action_url || "#";
    link.textContent = next.action_label || "Open provider";
  }
  if (provisionProgress) {
    provisionProgress.hidden = !setupInProgress;
    provisionProgress.classList.toggle("hidden", !setupInProgress);
    const progressDetail = next.detail || "Diony is completing this site.";
    provisionProgress.setAttribute("aria-valuetext", progressDetail);
    setText(document.getElementById("provision-progress-detail"), progressDetail);
  }
  if (provisionContinue) {
    provisionContinue.hidden = setupInProgress;
    provisionContinue.classList.toggle("hidden", setupInProgress);
    provisionContinue.disabled = setupInProgress;
    provisionContinue.classList.toggle("setup-complete", setupComplete);
    const inspecting = next.checkpoint === "existing_connection";
    provisionContinue.classList.toggle("inspect-compute-button", inspecting);
    provisionContinue.setAttribute(
      "aria-label",
      setupComplete
        ? "Continue to Producer"
        : inspecting
          ? "Inspect compute through a read-only SSH connection"
          : "Continue setup"
    );
    if (setupComplete) {
      provisionContinue.textContent = "Continue";
    } else if (inspecting) {
      provisionContinue.innerHTML = `<span><small>READ-ONLY SSH</small><strong>Inspect Compute</strong></span><b aria-hidden="true">→</b>`;
    } else {
      provisionContinue.textContent =
        next.checkpoint === "repair_credentials"
          ? "Repair and validate"
          : next.checkpoint === "validating_existing"
            ? "Retry validation"
            : "Continue";
    }
  }
  if (next.status === "ready" && !provisionAdopting) adoptProvisionedSite();
}

function stopProvisionPoll() {
  if (provisionTimer) {
    clearInterval(provisionTimer);
    provisionTimer = null;
  }
}

function pollProvisioning() {
  if (provisionTimer) return;
  const tick = async () => {
    try {
      const next = await fetch("/api/provision", { cache: "no-store" }).then((r) => r.json());
      paintProvisioning(next);
      if (next.status !== "working") stopProvisionPoll();
    } catch {
      if (provisionError) provisionError.textContent = "Provider status is temporarily unavailable. Diony will retry.";
    }
  };
  provisionTimer = setInterval(tick, 2500);
  tick();
}

async function startProvisioning(path) {
  stopProvisionPoll();
  if (provisionError) provisionError.textContent = "";
  const res = await fetch("/api/provision/start", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ path }),
  });
  const body = await res.json();
  if (!res.ok) {
    if (errEl) errEl.textContent = body.detail || "Could not start the setup agent.";
    return;
  }
  paintProvisioning(body);
}

async function startExistingCompute(scope) {
  stopProvisionPoll();
  if (provisionError) provisionError.textContent = "";
  const res = await fetch("/api/provision/existing/start", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ scope }),
  });
  const body = await res.json();
  if (!res.ok) {
    if (errEl) errEl.textContent = body.detail || "Could not start existing-compute discovery.";
    return;
  }
  paintProvisioning(body);
}

function provisionValues() {
  const values = {};
  provisionForm?.querySelectorAll("[name]").forEach((input) => {
    values[input.name] = input.value;
  });
  return values;
}

async function advanceProvisioning() {
  if (!provision) return;
  if (provision?.status === "complete" && provision.handoff_url) {
    window.location.assign(provision.handoff_url);
    return;
  }
  if (provision.checkpoint === "validating_existing") {
    provisionContinue.disabled = true;
    if (provisionError) provisionError.textContent = "Rechecking the corrected site…";
    pollExistingValidation();
    return;
  }
  const action =
    provision.checkpoint === "existing_connection"
      ? "inspect_existing"
      : provision.checkpoint === "repair_credentials"
        ? "repair_existing"
        : provision.checkpoint === "provider_account"
      ? provision.provider === "Runpod"
        ? "provider_ready"
        : "allocation_requested"
      : provision.checkpoint === "deploy_confirmation"
        ? "deploy"
        : provision.checkpoint === "provider_allocation"
          ? "connection_ready"
          : "";
  if (!action) return;
  provisionContinue.disabled = true;
  if (provisionError) provisionError.textContent = "";
  const res = await fetch("/api/provision/advance", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ action, ...provisionValues() }),
  });
  const body = await res.json();
  provisionContinue.disabled = false;
  if (!res.ok) {
    const detail = body.detail || "This checkpoint needs attention.";
    try {
      const current = await fetch("/api/provision", { cache: "no-store" }).then((r) => r.json());
      paintProvisioning(current);
    } catch {
      /* Keep the current checkpoint visible. */
    }
    if (provisionError) provisionError.textContent = detail;
    return;
  }
  paintProvisioning(body);
  if (body.status === "working") pollProvisioning();
}

async function adoptProvisionedSite() {
  if (provisionAdopting) return;
  provisionAdopting = true;
  if (provisionError) provisionError.textContent = "Recording the provider connection in Studio…";
  try {
    const res = await fetch("/api/provision/adopt", { method: "POST", cache: "no-store" });
    const body = await res.json();
    if (!res.ok) {
      if (provisionError) provisionError.textContent = body.detail || "Studio could not adopt this site.";
      return;
    }
    provision = body.provision || { status: "complete" };
    state = body;
    viewing = state.active_step;
    if (provision.mode === "existing" && provision.status === "working") {
      render();
      paintProvisioning(provision);
      pollExistingValidation();
      return;
    }
    stopProvisionPoll();
    if (provisionPanel) {
      provisionPanel.hidden = true;
      provisionPanel.classList.add("hidden");
    }
    if (formEl) formEl.hidden = false;
    if (formEl) formEl.classList.remove("hidden");
    document.body.dataset.provision = "idle";
    render();
    if (body.apply?.running) pollApply();
  } finally {
    provisionAdopting = false;
  }
}

function pollExistingValidation() {
  stopProvisionPoll();
  const tick = async () => {
    if (existingFinalizeBusy) return;
    existingFinalizeBusy = true;
    try {
      const res = await fetch("/api/provision/existing/finalize", {
        method: "POST",
        cache: "no-store",
      });
      const body = await res.json();
      if (!res.ok) throw new Error(body.detail || "Existing compute validation failed.");
      state = body;
      provision = body.provision || provision;
      paintProvisioning(provision);
      if (provision.status === "complete") {
        stopProvisionPoll();
      } else if (provision.status === "attention") {
        stopProvisionPoll();
      }
    } catch (error) {
      stopProvisionPoll();
      if (provisionError) provisionError.textContent = error.message || "Existing compute validation failed.";
    } finally {
      existingFinalizeBusy = false;
    }
  };
  provisionTimer = window.setInterval(tick, 2500);
  tick();
}

async function act(kind) {
  errEl.textContent = "";
  clearFieldErrors();
  if (kind === "reset") {
    await refreshWizard();
    return;
  }
  const stepId = viewing || state.active_step;
  if (!stepId) return;
  const answers = collect();
  const res = await fetch(`/api/steps/${stepId}/pass`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ answers }),
  });
  const body = await res.json();
  if (!res.ok) {
    const detail =
      typeof body.detail === "string"
        ? body.detail
        : (body.detail && body.detail.message) || "";
    if (String(detail).toLowerCase().includes("not active")) {
      await load();
      return;
    }
    markFieldError(detail);
    errEl.textContent = detail || (isLockStep(state.active_step) ? "Could not sign." : "Could not continue.");
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
  const existing = event.target.closest("[data-existing-scope]");
  if (existing && formEl.contains(existing)) {
    event.preventDefault();
    startExistingCompute(existing.getAttribute("data-existing-scope"));
    return;
  }
  const btn = event.target.closest("[data-site]");
  if (!btn || !formEl.contains(btn)) return;
  event.preventDefault();
  const site = btn.getAttribute("data-site");
  pickSite(site, btn);
  openComputeConfirmation(site);
});
formEl?.addEventListener("change", saveQuiet);
formEl?.addEventListener("input", (event) => {
  if (!["hosts", "ssh_user", "ssh_port", "nas_ip"].includes(event.target?.name)) return;
  paintConfirm();
});
formEl?.addEventListener("keydown", (event) => {
  if (event.key !== "Enter" || event.shiftKey) return;
  if (event.target && event.target.tagName === "TEXTAREA") return;
  event.preventDefault();
  if ((viewing || state?.active_step) === "path") openComputeConfirmation();
  else act("pass");
});
passBtn?.addEventListener("click", () => {
  if ((viewing || state?.active_step) === "path") {
    openComputeConfirmation();
    return;
  }
  act("pass");
});
document.getElementById("compute-confirm-cancel")?.addEventListener("click", closeComputeConfirmation);
document.getElementById("compute-confirm-continue")?.addEventListener("click", async () => {
  const site = computeConfirm?.dataset.site || currentSite();
  closeComputeConfirmation();
  if (site === "local") {
    await act("pass");
  } else {
    await startProvisioning(site);
  }
});
provisionContinue?.addEventListener("click", advanceProvisioning);
provisionForm?.addEventListener("click", (event) => {
  if (!event.target.closest("[data-parse-runpod]")) return;
  event.preventDefault();
  applyRunpodSSHCommand();
});
provisionForm?.addEventListener("keydown", (event) => {
  if (event.key !== "Enter" || event.target?.id !== "runpod-ssh-command") return;
  event.preventDefault();
  applyRunpodSSHCommand();
});
retryStateBtn()?.addEventListener("click", () => {
  if (errEl) errEl.textContent = "";
  load();
});
document.getElementById("site-refresh")?.addEventListener("click", () => refreshWizard());
document.getElementById("start-over")?.addEventListener("click", () => {
  sessionStorage.removeItem("diony_studio_entry");
  stopProvisionPoll();
  act("reset");
});
document.getElementById("reset-done")?.addEventListener("click", () => {
  sessionStorage.removeItem("diony_studio_entry");
  stopProvisionPoll();
  act("reset");
});
document.getElementById("retry-apply")?.addEventListener("click", async () => {
  errEl.textContent = "";
  await saveQuiet();
  if (statusEl) statusEl.textContent = "Pulling pack…";
  const res = await fetch("/api/apply/retry", { method: "POST", cache: "no-store" });
  const body = await res.json();
  if (!res.ok) {
    if (statusEl) statusEl.textContent = "";
    errEl.textContent = body.detail || "Could not pull pack.";
    return;
  }
  state = body;
  render();
  if (body.apply?.running) pollApply();
});
async function testGpu() {
  errEl.textContent = "";
  if (statusEl) statusEl.textContent = "Testing GPU…";
  const res = await fetch("/api/prove", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ scope: "gpu" }),
    cache: "no-store",
  });
  let body = null;
  try {
    body = await res.json();
  } catch {
    body = null;
  }
  if (!res.ok) {
    if (statusEl) statusEl.textContent = "";
    errEl.textContent = (body && body.detail) || "Could not test GPU.";
    return;
  }
  state = body;
  if (statusEl) {
    statusEl.textContent = body.prove?.ok
      ? "GPU is ready."
      : packRowMissing()
        ? "Pack is missing. Pull pack, then Test GPU."
        : "Fix the red row, then Test GPU.";
  }
  render();
}
document.getElementById("test-gpu")?.addEventListener("click", () => testGpu());
window.addEventListener("pageshow", (event) => {
  if (event.persisted) load();
});

const studioEntryMotion = Array.from(document.querySelectorAll(".entry-motion"));

function resetStudioEntryMotion(motion) {
  const video = motion.querySelector(".entry-motion-video");
  if (!video) return;
  video.pause();
  try {
    video.currentTime = 0;
  } catch (_) {
    // The first-frame still remains visible if media metadata is not ready yet.
  }
  motion.dataset.motionState = "still";
}

function playStudioEntryMotionOnce() {
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  studioEntryMotion.forEach((motion) => {
    if (motion.dataset.motionPlayed === "true") return;
    motion.dataset.motionPlayed = "true";
    const video = motion.querySelector(".entry-motion-video");
    if (!video || reduceMotion) {
      resetStudioEntryMotion(motion);
      return;
    }
    video.addEventListener("ended", () => resetStudioEntryMotion(motion), { once: true });
    resetStudioEntryMotion(motion);
    motion.dataset.motionState = "playing";
    const playback = video.play();
    if (playback) playback.catch(() => resetStudioEntryMotion(motion));
  });
}

function showStudioEntryChoice() {
  document.body.dataset.entry = "choice";
  document.getElementById("studio-intro")?.setAttribute("hidden", "");
  document.getElementById("studio-entry")?.removeAttribute("hidden");
  requestAnimationFrame(playStudioEntryMotionOnce);
}

function enterStudioFlow() {
  sessionStorage.setItem("diony_studio_intro", "entered");
  showStudioEntryChoice();
  triggerStudioTransition("intro", "entry-choice");
  document.getElementById("studio-entry-title")?.focus({ preventScroll: true });
}

function startNewStudioEntry() {
  sessionStorage.setItem("diony_studio_entry", "start");
  document.body.dataset.entry = "start";
  document.getElementById("studio-entry")?.setAttribute("hidden", "");
  triggerStudioTransition("entry-choice", "path");
}

function syncStudioEntry() {
  const intro = document.getElementById("studio-intro");
  const entry = document.getElementById("studio-entry");
  if (state?.complete) {
    document.body.dataset.entry = "signed";
    intro?.setAttribute("hidden", "");
    entry?.setAttribute("hidden", "");
    return;
  }
  if (sessionStorage.getItem("diony_studio_entry") === "start") {
    document.body.dataset.entry = "start";
    intro?.setAttribute("hidden", "");
    entry?.setAttribute("hidden", "");
    return;
  }
  if (sessionStorage.getItem("diony_studio_intro") !== "entered") {
    document.body.dataset.entry = "intro";
    entry?.setAttribute("hidden", "");
    intro?.removeAttribute("hidden");
    return;
  }
  showStudioEntryChoice();
}

function handoffToProducer(href) {
  if (!href) return;
  stopApplyPoll();
  document.body.dataset.handoff = "producer";
  window.location.replace(href);
}

document.getElementById("enter-studio")?.addEventListener("click", enterStudioFlow);
document.addEventListener("keydown", (event) => {
  if (event.key !== "Enter" || document.body.dataset.entry !== "intro") return;
  if (event.metaKey || event.ctrlKey || event.altKey || event.shiftKey) return;
  if (event.target?.closest?.("button, a, input, select, textarea, summary")) return;
  event.preventDefault();
  enterStudioFlow();
});
document.getElementById("start-studio")?.addEventListener("click", startNewStudioEntry);
document.getElementById("join-existing-studio")?.addEventListener("click", () => {
  handoffToProducer("http://producer.diony.local/#join");
});
document.addEventListener("click", (event) => {
  const link = event.target?.closest?.('a[href^="http://producer.diony.local"]');
  if (!link) return;
  event.preventDefault();
  handoffToProducer(link.href);
});
window.addEventListener("pagehide", stopApplyPoll);
window.addEventListener("pagehide", stopProvisionPoll);

load();
