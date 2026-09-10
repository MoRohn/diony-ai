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
  return `${(value / (1024 * 1024)).toFixed(value < 10 * 1024 * 1024 ? 1 : 0)} MB`;
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
  setTextNode(frame, "span", item.kind === "film" ? "FILM" : (item.resolution || "clip").toUpperCase(), "gallery-resolution");
  return frame;
}

function selectedGalleryItems() {
  const byId = new Map((gallery?.items || []).map((item) => [item.id, item]));
  return gallerySelection.map((id) => byId.get(id)).filter(Boolean);
}

function renderGallerySelection() {
  const mode = document.getElementById("gallery-select-mode");
  const count = document.getElementById("gallery-selection-count");
  const join = document.getElementById("join-selected");
  const download = document.getElementById("download-selected");
  const remove = document.getElementById("delete-selected");
  if (mode) {
    mode.setAttribute("aria-pressed", String(gallerySelectMode));
    mode.textContent = gallerySelectMode ? "Done selecting" : "Select clips";
  }
  if (count) {
    count.textContent = gallerySelection.length
      ? `${gallerySelection.length} selected · order ${gallerySelection.map((_, index) => index + 1).join(" → ")}`
      : "No clips selected";
  }
  if (join) {
    join.disabled = gallerySelection.length < 2;
    join.textContent = gallerySelection.length >= 2 ? `Stitch selected (${gallerySelection.length})` : "Stitch selected";
  }
  if (download) {
    download.disabled = gallerySelection.length === 0;
    download.textContent = gallerySelection.length ? `Download selected (${gallerySelection.length})` : "Download selected";
  }
  if (remove && remove.dataset.armed !== "1") {
    remove.disabled = gallerySelection.length === 0;
    remove.textContent = gallerySelection.length ? `Delete selected (${gallerySelection.length})` : "Delete selected";
  }
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

function itemMeta(item) {
  const parts = [item.plate];
  if (item.duration_s) parts.push(durationLabel(item.duration_s));
  if (item.bytes) parts.push(galleryFileSize(item.bytes));
  if (item.kind === "film") parts.push(`${costLabel(item.spent_usd_micros)} spent`);
  else if (item.estimate_usd_micros) parts.push(`${costLabel(item.estimate_usd_micros)} est.`);
  return parts.filter(Boolean).join(" · ");
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

function renderGalleryDetails(details) {
  const body = document.getElementById("gallery-details-body");
  if (!body) return;
  body.replaceChildren();
  const slate = document.createElement("section");
  slate.className = "gallery-source-slate";

  const header = document.createElement("header");
  setTextNode(header, "span", details.kind === "film" ? "Assembled film" : "Printed clip");
  setTextNode(header, "strong", details.session || "");
  slate.appendChild(header);

  const facts = document.createElement("dl");
  facts.className = "join-passport";
  const fact = (label, value) => {
    if (!value) return;
    setTextNode(facts, "dt", label);
    setTextNode(facts, "dd", String(value));
  };
  fact("Camera", [details.provider, details.model].filter(Boolean).join(" · "));
  fact("Mode", details.mode);
  fact("Booked", details.duration_s ? durationLabel(details.duration_s) : "");
  fact("Resolution", details.resolution);
  fact("Estimate", details.estimate_usd_micros ? costLabel(details.estimate_usd_micros) : "");
  fact("Prompt version", details.prompt_version);
  fact("Size", galleryFileSize(details.bytes));
  if (facts.childElementCount) slate.appendChild(facts);

  const promptPanel = document.createElement("div");
  promptPanel.className = "gallery-prompt-panel";
  const promptHead = document.createElement("div");
  setTextNode(promptHead, "h3", details.kind === "film" ? "Idea" : "Prompt that rolled");
  const prompt = String((details.kind === "film" ? details.idea : details.prompt) || "").trim();
  const copy = document.createElement("button");
  copy.type = "button";
  copy.className = "gallery-copy-prompt";
  copy.setAttribute("aria-label", "Copy prompt");
  copy.setAttribute("title", "Copy prompt");
  copy.appendChild(galleryCopyIcon());
  copy.disabled = !prompt;
  const promptField = document.createElement("textarea");
  const promptDisplay = prompt || "No prompt was recorded for this item.";
  promptField.className = "gallery-idea-prompt";
  if (!prompt) promptField.classList.add("is-empty");
  promptField.readOnly = true;
  promptField.spellcheck = false;
  promptField.setAttribute("aria-label", "Prompt");
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

  if (details.rationale) {
    const why = document.createElement("div");
    why.className = "gallery-prompt-panel";
    setTextNode(why, "h3", "Why this shot");
    setTextNode(why, "p", details.rationale);
    slate.appendChild(why);
  }
  if (details.idea && details.kind !== "film") {
    const idea = document.createElement("div");
    idea.className = "gallery-prompt-panel";
    setTextNode(idea, "h3", "Idea");
    setTextNode(idea, "p", details.idea);
    slate.appendChild(idea);
  }

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
      setTextNode(row, "small", ` ${reference.role || "reference"} · ${galleryFileSize(reference.bytes)}`);
      list.appendChild(row);
    });
    referencePanel.appendChild(list);
  }
  slate.appendChild(referencePanel);
  const deliverables = Array.isArray(details.deliverables) ? details.deliverables : [];
  if (deliverables.length) {
    const panel = document.createElement("div");
    panel.className = "gallery-reference-panel";
    setTextNode(panel, "h3", "Deliverables beside the film");
    setTextNode(panel, "p", deliverables.join(" · "));
    slate.appendChild(panel);
  }
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

