const nativeFetch = window.fetch.bind(window);
window.fetch = (input, init = {}) => {
  const token = sessionStorage.getItem("diony_session");
  const url = typeof input === "string" ? input : input?.url || "";
  if (!token || !url.startsWith("/api/")) return nativeFetch(input, init);
  const headers = new Headers(init.headers || {});
  if (!headers.has("Authorization")) headers.set("Authorization", `Bearer ${token}`);
  return nativeFetch(input, { ...init, headers });
};

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
const refsEl = document.getElementById("refs");
const benchEmpty = document.getElementById("bench-empty");
const stageEl = document.getElementById("page");
const castBtn = document.getElementById("cast");
const sampleBtn = document.getElementById("make-sample");
const hintKeys = document.getElementById("hint-keys");
const productionFloor = document.getElementById("bay-production");
const productionStopDialog = document.getElementById("production-stop-dialog");

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
let written = false;
let starting = false;
let busy = false;
let program = null;
let run = null;
let cameraLive = false;
let farmBusy = false;
let farmDetail = "";
let gallery = null;
let watchGalleryId = null;
let gallerySelection = [];
let gallerySelectMode = false;
let gallerySelectionAnchor = null;
let joinDraft = null;
let joinPlan = null;
let joinOptions = null;
let joinPlanTimer = null;
let joinJobId = null;
let joinJobTimer = null;
let clockTimer = null;
let bayTransitionTimer = null;
let runRenderSignature = "";
let lastSignalAnnouncement = "";

const KIND_HIRED = {
  feature: "director",
  series: "showrunner",
  commercial: "agency",
  music_video: "artist",
  education: "instructor",
  social: "creator",
};

function productionReady() {
  return Boolean(starting || (written && project?.takes?.length));
}

function gateOpen(name) {
  return Boolean(project?.gates?.find((g) => g.name === name)?.open);
}

function paintFloors() {
  document.querySelectorAll(".bays button").forEach((btn) => {
    const name = btn.dataset.bay;
    if (name === "production") btn.disabled = !productionReady();
    if (name === "gallery") btn.disabled = false;
  });
  const openGallery = document.getElementById("open-gallery");
  if (openGallery) openGallery.disabled = false;
  const lockBible = document.getElementById("lock-bible");
  if (lockBible) {
    lockBible.classList.toggle("hidden", !written);
    lockBible.disabled = !gateOpen("bible");
  }
  const lockDist = document.getElementById("lock-distribution");
  if (lockDist) lockDist.disabled = gateOpen("mix") || !gateOpen("distribution");
}

function triggerBayTransition(previousBay, nextBay) {
  if (!previousBay || previousBay === nextBay) return;
  const order = ["idea", "production", "gallery"];
  document.body.dataset.sceneDirection =
    order.indexOf(nextBay) < order.indexOf(previousBay) ? "back" : "forward";
  document.body.classList.remove("producer-scene-transition");
  void stageEl?.offsetWidth;
  document.body.classList.add("producer-scene-transition");
  if (bayTransitionTimer) clearTimeout(bayTransitionTimer);
  bayTransitionTimer = window.setTimeout(() => {
    document.body.classList.remove("producer-scene-transition");
  }, 720);
}

function resetProductionViewport() {
  if (!productionFloor) return;
  window.requestAnimationFrame(() => {
    productionFloor.scrollTop = 0;
  });
}

function showBay(name) {
  const previousBay = bay;
  if (name === "production" && !productionReady()) name = "idea";
  if (!["idea", "production", "gallery"].includes(name)) name = "idea";
  bay = name;
  document.body.dataset.activeBay = name;
  document.querySelectorAll(".bay").forEach((el) => {
    el.classList.toggle("hidden", el.id !== `bay-${name}`);
  });
  document.querySelectorAll(".bays button").forEach((btn) => {
    btn.classList.toggle("on", btn.dataset.bay === name);
  });
  castBtn.classList.toggle("hidden", name !== "idea");
  if (name === "production") {
    if (previousBay !== "production") resetProductionViewport();
    loadRun();
    pollClock();
  } else if (clockTimer) {
    clearInterval(clockTimer);
    clockTimer = null;
  }
  if (name === "gallery") loadGallery();
  paintFloors();
  triggerBayTransition(previousBay, name);
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
    row.appendChild(btn);
  });
}

function filmFrom(arts) {
  const list = arts || [];
  return (
    list.find((a) => /finish_1080/i.test(a.path || "") && a.bytes > 32) ||
    list.find((a) => isVideoPath(a.path) && a.bytes > 32) ||
    null
  );
}

function paintStage(stageId, videoId, slugId, noteId, slug, note, film) {
  const stage = document.getElementById(stageId);
  const video = document.getElementById(videoId);
  const slugEl = document.getElementById(slugId);
  const noteEl = document.getElementById(noteId);
  if (!stage || !video) return;
  if (slugEl) slugEl.textContent = slug;
  if (noteEl) noteEl.textContent = note;
  if (!video.dataset.stageListeners) {
    video.dataset.stageListeners = "bound";
    video.addEventListener("loadeddata", () => {
      stage.classList.add("playing-video");
    });
    video.addEventListener("error", () => {
      stage.classList.remove("playing-video");
      if (noteEl) {
        noteEl.textContent =
          "Preview unavailable in this browser. The finished print is preserved below.";
      }
    });
  }
  if (film && film.bytes > 32) {
    video.hidden = false;
    if (video.dataset.artifactId !== film.id) {
      stage.classList.remove("playing-video");
      video.dataset.artifactId = film.id;
      video.src = `/api/artifacts/${film.id}/file`;
      video.load();
    } else if (video.readyState >= HTMLMediaElement.HAVE_CURRENT_DATA) {
      stage.classList.add("playing-video");
    }
  } else {
    video.hidden = true;
    delete video.dataset.artifactId;
    video.removeAttribute("src");
    video.load();
    stage.classList.remove("playing-video");
  }
}

const STEP_WORD = {
  done: "done",
  rolling: "rolling",
  waiting: "waiting on you",
  stub: "cannot start",
  blocked: "not on this desk",
  failed: "failed",
  stopped: "stopped",
  next: "next",
};

function clipWord(state) {
  if (state === "rolling") return "working";
  if (state === "live") return "negative ready";
  if (state === "ready") return "1080 ready";
  if (state === "failed") return "attention";
  if (state === "deleted") return "deleted";
  if (state === "stopped") return "stopped";
  return "waiting";
}

