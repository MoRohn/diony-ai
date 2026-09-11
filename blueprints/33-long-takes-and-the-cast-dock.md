# Blueprint 33 — Long takes, and a cast dock that never needs scrolling

Status: implemented 2026-09-08. Applies to the Director's scripted lane (Draft Script → Lock → Run) on both engines: Grok Imagine and Google Flow (omni). One-shots already chain on the route's grid (`session._clip_plan`) and are untouched.

## What was true before this blueprint

| Layer | Before |
|---|---|
| split | one script line = one clip. An action beat booked 4s (the opener 6s); a spoken line booked its words plus handles, quantized up. A 16–20s scene the screenwriter was asked to write became four or five clips of 4–6s — the shortest legal sizes, on engines that shoot 15s (Grok) and 10s (omni) in one generation |
| quality | every clip is a fresh generation: a new identity roll, a new chain join, motion that restarts every four seconds. Five joins per scene where one or two would do |
| screenwriter | told to write 2–4 beats per scene; never told how long a take runs on the route it is writing for |
| approving faces | the "Approve X's face" buttons lived in the BIBLES section of the Production card, one message among dozens of op cards in the thread. Reviewing a cast meant scrolling the whole room to find them, then scrolling back |

## 1. Takes, not lines

`splitter.split_script` dissects a scene into **beats** (an action line ≈ 4s, the scene's opener ≈ 6s, a spoken line = its words at 2.5 wps plus the 0.5s/1.0s handles), then **packs** the beats into takes (`splitter.pack_takes`):

- **The fewest takes the route allows.** The scene's natural length is divided by the route's longest legal clip — `splitter.take_unit`: Grok 15s, omni 10s, Veo the plan's top of grid — with a quarter of pace tolerance, so 18s of beats is ONE 15s take on Grok, not 9s + 9s and never 15s + 3s.
- **As even as the beats permit.** A dynamic partition puts each take closest to the scene's even share (least squared deviation): 30s of seven beats is two 15s takes; 22s is 11s + 11s. When the share cannot be met, one more take is tried.
- **One spoken line per take, always.** Every downstream consumer — the compiler's spoken clause, the cue sheet, the review's line measure, the subtitles, the speech guard — reads one excerpt per clip. Two lines never share a take; three lines are three takes even when they would fit one.
- **Each lane's cap holds.** A take carrying a line is capped by the dialogue lane (10s on omni under Auto and Google Flow); a picture-only take by the picture lane (15s on Grok). A lone line longer than the cap rides the cap uncut, as before.
- **Paced, then quantized.** Each take is paced to the share — its beats stretch or compress up to a quarter to meet it, the words never compress — and `ClipUnit.natural_s` records that paced length before the grid rounds it up (`duration_s`).

A take's camera is every authored shot in it, in order, as a progression (`Overhead top-shot…; then low-angle close-up…`). Fallback grammar only opens a take (the wide establishing frame, the coverage rotation) or answers a line (the listener's reaction close-up) — never a rotation inside one continuous take. A take with a line carries the line's setup beat before it and its reaction after it in the same frame; the line's class, lane, lock recipe, identity packs, and voicing are exactly what the lone-line clip carried.

**What packing gives up.** A scene whose opener beat shares its take with the first line rides the voice lane under the dialogue lock recipe; the plate anchors that take as a reference, not as its first frame (the same as a scene that opens on a line always did). A picture-only opener keeps the plate as its first frame when the authored shot reads as the room.

## 2. The words sit where the take puts them

`AudioPacket.line_at_s` is where the spoken line begins inside its take (0 when the take opens on the line), paced to the booking. The post narrator's cue lands there (`narration.cue_sheet`: `start_s = timeline + line_at_s + lead-in`, `window_s = duration − line_at_s`) and the subtitle follows (`deliverables`). The assembly's dead-air trim on a spoken take follows `natural_s` — the whole take, beats included — never the word-count arithmetic alone (`app._assembly_spec`); picture takes play what they booked.

## 3. The screenwriter writes for long takes

`screenwriter.take_brief` tells the brain the route's take length (`session.draft_take_units` reads the same engine choice the lock reads): a 16–20s scene plays as one or two takes, beats are continuous physical motion inside one camera setup, the setup changes only when the story needs a new angle, and the beat before a line sets it up while the beat after is the reaction — in the same frame. The draft reply and the lock reply name the take length (`takes of up to 15s (10s when a line is spoken in it)`).

## 4. The cast dock

