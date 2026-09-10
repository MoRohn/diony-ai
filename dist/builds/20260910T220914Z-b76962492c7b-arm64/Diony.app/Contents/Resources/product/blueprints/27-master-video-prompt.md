# Blueprint 27 — The Master Video Prompt

Status: implemented contract; the derivation runs on the Director brain when one is configured and on a named deterministic template otherwise

Owner: Diony AI

Applies to: Director one-shot proposals, Director scripted drafts, the screenwriter, the splitter's input, the compiler (all three dialects), state.json, the Director UI, the schema export

Depends on: Blueprints 25 (Director pivot), 26 (Continuity Lock); the Flow and Grok prompt lexicons

## Product decision

Before this Blueprint the Director's idea reached the generator two ways: a one-shot
compiled the raw idea plus a route guard, and a scripted draft asked the brain for a
screenplay straight from the idea. Both skipped the step a director actually does
first — deciding WHO, WHAT, WHERE, how the scene is layered, how the action develops
in time, how the subject physically performs, how the environment responds, how the
camera observes, how the scene is lit, what must stay consistent, what must be
prevented, and exactly how the clip ends.

The **MASTER AI VIDEO PROMPT TEMPLATE** is now that step, for every flow and every
model. It runs as the system prompt of one brain call per brief and returns the
**pre-split finalized artifact** — a `MasterPrompt` — before any shot is split. The
template text is carried verbatim in `diony_director.master_prompt.MASTER_TEMPLATE`;
this Blueprint records how it is wired, not a paraphrase of it.

The user supplies only the idea, optional reference stills, and a duration. The
system derives everything else.

## 1. The artifact

`diony_contracts.production.MasterPrompt` (schema-exported as `master_prompt`):

| Field | Meaning |
|---|---|
| `idea`, `target_duration_s`, `reference_image` | the inputs, as received |
| `estimated_duration_s` | the derivation's honest estimate of how long the action as written plays |
| `visual_style`, `primary_subject`, `primary_action`, `setting` | the visual anchor |
| `beats` | `{stage, text}` in order, stages from `initial_state … final_state`, only the beats used |
| `scene_depth`, `spatial` | background / middle ground / foreground and the spatial relationships held for the clip |
| `camera`, `performance`, `micro_expressions`, `physics`, `body_mechanics` | how it is observed and how it moves |
| `lighting` | one coherent system: `source`, `quality`, `shadows`, optional `secondary` |
| `mood`, `continuity_anchors`, `ending`, `constraints` | the tone, what must not drift, the last image, 3–6 scene-specific restrictions |
| `prompt`, `word_count` | the generator-ready prompt after the optimization pass |
| `source`, `warnings`, `brain_usage` | `brain` or `template`; every degradation named; token spend |

## 2. Duration is an input

The one amendment to the template is a **TARGET DURATION** section. The user's
duration drives the derivation three ways:

- **Beat budget** — `beat_budget(seconds)`: ≤4 s → 2 beats, 5–8 s → 3, 9–15 s → 4,
  longer → the full progression written as one continuous action a shooting script
  realizes across shots in order.
- **Pacing sentence** — every final prompt carries "Pace the action to play in about
  N seconds." (the optimizer guarantees it).
- **Estimate back to the user** — `estimated_duration_s` is compared with what was
  booked. A one-shot reply names the gap and the exact Duration to set (and the 15 s
  one-shot cap when the action outgrows it); a draft reply names the gap against the
  runtime target. The booking never changes silently — the number is advice, the
  Duration control stays the user's.

## 3. Derivation

`master_prompt.derive(idea, target_duration_s=, reference_images=, authored=)`

- **Brain configured** (`DIONY_DIRECTOR_BRAIN_URL`, or `XAI_API_KEY` → grok-4.6):
  `POST /v1/chat/completions`, system = template + duration section + output
  contract, user = the INPUT block (idea, target duration, reference count, and any
  ALREADY ESTABLISHED elements), stills attached as image parts (≤4), JSON mode,
  temperature 0.4. Parsed strictly: no `beats` or no `prompt` is a refusal. Two
  attempts, then `MasterUnavailable` — **never a template stand-in** when a brain is
  configured (the same doctrine as the screenwriter).
