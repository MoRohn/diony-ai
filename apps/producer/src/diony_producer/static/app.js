const titleEl = document.getElementById("title");
const loglineEl = document.getElementById("logline");
const hiredEl = document.getElementById("hired");
const trtEl = document.getElementById("trt");
const fleetEl = document.getElementById("fleet");
const siteLine = document.getElementById("site-line");
const cuesEl = document.getElementById("cues");
const customersEl = document.getElementById("customers");
const ideaEl = document.getElementById("idea");
const titleHintEl = document.getElementById("titleHint");
const ideaErr = document.getElementById("idea-err");
const dailiesEl = document.getElementById("dailies");
const notesEl = document.getElementById("notes");
const shotPick = document.getElementById("shotPick");
const noteBody = document.getElementById("noteBody");
const charsEl = document.getElementById("characters");
const refsEl = document.getElementById("refs");
const benchEmpty = document.getElementById("bench-empty");
const bibleSheet = document.getElementById("bible-sheet");
const printDesk = document.getElementById("print-desk");
const ladderEl = document.getElementById("ladder");
const cansEl = document.getElementById("cans");
const deskSlug = document.getElementById("desk-slug");
const deskMeta = document.getElementById("desk-meta");
const wellGrammar = document.getElementById("well-grammar");
const tightenBtn = document.getElementById("tighten");
const editErr = document.getElementById("edit-err");
const editInstruction = document.getElementById("edit-instruction");
const editIn = document.getElementById("edit-in");
const editOut = document.getElementById("edit-out");
const editStillName = document.getElementById("edit-still-name");
const stageEl = document.getElementById("page");
const castBtn = document.getElementById("cast");
const hintKeys = document.getElementById("hint-keys");

let project = null;
let catalog = [];
let allotments = [
  { id: "30s", label: "30s", minutes: 0.5 },
  { id: "1m", label: "1m", minutes: 1 },
  { id: "3m", label: "3m", minutes: 3 },
  { id: "5m", label: "5m", minutes: 5 },
  { id: "10m", label: "10m", minutes: 10 },
  { id: "15m", label: "15m", minutes: 15 },
  { id: "20m", label: "20m", minutes: 20 },
  { id: "30m", label: "30m", minutes: 30 },
  { id: "40m", label: "40m", minutes: 40 },
  { id: "1hr", label: "1hr", minutes: 60 },
];
let site = null;
let customerKind = "series";
let targetMinutes = 5;
let bay = "idea";
let bench = [];
let watchShotId = null;
let editStillId = null;
let written = false;
let busy = false;
let program = null;
let cameraLive = false;

const KIND_HIRED = {
  feature: "director",
  series: "showrunner",
  commercial: "agency",
  music_video: "artist",
  education: "instructor",
  social: "creator",
};

function showBay(name) {
  bay = name;
  document.querySelectorAll(".bay").forEach((el) => {
    el.classList.toggle("hidden", el.id !== `bay-${name}`);
  });
  document.querySelectorAll(".bays button").forEach((btn) => {
    btn.classList.toggle("on", btn.dataset.bay === name);
  });
  castBtn.classList.toggle("hidden", name !== "idea");
  stageEl.classList.toggle("program-sheet", name === "program");
  if (name === "program") loadProgram();
}

function pictureOpen() {
  return Boolean(project?.gates?.find((g) => g.name === "picture")?.open);
}

function qcClass(shot) {
  const takes = project.takes.filter((t) => t.shot_id === shot.id);
  const selected = takes.find((t) => t.id === shot.selected_take_id) || takes.at(-1);
  return selected?.qc?.verdict || "";
}

function ladderFor(shotId) {
  return project.takes
    .filter((t) => t.shot_id === shotId)
    .slice()
    .sort((a, b) => (a.version || 1) - (b.version || 1) || a.attempt - b.attempt);
}

function growIdea() {
  ideaEl.style.height = "auto";
  ideaEl.style.height = `${Math.max(220, ideaEl.scrollHeight)}px`;
}

function isVideoPath(path) {
  return /\.(mov|mp4|webm)$/i.test(path || "");
}

function secondsLabel(frames, fps) {
  const sec = frames / (fps || 23.976);
  if (sec < 60) return `${sec.toFixed(1)}s`;
  const m = Math.floor(sec / 60);
  const s = Math.round(sec % 60);
  return `${m}m ${String(s).padStart(2, "0")}s`;
}