function clockLabel(at) {
  if (!at) return "";
  if (/^\d{1,2}:\d{2}/.test(String(at))) return String(at);
  const d = new Date(at);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function activityStatusLabel(level) {
  if (level === "live") return "Working";
  if (level === "fail") return "Attention";
  if (level === "wait") return "Waiting";
  return "Checkpoint";
}

function activityStageLabel(stage) {
  const labels = {
    camera: "Wan2.2 · 720p Camera",
    farm: "Wan2.2 · 720p Camera",
    wan: "Wan2.2 · 720p Camera",
    finish: "diony_sr_v1 · 1080 Finish",
    picture: "diony_sr_v1 · 1080 Finish",
    sr: "diony_sr_v1 · 1080 Finish",
    mix: "Mix",
    distribution: "Distribution",
    done: "Complete",
  };
  return labels[stage] || String(stage || "Standby");
}

function activityComputeLabel(gpu) {
  const parts = [];
  const util = gpu?.util;
  if (util !== null && util !== undefined && util !== "" && Number.isFinite(Number(util))) {
    parts.push(`${Number(util)}% load`);
  }
  const used = Number(gpu?.used_mib);
  const total = Number(gpu?.total_mib);
  if (Number.isFinite(used) && Number.isFinite(total) && total > 0) {
    parts.push(`${(used / 1024).toFixed(1)} / ${(total / 1024).toFixed(1)} GiB`);
  }
  return parts.join(" · ") || "Awaiting telemetry";
}

function paintActivityFocus(monitoring, rows) {
  const signal = monitoring?.signal || {};
  const gpu = monitoring?.gpu || {};
  const state = document.getElementById("run-activity-state");
  const updated = document.getElementById("run-activity-updated");
  const current = document.getElementById("run-activity-current");
  const detail = document.getElementById("run-activity-current-detail");
  const identity = document.getElementById("run-activity-identity");
  const stage = document.getElementById("run-activity-stage");
  const gpuName = document.getElementById("run-activity-gpu");
  const compute = document.getElementById("run-activity-compute");
  const logMeta = document.getElementById("run-activity-log-meta");
  const signalState = signal.state || "waiting";
  if (state) {
    state.textContent = signalState === "live" ? "WORKING" : signalState === "attention" ? "ATTENTION" : signalState === "ready" ? "READY" : signalState === "stopped" ? "STOPPED" : "WAITING";
    state.className = signalState;
  }
  const when = clockLabel(signal.updated_at);
  if (updated) {
    updated.textContent = when
      ? `UPDATED ${when}`
      : signalState === "live"
        ? "LIVE NOW"
        : "NOT YET UPDATED";
  }
  if (current) current.textContent = signal.title || "Production is waiting";
  if (detail) detail.textContent = signal.detail || "Produce starts the run.";
  const ids = [signal.clip_id, signal.take_id].filter(Boolean);
  if (identity) identity.textContent = ids.join(" / ") || "—";
  if (stage) stage.textContent = activityStageLabel(signal.stage);
  if (gpuName) gpuName.textContent = gpu.name || "Signed production site";
  if (compute) compute.textContent = activityComputeLabel(gpu);
  const count = Array.isArray(rows) ? rows.length : 0;
  if (logMeta) logMeta.textContent = count ? `${count} ${count === 1 ? "message" : "messages"} · newest first` : "No messages yet";
}

function paintActivity(logEl, rows) {
  const wasEmpty = logEl.childElementCount === 0;
  const previousScrollTop = logEl.scrollTop;
  logEl.replaceChildren();
  const list =
    rows && rows.length
      ? rows
      : [{ src: "run", level: "wait", title: "Production is waiting", detail: "Produce starts the run." }];
  const srcWord = { wan: "Camera", sr: "Picture", finish: "Picture", gpu: "GPU", qc: "QC", run: "Run" };
  list.forEach((row) => {
    const li = document.createElement("li");
    li.className = row.level || "info";
    const marker = document.createElement("span");
    marker.className = "activity-marker";
    marker.setAttribute("aria-hidden", "true");
    const body = document.createElement("div");
    body.className = "activity-body";
    const meta = document.createElement("div");
    meta.className = "activity-meta";
    const src = document.createElement("span");
    src.className = "src";
    src.textContent = srcWord[row.src] || String(row.src || "Run");
    meta.appendChild(src);
    const when = clockLabel(row.at);
    if (when) {
      const at = document.createElement("time");
      at.className = "at";
      at.textContent = when;
      meta.appendChild(at);
    }
    const status = document.createElement("span");
    status.className = "activity-status";
    status.textContent = activityStatusLabel(row.level);
    meta.appendChild(status);
    body.appendChild(meta);
    const title = document.createElement("strong");
    title.className = "activity-title";
    title.textContent = row.title || row.text || "Production updated";
    body.appendChild(title);
    if (row.detail) {
      const detail = document.createElement("p");
      detail.className = "activity-detail";
      detail.textContent = row.detail;
      body.appendChild(detail);
    }
    const facts = [row.shot_id, row.take_id, row.progress?.label, row.progress?.rate, row.progress?.eta].filter(Boolean);
    if (facts.length) {
      const context = document.createElement("div");
      context.className = "activity-context";
      facts.forEach((value) => {
        const fact = document.createElement("span");
        fact.textContent = value;
        context.appendChild(fact);
      });
      body.appendChild(context);
    }
    const technical = String(row.technical || row.text || "").trim();
    if (technical && technical !== title.textContent && technical !== row.detail) {
      const evidence = document.createElement("div");
      evidence.className = "activity-evidence activity-raw-message";
      const rawLabel = document.createElement("span");
      rawLabel.textContent = "Raw message";
      const code = document.createElement("code");
      code.textContent = technical;
      evidence.append(rawLabel, code);
      body.appendChild(evidence);
    }
    li.append(marker, body);
    logEl.appendChild(li);
  });
  if (!wasEmpty) {
    logEl.scrollTop = Math.min(previousScrollTop, Math.max(0, logEl.scrollHeight - logEl.clientHeight));
  }
}

function addSignalMetric(list, label, value) {
  if (!value) return;
  const group = document.createElement("div");
  const term = document.createElement("dt");
  const data = document.createElement("dd");
  term.textContent = label;
  data.textContent = value;
  group.append(term, data);
  list.appendChild(group);
}

function paintSignal(monitoring) {
  const signal = monitoring?.signal || {};
  const rail = document.getElementById("run-signal");
  if (!rail) return;
  const state = signal.state || "waiting";
  rail.className = `run-signal ${state}`;
  const stateWord = { live: "Live", attention: "Attention", ready: "Ready", stopped: "Stopped", waiting: "Standby" }[state] || "Standby";
  document.getElementById("run-signal-state").textContent = stateWord;
  document.getElementById("run-signal-eyebrow").textContent = signal.eyebrow || "PRODUCTION · STANDBY";
  document.getElementById("run-signal-title").textContent = signal.title || "Production is waiting";
  document.getElementById("run-signal-detail").textContent = signal.detail || "Produce starts the run.";
  const updated = clockLabel(signal.updated_at);
  document.getElementById("run-signal-updated").textContent = updated ? `Updated ${updated}` : "";
  const announcement = `${stateWord}. ${signal.title || "Production is waiting"}`;
  if (announcement !== lastSignalAnnouncement) {
    document.getElementById("run-signal-announcement").textContent = announcement;
    lastSignalAnnouncement = announcement;
  }

  const progress = signal.progress || {};
  const progressEl = document.getElementById("run-signal-progress");
  const hasProgress = Number(progress.total) > 0;
  progressEl.hidden = !hasProgress;
  document.getElementById("run-signal-progress-fill").style.width = hasProgress ? `${Math.min(100, Math.max(0, Number(progress.pct) || 0))}%` : "0%";
  document.getElementById("run-signal-progress-label").textContent = progress.label || "";

  const metrics = document.getElementById("run-signal-metrics");
  metrics.replaceChildren();
  const gpu = monitoring?.gpu || {};
  addSignalMetric(metrics, "Line", monitoring?.connection === "live" ? "Connected" : monitoring?.connection === "checking" ? "Checking" : "Idle");
  const util = gpu.util === null || gpu.util === undefined ? "" : `${gpu.util}% load`;
  addSignalMetric(metrics, gpu.name || "GPU", util || (gpu.heartbeat ? "Responding" : "No sample"));
  if (gpu.used_mib && gpu.total_mib) {
    addSignalMetric(metrics, "GPU memory", `${(gpu.used_mib / 1024).toFixed(1)} / ${(gpu.total_mib / 1024).toFixed(1)} GiB`);
  }
}

function paintClipSummary(monitoring) {
  const counts = monitoring?.counts || {};
  const total = Number(counts.total) || 0;
  const summary = document.getElementById("run-clip-summary");
  if (summary) {
    const pieces = [];
    if (counts.working) pieces.push(`${counts.working} working`);
    if (counts.negatives) pieces.push(`${counts.negatives} negative${counts.negatives === 1 ? "" : "s"} ready`);
    if (counts.ready) pieces.push(`${counts.ready} master${counts.ready === 1 ? "" : "s"} ready`);
    if (counts.failed) pieces.push(`${counts.failed} need${counts.failed === 1 ? "s" : ""} attention`);
    if (counts.deleted) pieces.push(`${counts.deleted} deleted`);
    if (counts.stopped) pieces.push(`${counts.stopped} not generated`);
    if (counts.queued) pieces.push(`${counts.queued} waiting`);
    summary.textContent = pieces.join(" · ") || (total ? `${total} clips` : "Waiting for the run.");
  }
  const badges = document.getElementById("run-clip-counts");
  if (!badges) return;
  badges.replaceChildren();
  [["Ready", Number(counts.ready || 0), "ready"], ["Attention", Number(counts.failed || 0), "attention"]].forEach(([label, value, tone]) => {
    if (!value) return;
    const badge = document.createElement("span");
    badge.dataset.tone = tone;
    badge.textContent = `${value} ${label}`;
    badges.appendChild(badge);
  });
}

function paintClips(list, clips) {
  list.replaceChildren();
  (clips || []).forEach((clip) => {
    const li = document.createElement("li");
    li.className = `clip-row ${clip.state === "rolling" ? "on" : clip.state || "queued"}`;
    const head = document.createElement("div");
    head.className = "clip-head";
    const identity = document.createElement("div");
    identity.className = "clip-identity";
    const id = document.createElement("strong");
    id.className = "id";
    id.textContent = clip.shot_id || "Clip";
    const take = document.createElement("span");
    const lane = String(clip.generation_lane_label || "").trim();
    const effectiveSteps = Number(clip.effective_denoise_steps || 0);
    const passes = Number(clip.denoise_passes || 0);
    const work = effectiveSteps
      ? `${effectiveSteps} denoise steps${passes > 1 ? ` · ${passes} passes` : ""}`
      : "";
    take.textContent = [clip.take_id || clip.engine || "", lane, work].filter(Boolean).join(" · ");
    if (clip.generation_fallback) take.title = clip.generation_fallback;
    identity.append(id, take);
    const state = document.createElement("span");
    state.className = "clip-state";
    state.textContent = clipWord(clip.state);
    head.append(identity, state);
    const status = document.createElement("p");
    status.className = "clip-status";
    status.textContent = clip.status || clip.note || "Waiting for camera";
    const note = document.createElement("p");
    note.className = "clip-note";
    note.textContent = clip.note || "";
    const milestones = document.createElement("ol");
    milestones.className = "clip-milestones";
    (clip.milestones || []).forEach((item) => {
      const stop = document.createElement("li");
      stop.className = item.state || "next";
      stop.dataset.stage = item.id || "";
      const dot = document.createElement("i");
      dot.setAttribute("aria-hidden", "true");
      const label = document.createElement("span");
      label.textContent = item.label || item.id || "";
      stop.append(dot, label);
      milestones.appendChild(stop);
    });
    li.append(head, status, note, milestones);
    const progress = clip.progress || {};
    if (Number(progress.total) > 0) {
      const meter = document.createElement("div");
      meter.className = "clip-progress";
      const fill = document.createElement("i");
      fill.style.width = `${Math.min(100, Math.max(0, Number(progress.pct) || 0))}%`;
      const label = document.createElement("span");
      label.textContent = progress.label || "";
      meter.append(fill, label);
      li.appendChild(meter);
    }
    list.appendChild(li);
  });
}

function formatElapsed(seconds) {
  const total = Math.max(0, Math.floor(Number(seconds) || 0));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const secs = total % 60;
  if (hours) {
    return `${hours}:${String(minutes).padStart(2, "0")}:${String(secs).padStart(2, "0")}`;
  }
  return `${minutes}:${String(secs).padStart(2, "0")}`;
}

function clockSeconds(clock) {
  if (!clock || !clock.started_at) return 0;
  const start = Date.parse(clock.started_at);
  if (!Number.isFinite(start)) return Number(clock.elapsed_s) || 0;
  if (clock.complete || clock.ended_at) {
    const end = clock.ended_at ? Date.parse(clock.ended_at) : NaN;
    if (Number.isFinite(end)) return Math.max(0, Math.floor((end - start) / 1000));
    return Number(clock.elapsed_s) || 0;
  }
  return Math.max(0, Math.floor((Date.now() - start) / 1000));
}

function paintClock() {
  const clock = run?.clock;
  const el = document.getElementById("run-clock");
  const value = document.getElementById("run-clock-value");
  const label = document.getElementById("run-clock-label");
  if (!el || !value || !label) return;
  if (!clock || !clock.started_at) {
    value.textContent = "0:00";
    label.textContent = "Elapsed";
    el.classList.remove("live", "done");
    return;
  }
  value.textContent = formatElapsed(clockSeconds(clock));
  label.textContent = clock.label || (clock.complete ? "Total" : "Elapsed");
  const live = Boolean(clock.running) || (!clock.complete && !clock.ended_at);
  el.classList.toggle("live", live);
  el.classList.toggle("done", Boolean(clock.complete));
}

function pollClock() {
  if (clockTimer) return;
  const tick = () => {
    if (bay !== "production") {
      clearInterval(clockTimer);
      clockTimer = null;
      return;
    }
    paintClock();
  };
  clockTimer = setInterval(tick, 1000);
  tick();
}

function renderProductionStandard() {
  if (!run) return;
  const standard = run.production_standard;
  if (!standard) return;
  const status = document.getElementById("master-standard-status");
  const negative = document.getElementById("master-standard-negative");
  const final = document.getElementById("master-standard-final");
  const picture = document.getElementById("master-standard-picture");
  const sound = document.getElementById("master-standard-sound");
  const material = document.getElementById("master-standard-material");
  const blockers = document.getElementById("master-standard-blockers");
  const rail = document.getElementById("master-standard");
  if (status) status.textContent = standard.status || "PLANNING";
  if (negative) {
    negative.textContent = [standard.negative?.model, standard.negative?.label, standard.negative?.role]
      .filter(Boolean)
      .join(" · ");
  }
  if (final) {
    final.textContent = [standard.final?.label, standard.final?.aspect, standard.final?.fps, standard.final?.color]
      .filter(Boolean)
      .join(" · ");
  }
  if (picture) {
    picture.textContent = [standard.finish?.model, standard.finish?.method]
      .filter(Boolean)
      .join(" · ");
  }
  if (sound) {
    sound.textContent = [standard.audio?.target, standard.audio?.true_peak]
      .filter(Boolean)
      .join(" · ");
  }
  if (material) material.textContent = standard.material_label || "Verification needed";
  if (blockers) blockers.textContent = (standard.blockers || []).join("  /  ");
  if (rail) {
    rail.classList.toggle("ready", Boolean(standard.master_ready));
    rail.classList.toggle("hold", standard.status === "QC HOLD");
  }
}

function renderIncompleteResult() {
  const section = document.getElementById("run-incomplete-result");
  const video = document.getElementById("run-incomplete-video");
  const download = document.getElementById("run-incomplete-download");
  const detail = document.getElementById("run-incomplete-detail");
  if (!section || !video || !download || !detail) return;
  const stop = run?.stop || {};
  const visible = Boolean(stop.stopped && stop.disposition === "keep" && stop.result_url);
  section.hidden = !visible;
  if (!visible) {
    video.pause();
    video.removeAttribute("src");
    download.removeAttribute("href");
    return;
  }
  const cacheKey = stop.result_sha256 ? `?v=${encodeURIComponent(stop.result_sha256.slice(0, 16))}` : "";
  const source = `${stop.result_url}${cacheKey}`;
  if (video.dataset.source !== source) {
    video.dataset.source = source;
    video.src = source;
    video.load();
  }
  download.href = stop.download_url || `${stop.result_url}?download=true`;
  detail.textContent = `${stop.completed_clip_count} completed clip${stop.completed_clip_count === 1 ? "" : "s"} · saved when Production stopped.`;
}

function runVisualSignature(board) {
  if (!board) return "";
  const clock = board.clock
    ? {
        started_at: board.clock.started_at || "",
        ended_at: board.clock.ended_at || "",
        complete: Boolean(board.clock.complete),
        running: Boolean(board.clock.running),
        label: board.clock.label || "",
      }
    : null;
  return JSON.stringify({ ...board, clock });
}

function renderRun() {
  if (!run) return;
  const signature = runVisualSignature(run);
  if (signature === runRenderSignature) {
    paintClock();
    return;
  }
  runRenderSignature = signature;
  const title = document.getElementById("run-title");
  const now = document.getElementById("run-now");
  const count = document.getElementById("run-count");
  const meta = document.getElementById("run-meta");
  const beat = document.getElementById("run-beat");
  const steps = document.getElementById("run-steps");
  const clips = document.getElementById("run-clips");
  const logEl = document.getElementById("run-log");
  const bar = document.getElementById("run-bar");
  const fill = document.getElementById("run-bar-fill");
  const rolling = Boolean(run.rolling || run.farm?.busy || run.finish?.busy);
  const failed = Boolean(run.failed) || (run.tally_note || "").toLowerCase().includes("fail");
  if (title) {
    title.textContent = run.title || "Production";
    title.classList.toggle("long", title.textContent.length > 48);
  }
  if (now) {
    now.textContent = run.now || "";
    now.classList.toggle("fail", failed);
  }
  if (count) {
    count.textContent = run.tally || "—";
  }
  if (meta) {
    const format = run.production_kind === "sample" ? `${run.sample_seconds}s sample` : "full production";
    meta.textContent = format;
  }
  if (beat) {
    const connection = run.monitoring?.connection || "idle";
    if (run.phase === "gpu" || (run.camera === "site" && !run.rolling)) {
      beat.textContent = "Connect required";
      beat.classList.remove("live");
    } else {
      beat.textContent = connection === "live"
        ? "Live · 2.5s"
        : rolling
          ? "Connecting…"
          : failed
            ? "Stopped"
            : "Standby";
      beat.classList.toggle("live", connection === "live" && rolling);
    }
  }
  const total = Number(run.shots) || 0;
  const done = Number(run.farmed) || 0;
  const overall = Number(run.progress?.pct);
  if (fill) {
    if (Number.isFinite(overall)) {
      fill.style.width = `${Math.min(100, Math.max(0, overall))}%`;
    } else {
      fill.style.width = total ? `${Math.min(100, (done / total) * 100)}%` : rolling ? "18%" : "0%";
    }
  }
  if (bar) bar.classList.toggle("rolling", rolling);
  renderProductionStandard();
  renderIncompleteResult();
  paintSignal(run.monitoring);
  paintClipSummary(run.monitoring);
  if (clips) paintClips(clips, run.clips || []);
  if (steps) {
    steps.innerHTML = "";
    (run.steps || []).forEach((step, i) => {
      const li = document.createElement("li");
      if (step.state === "rolling" || step.state === "waiting") li.className = "on";
      if (step.state === "failed") li.className = "failed";
      const mark = String(i + 1).padStart(2, "0");
      const extra = step.note ? `<span>${step.note}</span>` : "";
      li.innerHTML = `<span class="n">${mark}</span><span class="label">${step.label}${extra}</span><span class="state">${STEP_WORD[step.state] || step.state}</span>`;
      steps.appendChild(li);
    });
  }
  if (logEl) {
    paintActivityFocus(run.monitoring, run.activity || []);
    paintActivity(logEl, run.activity || []);
  }
  paintClock();
  farmBusy = Boolean(run.farm?.busy);
  farmDetail = run.farm?.busy ? run.farm.detail || "" : "";
  cameraLive = run.camera === "live";
  const stopButton = document.getElementById("run-stop");
  if (stopButton) {
    stopButton.hidden = !run.can_stop;
    stopButton.disabled = !run.can_stop;
  }
  paintFloors();
}

const PRODUCTION_WATCH_MS = 2500;
const PRODUCTION_WATCH_TIMEOUT_MS = 40000;
let runTimer = null;
let productionWatchInFlight = false;
let productionWatchEnabled = false;
let productionWatchForced = false;
let productionWatchAbort = null;
let productionWatchWasBusy = false;

async function loadRun() {
  const err = document.getElementById("program-err");
  if (err) err.textContent = "";
  const res = await fetch("/api/run");
  run = await res.json();
  if (!res.ok) {
    if (err) err.textContent = failText(run, "Could not load the run.");
    return;
  }
  farmBusy = Boolean(run.farm?.busy);
  renderRun();
  pollRun();
  if (run.farm?.busy) pollFarm();
  if (run.finish?.busy) pollFinish();
}

function pollRun() {
  productionWatchEnabled = true;
  scheduleProductionWatch(0);
}

function productionWatchNeeded() {
  return Boolean(
    productionWatchEnabled &&
      (productionWatchForced || bay === "production" || starting || farmBusy || run?.farm?.busy || run?.finish?.busy)
  );
}

function scheduleProductionWatch(delay = PRODUCTION_WATCH_MS) {
  if (!productionWatchNeeded() || runTimer || productionWatchInFlight) return;
  runTimer = window.setTimeout(productionWatchTick, delay);
}

async function productionWatchTick() {
  runTimer = null;
  if (!productionWatchNeeded()) return;
  if (document.hidden) {
    scheduleProductionWatch();
    return;
  }
  if (productionWatchInFlight) return;
  productionWatchInFlight = true;
  const controller = new AbortController();
  productionWatchAbort = controller;
  const timeout = window.setTimeout(() => controller.abort(), PRODUCTION_WATCH_TIMEOUT_MS);
  try {
    const response = await fetch("/api/run", { cache: "no-store", signal: controller.signal });
    const board = await response.json();
    if (!response.ok) throw new Error(failText(board, "Could not update Production."));
    run = board;
    const busyNow = Boolean(board.farm?.busy || board.finish?.busy);
    productionWatchForced = false;
    farmBusy = Boolean(board.farm?.busy);
    farmDetail = farmBusy ? board.farm?.detail || "Wan2.2 FARM" : "";
    renderRun();

    if (busyNow || productionWatchWasBusy) {
      const projectResponse = await fetch("/api/project", {
        cache: "no-store",
        signal: controller.signal,
      });
      const nextProject = await projectResponse.json();
      if (projectResponse.ok) {
        project = nextProject;
        written = Boolean(project.shots?.length && project.takes?.length && project.id !== "ep-blank");
        if (bay !== "production" || !busyNow) render();
        else paintFloors();
      }
    }
    if (productionWatchWasBusy && !busyNow) {
      await loadGallery();
    }
    productionWatchWasBusy = busyNow;
  } catch (error) {
    if (error?.name !== "AbortError") {
      /* A dropped tunnel keeps the last stable board visible; the next completed cycle retries. */
    }
  } finally {
    window.clearTimeout(timeout);
    productionWatchAbort = null;
    productionWatchInFlight = false;
    if (productionWatchNeeded()) scheduleProductionWatch();
  }
}

document.addEventListener("visibilitychange", () => {
  if (!document.hidden && productionWatchEnabled) scheduleProductionWatch(0);
});

function galleryThumbnail(item) {
  const frame = document.createElement("div");
  frame.className = "gallery-thumb";
  const image = document.createElement("img");
  image.alt = "";
  image.loading = "lazy";
  image.decoding = "async";
  image.src = item.thumbnail_url;
  image.addEventListener("error", () => {
    frame.classList.add("preview-unavailable");
    image.remove();
    setTextNode(frame, "span", "Preview unavailable");
  }, { once: true });
  frame.appendChild(image);
  setTextNode(frame, "span", item.finished ? "1080" : "720", "gallery-resolution");
  return frame;
}

function framePrecision(frame, fps) {
  const rate = Number(fps || 23.976);
  const value = Math.max(0, Number(frame || 0));
  const nominal = Math.abs(rate - 23.976) < 0.01 ? 24 : Math.round(rate);
  let rest = Math.round(value);
  const hours = Math.floor(rest / (nominal * 3600));
  rest %= nominal * 3600;
  const minutes = Math.floor(rest / (nominal * 60));
  rest %= nominal * 60;
  const seconds = Math.floor(rest / nominal);
  const frames = rest % nominal;
  const timecode = [hours, minutes, seconds, frames].map((part) => String(part).padStart(2, "0")).join(":");
  return `${timecode} · ${Math.round(value * 1000 / rate)} ms`;
}

function selectedGalleryItems() {
  const byId = new Map((gallery?.items || []).map((item) => [item.id, item]));
  return gallerySelection.map((id) => byId.get(id)).filter(Boolean);
}

function renderGallerySelection() {
  const mode = document.getElementById("gallery-select-mode");
  const count = document.getElementById("gallery-selection-count");
  const join = document.getElementById("join-selected");
  if (mode) {
    mode.setAttribute("aria-pressed", String(gallerySelectMode));
    mode.textContent = gallerySelectMode ? "Done selecting" : "Select clips";
  }
  if (count) {
    count.textContent = gallerySelection.length
      ? `${gallerySelection.length} selected · order ${gallerySelection.map((_, index) => index + 1).join(" → ")}`
      : "No clips selected";
  }
  if (join) join.disabled = gallerySelection.length < 2;
}

function toggleGallerySelection(clipId, options = {}) {
  const items = (gallery?.items || []).filter((item) => item.playable);
  const currentIndex = items.findIndex((item) => item.id === clipId);
  if (currentIndex < 0) return;
  if (options.shift && gallerySelectionAnchor) {
    const anchorIndex = items.findIndex((item) => item.id === gallerySelectionAnchor);
    if (anchorIndex >= 0) {
      const [start, end] = [anchorIndex, currentIndex].sort((a, b) => a - b);
      const range = items.slice(start, end + 1).map((item) => item.id);
      gallerySelection = [...gallerySelection, ...range.filter((id) => !gallerySelection.includes(id))];
    }
  } else if (gallerySelection.includes(clipId)) {
    gallerySelection = gallerySelection.filter((id) => id !== clipId);
  } else {
    gallerySelection.push(clipId);
  }
  gallerySelectionAnchor = clipId;
  renderGallery();
}

function galleryItemById(clipId) {
  return (gallery?.items || []).find((item) => item.id === clipId) || null;
}

function clearGalleryViewer() {
  const video = document.getElementById("gallery-viewer-video");
  if (video) {
    video.pause();
    video.removeAttribute("src");
    video.removeAttribute("poster");
    video.load();
  }
  watchGalleryId = null;
}

function closeGalleryViewer() {
  const viewer = document.getElementById("gallery-viewer");
  if (viewer?.open) viewer.close();
  clearGalleryViewer();
  renderGallery();
}

function openGalleryViewer(item) {
  const viewer = document.getElementById("gallery-viewer");
  const video = document.getElementById("gallery-viewer-video");
  if (!viewer || !video || !item?.file_url) return;
  watchGalleryId = item.id;
  document.getElementById("gallery-viewer-title").textContent = item.title;
  document.getElementById("gallery-viewer-meta").textContent =
    `${item.plate} · ${secondsLabel(item.duration_frames, item.fps)} · ${Number(item.fps).toFixed(3)} fps`;
  const download = document.getElementById("gallery-viewer-download");
  download.href = item.download_url;
  download.setAttribute("download", "");
  video.poster = item.thumbnail_url;
  video.src = item.file_url;
  if (!viewer.open) viewer.showModal();
  video.play().catch(() => {});
  renderGallery();
}

function galleryDetailsIcon() {
  const namespace = "http://www.w3.org/2000/svg";
  const icon = document.createElementNS(namespace, "svg");
  icon.classList.add("gallery-details-icon");
  icon.setAttribute("viewBox", "0 0 22 24");
  icon.setAttribute("aria-hidden", "true");
  const page = document.createElementNS(namespace, "path");
  page.setAttribute("d", "M4.5 1.75h8.25l4.75 4.8v15.7h-13z");
  const fold = document.createElementNS(namespace, "path");
  fold.setAttribute("d", "M12.75 1.75v4.8h4.75");
  const dot = document.createElementNS(namespace, "circle");
  dot.setAttribute("cx", "11");
  dot.setAttribute("cy", "10.5");
  dot.setAttribute("r", "0.8");
  const line = document.createElementNS(namespace, "path");
  line.setAttribute("d", "M11 13.5v4.25");
  icon.append(page, fold, dot, line);
  return icon;
}

function galleryCopyIcon() {
  const namespace = "http://www.w3.org/2000/svg";
  const icon = document.createElementNS(namespace, "svg");
  icon.classList.add("gallery-copy-icon");
  icon.setAttribute("viewBox", "0 0 24 24");
  icon.setAttribute("aria-hidden", "true");
  const back = document.createElementNS(namespace, "rect");
  back.setAttribute("x", "7.5");
  back.setAttribute("y", "4.5");
  back.setAttribute("width", "12");
  back.setAttribute("height", "12");
  back.setAttribute("rx", "1.25");
  const front = document.createElementNS(namespace, "rect");
  front.setAttribute("x", "4.5");
  front.setAttribute("y", "7.5");
  front.setAttribute("width", "12");
  front.setAttribute("height", "12");
  front.setAttribute("rx", "1.25");
  icon.append(back, front);
  return icon;
}

function galleryFileSize(bytes) {
  const value = Number(bytes || 0);
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(value < 10240 ? 1 : 0)} KB`;
  return `${(value / (1024 * 1024)).toFixed(value < 10 * 1024 * 1024 ? 1 : 0)} MB`;
}

async function copyGalleryPrompt(prompt, button, promptField) {
  const feedback = document.getElementById("gallery-copy-feedback");
  let copied = false;

  if (feedback) {
    feedback.textContent = "";
    feedback.classList.remove("is-error");
  }

  try {
    // Safari requires the selection and legacy copy to happen before the click
    // gesture crosses an async boundary. Chromium also supports this path.
    promptField.focus({ preventScroll: true });
    promptField.select();
    promptField.setSelectionRange(0, promptField.value.length);
    copied = document.execCommand("copy");
  } catch {
    copied = false;
  }

  if (!copied && navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(prompt);
      copied = true;
    } catch {
      copied = false;
    }
  }

  if (!copied) {
    promptField.focus({ preventScroll: true });
    promptField.select();
    promptField.setSelectionRange(0, promptField.value.length);
    if (feedback) {
      feedback.classList.add("is-error");
      feedback.textContent = "Copy was blocked. The prompt is selected—press Command-C on Mac or Ctrl-C on Windows.";
    }
    return;
  }

  promptField.setSelectionRange(0, 0);
  button.focus({ preventScroll: true });
  button.classList.add("is-copied");
  button.setAttribute("aria-label", "Prompt copied");
  button.setAttribute("title", "Prompt copied");
  if (feedback) feedback.textContent = "Prompt copied.";
  window.setTimeout(() => {
    if (!button.isConnected) return;
    button.classList.remove("is-copied");
    button.setAttribute("aria-label", "Copy prompt");
    button.setAttribute("title", "Copy prompt");
  }, 1600);
}

function renderGalleryDetails(details) {
  const body = document.getElementById("gallery-details-body");
  if (!body) return;
  body.replaceChildren();
  const sources = Array.isArray(details?.sources) ? details.sources : [];
  if (!sources.length) {
    setTextNode(body, "p", "No source record is available for this clip.", "gallery-details-empty");
    return;
  }
  sources.forEach((source, index) => {
    const slate = document.createElement("section");
    slate.className = "gallery-source-slate";

    const header = document.createElement("header");
    const sourceNumber = sources.length > 1 ? `Source ${index + 1} of ${sources.length}` : "Original project";
    setTextNode(header, "span", sourceNumber);
    setTextNode(header, "strong", source.title || "Untitled source");
    slate.appendChild(header);

    const promptPanel = document.createElement("div");
    promptPanel.className = "gallery-prompt-panel";
    const promptHead = document.createElement("div");
    setTextNode(promptHead, "h3", "Idea prompt");
    const prompt = String(source.idea_prompt || "").trim();
    const copy = document.createElement("button");
    copy.type = "button";
    copy.className = "gallery-copy-prompt";
    copy.setAttribute("aria-label", "Copy prompt");
    copy.setAttribute("title", "Copy prompt");
    copy.appendChild(galleryCopyIcon());
    copy.disabled = !prompt;
    const promptField = document.createElement("textarea");
    const promptDisplay = prompt || "No written Idea prompt was recorded. This source began with reference files.";
    promptField.className = "gallery-idea-prompt";
    if (!prompt) promptField.classList.add("is-empty");
    promptField.readOnly = true;
    promptField.spellcheck = false;
    promptField.setAttribute("aria-label", "Idea prompt");
    promptField.value = promptDisplay;
    promptField.rows = Math.max(
      4,
      Math.min(12, promptDisplay.split("\n").reduce((rows, line) => rows + Math.max(1, Math.ceil(line.length / 72)), 0)),
    );
    copy.addEventListener("click", () => copyGalleryPrompt(prompt, copy, promptField));
    promptHead.appendChild(copy);
    promptPanel.appendChild(promptHead);
    promptPanel.appendChild(promptField);
    slate.appendChild(promptPanel);

    const referencePanel = document.createElement("div");
    referencePanel.className = "gallery-reference-panel";
    setTextNode(referencePanel, "h3", "Reference files");
    const references = Array.isArray(source.references) ? source.references : [];
    if (!references.length) {
      setTextNode(referencePanel, "p", "No reference files were attached.", "gallery-reference-empty");
    } else {
      const list = document.createElement("ul");
      references.forEach((reference) => {
        const row = document.createElement("li");
        const link = document.createElement("a");
        link.href = reference.download_url;
        link.download = reference.filename || "reference";
        link.setAttribute("aria-label", `Download reference ${reference.filename}`);
        setTextNode(link, "span", "↓", "gallery-reference-download-mark");
        const label = document.createElement("span");
        setTextNode(label, "strong", reference.filename || "Untitled reference");
        setTextNode(
          label,
          "small",
          `${String(reference.kind || "file").replaceAll("_", " ")} · ${galleryFileSize(reference.bytes)}`,
        );
        link.appendChild(label);
        row.appendChild(link);
        list.appendChild(row);
      });
      referencePanel.appendChild(list);
    }
    slate.appendChild(referencePanel);
    body.appendChild(slate);
  });
}

async function openGalleryDetails(item) {
  const dialog = document.getElementById("gallery-details");
  const body = document.getElementById("gallery-details-body");
  if (!dialog || !body || !item?.details_url) return;
  dialog.dataset.clipId = item.id;
  document.getElementById("gallery-details-title").textContent = item.title;
  document.getElementById("gallery-details-meta").textContent =
    `${item.plate} · original Idea${item.source_count === 1 ? "" : "s"} and reference files`;
  body.replaceChildren();
  const feedback = document.getElementById("gallery-copy-feedback");
  if (feedback) {
    feedback.textContent = "";
    feedback.classList.remove("is-error");
  }
  setTextNode(body, "p", "Loading the source record…", "gallery-details-loading");
  if (!dialog.open) dialog.showModal();
  try {
    const details = await responseJson(await fetch(item.details_url));
    if (dialog.dataset.clipId === item.id) renderGalleryDetails(details);
  } catch (error) {
    if (dialog.dataset.clipId !== item.id) return;
    body.replaceChildren();
    setTextNode(body, "p", error.message, "gallery-details-empty");
  }
}

function closeGalleryDetails() {
  const dialog = document.getElementById("gallery-details");
  if (dialog?.open) dialog.close();
}

async function requestGalleryFullscreen() {
  const video = document.getElementById("gallery-viewer-video");
  const viewer = document.getElementById("gallery-viewer");
  if (!video) return;
  try {
    if (video.requestFullscreen) await video.requestFullscreen();
    else if (video.webkitEnterFullscreen) video.webkitEnterFullscreen();
    else if (viewer?.requestFullscreen) await viewer.requestFullscreen();
  } catch {
    /* The focused viewer remains available when the host blocks full screen. */
  }
}

async function removeGalleryItem(item) {
  const keep = window.confirm(`Delete “${item.title}” from Gallery? The master stays recoverable in the vault.`);
  if (!keep) return;
  try {
    const result = await responseJson(await fetch(item.remove_url, { method: "DELETE" }));
    gallerySelection = gallerySelection.filter((id) => id !== item.id);
    if (watchGalleryId === item.id) closeGalleryViewer();
    await loadGallery();
    const note = document.getElementById("gallery-note");
    if (note) note.textContent = result.detail;
  } catch (error) {
    const note = document.getElementById("gallery-note");
    if (note) note.textContent = error.message;
  }
}

function renderGallery() {
  const grid = document.getElementById("gallery-grid");
  const empty = document.getElementById("gallery-empty");
  const meta = document.getElementById("gallery-meta");
  const heading = document.getElementById("gallery-title");
  const note = document.getElementById("gallery-note");
  if (!grid || !gallery) return;
  const items = (gallery.items || []).filter((item) => item.playable);
  if (heading) heading.textContent = "Finished clips";
  if (meta) {
    meta.textContent = items.length
      ? `${items.length} full clip${items.length === 1 ? "" : "s"}`
      : "No clips yet";
  }
  if (note) note.textContent = "Watch, review details, select, download, or remove a finished clip.";
  empty.classList.toggle("hidden", items.length > 0);
  const dist = document.getElementById("lock-distribution");
  if (dist) dist.classList.toggle("hidden", items.length === 0);
  grid.innerHTML = "";
  if (!items.length) {
    if (document.getElementById("gallery-viewer")?.open) closeGalleryViewer();
    watchGalleryId = null;
    gallerySelection = [];
    renderGallerySelection();
    return;
  }
  gallerySelection = gallerySelection.filter((id) => items.some((item) => item.id === id));
  items.forEach((item) => {
    const selectedIndex = gallerySelection.indexOf(item.id);
    const card = document.createElement("article");
    card.className = `gallery-card${item.id === watchGalleryId ? " on" : ""}${selectedIndex >= 0 ? " selected" : ""}`;
    card.dataset.gallery = item.id;
    card.setAttribute("role", "listitem");
    const watch = document.createElement("button");
    watch.type = "button";
    watch.className = "gallery-watch";
    watch.setAttribute("aria-label", `Watch ${item.title}`);
    watch.appendChild(galleryThumbnail(item));
    const copy = document.createElement("div");
    copy.className = "gallery-card-copy";
    setTextNode(copy, "em", `${item.plate}${item.current ? " · this show" : ""}`);
    setTextNode(copy, "span", "Prompt", "gallery-prompt-label");
    setTextNode(
      copy,
      "strong",
      String(item.prompt || item.title).trim() || "No prompt recorded.",
      "gallery-prompt-description",
    );
    setTextNode(
      copy,
      "small",
      `${secondsLabel(item.duration_frames, item.fps)} · ${item.width}×${item.height} · ${item.source_count} source${item.source_count === 1 ? "" : "s"}`,
    );
    watch.appendChild(copy);
    watch.addEventListener("click", (event) => {
      if (gallerySelectMode || event.metaKey || event.ctrlKey || event.shiftKey) {
        toggleGallerySelection(item.id, { shift: event.shiftKey });
        return;
      }
      openGalleryViewer(item);
    });
    card.appendChild(watch);
    const select = document.createElement("button");
    select.type = "button";
    select.className = "gallery-select-cue";
    select.setAttribute("aria-pressed", String(selectedIndex >= 0));
    select.setAttribute("aria-label", selectedIndex >= 0 ? `Remove ${item.title} from join` : `Select ${item.title} for join`);
    select.textContent = selectedIndex >= 0 ? String(selectedIndex + 1) : "+";
    select.addEventListener("click", (event) => toggleGallerySelection(item.id, { shift: event.shiftKey }));
    card.appendChild(select);

    const actions = document.createElement("div");
    actions.className = "gallery-card-actions";
    const watchAction = document.createElement("button");
    watchAction.type = "button";
    watchAction.textContent = "Watch";
    watchAction.addEventListener("click", () => openGalleryViewer(item));
    actions.appendChild(watchAction);

    const details = document.createElement("button");
    details.type = "button";
    details.className = "gallery-details-action";
    details.setAttribute("aria-label", `View Idea prompt and references for ${item.title}`);
    details.append(galleryDetailsIcon(), document.createTextNode("Details"));
    details.addEventListener("click", () => openGalleryDetails(item));
    actions.appendChild(details);

    const fullscreen = document.createElement("button");
    fullscreen.type = "button";
    fullscreen.textContent = "Full screen";
    fullscreen.addEventListener("click", () => {
      openGalleryViewer(item);
      requestGalleryFullscreen();
    });
    actions.appendChild(fullscreen);

    const download = document.createElement("a");
    download.href = item.download_url;
    download.download = "";
    download.textContent = "Download";
    download.setAttribute("aria-label", `Download ${item.title}`);
    actions.appendChild(download);

    if (item.composition_id) {
      const reedit = document.createElement("button");
      reedit.type = "button";
      reedit.className = "gallery-reedit";
      reedit.textContent = "Revise version";
      reedit.addEventListener("click", () => openJoinWorkspace(item.composition_id));
      actions.appendChild(reedit);
    }

    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "gallery-delete";
    remove.textContent = "Delete";
    remove.addEventListener("click", () => removeGalleryItem(item));
    actions.appendChild(remove);
    card.appendChild(actions);
    grid.appendChild(card);
  });
  renderGallerySelection();
}

async function loadGallery() {
  const meta = document.getElementById("gallery-meta");
  const note = document.getElementById("gallery-note");
  if (meta) meta.textContent = "Loading finished clips…";
  try {
    const res = await fetch("/api/gallery");
    const payload = await responseJson(res);
    gallery = payload;
    if (watchGalleryId && !galleryItemById(watchGalleryId)) clearGalleryViewer();
    const detailsDialog = document.getElementById("gallery-details");
    if (detailsDialog?.open && !galleryItemById(detailsDialog.dataset.clipId)) closeGalleryDetails();
    renderGallery();
  } catch (error) {
    if (meta) meta.textContent = gallery ? `${gallery.items?.length || 0} saved clips` : "Gallery unavailable";
    if (note) note.textContent = `Gallery could not be loaded: ${error.message}`;
  }
}

function resetJoinJunctions() {
  if (!joinDraft) return;
  joinDraft.junctions = joinDraft.clip_ids.slice(1).map(() => ({ transition: "cut", duration_frames: 0 }));
}

function joinSourceById(clipId) {
  return (joinPlan?.sources || []).find((source) => source.clip_id === clipId)
    || selectedGalleryItems().find((item) => item.id === clipId);
}

function moveJoinClip(index, delta) {
  if (!joinDraft) return;
  const next = index + delta;
  if (next < 0 || next >= joinDraft.clip_ids.length) return;
  [joinDraft.clip_ids[index], joinDraft.clip_ids[next]] = [joinDraft.clip_ids[next], joinDraft.clip_ids[index]];
  gallerySelection = [...joinDraft.clip_ids];
  resetJoinJunctions();
  renderJoinWorkspace();
  scheduleJoinPlan();
}

function nudgeFrameInput(input, event) {
  if (!event.key.startsWith("Arrow")) return;
  event.preventDefault();
  const direction = event.key === "ArrowUp" || event.key === "ArrowRight" ? 1 : -1;
  const amount = event.shiftKey ? 10 : 1;
  const min = Number(input.min || 0);
  const max = Number(input.max || Number.MAX_SAFE_INTEGER);
  input.value = String(Math.max(min, Math.min(max, Number(input.value || 0) + direction * amount)));
  input.dispatchEvent(new Event("input", { bubbles: true }));
}

function renderJoinSequence() {
  const rail = document.getElementById("join-sequence");
  if (!rail || !joinDraft) return;
  rail.replaceChildren();
  joinDraft.clip_ids.forEach((clipId, index) => {
    const source = joinSourceById(clipId);
    const item = document.createElement("li");
    item.className = "splice-clip";
    item.dataset.clipId = clipId;
    setTextNode(item, "span", String(index + 1), "splice-number");
    const copy = document.createElement("div");
    setTextNode(copy, "strong", source?.title || clipId);
    setTextNode(copy, "em", `${source?.width || 1920}×${source?.height || 1080} · ${Number(source?.fps || 23.976).toFixed(3)} fps`);
    item.appendChild(copy);
    const controls = document.createElement("div");
    controls.className = "splice-order";
    [["↑", -1, "Move earlier"], ["↓", 1, "Move later"]].forEach(([label, delta, aria]) => {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = label;
      button.disabled = index + delta < 0 || index + delta >= joinDraft.clip_ids.length;
      button.setAttribute("aria-label", `${aria}: ${source?.title || clipId}`);
      button.addEventListener("click", () => moveJoinClip(index, delta));
      controls.appendChild(button);
    });
    item.appendChild(controls);
    rail.appendChild(item);
  });
}

function transitionDefault(id) {
  return (joinOptions?.transitions || []).find((row) => row.id === id)?.default_frames ?? 12;
}

function renderJoinJunctions() {
  const host = document.getElementById("join-junctions");
  if (!host || !joinDraft) return;
  host.replaceChildren();
  joinDraft.junctions.forEach((junction, index) => {
    const leftId = joinDraft.clip_ids[index];
    const rightId = joinDraft.clip_ids[index + 1];
    const left = joinSourceById(leftId);
    const right = joinSourceById(rightId);
    const leftDuration = Number(left?.duration_frames || 2);
    const rightDuration = Number(right?.duration_frames || 2);
    const leftTrim = joinDraft.trims[leftId] || { in_frame: 0, out_frame: leftDuration };
    const rightTrim = joinDraft.trims[rightId] || { in_frame: 0, out_frame: rightDuration };
    joinDraft.trims[leftId] = leftTrim;
    joinDraft.trims[rightId] = rightTrim;
    const card = document.createElement("fieldset");
    card.className = "join-junction";
    const legend = document.createElement("legend");
    legend.textContent = `JOIN ${String(index + 1).padStart(2, "0")} · ${left?.title || leftId} → ${right?.title || rightId}`;
    card.appendChild(legend);
    const precision = document.createElement("div");
    precision.className = "join-precision";

    const preciseInput = (label, value, min, max, onInput) => {
      const wrap = document.createElement("label");
      wrap.className = "precision-control";
      setTextNode(wrap, "span", label);
      const input = document.createElement("input");
      input.type = "number";
      input.inputMode = "numeric";
      input.step = "1";
      input.min = String(min);
      input.max = String(max);
      input.value = String(value);
      const readout = setTextNode(wrap, "output", framePrecision(value, joinPlan?.fps || 23.976));
      input.addEventListener("keydown", (event) => nudgeFrameInput(input, event));
      input.addEventListener("input", () => {
        onInput(Number(input.value));
        readout.textContent = framePrecision(input.value, joinPlan?.fps || 23.976);
        scheduleJoinPlan();
      });
      wrap.appendChild(input);
      wrap.appendChild(readout);
      return wrap;
    };
    precision.appendChild(preciseInput("OUT · LEFT", leftTrim.out_frame, leftTrim.in_frame + 2, leftDuration, (value) => { leftTrim.out_frame = value; }));
    precision.appendChild(preciseInput("IN · RIGHT", rightTrim.in_frame, 0, rightTrim.out_frame - 2, (value) => { rightTrim.in_frame = value; }));

    const transition = document.createElement("label");
    transition.className = "precision-control transition-control";
    setTextNode(transition, "span", "TRANSITION");
    const select = document.createElement("select");
    (joinOptions?.transitions || [{ id: "cut", label: "Clean cut" }]).forEach((row) => select.appendChild(new Option(row.label, row.id)));
    select.value = junction.transition;
    select.addEventListener("change", () => {
      junction.transition = select.value;
      junction.duration_frames = select.value === "cut" ? 0 : transitionDefault(select.value);
      renderJoinWorkspace();
      scheduleJoinPlan();
    });
    transition.appendChild(select);
    precision.appendChild(transition);
    precision.appendChild(preciseInput("DURATION · FRAMES", junction.duration_frames, junction.transition === "cut" ? 0 : 2, junction.transition === "cut" ? 0 : 120, (value) => { junction.duration_frames = value; }));
    card.appendChild(precision);
    const reset = document.createElement("button");
    reset.type = "button";
    reset.className = "join-reset";
    reset.textContent = "Reset this join";
    reset.addEventListener("click", () => {
      leftTrim.out_frame = leftDuration;
      rightTrim.in_frame = 0;
      junction.transition = "cut";
      junction.duration_frames = 0;
      renderJoinWorkspace();
      scheduleJoinPlan();
    });
    card.appendChild(reset);
    const previewThis = document.createElement("button");
    previewThis.type = "button";
    previewThis.className = "join-preview-this";
    previewThis.textContent = "Preview this join";
    previewThis.addEventListener("click", () => {
      joinDraft.preview_junction = index;
      previewJoin();
    });
    card.appendChild(previewThis);
    host.appendChild(card);
  });
}

function renderJoinSummary() {
  const summary = document.getElementById("join-summary");
  const normalization = document.getElementById("join-normalization");
  if (!summary) return;
  summary.replaceChildren();
  const facts = joinPlan?.summary
    ? [["New version", `v${joinPlan.version} · ${joinPlan.summary.source_count} sources`], ["Duration", `${joinPlan.summary.timecode} · ${joinPlan.summary.duration_ms} ms`], ["Output", joinPlan.summary.output], ["Attribution", joinPlan.profile_name || "Studio maker"]]
    : [["Result", "Adjust the marked fields to make a valid join."]];
  facts.forEach(([label, value]) => {
    const row = document.createElement("div");
    setTextNode(row, "dt", label);
    setTextNode(row, "dd", value);
    summary.appendChild(row);
  });
  if (normalization) {
    normalization.textContent = (joinPlan?.normalization || []).length
      ? `Before the cut: ${joinPlan.normalization.join(" · ")}`
      : "Sources already match the Studio master standard.";
  }
}

function renderJoinWorkspace() {
  renderJoinSequence();
  renderJoinJunctions();
  renderJoinSummary();
}

function joinRequestBody() {
  return JSON.parse(JSON.stringify(joinDraft));
}

async function refreshJoinPlan() {
  if (!joinDraft) return;
  const error = document.getElementById("gallery-join-error");
  try {
    joinPlan = await responseJson(await fetch("/api/gallery/joins/plan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(joinRequestBody()),
    }));
    error.textContent = "";
  } catch (caught) {
    joinPlan = null;
    error.textContent = caught.message;
  }
  renderJoinSummary();
  document.getElementById("preview-join").disabled = !joinPlan;
  document.getElementById("create-join").disabled = !joinPlan;
}

function scheduleJoinPlan() {
  clearTimeout(joinPlanTimer);
  joinPlanTimer = setTimeout(refreshJoinPlan, 180);
}

async function openJoinWorkspace(compositionId = null) {
  if (!compositionId && gallerySelection.length < 2) return;
  const dialog = document.getElementById("join-workspace");
  try {
    joinOptions ||= await responseJson(await fetch("/api/gallery/joins/options"));
  } catch (error) {
    document.getElementById("gallery-note").textContent = error.message;
    return;
  }
  if (compositionId) {
    let original;
    try {
      original = await responseJson(await fetch(`/api/gallery/joins/${encodeURIComponent(compositionId)}`));
    } catch (error) {
      document.getElementById("gallery-note").textContent = error.message;
      return;
    }
    gallerySelection = original.sources.map((source) => source.clip_id);
    joinDraft = {
      clip_ids: [...gallerySelection],
      trims: Object.fromEntries(original.sources.map((source) => [source.clip_id, {
        in_frame: source.trim_in_frame,
        out_frame: source.trim_out_frame,
      }])),
      junctions: original.junctions.map((junction) => ({
        transition: junction.transition,
        duration_frames: junction.duration_frames,
      })),
      parent_composition_id: original.id,
      preview_junction: 0,
    };
    joinPlan = original;
  } else {
    const items = selectedGalleryItems();
    joinDraft = {
      clip_ids: items.map((item) => item.id),
      trims: Object.fromEntries(items.map((item) => [item.id, { in_frame: 0, out_frame: item.duration_frames }])),
      junctions: items.slice(1).map(() => ({ transition: "cut", duration_frames: 0 })),
      parent_composition_id: items.find((item) => item.composition_id)?.composition_id || null,
      preview_junction: 0,
    };
    joinPlan = null;
  }
  document.getElementById("gallery-join-error").textContent = "";
  document.getElementById("join-preview").hidden = true;
  document.getElementById("join-preview-empty").hidden = false;
  dialog.showModal();
  renderJoinWorkspace();
  await refreshJoinPlan();
  document.getElementById("join-sequence")?.querySelector("button:not(:disabled)")?.focus();
}

function setJoinBusy(busy, label = "Creating the new version…") {
  const progress = document.getElementById("join-progress");
  progress.hidden = !busy;
  progress.querySelector("span").textContent = label;
  document.getElementById("preview-join").disabled = busy || !joinPlan;
  document.getElementById("create-join").disabled = busy || !joinPlan;
  document.getElementById("cancel-join-process").classList.toggle("hidden", !busy || !joinJobId);
}

async function previewJoin() {
  if (!joinPlan) return;
  const error = document.getElementById("gallery-join-error");
  error.textContent = "";
  setJoinBusy(true, "Rendering the join preview…");
  try {
    const preview = await responseJson(await fetch("/api/gallery/joins/preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(joinRequestBody()),
    }));
    const video = document.getElementById("join-preview");
    video.src = `${preview.preview_url}?v=${Date.now()}`;
    video.hidden = false;
    document.getElementById("join-preview-empty").hidden = true;
    await video.play().catch(() => {});
    error.textContent = "";
    document.getElementById("gallery-join-live").textContent = "Join preview ready.";
  } catch (caught) {
    error.textContent = caught.message;
  } finally {
    setJoinBusy(false);
  }
}

function stopJoinJobPoll() {
  if (joinJobTimer) clearInterval(joinJobTimer);
  joinJobTimer = null;
}

async function pollJoinJob() {
  if (!joinJobId) return;
  try {
    const status = await responseJson(await fetch(`/api/gallery/joins/${encodeURIComponent(joinJobId)}`));
    if (["queued", "processing"].includes(status.status)) return;
    stopJoinJobPoll();
    if (status.status === "complete") {
      document.getElementById("gallery-join-live").textContent = "New joined version created in the Gallery.";
      document.getElementById("join-workspace").close();
      gallerySelection = [];
      gallerySelectMode = false;
      joinJobId = null;
      setJoinBusy(false);
      await loadGallery();
      return;
    }
    document.getElementById("gallery-join-error").textContent = status.error || "The joined version was not created. Source clips are unchanged.";
    joinJobId = null;
    setJoinBusy(false);
  } catch (error) {
    stopJoinJobPoll();
    document.getElementById("gallery-join-error").textContent = `${error.message} Retry status without rebuilding the cut.`;
    setJoinBusy(false);
  }
}

async function createJoinedVersion() {
  if (!joinPlan) return;
  setJoinBusy(true);
  try {
    const job = await responseJson(await fetch("/api/gallery/joins", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(joinRequestBody()),
    }));
    joinJobId = job.id;
    setJoinBusy(true);
    stopJoinJobPoll();
    joinJobTimer = setInterval(pollJoinJob, 750);
    pollJoinJob();
  } catch (error) {
    setJoinBusy(false);
    document.getElementById("gallery-join-error").textContent = error.message;
  }
}

async function cancelJoinedVersion() {
  if (!joinJobId) return;
  try {
    await responseJson(await fetch(`/api/gallery/joins/${encodeURIComponent(joinJobId)}`, { method: "DELETE" }));
    document.getElementById("join-progress").querySelector("span").textContent = "Cancelling safely…";
  } catch (error) {
    document.getElementById("gallery-join-error").textContent = error.message;
  }
}

document.getElementById("gallery-select-mode")?.addEventListener("click", () => {
  gallerySelectMode = !gallerySelectMode;
  renderGallery();
});
document.getElementById("gallery-viewer-close")?.addEventListener("click", closeGalleryViewer);
document.getElementById("gallery-viewer-fullscreen")?.addEventListener("click", requestGalleryFullscreen);
document.getElementById("gallery-viewer")?.addEventListener("close", () => {
  clearGalleryViewer();
  renderGallery();
});
document.getElementById("gallery-details-close")?.addEventListener("click", closeGalleryDetails);
document.getElementById("join-selected")?.addEventListener("click", openJoinWorkspace);
document.getElementById("preview-join")?.addEventListener("click", previewJoin);
document.getElementById("create-join")?.addEventListener("click", createJoinedVersion);
document.getElementById("cancel-join-process")?.addEventListener("click", cancelJoinedVersion);
document.getElementById("close-join-workspace")?.addEventListener("click", () => {
  if (joinJobId) {
    document.getElementById("gallery-join-error").textContent = "Cancel the active process before closing this cut.";
    return;
  }
  document.getElementById("join-workspace").close();
});

function renderCues() {
  if (!cuesEl) return;
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

function renderSiteStatus() {
  if (!siteLine) return;
  const camera = cameraLive
    ? "Wan · live 720p"
    : run?.camera === "site"
      ? "GPU ready"
      : "Preview stub";
  const items = site?.signed
    ? [
        { label: "Site", value: site.label || "Signed site" },
        {
          label: "Engine plan",
          value: site.sequential ? "One at a time" : "Parallel split",
        },
        site.sequential
          ? { label: "Handoff", value: "Drain → unload → load" }
          : { label: "Split", value: site.split || "2 brain + 2 Wan" },
        { label: "Camera", value: camera, live: cameraLive },
      ]
    : [{ label: "Camera", value: camera, live: cameraLive }];

  const nodes = items.map((item) => {
    const node = document.createElement("span");
    node.className = `site-status-item${item.live ? " is-live" : ""}`;
    const label = document.createElement("span");
    label.className = "site-status-label";
    label.textContent = item.label;
    const value = document.createElement("strong");
    value.className = "site-status-value";
    value.textContent = item.value;
    node.append(label, value);
    return node;
  });
  siteLine.replaceChildren(...nodes);
  siteLine.setAttribute(
    "aria-label",
    items.map((item) => `${item.label}: ${item.value}`).join(". ")
  );
}

function render() {
  titleEl.textContent = project.bible.title;
  loglineEl.textContent = project.bible.logline;
  hiredEl.textContent = KIND_HIRED[project.customer_kind] || project.customer_kind;
  trtEl.textContent = allotmentLabel(targetMinutes);
  paintAllotment();
  if (site?.sequential) {
    fleetEl.textContent = `${project.fleet_mode} · sequential ${site.recipe}`;
  } else {
    fleetEl.textContent = project.fleet_mode;
  }
  renderSiteStatus();
  customerKind = project.customer_kind || customerKind;
  hintKeys.classList.toggle("hidden", written);
  renderCues();
  renderBench();
  if (run) renderRun();
  if (gallery) renderGallery();
  paintFloors();
}

async function load() {
  const requestedBay = new URLSearchParams(location.search).get("bay");
  const [p, c, s, r] = await Promise.all([
    fetch("/api/project"),
    fetch("/api/catalog"),
    fetch("/api/site"),
    fetch("/api/run", { cache: "no-store" }).catch(() => null),
  ]);
  project = await p.json();
  const cat = await c.json();
  catalog = cat.customers;
  if (Array.isArray(cat.allotments) && cat.allotments.length) {
    allotments = cat.allotments;
  }
  cameraLive = Boolean(cat.live);
  site = (await s.json()) || cat.site || null;
  run = r?.ok ? await r.json() : null;
  written = Boolean(project.shots?.length && project.takes?.length && project.id !== "ep-blank");
  if (project.brief && typeof project.brief.target_minutes === "number" && !written) {
    targetMinutes = project.brief.target_minutes;
  }
  render();
  const requested = ["production", "gallery"].includes(requestedBay) ? requestedBay : "";
  const productionActive = Boolean(
    run?.rolling || run?.can_stop || run?.farm?.busy || run?.finish?.busy || run?.mix?.busy
  );
  showBay(requested || (productionActive ? "production" : "idea"));
}

function pollFinish() {
  productionWatchForced = true;
  pollRun();
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

function pollFarm() {
  productionWatchForced = true;
  pollRun();
}

function failText(body, fallback) {
  const detail = body?.detail;
  if (typeof detail === "string" && detail) return detail;
  if (Array.isArray(detail) && detail.length) {
    return detail.map((item) => item.msg || item).join(" ");
  }
  return fallback;
}

async function makeStudio({ production_kind = "program", sample_seconds = null } = {}) {
  if (busy) return;
  ideaErr.textContent = "";
  const idea = ideaEl.value.trim();
  if (idea.length < 8 && !bench.length) {
    ideaErr.textContent = "Write an idea (8+ characters), or attach a reference.";
    return;
  }
  busy = true;
  const isSample = production_kind === "sample";
  castBtn.disabled = true;
  if (sampleBtn) {
    sampleBtn.disabled = true;
    sampleBtn.textContent = isSample ? "Starting sample…" : "Make sample";
  }
  starting = true;
  farmBusy = true;
  written = true;
  showBay("production");
  try {
    const farm = await fetch("/api/farm").then((r) => r.json());
    if (farm.busy) {
      pollFarm();
      return;
    }
    const res = await fetch("/api/studio/open", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        idea: ideaEl.value,
        customer_kind: customerKind,
        target_minutes: isSample ? sample_seconds / 60 : targetMinutes,
        production_kind,
        sample_seconds,
        title_hint: titleHintEl.value || idea.split("\n")[0].slice(0, 80),
        attachment_ids: bench.map((a) => a.id),
      }),
    });
    const body = await res.json();
    if (!res.ok) {
      ideaErr.textContent = failText(body, "Write on the page, or drop a file.");
      const stuck = res.status === 409 || res.status === 503;
      if (stuck) {
        showBay("production");
        await loadRun();
        if (res.status === 409) pollFarm();
        return;
      }
      starting = false;
      written = Boolean(project?.takes?.length);
      if (!productionReady()) showBay("idea");
      return;
    }
    project = body;
    const rolled = await fetch("/api/studio/roll", { method: "POST" });
    const rolledBody = await rolled.json();
    if (!rolled.ok) {
      ideaErr.textContent = failText(rolledBody, "Could not roll the picture.");
      return;
    }
    project = rolledBody;
    written = true;
    render();
    pollFarm();
  } catch (err) {
    ideaErr.textContent = err.message || "Produce failed.";
    if (!run?.rolling && !run?.farm?.busy && !project?.takes?.length) {
      starting = false;
      written = false;
      showBay("idea");
    }
  } finally {
    busy = false;
    starting = false;
    castBtn.disabled = false;
    if (sampleBtn) {
      sampleBtn.disabled = false;
      sampleBtn.textContent = "Make sample";
    }
    paintFloors();
  }
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

ideaEl.addEventListener("keydown", (ev) => {
  if (ev.key === "Enter" && (ev.metaKey || ev.ctrlKey)) {
    ev.preventDefault();
    makeStudio();
  }
});

castBtn.addEventListener("click", () => makeStudio());

function stopRunWatch() {
  clearTimeout(joinPlanTimer);
  joinPlanTimer = null;
  stopJoinJobPoll();
  productionWatchEnabled = false;
  productionWatchForced = false;
  if (runTimer) {
    clearTimeout(runTimer);
    runTimer = null;
  }
  if (productionWatchAbort) {
    productionWatchAbort.abort();
    productionWatchAbort = null;
  }
  productionWatchWasBusy = false;
  runRenderSignature = "";
  if (clockTimer) {
    clearInterval(clockTimer);
    clockTimer = null;
  }
  farmBusy = false;
  farmDetail = "";
  starting = false;
  written = false;
  busy = false;
  project = null;
  run = null;
  program = null;
  gallery = null;
  gallerySelection = [];
  gallerySelectMode = false;
  gallerySelectionAnchor = null;
  joinDraft = null;
  joinPlan = null;
  joinJobId = null;
  bench = [];
}

function refreshProductionPage() {
  const btn = document.getElementById("run-refresh");
  const refreshLabel = btn ? btn.querySelector("[data-run-refresh-label]") : null;
  if (btn) {
    btn.disabled = true;
    btn.setAttribute("aria-busy", "true");
  }
  if (refreshLabel) refreshLabel.textContent = "Refreshing…";
  stopRunWatch();
  window.location.replace("/?bay=production&r=" + Date.now());
}

async function startOverStudio() {
  const mast = document.getElementById("start-over");
  if (run?.can_stop) {
    openProductionStop();
    return;
  }
  if (mast) mast.disabled = true;
  stopRunWatch();
  try {
    const res = await fetch("/api/studio/new", { method: "POST", cache: "no-store" });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      if (errEl) {
        errEl.textContent = body.detail || "Start over cannot interrupt an active production. Use STOP.";
      }
      return;
    }
    window.location.replace("/?r=" + Date.now());
  } catch {
    if (errEl) errEl.textContent = "Start over could not reach Producer. Nothing was changed.";
  } finally {
    if (mast) mast.disabled = false;
  }
}

function openProductionStop() {
  if (!productionStopDialog || !run?.can_stop) return;
  const error = document.getElementById("production-stop-error");
  if (error) error.textContent = "";
  productionStopDialog.showModal();
  productionStopDialog.querySelector('[data-stop-disposition="keep"]')?.focus();
}

async function submitProductionStop(disposition) {
  if (!productionStopDialog || !["keep", "delete"].includes(disposition)) return;
  const error = document.getElementById("production-stop-error");
  const choices = [...productionStopDialog.querySelectorAll("[data-stop-disposition]")];
  if (error) error.textContent = "";
  choices.forEach((choice) => {
    choice.disabled = true;
    choice.setAttribute("aria-busy", "true");
  });
  try {
    const response = await fetch("/api/production/stop", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ disposition }),
    });
    const body = await response.json();
    if (!response.ok) throw new Error(failText(body, "Production could not be stopped."));
    run = body;
    runRenderSignature = "";
    const projectResponse = await fetch("/api/project", { cache: "no-store" });
    if (projectResponse.ok) project = await projectResponse.json();
    farmBusy = false;
    farmDetail = "";
    productionStopDialog.close();
    renderRun();
    paintFloors();
  } catch (stopError) {
    if (error) error.textContent = stopError.message || "Production could not be stopped.";
  } finally {
    choices.forEach((choice) => {
      choice.disabled = false;
      choice.removeAttribute("aria-busy");
    });
  }
}

document.getElementById("run-stop")?.addEventListener("click", openProductionStop);
document.getElementById("production-stop-cancel")?.addEventListener("click", () => {
  productionStopDialog?.close();
});
productionStopDialog?.addEventListener("click", (event) => {
  const choice = event.target.closest("[data-stop-disposition]");
  if (choice) submitProductionStop(choice.dataset.stopDisposition);
});

document.getElementById("run-refresh")?.addEventListener("click", refreshProductionPage);
document.getElementById("start-over")?.addEventListener("click", startOverStudio);

function selectSampleSeconds(value) {
  const seconds = Number(value) === 10 ? 10 : 5;
  document.querySelectorAll("[data-sample-seconds]").forEach((choice) => {
    choice.setAttribute("aria-checked", String(Number(choice.dataset.sampleSeconds) === seconds));
  });
  return seconds;
}

function selectedSampleSeconds() {
  const selected = document.querySelector('[data-sample-seconds][aria-checked="true"]');
  return Number(selected?.dataset.sampleSeconds) === 10 ? 10 : 5;
}

document.getElementById("sample-length")?.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-sample-seconds]");
  if (!button) return;
  selectSampleSeconds(button.dataset.sampleSeconds);
});

document.getElementById("make-sample")?.addEventListener("click", () => {
  makeStudio({ production_kind: "sample", sample_seconds: selectedSampleSeconds() });
});

document.getElementById("open-gallery")?.addEventListener("click", () => {
  showBay("gallery");
});

["lock-bible", "lock-distribution"].forEach((id) => {
  const btn = document.getElementById(id);
  if (!btn) return;
  const name = id.replace("lock-", "");
  btn.addEventListener("click", () => lock(name));
});

document.getElementById("allotment-picks")?.addEventListener("click", (ev) => {
  const btn = ev.target.closest("button[data-minutes]");
  if (!btn) return;
  ev.preventDefault();
  const next = Number(btn.dataset.minutes);
  if (!Number.isFinite(next)) return;
  targetMinutes = next;
  paintAllotment();
});

function setTextNode(parent, tag, text, className = "") {
  const node = document.createElement(tag);
  node.textContent = text;
  if (className) node.className = className;
  parent.appendChild(node);
  return node;
}

async function responseJson(res) {
  let body = null;
  try {
    body = await res.json();
  } catch {
    body = null;
  }
  if (!res.ok) throw new Error(body?.detail || "The Studio could not complete that action.");
  return body;
}

function governanceRow(primary, secondary) {
  const row = document.createElement("div");
  row.className = "governance-row";
  setTextNode(row, "strong", primary);
  setTextNode(row, "small", secondary);
  return row;
}

let governanceMembers = [];
let governanceDepartments = [];
let governanceCostCenters = [];
let governanceCanManageMembers = false;
let governanceCanManageCosts = false;

function assignedCenterIds(member) {
  return Array.from(new Set([
    member.personal_cost_center_id,
    ...(member.assigned_cost_center_ids || []),
  ].filter(Boolean)));
}

async function assignCostCenter(member, centerId) {
  const costCenterIds = Array.from(new Set([...assignedCenterIds(member), centerId]));
  return responseJson(await fetch(`/api/v1/studio/members/${encodeURIComponent(member.id)}/cost-centers`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ cost_center_ids: costCenterIds }),
  }));
}

function populateCostCenterMemberSelect() {
  const select = document.getElementById("cost-center-member");
  if (!select) return;
  const current = select.value;
  select.replaceChildren(new Option("Choose a Studio member", ""));
  governanceMembers
    .filter((member) => (member.status || "active") === "active")
    .forEach((member) => select.appendChild(new Option(member.profile_name || member.user_id, member.id)));
  if (governanceMembers.some((member) => member.id === current)) select.value = current;
}

function paintCostCenterRows(rows, members) {
  const list = document.getElementById("cost-center-list");
  const count = document.getElementById("cost-center-count");
  if (!list) return;
  const active = rows.filter((center) => center.active !== false);
  list.replaceChildren();
  if (count) count.textContent = String(active.length);
  if (!active.length) {
    setTextNode(list, "p", "No named Cost Centers yet.", "hint");
    return;
  }
  active.forEach((center) => {
    const assigned = members.filter((member) => assignedCenterIds(member).includes(center.id));
    const names = assigned.map((member) => member.profile_name || member.user_id);
    const row = governanceRow(
      center.name,
      `${center.code} · ${names.length ? names.join(", ") : "no members assigned"}`,
    );
    const actions = document.createElement("div");
    actions.className = "governance-actions";
    const memberSelect = document.createElement("select");
    memberSelect.setAttribute("aria-label", `Assign a member to ${center.name}`);
    memberSelect.appendChild(new Option("Assign member…", ""));
    members
      .filter((member) => (member.status || "active") === "active" && !assignedCenterIds(member).includes(center.id))
      .forEach((member) => memberSelect.appendChild(new Option(member.profile_name || member.user_id, member.id)));
    const assign = setTextNode(actions, "button", "Assign", "word");
    assign.type = "button";
    assign.addEventListener("click", async () => {
      const member = members.find((candidate) => candidate.id === memberSelect.value);
      if (!member) {
        document.getElementById("cost-center-error").textContent = "Choose a Studio member first.";
        return;
      }
      try {
        await assignCostCenter(member, center.id);
        document.getElementById("studio-live").textContent = `${member.profile_name || member.user_id} is assigned to ${center.name}.`;
        await loadStudioGovernance();
      } catch (error) {
        document.getElementById("cost-center-error").textContent = error.message;
      }
    });
    const rename = setTextNode(actions, "button", "Rename", "word");
    rename.type = "button";
    rename.addEventListener("click", async () => {
      const name = window.prompt("Cost Center name", center.name);
      if (!name?.trim() || name.trim() === center.name) return;
      try {
        await responseJson(await fetch(`/api/v1/cost-centers/${encodeURIComponent(center.id)}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ name: name.trim() }),
        }));
        document.getElementById("studio-live").textContent = `Cost Center renamed to ${name.trim()}.`;
        await loadStudioGovernance();
      } catch (error) {
        document.getElementById("cost-center-error").textContent = error.message;
      }
    });
    actions.prepend(memberSelect);
    if (governanceCanManageCosts) row.appendChild(actions);
    list.appendChild(row);
  });
}

