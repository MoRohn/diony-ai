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

// ---- Studio governance and the join surface -----------------------------

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

// No native dialogs, ever (browsers may suppress them into a silent no-op):
// a question that needs typed input is asked inline, beside the row it is about.
function askInline(host, label, initial = "", { danger = false } = {}) {
  return new Promise((resolve) => {
    host.querySelectorAll(".inline-ask").forEach((old) => old.remove());
    const form = document.createElement("form");
    form.className = "inline-ask" + (danger ? " inline-ask-danger" : "");
    const field = document.createElement("label");
    field.textContent = label;
    const input = document.createElement("input");
    input.type = "text";
    input.value = initial;
    input.autocomplete = "off";
    field.appendChild(input);
    const ok = document.createElement("button");
    ok.type = "submit";
    ok.className = "word";
    ok.textContent = "OK";
    const cancel = document.createElement("button");
    cancel.type = "button";
    cancel.className = "word";
    cancel.textContent = "Cancel";
    form.append(field, ok, cancel);
    const finish = (value) => {
      form.remove();
      resolve(value);
    };
    cancel.addEventListener("click", () => finish(null));
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      finish(input.value);
    });
    host.appendChild(form);
    input.focus();
    input.select();
  });
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
      const name = await askInline(row, "Cost Center name", center.name);
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
      const note = (await askInline(row, "Optional note for the maker", "")) ?? "";
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
        const confirmation = await askInline(
          row,
          `${member.profile_name} loses Studio access now. Their work and cost history stay attributed. Type ${exact}.`,
          "",
          { danger: true },
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

// ---- boot ---------------------------------------------------------------

async function load() {
  try {
    const engine = await fetch("/api/engine").then(responseJson);
    const directorLink = document.getElementById("director-link");
    if (directorLink && engine.director_url) directorLink.href = engine.director_url;
  } catch (error) {
    /* the static default href stands */
  }
  await loadGallery();
  watchGallery();
}

bootGalleryActions();
bootJoinSurface();
load();