function allotmentLabel(minutes) {
  const n = Number(minutes);
  const hit = allotments.find((item) => Math.abs(item.minutes - n) < 0.05);
  if (hit) return hit.label;
  if (n < 1) return `${Math.round(n * 60)}s`;
  if (n >= 59.5) return "1hr";
  return Number.isInteger(n) ? `${n}m` : `${n}m`;
}

function paintAllotment() {
  const row = document.getElementById("allotment-picks");
  if (!row) return;
  row.innerHTML = "";
  allotments.forEach((item) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.textContent = item.label;
    btn.dataset.minutes = String(item.minutes);
    btn.setAttribute("role", "option");
    btn.setAttribute("aria-selected", Math.abs(item.minutes - targetMinutes) < 0.05 ? "true" : "false");
    btn.disabled = written;
    btn.addEventListener("click", () => {
      if (written) return;
      targetMinutes = item.minutes;
      paintAllotment();
    });
    row.appendChild(btn);
  });
}

function renderStage(clip) {
  const stage = document.getElementById("stage");
  const video = document.getElementById("stage-video");
  const slug = document.getElementById("stage-slug");
  const note = document.getElementById("stage-note");
  if (!stage || !clip) return;
  const arts = clip.artifacts || [];
    const film =
      arts.find((a) => /finish_1080/i.test(a.path || "") && a.bytes > 32) ||
      arts.find((a) => isVideoPath(a.path) && a.bytes > 32);
  slug.textContent = clip.slug;
  const overlap = clip.overlap_frames
    ? `overlap ${clip.overlap_frames}f`
    : "head of cut";
  const from = clip.keyframe_from_take_id
    ? ` · frame from ${clip.keyframe_from_take_id}`
    : "";
  const plate = film && /finish_1080/i.test(film.path || "") ? "1080 master" : "720 negative";
  note.textContent = `${clip.engine.replaceAll("-", " ")} · ${secondsLabel(
    clip.duration_frames,
    program?.fps
  )} · ${overlap}${from} · ${plate}`;
  if (film && film.bytes > 32) {
    video.hidden = false;
    video.src = `/api/artifacts/${film.id}/file`;
    stage.classList.add("playing-video");
  } else {
    video.hidden = true;
    video.removeAttribute("src");
    stage.classList.remove("playing-video");
  }
}

function renderProgram() {
  const reel = document.getElementById("reel");
  const meta = document.getElementById("program-meta");
  const trt = document.getElementById("program-trt");
  const stitch = document.getElementById("program-stitch");
  if (!reel || !program) return;
  const clips = program.clips || [];
  if (!watchShotId && clips[0]) watchShotId = clips[0].shot_id;
  const on = clips.find((c) => c.shot_id === watchShotId) || clips[0];
  meta.textContent = on
    ? `${on.shot_id}  ${on.take_id || "no take"}  v${on.version || "—"}`
    : "No cut yet";
  trt.textContent = program.trt_seconds
    ? `${secondsLabel(program.total_frames, program.fps)} assembled · ${program.reel} · goal ${allotmentLabel(program.target_minutes)}`
    : "Roll dailies, then Assemble.";
  const gaps = (program.gaps || []).join(", ");
  stitch.textContent = gaps
    ? `Missing prints: ${gaps}`
    : program.stitched
      ? "Overlap stitch. I2V inherits the previous print. 1080 finish after picture lock."
      : "Assemble writes the overlap cut. Optional prompt-to-edit stays on Dailies.";
  reel.innerHTML = "";
  clips.forEach((clip) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = clip.shot_id === watchShotId ? "watching" : "";
    btn.dataset.watch = clip.shot_id;
    btn.innerHTML = `<span>${clip.shot_id}</span>${clip.slug}`;
    reel.appendChild(btn);
  });
  reel.querySelectorAll("[data-watch]").forEach((el) => {
    el.addEventListener("click", () => {
      watchShotId = el.dataset.watch;
      renderProgram();
    });
  });
  if (on) renderStage(on);
}

async function loadProgram() {
  const err = document.getElementById("program-err");
  if (err) err.textContent = "";
  const res = await fetch("/api/program");
  program = await res.json();
  if (!res.ok) {
    if (err) err.textContent = program.detail || "Could not load the cut.";
    return;
  }
  renderProgram();
}

