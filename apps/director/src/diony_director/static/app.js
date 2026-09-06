/* Diony Director M1 — Guided chat over the director API. */
(() => {
  const $ = (id) => document.getElementById(id);
  const thread = $("thread");
  let sessionId = null;
  // The run a Retry resumes. Run & Assemble (and Run, Assemble & Upscale All)
  // register their watch-then-cut loop here; a Retry re-enters it instead of
  // asking the user to press the run button again. Survives a reload through
  // the server's run plan (resumeFromPlan).
  let runContinuation = null; // { active: bool, resume(): void }
  let planResumeActive = false;
  const usd = (micros) => `$${(micros / 1e6).toFixed(2)}`;
  const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

  // Reference stills: uploaded or dropped images anchor the film's LOOK and
  // identity. Every still rides as a reference; exactly one may be marked
  // the OPENER (the first frame) — nothing opens the frame unless the user
  // says so (Blueprint 29).
  const refUploads = []; // {id, name, url, role}

  function renderRefChips() {
    const host = $("refchips");
    host.replaceChildren();
    refUploads.forEach((u) => {
      const opener = u.role === "opener";
      const chip = document.createElement("span");
      chip.className = "refchip" + (opener ? " opening" : "");
      const img = document.createElement("img");
      img.src = u.url;
      img.alt = u.name;
      chip.appendChild(img);
      const label = document.createElement("span");
      label.className = "refname";
      label.textContent = (opener ? "OPENS · " : "") + u.name;
      chip.appendChild(label);
      const role = document.createElement("button");
      role.type = "button";
      role.className = "refrole";
      role.textContent = opener ? "opener" : "reference";
      role.title = opener
        ? "This still opens the frame — click to make it a look reference instead"
        : "A look and identity reference — click to make it the opening frame";
      role.addEventListener("click", async () => {
        const next = opener ? "reference" : "opener";
        try {
          const body = await api(`/api/director/sessions/${sessionId}/uploads/${u.id}/role`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ role: next }),
          });
          const roles = new Map((body.uploads || []).map((r) => [r.id, r.role]));
          refUploads.forEach((r) => { r.role = roles.get(r.id) || "reference"; });
          renderRefChips();
        } catch (err) {
          addMsg("director", `I couldn't change that still's role: ${err.message || err}`);
        }
      });
      chip.appendChild(role);
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
            refUploads.push({
              id: body.upload.id, name: file.name, url: URL.createObjectURL(file),
              role: body.upload.role || "reference",
            });
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
      hint.textContent = `${Math.floor(total / 60)}m ${total % 60}s — a one-shot this long rolls as chained clips`;
      hint.className = "durhint";
    } else {
      hint.textContent = "any length · long one-shots chain into sequential clips";
      hint.className = "durhint";
    }
  }

  async function api(path, options) {
    const res = await fetch(path, options);
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(body.detail || `${res.status}`);
    return body;
  }

  function addMsg(who, text, kind) {
    const li = document.createElement("li");
    li.className = `msg ${who}`;
    if (kind) li.classList.add(kind);
    li.innerHTML = `<div class="who">${who === "director" ? "Director" : "You"}</div><div class="body"></div>`;
    li.querySelector(".body").textContent = text;
    thread.appendChild(li);
    li.scrollIntoView({ block: "end" });
    return li;
  }

  // A proposal reply arrives with structured facts (plan) beside the prose.
  // The room renders those as a plan card — headline, stat tiles, note
  // lines — so the numbers read at a glance; without a plan the prose
  // reply stands as before.
  function addPlanMsg(plan, fallbackText) {
    if (!plan) return addMsg("director", fallbackText);
    const li = document.createElement("li");
    li.className = "msg director plan";
    li.innerHTML = `<div class="who">Director</div><div class="body"></div>`;
    const body = li.querySelector(".body");

    const lead = document.createElement("p");
    lead.className = "planlead";
    lead.textContent = plan.headline;
    body.appendChild(lead);

    const facts = document.createElement("div");
    facts.className = "planfacts";
    const fact = (label, value, sub, cls) => {
      const f = document.createElement("div");
      f.className = "fact" + (cls ? ` ${cls}` : "");
      const l = document.createElement("span");
      l.className = "flabel";
      l.textContent = label;
      const v = document.createElement("span");
      v.className = "fval";
      v.textContent = value;
      f.appendChild(l);
      f.appendChild(v);
      if (sub) {
        const s = document.createElement("span");
        s.className = "fsub";
        s.textContent = sub;
        f.appendChild(s);
      }
      facts.appendChild(f);
    };
    const secs = plan.clip_seconds || [];
    const totalS = secs.reduce((a, b) => a + b, 0);
    const uniform = secs.length > 1 && secs.every((s) => s === secs[0]);
    fact("Route", plan.model, plan.provider_label);
    if (secs.length > 1) {
      fact(
        "Clips",
        uniform ? `${secs.length} × ${secs[0]}s` : `${secs.length}`,
        uniform ? "sequential chain" : secs.map((s) => `${s}s`).join(" + ")
      );
    }
    fact("Runtime", `${totalS}s`, plan.resolution);
    fact("This pass", `+${usd(plan.pass_usd_micros)}`, "adds to session", "cost");
    fact("Session", usd(plan.session_after_usd_micros), "after this pass", "cost");
    body.appendChild(facts);

    if ((plan.notes || []).length) {
      const ul = document.createElement("ul");
      ul.className = "plannotes";
      plan.notes.forEach((n) => {
        const item = document.createElement("li");
        item.textContent = n;
        ul.appendChild(item);
      });
      body.appendChild(ul);
    }

    thread.appendChild(li);
    li.scrollIntoView({ block: "end" });
    return li;
  }

  // Hold/failure reasons get one loud bubble, not a wall: an identical
  // reason repeating (the same hold across waves) bumps a counter on the
  // bubble it already has instead of burying the room in duplicates.
  let lastHeld = { text: "", el: null, n: 0 };
  const holdTimers = new Set();
  function clockOf(epochS) {
    // the desk's own clock, 12-hour, with the zone the user lives in
    const d = new Date(epochS * 1000);
    const t = d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
    const zone = (new Intl.DateTimeFormat([], { timeZoneName: "short" })
      .formatToParts(d).find((p) => p.type === "timeZoneName") || {}).value || "";
    return zone ? `${t} ${zone}` : t;
  }
  function untilText(epochS) {
    const s = Math.max(0, Math.round(epochS - Date.now() / 1000));
    if (s < 60) return s ? `in ${s}s` : "any moment now";
    const m = Math.round(s / 60);
    return m < 60 ? `in ${m} min` : `in ${Math.floor(m / 60)}h ${m % 60}m`;
  }
  function addHeldMsg(entry) {
    // A hold is structured when the server knows why (a provider quota with
    // a reset time); a plain string is any other reason. Either way one
    // bubble per distinct reason — a repeat bumps its counter.
    const h = typeof entry === "string" ? { reason: entry } : (entry || {});
    const key = h.kind === "quota" ? `quota:${h.provider || ""}:${h.detail || h.reason}` : String(h.reason || "");
    if (lastHeld.el && lastHeld.text === key && lastHeld.el.isConnected) {
      lastHeld.n += 1;
      const count = lastHeld.el.querySelector(".holdcount");
      if (count) count.textContent = `×${lastHeld.n}`;
      lastHeld.el.scrollIntoView({ block: "end" });
      return lastHeld.el;
    }
    const li = addMsg("director", "", h.kind === "quota" ? "hold" : "warn");
    const body = li.querySelector(".body");
    if (h.kind === "quota" && h.resume_at) {
      runState.hold = h; // the strip shows the pause and its clock
      repaintRun();
    }
    if (h.kind === "quota") {
      const provider = { "google-flow": "Google Flow", grok: "Grok Imagine" }[h.provider] || "Provider";
      body.innerHTML =
        `<div class="holdhead"><span class="holdtag">Run paused</span><span>${esc(provider)} quota</span><span class="holdcount"></span></div>` +
        `<div class="holdwhy">${esc(h.detail || h.reason || "")}</div>` +
        `<div class="holdwhen">Resumes automatically at <b class="holdclock"></b> <span class="holduntil"></span></div>` +
        `<div class="holdnote">Nothing was spent. ${h.clip_id ? esc(h.clip_id) + " and the clips behind it" : "The held clips"} stay queued and roll on their own when the window opens.</div>`;
      const clock = body.querySelector(".holdclock");
      const until = body.querySelector(".holduntil");
      const paint = () => {
        if (!li.isConnected) { clearInterval(timer); holdTimers.delete(timer); return; }
        if (h.resume_at) {
          clock.textContent = clockOf(h.resume_at);
          until.textContent = `· ${untilText(h.resume_at)}`;
          if (h.resume_at <= Date.now() / 1000) {
            until.textContent = "· resuming";
            clearInterval(timer); holdTimers.delete(timer);
          }
        } else {
          clock.textContent = "the provider's next window";
        }
      };
      const timer = setInterval(paint, 15000);
      holdTimers.add(timer);
      paint();
    } else {
      body.innerHTML =
        `<div class="holdhead"><span class="holdtag">Held</span><span class="holdcount"></span></div>` +
        `<div class="holdwhy"></div>`;
      body.querySelector(".holdwhy").textContent = h.reason || "";
    }
    lastHeld = { text: key, el: li, n: 1 };
    return li;
  }

  function failedText(body) {
    // a submit-side failure has no provider job — its reason lives on the op
    const fromJob = body.job && body.job.error_message;
    return `failed — ${fromJob || body.submit_error || "see the room for the hold reason"}`;
  }

  function estimateTable(est) {
    const rows = est.lines
      .map((l) => `<tr><td>${esc(l.label)}</td><td>${usd(l.usd_micros)}</td></tr>`)
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

  // Blueprint 27: the pre-split finalized artifact — what the Idea-to-Prompt
  // stage derived from the brief and the duration, before any shot was split.
  function renderMaster(host, master, opts) {
    if (!master || !master.prompt) return null;
    const details = document.createElement("details");
    details.className = "prompt master";
    if (opts && opts.open) details.open = true;
    const summary = document.createElement("summary");
    const est = master.estimated_duration_s || master.target_duration_s;
    const src = master.source === "brain" ? "brain" : "template";
    summary.textContent =
      `master prompt — the pre-split finalized artifact · ${master.word_count} words · ` +
      `${(master.beats || []).length} beats · plays ~${est}s of your ${master.target_duration_s}s · ${src}`;
    summary.title = "Derived from your idea, stills, and duration by the MASTER AI VIDEO PROMPT TEMPLATE (GET /api/director/master/template)";
    const text = document.createElement("div");
    text.className = "ptext";
    text.textContent = master.prompt;
    details.appendChild(summary);
    details.appendChild(text);
    const fields = document.createElement("dl");
    fields.className = "mfields";
    const light = master.lighting || {};
    const look = master.look || {};
    const pairs = [
      ["style", master.visual_style],
      ["look", [look.medium, look.palette].filter(Boolean).join(" · ")],
      ["subject", master.primary_subject],
      ["setting", master.setting],
      ["camera", master.camera],
      ["lighting", [light.source, light.quality, light.shadows].filter(Boolean).join(" · ")],
      ["mood", master.mood],
      ["anchors", (master.continuity_anchors || []).join(" · ")],
      ["constraints", (master.constraints || []).join(" · ")],
      ["ending", master.ending],
    ];
    pairs.forEach(([k, v]) => {
      if (!v) return;
      const dt = document.createElement("dt");
      dt.textContent = k;
      const dd = document.createElement("dd");
      dd.textContent = v;
      fields.appendChild(dt);
      fields.appendChild(dd);
    });
    details.appendChild(fields);
    if (master.warnings && master.warnings.length) {
      const warn = document.createElement("div");
      warn.className = "draft-warn";
      warn.textContent = master.warnings.join(" · ");
      details.appendChild(warn);
    }
    host.appendChild(details);
    return details;
  }

  // Chain-aware artifact display: a chained one-shot card carries ITS OWN
  // post-split clip prompt from the master's CLIP DELIVERY split — never the
  // whole pre-split master duplicated per link. The pre-split master rides
  // the chain opener once; a single-clip one-shot keeps it as before.
  function renderMasterFor(card, master, chain) {
    if (!master) return;
    if (chain && Array.isArray(master.clips) && master.clips.length > chain.index) {
      if (chain.index === 0) renderMaster(card, master);
      renderClipMaster(card, master, chain.index, chain.count);
      return;
    }
    renderMaster(card, master);
  }

  function renderClipMaster(host, master, index, count) {
    const clip = master.clips[index];
    if (!clip || !clip.prompt) return null;
    const details = document.createElement("details");
    details.className = "prompt master clipm";
    const summary = document.createElement("summary");
    const words = String(clip.prompt).trim().split(/\s+/).length;
    const dur = clip.duration_s ? ` · plays ${clip.duration_s}s` : "";
    summary.textContent =
      `clip ${index + 1}/${count} prompt — the post-split artifact · ${words} words${dur} · ` +
      (master.source === "brain" ? "brain" : "template");
    summary.title =
      "This clip's own segment of the master's CLIP DELIVERY split — the prose this card actually rolls, plus the route's guards";
    const text = document.createElement("div");
    text.className = "ptext";
    text.textContent = clip.prompt;
    details.appendChild(summary);
    details.appendChild(text);
    host.appendChild(details);
    return details;
  }

  // Blueprint 29: a chain link that cuts to a new place opens from a STYLE
  // FRAME — a still composed in the film's look before the clip rolls. The
  // card says so, prices it, and shows the frame once it has printed.
  function attachStyleFrame(card, body) {
    const frame = body && body.style_frame;
    if (!frame) return;
    let note = card.querySelector(".stylenote");
    if (!note) {
      note = document.createElement("div");
      note.className = "stylenote";
      const prompt = card.querySelector("details.prompt");
      if (prompt) card.insertBefore(note, prompt); else card.appendChild(note);
    }
    const cost = frame.est_usd_micros ? ` · ${usd(frame.est_usd_micros)}` : "";
    const status = frame.ready ? "printed" : frame.status;
    const redrawn = frame.redraws
      ? ` · opening frame redrawn ${frame.redraws}× after a failed attempt${frame.safe ? ", steered clear of any recognizable person" : ""}`
      : "";
    note.textContent = frame.continues
      ? `continues the previous shot — opens from a redrawn frame composed in the film's look (${frame.model}${cost}, ${status})${redrawn}`
      : `scene change — opens from a style frame composed in the film's look (${frame.model}${cost}, ${status})${redrawn}`;
    let img = card.querySelector(".styleframe");
    if (frame.ready && (!img || img.dataset.frameOp !== frame.op_id)) {
      // a redrawn frame replaces the picture on the card — never a stale still
      if (!img) {
        img = document.createElement("img");
        img.className = "styleframe";
        img.alt = "style frame — the opening frame of this clip";
        img.title = img.alt;
        note.after(img);
      }
      img.dataset.frameOp = frame.op_id;
      img.src = `/api/director/ops/${body.op.id}/style_frame?t=${Date.now()}`;
    } else if (!frame.ready && img && frame.redraws) {
      img.remove(); // the old frame is discarded; the redraw is on its way
    }
  }

  // The failure a derivation left on the op's rationale, in the user's words.
  function derivationNote(op) {
    const parts = String(op.rationale || "").split(" · ").slice(1);
    return (
      parts.find((p) => p.includes("derivation")) ||
      "the master prompt was never derived"
    );
  }

  // A provisional prompt never rolls — the server 409s it, and the desk says
  // why. s-16d2e6e5e0: a failed derivation released the chain as runnable
  // and twelve clips rolled on the raw idea with no script. The card holds
  // with the failure named and, alone, a Retry that re-derives in place; a
  // chain link defers to the chain bar's one Retry.
  function holdProvisional(op, card, chain, onRetry) {
    const state = card.querySelector(".state");
    const btn = card.querySelector("button");
    if (btn) btn.disabled = true;
    state.textContent = "held — master prompt not derived";
    state.className = "state failed";
    card.querySelectorAll(".rederive, .derivenote").forEach((el) => el.remove());
    if (chain) return;
    const note = document.createElement("div");
    note.className = "derivenote";
    note.textContent = derivationNote(op);
    const retry = document.createElement("button");
    retry.type = "button";
    retry.className = "rederive";
    retry.textContent = "Retry derivation";
    retry.addEventListener("click", async () => {
      retry.disabled = true;
      try {
        await api(`/api/director/ops/${op.id}/derive`, { method: "POST" });
        note.remove();
        retry.remove();
        onRetry();
      } catch (err) {
        retry.disabled = false;
        note.textContent = `retry failed: ${err.message || err}`;
      }
    });
    state.after(retry);
    card.querySelector(".row").after(note);
  }

  // A one-shot priced while the brain derives its master prompt: the card
  // holds (Run disabled) and unlocks — prompt swapped in, artifact shown —
  // the moment the artifact lands. Nothing can roll before it, and a
  // derivation that failed holds the card instead of unlocking it.
  function landDerivedCard(op, card, body, chain) {
    // the artifact landed: swap the provisional prompt for the derived one,
    // hang the artifact panels, and unlock the Run button
    card.querySelectorAll(".whatrolls, details.prompt").forEach((el) => el.remove());
    attachPrompt(card, body.op);
    attachStyleFrame(card, body);
    renderMasterFor(card, body.master, chain);
    Object.assign(op, body.op);
    const state = card.querySelector(".state");
    const btn = card.querySelector("button");
    if (body.op.prompt_version === "v0-provisional") {
      holdProvisional(op, card, chain, () => watchDeriving(op, card, btn, state, chain));
      return false;
    }
    state.textContent = body.op.status;
    state.className = "state";
    if (btn && body.op.status === "proposed") btn.disabled = false;
    return true;
  }

  function watchDeriving(op, card, btn, state, chain) {
    state.textContent = "deriving master prompt…";
    state.className = "state deriving";
    btn.disabled = true;
    const started = Date.now();
    runBegin("derive", {
      phase: "deriving", total: 0,
      detail: `${op.duration_s}s one shot · ${op.provider} · ${op.model}`,
    });
    const tick = async () => {
      let body;
      try {
        body = await api(`/api/director/ops/${op.id}`);
      } catch (err) {
        setTimeout(tick, 3000);
        return;
      }
      if (body.op.status === "deriving") {
        state.textContent = `deriving master prompt… ${Math.round((Date.now() - started) / 1000)}s`;
        setTimeout(tick, 3000);
        return;
      }
      landDerivedCard(op, card, body, chain);
      runEnd("derive");
    };
    setTimeout(tick, 2500);
  }

  function addOpCard(host, op, chain) {
    const card = document.createElement("div");
    card.className = "opcard";
    const clipChip = chain
      ? `<span class="chip clipnum">clip ${chain.index + 1} / ${chain.count}</span>`
      : "";
    card.innerHTML = `
      <div class="row">
        ${clipChip}
        <span class="chip">${esc(op.provider)} · ${esc(op.model)}</span>
        <span class="chip">${esc(op.duration_s)}s · ${esc(op.resolution)}</span>
        <span class="est">est ${usd(op.estimate.total_usd_micros)}</span>
        <button type="button">Run — ${usd(op.estimate.total_usd_micros)}</button>
        <span class="state">${esc(op.status || "proposed")}</span>
      </div>
      ${estimateTable(op.estimate)}`;
    attachPrompt(card, op);
    host.appendChild(card);
    const btn = card.querySelector("button");
    const state = card.querySelector(".state");
    if (op.status === "deriving") {
      if (chain) {
        // one derivation covers the whole chain — the chain bar carries the
        // ticking status once, and unlocks every card when it lands; the
        // card itself just says it's waiting
        state.textContent = "deriving";
        state.className = "state deriving";
        btn.disabled = true;
      } else {
        watchDeriving(op, card, btn, state, chain);
      }
    } else if (op.prompt_version === "v0-provisional" && op.status === "proposed") {
      // a reopened room whose derivation failed (or a restart cut it short)
      holdProvisional(op, card, chain, () => watchDeriving(op, card, btn, state, chain));
    } else if (op.master) renderMasterFor(card, op.master, chain);
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      state.textContent = "submitting…";
      singleBegin(op.id, chain ? `clip ${chain.index + 1} / ${chain.count}` : op.clip_id || "the clip", op.estimate.total_usd_micros);
      try {
        await api(`/api/director/ops/${op.id}/run`, { method: "POST" });
        state.textContent = "generating…";
        singleSet(op.id, "submitted");
        poll(op.id, card, state);
      } catch (err) {
        state.textContent = String(err.message || err);
        state.className = "state failed";
        btn.disabled = false;
        singleSet(op.id, "failed");
      }
      refreshMeter();
    });
    return card;
  }

  function poll(opId, card, state) {
    const tick = async () => {
      try {
        const body = await api(`/api/director/ops/${opId}`);
        const status = body.op.status;
        if (status === "completed") {
          singleSet(opId, "completed");
          state.textContent = `printed · ${body.clip ? body.clip.bytes + " bytes" : ""}`;
          state.className = "state done";
          attachStyleFrame(card, body);
          const video = document.createElement("video");
          video.controls = true;
          video.src = `/api/director/clips/${opId}`;
          card.appendChild(video);
          // a chain link's look review lands on its card, with Retake
          attachVerdict(card, state, body.review, opId, { retake: !body.op.clip_id });
          if (body.narration && body.review && body.review.verdict === "fail") {
            addMsg("director", body.narration, "warn");
          }
          refreshMeter();
          return;
        }
        if (status === "failed") {
          singleSet(opId, "failed");
          state.textContent = failedText(body);
          state.className = "state failed";
          attachRetry(card, opId, state);
          refreshMeter();
          return;
        }
        if (status === "discarded") {
          singleSet(opId, "discarded");
          state.textContent = "skipped — the cut leaves this shot out";
          state.className = "state skipped";
          return;
        }
        setTimeout(tick, 2500);
      } catch (err) {
        singleSet(opId, "failed");
        state.textContent = String(err.message || err);
        state.className = "state failed";
      }
    };
    setTimeout(tick, 1500);
  }

  // --- The run strip -------------------------------------------------------
  // While the Director rolls, the console folds into one live status panel:
  // phase, a per-clip track in shot order, pace and ETA, the locked price
  // against spend so far, and the set the run holds — read-only. The idea,
  // stills, and pills are hidden and inert until the set settles: nothing
  // can be re-aimed mid-run. Three sources feed it, in precedence order:
  // the loop that owns a run (batch / chain / plan / retakes / draft), the
  // one-off Run buttons, and the Production card's own poll — which is what
  // survives a reload. body[data-running="<phase>"] is the one switch.
  const RUN_PHASES = {
    deriving: { tag: "Deriving", title: "Deriving the master prompt" },
    drafting: { tag: "Drafting", title: "Writing the screenplay" },
    rolling: { tag: "Rolling", title: "Rolling the clips" },
    retaking: { tag: "Retaking", title: "Rolling the retakes" },
    finishing: { tag: "Finishing", title: "Finishing at 1080p" },
    assembling: { tag: "Assembling", title: "Cutting the film" },
    holding: { tag: "Paused", title: "Run paused" },
  };
  const runState = {
    flow: null, // { source, model } — the loop that owns the run
    single: new Map(), // op_id -> { status, label, est } — one-off Run buttons
    board: null, // a model derived from the Production card's poll
    hold: null, // a quota pause { provider, detail, resume_at }
    startedAt: 0, // wall clock of the first activity; resets when all settles
    phase: "", phaseAt: 0, phasePrinted: 0, lastPrintAt: 0,
    ticker: null,
    spendMicros: null, spendFor: -1,
  };
  const runbar = $("runbar");

  function runBegin(source, model) {
    runState.flow = { source, model: Object.assign({ phase: "rolling" }, model) };
    repaintRun();
  }
  function runUpdate(source, patch) {
    if (!runState.flow || runState.flow.source !== source) return;
    Object.assign(runState.flow.model, patch);
    repaintRun();
  }
  // a later stage (finish, cut) rides whichever loop owns the run
  function runPatch(patch) {
    if (!runState.flow) return;
    Object.assign(runState.flow.model, patch);
    repaintRun();
  }
  function runEnd(source) {
    if (runState.flow && runState.flow.source === source) runState.flow = null;
    repaintRun();
  }
  function runReset() {
    runState.flow = null;
    runState.single.clear();
    runState.board = null;
    runState.hold = null;
    repaintRun();
  }

  // One-off Run buttons: every op pressed is a segment; the strip folds when
  // the last one settles (a terminal entry stays on the track until then).
  const SINGLE_LIVE = new Set(["submitting", "submitted", "proposed", "deriving"]);
  function singleBegin(opId, label, estMicros) {
    runState.single.set(opId, { status: "submitting", label, est: estMicros || 0 });
    repaintRun();
  }
  function singleSet(opId, status) {
    const entry = runState.single.get(opId);
    if (!entry) return;
    entry.status = status;
    const live = [...runState.single.values()].some((s) => SINGLE_LIVE.has(s.status));
    if (!live) runState.single.clear();
    repaintRun();
  }

  function segClass(status) {
    if (status === "completed") return "done";
    if (status === "failed") return "failed";
    if (status === "discarded") return "skipped";
    if (status === "submitted" || status === "submitting" || status === "deriving") return "rolling";
    if (status === "proposed") return "queued";
    return "idle";
  }
  // counts from a list of {status, label} segments in shot order
  function countSegments(segments) {
    const m = { total: segments.length, printed: 0, rolling: 0, queued: 0, failed: 0, skipped: 0, segments, detail: "" };
    const live = [];
    segments.forEach((s) => {
      const k = segClass(s.status);
      if (k === "done") m.printed += 1;
      else if (k === "failed") m.failed += 1;
      else if (k === "skipped") m.skipped += 1;
      else if (k === "queued") m.queued += 1;
      else if (k === "rolling") { m.rolling += 1; live.push(s.label); }
    });
    if (live.length) {
      m.detail = `rolling ${live.slice(0, 3).join(", ")}${live.length > 3 ? ` +${live.length - 3} more` : ""}`;
    }
    return m;
  }
  // the advance tick's per-op statuses, mapped onto a fixed id order
  function segmentsFor(ids, labels, ops) {
    const byId = new Map((ops || []).map((o) => [o.op_id, o]));
    return ids.map((id, i) => {
      const o = byId.get(id);
      return { status: o ? o.status : "proposed", label: labels[i] || (o && o.clip_id) || `clip ${i + 1}` };
    });
  }
  // A run loop's handle on the strip: begin once, feed every advance tick,
  // end when the loop settles. `ids` fixes the track's order and labels.
  function runTracker(source, ids, labels, base) {
    let last = countSegments(ids.map((id, i) => ({ status: "proposed", label: labels[i] || `clip ${i + 1}` })));
    return {
      begin(extra) { runBegin(source, Object.assign({}, base, last, extra || {})); },
      tick(counts, extra) {
        last = countSegments(segmentsFor(ids, labels, counts.ops));
        const stalled = counts.stalled || [];
        runUpdate(source, Object.assign(
          { phase: "rolling", note: stalled.length ? `stalled — ${stalled[0].reason}` : "" },
          last, extra || {}
        ));
      },
      end() { runEnd(source); },
    };
  }
  function modelFromSingle() {
    const items = [...runState.single.values()];
    if (!items.length) return null;
    const m = countSegments(items.map((s) => ({ status: s.status, label: s.label })));
    return Object.assign(m, {
      phase: "rolling",
      title: items.length === 1 ? `Rolling ${items[0].label}` : RUN_PHASES.rolling.title,
      estMicros: items.reduce((t, s) => t + s.est, 0),
    });
  }
  function modelFromBoard(b) {
    const clips = b.clips || [];
    const takesLive = clips.some((c) => IN_FLIGHT.has(c.status));
    if (!takesLive) {
      // only finishes are moving: the track is the finishes, in shot order
      const withFinish = clips.filter((c) => c.finish);
      const segs = withFinish.map((c) => ({
        status: c.finish.usable ? "completed" : c.finish.status, label: `${c.clip_id} ${c.finish.resolution || "1080p"}`,
      }));
      return Object.assign(countSegments(segs), { phase: "finishing", queuedLabel: "Waiting" });
    }
    const segs = clips.map((c) => ({ status: c.status, label: c.clip_id }));
    return Object.assign(countSegments(segs), {
      phase: "rolling",
      queuedLabel: "Drafted",
      estMicros: clips.reduce((t, c) => t + (c.est_usd_micros || 0), 0),
    });
  }

  function activeRun() {
    if (runState.flow) return runState.flow.model;
    return modelFromSingle() || runState.board;
  }
  function repaintRun() {
    const m = activeRun();
    if (!m) {
      runState.startedAt = 0;
      runState.phase = "";
      runState.spendMicros = null;
      runState.spendFor = -1;
      stopRunTicker();
      delete document.body.dataset.running;
      document.body.classList.remove("peek");
      runbar.hidden = true;
      runbar.replaceChildren();
      return;
    }
    const now = Date.now();
    if (!runState.startedAt) {
      runState.startedAt = now;
      runState.lastPrintAt = now;
    }
    if (m.phase !== runState.phase) {
      runState.phase = m.phase;
      runState.phaseAt = now;
      runState.phasePrinted = m.printed || 0;
      runState.lastPrintAt = now;
    }
    if ((m.printed || 0) !== runState.spendFor) {
      if (runState.spendFor >= 0) runState.lastPrintAt = now;
      fetchRunSpend(m.printed || 0);
    }
    const holding = !!(runState.hold && runState.hold.resume_at > now / 1000);
    if (!holding) runState.hold = null;
    document.body.dataset.running = holding ? "holding" : m.phase;
    runbar.hidden = false;
    paintRunbar(m, holding);
    startRunTicker();
  }

  function mk(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = String(text);
    return node;
  }
  function runFact(host, label, value, sub, cls) {
    const f = mk("div", "fact" + (cls ? ` ${cls}` : ""));
    f.appendChild(mk("span", "flabel", label));
    f.appendChild(mk("span", "fval", value));
    const s = mk("span", "fsub" + (label === "Pace" ? " runeta" : label === "Locked" ? " runspend" : ""), sub || "");
    f.appendChild(s);
    host.appendChild(f);
    return f;
  }
  function paintRunbar(m, holding) {
    const phase = RUN_PHASES[m.phase] || RUN_PHASES.rolling;
    const total = m.total || 0;
    const printed = m.printed || 0;
    runbar.replaceChildren();

    const head = mk("div", "runhead");
    head.appendChild(mk("span", "runpulse"));
    head.appendChild(mk("span", "runtag", holding ? RUN_PHASES.holding.tag : phase.tag));
    head.appendChild(mk("span", "runtitle", m.title || phase.title));
    if (m.detail) head.appendChild(mk("span", "rundetail", m.detail));
    const clock = mk("span", "runclock", elapsedText());
    clock.title = "elapsed since the run began";
    head.appendChild(clock);
    runbar.appendChild(head);

    if (total) {
      const track = mk("div", "runtrack" + (total > 60 ? " dense" : ""));
      track.setAttribute("role", "progressbar");
      track.setAttribute("aria-valuemin", "0");
      track.setAttribute("aria-valuemax", String(total));
      track.setAttribute("aria-valuenow", String(printed));
      track.setAttribute("aria-label", `${printed} of ${total} printed`);
      m.segments.forEach((s) => {
        const seg = mk("span", `seg ${segClass(s.status)}`);
        seg.title = `${s.label} — ${s.status}`;
        track.appendChild(seg);
      });
      runbar.appendChild(track);

      const facts = mk("div", "runfacts");
      const pct = Math.round((printed / total) * 100);
      runFact(facts, "Printed", `${printed} / ${total}`, `${pct}% of the set`, "ok");
      runFact(facts, "Rolling", m.rolling || 0, m.rolling ? "at the provider now" : "none in flight", m.rolling ? "hot" : "quiet");
      runFact(facts, m.queuedLabel || "Queued", m.queued || 0, m.queued ? "roll in order" : "", "quiet");
      if (m.failed) runFact(facts, "Failed", m.failed, "Retry or Skip on the card", "bad");
      if (m.skipped) runFact(facts, "Skipped", m.skipped, "left out of the cut", "quiet");
      runFact(facts, "Pace", paceText(m), etaText(m), "quiet");
      if (m.estMicros) runFact(facts, "Locked", usd(m.estMicros), spendText(), "cost");
      runbar.appendChild(facts);

      const legend = mk("div", "runlegend");
      legend.appendChild(mk("span", "done", "printed"));
      legend.appendChild(mk("span", "rolling", "rolling"));
      legend.appendChild(mk("span", "queued", (m.queuedLabel || "queued").toLowerCase()));
      if (m.failed) legend.appendChild(mk("span", "failed", "failed"));
      if (m.skipped) legend.appendChild(mk("span", "skipped", "skipped"));
      runbar.appendChild(legend);
    }

    if (holding) {
      const h = runState.hold;
      const provider = { "google-flow": "Google Flow", grok: "Grok Imagine" }[h.provider] || "Provider";
      const box = mk("div", "runhold");
      box.appendChild(mk("span", "holdtag", "Paused"));
      box.appendChild(mk("span", "", `${provider} quota — ${h.detail || h.reason || "the provider's window is closed"}. Nothing is spent while it waits.`));
      const when = mk("span", "");
      when.append("Resumes at ", mk("b", "holdclock", clockOf(h.resume_at)), " ");
      when.appendChild(mk("span", "holduntil", untilText(h.resume_at)));
      box.appendChild(when);
      runbar.appendChild(box);
    }
    if (m.note) runbar.appendChild(mk("div", "runnote", m.note));

    const foot = mk("div", "runfoot");
    const cfg = mk("span", "runcfg");
    cfg.appendChild(mk("span", "cfglbl", "holds"));
    configSummary().forEach((bit, i) => {
      if (i) cfg.append(" · ");
      cfg.appendChild(mk("b", "", bit));
    });
    foot.appendChild(cfg);
    const peek = mk("button", "runbtn" + (document.body.classList.contains("peek") ? " on" : ""), "Settings");
    peek.type = "button";
    peek.title = "Peek at the pills this run holds — read-only until the set settles";
    peek.addEventListener("click", () => {
      peek.classList.toggle("on", document.body.classList.toggle("peek"));
    });
    foot.appendChild(peek);
    const jump = mk("button", "runbtn", productionCard && productionCard.isConnected ? "Production ▸" : "Latest ▸");
    jump.type = "button";
    jump.title = "Scroll to where the run is reporting";
    jump.addEventListener("click", () => {
      if (productionCard && productionCard.isConnected) showProductionCard();
      else if (thread.lastElementChild) thread.lastElementChild.scrollIntoView({ block: "end" });
    });
    foot.appendChild(jump);
    runbar.appendChild(foot);
  }

  // pace: wall time per print within the current phase; ETA counts down
  // from the last print — never a number before the first one lands
  function paceInfo(m) {
    const done = (m.printed || 0) - runState.phasePrinted;
    const elapsed = Date.now() - runState.phaseAt;
    if (done < 1 || elapsed < 1000) return null;
    const per = elapsed / done;
    const left = Math.max(0, (m.total || 0) - (m.printed || 0) - (m.failed || 0) - (m.skipped || 0));
    const remaining = Math.max(0, left * per - (Date.now() - runState.lastPrintAt));
    return { per, left, remaining };
  }
  function durText(ms) {
    const s = Math.max(0, Math.round(ms / 1000));
    if (s < 60) return `${s}s`;
    const m = Math.floor(s / 60);
    return m < 60 ? `${m}m ${String(s % 60).padStart(2, "0")}s` : `${Math.floor(m / 60)}h ${m % 60}m`;
  }
  function paceText(m) {
    const p = paceInfo(m);
    return p ? `~${durText(p.per)} / clip` : "measuring…";
  }
  function etaText(m) {
    const p = paceInfo(m);
    if (!p) return "ETA after the first print";
    if (!p.left) return "last one landing";
    return `~${durText(p.remaining)} left`;
  }
  function elapsedText() {
    const s = Math.max(0, Math.floor((Date.now() - runState.startedAt) / 1000));
    const h = Math.floor(s / 3600);
    const mm = String(Math.floor((s % 3600) / 60)).padStart(2, "0");
    const ss = String(s % 60).padStart(2, "0");
    return (h ? `${h}:` : "") + `${mm}:${ss}`;
  }
  function spendText() {
    return runState.spendMicros == null ? "spend so far — reading the ledger" : `spent so far ${usd(runState.spendMicros)}`;
  }
  async function fetchRunSpend(printed) {
    runState.spendFor = printed;
    if (!sessionId) return;
    try {
      const r = await api(`/api/director/sessions/${sessionId}/receipt`);
      runState.spendMicros = r.totals.actual_usd_micros;
    } catch (err) {
      /* the strip never invents a number */
    }
    const sub = runbar.querySelector(".runspend");
    if (sub) sub.textContent = spendText();
  }
  function startRunTicker() {
    if (!runState.ticker) runState.ticker = setInterval(tickRun, 1000);
  }
  function stopRunTicker() {
    if (runState.ticker) {
      clearInterval(runState.ticker);
      runState.ticker = null;
    }
  }
  function tickRun() {
    const m = activeRun();
    if (!m) { repaintRun(); return; }
    const clock = runbar.querySelector(".runclock");
    if (clock) clock.textContent = elapsedText();
    const eta = runbar.querySelector(".runeta");
    if (eta) {
      eta.textContent = etaText(m);
      const val = eta.parentElement.querySelector(".fval");
      if (val) val.textContent = paceText(m);
    }
    if (runState.hold) {
      if (runState.hold.resume_at <= Date.now() / 1000) { repaintRun(); return; }
      const until = runbar.querySelector(".runhold .holduntil");
      if (until) until.textContent = untilText(runState.hold.resume_at);
    }
  }
  // the pills the run holds, as words — the route decides which ones matter
  function configSummary() {
    const sel = (id) => {
      const e = $(id);
      if (!e) return "";
      const o = e.options ? e.options[e.selectedIndex] : null;
      return (o ? o.textContent : e.value || "").trim();
    };
    const provider = $("provider").value;
    const bits = [sel("provider"), `${Number($("dur-min").value) || 0}m ${Number($("dur-sec").value) || 0}s`, sel("resolution"), sel("aspect"), sel("style")];
    const voice = sel("voice");
    if (voice) bits.push(`voice ${voice}`);
    if (provider !== "grok") {
      bits.push(sel("flow-world") || "Baseline");
      const cam = sel("flow-camera");
      if (cam && cam !== "Authored") bits.push(`cam ${cam}`);
    }
    if (provider !== "google-flow") {
      const cam = sel("grok-camera");
      if (cam && cam !== "Authored") bits.push(`grok cam ${cam}`);
      const atmo = sel("grok-atmosphere");
      if (atmo && atmo !== "None") bits.push(`atmo ${atmo}`);
    }
    const budget = Number($("budget").value) || 0;
    bits.push(budget ? `ceiling $${budget}` : "no ceiling");
    return bits.filter(Boolean);
  }
  if (new URLSearchParams(location.search).has("qa")) {
    // the layout QA harness drives the strip without spending: ?qa=1
    window.dionyRun = {
      begin: runBegin, update: runUpdate, patch: runPatch, end: runEnd, reset: runReset,
      single: { begin: singleBegin, set: singleSet },
      hold(h) { runState.hold = h; repaintRun(); },
      state: runState,
      // card builders, so the harness can paint a failed card without an op
      addOpCard, attachRetry, addMsg,
    };
  }

  async function refreshMeter() {
    // every event that moves money also re-reads the Production card (and
    // restarts its poll when something is in flight again)
    refreshProduction();
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
      // The Flow lexicon (Google route only): worlds fill the style slot,
      // cameras direct one-shot drafts. Custom entries in flow_lexicon.json
      // arrive through the same catalog.
      try {
        const lex = await api("/api/director/flow/lexicon");
        flowLex = lex;
        fillLexSelect($("flow-world"), lex.worlds, lex.default_world || "baseline");
        fillLexSelect($("flow-camera"), lex.cameras, "", "Authored");
      } catch (err) {
        /* static fallbacks in the HTML keep the pills usable */
      }
      try {
        const glex = await api("/api/director/grok/lexicon");
        grokLex = glex;
        fillLexSelect($("grok-camera"), glex.cameras, "", "Authored");
        fillLexSelect($("grok-atmosphere"), glex.atmospheres, "", "None");
      } catch (err) {
        /* static fallbacks in the HTML keep the pills usable */
      }
      // The production styles (Blueprint 31): Narrated (default), Dialogue, Mix.
      try {
        const styles = await api("/api/director/styles");
        fillLexSelect(
          $("style"),
          (styles.styles || []).map((s) => ({ key: s.key, label: s.label, hint: s.hint })),
          styles.default || "narrated",
          null
        );
      } catch (err) {
        /* the static options keep the pill usable */
      }
      // The narrator roster (Blueprint 28): Auto casts per film; a name
      // pins one Grok voice for the whole film.
      try {
        const cat = await api("/api/director/voices");
        fillLexSelect(
          $("voice"),
          (cat.voices || []).map((v) => ({ key: v.key, label: v.label, hint: v.hint })),
          "",
          cat.pinned ? `Auto (site pin: ${cat.pinned})` : "Auto"
        );
      } catch (err) {
        /* the static Auto option keeps the pill usable */
      }
      updateLexiconPillState();
      renderPairings();
      updateTemplateChip();
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
        await syncSessionSettings();
        const bits = [`${resume.completed} printed`];
        if (resume.failed) bits.push(`${resume.failed} failed — each has a Retry on the SHOTS tab`);
        if (resume.in_flight) bits.push(`${resume.in_flight} still rolling`);
        addMsg(
          "director",
          `Resuming session ${resume.session_id}: ${bits.join(", ")}. ` +
            "Retries re-roll at their locked price; assemble from the Production card below when the set is complete. " +
            "＋ NEW SESSION starts fresh — the ledger keeps everything."
        );
        showProductionCard();
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

  let flowLex = null;
  let grokLex = null;
  // The None-means-keep contract lives here: the client asserts a program
  // setting ONLY when the user actually chose it this session. A control at
  // its untouched default sends null (keep) — so a resume where the settings
  // sync silently failed can never revert a stored template or 409 a locked
  // session by posting the static default over it.
  const dirtyControls = new Set();
  function markDirty(id) {
    dirtyControls.add(id);
  }

  async function syncSessionSettings() {
    // A resumed room MUST adopt the session's stored settings before it
    // speaks: the console sends explicit select values, and defaults sent
    // over a stored template would silently clear it — or 409 an innocent
    // message after the lock. (A stored key whose custom lexicon entry has
    // since vanished simply leaves that select where it is.)
    try {
      const s = await api(`/api/director/sessions/${sessionId}/settings`);
      const set = (id, value) => {
        const el = $(id);
        if (el && [...el.options].some((o) => o.value === value)) el.value = value;
      };
      if (s.aspect) set("aspect", s.aspect);
      set("voice", s.voice || "");
      set("style", s.style || "narrated");
      if (s.flow_template) {
        set("flow-world", s.flow_template.world || "baseline");
        set("flow-camera", s.flow_template.camera || "");
      }
      if (s.grok_template) {
        set("grok-camera", s.grok_template.camera || "");
        set("grok-atmosphere", s.grok_template.atmosphere || "");
      }
    } catch (err) {
      /* settings unavailable — the selects keep their defaults */
    }
    updateTemplateChip();
  }

  function updateLexiconPillState() {
    // Each lexicon shapes its own route only — on the other route's explicit
    // lane the pills go quiet so nobody reads them as active controls.
    const provider = $("provider").value;
    document.querySelectorAll(".flowpill").forEach((p) => p.classList.toggle("dim", provider === "grok"));
    document.querySelectorAll(".grokpill").forEach((p) => p.classList.toggle("dim", provider === "google-flow"));
  }

  function fillLexSelect(select, entries, selectedKey, noneLabel) {
    if (!select || !Array.isArray(entries) || !entries.length) return;
    select.replaceChildren();
    if (noneLabel != null) {
      const none = document.createElement("option");
      none.value = "";
      none.textContent = noneLabel;
      select.appendChild(none);
    }
    entries.forEach((e) => {
      const opt = document.createElement("option");
      opt.value = e.key;
      opt.textContent = e.label;
      opt.title = [e.hint, e.clause].filter(Boolean).join(" — ");
      select.appendChild(opt);
    });
    select.value = selectedKey;
  }

  function fieldOrKeep(id) {
    // an untouched control asserts nothing — null tells the server to keep
    // whatever it has stored (None-means-keep)
    const el = $(id);
    if (!el || !dirtyControls.has(id)) return null;
    return el.value;
  }

  function lexiconTemplates() {
    return {
      flow_world: fieldOrKeep("flow-world"),
      flow_camera: fieldOrKeep("flow-camera"),
      grok_camera: fieldOrKeep("grok-camera"),
      grok_atmosphere: fieldOrKeep("grok-atmosphere"),
      voice: fieldOrKeep("voice"),
      style: fieldOrKeep("style"),
    };
  }

  // The production style (Blueprint 31) lands on the session at once, like
  // the voice — a one-shot drafted right after the change must already
  // carry the new voicing. A locked script refuses it, and the room says so.
  const STYLE_NOTES = {
    narrated: "one narrator records every line in post; location tracks stay speechless.",
    dialogue: "the script lines ride each clip's prompt and the characters voice them in the clip; no post narrator.",
    mix: "characters speak on camera in the clip, voice-over is narrated in post; the cut balances, rescues, and mutes per the script.",
  };
  async function pushStyle() {
    const el = $("style");
    if (!el || !sessionId) return;
    try {
      const res = await api(`/api/director/sessions/${sessionId}/style`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ style: el.value }),
      });
      const label = [...el.options].find((o) => o.value === res.style)?.textContent || res.style;
      addMsg("director", `Production style: ${label} — ${STYLE_NOTES[res.style] || ""}`);
    } catch (err) {
      addMsg("director", `Couldn't set the production style: ${err.message || err}`, "warn");
      // the server kept its style — put the pill back where the session is
      syncSessionSettings();
    }
  }

  // The voice is post-recorded, so a choice lands on the session at once —
  // "Assemble completed" after a change must speak with the new voice even
  // when no message follows.
  async function pushVoice() {
    const el = $("voice");
    if (!el || !sessionId) return;
    try {
      const res = await api(`/api/director/sessions/${sessionId}/voice`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ voice: el.value }),
      });
      const label = res.voice
        ? [...el.options].find((o) => o.value === res.voice)?.textContent || res.voice
        : "Auto (the Director casts from the film's tone)";
      addMsg("director", `Narrator voice: ${label}.`);
    } catch (err) {
      addMsg("director", `Couldn't set the voice: ${err.message || err}`, "warn");
    }
  }

  function lexTokensIn(text) {
    // same token grammar as the server: a /word at the start or after
    // whitespace/"("/"+" — never mid-word, so "day/night" prose is safe
    const re = /(?:^|[\s(+])\/([A-Za-z][A-Za-z0-9_-]*)/g;
    const out = [];
    let m;
    while ((m = re.exec(text))) {
      const key = m[1].toLowerCase();
      if (!out.includes(key)) out.push(key);
    }
    return out;
  }

  function lexKnown(lex, families) {
    const known = new Set();
    if (lex) families.forEach((f) => (lex[f] || []).forEach((e) => known.add(e.key)));
    return known;
  }

  function unknownLexTokens(text, route) {
    // route "grok"/"google-flow" checks that route's lexicon; anything else
    // (auto, shot lines) warns only when NEITHER route knows the token —
    // mirroring the server's shot-edit rule. A lexicon that failed to load
    // is never used to judge tokens: with grokLex missing we must not
    // red-flag a valid /pushin the server would happily expand.
    if (!text.includes("/")) return [];
    if (route === "grok") {
      if (!grokLex) return [];
      const grokKnown = lexKnown(grokLex, ["cameras", "atmospheres"]);
      return lexTokensIn(text).filter((t) => !grokKnown.has(t));
    }
    if (route === "google-flow") {
      if (!flowLex) return [];
      const flowKnown = lexKnown(flowLex, ["worlds", "cameras"]);
      return lexTokensIn(text).filter((t) => !flowKnown.has(t));
    }
    // auto / shot-line: flag only tokens unknown to every LOADED lexicon
    if (!flowLex && !grokLex) return [];
    const flowKnown = lexKnown(flowLex, ["worlds", "cameras"]);
    const grokKnown = lexKnown(grokLex, ["cameras", "atmospheres"]);
    return lexTokensIn(text).filter(
      (t) => !(flowLex && flowKnown.has(t)) && !(grokLex && grokKnown.has(t))
    );
  }

  function lintComposer() {
    // catch a /typo BEFORE it rides a paid round-trip as literal text
    const el = $("lexhint");
    if (!el) return;
    const unknown = unknownLexTokens($("idea").value, $("provider").value);
    if (unknown.length) {
      el.textContent = `⚠ ${unknown.map((t) => "/" + t).join(" ")} isn't shorthand on this route — it would ride the prompt as literal text`;
      el.hidden = false;
    } else {
      el.hidden = true;
    }
  }

  function lexLabel(lex, family, key) {
    const entry = lex && (lex[family] || []).find((e) => e.key === key);
    return entry ? entry.label : key;
  }

  function updateTemplateChip() {
    // the session's look, visible without scrollback
    const el = $("lexchip");
    if (!el) return;
    const parts = [];
    const fw = ($("flow-world") && $("flow-world").value) || "";
    const fc = ($("flow-camera") && $("flow-camera").value) || "";
    if ((fw && fw !== "baseline") || fc) {
      const bits = [];
      if (fw && fw !== "baseline") bits.push(lexLabel(flowLex, "worlds", fw));
      if (fc) bits.push(lexLabel(flowLex, "cameras", fc));
      parts.push("Flow: " + bits.join(" · "));
    }
    const gc = ($("grok-camera") && $("grok-camera").value) || "";
    const ga = ($("grok-atmosphere") && $("grok-atmosphere").value) || "";
    if (gc || ga) {
      const bits = [];
      if (gc) bits.push(lexLabel(grokLex, "cameras", gc));
      if (ga) bits.push(lexLabel(grokLex, "atmospheres", ga));
      parts.push("Grok: " + bits.join(" · "));
    }
    el.textContent = parts.join("  —  ");
    el.hidden = !parts.length;
  }

  function renderPairings() {
    // the cheat sheet's curated world+camera stacks — one tap sets both
    // Flow pills; never auto-applied
    const row = $("pairrow");
    if (!row || !flowLex || !Array.isArray(flowLex.pairings)) return;
    row.replaceChildren();
    const tag = document.createElement("span");
    tag.className = "pairlabel";
    tag.textContent = "PAIRINGS";
    row.appendChild(tag);
    flowLex.pairings.forEach((p) => {
      const worldOk = [...$("flow-world").options].some((o) => o.value === p.world);
      const camOk = [...$("flow-camera").options].some((o) => o.value === p.camera);
      if (!worldOk || !camOk) return; // a pairing naming a vanished key renders nothing
      const chip = document.createElement("button");
      chip.type = "button";
      chip.className = "pairchip";
      chip.textContent = `${lexLabel(flowLex, "worlds", p.world)} + ${lexLabel(flowLex, "cameras", p.camera)}`;
      chip.title = `${p.why} — one tap sets both Flow pills`;
      chip.addEventListener("click", () => {
        $("flow-world").value = p.world;
        $("flow-camera").value = p.camera;
        markDirty("flow-world"); // a tapped pairing is a user choice
        markDirty("flow-camera");
        updateTemplateChip();
      });
      row.appendChild(chip);
    });
    row.hidden = row.children.length <= 1;
  }

  // No one-shot cap: a runtime past the route's single-clip length comes back
  // as a chained one-shot — the server splits it and prices every link.

  async function send() {
    const idea = $("idea").value.trim();
    if (!idea || !sessionId) return;
    addMsg("user", withRefSuffix(idea));
    $("idea").value = "";
    runBegin("derive", {
      phase: "deriving", total: 0, title: "Reading the idea",
      detail: `${durationSeconds() || 8}s · compiling and pricing the shot`,
    });
    try {
      const body = await api(`/api/director/sessions/${sessionId}/message`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          text: idea,
          provider: $("provider").value,
          duration_s: durationSeconds() || 8,
          resolution: $("resolution").value,
          aspect: fieldOrKeep("aspect"),
          ...lexiconTemplates(),
        }),
      });
      const li = addPlanMsg(body.plan, body.reply);
      const isChain = body.ops && body.ops.length > 1;
      const chain = isChain ? body.ops : [body.op];
      const cardEls = new Map(); // op_id -> card element
      if (isChain) setupChainBar(li, chain, cardEls);
      chain.forEach((one, index) => {
        // the clip number lives INSIDE the card (addOpCard renders it as the
        // leading chip) — a floating label between cards read as a caption
        // for the card above it, off by one to every eye that saw it
        const card = addOpCard(li, one, isChain ? { index, count: chain.length } : null);
        cardEls.set(one.id, card);
      });
      if (body.op.status === "proposed") {
        // the artifact rides the op state; show it under the priced card —
        // one fetch covers the chain, each card gets its own split segment
        try {
          const state = await api(`/api/director/ops/${body.op.id}`);
          if (state.master) {
            chain.forEach((one, index) => {
              const card = cardEls.get(one.id);
              if (card && !card.querySelector("details.master")) {
                renderMasterFor(card, state.master, isChain ? { index, count: chain.length } : null);
              }
            });
          }
        } catch (err) {
          /* the card stands without the artifact panel */
        }
      }
      // a card or chain still deriving keeps the strip; anything else releases it
      if (body.op.status !== "deriving") runEnd("derive");
    } catch (err) {
      addMsg("director", `I couldn't price that: ${err.message || err}`);
      runEnd("derive");
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

  function attachVerdict(card, state, review, opId, opts) {
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
    if (opts && opts.retake && review.verdict === "fail") {
      // a chain link that printed but failed its look review rolls again —
      // the rejected take stays on the ledger, the chain resumes from it
      const retake = document.createElement("button");
      retake.type = "button";
      retake.className = "retrybtn";
      retake.textContent = "↻ Retake";
      retake.title = "Roll this clip again from the same opening frame — the rejected take stays on the ledger";
      retake.addEventListener("click", async () => {
        retake.disabled = true;
        try {
          await api(`/api/director/ops/${opId}/retry`, { method: "POST" });
          card.querySelectorAll(".verdict, .verdict-issues, .acceptbtn, .skipbtn, video").forEach((el) => el.remove());
          retake.remove();
          state.textContent = "retaking…";
          state.className = "state";
          watchRetried(opId, card, state);
        } catch (err) {
          state.textContent = String(err.message || err);
          retake.disabled = false;
        }
      });
      card.appendChild(retake);
      attachSkip(card, opId, state); // or leave the shot out and move on
    }
  }

  // Skip shot: the way past a clip whose retries keep failing. The shot is
  // discarded — the cut leaves it out — and the run the user chose resumes
  // past it: the later links rejoin and open from the last shot that printed.
  function attachSkip(card, opId, state) {
    if (card.querySelector(".skipbtn")) return;
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "skipbtn";
    btn.textContent = "Skip shot";
    btn.title = "Give up on this shot: the cut leaves it out and the run continues with every other clip — nothing more is spent on it";
    btn.addEventListener("click", () => {
      armConfirm(btn, "Confirm — leave this shot out of the cut", async () => {
        btn.disabled = true;
        try {
          const res = await api(`/api/director/ops/${opId}/skip`, { method: "POST" });
          card.querySelectorAll(".retrybtn, .skipbtn, .acceptbtn, .verdict-issues, video").forEach((el) => el.remove());
          state.textContent = "skipped — the cut leaves this shot out";
          state.className = "state skipped";
          singleSet(opId, "discarded");
          addMsg("director", res.reply);
          refreshMeter();
          resumeRunAfterRetry(res);
        } catch (err) {
          state.textContent = String(err.message || err);
          btn.disabled = false;
        }
      });
    });
    (card.querySelector(".row") || card).appendChild(btn);
  }

  // Failed ops are never dead ends: retry re-proposes at the locked price and
  // rejoins the submit wave — completed clips are never re-spent.
  function attachRetry(card, opId, state) {
    attachSkip(card, opId, state);
    if (card.querySelector(".retrybtn")) return;
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "retrybtn";
    btn.textContent = "↻ Retry";
    btn.title = "Re-roll this clip at its locked price — completed clips are never re-spent";
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      try {
        const res = await api(`/api/director/ops/${opId}/retry`, { method: "POST" });
        btn.remove();
        card.querySelectorAll(".skipbtn").forEach((el) => el.remove());
        state.textContent = res.redraw ? "redrawing the opening frame…" : "retrying…";
        state.className = "state";
        if (res.redraw) addMsg("director", res.redraw.note);
        if (res.retargeted && res.retargeted.length) {
          addMsg(
            "director",
            `${plural(res.retargeted.length, "later clip")} rejoin the run behind this retake — they roll in order once it prints.`
          );
        }
        watchRetried(opId, card, state);
        resumeRunAfterRetry(res);
      } catch (err) {
        state.textContent = String(err.message || err);
        btn.disabled = false;
      }
    });
    const skip = card.querySelector(".skipbtn");
    if (skip) skip.before(btn); else (card.querySelector(".row") || card).appendChild(btn);
  }

  // A Retry continues the run the user already chose — Run & Assemble, or
  // Run, Assemble & Upscale All — without another press. The bar's own loop
  // resumes when this page ran the batch; after a reload the server's run
  // plan drives a generic watch that finishes and cuts the same way.
  function resumeRunAfterRetry(res) {
    if (runContinuation) {
      if (!runContinuation.active) runContinuation.resume();
      return; // an active loop sees the retake in the counts and waits for it
    }
    const plan = res && res.run_plan;
    if (plan && plan.assemble && !planResumeActive) resumeFromPlan(plan);
  }

  async function resumeFromPlan(plan) {
    planResumeActive = true;
    const withFinish = plan.finish === FINISH_RESOLUTION || !!plan.upscale;
    const li = addMsg(
      "director",
      `Resuming the run — the retake rolls, then the film ${withFinish ? `finishes at ${FINISH_RESOLUTION} and ` : ""}cuts as you asked. `
    );
    const progress = document.createElement("span");
    progress.className = "progress";
    li.appendChild(progress);
    const planIds = new Set(plan.op_ids || []);
    const strip = runTracker("plan", plan.op_ids || [], [], {
      title: `Resuming the run${withFinish ? " — finish, then cut" : " — then cut"}`,
    });
    strip.begin();
    const tick = async () => {
      let counts;
      try {
        counts = await api(`/api/director/sessions/${sessionId}/advance`, { method: "POST" });
      } catch (err) {
        progress.textContent = `lost the room: ${err.message || err}`;
        planResumeActive = false;
        strip.end();
        return;
      }
      (counts.held || []).forEach((h) => addHeldMsg(h));
      strip.tick(counts);
      const mine = (counts.ops || []).filter((o) => planIds.has(o.op_id));
      const done = mine.filter((o) => o.status === "completed").length;
      const failed = mine.filter((o) => o.status === "failed").length;
      const pending = mine.filter((o) => o.status === "submitted" || o.status === "submitting").length;
      progress.textContent =
        `${done}/${planIds.size} printed · ${failed} failed · ${pending} generating` +
        ((counts.queued || 0) ? ` · ${counts.queued} queued (chained)` : "");
      refreshMeter();
      if (counts.pending > 0 || (counts.queued || 0) > 0 || counts.submitting) {
        setTimeout(tick, 3000);
        return;
      }
      planResumeActive = false;
      refreshProduction();
      if (failed) {
        progress.textContent += " — the cut waits for the failed clips; each has a Retry on the SHOTS tab";
        strip.end();
        return;
      }
      if (withFinish) {
        progress.textContent += " — finishing…";
        const finished = await upscaleAll(progress);
        if (finished !== true && finished !== "none") {
          progress.textContent += " — some finishes failed; the cut uses the prints that exist";
        }
      }
      progress.textContent += " — assembling…";
      const delivered = await assembleNow(false, { finish: withFinish ? FINISH_RESOLUTION : "auto" });
      progress.textContent = progress.textContent.replace(
        " — assembling…",
        delivered ? " — film delivered" : " — assembly is holding (see the room)"
      );
      strip.end();
    };
    setTimeout(tick, 2500);
  }

  async function watchRetried(opId, card, state, deadlineTicks = 120) {
    if (!runState.single.has(opId)) {
      const num = card.querySelector(".clipnum");
      singleBegin(opId, num ? num.textContent.trim() : "the retake", 0);
    }
    for (let i = 0; i < deadlineTicks; i++) {
      await new Promise((r) => setTimeout(r, 2500));
      let body;
      try {
        body = await api(`/api/director/ops/${opId}`);
      } catch (err) {
        continue;
      }
      const st = body.op.status;
      attachStyleFrame(card, body);
      singleSet(opId, st);
      if (st === "discarded") {
        state.textContent = "skipped — the cut leaves this shot out";
        state.className = "state skipped";
        return;
      }
      if (st === "completed") {
        state.textContent = "printed";
        state.className = "state done";
        if (!card.querySelector("video")) {
          const video = document.createElement("video");
          video.controls = true;
          video.src = `/api/director/clips/${opId}`;
          card.appendChild(video);
        }
        attachVerdict(card, state, body.review, opId, { retake: !body.op.clip_id });
        refreshMeter();
        return;
      }
      if (st === "failed") {
        state.textContent = failedText(body);
        state.className = "state failed";
        attachRetry(card, opId, state);
        refreshMeter();
        return;
      }
      const frame = body.style_frame;
      const redrawing = frame && frame.redraws && !frame.ready && st === "proposed";
      state.textContent = redrawing
        ? "redrawing the opening frame…"
        : st === "proposed" ? "waiting for the wave…" : `${st}…`;
    }
    state.textContent = "still not rolling — check the connection and Retry";
    state.className = "state failed";
    singleSet(opId, "failed");
    attachRetry(card, opId, state);
  }

  function pollProductionOp(op, card, state) {
    const tick = async () => {
      try {
        const body = await api(`/api/director/ops/${op.id}`);
        const status = body.op.status;
        if (status === "discarded") {
          singleSet(op.id, status);
          state.textContent = "skipped — the cut leaves this shot out";
          state.className = "state skipped";
          return;
        }
        if (status === "completed" || status === "failed") {
          singleSet(op.id, status);
          refreshMeter();
          if (status === "completed") {
            // set the text BEFORE the verdict chip lands — textContent wipes
            // children; without this the card reads "generating…" forever
            state.textContent = "printed";
            state.className = "state done";
            const video = document.createElement("video");
            video.controls = true;
            video.src = `/api/director/clips/${op.id}`;
            card.appendChild(video);
          } else {
            // set the text BEFORE the verdict chip lands — textContent wipes children
            state.textContent = failedText(body);
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
        singleSet(op.id, "failed");
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
        <span class="chip">${esc(op.clip_id || op.op)} · ${esc(op.provider)} · ${esc(op.model)}</span>
        <span class="chip">${esc(op.duration_s)}s</span>
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
      singleBegin(op.id, op.clip_id || op.op || "the clip", op.estimate.total_usd_micros);
      try {
        await api(`/api/director/ops/${op.id}/run`, { method: "POST" });
        state.textContent = "generating…";
        singleSet(op.id, "submitted");
        poller(op, card, state);
      } catch (err) {
        state.textContent = String(err.message || err);
        state.className = "state failed";
        btn.disabled = false;
        singleSet(op.id, "failed");
      }
      refreshMeter();
    });
    return card;
  }

  // Blueprint 30: which print of each take the cut uses. "auto" cuts every
  // usable 1080p finish and lifts the raster the moment one exists; "1080p"
  // refuses (naming the takes) while a Flow take still lacks its finish.
  async function assembleNow(allowGaps, opts) {
    const finish = (opts && opts.finish) || "auto";
    const ownStrip = !runState.flow;
    if (ownStrip) runBegin("assemble", { phase: "assembling", total: 0 });
    else runPatch({ phase: "assembling", detail: "", note: "" });
    try {
      const body = allowGaps === true ? { allow_gaps: true } : {};
      if (finish !== "auto") body.finish = finish;
      const film = await api(`/api/director/sessions/${sessionId}/assemble`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const raster = film.raster ? ` at ${film.raster.replace("x", "×")}` : "";
      const skipped = film.skipped && film.skipped.length
        ? ` ${plural(film.skipped.length, "skipped shot")} left out by your call: ${film.skipped.join(", ")}.`
        : "";
      const done = addMsg(
        "director",
        `Assembled ${plural(film.clip_count, "clip")} into a ${film.duration_s.toFixed(1)}s cut${raster}, ${film.loudness_target} loudness, ${film.look} look.${skipped}`
      );
      if (film.finish && film.finish.finished && film.finish.finished.length) {
        let line = `${plural(film.finish.finished.length, "take")} cut from the 1080p finish.`;
        if (film.finish.conformed_up && film.finish.conformed_up.length) {
          line += ` ${plural(film.finish.conformed_up.length, "take")} had no server-side lane and ` +
            `were conformed up from their native print: ${film.finish.conformed_up.join(", ")}.`;
        }
        addMsg("director", line);
      }
      const video = document.createElement("video");
      video.controls = true;
      video.src = `/api/director/films/${sessionId}`;
      done.appendChild(video);
      productionFilm = null; // the Production card loads the new cut
      if (film.narration && film.narration.voice && film.narration.voice.voice_id) {
        const v = film.narration.voice;
        const name = v.voice_id[0].toUpperCase() + v.voice_id.slice(1);
        const hold = film.narration.tail_hold_s ? ` The last frame held ${film.narration.tail_hold_s.toFixed(1)}s so the final line could finish.` : "";
        addMsg("director", `Narrated by ${name} (${v.cast}).${hold}`);
      }
      // Blueprint 31: how the film's lines were voiced — in the clips, by
      // the narrator, rescued — and what the cut balanced or muted
      if (film.style && film.style.key && film.style.key !== "narrated") {
        const st = film.style;
        const label = st.key === "mix" ? "Mix" : "Dialogue";
        const bits = [];
        if (st.spoken_in_clip && st.spoken_in_clip.length) bits.push(`${plural(st.spoken_in_clip.length, "line")} voiced in the clips`);
        if (st.narrated && st.narrated.length) bits.push(`${plural(st.narrated.length, "line")} narrated in post`);
        if (st.rescued && st.rescued.length) bits.push(`${plural(st.rescued.length, "line")} rescued by the narrator (${st.rescued.join(", ")})`);
        const guard = film.speech_guard || {};
        if (guard.balanced && guard.balanced.length) bits.push(`${plural(guard.balanced.length, "spoken track")} balanced to the narrator's level`);
        if (guard.muted && guard.muted.length) bits.push(`${plural(guard.muted.length, "track")} muted (${guard.muted.map((m) => m.clip).join(", ")})`);
        addMsg("director", `${label} style: ${bits.length ? bits.join("; ") : "no spoken lines in this cut"}.`);
      }
      if (film.deliverables && film.deliverables.length) {
        let line = `Delivered with ${plural(film.cue_count || 0, "subtitle cue")}:`;
        if (film.final_check && film.final_check.verdict !== "pass") {
          line += ` Final master check: ${film.final_check.verdict} (${(film.final_check.issues || []).join("; ")}).`;
        }
        const li = addMsg("director", line);
        li.querySelector(".body").appendChild(deliverableLinks(film.deliverables));
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
      refreshProduction();
      return true;
    } catch (err) {
      const text = String(err.message || err);
      const note = addMsg("director", `Assembly isn't ready: ${text}`);
      if (text.includes("finish=auto")) {
        // the strict 1080p cut refused: a finish failed or never rolled —
        // the user may cut the prints that exist, at whatever raster they earn
        const body = note.querySelector(".body");
        const anyway = document.createElement("button");
        anyway.type = "button";
        anyway.className = "batch ghost";
        anyway.textContent = "Assemble with the prints that exist";
        anyway.title = "Cuts every usable 1080p finish; takes without one keep their native print";
        anyway.addEventListener("click", async () => {
          anyway.disabled = true;
          await assembleNow(allowGaps, { finish: "auto" });
        });
        body.appendChild(anyway);
        return false;
      }
      if (text.includes("allow_gaps")) {
        if (ownStrip) runEnd("assemble"); // the retake watch below owns its own
        const body = note.querySelector(".body");
        const retry = document.createElement("button");
        retry.type = "button";
        retry.className = "batch primary";
        retry.textContent = "Retry missing shots";
        const anyway = document.createElement("button");
        anyway.type = "button";
        anyway.className = "batch ghost";
        anyway.textContent = "Assemble anyway (ship with gaps)";
        retry.addEventListener("click", async () => {
          retry.disabled = true;
          anyway.disabled = true;
          try {
            const res = await api(`/api/director/sessions/${sessionId}/run_retakes`, {
              method: "POST",
            });
            (res.held || []).forEach((h) => addHeldMsg(h));
            if (!res.targeted.length) {
              addMsg(
                "director",
                res.undrafted && res.undrafted.length
                  ? `No retake is drafted for ${res.undrafted.join(", ")} — redraft from the Production card, then assemble again.`
                  : "Nothing to retry — the missing shots may already be rolling; assemble again once they settle."
              );
              retry.disabled = false;
              anyway.disabled = false;
              return;
            }
            const rolling = addMsg(
              "director",
              `Rolling ${plural(res.missing.length, "missing shot")} (${res.missing.join(", ")}) — I'll cut the film when they print.`
            );
            // The retake watch is real work the user can't otherwise see:
            // a live line on the bubble tracks the targeted set every tick
            // so "Retry missing shots" never reads as a dead button.
            const targeted = new Set(res.targeted);
            const progress = document.createElement("div");
            progress.className = "draft-status";
            progress.textContent = `retakes rolling — 0/${targeted.size} printed`;
            rolling.querySelector(".body").appendChild(progress);
            const strip = runTracker("retakes", res.targeted, res.missing || [], { title: RUN_PHASES.retaking.title });
            strip.begin({ phase: "retaking" });
            let lastPrinted = -1;
            const tick = async () => {
              let counts;
              try {
                counts = await api(`/api/director/sessions/${sessionId}/advance`, { method: "POST" });
              } catch (e2) {
                progress.textContent = `retake watch lost the room: ${e2.message || e2} — hit Retry again`;
                progress.className = "draft-status failed";
                retry.disabled = false;
                anyway.disabled = false;
                strip.end();
                return;
              }
              (counts.held || []).forEach((h) => addHeldMsg(h));
              strip.tick(counts, { phase: "retaking" });
              const mine = (counts.ops || []).filter((o) => targeted.has(o.op_id));
              const printed = mine.filter((o) => o.status === "completed").length;
              const failed = mine.filter((o) => o.status === "failed").length;
              const inFlight = mine.filter((o) =>
                o.status === "submitted" || o.status === "submitting"
              ).length;
              const waiting = targeted.size - printed - failed - inFlight;
              const parts = [`${printed}/${targeted.size} printed`];
              if (inFlight) parts.push(`${inFlight} rolling`);
              if (waiting > 0) parts.push(`${waiting} queued`);
              if (failed) parts.push(`${failed} failed`);
              progress.textContent = `retakes — ${parts.join(" · ")}`;
              if (printed !== lastPrinted) {
                lastPrinted = printed;
                refreshProduction(); // the Production card tracks each print as it lands
              }
              if (counts.pending > 0 || (counts.queued || 0) > 0 || counts.submitting) {
                setTimeout(tick, 3000);
                return;
              }
              if (failed) {
                progress.textContent =
                  `retakes settled — ${printed}/${targeted.size} printed, ${failed} failed ` +
                  "(reasons on the cards)";
                progress.className = "draft-status failed";
              } else {
                progress.textContent = `all ${targeted.size} printed — cutting the film`;
                progress.classList.add("done");
              }
              refreshMeter();
              refreshProduction();
              await assembleNow();
              strip.end();
            };
            setTimeout(tick, 2500);
          } catch (e1) {
            addMsg("director", `Couldn't roll the retakes: ${e1.message || e1}`);
            retry.disabled = false;
            anyway.disabled = false;
          }
        });
        anyway.addEventListener("click", async () => {
          retry.disabled = true;
          anyway.disabled = true;
          await assembleNow(true);
        });
        body.appendChild(retry);
        body.appendChild(anyway);
      }
      return false;
    } finally {
      if (ownStrip) runEnd("assemble");
    }
  }

  // --- Blueprint 30: the 1080p finish -------------------------------------
  //
  // One server-side upscale per printed Flow take, priced from the rate card
  // before anything rolls, watched on the bar like the takes themselves, and
  // then the cut at 1920×1080. Grok takes have no server-side lane: they are
  // named, and the cut conforms them up locally.
  const FINISH_RESOLUTION = "1080p";
  let finishRate = null; // {usd_micros, credits} once fetched
  async function loadFinishRate() {
    if (finishRate) return finishRate;
    try {
      finishRate = await api(`/api/director/upscale/rate?resolution=${FINISH_RESOLUTION}`);
    } catch (err) {
      finishRate = { usd_micros: 0, credits: 0, unavailable: String(err.message || err) };
    }
    return finishRate;
  }
  function finishableCount(ops) {
    return ops.filter((op) => op.provider === "google-flow").length;
  }
  function finishCost(ops) {
    return finishableCount(ops) * ((finishRate && finishRate.usd_micros) || 0);
  }
  function finishLabel(ops, runCost, suffix) {
    const n = finishableCount(ops);
    return `Run, Assemble & Upscale All (${ops.length} + ${n}×${FINISH_RESOLUTION}${suffix || ""}) — ${usd(runCost + finishCost(ops))}`;
  }

  // Finish every printed Flow take, then report on `progress` until the
  // finishes settle. Resolves true when every targeted finish printed.
  async function upscaleAll(progress) {
    let up;
    try {
      up = await api(`/api/director/sessions/${sessionId}/upscale_all`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ resolution: FINISH_RESOLUTION }),
      });
    } catch (err) {
      addMsg("director", `Couldn't start the ${FINISH_RESOLUTION} finish: ${err.message || err}`, "warn");
      return false;
    }
    (up.held || []).forEach((h) => addHeldMsg(h));
    if (up.skipped && up.skipped.length) {
      addMsg(
        "director",
        `${plural(up.skipped.length, "take")} keep their native print — no server-side upscale lane ` +
          `(${up.skipped.map((s) => s.clip_id).join(", ")}); the cut conforms them to the 1080 raster.`
      );
    }
    if (!up.targeted.length) {
      if (up.already.length) {
        addMsg("director", `Every Flow take already carries its ${FINISH_RESOLUTION} finish (${up.already.length}).`);
        return true;
      }
      if (up.rolling.length) {
        addMsg("director", `Finishes are already rolling for ${up.rolling.join(", ")} — waiting on them.`);
      } else {
        addMsg("director", "Nothing to finish — no printed Flow take in the cut.");
        return "none";
      }
    } else {
      addMsg(
        "director",
        `Finishing ${plural(up.targeted.length, "Flow take")} at ${FINISH_RESOLUTION} — ${usd(up.est_usd_micros)}. ` +
          "Finishes roll a few at a time (the provider throttles parallel requests); the cut follows."
      );
    }
    const targeted = new Set(up.targeted);
    const finishIds = [...targeted];
    runPatch({
      phase: "finishing", note: "",
      ...countSegments(finishIds.map((id, i) => ({ status: "proposed", label: `finish ${i + 1}` }))),
      queuedLabel: "Waiting",
      detail: finishIds.length ? "" : `waiting on ${plural(up.rolling.length, "finish")} already rolling`,
    });
    return new Promise((resolve) => {
      const tick = async () => {
        let counts;
        try {
          counts = await api(`/api/director/sessions/${sessionId}/advance`, { method: "POST" });
        } catch (err) {
          progress.textContent = `lost the room during the finish: ${err.message || err}`;
          resolve(false);
          return;
        }
        (counts.held || []).forEach((h) => addHeldMsg(h));
        if (finishIds.length) {
          const byId = new Map((counts.ops || []).map((o) => [o.op_id, o]));
          const labels = finishIds.map((id, i) => {
            const o = byId.get(id);
            return o && o.clip_id ? `${o.clip_id} ${FINISH_RESOLUTION}` : `finish ${i + 1}`;
          });
          runPatch({
            phase: "finishing", queuedLabel: "Waiting",
            ...countSegments(segmentsFor(finishIds, labels, counts.ops)),
            note: (counts.stalled || []).length ? `stalled — ${counts.stalled[0].reason}` : "",
          });
        }
        const mine = (counts.ops || []).filter((o) => targeted.has(o.op_id));
        const printed = mine.filter((o) => o.status === "completed").length;
        const failed = mine.filter((o) => o.status === "failed").length;
        const total = targeted.size || up.rolling.length || 0;
        progress.textContent =
          `finishing at ${FINISH_RESOLUTION} — ${printed}/${total} printed` +
          (failed ? ` · ${failed} failed` : "") +
          ((counts.stalled || []).length ? ` · stalled: ${counts.stalled[0].reason}` : "");
        refreshMeter();
        if (counts.pending > 0 || (counts.queued || 0) > 0 || counts.submitting) {
          setTimeout(tick, 3000);
          return;
        }
        refreshProduction(); // the Production card shows each take's finish chip
        if (failed) {
          addMsg(
            "director",
            `${plural(failed, "finish")} failed — the takes' own prints stay in the cut. ` +
              "Retry the finish on the Production card, or assemble with the prints that exist.",
            "warn"
          );
        }
        resolve(failed === 0);
      };
      setTimeout(tick, 2500);
    });
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
      <button type="button" class="batch primary finish" id="run-all-up" title="Roll every clip, finish each printed Flow take at 1080p server-side, then cut the film at 1920×1080"></button>
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
    const upBtn = bar.querySelector("#run-all-up");
    const selBtn = bar.querySelector("#run-sel");
    const asmBtn = bar.querySelector("#assemble-only");
    const selAll = bar.querySelector("#selall-box");
    const progress = bar.querySelector("#batch-progress");
    loadFinishRate().then(refreshBatchLabels);

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
      upBtn.textContent = finishLabel(ops, sumFor(allIds) + bibleCost, bibleSuffix);
      upBtn.disabled = allBtn.disabled || finishableCount(ops) === 0;
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

    async function runAndAssemble(ids, opts) {
      const withFinish = !!(opts && opts.upscale);
      allBtn.disabled = true; selBtn.disabled = true; upBtn.disabled = true;
      progress.textContent = "submitting…";
      const labelOf = new Map(ops.map((op, i) => [op.id, op.clip_id || `clip ${i + 1}`]));
      const strip = runTracker("batch", ids, ids.map((id) => labelOf.get(id)), {
        title: withFinish ? "Rolling the clips, then finish and cut" : "Rolling the clips, then the cut",
        estMicros: sumFor(ids) + bibleCost + (withFinish ? finishCost(ops) : 0),
      });
      strip.begin();
      let batch;
      try {
        batch = await api(`/api/director/sessions/${sessionId}/run_batch`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            op_ids: ids,
            assemble: true,
            finish: withFinish ? FINISH_RESOLUTION : "auto",
            upscale: withFinish,
          }),
        });
      } catch (err) {
        progress.textContent = `batch failed: ${err.message || err}`;
        allBtn.disabled = false; refreshBatchLabels();
        strip.end();
        return;
      }
      ids.forEach((id) => {
        const c = cards.get(id);
        if (c) { c.state.textContent = "queued…"; c.btn.disabled = true; }
      });
      const cutFilm = async () => {
        if (withFinish) {
          progress.textContent += " — finishing…";
          const finished = await upscaleAll(progress);
          progress.textContent = progress.textContent.replace(
            " — finishing…",
            finished === true
              ? ` — finished at ${FINISH_RESOLUTION}`
              : finished === "none" ? " — nothing to finish" : " — some finishes failed"
          );
        }
        progress.textContent += " — assembling…";
        const delivered = await assembleNow(false, { finish: withFinish ? FINISH_RESOLUTION : "auto" });
        progress.textContent = progress.textContent.replace(
          " — assembling…",
          delivered ? " — film delivered" : " — assembly is holding (see the room)"
        );
        allBtn.disabled = false; refreshBatchLabels();
        strip.end();
      };
      batch.held.forEach((h) => {
        const c = cards.get(h.op_id);
        if (c) { c.state.textContent = h.reason; c.state.className = "state failed"; }
      });
      if (batch.held.length) {
        addMsg("director", `${plural(batch.held.length, "clip")} held: ${batch.held[0].reason}`);
      }
      if (!(batch.queued || 0) && !batch.submitting && !batch.submitted.length) {
        if (withFinish && batch.skipped) {
          // nothing left to roll (every clip already printed): still finish and cut
          progress.textContent = "every clip already printed";
          await cutFilm();
          return;
        }
        progress.textContent = "nothing submitted";
        allBtn.disabled = false; refreshBatchLabels();
        strip.end();
        return;
      }

      const tick = async () => {
        let counts;
        try {
          counts = await api(`/api/director/sessions/${sessionId}/advance`, { method: "POST" });
        } catch (err) {
          progress.textContent = `lost the room: ${err.message || err}`;
          runContinuation.active = false;
          strip.end();
          return;
        }
        const queued = counts.queued || 0;
        const stalled = (counts.stalled || []).length;
        // Hold reasons are drained exactly once — a budget hold or a held
        // scene must reach the room, not vanish into an unread field.
        (counts.held || []).forEach((h) => addHeldMsg(h));
        strip.tick(counts);
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
        runContinuation.active = false; // a Retry from here re-enters this loop
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
              c.state.textContent = failedText(body);
              c.state.className = "state failed";
              attachRetry(c.card, id, c.state);
              if (body.narration) addMsg("director", body.narration);
            } else if (body.op.status === "discarded") {
              c.state.textContent = "skipped — the cut leaves this shot out";
              c.state.className = "state skipped";
            }
            attachVerdict(c.card, c.state, body.review, id);
          } catch (err) { /* card stays coarse; the film still cuts */ }
        }
        await cutFilm();
      };
      runContinuation = {
        active: true,
        resume() {
          this.active = true;
          allBtn.disabled = true; selBtn.disabled = true; upBtn.disabled = true;
          progress.textContent = "resuming the run — the retake rolls, then the film cuts…";
          strip.begin({ title: "Resuming the run — the retake rolls, then the cut" });
          setTimeout(tick, 2500);
        },
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
    upBtn.addEventListener("click", () => {
      const ids = [...cards.keys()];
      const n = finishableCount(ops);
      armConfirm(
        upBtn,
        `Confirm — roll ${plural(ids.length, "clip")}${bibleSuffix}, finish ${n} at ${FINISH_RESOLUTION}, ` +
          `cut at 1080 — ${usd(sumFor(ids) + bibleCost + finishCost(ops))} total`,
        () => runAndAssemble(ids, { upscale: true })
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

  // The chained one-shot's batch bar: one button rolls every link in order —
  // the submitter waves bridge each link from its predecessor's final frame —
  // and cuts the film when the last one prints. Cards keep their individual
  // Run buttons for link-by-link control.
  function setupChainBar(host, ops, cardEls) {
    const bar = document.createElement("div");
    bar.className = "batchbar";
    const total = ops.reduce(
      (sum, op) => sum + (op.estimate ? op.estimate.total_usd_micros : 0), 0);
    bar.innerHTML = `
      <button type="button" class="batch primary" id="chain-run-all">Run &amp; Assemble All (${ops.length}) — ${usd(total)}</button>
      <button type="button" class="batch primary finish" id="chain-run-all-up" title="Roll every link in order, finish each printed Flow link at 1080p server-side, then cut the film at 1920×1080"></button>
      <button type="button" class="batch ghost" id="chain-assemble">Assemble completed</button>
      <span class="progress" id="chain-progress"></span>`;
    host.appendChild(bar);
    const allBtn = bar.querySelector("#chain-run-all");
    const upBtn = bar.querySelector("#chain-run-all-up");
    const asmBtn = bar.querySelector("#chain-assemble");
    const progress = bar.querySelector("#chain-progress");
    asmBtn.addEventListener("click", () => assembleNow());
    const refreshUpLabel = () => {
      upBtn.textContent = finishLabel(ops, total);
      upBtn.disabled = allBtn.disabled || finishableCount(ops) === 0;
    };
    refreshUpLabel();
    loadFinishRate().then(refreshUpLabel);

    // Nothing can roll before the master's split lands on every link. ONE
    // derivation covers the chain, so ONE ticker lives here on the bar —
    // never duplicated per card — and unlocks every card when it lands.
    // A chain whose derivation failed is HELD: Run All stays off, the bar
    // names the failure, and one Retry re-derives every link in place.
    const holdChain = (op) => {
      allBtn.disabled = true;
      upBtn.disabled = true;
      progress.textContent = `held — ${derivationNote(op)} `;
      const retry = document.createElement("button");
      retry.type = "button";
      retry.className = "batch ghost rederive";
      retry.textContent = "Retry derivation";
      retry.addEventListener("click", async () => {
        retry.disabled = true;
        try {
          await api(`/api/director/ops/${ops[0].id}/derive`, { method: "POST" });
        } catch (err) {
          progress.textContent = `retry failed: ${err.message || err} `;
          progress.appendChild(retry);
          retry.disabled = false;
          return;
        }
        ops.forEach((o) => {
          o.status = "deriving";
          const card = cardEls.get(o.id);
          if (!card) return;
          const st = card.querySelector(".state");
          st.textContent = "deriving";
          st.className = "state deriving";
          const b = card.querySelector("button");
          if (b) b.disabled = true;
        });
        gateOnDerivation();
      });
      progress.appendChild(retry);
    };
    const gateOnDerivation = () => {
      allBtn.disabled = true;
      upBtn.disabled = true;
      const started = Date.now();
      progress.textContent = `deriving the master prompt + ${ops.length}-clip split…`;
      runBegin("derive", {
        phase: "deriving", total: 0,
        detail: `${ops.length}-clip split · ${ops[0].provider} · ${ops[0].model}`,
      });
      const gate = async () => {
        let body;
        try {
          body = await api(`/api/director/ops/${ops[0].id}`);
        } catch (err) {
          setTimeout(gate, 3000);
          return;
        }
        if (body.op.status === "deriving") {
          progress.textContent =
            `deriving the master prompt + ${ops.length}-clip split… ` +
            `${Math.round((Date.now() - started) / 1000)}s`;
          setTimeout(gate, 3000);
          return;
        }
        for (const [index, op] of ops.entries()) {
          const card = cardEls.get(op.id);
          if (!card) continue;
          let one = body;
          if (index > 0) {
            try {
              one = await api(`/api/director/ops/${op.id}`);
            } catch (err) {
              continue; // this card unlocks on its next interaction
            }
          }
          landDerivedCard(op, card, one, { index, count: ops.length });
        }
        runEnd("derive");
        if (body.op.prompt_version === "v0-provisional") {
          holdChain(body.op);
          return;
        }
        allBtn.disabled = false;
        refreshUpLabel();
        progress.textContent = "";
      };
      setTimeout(gate, 2500);
    };
    if (ops[0].status === "deriving") gateOnDerivation();
    else if (ops[0].prompt_version === "v0-provisional" && ops[0].status === "proposed") {
      holdChain(ops[0]);
    }

    const parts = (id) => {
      const card = cardEls.get(id);
      return card
        ? { card, state: card.querySelector(".state"), btn: card.querySelector("button") }
        : null;
    };

    async function runAll(opts) {
      const withFinish = !!(opts && opts.upscale);
      allBtn.disabled = true;
      upBtn.disabled = true;
      progress.textContent = "submitting…";
      const chainIds = ops.map((op) => op.id);
      const strip = runTracker("chain", chainIds, chainIds.map((id, i) => `clip ${i + 1} / ${ops.length}`), {
        title: withFinish ? "Rolling the chain in order, then finish and cut" : "Rolling the chain in order, then the cut",
        estMicros: total + (withFinish ? finishCost(ops) : 0),
      });
      strip.begin();
      let batch;
      try {
        batch = await api(`/api/director/sessions/${sessionId}/run_batch`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            op_ids: ops.map((op) => op.id),
            assemble: true,
            finish: withFinish ? FINISH_RESOLUTION : "auto",
            upscale: withFinish,
          }),
        });
      } catch (err) {
        progress.textContent = `batch failed: ${err.message || err}`;
        allBtn.disabled = false;
        refreshUpLabel();
        strip.end();
        return;
      }
      ops.forEach((op) => {
        const c = parts(op.id);
        if (c && !c.card.querySelector("video")) {
          c.state.textContent = "queued…";
          if (c.btn) c.btn.disabled = true;
        }
      });
      (batch.held || []).forEach((h) => addHeldMsg(h));
      const tick = async () => {
        let counts;
        try {
          counts = await api(`/api/director/sessions/${sessionId}/advance`, { method: "POST" });
        } catch (err) {
          progress.textContent = `lost the room: ${err.message || err}`;
          allBtn.disabled = false;
          runContinuation.active = false;
          strip.end();
          return;
        }
        (counts.held || []).forEach((h) => addHeldMsg(h));
        strip.tick(counts);
        const queued = counts.queued || 0;
        progress.textContent =
          `${counts.completed} done · ${counts.failed} failed · ${counts.pending} generating` +
          (queued ? ` · ${queued} queued (chained)` : "");
        refreshMeter();
        if (counts.pending > 0 || queued > 0 || counts.submitting) {
          setTimeout(tick, 3000);
          return;
        }
        runContinuation.active = false; // a Retry from here re-enters this loop
        // settle each card with its final state, then cut the film
        const lookHeld = [];
        for (const [index, op] of ops.entries()) {
          const c = parts(op.id);
          if (!c) continue;
          try {
            const body = await api(`/api/director/ops/${op.id}`);
            attachStyleFrame(c.card, body);
            if (body.op.status === "completed") {
              c.state.textContent = "printed";
              c.state.className = "state done";
              if (!c.card.querySelector("video")) {
                const video = document.createElement("video");
                video.controls = true;
                video.src = `/api/director/clips/${op.id}`;
                c.card.appendChild(video);
              }
              if (body.review && body.review.verdict === "fail") lookHeld.push(index + 1);
            } else if (body.op.status === "failed") {
              c.state.textContent = failedText(body);
              c.state.className = "state failed";
              attachRetry(c.card, op.id, c.state);
            } else if (body.op.status === "discarded") {
              c.state.textContent = "skipped — the cut leaves this shot out";
              c.state.className = "state skipped";
            }
            attachVerdict(c.card, c.state, body.review, op.id, { retake: true });
          } catch (err) { /* card stays coarse; the film still cuts */ }
        }
        if (lookHeld.length) {
          // a link that printed but failed its look review holds the chain:
          // the next link would open from a frame the film rejects
          progress.textContent += " — chain holding on a look review";
          addMsg(
            "director",
            `Clip ${lookHeld[0]} printed but failed its look review — hit ↻ Retake on its card ` +
              "(or Accept anyway). The run resumes from there on its own and cuts the film as you asked.",
            "warn"
          );
          allBtn.disabled = false;
          refreshUpLabel();
          strip.end();
          return;
        }
        if (counts.failed > 0) {
          // a chain with a hole is not a film — never auto-cut over it
          progress.textContent += " — chain stopped";
          addMsg(
            "director",
            `${plural(counts.failed, "clip")} failed — hit ↻ Retry on the failed card ` +
              "(its reason is printed there). A clip refused for its opening frame gets a fresh " +
              "frame redrawn; the run resumes from the break on its own and cuts the film as you asked. " +
              "If the retries keep failing, Skip shot leaves it out and the run continues with every other clip. " +
              "Printed clips are never re-spent.",
            "warn"
          );
          allBtn.disabled = false;
          refreshUpLabel();
          strip.end();
          return;
        }
        if (withFinish) {
          progress.textContent += " — finishing…";
          const finished = await upscaleAll(progress);
          progress.textContent = progress.textContent.replace(
            " — finishing…",
            finished === true
              ? ` — finished at ${FINISH_RESOLUTION}`
              : finished === "none" ? " — nothing to finish" : " — some finishes failed"
          );
        }
        progress.textContent += " — assembling…";
        const delivered = await assembleNow(false, { finish: withFinish ? FINISH_RESOLUTION : "auto" });
        progress.textContent = progress.textContent.replace(
          " — assembling…",
          delivered ? " — film delivered" : " — assembly is holding (see the room)"
        );
        allBtn.disabled = false;
        refreshUpLabel();
        strip.end();
      };
      runContinuation = {
        active: true,
        resume() {
          this.active = true;
          allBtn.disabled = true;
          upBtn.disabled = true;
          progress.textContent = "resuming the run — the retake rolls, the chain follows, then the film cuts…";
          strip.begin({ title: "Resuming the chain — the retake rolls, the chain follows, then the cut" });
          setTimeout(tick, 2500);
        },
      };
      setTimeout(tick, 2500);
    }

    allBtn.addEventListener("click", () => {
      armConfirm(
        allBtn,
        `Confirm — roll ${plural(ops.length, "clip")} in order, ${usd(total)} total`,
        () => runAll()
      );
    });
    upBtn.addEventListener("click", () => {
      armConfirm(
        upBtn,
        `Confirm — roll ${plural(ops.length, "clip")} in order, finish ${finishableCount(ops)} at ` +
          `${FINISH_RESOLUTION}, cut at 1080 — ${usd(total + finishCost(ops))} total`,
        () => runAll({ upscale: true })
      );
    });
  }

  function renderDraft(li, state) {
    li.querySelector(".body").textContent = state.reply;
    if (state.warnings && state.warnings.length) {
      const warn = document.createElement("div");
      warn.className = "draft-warn";
      warn.textContent = state.warnings.join(" · ");
      li.appendChild(warn);
    }
    if (state.master) renderMaster(li, state.master);
    attachScriptSheet(li, state);
    attachLockButton(li);
  }

  // The script sheet lives IN the draft card: beats, every line and its
  // authored shot — editable until the lock, read-only after it. The static
  // preview stands in only when the sheet cannot be fetched.
  async function attachScriptSheet(li, state) {
    const box = document.createElement("div");
    box.className = "sheetcard scriptsheet";
    li.appendChild(box);
    try {
      renderScriptSheet(box, await fetchBoard());
    } catch (err) {
      box.remove();
      renderScript(li, state.script);
    }
  }

  async function refreshScriptSheets() {
    const sheets = document.querySelectorAll(".scriptsheet");
    if (!sheets.length || !sessionId) return;
    try {
      const b = await fetchBoard();
      sheets.forEach((box) => {
        if (!box.querySelector("input")) renderScriptSheet(box, b);
      });
    } catch (err) {
      /* the sheet keeps its last paint */
    }
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
          aspect: fieldOrKeep("aspect"),
          ...lexiconTemplates(),
        }),
      });
      if (maxUsd > 0) addMsg("director", `Budget ceiling set: $${maxUsd.toFixed(2)} for this session — anything past it holds.`);
      const li = addMsg("director", body.reply);
      if (body.status === "drafting") {
        const started = Date.now();
        const status = document.createElement("div");
        status.className = "draft-status";
        li.appendChild(status);
        let stage = "deriving";
        const runtime = durationSeconds() || 60;
        runBegin("draft", {
          phase: "deriving", total: 0,
          detail: `${Math.floor(runtime / 60)}m ${runtime % 60}s of screen time from the brief`,
        });
        const tick = async () => {
          const elapsed = Math.round((Date.now() - started) / 1000);
          status.textContent =
            stage === "writing"
              ? `master prompt derived · writing the screenplay from it… ${elapsed}s`
              : `deriving the master prompt… ${elapsed}s`;
          let state;
          try {
            state = await api(`/api/director/sessions/${sessionId}/draft`);
          } catch (err) {
            setTimeout(tick, 3000);
            return;
          }
          if (state.stage) stage = state.stage;
          runUpdate("draft", { phase: stage === "writing" ? "drafting" : "deriving" });
          if (state.status === "ready") {
            status.remove();
            renderDraft(li, state);
            $("produce").disabled = false;
            runEnd("draft");
            return;
          }
          if (state.status === "failed") {
            status.textContent = `draft failed: ${state.error} — hit Draft script to let the brain try again`;
            status.classList.add("failed");
            $("produce").disabled = false;
            runEnd("draft");
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
      runEnd("draft");
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
          refreshScriptSheets(); // the sheet turns read-only
          showProductionCard(); // bibles (the face gate), shots, and the cut, live
        } catch (err) {
          addMsg("director", `Couldn't lock: ${err.message || err}`);
          lockBtn.disabled = false;
        }
      });
    }
  }

  // --- Production sheets, in the flow of the room ---------------------------
  // The former side board. The script sheet renders inside the draft card;
  // bibles, shots, and the cut render as ONE live Production card that
  // refreshes itself every few seconds while anything is still rolling
  // (a bible still, a take, a finish) and goes quiet when the set settles.

  function esc(text) {
    const box = document.createElement("div");
    box.textContent = text == null ? "" : String(text);
    return box.innerHTML;
  }

  function fetchBoard() {
    return api(`/api/director/sessions/${sessionId}/board`);
  }

  function deliverableLinks(names) {
    const list = document.createElement("div");
    list.className = "bdeliv";
    names.forEach((n, index) => {
      const a = document.createElement("a");
      a.href = `/api/director/sessions/${sessionId}/deliverables/${n}`;
      a.target = "_blank";
      a.rel = "noopener";
      a.textContent = n;
      list.appendChild(a);
      if (index < names.length - 1) list.appendChild(document.createTextNode(" · "));
    });
    return list;
  }

  let productionCard = null;
  let productionTimer = null;
  let productionInFlight = false;
  // the cut's player survives repaints: the node is MOVED into each new
  // paint, so a poll never restarts playback; a new cut drops it
  let productionFilm = null;

  // in flight = money is moving; "proposed" is drafted and waits for a Run
  const IN_FLIGHT = new Set(["submitting", "submitted", "deriving"]);
  function productionLive(b) {
    return (b.clips || []).some(
      (c) => IN_FLIGHT.has(c.status) || (c.finish && IN_FLIGHT.has(c.finish.status))
    );
  }

  function startProductionPoll() {
    if (!productionTimer) productionTimer = setInterval(refreshProduction, 5000);
  }
  function stopProductionPoll() {
    if (productionTimer) {
      clearInterval(productionTimer);
      productionTimer = null;
    }
  }

  async function refreshProduction() {
    if (!productionCard || !sessionId || productionInFlight) return;
    if (!productionCard.isConnected) {
      stopProductionPoll();
      productionCard = null;
      return;
    }
    productionInFlight = true;
    try {
      const b = await fetchBoard();
      paintProduction(productionCard, b);
      const live = productionLive(b);
      runState.board = live ? modelFromBoard(b) : null;
      repaintRun();
      if (live) startProductionPoll();
      else stopProductionPoll();
    } catch (err) {
      const note = productionCard.querySelector(".prodnote");
      if (note) note.textContent = `status unavailable: ${err.message || err}`;
    } finally {
      productionInFlight = false;
    }
  }

  function paintProduction(li, b) {
    const body = li.querySelector(".body");
    body.replaceChildren();
    const head = document.createElement("div");
    head.className = "prodhead";
    const title = document.createElement("span");
    title.className = "bhead";
    title.textContent = "PRODUCTION";
    head.appendChild(title);
    const note = document.createElement("span");
    note.className = "prodnote";
    const clips = b.clips || [];
    if (clips.length) {
      const printed = clips.filter((c) => c.status === "completed").length;
      const failed = clips.filter((c) => c.status === "failed").length;
      const rolling = clips.filter((c) => IN_FLIGHT.has(c.status)).length;
      const drafted = clips.filter((c) => c.status === "proposed").length;
      note.textContent =
        `${printed}/${clips.length} printed` +
        (failed ? ` · ${failed} failed` : "") +
        (rolling ? ` · ${rolling} rolling` : "") +
        (drafted ? ` · ${drafted} drafted` : "") +
        (b.film_ready ? " · film cut" : "");
    } else {
      note.textContent = b.locked ? "locked" : "not locked yet";
    }
    head.appendChild(note);
    const refresh = document.createElement("button");
    refresh.type = "button";
    refresh.className = "retrybtn";
    refresh.textContent = "↻ Refresh";
    refresh.title = "Re-read the production state now — it refreshes itself while anything rolls";
    refresh.addEventListener("click", refreshProduction);
    head.appendChild(refresh);
    body.appendChild(head);

    const section = (label, paint) => {
      const sec = document.createElement("div");
      sec.className = "prodsection";
      const h = document.createElement("span");
      h.className = "bhead";
      h.textContent = label;
      sec.appendChild(h);
      const pane = document.createElement("div");
      paint(pane, b);
      sec.appendChild(pane);
      body.appendChild(sec);
    };
    if ((b.characters || []).length || (b.settings || []).length) section("BIBLES", renderBiblesSheet);
    if (clips.length) section("SHOTS", renderShotsSheet);
    section("CUT", renderCutSheet);
  }

  // One Production card per room: a second call re-paints and scrolls to it.
  function showProductionCard() {
    if (!sessionId) return null;
    if (productionCard && productionCard.isConnected) {
      productionCard.scrollIntoView({ block: "end" });
      refreshProduction();
      return productionCard;
    }
    const li = document.createElement("li");
    li.className = "msg director production";
    li.innerHTML = `<div class="who">Director</div><div class="body sheetcard"></div>`;
    li.querySelector(".body").textContent = "reading the production…";
    thread.appendChild(li);
    li.scrollIntoView({ block: "end" });
    productionCard = li;
    refreshProduction();
    return li;
  }

  function renderScriptSheet(pane, b) {
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
    if (b.master) {
      const head = document.createElement("div");
      head.className = "bhead";
      head.textContent = "MASTER PROMPT";
      pane.appendChild(head);
      const box = document.createElement("div");
      box.className = "bmaster";
      renderMaster(box, b.master, { open: false });
      pane.appendChild(box);
    }
    const note = document.createElement("div");
    note.className = "bnote";
    note.textContent = b.locked
      ? "Script is locked — the money pass runs from it."
      : "Click a line to rewrite it. Headings hold the structure.";
    pane.appendChild(note);
    if (!b.locked && flowLex) {
      const legend = document.createElement("div");
      legend.className = "bnote";
      legend.textContent =
        "⌖ shot shorthand (Google route) — cameras: " +
        flowLex.cameras.map((c) => "/" + c.key).join(" ") +
        " · styles: " +
        flowLex.worlds.filter((w) => w.key !== "baseline").map((w) => "/" + w.key).join(" ");
      pane.appendChild(legend);
    }
    if (!b.locked && grokLex) {
      const legend = document.createElement("div");
      legend.className = "bnote";
      legend.textContent =
        "⌖ shot shorthand (Grok route) — cameras: " +
        grokLex.cameras.map((c) => "/" + c.key).join(" ") +
        " · atmosphere: " +
        grokLex.atmospheres.map((a) => "/" + a.key).join(" ");
      pane.appendChild(legend);
    }
    const list = document.createElement("div");
    list.className = "blines";
    const editable = new Set(["action", "dialogue", "vo", "parenthetical"]);
    const shotKinds = new Set(["action", "dialogue", "vo"]);
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
      // The authored frame for the beat. Directable until the lock; on the
      // Google route /shorthand (/closeup, /orbit, /filmic…) expands at
      // compile time — Grok keeps it literal.
      if (shotKinds.has(l.kind) && (l.shot || !b.locked)) {
        const shotRow = document.createElement("div");
        shotRow.className = "bshotline"; // NOT .bshot — that's the SHOTS-tab row
        shotRow.textContent = l.shot ? `⌖ ${l.shot}` : "⌖ auto coverage — click to direct";
        if (!b.locked) {
          shotRow.classList.add("editable");
          shotRow.title = "click to direct the camera — /shorthand expands on the Google route";
          shotRow.addEventListener("click", () => editShot(shotRow, l));
        }
        list.appendChild(shotRow);
      }
    });
    pane.appendChild(list);
  }

  function editShot(row, l) {
    if (row.querySelector("input")) return;
    const input = document.createElement("input");
    input.type = "text";
    input.value = l.shot || "";
    input.placeholder = "/closeup: on her hands — or full camera prose; empty = auto coverage";
    input.className = "blineedit";
    row.replaceChildren(input);
    input.focus();
    input.select();
    // live lint: red border while a token neither route knows sits in the shot
    input.addEventListener("input", () => {
      input.classList.toggle("lintwarn", unknownLexTokens(input.value, "").length > 0);
    });
    const restore = () => {
      row.textContent = l.shot ? `⌖ ${l.shot}` : "⌖ auto coverage — click to direct";
    };
    const commit = async () => {
      const shot = input.value.trim();
      if (shot === (l.shot || "")) {
        restore();
        return;
      }
      try {
        const res = await api(`/api/director/sessions/${sessionId}/draft/line`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ line_id: l.line_id, shot }),
        });
        l.shot = shot;
        restore();
        if (res && res.shot_warnings && res.shot_warnings.length) {
          addMsg(
            "director",
            `Shot saved — but ${res.shot_warnings.join(", ")} isn't shorthand on either route; it rides the prompt as literal text.`
          );
        }
      } catch (err) {
        restore();
        addMsg("director", `Couldn't direct that shot: ${err.message || err}`);
      }
    };
    input.addEventListener("keydown", (ev) => {
      if (ev.key === "Enter") { ev.preventDefault(); input.blur(); }
      if (ev.key === "Escape") { input.value = l.shot || ""; input.blur(); }
    });
    input.addEventListener("blur", commit);
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

  function renderBiblesSheet(pane, b) {
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
            refreshProduction();
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

  // Blueprint 30: the take's 1080p finish, beside its verdict on the sheet
  function finishChip(finish) {
    if (!finish) return "";
    const res = esc(finish.resolution || "1080p");
    if (finish.usable) return ` <span class="bok">${res} ✓</span>`;
    if (finish.status === "proposed" || finish.status === "submitting" || finish.status === "submitted") {
      return ` <span class="bt">${res} finishing…</span>`;
    }
    const why = finish.reason || (finish.issues || []).join("; ") || finish.status;
    return ` <span class="verdict fail" title="${esc(why)}">${res} finish failed</span>`;
  }

  function renderShotsSheet(pane, b) {
    pane.replaceChildren();
    if (!b.clips.length) {
      pane.textContent = "The shot list fills at lock.";
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
        `<span class="bstate${c.status === "discarded" ? " skipped" : ""}">${esc(c.status === "discarded" ? "skipped" : c.status)}</span>${verdict}${accepted}${finishChip(c.finish)}` +
        `<span class="bjoin">${join}</span></div>` +
        `<div class="bcam">${esc(c.camera)}</div>`;
      if (c.finish && c.finish.status === "failed" && c.finish.op_id) {
        const retry = document.createElement("button");
        retry.type = "button";
        retry.className = "retrybtn";
        retry.textContent = `↻ Retry ${esc(c.finish.resolution)} finish`;
        retry.title = c.finish.reason || "Re-run the server-side finish — the take itself is never re-shot";
        retry.addEventListener("click", async () => {
          retry.disabled = true;
          try {
            await api(`/api/director/ops/${c.finish.op_id}/retry`, { method: "POST" });
            addMsg("director", `${c.clip_id}: the ${c.finish.resolution} finish is rolling again.`);
            refreshProduction();
          } catch (err) {
            retry.textContent = String(err.message || err);
          }
        });
        row.appendChild(retry);
      }
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
            refreshProduction();
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
            refreshProduction();
          } catch (err) {
            retry.textContent = String(err.message || err);
          }
        });
        row.appendChild(retry);
        const skip = document.createElement("button");
        skip.type = "button";
        skip.className = "skipbtn";
        skip.textContent = "Skip shot";
        skip.title = "Give up on this shot: the cut leaves it out and the run continues with every other clip";
        skip.addEventListener("click", () => {
          armConfirm(skip, "Confirm — leave this shot out of the cut", async () => {
            skip.disabled = true;
            retry.disabled = true;
            try {
              const res = await api(`/api/director/ops/${c.op_id}/skip`, { method: "POST" });
              addMsg("director", res.reply);
              refreshProduction();
              refreshMeter();
              resumeRunAfterRetry(res);
            } catch (err) {
              skip.textContent = String(err.message || err);
            }
          });
        });
        row.appendChild(skip);
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

  function renderCutSheet(pane, b) {
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
        refreshProduction();
      });
      pane.appendChild(btn);
      return;
    }
    if (!productionFilm) {
      productionFilm = document.createElement("video");
      productionFilm.controls = true;
      productionFilm.className = "bfilm";
      productionFilm.preload = "metadata";
      productionFilm.src = `/api/director/films/${sessionId}?t=${Date.now()}#t=0.001`;
    }
    pane.appendChild(productionFilm);
    if (b.deliverables && b.deliverables.length) {
      const head = document.createElement("div");
      head.className = "bhead";
      head.textContent = "DELIVERABLES";
      pane.appendChild(head);
      pane.appendChild(deliverableLinks(b.deliverables));
    }
  }

  ["dur-min", "dur-sec"].forEach((id) => {
    $(id).addEventListener("change", normalizeDuration);
    $(id).addEventListener("input", updateDurationHint);
  });
  updateDurationHint();

  async function resetSession() {
    try {
      // Retire the room we're leaving, or its failed clips reclaim the
      // resume slot on every launch. Ledger and media stay untouched.
      if (sessionId) {
        try {
          await api(`/api/director/sessions/${sessionId}/dismiss`, { method: "POST" });
        } catch (err) {
          /* a failed dismiss must not block the fresh room */
        }
      }
      const created = await api("/api/director/sessions", { method: "POST" });
      sessionId = created.session_id;
      thread.replaceChildren();
      $("idea").value = "";
      clearRefUploads();
      setDuration(8);
      runReset();
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
      if (!document.body.dataset.running) composerEl.classList.add("dropping");
    })
  );
  ["dragleave", "drop"].forEach((t) =>
    composerEl.addEventListener(t, (ev) => {
      ev.preventDefault();
      composerEl.classList.remove("dropping");
    })
  );
  composerEl.addEventListener("drop", (ev) => {
    if (document.body.dataset.running) return; // the console is inert mid-run
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
  $("provider").addEventListener("change", () => {
    updateLexiconPillState();
    lintComposer(); // the known-token set depends on the route
  });
  $("idea").addEventListener("input", lintComposer);
  ["flow-world", "flow-camera", "grok-camera", "grok-atmosphere"].forEach((id) => {
    const el = $(id);
    if (el)
      el.addEventListener("change", () => {
        markDirty(id); // a user choice is an intent to assert this setting
        updateTemplateChip();
      });
  });
  { const a = $("aspect"); if (a) a.addEventListener("change", () => markDirty("aspect")); }
  { const v = $("voice"); if (v) v.addEventListener("change", () => { markDirty("voice"); pushVoice(); }); }
  { const st = $("style"); if (st) st.addEventListener("change", () => { markDirty("style"); pushStyle(); }); }
  $("idea").addEventListener("keydown", (ev) => {
    if (ev.key === "Enter" && !ev.shiftKey) {
      ev.preventDefault();
      send();
    }
  });
  boot();
})();