Approving a face never means finding the Production card. While a locked film has a face awaiting approval, the console carries a **cast dock** (`#castdock`, painted from every Production re-read): one row per character — the anchor still, name and role, the measured sheet's verdict in plain words (coheres / does not cohere / not scored / drawing), and **Approve** — plus **Approve all N** (an in-page confirm, `POST …/bible/approve_all`: every landed face frozen in one press, a sheet still drawing named and left for the next press) and **Cast ▸**, which scrolls the BIBLES section into view for the identity cards, every still, redraw, and Flow registration. A role already bound to a reusable Flow character shows **FLOW ✓** immediately; before its local review sheet exists the thumbnail is a locally cached provider portrait and the row explicitly says the local sheet still waits for Run. The dock stays visible while the run strip folds the console (Blueprint 32 sheets roll first, so this is exactly when faces land), and goes away once every face is approved; the FACE ✓ stays on the card. Approvals persist (`session.persist`) like every decision. Each character's `sheet_state` on the board (`waiting` for Run, `drawing` at the provider, `failed`, `landed`) is read from the sheet ops themselves; until a sheet draws or lands the dock is one quiet line ("2 sheets wait for Run — faces come here to be approved") only when no linked Flow identity also needs to be shown. The SHOTS rows name each take's beat count (`8s · 2 beats · omni-flash`).

The whole bottom Director console — Cast dock, run status, prompt, references, and configuration — sits below a deliberate horizontal splitter and a full-width **Director controls** dock header. Its supporting line names the contents, and its explicit **Collapse / Expand** action controls `#composer-panel` with `aria-expanded` and remembers the user's choice locally. The header remains visible in either state, so the thread and its controls no longer visually run together. When expanded, the dock may occupy at most 78% of a desktop viewport (84% on a narrow viewport) by default; `#composer-panel` is the single contained vertical scroll region, keeping every control reachable while the screenplay retains its own independent scroll position.

The splitter is a visible, full-width drag target with an `ns-resize` cursor. Dragging it upward enlarges the Director dock; dragging downward gives the screenplay more room. The live size is clamped so the controls retain at least 190 px and the screenplay retains at least 160 px. Diony stores the selected ratio rather than a device-specific pixel height and reapplies it after refresh or window resizing. The separator exposes horizontal-separator ARIA value metadata, accepts **Arrow Up / Arrow Down** in 32 px steps, **Home / End** for the minimum and maximum, and double-click resets the default responsive height. Starting a drag or keyboard resize from the collapsed state expands the dock first.

The run strip treats the priced BIBLES as the first phase of the same batch, not as invisible work behind a `0/N clips` counter. Until every current plate and character-sheet operation settles it reads **Preparing cast and locations**, names the active character/view, and reports ready / drawing / waiting BIBLE assets; only then does its track switch to clips. The Production heading separately reports character-view progress and the batch watcher repaints the BIBLES card on every advance tick, so an uploaded anchor and each derived view appear without a manual refresh. A browser reload reconnects the durable `run_plan` from the first advance snapshot and resumes the same watch-through-finish-and-cut loop—refreshing the page cannot orphan an armed Run & Assemble production.

## 5. A priced room comes back

`session.resumable_sessions` used to revive only rooms with failed or in-flight ops, so a script locked and priced but never rolled came back as a fresh room over a package on disk. A locked package with drafted clips and nothing printed is now a `priced` row (a `running` row always outranks it, newest first within each; a dismissed room stays dismissed). The room rebuilds itself from `GET …/sessions/{sid}/lock` — the clip ops in shot order, the plate & sheet stills, the cast keyframes, the planned seconds and the locked total — as the draft card with its read-only sheet (no second Lock button), the priced cards with the batch bar, and the Production card with the dock. ＋ NEW SESSION dismisses the room it leaves, drops the Production card, and clears the dock.

## Tests

`apps/director/tests/test_long_takes.py` (units per route, fifteen-second takes, even shares, one line per take, the line's offset, the cue sheet, the trim, the brief, the replies), `test_cast_dock.py` (approve all: landed vs drawing, refusal, idempotence, 404; Flow-linked dock state; the accessible, bounded, internally scrolling console and persistent splitter; the board's sheet state and beat counts; BIBLE-first run status and reload reconnection), `test_resume_priced.py` (the priced row, its ranking, dismissal, the lock endpoint), and the rewritten expectations in `test_splitter.py`, `test_p1_craft.py`, `test_craft_quality.py`, `test_cast_lock.py`, `test_production_style.py`. Current UI stamp p66; QA through `/?qa=1` → `window.dionyRun.setSession(sid); window.dionyRun.paintCastDock(board)`.