function renderCues() {
  cuesEl.innerHTML = "";
  project.gates.forEach((g) => {
    const li = document.createElement("li");
    li.className = `cue ${g.open ? "open" : "locked"}`;
    li.innerHTML = `
      <span class="lamp" aria-hidden="true"></span>
      <span>${g.name}</span>
      ${g.open ? `<button type="button" data-gate="${g.name}">Lock</button>` : "<span>held</span>"}
    `;
    cuesEl.appendChild(li);
  });
  cuesEl.querySelectorAll("button[data-gate]").forEach((btn) => {
    btn.addEventListener("click", () => lock(btn.dataset.gate));
  });
}

function frameCard(asset, removable) {
  const card = document.createElement("article");
  card.className = "ref-card";
  let media = `<div class="ref-page">${asset.kind}</div>`;
  if (asset.kind === "image") {
    media = `<img alt="" src="/api/assets/${asset.id}/file" />`;
  } else if (asset.kind === "video") {
    media = `<div class="ref-core">clip</div>`;
  }
  const off = removable
    ? `<button type="button" data-off="${asset.id}" aria-label="Remove">×</button>`
    : "";
  card.innerHTML = `${media}<em>${asset.kind}</em><strong>${asset.filename}</strong>${off}`;
  return card;
}

function renderBench() {
  refsEl.innerHTML = "";
  benchEmpty.classList.toggle("hidden", bench.length > 0);
  bench.forEach((asset) => refsEl.appendChild(frameCard(asset, true)));
  refsEl.querySelectorAll("[data-off]").forEach((btn) => {
    btn.addEventListener("click", () => {
      bench = bench.filter((a) => a.id !== btn.dataset.off);
      renderBench();
    });
  });
}

function renderDesk() {
  const shot = project.shots.find((s) => s.id === watchShotId) || project.shots[0];
  if (!shot) {
    printDesk.hidden = true;
    return;
  }
  watchShotId = shot.id;
  printDesk.hidden = false;
  const takes = ladderFor(shot.id);
  const selected =
    takes.find((t) => t.id === shot.selected_take_id) || takes.at(-1) || null;
  deskSlug.textContent = shot.slug;
  deskMeta.textContent = selected
    ? `${shot.id}  v${selected.version || 1}  ${selected.origin || "generation"}`
    : `${shot.id}  no take yet`;
  if (deskMeta.dataset.farm) {
    deskMeta.textContent += `  ·  ${deskMeta.dataset.farm}`;
  }
  wellGrammar.textContent = `${shot.focal_mm}mm  T${shot.t_stop}  ${String(shot.camera_move).replaceAll("_", " ")}  ${shot.lighting_ratio}`;
  tightenBtn.classList.toggle("hidden", !shot.id);
  tightenBtn.dataset.expand = shot.id;
  ladderEl.innerHTML = "";
  takes.forEach((t) => {
    const li = document.createElement("li");
    const on = t.id === shot.selected_take_id;
    li.innerHTML = `
      <button type="button" class="${on ? "print" : ""}" data-shot="${shot.id}" data-take="${t.id}">
        v${t.version || 1}
      </button>`;
    ladderEl.appendChild(li);
  });
  if (!takes.length) {
    ladderEl.innerHTML = "<li class='hint'>Roll first.</li>";
  }
  ladderEl.querySelectorAll("button[data-take]").forEach((btn) => {
    btn.addEventListener("click", () => select(btn.dataset.shot, btn.dataset.take));
  });
  cansEl.innerHTML = (selected?.artifacts || [])
    .map((a) => `<a href="/api/artifacts/${a.id}/file" download>${a.kind.replaceAll("_", " ")}</a>`)
    .join("");
  const locked = !pictureOpen();
  editInstruction.disabled = locked;
  editIn.disabled = locked;
  editOut.disabled = locked;
  document.getElementById("cut-print").disabled = locked || !selected;
  document.getElementById("lay-edit-still").disabled = locked;
  if (locked) {
    editErr.textContent = "Picture is locked.";
  } else if (editErr.textContent.includes("locked")) {
    editErr.textContent = "";
  }
}

