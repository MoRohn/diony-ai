# Blueprint 29 — The Look Bible and the Style Frame

Status: implemented; the look derives on the Director brain when one is configured and from the idea's tells otherwise

Owner: Diony AI

Applies to: the master derivation (Blueprint 27), every compiled clip prompt on all three dialects, one-shots and chained one-shots, setting plates and character sheets, the screenwriter brief, the chain submit path, chain review, uploads, state.json, the Director UI, the schema export

Depends on: Blueprints 25 (Director pivot), 26 (Continuity Lock), 27 (Master Video Prompt); the Flow and Grok prompt lexicons

## The failure this answers

Production s-e790706066 (2026-09-02): a twelve-clip chained one-shot, an authored
2.5D explainer, printed in eight visual languages — a flat infographic, a photoreal
sedan interior, white paper-cutout towers, a sepia clay isometric town, a beige
desert render, a cold sci-fi control room, a white corporate isometric diagram,
three dark-blue isometric cities, a cracked-white split landscape, and a photoreal
sedan interior with a different man. Every shot looked drawn by a different studio.

Traced to code, the film had no look anywhere in its pipeline:

| Where | What was true |
|---|---|
| chained one-shot | the ONLY visual input per link was the previous link's last frame; style rode in text alone. Frames and reference images are exclusive on every lane (`capabilities.frames_exclusive_with_refs`), so no look anchor could ever ride beside the bridge frame. When the script changed subject every scene, the start frame was something to morph away from, and Veo reinvented the medium from loose style words each time. |
| uploads | `_attach_stills` forced the FIRST upload as clip 1's start frame — an infographic meant as a structural reference for clip 6 opened the film; the portrait could not ride at all |
| CLIP DELIVERY | the brain was told to "restate lighting" per clip; lighting, palette, and medium were re-derived per link. Nothing byte-identical carried across links except one loose style sentence and the Flow world clause |
| chain review | `review_clip` runs only when the op maps to a package clip; chain links have no clip id → no review, no drift gate, every link auto-advanced from a drifted frame |
| Draft Script path | `MasterPrompt.visual_style` was consumed nowhere outside `master_prompt.py`: the screenwriter brief omitted it, the compiler's inherited pieces omitted it, the screenwriter brain contract had no film-level style or medium (only per-setting `grade_language`, written independently per setting) |
| hard-coded photorealism | plates were always "Photorealistic cinema still", every Grok prompt appended REALISM ("digital cinema camera … documentary realism"), the Flow baseline world was "Shot on a cinema camera, 35mm" — a non-photoreal film was fought by its own prompts |

## 1. The Look Bible

`diony_contracts.production.LookBible` (schema-exported as `look_bible`), carried on
`MasterPrompt.look`:

| Field | Meaning |
|---|---|
| `medium` | the one medium every shot is made in ("2.5D animated explainer, flat shaded with soft paper texture"; "photoreal live action on a digital cinema camera") |
| `render_language` | how surfaces, edges, and light are drawn: shading model, outline, grain |
| `palette` | the colours and their discipline |
| `lens_grade` | lens, depth of field, contrast, and grade the whole film shares |
| `lighting_system` | the lighting philosophy — never one scene's sources |
| `negatives` | what the look must never become, stored as the user wrote them, read back as nouns ("no photoreal footage" → "Never: photoreal footage") |
| `source` | brain, template, or user |
| `anchor_assets` | the look's reference images: the user's stills and, once the chain opener prints, the frame the film established its look with |
| `photoreal` (property) | decided by the medium: a stylized tell anywhere in the description wins; no tells reads as photoreal (the house default every prompt carried before) |
| `block()` | the verbatim LOOK BIBLE clause, deterministic from the fields |

**Derivation.** The master template's OUTPUT CONTRACT gained the `look` object with the
instruction that it is appended verbatim to every clip prompt so no prompt may re-derive
or contradict it. The brain answer is parsed strictly; a missing block is derived from
`visual_style` and the idea, named in warnings — a film never ships without a look. The
brainless template derives the look from the idea's tells (medium, palette, lens) —
photoreal unless the idea names a medium.

**Carriage.** The block rides at priority 0 (untouchable under length pressure) on:

- every compiled clip of a scripted package, on the Grok, Veo, and Omni dialects
  (`compiler._look_block`), byte-identical across clips;