function paintInvitationRows(rows) {
  const list = document.getElementById("invitation-list");
  const count = document.getElementById("invite-count");
  if (!list) return;
  list.replaceChildren();
  if (count) count.textContent = String(rows.length);
  if (!rows.length) {
    setTextNode(list, "p", "No open invitations. Create one when the next maker is ready.", "hint");
    return;
  }
  rows.slice().reverse().forEach((invite) => {
    const delivery = invite.delivery || {};
    const row = governanceRow(
      invite.email,
      `${invite.role} · ${invite.status} · email ${delivery.status || "queued"}`,
    );
    if (["active", "partially_used"].includes(invite.status)) {
      const actions = document.createElement("div");
      actions.className = "governance-actions";
      const revoke = setTextNode(actions, "button", "Revoke", "word");
      revoke.type = "button";
      revoke.addEventListener("click", async () => {
        try {
          await responseJson(await fetch(`/api/v1/studio/invitations/${invite.id}/revoke`, { method: "POST" }));
          document.getElementById("studio-live").textContent = "Invitation revoked. It cannot open the Studio now.";
          loadStudioGovernance();
        } catch (error) {
          document.getElementById("invite-error").textContent = error.message;
        }
      });
      row.appendChild(actions);
    }
    list.appendChild(row);
  });
}