function render() {
  titleEl.textContent = project.bible.title;
  loglineEl.textContent = project.bible.logline;
  hiredEl.textContent = KIND_HIRED[project.customer_kind] || project.customer_kind;
  if (written && project.brief && typeof project.brief.target_minutes === "number") {
    targetMinutes = project.brief.target_minutes;
  }
  trtEl.textContent = allotmentLabel(targetMinutes);
  paintAllotment();
  if (site?.sequential) {
    fleetEl.textContent = `${project.fleet_mode} · sequential ${site.recipe}`;
  } else {
    fleetEl.textContent = project.fleet_mode;
  }
  if (siteLine) {
    const camera = cameraLive ? "Wan live 720p" : "stub camera";
    siteLine.textContent = site?.signed
      ? `${site.label} · ${site.split} · ${camera}`
      : `Unsigned · ${camera}`;
  }
  customerKind = project.customer_kind || customerKind;
  bibleSheet.classList.toggle("hidden", !written);
  hintKeys.classList.toggle("hidden", written);
  renderCues();
  renderBench();

  document.getElementById("bible-title").value = project.bible.title;
  document.getElementById("bible-logline").value = project.bible.logline;
  document.getElementById("bible-world").value = project.bible.world;
  document.getElementById("bible-grammar").value = project.bible.visual_grammar;
  const locked = !project.gates.find((g) => g.name === "bible")?.open;
  ["bible-title", "bible-logline", "bible-world", "bible-grammar"].forEach((id) => {
    document.getElementById(id).disabled = locked;
  });
  charsEl.innerHTML = project.bible.characters
    .map((c) => `<article class="char"><h3>${c.name}</h3><p>${c.wardrobe}</p></article>`)
    .join("");
  const bibleRefs = document.getElementById("bible-refs");
  bibleRefs.innerHTML = "";
  (project.assets || []).forEach((asset) => bibleRefs.appendChild(frameCard(asset, false)));

  dailiesEl.innerHTML = "";
  project.shots.forEach((shot) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = `${qcClass(shot)} ${shot.id === watchShotId ? "watching" : ""}`;
    btn.dataset.watch = shot.id;
    btn.innerHTML = `<span>${shot.id}</span>${shot.slug}`;
    dailiesEl.appendChild(btn);
  });
  dailiesEl.querySelectorAll("[data-watch]").forEach((el) => {
    el.addEventListener("click", () => {
      watchShotId = el.dataset.watch;
      const shot = project.shots.find((s) => s.id === watchShotId);
      if (shot?.selected_take_id) {
        select(watchShotId, shot.selected_take_id);
      } else {
        render();
      }
    });
  });
  renderDesk();

  shotPick.innerHTML = project.shots
    .map((s) => `<option value="${s.id}">${s.id}</option>`)
    .join("");
  notesEl.innerHTML = "";
  project.notes.forEach((n) => {
    const li = document.createElement("li");
    li.textContent = `${n.shot_id || n.take_id} — ${n.body}`;
    notesEl.appendChild(li);
  });
}

async function load() {
  const [p, c, s] = await Promise.all([
    fetch("/api/project"),
    fetch("/api/catalog"),
    fetch("/api/site"),
  ]);
  project = await p.json();
  const cat = await c.json();
  catalog = cat.customers;
  if (Array.isArray(cat.allotments) && cat.allotments.length) {
    allotments = cat.allotments;
  }
  cameraLive = Boolean(cat.live);
  site = (await s.json()) || cat.site || null;
  watchShotId = project.shots[0]?.id || null;
  render();
  showBay("idea");
  ideaEl.focus();
}

let finishTimer = null;

async function pollFinish() {
  if (finishTimer) return;
  const tick = async () => {
    try {
      const fin = await fetch("/api/finish").then((r) => r.json());
      if (deskMeta && fin.detail) {
        deskMeta.dataset.farm = fin.detail;
      }
      const p = await fetch("/api/project").then((r) => r.json());
      project = p;
      render();
      if (!fin.busy) {
        clearInterval(finishTimer);
        finishTimer = null;
        await loadProgram();
      }
    } catch {
      clearInterval(finishTimer);
      finishTimer = null;
    }
  };
  finishTimer = setInterval(tick, 4000);
  tick();
}

async function lock(name) {
  const res = await fetch(`/api/gates/${name}/lock`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ locked_by: "showrunner" }),
  });
  const body = await res.json();
  if (!res.ok) {
    alert(body.detail || "Gate refused");
    return;
  }
  project = body;
  render();
  if (name === "picture") pollFinish();
}

