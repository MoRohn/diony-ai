# Blueprint 32 — Cast Lock: character identity that holds, and lines heard from off-screen

Status: implemented on the Director (cloud) lane as an opt-in per-session mode (**Cast: Standard | Locked**); off-screen lines work under both modes

Owner: Diony AI

Applies to: the contracts (`IdentityCard`, `CharacterProfile.identity / sheet_receipt / sheet_retries`, `ScriptLine.off_screen`, `ProductionPackage.cast_lock`), the screenwriter brain contract, the splitter, the compiler (all three dialects), `provider_artifact_refs`, both provider adapters, the sheet planner, the late-bind path, review, deliverables, the Producer's face-identity module, the Director console, `diony-director-check`

Depends on: Blueprints 26 (production package), 27 (master prompt, likeness guard), 28 (post narration), 29 (look bible, style frames), 31 (production style)

## What was true before this blueprint

The Director always had an identity path — a character sheet, a face gate, Flow character registration, a SFace identity family in review — but every link in it was soft. Traced to code (2026-09-07):

| Where | What was true |
|---|---|
| prompts | the character description was prose re-derived per clip from `age_body_notes`, riding at **priority 3** — the FIRST piece `_fit_prompt` shrinks and deletes under length pressure — and placed after sound and lighting, where atmosphere dilutes a face |
| sheets | five angles chained one still at a time; the Grok image lane takes ONE reference, so "chain the approved stills" meant "chain the front still only"; nothing ever measured whether the five stills were one person |
| references vs frames | frames and references are exclusive on every lane; every bridged clip opened from the previous frame and DROPPED its identity references (`references_dropped` on the handle); the every-2nd-link re-anchor was the only moment identity rode again |
| Flow characters | `POST /characters` was reachable only by an endpoint no UI called, and sent up to seven images against a documented maximum of two; `refs["characters"]` was empty in practice, so the seven-character Omni budget went unused |
| provider markers | Grok documents `<IMAGE_n>` tags and useapi documents `@character_n` / `@referenceImage_n` markers; the Director sent untagged images and let the model guess what each was |
| review | `_measure_identity` returned on the first drifted face — a two-hander's second lead was never checked; the models were staged only on the GPU host, so every desk read `identity unmeasured` forever |
| off-screen | there was no way to write a character who speaks from outside the frame; a `vo` line attached the speaker's sheet to a shot they are not in |

## What the frontier tools reward (the research this answers)

- **The image is the identity contract; the video prompt is the motion contract.** Veo 3.1's Ingredients and Grok Imagine's reference-to-video both anchor identity on stills; the guides say never to re-describe the character in the video prompt beyond a locked descriptor, and to place that descriptor BEFORE scene and style so atmosphere tokens do not dilute it.
- **Sheets over prose.** The production workflows that hold identity across a finished film use a neutral-light, plain-backdrop anchor portrait and a multi-view turnaround board drawn FROM it, hands empty (props cause angle-to-angle drift), then reuse the same references on every shot. A single grid is one person by construction.
- **Keyframe-first.** Compose the opening frame with the image model from the character references and the setting, then animate it — the still model holds the face, the video model animates.
- **Priority order matters.** On both lanes the first reference has the strongest pull; on Grok the earliest words in the prompt weigh most.
- **Measure, don't eyeball.** Face-embedding cosine similarity (ArcFace/SFace, threshold ~0.36) is the standard evidence for identity consistency across generations; the Producer already ships YuNet+SFace.
- **Off-screen speech** is written by describing the voice without a talking face in frame; the shot frames who hears it.

Provider contracts pinned while writing this (see `capabilities.py` for the encoded matrix): Grok `POST /v1/videos/generations` takes `reference_images` (docs show three; the July 2026 release notes say up to seven) exclusive with `image`; `POST /v1/images/edits` takes one `image` or up to five `image_urls`. useapi Flow `POST /videos` takes `referenceImage_1..7` (Veo three, Omni seven), `character_1..7`, inline `@` markers that MUST match a supplied slot; `POST /characters` takes `imageReference_1..2` (max two), `personalityNotes`, `voice`; `POST /images` takes `reference_1..10` and `character_1..7`.

## 1. The mode