function paintRequestRows(rows) {
  const list = document.getElementById("request-list");
  const count = document.getElementById("request-count");
  if (!list) return;
  const pending = rows.filter((row) => row.status === "pending");
  list.replaceChildren();
  if (count) count.textContent = String(pending.length);
  if (!pending.length) {
    setTextNode(list, "p", "No one is waiting at the door.", "hint");
    return;
  }
  pending.forEach((request) => {
    const row = governanceRow(request.requested_profile_name, `${request.requested_role} · pending`);
    const actions = document.createElement("div");
    actions.className = "governance-actions";
    const approve = setTextNode(actions, "button", "Approve", "word");
    approve.type = "button";
    approve.addEventListener("click", async () => {
      try {
        await responseJson(await fetch(`/api/v1/studio/join-requests/${request.id}/approve`, { method: "POST" }));
        document.getElementById("studio-live").textContent = `${request.requested_profile_name} joined the Studio.`;
        loadStudioGovernance();
      } catch (error) {
        document.getElementById("invite-error").textContent = error.message;
      }
    });
    const deny = setTextNode(actions, "button", "Deny", "word");
    deny.type = "button";
    deny.addEventListener("click", async () => {
      const note = window.prompt("Optional note for the maker", "") ?? "";
      try {
        await responseJson(await fetch(`/api/v1/studio/join-requests/${request.id}/deny`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ note }),
        }));
        document.getElementById("studio-live").textContent = "Join request denied.";
        loadStudioGovernance();
      } catch (error) {
        document.getElementById("invite-error").textContent = error.message;
      }
    });
    row.appendChild(actions);
    list.appendChild(row);
  });
}