async function select(shot, take) {
  const res = await fetch(`/api/shots/${shot}/select/${take}`, { method: "POST" });
  project = await res.json();
  watchShotId = shot;
  render();
}

async function expand(shotId) {
  const res = await fetch(`/api/shots/${shotId}/expand`, { method: "POST" });
  project = await res.json();
  render();
}

async function uploadFiles(fileList, role) {
  const out = [];
  for (const file of fileList) {
    const data = new FormData();
    data.append("file", file);
    data.append("role", role);
    const res = await fetch("/api/assets", { method: "POST", body: data });
    const body = await res.json();
    if (!res.ok) {
      throw new Error(body.detail || `Could not add ${file.name}.`);
    }
    out.push(body);
  }
  return out;
}

let farmTimer = null;

async function pollFarm() {
  if (farmTimer) return;
  const tick = async () => {
    try {
      const farm = await fetch("/api/farm").then((r) => r.json());
      if (deskMeta) {
        deskMeta.dataset.farm = farm.busy ? farm.detail || "Wan2.2 FARM" : "";
      }
      const p = await fetch("/api/project").then((r) => r.json());
      project = p;
      render();
      const waiting = (project.takes || []).some((t) => {
        const art = (t.artifacts || []).find((a) => /\.mp4$/i.test(a.path || ""));
        if (!art) return false;
        if (t.qc?.verdict === "fail") return false;
        return !art.bytes || art.bytes < 32;
      });
      if (!farm.busy && !waiting) {
        clearInterval(farmTimer);
        farmTimer = null;
        await loadProgram();
      }
    } catch {
      clearInterval(farmTimer);
      farmTimer = null;
    }
  };
  farmTimer = setInterval(tick, 4000);
  tick();
}

async function makeStudio() {
  if (busy) return;
  ideaErr.textContent = "";
  busy = true;
  castBtn.disabled = true;
  try {
    const res = await fetch("/api/studio/open", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        idea: ideaEl.value,
        customer_kind: customerKind,
        target_minutes: targetMinutes,
        title_hint: titleHintEl.value || ideaEl.value.trim().split("\n")[0].slice(0, 80),
        attachment_ids: bench.map((a) => a.id),
      }),
    });
    const body = await res.json();
    if (!res.ok) {
      ideaErr.textContent = body.detail || "Write on the page, or drop a file.";
      return;
    }
    project = body;
    const rolled = await fetch("/api/studio/roll", { method: "POST" });
    project = await rolled.json();
    written = true;
    watchShotId = project.shots[0]?.id || null;
    render();
    await loadProgram();
    showBay("dailies");
    pollFarm();
  } finally {
    busy = false;
    castBtn.disabled = false;
  }
}