- **Brain off**: a deterministic template derivation, named in `warnings`. It never
  paraphrases the idea (it cannot); it states it once as the anchor and fills the
  structure from keyword-derived lighting, mood, style, and setting.
- **Optimization pass** (`optimize`, idempotent in prompt and warnings, both paths): remove repeated
  sentences; any ending lead the brain chose ("The video ends on…") normalized to the template's; the reference-image opener present exactly when a still is supplied;
  "The clip ends with …" present exactly once, last before the constraints;
  constraints trimmed to 6 and flagged under 3; the pacing sentence (skipped when the brain already paced to the target in its own words); the word count
  checked against the template's bands (60–100 / 90–140 / 120–180 by beat count,
  220 ceiling) with anything outside named in `warnings`.

**Authored elements.** Inline `/shorthand` in the idea is expanded in the route's
dialect BEFORE derivation (a token the route's lexicon does not know rides literally,
exactly as it would compile). Session templates — Grok camera/atmosphere, Flow
camera/world — are passed as ALREADY ESTABLISHED elements the template says to
preserve rather than reinterpret. Route isolation is unchanged: Grok tokens on Flow
stay literal and vice-versa.

## 3a. The likeness guard — a real person is described, never named

2026-09-05: the idea named nobody; the reference still was a truck poster; the brain,
which SEES the still, recognized the actor and wrote his name into the master prompt
and all 26 clip prompts (`Create a video of <actor> locked in a full split…`,
`<actor>'s face`). Google Flow's prominent-people filter refuses such a prompt before
render (Blueprint 30 `PROMINENT_PERSON`), Grok's likeness policy is no kinder, and a
name is not a visual instruction — the generator needs the build, the sunglasses, the
vest, the pose. `diony_director.likeness` closes it in three layers, one law:

1. **The brain is told.** Both system prompts (the master template and the
   screenwriter) carry the LIKENESS LAW: never write a real, identifiable person's
   name, nickname, initials, or identifying title — from the idea or from a still —
   describe them as a character instead; and return `real_people`
   (`{name, aliases, descriptor, label}`) for everyone recognized. A script character
   is an invented name or a role label, never a real person's.
2. **The system enforces.** Every listed person's full name, surname group (particles
   ride with it: "Van Damme", "De Niro"), a distinctive first name (hyphenated or ≥ 5
   letters — "Will" stays a verb), the brain's aliases, and their possessives are
   replaced by the descriptor in every text field of the artifact: prompt, narration,
   subject/action/setting, beats, depth, anchors, constraints, ending, every chained
   clip's prompt/opening/narration, and the look. Running prose capitalizes a
   replacement that opens a sentence; fragments the assembler joins (anchors,
   constraints) never do; an ALL CAPS mention (a cue line) becomes the label in caps.
   In a screenplay a real person cast as a character is renamed to the label and every
   speaker follows. The record lands on the artifact (`MasterPrompt.real_people`), so a
   revision note, an authored shot, or a chained clip compiled later holds the same law
   (`compiler._guarded`, `session.compile_master_body(people=)`); the master's record
   is inherited by the screenplay stage.
3. **The system audits.** Capitalized phrases the brain did not list (multi-word
   anywhere; single words only mid-sentence; never ALL CAPS on-screen titles) go back
   to the brain once, text only (`LIKENESS AUDIT`, temperature 0): which of these name
   a real person? Anyone named is enforced as above, and a phrase containing a
   confirmed surname ("Jean-Claude Van Damme" when the audit said "Van Damme") joins
   the aliases. Best effort: an audit that fails is a warning on the artifact, never a
   failed derivation. `DIONY_LIKENESS_AUDIT=0` switches the second call off.

Only people are touched. Brands (Volvo FM), places, on-screen titles (PRECISION), and
the user's own invented characters (Mara, ELIAS) are left exactly as written — the
brain decides who is real, the code never guesses. The artifact's warning names the
descriptor and the mention count, never the name; the record keeps the name for
enforcement only, and it never reaches a provider. A film with no real people compiles
byte-identical to before.