`cast.py` holds the catalog. **Standard** is the pre-32 platform, byte-identical (the compiler emits no identity block, the sheet keeps five slots, no keyframes, no markers). **Locked** turns on everything below. The mode is a session setting beside Style (`POST /api/director/sessions/{sid}/cast`, `cast` on message/produce bodies, `cast_lock` in `GET …/settings`, persisted as `cast_lock` in state.json), fixed at the lock with a 409 — the package carries it as `ProductionPackage.cast_lock`, and every consumer reads the package, never the session.

## 2. The identity card

`IdentityCard` on `CharacterProfile.identity`: `anchor` (age, build, face geometry), `features` (3–5 non-negotiables), `wardrobe_lock`, `negatives`, `source` (brain | derived | user). `block(name)` renders the verbatim `IDENTITY LOCK — NAME: …` clause; negatives read back as nouns ("no hair restyle" → "Never: hair restyle").

The screenwriter brain contract asks for `identity` per character; `cast.derive_identity` writes one deterministically from the casting notes when the brain did not. Under Locked the card rides **every** prompt that shows the character at **priority 0**, placed right after the camera and blocking locks on all three dialects; the compiler's old prose block is not emitted. It also rides the anchor portrait, the board, and every keyframe, so one text casts the face everywhere.

## 3. The board sheet

Locked sheets are two slots: `front` (the anchor: straight-on, neutral light, plain mid-grey, nothing in the hands) and `board` (a 2×2 turnaround — front, three-quarter, profile, full body — drawn FROM the anchor). On Grok the board rolls on the **image edit lane** (`grok.submit_image_edit`, square) with the anchor as its source; on Flow, nano-banana with the anchor as `reference_1`. `cast.identity_refs` orders a character's video references board-first (four views in one slot), then the anchor.

## 4. The measured sheet

When a still lands, `app._after_sheet_still` scores it against the anchor with the Producer's `measure_still_identity` (YuNet+SFace, cosine ≥ 0.363). The receipt lives on `CharacterProfile.sheet_receipt` (`measured, threshold, scores, min, verified, failed, notes`) and on the bible card ("sheet coheres — 1/1 views read as MARA (min 0.71…)" / "sheet does NOT cohere — board reads as another person"). A failing slot is thrown away and redrawn from the anchor, at most `MAX_SLOT_REDRAWS` (2) times, the new op joining the bible gate and the running batch through `session.add_bible_op`. A human can do the same with **redraw** on any still (`POST …/bible/redraw/{char_id}/{slot}`; the anchor refuses while anything else draws from it).

Honesty rule: no models → `measured: false` with the staging command named; a still with no usable face is named in `notes` and never counts as a pass.

**Desk staging.** `diony-director-check --stage-face-models` downloads the two OpenCV Zoo files (from `media.githubusercontent.com` — the zoo keeps its weights in Git LFS and the raw host serves 130-byte pointers, which cv2 refuses; each file is pinned to its published SHA-256 and a pointer or wrong hash is refused, a bad earlier file replaced) into `<vault>/models/opencv-zoo/` (or `DIONY_FACE_ID_DIR`); `face_identity.staged_face_models()` finds them there when the env vars are unset, so review's identity family and the sheet measure work on a laptop, not only on the GPU host. `GET /api/director/cast/modes` reports `face_models_staged`; the console says so when Locked is chosen on an unstaged desk.

## 5. Cast keyframes

At lock, `session._plan_cast_keyframes` registers one priced image op per scripted clip that shows a packed character — tied to the clip through the same `style_frame_op` runtime key a chain link's style frame uses. Its prompt (`cast.keyframe_prompt`) is the clip's camera, its first beat frozen, every visible character's card verbatim, the setting, the look, "a single still frame, no text". Its references (`app._style_frame_references`, keyframe branch): each character's board, the setting plate, the director's uploads, and — when the clip continues its scene — the previous clip's last frame at the cut point. On Grok it composes on the edit lane (≤5 sources); on Flow, nano-banana.

It rolls inside the clip's submit (`_run_link_or_op` → `_roll_style_frame`, now for any op with a frame op), synchronously; `_late_bind_locks` then opens the clip FROM it (`start_image`), dropping the references it was drawn from. The bridge stays fluid because the previous frame shaped the keyframe; identity is never dropped for a frame again.