function paintMemberRows(rows) {
  const list = document.getElementById("member-list");
  const count = document.getElementById("member-count");
  if (!list) return;
  list.replaceChildren();
  if (count) count.textContent = String(rows.length);
  rows.forEach((member) => {
    const row = governanceRow(
      member.profile_name || member.user_id,
      `${member.role} · ${member.status || "active"} · ${assignedCenterIds(member).length} Cost Center${assignedCenterIds(member).length === 1 ? "" : "s"}`,
    );
    if (governanceCanManageMembers && (member.status || "active") === "active" && member.role !== "owner") {
      const actions = document.createElement("div");
      actions.className = "governance-actions";
      const remove = setTextNode(actions, "button", "Remove", "word");
      remove.type = "button";
      remove.addEventListener("click", async () => {
        const exact = `REMOVE ${member.profile_name}`;
        const confirmation = window.prompt(
          `${member.profile_name} loses Studio access now. Their work and cost history stay attributed. Type ${exact}.`,
          "",
        );
        if (confirmation === null) return;
        try {
          await responseJson(await fetch(`/api/v1/studio/members/${encodeURIComponent(member.id)}/remove`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ confirmation }),
          }));
          document.getElementById("studio-live").textContent = `${member.profile_name} was removed from the Studio.`;
          loadStudioGovernance();
        } catch (error) {
          document.getElementById("invite-error").textContent = error.message;
        }
      });
      row.appendChild(actions);
    }
    list.appendChild(row);
  });
}