## 4. Where it lands

**One-shot** (`session.propose`). The master prompt IS the prompt body; the route's
guards ride on top — Grok: camera-lock hardening over the body, the i2v directive
when a still opens the frame, the realism/anti-artifact ground truth; Flow: the world
clause and the anatomy guard. Brain off: derived inline. Brain on: the op is priced
immediately and held as **`deriving`** (a new `DirectorOp.status`, not runnable —
`/run` 409s), carrying a provisional raw-idea prompt labeled `v0-provisional`; a
worker derives, recompiles as `v1`, and releases it to `proposed`. A failed
derivation releases the op with the provisional prompt and the failure named in the
rationale; a restart releases it likewise, named. Nothing slow rides a request
through the name router.

**Scripted draft** (`session.begin_draft`). Two stages, both off-request: derive the
film's master prompt (runtime target = the duration input, engine choice picks the
dialect), then write the screenplay FROM it. The screenwriter's brain call carries the
artifact as a **MASTER PROMPT brief**: subject, setting, beats in order, camera
philosophy, lighting system, mood, anchors, constraints, ending — "preserve, do not
reinterpret; the final scene lands the ending exactly". The template screenwriter
uses the same fields (the lighting names the setting's light and grade, the camera
frames the opener, the ending is the last scene's closing beat). Draft status exposes
`stage` (`deriving` → `writing` → `ready`) and `master`.

**Lock and split.** `packagebuilder.build_package` carries the artifact onto
`ProductionPackage.master_prompt`. The splitter is unchanged: shots come from the
screenplay the artifact shaped.

**Compiler, all three dialects** (`_master_pieces`). Every split clip inherits from
the artifact: the lighting system (priority 2 — only when every scene shares one setting; a film that moves between settings lights each scene from its own setting block), the physics/body-mechanics grounding
(2), the continuity anchors (1), the scene-specific constraints (1), and — on the
last clip of the package only — "The clip ends with …" (1). Priority-0 pieces
(camera intent, quoted dialogue, anti-artifact) still win under length pressure, and
a package locked without an artifact compiles exactly as before.

## 5. Persistence and surfaces

- state.json: the draft doc carries `master`; a one-shot's artifact rides its
  runtime entry (`runtime[op_id].master`). Both rehydrate; a corrupt artifact leaves
  the session cold rather than bricked.
- `GET /api/director/sessions/{sid}/master` — the package's artifact, else the ready
  draft's, else the newest one-shot's; `GET /api/director/ops/{id}` carries `master` for one-shots;
  `GET /api/director/master/template` — what the brain is told; the board payload
  carries `master` and `draft_stage`.
- UI: the draft message and the board's Script tab show the artifact under a
  "master prompt — the pre-split finalized artifact · words · beats · plays ~N s of
  your M s · brain|template" disclosure with the derived fields; a deriving one-shot
  card pulses "deriving master prompt…" with Run disabled until the artifact lands;
  the draft status reads "deriving the master prompt…" then "writing the screenplay
  from it…".

## 6. Tests

`apps/director/tests/test_master_prompt.py` — the template reaches the brain
verbatim; the offline structure; beat budget by duration; reference opener; authored
elements preserved; strict parse + optimizer (dedupe, ending, pacing, constraint
trim, idempotence); named failure; a screenplay-shaped reply refused; stills as
pixels; one-shot bodies on both routes; lock hardening on the body; the duration note
and the cap; the async hold, release, and named failure; the draft brief; per-clip
inheritance with the ending on the last clip only; route neutrality; no-artifact
parity; provider limits; state.json round trip; restart release; the API surfaces.
`apps/director/tests/test_likeness.py` — the likeness guard (§3a): every form of a
name, possessives, sentence case, cue lines, aliases, a descriptor that names the
person refused, candidates, the confessed path, the audit path, audit failure as a
warning, the switch, byte-identical parity, the shippable body, a revision note, a
screenplay character relabeled with its speakers, master-record inheritance.
Existing brain fakes that serve a screenplay now also serve a master reply when the
system prompt is the template (`conftest.master_brain_body`, `is_master_call`), as
the real brain does.