- every one-shot and every chained link (`session.compile_master_body(look=)`);
- every setting plate and character sheet (`artifacts._plate_prompt`, `_sheet_prompt`),
  which also render IN the medium — an animated film casts and plates in its own medium,
  never as photographs the clips then have to abandon;
- the screenwriter brief (`master_brief`) and the screenwriter brain's rules: every
  setting's `grade_language` is a variation inside the look, never a new style. The
  template screenwriter takes the look's palette as every setting's grade.

**Ground truth yields.** Grok's REALISM clause and the Flow BASELINE world clause ride a
photoreal look only. An authored world (inline `/world` or a chosen session world)
always rides beside the look — authorship wins. A package locked without a look compiles
byte-identical to before.

## 2. The style frame

Inside a chained one-shot, the CLIP DELIVERY split now returns per clip `scene_change`
(true when the clip opens on a different place or subject than the previous clip's final
frame — a cut, not a continuation; the opener is never a cut) and `opening` (one
sentence describing the clip's first frame as a still). The brain is told to name each
scene's motivated light sources but NEVER restate or vary the medium, palette, grain, or
rendering style — the system appends the look.

For every link flagged `scene_change`, `session._plan_style_frames` registers a priced
image op on the sheet lane (nano-banana on Flow, the Grok image lane otherwise) whose
prompt is the clip's opening composed in the look (`look.style_frame_prompt`). It is
tied to its link through the link's runtime (`style_frame_op`), registered in the
session and the ledger, priced in the reply and the opener's rationale, and never part
of the chain sequence or a batch target: it rolls INSIDE the link's submit
(`app._roll_style_frame`) — synchronously, since every image lane is synchronous —
referencing, in order, the frame the chain established its look with (the opener's
first frame), the look's anchor assets and the user's stills, then the previous link's
last frame for compositional continuity. Its print is conformed to the program raster
and becomes the link's `start_image` (`_late_bind_chain_bridge`). A continuing link
still bridges from the previous link's last frame exactly as before.

The video model only animates; the still model holds the artist.

## 3. Stills are references unless marked

Every upload is a look-and-identity reference (`role: reference`). Exactly one may be
marked the OPENER (`POST …/uploads/{id}/role {"role": "opener"}`, or the role toggle on
its chip). Nothing opens the frame unless the user says so. Unmarked stills ride a
one-shot or chain opener as reference images — legal without a frame on every lane —
so the model composes the shot IN their look instead of morphing away from one of them.
They also lead every plate's references, with the look's anchors behind them.

## 4. Chain links are reviewed

`review.review_chain_link` measures a chained link the way a scripted take is measured:
delivery, duration, frames, and look continuity — did the clip open from the frame it
was given (`open_drift` vs its start image, fail above 0.06), did it hold the previous
link's colour when it continued it (`chain_color_drift`, fail above 0.05; never measured
across a scene change), and did the look wander within the clip (`intra_clip_drift`,
advisory). A fail holds the chain at that link (`_oneshot_broken` names the review,
`_guard_chain_order` names it on a manual run): **Retake** (`POST /ops/{id}/retry` on a
printed, review-failed, unaccepted link rolls it again from the same opening frame; the
rejected take stays on the ledger) or **Accept** re-arms the chain. The chain opener's
first frame registers as a look anchor.

## 5. Surfaces

- `GET /ops/{id}` carries `scene_change` and `style_frame` `{op_id, status, model,
  est_usd_micros, ready}`; `GET /ops/{id}/style_frame` serves the still.
- Cards: a scene-change link says it opens from a style frame (priced, with its status),
  shows the frame once printed, lands its look review with Accept / ↻ Retake, and the
  chain bar holds — loudly, once — on a failed look review instead of cutting over it.
- The master disclosure shows the look; still chips show and toggle their role.
- state.json: the look rides the master docs; `style_frame_op` and `style_frame`
  survive a restart with the other runtime keys.

## Not done

- Extend-lane chaining on Flow (`/videos/extend`) for same-scene continuation was not
  wired; it needs a live check on extension limits before it can be doctrine.
- A user-authored look (`source: user`) has the contract but no UI or endpoint yet — the
  look derives from the idea, the stills, and the brain.
