/* Diony Director M1 — Guided chat over the director API. */
(() => {
  const $ = (id) => document.getElementById(id);
  const thread = $("thread");
  let sessionId = null;
  const usd = (micros) => `$${(micros / 1e6).toFixed(2)}`;
  const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

  // Reference stills: uploaded or dropped images ride the initial shot.
  // One-shot opens on the first; a drafted script uses them as character/
  // scene consistency references (never a forced first frame).
  const refUploads = []; // {id, name, url}

  function renderRefChips() {
    const host = $("refchips");
    host.replaceChildren();
    refUploads.forEach((u, i) => {
      const chip = document.createElement("span");
      chip.className = "refchip" + (i === 0 ? " opening" : "");
      const img = document.createElement("img");
      img.src = u.url;
      img.alt = u.name;
      chip.appendChild(img);
      const label = document.createElement("span");
      label.className = "refname";
      label.textContent = (i === 0 ? "LEAD · " : "") + u.name;
      chip.appendChild(label);
      const x = document.createElement("button");
      x.type = "button";
      x.className = "refx";
      x.textContent = "×";
      x.setAttribute("aria-label", `remove ${u.name}`);
      x.addEventListener("click", async () => {
        try {
          await api(`/api/director/sessions/${sessionId}/uploads/${u.id}`, { method: "DELETE" });
        } catch (err) { /* already gone server-side is fine — the chip still leaves */ }
        URL.revokeObjectURL(u.url);
        refUploads.splice(refUploads.indexOf(u), 1);
        renderRefChips();
      });
      chip.appendChild(x);
      host.appendChild(chip);
    });
    $("refhint").style.display = refUploads.length ? "none" : "";
  }

  function addImageFiles(fileList) {
    [...fileList]
      .filter((f) => /^image\/(png|jpe?g|webp)$/.test(f.type))
      .forEach((file) => {
        const reader = new FileReader();
        reader.onload = async () => {
          const b64 = String(reader.result).split(",", 2)[1] || "";
          try {
            const body = await api(`/api/director/sessions/${sessionId}/uploads`, {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({ name: file.name, data_base64: b64 }),
            });
            refUploads.push({ id: body.upload.id, name: file.name, url: URL.createObjectURL(file) });
            renderRefChips();
          } catch (err) {
            addMsg("director", `I couldn't take ${file.name}: ${err.message || err}`);
          }
        };
        reader.readAsDataURL(file);
      });
  }

  function clearRefUploads() {
    refUploads.forEach((u) => URL.revokeObjectURL(u.url));
    refUploads.length = 0;
    renderRefChips();
  }

  function withRefSuffix(idea) {
    return refUploads.length ? `${idea}  [+${plural(refUploads.length, "image")}]` : idea;
  }

  function durationSeconds() {
    const mins = Math.max(0, Math.min(180, Math.floor(Number($("dur-min").value) || 0)));
    const secs = Math.max(0, Math.floor(Number($("dur-sec").value) || 0));
    return mins * 60 + secs;
  }

  function setDuration(total) {
    const clamped = Math.max(1, Math.min(10800, Math.round(total)));
    $("dur-min").value = Math.floor(clamped / 60);
    $("dur-sec").value = clamped % 60;
    updateDurationHint();
  }

  function normalizeDuration() {
    // 90 typed into the seconds field becomes 1m 30s, not an error.
    const secs = Math.floor(Number($("dur-sec").value) || 0);
    const mins = Math.floor(Number($("dur-min").value) || 0);
    if (secs > 59 || secs < 0 || mins < 0 || mins > 180) {
      setDuration(Math.min(180, Math.max(0, mins)) * 60 + Math.max(0, secs));
      return;
    }
    updateDurationHint();
  }

  function updateDurationHint() {
    const hint = $("durhint");
    const total = durationSeconds();
    if (total > 15) {
      hint.textContent = `${Math.floor(total / 60)}m ${total % 60}s — script runtime (a single shot caps at 15s)`;
      hint.className = "durhint warn";
    } else {
      hint.textContent = "one shot ≤ 15s · scripts run longer";
      hint.className = "durhint";
    }
  }

  async function api(path, options) {
    const res = await fetch(path, options);
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(body.detail || `${res.status}`);
    return body;
  }

  function addMsg(who, text) {
    const li = document.createElement("li");
    li.className = `msg ${who}`;
    li.innerHTML = `<div class="who">${who === "director" ? "Director" : "You"}</div><div class="body"></div>`;
    li.querySelector(".body").textContent = text;
    thread.appendChild(li);
    li.scrollIntoView({ block: "end" });
    return li;
  }

  function estimateTable(est) {
    const rows = est.lines
      .map((l) => `<tr><td>${l.label}</td><td>${usd(l.usd_micros)}</td></tr>`)
      .join("");
    return `<details class="lines"><summary>estimate breakdown</summary><table>${rows}</table></details>`;
  }

  function armConfirm(btn, confirmLabel, onConfirm) {
    // In-page two-step confirm: native dialogs can be suppressed by the
    // browser, which reads as a dead button. First click arms; second fires.
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

  function attachPrompt(card, op) {
    if (op.rationale) {
      const line = document.createElement("div");
      line.className = "whatrolls";
      line.textContent = op.rationale;
      card.appendChild(line);
    }
    if (!op.prompt) return;
    const details = document.createElement("details");
    details.className = "prompt";
    const summary = document.createElement("summary");
    summary.textContent = `prompt ${op.prompt_version || "v1"} — what the camera will be told`;
    const text = document.createElement("div");
    text.className = "ptext";
    text.textContent = op.prompt;
    details.appendChild(summary);
    details.appendChild(text);
    card.appendChild(details);
  }

  function addOpCard(host, op) {
    const card = document.createElement("div");
    card.className = "opcard";
    card.innerHTML = `
      <div class="row">
        <span class="chip">${op.provider} · ${op.model}</span>
        <span class="chip">${op.duration_s}s · ${op.resolution}</span>
        <span class="est">est ${usd(op.estimate.total_usd_micros)}</span>
        <button type="button">Run — ${usd(op.estimate.total_usd_micros)}</button>
        <span class="state">proposed</span>
      </div>
      ${estimateTable(op.estimate)}`;
    attachPrompt(card, op);
    host.appendChild(card);
    const btn = card.querySelector("button");
    const state = card.querySelector(".state");
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      state.textContent = "submitting…";
      try {
        await api(`/api/director/ops/${op.id}/run`, { method: "POST" });
        state.textContent = "generating…";
        poll(op.id, card, state);
      } catch (err) {
        state.textContent = String(err.message || err);
        state.className = "state failed";
        btn.disabled = false;
      }
      refreshMeter();
    });
  }

  function poll(opId, card, state) {
    const tick = async () => {
      try {
        const body = await api(`/api/director/ops/${opId}`);
        const status = body.op.status;
        if (status === "completed") {
          state.textContent = `printed · ${body.clip ? body.clip.bytes + " bytes" : ""}`;
          state.className = "state done";
          const video = document.createElement("video");
          video.controls = true;
          video.src = `/api/director/clips/${opId}`;
          card.appendChild(video);
          refreshMeter();
          return;
        }
        if (status === "failed") {
          state.textContent = `failed — ${body.job ? body.job.error_message : ""}`;
          state.className = "state failed";
          attachRetry(card, opId, state);
          refreshMeter();
          return;
        }
        setTimeout(tick, 2500);
      } catch (err) {
        state.textContent = String(err.message || err);
        state.className = "state failed";
      }
    };
    setTimeout(tick, 1500);
  }

  async function refreshMeter() {
    try {
      const ledger = await api("/api/director/ledger");
      $("spent-total").textContent = usd(ledger.totals.cost_usd_micros);
      $("spent-today").textContent = usd(ledger.today_usd_micros || 0);
    } catch (err) {
      /* meter stays as-is; never invent numbers */
    }
  }

  async function boot() {
    try {
      const status = await api("/api/director/status");
      $("engine-chip").textContent = `engine ${status.engine}`;
      $("engine-chip").className = `chip ${status.engine === "cloud" ? "on" : "off"}`;
      $("grok-chip").textContent = `grok ${status.providers.grok ? "ready" : "no key"}`;
      $("grok-chip").className = `chip ${status.providers.grok ? "on" : "off"}`;
      $("flow-chip").textContent = `flow ${status.providers["google-flow"] ? "ready" : "no token"}`;
      $("flow-chip").className = `chip ${status.providers["google-flow"] ? "on" : "off"}`;
      if (status.brain) {
        $("brain-chip").textContent = status.brain.configured
          ? `brain ${status.brain.model}`
          : "brain off (template)";
        $("brain-chip").className = `chip ${status.brain.configured ? "on" : "off"}`;
      }
      if (status.studio_url) document.getElementById("studio-link").href = status.studio_url;
      if (status.producer_url) document.getElementById("producer-link").href = status.producer_url;
      if (status.engine_choice === "grok" || status.engine_choice === "google-flow") {
        $("provider").value = status.engine_choice;
      }
      // A rehydrated session with unfinished business gets the room back —
      // failed clips carry a Retry, and the printed ones are never re-spent.
      let resume = null;
      try {
        const r = await api("/api/director/resumable");
        resume = (r.sessions || [])[0] || null;
      } catch (err) {
        /* no resume — fresh room */
      }
      if (resume) {
        sessionId = resume.session_id;
        refreshMeter();
        const bits = [`${resume.completed} printed`];
        if (resume.failed) bits.push(`${resume.failed} failed — each has a Retry on the SHOTS tab`);
        if (resume.in_flight) bits.push(`${resume.in_flight} still rolling`);
        addMsg(
          "director",
          `Resuming session ${resume.session_id}: ${bits.join(", ")}. ` +
            "Retries re-roll at their locked price; assemble from the CUT tab when the set is complete. " +
            "＋ NEW SESSION starts fresh — the ledger keeps everything."
        );
        setBoardOpen(true);
        const shotsBtn = document.querySelector('.btabs button[data-tab="shots"]');
        if (shotsBtn) shotsBtn.click();
      } else {
        const created = await api("/api/director/sessions", { method: "POST" });
        sessionId = created.session_id;
        refreshMeter();
        addMsg(
          "director",
          "I'm the Director. Give me one shot to start — I'll compile the prompt, lock the price, and roll only when you approve."
        );
      }
    } catch (err) {
      addMsg("director", `The service isn't ready: ${err.message || err}`);
    }
  }

  async function send() {
    const idea = $("idea").value.trim();
    if (!idea || !sessionId) return;
    addMsg("user", withRefSuffix(idea));
    $("idea").value = "";
    try {
      const body = await api(`/api/director/sessions/${sessionId}/message`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          text: idea,
          provider: $("provider").value,
          duration_s: Math.min(15, durationSeconds() || 8),
          resolution: $("resolution").value,
        }),
      });
      const li = addMsg("director", body.reply);
      addOpCard(li, body.op);
    } catch (err) {
      addMsg("director", `I couldn't price that: ${err.message || err}`);
    }
  }

  function renderScript(host, script) {
    const box = document.createElement("div");
    box.className = "script";
    box.innerHTML = script.lines
      .map((l) => {
        const text = l.text.replace(/&/g, "&amp;").replace(/</g, "&lt;");
        if (l.kind === "heading") return `<span class="hd">${text}</span>`;
        if (l.kind === "cue") return `<span class="cue">          ${text}</span>`;
        if (l.kind === "dialogue" || l.kind === "vo") return `        ${text}`;
        if (l.kind === "parenthetical") return `          (${text})`;
        return text;
      })
      .join("\n");
    host.appendChild(box);
  }

  function attachVerdict(card, state, review, opId) {
    // The verdict lives on the card with its evidence; warn/fail carry the
    // Accept action so shipping a flagged take is a decision, never a default.
    if (!review || card.querySelector(".verdict")) return;
    const chip = document.createElement("span");
    chip.className = `verdict ${review.verdict}`;
    chip.textContent = ` review: ${review.verdict}`;
    state.appendChild(chip);
    if (review.verdict === "pass") return;
    const why = document.createElement("div");
    why.className = "verdict-issues";
    why.textContent = (review.issues || []).join(" · ") || "no evidence recorded";
    card.appendChild(why);
    const accept = document.createElement("button");
    accept.type = "button";
    accept.className = "acceptbtn";
    accept.textContent = review.verdict === "fail" ? "Accept anyway — cut it in" : "Accept take";
    accept.addEventListener("click", async () => {
      accept.disabled = true;
      try {
        const res = await api(`/api/director/ops/${opId}/accept`, { method: "POST" });
        accept.textContent = "accepted ✓";
        addMsg("director", res.reply);
      } catch (err) {
        accept.textContent = String(err.message || err);
        accept.disabled = false;
      }
    });
    card.appendChild(accept);
  }

  // Failed ops are never dead ends: retry re-proposes at the locked price and
  // rejoins the submit wave — completed clips are never re-spent.
  function attachRetry(card, opId, state) {
    if (card.querySelector(".retrybtn")) return;
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "retrybtn";
    btn.textContent = "↻ Retry";
    btn.title = "Re-roll this clip at its locked price — completed clips are never re-spent";
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      try {
        await api(`/api/director/ops/${opId}/retry`, { method: "POST" });
        btn.remove();
        state.textContent = "retrying…";
        state.className = "state";
        watchRetried(opId, card, state);
      } catch (err) {
        state.textContent = String(err.message || err);
        btn.disabled = false;
      }
    });
    (card.querySelector(".row") || card).appendChild(btn);
  }

  async function watchRetried(opId, card, state, deadlineTicks = 120) {
    for (let i = 0; i < deadlineTicks; i++) {
      await new Promise((r) => setTimeout(r, 2500));
      let body;
      try {
        body = await api(`/api/director/ops/${opId}`);
      } catch (err) {
        continue;
      }
      const st = body.op.status;
      if (st === "completed") {
        state.textContent = "printed";
        state.className = "state done";
        if (!card.querySelector("video")) {
          const video = document.createElement("video");
          video.controls = true;
          video.src = `/api/director/clips/${opId}`;
          card.appendChild(video);
        }
        attachVerdict(card, state, body.review, opId);
        refreshMeter();
        return;
      }
      if (st === "failed") {
        state.textContent = `failed — ${body.job ? body.job.error_message || "" : ""}`;
        state.className = "state failed";
        attachRetry(card, opId, state);
        refreshMeter();
        return;
      }
      state.textContent = st === "proposed" ? "waiting for the wave…" : `${st}…`;
    }
    state.textContent = "still not rolling — check the connection and Retry";
    state.className = "state failed";
    attachRetry(card, opId, state);
  }

  function pollProductionOp(op, card, state) {
    const tick = async () => {
      try {
        const body = await api(`/api/director/ops/${op.id}`);
        const status = body.op.status;
        if (status === "completed" || status === "failed") {
          refreshMeter();
          if (status === "completed") {
            state.className = "state done";
            const video = document.createElement("video");
            video.controls = true;
            video.src = `/api/director/clips/${op.id}`;
            card.appendChild(video);
          } else {
            // set the text BEFORE the verdict chip lands — textContent wipes children
            state.textContent = `failed — ${body.job ? body.job.error_message || "" : ""}`;
            state.className = "state failed";
            attachRetry(card, op.id, state);
          }
          attachVerdict(card, state, body.review, op.id);
          if (body.narration) {
            const li = addMsg("director", body.narration);
            if (body.next_op) addOpCardWith(li, body.next_op, pollProductionOp);
          }
          return;
        }
        setTimeout(tick, 2500);
      } catch (err) {
        state.textContent = String(err.message || err);
        state.className = "state failed";
      }
    };
    setTimeout(tick, 1500);
  }

  function addOpCardWith(host, op, poller) {
    const card = document.createElement("div");
    card.className = "opcard";
    card.innerHTML = `
      <div class="row">
        <span class="chip">${op.clip_id || op.op} · ${op.provider} · ${op.model}</span>
        <span class="chip">${op.duration_s}s</span>
        <span class="est">est ${usd(op.estimate.total_usd_micros)}</span>
        <button type="button">Run — ${usd(op.estimate.total_usd_micros)}</button>
        <span class="state">${op.status}</span>
      </div>
      ${estimateTable(op.estimate)}`;
    attachPrompt(card, op);
    host.appendChild(card);
    const btn = card.querySelector("button");
    const state = card.querySelector(".state");
    if (op.status !== "proposed") btn.disabled = true;
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      state.textContent = "submitting…";
      try {
        await api(`/api/director/ops/${op.id}/run`, { method: "POST" });
        state.textContent = "generating…";
        poller(op, card, state);
      } catch (err) {
        state.textContent = String(err.message || err);
        state.className = "state failed";
        btn.disabled = false;
      }
      refreshMeter();
    });
    return card;
  }

  async function assembleNow() {
    try {
      const film = await api(`/api/director/sessions/${sessionId}/assemble`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      const done = addMsg(
        "director",
        `Assembled ${plural(film.clip_count, "clip")} into a ${film.duration_s.toFixed(1)}s cut, ${film.loudness_target} loudness, ${film.look} look.`
      );
      const video = document.createElement("video");
      video.controls = true;
      video.src = `/api/director/films/${sessionId}`;
      done.appendChild(video);
      if (film.deliverables && film.deliverables.length) {
        let line = `Delivered with ${plural(film.cue_count || 0, "subtitle cue")}: ${film.deliverables.join(", ")} — on the board's CUT tab.`;
        if (film.final_check && film.final_check.verdict !== "pass") {
          line += ` Final master check: ${film.final_check.verdict} (${(film.final_check.issues || []).join("; ")}).`;
        }
        addMsg("director", line);
      }
      const receipt = await api(`/api/director/sessions/${sessionId}/receipt`);
      addMsg(
        "director",
        `Receipt: estimated ${usd(receipt.totals.estimated_usd_micros)}, actual ${usd(receipt.totals.actual_usd_micros)} across ${plural(receipt.lines.length, "op")} — accepted and discarded takes both on the ledger.`
      );
      // The film stays on screen — the Gallery is a link, never a 3s countdown
      // that navigates away from the picture we just handed over.
      const producerHref = document.getElementById("producer-link").href.replace(/\/$/, "");
      const wrap = addMsg("director", "That's a wrap. The Gallery has the film too.");
      const link = document.createElement("a");
      link.className = "linkbtn";
      link.href = `${producerHref}/?bay=gallery`;
      link.textContent = "OPEN PRODUCER ▸";
      link.title = "The Gallery in Producer — watch, download, or stitch the film";
      wrap.querySelector(".body").appendChild(link);
      refreshBoard();
    } catch (err) {
      addMsg("director", `Assembly isn't ready: ${err.message || err}`);
    }
  }

  function setupBatch(host, ops, bibleOps) {
    const cards = new Map(); // op_id -> {card, state, checkbox, est}
    const biblePending = (bibleOps || []).filter((b) => b.status === "proposed");
    const bibleCost = biblePending.reduce(
      (total, b) => total + (b.estimate ? b.estimate.total_usd_micros : 0), 0);
    const bibleSuffix = biblePending.length
      ? ` + ${biblePending.length} stills ${usd(bibleCost)}`
      : "";

    const bar = document.createElement("div");
    bar.className = "batchbar";
    bar.innerHTML = `
      <label class="selall"><input type="checkbox" id="selall-box" checked> Select all</label>
      <button type="button" class="batch primary" id="run-all"></button>
      <button type="button" class="batch" id="run-sel"></button>
      <button type="button" class="batch ghost" id="assemble-only">Assemble completed</button>
      <span class="progress" id="batch-progress"></span>`;
    host.appendChild(bar);

    ops.forEach((op) => {
      const card = addOpCardWith(host, op, pollProductionOp);
      const row = card.querySelector(".row");
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.checked = true;
      checkbox.className = "clipsel";
      checkbox.setAttribute("aria-label", `select ${op.clip_id || op.id}`);
      row.insertBefore(checkbox, row.firstChild);
      checkbox.addEventListener("change", refreshBatchLabels);
      cards.set(op.id, {
        card, checkbox,
        state: card.querySelector(".state"),
        btn: card.querySelector("button"),
        est: op.estimate ? op.estimate.total_usd_micros : 0,
      });
    });

    const allBtn = bar.querySelector("#run-all");
    const selBtn = bar.querySelector("#run-sel");
    const asmBtn = bar.querySelector("#assemble-only");
    const selAll = bar.querySelector("#selall-box");
    const progress = bar.querySelector("#batch-progress");

    function selectedIds() {
      return [...cards.entries()].filter(([, c]) => c.checkbox.checked).map(([id]) => id);
    }
    function sumFor(ids) {
      return ids.reduce((total, id) => total + (cards.get(id)?.est || 0), 0);
    }
    function refreshBatchLabels() {
      const sel = selectedIds();
      const allIds = [...cards.keys()];
      allBtn.textContent =
        `Run & Assemble All (${allIds.length}${bibleSuffix}) — ${usd(sumFor(allIds) + bibleCost)}`;
      selBtn.textContent =
        `Run & Assemble Selected (${sel.length}${bibleSuffix}) — ${usd(sumFor(sel) + (sel.length ? bibleCost : 0))}`;
      selBtn.disabled = sel.length === 0;
      selAll.checked = sel.length === allIds.length;
    }
    selAll.addEventListener("change", () => {
      cards.forEach((c) => { c.checkbox.checked = selAll.checked; });
      refreshBatchLabels();
    });
    refreshBatchLabels();

    async function runAndAssemble(ids) {
      allBtn.disabled = true; selBtn.disabled = true;
      progress.textContent = "submitting…";
      let batch;
      try {
        batch = await api(`/api/director/sessions/${sessionId}/run_batch`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ op_ids: ids }),
        });
      } catch (err) {
        progress.textContent = `batch failed: ${err.message || err}`;
        allBtn.disabled = false; refreshBatchLabels();
        return;
      }
      ids.forEach((id) => {
        const c = cards.get(id);
        if (c) { c.state.textContent = "queued…"; c.btn.disabled = true; }
      });
      batch.held.forEach((h) => {
        const c = cards.get(h.op_id);
        if (c) { c.state.textContent = h.reason; c.state.className = "state failed"; }
      });
      if (batch.held.length) {
        addMsg("director", `${plural(batch.held.length, "clip")} held: ${batch.held[0].reason}`);
      }
      if (!(batch.queued || 0) && !batch.submitting && !batch.submitted.length) {
        progress.textContent = "nothing submitted";
        allBtn.disabled = false; refreshBatchLabels();
        return;
      }

      const total = batch.submitted.length;
      const tick = async () => {
        let counts;
        try {
          counts = await api(`/api/director/sessions/${sessionId}/advance`, { method: "POST" });
        } catch (err) {
          progress.textContent = `lost the room: ${err.message || err}`;
          return;
        }
        const queued = counts.queued || 0;
        const stalled = (counts.stalled || []).length;
        // Hold reasons are drained exactly once — a budget hold or a held
        // scene must reach the room, not vanish into an unread field.
        (counts.held || []).forEach((h) => addMsg("director", `Held: ${h.reason}`));
        progress.textContent =
          `${counts.completed} done · ${counts.failed} failed · ${counts.pending} generating` +
          (queued ? ` · ${queued} queued (chained)` : "") +
          ((counts.held || []).length ? ` · ${counts.held.length} held` : "") +
          (stalled ? ` · ${stalled} stalled: ${counts.stalled[0].reason}` : "");
        refreshMeter();
        if (counts.pending > 0 || queued > 0 || counts.submitting) {
          setTimeout(tick, 3000);
          return;
        }
        // settle each card with its final state, then cut the film
        for (const id of ids) {
          try {
            const body = await api(`/api/director/ops/${id}`);
            const c = cards.get(id);
            if (!c) continue;
            if (body.op.status === "completed") {
              c.state.textContent = "printed";
              c.state.className = "state done";
              if (!c.card.querySelector("video")) {
                const video = document.createElement("video");
                video.controls = true;
                video.src = `/api/director/clips/${id}`;
                c.card.appendChild(video);
              }
            } else if (body.op.status === "failed") {
              c.state.textContent = `failed — ${body.job ? body.job.error_message : ""}`;
              c.state.className = "state failed";
              attachRetry(c.card, id, c.state);
              if (body.narration) addMsg("director", body.narration);
            }
            attachVerdict(c.card, c.state, body.review, id);
          } catch (err) { /* card stays coarse; the film still cuts */ }
        }
        progress.textContent += " — assembling…";
        await assembleNow();
        progress.textContent = progress.textContent.replace(" — assembling…", " — film delivered");
        allBtn.disabled = false; refreshBatchLabels();
      };
      setTimeout(tick, 2500);
    }

    allBtn.addEventListener("click", () => {
      const ids = [...cards.keys()];
      armConfirm(
        allBtn,
        `Confirm — roll ${plural(ids.length, "clip")}${bibleSuffix}, ${usd(sumFor(ids) + bibleCost)} total`,
        () => runAndAssemble(ids)
      );
    });
    selBtn.addEventListener("click", () => {
      const ids = selectedIds();
      if (!ids.length) return;
      armConfirm(
        selBtn,
        `Confirm — roll ${plural(ids.length, "clip")}${bibleSuffix}, ${usd(sumFor(ids) + bibleCost)} total`,
        () => runAndAssemble(ids)
      );
    });
    asmBtn.addEventListener("click", assembleNow);
  }

  function renderDraft(li, state) {
    refreshBoard();
    li.querySelector(".body").textContent = state.reply;
    if (state.warnings && state.warnings.length) {
      const warn = document.createElement("div");
      warn.className = "draft-warn";
      warn.textContent = state.warnings.join(" · ");
      li.appendChild(warn);
    }
    renderScript(li, state.script);
    attachLockButton(li);
  }

  async function produce() {
    const idea = $("idea").value.trim();
    if (!idea || !sessionId) return;
    if (durationSeconds() < 30) setDuration(60); // a scripted piece needs a runtime; the change is visible, never silent
    addMsg("user", withRefSuffix(idea));
    $("idea").value = "";
    $("produce").disabled = true;
    try {
      const maxUsd = Math.max(0, Number($("budget").value) || 0);
      const body = await api(`/api/director/sessions/${sessionId}/produce`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          idea: idea,
          runtime_s: durationSeconds() || 60,
          max_usd: maxUsd,
        }),
      });
      if (maxUsd > 0) addMsg("director", `Budget ceiling set: $${maxUsd.toFixed(2)} for this session — anything past it holds.`);
      const li = addMsg("director", body.reply);
      if (body.status === "drafting") {
        const started = Date.now();
        const status = document.createElement("div");
        status.className = "draft-status";
        li.appendChild(status);
        const tick = async () => {
          const elapsed = Math.round((Date.now() - started) / 1000);
          status.textContent = `writing… ${elapsed}s`;
          let state;
          try {
            state = await api(`/api/director/sessions/${sessionId}/draft`);
          } catch (err) {
            setTimeout(tick, 3000);
            return;
          }
          if (state.status === "ready") {
            status.remove();
            renderDraft(li, state);
            $("produce").disabled = false;
            return;
          }
          if (state.status === "failed") {
            status.textContent = `draft failed: ${state.error} — hit Draft script to let the brain try again`;
            status.classList.add("failed");
            $("produce").disabled = false;
            return;
          }
          setTimeout(tick, 3000);
        };
        setTimeout(tick, 1500);
        return;
      }
      renderDraft(li, body);
      $("produce").disabled = false;
    } catch (err) {
      addMsg("director", `I couldn't draft that: ${err.message || err}`);
      $("produce").disabled = false;
    }
  }

  function attachLockButton(li) {
    {
      const lockBtn = document.createElement("button");
      lockBtn.textContent = "Lock script & price the clips";
      lockBtn.style.marginTop = ".6rem";
      lockBtn.className = "lock";
      li.appendChild(lockBtn);
      lockBtn.addEventListener("click", async () => {
        lockBtn.disabled = true;
        try {
          const locked = await api(`/api/director/sessions/${sessionId}/lock_script`, { method: "POST" });
          const reply = addMsg("director", locked.reply);
          setupBatch(reply, locked.ops, locked.bible_ops || []);
          refreshBoard();
        } catch (err) {
          addMsg("director", `Couldn't lock: ${err.message || err}`);
          lockBtn.disabled = false;
        }
      });
    }
  }

  // --- Production board: script / bibles / shots / cut ----------------------
  let boardTab = "script";
  let boardTimer = null;

  function esc(text) {
    const box = document.createElement("div");
    box.textContent = text == null ? "" : String(text);
    return box.innerHTML;
  }

  async function refreshBoard() {
    if ($("board").hidden || !sessionId) return;
    let b;
    try {
      b = await api(`/api/director/sessions/${sessionId}/board`);
    } catch (err) {
      $("bpane").textContent = `board unavailable: ${err.message || err}`;
      return;
    }
    if (boardTab === "script") renderScriptTab($("bpane"), b);
    else if (boardTab === "bibles") renderBiblesTab($("bpane"), b);
    else if (boardTab === "shots") renderShotsTab($("bpane"), b);
    else renderCutTab($("bpane"), b);
  }

  function renderScriptTab(pane, b) {
    pane.replaceChildren();
    if (!b.script) {
      pane.textContent = "No draft yet — Draft script to begin.";
      return;
    }
    if (b.beats && b.beats.length) {
      const beats = document.createElement("div");
      beats.className = "bbeats";
      beats.innerHTML =
        "<div class='bhead'>BEATS</div>" +
        b.beats.map((x) => `<div class="bbeat">${esc(x.beat)} <span class="bt">${esc(x.story_time || "")}</span></div>`).join("");
      pane.appendChild(beats);
    }
    const note = document.createElement("div");
    note.className = "bnote";
    note.textContent = b.locked
      ? "Script is locked — the money pass runs from it."
      : "Click a line to rewrite it. Headings hold the structure.";
    pane.appendChild(note);
    const list = document.createElement("div");
    list.className = "blines";
    const editable = new Set(["action", "dialogue", "vo", "parenthetical"]);
    b.script.lines.forEach((l) => {
      const row = document.createElement("div");
      row.className = `bline ${l.kind}`;
      row.textContent = l.kind === "cue" ? `      ${l.text}` : l.text;
      if (!b.locked && editable.has(l.kind)) {
        row.classList.add("editable");
        row.title = "click to rewrite";
        row.addEventListener("click", () => editLine(row, l));
      }
      list.appendChild(row);
    });
    pane.appendChild(list);
  }

  function editLine(row, l) {
    if (row.querySelector("input")) return;
    const input = document.createElement("input");
    input.type = "text";
    input.value = l.text;
    input.className = "blineedit";
    row.replaceChildren(input);
    input.focus();
    input.select();
    const commit = async () => {
      const text = input.value.trim();
      if (!text || text === l.text) {
        row.textContent = l.text;
        return;
      }
      try {
        await api(`/api/director/sessions/${sessionId}/draft/line`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ line_id: l.line_id, text }),
        });
        l.text = text;
        row.textContent = text;
      } catch (err) {
        row.textContent = l.text;
        addMsg("director", `Couldn't rewrite that line: ${err.message || err}`);
      }
    };
    input.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter") { ev.preventDefault(); input.blur(); }
      if (ev.key === "Escape") { input.value = l.text; input.blur(); }
    });
    input.addEventListener("blur", commit);
  }

  function renderBiblesTab(pane, b) {
    pane.replaceChildren();
    if (!b.characters.length && !b.settings.length) {
      pane.textContent = "Bibles land at lock — characters with sheets, settings with plates.";
      return;
    }
    b.characters.forEach((c) => {
      const card = document.createElement("div");
      card.className = "bcard";
      card.innerHTML =
        `<div class="bhead">${esc(c.name)} <span class="bt">${esc(c.role)}</span>` +
        `${c.face_approved ? ' <span class="bok">FACE ✓</span>' : ""}` +
        `${c.flow_registered ? ' <span class="bok">FLOW ✓</span>' : ""}</div>` +
        `<div class="bward">${esc(c.wardrobe || "wardrobe pending")}</div>` +
        `<div class="bstills">${c.stills
          .map((s) => `<img src="/api/director/sessions/${sessionId}/bible/media/${encodeURIComponent(c.char_id)}/${encodeURIComponent(s)}" alt="${esc(c.name)} ${esc(s)}" title="${esc(s)}">`)
          .join("")}</div>`;
      if (!c.face_approved && c.stills.length) {
        const approve = document.createElement("button");
        approve.type = "button";
        approve.className = "acceptbtn";
        approve.textContent = `Approve ${c.name}'s face`;
        approve.addEventListener("click", async () => {
          approve.disabled = true;
          try {
            const res = await api(
              `/api/director/sessions/${sessionId}/bible/approve/${encodeURIComponent(c.char_id)}`,
              { method: "POST" }
            );
            addMsg("director", res.reply);
            refreshBoard();
          } catch (err) {
            approve.textContent = String(err.message || err);
            approve.disabled = false;
          }
        });
        card.appendChild(approve);
      }
      pane.appendChild(card);
    });
    b.settings.forEach((s) => {
      const card = document.createElement("div");
      card.className = "bcard";
      card.innerHTML =
        `<div class="bhead">${esc(s.name)}</div>` +
        `<div class="bward">${esc([s.time_weather, s.grade_language, s.camera_kit].filter(Boolean).join(" · ") || "craft pending")}</div>` +
        (s.plate_ready
          ? `<div class="bstills"><img src="/api/director/sessions/${sessionId}/plate/${encodeURIComponent(s.setting_id)}" alt="${esc(s.name)} plate"></div>`
          : '<div class="bt">plate pending</div>');
      pane.appendChild(card);
    });
  }

  function renderShotsTab(pane, b) {
    pane.replaceChildren();
    if (!b.clips.length) {
      pane.textContent = "The shot board fills at lock.";
      return;
    }
    b.clips.forEach((c) => {
      const row = document.createElement("div");
      row.className = "bshot";
      const verdict = c.verdict ? ` <span class="verdict ${c.verdict}">${c.verdict}</span>` : "";
      const accepted = c.accepted ? ' <span class="bok">accepted ✓</span>' : "";
      const join = c.join_next === "cut" ? "∎ cut" : `→ ${esc(c.join_next)}`;
      row.innerHTML =
        `<div class="bshotrow"><span class="bid">${esc(c.clip_id)}</span>` +
        `<span class="bt">${esc(c.clip_class)}</span>` +
        `<span class="bt">${c.duration_s}s · ${esc(c.model)}</span>` +
        `<span class="bt">${usd(c.est_usd_micros)}</span>` +
        `<span class="bstate">${esc(c.status)}</span>${verdict}${accepted}` +
        `<span class="bjoin">${join}</span></div>` +
        `<div class="bcam">${esc(c.camera)}</div>`;
      if (c.issues && c.issues.length) {
        const why = document.createElement("div");
        why.className = "verdict-issues";
        why.textContent = c.issues.join(" · ");
        row.appendChild(why);
      }
      if (c.op_id && c.status === "completed" && (c.verdict === "warn" || c.verdict === "fail") && !c.accepted) {
        const accept = document.createElement("button");
        accept.type = "button";
        accept.className = "acceptbtn";
        accept.textContent = c.verdict === "fail" ? "Accept anyway — cut it in" : "Accept take";
        accept.addEventListener("click", async () => {
          accept.disabled = true;
          try {
            const res = await api(`/api/director/ops/${c.op_id}/accept`, { method: "POST" });
            addMsg("director", res.reply);
            refreshBoard();
          } catch (err) {
            accept.textContent = String(err.message || err);
          }
        });
        row.appendChild(accept);
      }
      if (c.op_id && c.status === "failed") {
        const retry = document.createElement("button");
        retry.type = "button";
        retry.className = "retrybtn";
        retry.textContent = `↻ Retry — ${usd(c.est_usd_micros)}`;
        retry.title = "Re-roll this clip at its locked price — completed clips are never re-spent";
        retry.addEventListener("click", async () => {
          retry.disabled = true;
          try {
            await api(`/api/director/ops/${c.op_id}/retry`, { method: "POST" });
            addMsg("director", `${c.clip_id} re-queued at its locked price.`);
            refreshBoard();
          } catch (err) {
            retry.textContent = String(err.message || err);
          }
        });
        row.appendChild(retry);
      }
      if (c.can_upscale) {
        const up = document.createElement("button");
        up.type = "button";
        up.className = "acceptbtn";
        up.textContent = "Upscale 1080p";
        up.addEventListener("click", async () => {
          up.disabled = true;
          try {
            const res = await api(`/api/director/ops/${c.op_id}/upscale`, { method: "POST" });
            const li = addMsg("director", res.reply);
            addOpCardWith(li, res.op, pollProductionOp);
          } catch (err) {
            up.textContent = String(err.message || err);
            up.disabled = false;
          }
        });
        row.appendChild(up);
      }
      pane.appendChild(row);
    });
  }

  function renderCutTab(pane, b) {
    pane.replaceChildren();
    if (!b.film_ready) {
      const printed = (b.clips || []).filter((c) => c.status === "completed").length;
      if (!printed) {
        pane.textContent = "No cut yet — the film lands here after Run & Assemble.";
        return;
      }
      const note = document.createElement("div");
      note.className = "bnote";
      note.textContent = `No cut yet — ${printed} printed clip${printed === 1 ? "" : "s"} ready to assemble.`;
      pane.appendChild(note);
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "acceptbtn";
      btn.textContent = "Assemble the cut";
      btn.addEventListener("click", async () => {
        btn.disabled = true;
        btn.textContent = "assembling…";
        await assembleNow();
        refreshBoard();
      });
      pane.appendChild(btn);
      return;
    }
    const video = document.createElement("video");
    video.controls = true;
    video.className = "bfilm";
    video.src = `/api/director/films/${sessionId}?t=${Date.now()}`;
    pane.appendChild(video);
    if (b.deliverables && b.deliverables.length) {
      const list = document.createElement("div");
      list.className = "bdeliv";
      list.innerHTML =
        "<div class='bhead'>DELIVERABLES</div>" +
        b.deliverables
          .map((n) => `<a href="/api/director/sessions/${sessionId}/deliverables/${n}" target="_blank" rel="noopener">${esc(n)}</a>`)
          .join(" · ");
      pane.appendChild(list);
    }
  }

  function setBoardOpen(opening) {
    $("board").hidden = !opening;
    document.body.classList.toggle("board-open", opening);
    if (opening) {
      refreshBoard();
      boardTimer = setInterval(refreshBoard, 5000);
    } else if (boardTimer) {
      clearInterval(boardTimer);
      boardTimer = null;
    }
  }
  $("board-toggle").addEventListener("click", () => setBoardOpen($("board").hidden));
  $("board-close").addEventListener("click", () => setBoardOpen(false));
  document.querySelectorAll(".btabs button[data-tab]").forEach((btn) => {
    btn.addEventListener("click", () => {
      boardTab = btn.dataset.tab;
      document.querySelectorAll(".btabs button[data-tab]").forEach((other) => other.classList.toggle("on", other === btn));
      refreshBoard();
    });
  });

  ["dur-min", "dur-sec"].forEach((id) => {
    $(id).addEventListener("change", normalizeDuration);
    $(id).addEventListener("input", updateDurationHint);
  });
  updateDurationHint();

  async function resetSession() {
    try {
      const created = await api("/api/director/sessions", { method: "POST" });
      sessionId = created.session_id;
      thread.replaceChildren();
      $("idea").value = "";
      clearRefUploads();
      setDuration(8);
      refreshMeter();
      addMsg(
        "director",
        "Fresh session. Give me the next idea — one shot or a whole scripted piece; the price locks before anything rolls."
      );
      $("idea").focus();
    } catch (err) {
      addMsg("director", `Couldn't open a fresh session: ${err.message || err}`);
    }
  }

  $("reset").addEventListener("click", () => {
    if (thread.children.length > 1) {
      armConfirm($("reset"), "Confirm — clear the room", resetSession);
    } else {
      resetSession();
    }
  });
  $("add-img").addEventListener("click", () => $("img-file").click());
  $("img-file").addEventListener("change", (ev) => {
    addImageFiles(ev.target.files);
    ev.target.value = "";
  });
  // Dropping anywhere outside a drop zone must never navigate away to the file.
  ["dragover", "drop"].forEach((t) => window.addEventListener(t, (ev) => ev.preventDefault()));
  const composerEl = document.querySelector(".composer");
  ["dragenter", "dragover"].forEach((t) =>
    composerEl.addEventListener(t, (ev) => {
      ev.preventDefault();
      composerEl.classList.add("dropping");
    })
  );
  ["dragleave", "drop"].forEach((t) =>
    composerEl.addEventListener(t, (ev) => {
      ev.preventDefault();
      composerEl.classList.remove("dropping");
    })
  );
  composerEl.addEventListener("drop", (ev) => {
    if (ev.dataTransfer && ev.dataTransfer.files.length) addImageFiles(ev.dataTransfer.files);
  });
  $("idea").addEventListener("paste", (ev) => {
    const files = ev.clipboardData ? ev.clipboardData.files : null;
    if (files && files.length) {
      addImageFiles(files);
      ev.preventDefault();
    }
  });

  $("send").addEventListener("click", send);
  $("produce").addEventListener("click", produce);
  $("idea").addEventListener("keydown", (ev) => {
    if (ev.key === "Enter" && !ev.shiftKey) {
      ev.preventDefault();
      send();
    }
  });
  boot();
})();
