// Diony Producer — the Gallery of Director films and the Studio governance
// drawer. Generation lives in the Director; nothing here renders a frame.
const nativeFetch = window.fetch.bind(window);
window.fetch = (input, init = {}) => {
  const token = sessionStorage.getItem("diony_session");
  const url = typeof input === "string" ? input : input?.url || "";
  if (!token || !url.startsWith("/api/")) return nativeFetch(input, init);
  const headers = new Headers(init.headers || {});
  if (!headers.has("Authorization")) headers.set("Authorization", `Bearer ${token}`);
  return nativeFetch(input, { ...init, headers });
};


// ---- Finished clips -----------------------------------------------------
//
// Every clip and film the Director printed, from its own records in the
// vault. Watch, read the prompt that rolled, select, download, stitch, delete.

let gallery = null;
let watchGalleryId = null;
let gallerySelection = [];
let gallerySelectMode = false;
let gallerySelectionAnchor = null;
let galleryArchiveBusy = false;
let galleryExpandedPrompts = new Set();

function costLabel(micros) {
  return `$${((micros || 0) / 1e6).toFixed(2)}`;
}

function durationLabel(seconds) {
  const value = Number(seconds || 0);
  if (!value) return "";
  const whole = Math.floor(value);
  const m = Math.floor(whole / 60);
  const sec = whole % 60;
  return m ? `${m}m ${String(sec).padStart(2, "0")}s` : `${value % 1 ? value.toFixed(1) : whole}s`;
}