async function removeGalleryItems(items) {
  const note = document.getElementById("gallery-actions-note");
  let removed = 0;
  for (const item of items) {
    try {
      await responseJson(await fetch(item.remove_url, { method: "DELETE" }));
      removed += 1;
      if (watchGalleryId === item.id) closeGalleryViewer();
    } catch (error) {
      if (note) note.textContent = `Delete failed: ${error.message}`;
    }
  }
  gallerySelection = gallerySelection.filter((id) => !items.some((item) => item.id === id));
  await loadGallery();
  if (note && removed) note.textContent = `${removed} removed — the ledger keeps every receipt`;
}

function renderGallery() {
  const grid = document.getElementById("gallery-grid");
  const empty = document.getElementById("gallery-empty");
  const meta = document.getElementById("gallery-meta");
  if (!grid || !gallery) return;
  const items = (gallery.items || []).filter((item) => item.playable);
  if (meta) {
    const films = items.filter((item) => item.kind === "film").length;
    const clips = items.length - films;
    meta.textContent = items.length
      ? [films ? `${films} film${films === 1 ? "" : "s"}` : "", clips ? `${clips} clip${clips === 1 ? "" : "s"}` : ""].filter(Boolean).join(" · ")
      : "No clips yet";
  }
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
    setTextNode(copy, "em", item.kind === "film" ? `Film · ${item.session}` : item.plate);
    setTextNode(copy, "span", item.kind === "film" ? "Idea" : "Prompt", "gallery-prompt-label");
    setTextNode(
      copy,
      "strong",
      String(item.prompt || item.title).trim() || "No prompt recorded.",
      "gallery-prompt-description",
    );
    setTextNode(copy, "small", itemMeta(item));
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
    select.setAttribute("aria-label", selectedIndex >= 0 ? `Remove ${item.title} from the selection` : `Select ${item.title}`);
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
    details.setAttribute("aria-label", `View the prompt and references for ${item.title}`);
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

    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "gallery-delete";
    remove.textContent = "Delete";
    remove.addEventListener("click", () => armConfirm(remove, "Confirm delete", () => removeGalleryItems([item])));
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
    if (!gallerySelectMode) gallerySelection = [];
    renderGallery();
  });
  document.getElementById("gallery-viewer-close")?.addEventListener("click", closeGalleryViewer);
  document.getElementById("gallery-viewer")?.addEventListener("close", clearGalleryViewer);
  document.getElementById("gallery-viewer-fullscreen")?.addEventListener("click", requestGalleryFullscreen);
  document.getElementById("gallery-details-close")?.addEventListener("click", closeGalleryDetails);
  const note = document.getElementById("gallery-actions-note");
  document.getElementById("download-selected")?.addEventListener("click", () => {
    let delay = 0;
    selectedGalleryItems().forEach((item) => {
      setTimeout(() => {
        const anchor = document.createElement("a");
        anchor.href = item.download_url;
        anchor.download = "";
        document.body.appendChild(anchor);
        anchor.click();
        anchor.remove();
      }, delay);
      delay += 400; // stagger so the browser accepts every download
    });
    if (note) note.textContent = `${gallerySelection.length} download(s) started`;
  });
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