async function cutPrint() {
  editErr.textContent = "";
  const shot = project.shots.find((s) => s.id === watchShotId);
  const takeId = shot?.selected_take_id;
  if (!takeId) {
    editErr.textContent = "Choose a version first.";
    return;
  }
  const instruction = editInstruction.value.trim();
  if (instruction.length < 4) {
    editErr.textContent = "Say what changes.";
    return;
  }
  const payload = {
    instruction,
    attachment_ids: editStillId ? [editStillId] : [],
  };
  if (editIn.value !== "") payload.t_in_frames = Number(editIn.value);
  if (editOut.value !== "") payload.t_out_frames = Number(editOut.value);
  const res = await fetch(`/api/takes/${takeId}/edit`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const body = await res.json();
  if (!res.ok) {
    editErr.textContent = body.detail || "Edit refused.";
    return;
  }
  project = body;
  editInstruction.value = "";
  editIn.value = "";
  editOut.value = "";
  editStillId = null;
  editStillName.textContent = "";
  render();
}

document.querySelectorAll(".bays button").forEach((btn) => {
  btn.addEventListener("click", () => showBay(btn.dataset.bay));
});

document.getElementById("lay-ref").addEventListener("click", () => {
  document.getElementById("ref-files").click();
});

document.getElementById("ref-files").addEventListener("change", async (ev) => {
  ideaErr.textContent = "";
  try {
    bench = [...bench, ...(await uploadFiles(ev.target.files, "idea_ref"))];
    renderBench();
  } catch (err) {
    ideaErr.textContent = err.message;
  }
  ev.target.value = "";
});

["dragenter", "dragover"].forEach((name) => {
  stageEl.addEventListener(name, (ev) => {
    ev.preventDefault();
    stageEl.classList.add("receiving");
  });
});
["dragleave", "drop"].forEach((name) => {
  stageEl.addEventListener(name, () => stageEl.classList.remove("receiving"));
});
stageEl.addEventListener("drop", async (ev) => {
  ev.preventDefault();
  ideaErr.textContent = "";
  try {
    bench = [...bench, ...(await uploadFiles(ev.dataTransfer.files, "idea_ref"))];
    renderBench();
  } catch (err) {
    ideaErr.textContent = err.message;
  }
});

ideaEl.addEventListener("input", growIdea);
ideaEl.addEventListener("keydown", (ev) => {
  if (ev.key === "Enter" && (ev.metaKey || ev.ctrlKey)) {
    ev.preventDefault();
    makeStudio();
  }
});
tightenBtn.addEventListener("click", () => expand(tightenBtn.dataset.expand));

document.getElementById("lay-edit-still").addEventListener("click", () => {
  document.getElementById("edit-still").click();
});

document.getElementById("edit-still").addEventListener("change", async (ev) => {
  editErr.textContent = "";
  try {
    const added = await uploadFiles(ev.target.files, "edit_ref");
    editStillId = added[0]?.id || null;
    editStillName.textContent = added[0]?.filename || "";
  } catch (err) {
    editErr.textContent = err.message;
  }
  ev.target.value = "";
});

castBtn.addEventListener("click", makeStudio);

document.getElementById("nightpress").addEventListener("click", async () => {
  const res = await fetch("/api/reset", { method: "POST" });
  project = await res.json();
  bench = [];
  written = true;
  watchShotId = project.shots[0]?.id || null;
  render();
  showBay("dailies");
});

document.getElementById("save-bible").addEventListener("click", async () => {
  const res = await fetch("/api/bible", {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      title: document.getElementById("bible-title").value,
      logline: document.getElementById("bible-logline").value,
      world: document.getElementById("bible-world").value,
      visual_grammar: document.getElementById("bible-grammar").value,
    }),
  });
  const body = await res.json();
  if (!res.ok) {
    alert(body.detail || "Not saved");
    return;
  }
  project = body;
  render();
});

document.getElementById("roll").addEventListener("click", async () => {
  const res = await fetch("/api/studio/roll", { method: "POST" });
  project = await res.json();
  watchShotId = watchShotId || project.shots[0]?.id;
  render();
  pollFarm();
});

document.getElementById("retake").addEventListener("click", async () => {
  if (!watchShotId) return;
  const res = await fetch(`/api/shots/${watchShotId}/retake`, { method: "POST" });
  const body = await res.json();
  if (!res.ok) {
    alert(body.detail || "Retake refused");
    return;
  }
  project = body;
  render();
  pollFarm();
});

document.getElementById("cut-print").addEventListener("click", cutPrint);

editInstruction.addEventListener("keydown", (ev) => {
  if (ev.key === "Enter" && !ev.shiftKey) {
    ev.preventDefault();
    cutPrint();
  }
});

document.getElementById("assemble")?.addEventListener("click", async () => {
  const err = document.getElementById("program-err");
  if (err) err.textContent = "";
  const res = await fetch("/api/program/assemble", { method: "POST" });
  const body = await res.json();
  if (!res.ok) {
    if (err) err.textContent = body.detail || "Could not assemble.";
    return;
  }
  project = body.project;
  program = body.program;
  render();
  renderProgram();
});

document.addEventListener("keydown", (ev) => {
  if (bay !== "program") return;
  if (ev.key !== "ArrowRight" && ev.key !== "ArrowLeft" && ev.key !== "j" && ev.key !== "k") {
    return;
  }
  const clips = program?.clips || [];
  const idx = clips.findIndex((c) => c.shot_id === watchShotId);
  if (idx < 0) return;
  const next = ev.key === "ArrowRight" || ev.key === "j" ? idx + 1 : idx - 1;
  if (!clips[next]) return;
  ev.preventDefault();
  watchShotId = clips[next].shot_id;
  renderProgram();
});

document.getElementById("pinNote").addEventListener("click", async () => {
  const body = noteBody.value.trim();
  if (!body) return;
  const res = await fetch("/api/notes", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ body, shot_id: shotPick.value }),
  });
  project = await res.json();
  noteBody.value = "";
  render();
});

paintAllotment();
load();