function galleryFileSize(bytes) {
  const value = Number(bytes || 0);
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(value < 10240 ? 1 : 0)} KB`;
  if (value < 1024 * 1024 * 1024) {
    return `${(value / (1024 * 1024)).toFixed(value < 10 * 1024 * 1024 ? 1 : 0)} MB`;
  }
  return `${(value / (1024 * 1024 * 1024)).toFixed(2)} GB`;
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
      feedback.textContent = "Copy was blocked. The prompt is selected—use your browser's copy shortcut.";
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

function armConfirm(btn, confirmLabel, onConfirm) {
  // Same doctrine as the Director UI: no native dialogs, ever.
  if (btn.dataset.armed === "1") {
    btn.dataset.armed = "";
    btn.classList.remove("armed");
    btn.textContent = btn.dataset.restLabel || btn.textContent;
    onConfirm();
    return;
  }
  btn.dataset.restLabel = btn.textContent;
  btn.dataset.armed = "1";
  btn.classList.add("armed");
  btn.textContent = confirmLabel;
  setTimeout(() => {
    if (btn.dataset.armed === "1") {
      btn.dataset.armed = "";
      btn.classList.remove("armed");
      btn.textContent = btn.dataset.restLabel;
    }
  }, 6000);
}

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
  const resolution = String(item.resolution || "clip").trim().replace(/p$/i, "p");
  setTextNode(frame, "span", item.kind === "film" ? "FILM" : resolution, "gallery-resolution");
  return frame;
}

function selectedGalleryItems() {
  const byId = new Map((gallery?.items || []).map((item) => [item.id, item]));
  return gallerySelection.map((id) => byId.get(id)).filter(Boolean);
}

function disarmGalleryDelete() {
  const remove = document.getElementById("delete-selected");
  if (!remove || remove.dataset.armed !== "1") return;
  remove.dataset.armed = "";
  remove.classList.remove("armed");
  remove.textContent = remove.dataset.restLabel || "Delete selected";
}

function renderGallerySelection() {
  const mode = document.getElementById("gallery-select-mode");
  const selectAll = document.getElementById("gallery-select-all");
  const count = document.getElementById("gallery-selection-count");
  const join = document.getElementById("join-selected");
  const download = document.getElementById("download-selected");
  const remove = document.getElementById("delete-selected");
  const playable = (gallery?.items || []).filter((item) => item.playable);
  const everyItemSelected = playable.length > 0 && playable.every((item) => gallerySelection.includes(item.id));
  if (mode) {
    mode.disabled = galleryArchiveBusy;
    mode.setAttribute("aria-pressed", String(gallerySelectMode));
    mode.textContent = gallerySelectMode ? "Done selecting" : "Select clips";
  }
  if (selectAll) {
    selectAll.disabled = playable.length === 0 || galleryArchiveBusy;
    selectAll.setAttribute("aria-pressed", String(everyItemSelected));
    selectAll.textContent = everyItemSelected ? "Clear all" : "Select all";
  }
  if (count) {
    if (!gallerySelection.length) {
      count.textContent = "No clips selected";
    } else if (everyItemSelected) {
      count.textContent = `${gallerySelection.length} selected · all finished items`;
    } else if (gallerySelection.length > 12) {
      count.textContent = `${gallerySelection.length} selected · gallery order`;
    } else {
      count.textContent = `${gallerySelection.length} selected · order ${gallerySelection.map((_, index) => index + 1).join(" → ")}`;
    }
  }
  if (join) {
    join.disabled = gallerySelection.length < 2 || galleryArchiveBusy;
    join.textContent = gallerySelection.length >= 2 ? `Stitch selected (${gallerySelection.length})` : "Stitch selected";
  }
  if (download) {
    download.disabled = gallerySelection.length === 0 || galleryArchiveBusy;
    download.textContent = galleryArchiveBusy
      ? "Building ZIP…"
      : gallerySelection.length
        ? `Download ZIP (${gallerySelection.length})`
        : "Download ZIP";
  }
  if (remove && remove.dataset.armed !== "1") {
    remove.disabled = gallerySelection.length === 0 || galleryArchiveBusy;
    remove.textContent = gallerySelection.length ? `Delete selected (${gallerySelection.length})` : "Delete selected";
  }
}

function toggleGallerySelection(clipId, options = {}) {
  if (galleryArchiveBusy) return;
  const items = (gallery?.items || []).filter((item) => item.playable);
  const currentIndex = items.findIndex((item) => item.id === clipId);
  if (currentIndex < 0) return;
  disarmGalleryDelete();
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

// One card per production (a session's cut film plus its printed clips).
// The card shows one ACTIVE piece — the film when the session cut one — and
// a row of piece buttons opens the others; every action on the card follows
// the active piece. The flat items stay the unit of selection.
let galleryActivePiece = new Map();

function galleryProductions(items) {
  const byId = new Map(items.map((item) => [item.id, item]));
  const rows = (gallery?.productions || []).length
    ? gallery.productions
    : [...new Set(items.map((item) => item.session))].map((session) => ({
        session,
        film_id: items.find((item) => item.session === session && item.kind === "film")?.id || "",
        clip_ids: items.filter((item) => item.session === session && item.kind !== "film").map((item) => item.id),
      }));
  return rows
    .map((row) => {
      const pieces = [row.film_id, ...(row.clip_ids || [])].map((id) => byId.get(id)).filter(Boolean);
      if (!pieces.length) return null;
      const film = pieces.find((item) => item.kind === "film") || null;
      const clips = pieces.filter((item) => item.kind !== "film");
      return { session: row.session, film, clips, pieces };
    })
    .filter(Boolean);
}

function galleryActivePieceOf(production) {
  const wanted = galleryActivePiece.get(production.session);
  return production.pieces.find((item) => item.id === wanted) || production.film || production.pieces[0];
}

function galleryPieceLabel(item, production) {
  if (item.kind === "film") return "FILM";
  const index = production.clips.indexOf(item);
  return String(index >= 0 ? index + 1 : "•");
}

function toggleGalleryProductionSelection(production) {
  if (galleryArchiveBusy) return;
  disarmGalleryDelete();
  const ids = production.pieces.map((item) => item.id);
  const everySelected = ids.every((id) => gallerySelection.includes(id));
  gallerySelection = everySelected
    ? gallerySelection.filter((id) => !ids.includes(id))
    : [...gallerySelection, ...ids.filter((id) => !gallerySelection.includes(id))];
  gallerySelectionAnchor = gallerySelection.at(-1) || null;
  renderGallery();
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

function itemFacts(item) {
  const parts = [];
  if (item.duration_s) parts.push(durationLabel(item.duration_s));
  if (item.bytes) parts.push(galleryFileSize(item.bytes));
  if (item.kind === "film") parts.push(`${costLabel(item.spent_usd_micros)} spent`);
  else if (item.estimate_usd_micros) parts.push(`${costLabel(item.estimate_usd_micros)} est.`);
  return parts.filter(Boolean);
}

function itemMeta(item) {
  return [item.plate, ...itemFacts(item)].filter(Boolean).join(" · ");
}

function openGalleryViewer(item) {
  const viewer = document.getElementById("gallery-viewer");
  const video = document.getElementById("gallery-viewer-video");
  if (!viewer || !video || !item?.file_url) return;
  watchGalleryId = item.id;
  document.getElementById("gallery-viewer-title").textContent = item.title;
  document.getElementById("gallery-viewer-meta").textContent = itemMeta(item);
  const download = document.getElementById("gallery-viewer-download");
  download.href = item.download_url;
  download.setAttribute("download", "");
  video.poster = item.thumbnail_url;
  video.src = item.file_url;
  if (!viewer.open) viewer.showModal();
  video.play().catch(() => {});
  renderGallery();
}

function madeLabel(iso) {
  const when = new Date(iso || "");
  if (Number.isNaN(when.getTime())) return "";
  return when.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

function fpsLabel(fps) {
  const value = Number(fps || 0);
  if (!value) return "";
  return `${Number.isInteger(value) ? value : value.toFixed(2).replace(/\.?0+$/, "")} fps`;
}

// The facts strip reads the file first (what ffprobe measured) and the booking
// second (what the record says was asked for), so a 720p booking finished at
// 1080p shows both without anyone doing arithmetic.
function galleryFactRows(details, item) {
  const measured = details.measured || {};
  const film = details.kind === "film";
  const rows = [];
  const fact = (label, value, note = "") => {
    if (value) rows.push({ label, value: String(value), note });
  };
  const raster = measured.width && measured.height ? `${measured.width}×${measured.height}` : "";
  if (measured.quality) {
    const booked = film ? details.finish : details.resolution;
    fact(
      "Quality",
      measured.quality,
      [raster, booked && booked !== measured.quality ? `booked ${booked}` : ""].filter(Boolean).join(" · "),
    );
  } else {
    fact("Quality", film ? details.finish || details.resolution : details.resolution, "as booked");
  }
  const runtime = measured.duration_s || details.duration_s;
  const bookedRuntime = details.duration_s ? durationLabel(details.duration_s) : "";
  fact(
    "Runtime",
    runtime ? durationLabel(runtime) : "",
    measured.duration_s && bookedRuntime && Math.abs(measured.duration_s - details.duration_s) >= 0.5
      ? `booked ${bookedRuntime}`
      : "",
  );
  fact("Frame rate", fpsLabel(measured.fps), measured.codec ? measured.codec.toUpperCase() : "");
  fact("Camera", [details.provider, details.model].filter(Boolean).join(" · "), details.mode);
  if (film) {
    fact("Cut", details.clips_in_cut ? `${details.clips_in_cut} clips` : "", details.style ? `${details.style} style` : "");
    fact("Finish", details.finish, details.upscaled ? "upscaled" : "");
  } else if (details.position_in_cut) {
    fact("In the cut", `Clip ${details.position_in_cut} of ${details.clips_in_cut}`);
  }
  fact("Aspect", details.aspect);
  if ("has_audio" in measured) {
    fact("Audio", measured.has_audio ? "Sound" : "Silent", measured.audio_channels ? `${measured.audio_channels} ch` : "");
  }
  fact("Size", galleryFileSize(details.bytes), measured.bit_rate ? `${Math.round(measured.bit_rate / 1000)} kbps` : "");
  if (film) fact("Spent", item.spent_usd_micros ? costLabel(item.spent_usd_micros) : "", "on the ledger");
  else fact("Estimate", details.estimate_usd_micros ? costLabel(details.estimate_usd_micros) : "");
  if (details.review?.verdict) fact("Review", details.review.verdict, (details.review.issues || []).slice(0, 2).join(" · "));
  if (!film) {
    fact("Prompt", details.prompt_version, details.voicing ? `${details.voicing} voicing` : "");
  }
  fact("Made", madeLabel(details.created_at));
  return rows;
}

function renderGalleryDetails(details, item = {}) {
  const body = document.getElementById("gallery-details-body");
  if (!body) return;
  body.replaceChildren();
  const film = details.kind === "film";
  const slate = document.createElement("section");
  slate.className = "gallery-source-slate";

  const header = document.createElement("header");
  setTextNode(header, "span", film ? "Assembled film" : "Printed clip");
  setTextNode(header, "strong", details.session || "");
  if (details.measured?.quality) {
    setTextNode(
      header,
      "em",
      [details.measured.quality, durationLabel(details.measured.duration_s), fpsLabel(details.measured.fps)]
        .filter(Boolean)
        .join(" · "),
    );
  }
  slate.appendChild(header);

  const rows = galleryFactRows(details, item);
  if (rows.length) {
    const facts = document.createElement("dl");
    facts.className = "gallery-facts";
    rows.forEach((row) => {
      const cell = document.createElement("div");
      cell.className = "gallery-fact";
      setTextNode(cell, "dt", row.label);
      const value = setTextNode(cell, "dd", row.value);
      if (row.note) setTextNode(value, "small", row.note);
      facts.appendChild(cell);
    });
    slate.appendChild(facts);
  }

  const columns = document.createElement("div");
  columns.className = "gallery-slate-columns";

  const promptPanel = document.createElement("div");
  promptPanel.className = "gallery-prompt-panel";
  const promptHead = document.createElement("div");
  setTextNode(promptHead, "h3", film ? "Idea this film answers" : "Prompt that rolled");
  const prompt = String((film ? details.idea : details.prompt) || "").trim();
  const copy = document.createElement("button");
  copy.type = "button";
  copy.className = "gallery-copy-prompt";
  copy.setAttribute("aria-label", film ? "Copy idea" : "Copy prompt");
  copy.setAttribute("title", film ? "Copy idea" : "Copy prompt");
  copy.appendChild(galleryCopyIcon());
  copy.disabled = !prompt;
  const promptField = document.createElement("textarea");
  const promptDisplay =
    prompt || (film ? "No idea was recorded for this production." : "No prompt was recorded for this clip.");
  promptField.className = "gallery-idea-prompt";
  if (!prompt) promptField.classList.add("is-empty");
  promptField.readOnly = true;
  promptField.spellcheck = false;
  promptField.setAttribute("aria-label", film ? "Idea" : "Prompt");
  promptField.value = promptDisplay;
  promptField.rows = Math.max(
    4,
    Math.min(14, promptDisplay.split("\n").reduce((rows, line) => rows + Math.max(1, Math.ceil(line.length / 72)), 0)),
  );
  copy.addEventListener("click", () => copyGalleryPrompt(prompt, copy, promptField));
  promptHead.appendChild(copy);
  promptPanel.appendChild(promptHead);
  promptPanel.appendChild(promptField);
  if (details.rationale) {
    setTextNode(promptPanel, "h3", "Why this shot", "gallery-prompt-sub");
    setTextNode(promptPanel, "p", details.rationale, "gallery-prompt-note");
  }
  if (details.narration) {
    setTextNode(promptPanel, "h3", "Narration", "gallery-prompt-sub");
    setTextNode(promptPanel, "p", details.narration, "gallery-prompt-note");
  }
  if (details.idea && !film) {
    setTextNode(promptPanel, "h3", "Idea this production answers", "gallery-prompt-sub");
    setTextNode(promptPanel, "p", details.idea, "gallery-prompt-note is-clamped");
  }
  columns.appendChild(promptPanel);

  const aside = document.createElement("div");
  aside.className = "gallery-slate-aside";
  const referencePanel = document.createElement("div");
  referencePanel.className = "gallery-reference-panel";
  setTextNode(referencePanel, "h3", "Reference stills");
  const references = Array.isArray(details.references) ? details.references : [];
  if (!references.length) {
    setTextNode(referencePanel, "p", "No reference stills were attached.", "gallery-reference-empty");
  } else {
    const list = document.createElement("ul");
    references.forEach((reference) => {
      const row = document.createElement("li");
      setTextNode(row, "strong", reference.name || "reference");
      setTextNode(row, "small", `${reference.role || "reference"} · ${galleryFileSize(reference.bytes)}`);
      list.appendChild(row);
    });
    referencePanel.appendChild(list);
  }
  aside.appendChild(referencePanel);
  const deliverables = Array.isArray(details.deliverables) ? details.deliverables : [];
  if (deliverables.length) {
    const panel = document.createElement("div");
    panel.className = "gallery-reference-panel";
    setTextNode(panel, "h3", film ? "Deliverables beside the film" : "Session deliverables");
    const list = document.createElement("ul");
    list.className = "gallery-deliverables";
    deliverables.forEach((name) => setTextNode(list, "li", name));
    panel.appendChild(list);
    aside.appendChild(panel);
  }
  columns.appendChild(aside);
  slate.appendChild(columns);
  body.appendChild(slate);
}

async function openGalleryDetails(item) {
  const dialog = document.getElementById("gallery-details");
  const body = document.getElementById("gallery-details-body");
  if (!dialog || !body || !item?.details_url) return;
  dialog.dataset.clipId = item.id;
  document.getElementById("gallery-details-title").textContent = item.title;
  document.getElementById("gallery-details-meta").textContent = itemMeta(item);
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
    if (dialog.dataset.clipId === item.id) renderGalleryDetails(details, item);
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

async function removeGalleryItems(items) {
  const note = document.getElementById("gallery-actions-note");
  if (galleryArchiveBusy) {
    if (note) note.textContent = "Wait for the ZIP to finish before deleting its source clips.";
    return;
  }
  const ids = items.map((item) => item.id);
  if (!ids.length) return;
  if (note) note.textContent = `Deleting ${ids.length} item${ids.length === 1 ? "" : "s"}…`;
  let body;
  try {
    body = await responseJson(await fetch("/api/gallery/delete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ items: ids }),
    }));
  } catch (error) {
    if (note) note.textContent = `Delete failed: ${error.message}`;
    renderGallerySelection();
    return;
  }
  if (watchGalleryId && ids.includes(watchGalleryId)) closeGalleryViewer();
  gallerySelection = gallerySelection.filter((id) => !ids.includes(id));
  await loadGallery();
  const removed = body.removed?.length || 0;
  const missing = body.missing?.length || 0;
  if (note) {
    note.textContent = removed
      ? `${removed} removed${missing ? ` · ${missing} already gone` : ""} — the ledger keeps every receipt`
      : `${missing} selected item${missing === 1 ? " was" : "s were"} already gone`;
  }
}

function galleryPause(milliseconds) {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
}

function startGalleryArchiveDownload(status) {
  const anchor = document.createElement("a");
  anchor.href = status.download_url;
  anchor.download = status.filename || "diony-finished-clips.zip";
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
}

async function downloadGalleryArchive() {
  const ids = [...gallerySelection];
  if (!ids.length || galleryArchiveBusy) return;
  const note = document.getElementById("gallery-actions-note");
  disarmGalleryDelete();
  galleryArchiveBusy = true;
  renderGallerySelection();
  if (note) note.textContent = `Preparing one ZIP for ${ids.length} item${ids.length === 1 ? "" : "s"}…`;
  try {
    let status = await responseJson(await fetch("/api/gallery/archive", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ items: ids }),
    }));
    const deadline = Date.now() + 10 * 60 * 1000;
    while (status.state === "queued" || status.state === "building") {
      if (Date.now() >= deadline) throw new Error("The ZIP is still building. Try Download ZIP again in a moment.");
      if (note) note.textContent = `Packing ${status.completed || 0} of ${status.count} into one ZIP…`;
      await galleryPause(650);
      status = await responseJson(await fetch(status.status_url, { cache: "no-store" }));
    }
    if (status.state !== "ready" || !status.download_url) {
      throw new Error(status.failure || "The ZIP could not be built.");
    }
    startGalleryArchiveDownload(status);
    if (note) {
      note.textContent = `${status.count} file${status.count === 1 ? "" : "s"} packed · ${galleryFileSize(status.archive_bytes)} ZIP download started`;
    }
  } catch (error) {
    if (note) note.textContent = `ZIP download failed: ${error.message}`;
  } finally {
    galleryArchiveBusy = false;
    renderGallerySelection();
  }
}

function renderGallery() {
  const grid = document.getElementById("gallery-grid");
  const empty = document.getElementById("gallery-empty");
  const meta = document.getElementById("gallery-meta");
  if (!grid || !gallery) return;
  const items = (gallery.items || []).filter((item) => item.playable);
  if (meta && !items.length) meta.textContent = "No clips yet";
  empty.classList.toggle("hidden", items.length > 0);
  grid.replaceChildren();
  if (!items.length) {
    if (document.getElementById("gallery-viewer")?.open) closeGalleryViewer();
    watchGalleryId = null;
    gallerySelection = [];
    renderGallerySelection();
    return;
  }
  gallerySelection = gallerySelection.filter((id) => items.some((item) => item.id === id));
  galleryExpandedPrompts = new Set(
    [...galleryExpandedPrompts].filter((id) => items.some((item) => item.id === id)),
  );
  const productions = galleryProductions(items);
  galleryActivePiece = new Map(
    [...galleryActivePiece].filter(([session, id]) =>
      productions.some((production) => production.session === session && production.pieces.some((item) => item.id === id)),
    ),
  );
  if (meta && items.length) {
    const films = items.filter((item) => item.kind === "film").length;
    const clips = items.length - films;
    meta.textContent = [
      `${productions.length} production${productions.length === 1 ? "" : "s"}`,
      films ? `${films} film${films === 1 ? "" : "s"}` : "",
      clips ? `${clips} clip${clips === 1 ? "" : "s"}` : "",
    ].filter(Boolean).join(" · ");
  }
  productions.forEach((production, cardIndex) => {
    const item = galleryActivePieceOf(production);
    const pieceIds = production.pieces.map((piece) => piece.id);
    const selectedPieces = pieceIds.filter((id) => gallerySelection.includes(id));
    const selectedIndex = gallerySelection.indexOf(item.id);
    const productionSelected = selectedPieces.length === pieceIds.length;
    const promptExpanded = galleryExpandedPrompts.has(item.id);
    const card = document.createElement("article");
    card.className = `gallery-card${pieceIds.includes(watchGalleryId) ? " on" : ""}${productionSelected ? " selected" : selectedPieces.length ? " part-selected" : ""}${promptExpanded ? " prompt-open" : ""}`;
    card.dataset.gallery = item.id;
    card.dataset.session = production.session;
    card.setAttribute("role", "listitem");
    const watch = document.createElement("button");
    watch.type = "button";
    watch.className = "gallery-watch";
    watch.setAttribute("aria-label", `Watch ${item.title}`);
    watch.appendChild(galleryThumbnail(item));
    watch.addEventListener("click", (event) => {
      if (gallerySelectMode || event.metaKey || event.ctrlKey || event.shiftKey) {
        toggleGallerySelection(item.id, { shift: event.shiftKey });
        return;
      }
      openGalleryViewer(item);
    });
    card.appendChild(watch);
    // the corner box selects the whole production — film and every clip —
    // so one ZIP, stitch, or delete covers the finished piece of work
    const select = document.createElement("button");
    select.type = "button";
    select.className = "gallery-select-cue";
    select.setAttribute("aria-pressed", String(productionSelected));
    select.setAttribute(
      "aria-label",
      productionSelected
        ? `Selected ${selectedIndex + 1}. Remove ${item.title} from the selection`
        : selectedPieces.length
          ? `${selectedPieces.length} of ${pieceIds.length} pieces selected. Select all of ${production.session}`
          : `Select ${production.session} — its film and every clip`,
    );
    select.textContent = "";
    select.addEventListener("click", () => toggleGalleryProductionSelection(production));
    card.appendChild(select);

    // the pieces: the film and each clip, one button each — open a piece
    // (or, while selecting, pick it on its own for a stitch or a ZIP)
    if (production.pieces.length > 1) {
      const pieces = document.createElement("div");
      pieces.className = "gallery-pieces";
      pieces.setAttribute("role", "group");
      pieces.setAttribute("aria-label", `Pieces of ${production.session}`);
      production.pieces.forEach((piece) => {
        const button = document.createElement("button");
        button.type = "button";
        const pieceSelected = gallerySelection.includes(piece.id);
        button.className = `gallery-piece${piece.kind === "film" ? " film" : ""}${pieceSelected ? " selected" : ""}`;
        button.textContent = galleryPieceLabel(piece, production);
        button.setAttribute("aria-pressed", String(piece.id === item.id));
        button.setAttribute(
          "title",
          piece.kind === "film" ? "The cut film" : `Clip ${galleryPieceLabel(piece, production)} · ${piece.plate}`,
        );
        button.setAttribute("aria-label", `${gallerySelectMode ? "Select" : "Open"} ${piece.title}`);
        button.addEventListener("click", (event) => {
          galleryActivePiece.set(production.session, piece.id);
          if (gallerySelectMode || event.metaKey || event.ctrlKey || event.shiftKey) {
            toggleGallerySelection(piece.id, { shift: event.shiftKey });
            return;
          }
          openGalleryViewer(piece);
        });
        pieces.appendChild(button);
      });
      card.appendChild(pieces);
    }

    const copy = document.createElement("div");
    copy.className = "gallery-card-copy";
    setTextNode(
      copy,
      "p",
      item.kind === "film" ? `Film · ${item.session}` : item.plate,
      "gallery-card-source",
    );
    setTextNode(copy, "p", itemFacts(item).join(" · "), "gallery-card-facts");

    const promptId = `gallery-card-prompt-${cardIndex}`;
    const promptToggle = document.createElement("button");
    promptToggle.type = "button";
    promptToggle.className = "gallery-prompt-toggle";
    promptToggle.setAttribute("aria-controls", promptId);
    promptToggle.setAttribute("aria-expanded", String(promptExpanded));
    promptToggle.setAttribute(
      "aria-label",
      `${promptExpanded ? "Collapse" : "Expand"} ${item.kind === "film" ? "idea" : "prompt"} for ${item.title}`,
    );
    setTextNode(promptToggle, "span", item.kind === "film" ? "Idea" : "Prompt", "gallery-prompt-label");
    setTextNode(promptToggle, "span", promptExpanded ? "−" : "+", "gallery-prompt-toggle-symbol");
    copy.appendChild(promptToggle);

    const promptDescription = document.createElement("p");
    promptDescription.id = promptId;
    promptDescription.className = "gallery-prompt-description";
    promptDescription.textContent = String(item.prompt || item.title).trim() || "No prompt recorded.";
    promptDescription.hidden = !promptExpanded;
    copy.appendChild(promptDescription);

    promptToggle.addEventListener("click", () => {
      const expanded = promptToggle.getAttribute("aria-expanded") !== "true";
      if (expanded) galleryExpandedPrompts.add(item.id);
      else galleryExpandedPrompts.delete(item.id);
      card.classList.toggle("prompt-open", expanded);
      promptToggle.setAttribute("aria-expanded", String(expanded));
      promptToggle.setAttribute(
        "aria-label",
        `${expanded ? "Collapse" : "Expand"} ${item.kind === "film" ? "idea" : "prompt"} for ${item.title}`,
      );
      promptToggle.querySelector(".gallery-prompt-toggle-symbol").textContent = expanded ? "−" : "+";
      promptDescription.hidden = !expanded;
    });
    card.appendChild(copy);

    const actions = document.createElement("div");
    actions.className = "gallery-card-actions";
    const watchAction = document.createElement("button");
    watchAction.type = "button";
    watchAction.textContent = "▶";
    watchAction.setAttribute("aria-label", `Watch ${item.title}`);
    watchAction.setAttribute("title", "Watch");
    watchAction.addEventListener("click", () => openGalleryViewer(item));
    actions.appendChild(watchAction);

    const details = document.createElement("button");
    details.type = "button";
    details.className = "gallery-details-action";
    details.setAttribute("aria-label", `View the prompt and references for ${item.title}`);
    details.setAttribute("title", "Details");
    details.appendChild(galleryDetailsIcon());
    details.addEventListener("click", () => openGalleryDetails(item));
    actions.appendChild(details);

    const fullscreen = document.createElement("button");
    fullscreen.type = "button";
    fullscreen.textContent = "⛶";
    fullscreen.setAttribute("aria-label", `Watch ${item.title} full screen`);
    fullscreen.setAttribute("title", "Full screen");
    fullscreen.addEventListener("click", () => {
      openGalleryViewer(item);
      requestGalleryFullscreen();
    });
    actions.appendChild(fullscreen);

    const download = document.createElement("a");
    download.href = item.download_url;
    download.download = "";
    download.textContent = "↓";
    download.setAttribute("aria-label", `Download ${item.title}`);
    download.setAttribute("title", "Download");
    actions.appendChild(download);

    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "gallery-delete";
    remove.textContent = "×";
    remove.setAttribute(
      "aria-label",
      production.pieces.length > 1
        ? `Delete media for ${production.session} — its film and every clip`
        : `Delete media for ${item.title}`,
    );
    remove.setAttribute("title", production.pieces.length > 1 ? "Delete the production's media" : "Delete media");
    remove.addEventListener("click", () =>
      armConfirm(remove, "Sure?", () => removeGalleryItems(production.pieces)),
    );
    actions.appendChild(remove);
    card.appendChild(actions);
    grid.appendChild(card);
  });
  renderGallerySelection();
}

async function loadGallery() {
  const meta = document.getElementById("gallery-meta");
  const note = document.getElementById("gallery-note");
  if (meta && !gallery) meta.textContent = "Loading finished clips…";
  try {
    gallery = await responseJson(await fetch("/api/gallery", { cache: "no-store" }));
    renderGallery();
  } catch (error) {
    if (note) note.textContent = `Gallery could not be loaded: ${error.message}`;
  }
}

let galleryWatchTimer = null;
function watchGallery() {
  // while any session is still generating, the inventory re-reads itself so
  // a clip appears the moment it lands
  const active = (gallery?.items || []).some((item) => item.active);
  if (active && !galleryWatchTimer) galleryWatchTimer = setInterval(loadGallery, 5000);
  if (!active && galleryWatchTimer) {
    clearInterval(galleryWatchTimer);
    galleryWatchTimer = null;
  }
}

function bootGalleryActions() {
  document.getElementById("gallery-select-mode")?.addEventListener("click", () => {
    gallerySelectMode = !gallerySelectMode;
    renderGallery();
  });
  document.getElementById("gallery-select-all")?.addEventListener("click", () => {
    const ids = (gallery?.items || []).filter((item) => item.playable).map((item) => item.id);
    if (!ids.length) return;
    disarmGalleryDelete();
    const everyItemSelected = ids.every((id) => gallerySelection.includes(id));
    gallerySelection = everyItemSelected ? [] : ids;
    gallerySelectionAnchor = gallerySelection.at(-1) || null;
    gallerySelectMode = gallerySelection.length > 0;
    renderGallery();
  });
  document.getElementById("gallery-viewer-close")?.addEventListener("click", closeGalleryViewer);
  document.getElementById("gallery-viewer")?.addEventListener("close", clearGalleryViewer);
  document.getElementById("gallery-viewer-fullscreen")?.addEventListener("click", requestGalleryFullscreen);
  document.getElementById("gallery-details-close")?.addEventListener("click", closeGalleryDetails);
  const note = document.getElementById("gallery-actions-note");
  document.getElementById("download-selected")?.addEventListener("click", downloadGalleryArchive);
  const remove = document.getElementById("delete-selected");
  remove?.addEventListener("click", () => {
    const items = selectedGalleryItems();
    if (!items.length) return;
    armConfirm(remove, `Confirm — delete ${items.length} item(s)`, () => removeGalleryItems(items));
  });
  document.getElementById("join-selected")?.addEventListener("click", async () => {
    const ids = [...gallerySelection];
    if (ids.length < 2) return;
    const join = document.getElementById("join-selected");
    join.disabled = true;
    if (note) note.textContent = `Stitching ${ids.length} clips…`;
    try {
      const body = await responseJson(await fetch("/api/gallery/stitch", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ items: ids }),
      }));
      if (note) note.textContent = `Stitched into ${body.session} (${galleryFileSize(body.bytes)})`;
      gallerySelection = [];
      await loadGallery();
    } catch (error) {
      if (note) note.textContent = `Stitch failed: ${error.message}`;
      renderGallerySelection();
    }
  });
}

// ---- helpers ------------------------------------------------------------

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

// ---- boot ---------------------------------------------------------------

async function load() {
  try {
    const engine = await fetch("/api/engine").then(responseJson);
    const directorLink = document.getElementById("director-link");
    if (directorLink && engine.director_url) directorLink.href = engine.director_url;
    ["director-link", "studio-surface-link"].forEach((id) => {
      const a = document.getElementById(id);
      if (!a) return;
      a.addEventListener("click", () => {
        const url = new URL(a.href);
        url.searchParams.set("fresh", String(Date.now()));
        a.href = url.toString();
      });
    });
  } catch (error) {
    /* the static default href stands */
  }
  await loadGallery();
  watchGallery();
}

bootGalleryActions();
load();