async function loadStudioGovernance() {
  const context = document.getElementById("studio-context-line");
  try {
    const studio = await fetch("/api/v1/studio").then(responseJson);
    governanceCanManageMembers = Boolean(studio.can_manage_members);
    governanceCanManageCosts = studio.cost_scope === "organization";
    document.querySelectorAll(".member-admin-only").forEach((section) => {
      section.hidden = !governanceCanManageMembers;
    });
    document.querySelectorAll(".cost-admin-only").forEach((control) => {
      control.hidden = !governanceCanManageCosts;
    });
    const [members, costCenters, departments, invitations, requests] = await Promise.all([
      fetch("/api/v1/members").then(responseJson),
      fetch("/api/v1/cost-centers").then(responseJson),
      fetch("/api/v1/departments").then(responseJson),
      governanceCanManageMembers
        ? fetch("/api/v1/studio/invitations").then(responseJson)
        : Promise.resolve({ invitations: [] }),
      governanceCanManageMembers
        ? fetch("/api/v1/studio/join-requests").then(responseJson)
        : Promise.resolve({ requests: [] }),
    ]);
    const name = studio.studio.name;
    document.getElementById("studio-drawer-title").textContent = name;
    if (context) context.textContent = `${studio.membership.profile_name || "Studio maker"} · ${studio.membership.role} · ${studio.cost_scope} cost view`;
    governanceMembers = members.members || [];
    governanceCostCenters = costCenters.cost_centers || [];
    governanceDepartments = departments.departments || [];
    paintInvitationRows(invitations.invitations || []);
    paintRequestRows(requests.requests || []);
    paintMemberRows(governanceMembers);
    populateCostCenterMemberSelect();
    paintCostCenterRows(governanceCostCenters, governanceMembers);
  } catch (error) {
    if (context) context.textContent = error.message;
  }
}