**The exception.** On Flow, a clip whose refs carry a character object or a voice keeps them (frames are exclusive with locks, and a lock outranks a frame); the keyframe is skipped, never spent (`style_frame_skipped` on the runtime). Locked totals list the keyframes as their own estimate line.

## 6. Provider identity markers

`compiler.reference_roles` labels every reference the op will send (character:NAME, plate, bridge, look, upload) by path — so late binding may reorder freely — and names registered characters in `character_names`. At submit, Grok appends `<IMAGE_n> is MARA's identity sheet — match this exact face…` for the images that actually ship; Flow appends `@character_n is MARA…` / `@referenceImage_n is the setting plate…` for the slots the body carries (a dangling marker 400s). A legend that would not fit the prompt limit is dropped whole — the stills still carry the identity.

## 7. Flow characters register themselves

Once a Locked sheet is complete and coheres (or cannot be measured), the character object is registered from the anchor and the board — two images, the documented maximum — with the character's Omni voice preset and the card's anchor as `personalityNotes`. `provider_artifact_refs` then carries it as `characters`; while no object exists yet, the identity stills ride as references beside the plate. The console's **Register on Flow** button does the same by hand; `register_character` caps at two images now (the old call sent seven).

## 8. Off-screen lines

`ScriptLine.off_screen` marks a dialogue line heard from outside the frame. The brain may set it (`off_screen: true`, told when to: hold on the listener, a voice from the next room or behind the camera); a screenplay cue tag on the speaker parses too (`MARA (O.S.)`, `(OS)`, `(off-screen)`; `(V.O.)` on a dialogue line makes it a `vo` line; `(CONT'D)` is stripped). The script sheet shows the cue as `MARA (O.S.)` and an **O.S.** toggle on every dialogue line until the lock (`POST …/draft/line {line_id, off_screen}`; a non-dialogue line 422s).

At the split: `on_camera=False`, the excerpt carries `off_screen` and the `listeners`, the default camera holds on who hears it, and the identity packs are the **visible** cast (the speaker's sheet never rides a shot they are not in; under Locked, an on-camera line also packs its named listener). Voicing by style: Narrated → post narrator; Dialogue → generated; **Mix → generated** (it is the character's line, one voice per character, unlike a voice-over). The compiler's delivery clause becomes "MARA speaks from off-screen — out of frame for the whole shot; JON listens and reacts; no one on screen forms words" (+ `NO_SPEECH`), and the spoken clause becomes the verbatim line "from OFF-SCREEN … the voice arrives from beyond the frame edge … no one on screen mouths the words" (+ `ONLY_THE_LINE`). Subtitles label the speaker `MARA (O.S.)`. The phrasing avoids the compiler's concealment triggers ("never seen") so the listener's face is never locked away.

## Review changes

`_measure_identity` measures **every** packed character and fails the family on any drifted face; a character whose face the frames never show stays unmeasured. `_face_receipt` uses the staged models.

## Not done / verify live

- `grok.submit_image_edit` wire shape (`image {url, type}` / `image_urls`) is per docs.x.ai and NOT yet verified against the live API — the first Locked Grok run's board names the field if it is wrong.
- Grok reference-to-video count stays at the encoded 3 until a live call proves 7.
- Chain links (one-shots) get no cast keyframes — they have no package cast; their style frames unchanged.
- A user-authored identity card (source `user`) has a contract and a board line but no editor yet.
- The e2e tests run the Locked production on both engine choices with faked lanes; nothing here has rolled for money yet.

## Tests

`apps/director/tests/test_cast_lock.py` (35): catalog, card derivation and brain parse, prompts on all three dialects (byte-identical card, priority 0, survives length pressure where Standard prose gave way), board sheet planning and references, identity reference order, unmeasured/measured/failed sheet receipts, desk model staging, every-cast-member review, legends on both adapters, the edit-lane payload, character registration payload, speaker tags, off-screen split/compile/voicing/subtitles, the O.S. toggle, the session setting (persist, rehydrate, 409 at lock), keyframe planning, and two end-to-end Locked productions (Grok-only: boards and keyframes on the edit lane, clips open from keyframes; mixed lane: Flow characters carry dialogue, keyframes carry pictures, a bad board is redrawn and the human redraw joins the gate).

UI stamp p45.