document.getElementById("studio-control")?.addEventListener("click", () => {
  const drawer = document.getElementById("studio-drawer");
  drawer.hidden = false;
  document.getElementById("studio-drawer-title")?.focus();
  loadStudioGovernance();
});

document.getElementById("close-studio")?.addEventListener("click", () => {
  document.getElementById("studio-drawer").hidden = true;
  document.getElementById("studio-control")?.focus();
});

function updateInvitePreview() {
  const profile = document.getElementById("invite-profile")?.value.trim();
  const preview = document.getElementById("invite-preview");
  if (!preview) return;
  const strong = preview.querySelector("strong");
  if (strong) strong.textContent = profile
    ? `${profile.toUpperCase()}, YOU'VE BEEN CALLED INTO THE STUDIO.`
    : "YOU'VE BEEN CALLED INTO THE STUDIO.";
}

document.getElementById("invite-profile")?.addEventListener("input", updateInvitePreview);
document.getElementById("invite-form")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const error = document.getElementById("invite-error");
  const submit = form.querySelector('button[type="submit"]');
  const submitLabel = submit.querySelector("[data-invite-action-label]");
  error.textContent = "";
  submit.disabled = true;
  submitLabel.textContent = "Sending…";
  const data = new FormData(form);
  try {
    const created = await responseJson(await fetch("/api/v1/studio/invitations", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        email: data.get("email"),
        role: data.get("role"),
        proposed_profile_name: data.get("proposed_profile_name"),
        note: data.get("note"),
        expires_hours: Number(data.get("expires_hours")),
      }),
    }));
    form.reset();
    updateInvitePreview();
    document.getElementById("studio-live").textContent = `Invitation ${created.delivery.status} to ${created.invitation.email}.`;
    await loadStudioGovernance();
  } catch (caught) {
    error.textContent = caught.message;
  } finally {
    submit.disabled = false;
    submitLabel.textContent = "Send invitation";
  }
});

document.getElementById("cost-center-form")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const error = document.getElementById("cost-center-error");
  const submit = form.querySelector('button[type="submit"]');
  const data = new FormData(form);
  const name = String(data.get("name") || "").trim();
  const member = governanceMembers.find((candidate) => candidate.id === data.get("membership_id"));
  const department = governanceDepartments.find((candidate) =>
    (candidate.team_ids || []).some((teamId) => (member?.team_ids || []).includes(teamId)),
  ) || governanceDepartments[0];
  error.textContent = "";
  if (!member || !department) {
    error.textContent = "Choose a member in a configured Studio Department.";
    return;
  }
  submit.disabled = true;
  submit.textContent = "Creating…";
  const code = name
    .normalize("NFKD")
    .replace(/[^a-zA-Z0-9]+/g, "-")
    .replace(/^-|-$/g, "")
    .slice(0, 40)
    .toUpperCase() || `CENTER-${Date.now().toString().slice(-6)}`;
  try {
    const created = await responseJson(await fetch("/api/v1/cost-centers", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name,
        code,
        department_id: department.id,
        team_ids: member.team_ids || [],
      }),
    }));
    await assignCostCenter(member, created.id);
    form.reset();
    document.getElementById("studio-live").textContent = `${created.name} was created and assigned to ${member.profile_name || member.user_id}.`;
    await loadStudioGovernance();
  } catch (caught) {
    error.textContent = caught.message;
  } finally {
    submit.disabled = false;
    submit.textContent = "Create & assign Cost Center";
  }
});

let joinInvitationToken = "";
let joinVerificationTicket = "";
let joinPassportData = null;

function joinTokenFrom(value) {
  const clean = String(value || "").trim();
  if (!clean) return "";
  try {
    const url = new URL(clean);
    return new URLSearchParams(url.hash.slice(1)).get("invite") || clean;
  } catch {
    return clean.replace(/^#?invite=/, "");
  }
}

function showJoinStep(id) {
  ["join-find", "join-review", "join-verify", "join-profile-step", "join-arrival"].forEach((stepId) => {
    const el = document.getElementById(stepId);
    if (el) el.hidden = stepId !== id;
  });
}

function passportRow(label, value) {
  const passport = document.getElementById("join-passport");
  setTextNode(passport, "dt", label);
  setTextNode(passport, "dd", value || "—");
}

async function discoverJoinInvitation(raw) {
  const error = document.getElementById("join-error");
  error.textContent = "";
  joinInvitationToken = joinTokenFrom(raw);
  if (!joinInvitationToken) {
    showJoinStep("join-find");
    return;
  }
  try {
    joinPassportData = await responseJson(await fetch("/api/studio-entry/discover", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token: joinInvitationToken }),
    }));
    history.replaceState(null, "", `${location.pathname}#join`);
    const passport = document.getElementById("join-passport");
    passport.replaceChildren();
    passportRow("Studio", joinPassportData.studio_name);
    passportRow("Production site", joinPassportData.site_label);
    passportRow("Role", joinPassportData.role);
    passportRow("Approval", joinPassportData.approval_required ? "Studio approval required" : "Joins when verified");
    passportRow("Fingerprint", joinPassportData.controller_fingerprint);
    document.getElementById("join-profile").value = joinPassportData.proposed_profile_name || "";
    showJoinStep("join-review");
    document.getElementById("join-title").textContent = "CHECK THE STUDIO BEFORE YOU ENTER.";
  } catch (caught) {
    error.textContent = caught.message;
    showJoinStep("join-find");
  }
}

document.getElementById("join-find")?.addEventListener("submit", (event) => {
  event.preventDefault();
  discoverJoinInvitation(document.getElementById("join-token").value);
});

document.getElementById("join-review-continue")?.addEventListener("click", async () => {
  try {
    const requested = await responseJson(await fetch("/api/studio-entry/verification/request", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token: joinInvitationToken }),
    }));
    document.getElementById("join-code-hint").textContent = `Enter the code sent to ${requested.recipient_mask}.`;
    showJoinStep("join-verify");
    document.getElementById("join-code").focus();
  } catch (caught) {
    document.getElementById("join-error").textContent = caught.message;
  }
});

document.getElementById("join-verify")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const confirmed = await responseJson(await fetch("/api/studio-entry/verification/confirm", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token: joinInvitationToken, code: document.getElementById("join-code").value }),
    }));
    joinVerificationTicket = confirmed.verification_ticket;
    const profile = document.getElementById("join-profile").value || "your Profile Name";
    document.getElementById("join-accept-summary").textContent = `Join ${joinPassportData.studio_name} as ${profile} with ${joinPassportData.role} access. Diony creates your personal Cost Center now.`;
    showJoinStep("join-profile-step");
    document.getElementById("join-profile").focus();
  } catch (caught) {
    document.getElementById("join-error").textContent = caught.message;
  }
});

document.getElementById("join-profile")?.addEventListener("input", (event) => {
  if (!joinPassportData) return;
  document.getElementById("join-accept-summary").textContent = `Join ${joinPassportData.studio_name} as ${event.target.value || "your Profile Name"} with ${joinPassportData.role} access. Diony creates your personal Cost Center now.`;
});

document.getElementById("join-profile-step")?.addEventListener("submit", async (event) => {
  event.preventDefault();
  const error = document.getElementById("join-error");
  error.textContent = "";
  try {
    const joined = await responseJson(await fetch("/api/studio-entry/join", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        token: joinInvitationToken,
        verification_ticket: joinVerificationTicket,
        profile_name: document.getElementById("join-profile").value,
      }),
    }));
    if (joined.status === "pending") {
      document.getElementById("join-arrival-copy").textContent = `${joined.studio_name} has your request. You can close this window.`;
      document.getElementById("join-arrival-facts").replaceChildren();
      document.getElementById("enter-producer").hidden = true;
      showJoinStep("join-arrival");
      return;
    }
    sessionStorage.setItem("diony_session", joined.session_token);
    document.getElementById("join-arrival-copy").textContent = `You are now part of ${joined.studio_name} as ${joined.membership.profile_name}.`;
    const facts = document.getElementById("join-arrival-facts");
    facts.replaceChildren();
    [["Role", joined.membership.role], ["Personal Cost Center", joined.cost_center.code]].forEach(([label, value]) => {
      setTextNode(facts, "dt", label);
      setTextNode(facts, "dd", value);
    });
    document.getElementById("enter-producer").hidden = false;
    showJoinStep("join-arrival");
    document.getElementById("join-live").textContent = `You are now part of ${joined.studio_name}.`;
  } catch (caught) {
    error.textContent = caught.message;
  }
});

document.getElementById("enter-producer")?.addEventListener("click", () => {
  window.location.replace("/");
});

function bootJoinSurface() {
  const params = new URLSearchParams(location.hash.slice(1));
  if (!location.hash.startsWith("#join") && !params.has("invite")) return;
  const surface = document.getElementById("join-studio");
  document.body.classList.add("join-open");
  surface.hidden = false;
  [document.querySelector("header.mast"), document.querySelector(".studio"), document.getElementById("studio-drawer")]
    .filter(Boolean)
    .forEach((background) => {
      background.inert = true;
      background.setAttribute("aria-hidden", "true");
    });
  document.getElementById("join-title")?.focus();
  const token = params.get("invite");
  if (token) discoverJoinInvitation(token);
  else showJoinStep("join-find");
}

paintAllotment();
bootJoinSurface();
load();
