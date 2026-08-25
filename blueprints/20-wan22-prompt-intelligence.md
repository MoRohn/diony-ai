# Blueprint 20 — Wan2.2 Prompt Intelligence

Status: production contract

Owner: Diony AI Producer + Conductor

Applies to: Idea intake, Shot contracts, on-site DeepSeek refinement, Wan FARM,
AutoQC retakes, artifact lineage

Technique input: [Wan 2.2 Prompt Guide](https://wan27.org/blog/wan-2-2-prompt-guide).
Diony adopts its useful layered prompt discipline while retaining the stricter
quality, privacy, reference-lock, continuity, and 1080-finish rules in Blueprints
10, 12, and 19.

## Product decision

The maker writes the Idea. They do not become a prompt engineer.

Producer autonomously compiles every Shot into a persisted `WanPromptPlan`:

1. **Subject** — the identity signal and stable visual anchors;
2. **Motion** — action with pace, direction, and body/environment mechanics;
3. **Camera** — one framing, one compatible move, focal length, and T-stop;
4. **Aesthetic / lighting** — world, motivated light, color grade, physical
   texture, atmosphere, and reference-medium ownership;
5. **Environment dynamics** — subtle secondary motion and parallax that give the
   frame life without competing with the subject.

The rendered order is binding: Subject → Motion → Camera → Aesthetic/Lighting →
Environment Dynamics. Wan receives 80–140 words. Earlier layers own the
higher-priority constraints. Adding more prose after the attention budget is not
quality.

The plan separately records high-noise intent (layout, spatial composition,
primary motion, camera path, parallax) and low-noise intent (identity, hands,
wardrobe/material detail, lighting continuity, clean edges, motion blur). Those
fields describe the dual-expert quality objective; they do not replace or merge
the official high-noise and low-noise A14B weights.

## Autonomous path

```text
Idea + references + optional explicit Scene plan
  → deterministic local scene parser and five-layer compiler
  → structural diagnostics (no GPU work)
  → optional typed DeepSeek refinement on the signed site
  → identity/camera locks reapplied programmatically
  → dispatch validation
  → Wan2.2 A14B 1280×720 quality-lane negative
  → AutoQC diagnosis
       known prompt failure → repair only the failed layer → immutable retake
       unknown failure      → stop with evidence for a human
  → mandatory Diony 1920×1080 finish
```

This runs behind the existing Produce motion. There is no advanced prompt form
on Idea and no internet prompt-expansion service. A browser refresh or process
restart resumes from the persisted plan and receipt.

A stopped pre-v2 Project does not replay its stale Take receipts. The explicit
same-Project recalibrated restart upgrades the full rundown to the current
reference binding and prompt strategy, preserves old Takes as audit history,
and assigns newly seeded immutable Takes before FARM starts. The first Shot is
reference-led I2V; later Shots are previous-frame I2V while retaining the exact
subject reference asset as independent identity evidence.

The guide proposes cheap low-resolution subject/motion experiments. Diony uses
the same principle as a zero-GPU structural preflight, then retains its pinned
five-second 720p A14B camera-negative lane. It does not add an untracked 480p
artifact or weaken the 1080 deliverable contract.

## Typed contract

`Shot.prompt_plan` persists:

- `strategy_version` (`wan22-five-layer-v3`);
- SHA-256 of the normalized Idea and attached-reference text used by the planner;
- two to four identity anchors;
- subject, motion, camera, aesthetic/lighting, and environment-dynamics layers;
- high-noise and low-noise intent;
- actual generation method and audio strategy;
- applied qualified quality-LoRA IDs and known continuity risks;
- ordered negative-conditioning terms;
- rendered word count;
- diagnostics, which must be empty before dispatch.

`Shot.prompt` and `Shot.negative_prompt` remain the actual Wan inputs. Before
camera dispatch, Producer reconstructs both from `prompt_plan` and refuses any
drift. Legacy Projects without a plan remain readable; a new Idea always receives
the typed plan.

The immutable prompt receipt hashes the positive prompt, negative prompt, and
complete typed plan. Changing an exclusion or strategy version invalidates the
old negative/finish lineage just as changing positive text does.

## Subject and reference rules

- A sequence repeats the same subject layer verbatim on its first camera pass.
- The subject layer names at least two stable anchors: face/design, silhouette,
  proportions, wardrobe, or supplied identifying details.
- A binding still is the literal subject. The plan says `exact supplied subject`
  and preserves original rendering medium, palette, and proportions.
- Explicit ordinals such as `first attached image` and `last attached
  illustration` bind that exact attachment before any generic first-image rule.
  The same resolution is reapplied when an older persisted Project is retaken.
- `character` reference lock preserves the subject while allowing the authored
  Scene to place it in a new Bible-locked environment. It excludes presenters,
  framed art, screens within screens, and medium changes, but does not forbid the
  planned world.
- `character_and_background` additionally preserves the supplied composition,
  background, palette, camera, and ambient motion. It excludes new backgrounds,
  title cards, cutaways, and palette shifts.
- Generic live-action cleanup must never add `cartoon` to a binding reference's
  negatives. The supplied image owns its medium.

## Script-to-clip mapping

An explicitly authored `Scene N — Title (start–end)` block is a production
instruction, not decorative Idea prose. Producer parses its title, visual action,
time weight, and Narration locally. Shot slots are allocated in script order and
weighted by authored scene duration. Each Shot receives that Scene's action in
its Motion layer and the first Shot for that Scene carries its Narration line.

When an exact attached subject leads the sequence, every camera Shot uses the
I2V-A14B quality lane with that subject reference. Shot one starts from the
reference; later Shots use the selected prior Take's final frame as the incoming
continuity handle. This keeps identity and frame boundaries explicit while also
avoiding unnecessary T2V/I2V task churn on a sequential single-GPU site.

Without an opening still, a long sequence uses one T2V-A14B establishing segment,
then 4–7 second I2V-A14B segments conditioned from each selected prior Take's
last usable frame. Producer plans the whole ordered rundown before FARM. The
handoff retains the incoming frame, identity anchors, lighting language, color
grammar, and motion direction; stitch then color-matches and smooths the bounded
segments.

Narration, dialogue, singing, or lipsynced intent creates a mandatory
VoiceProfile/PerformanceScript job before camera. It routes to S2V only after the
Shot owns a verified processed clean-dialogue Asset, a reference image or selected
prior frame, and the signed site has the qualified S2V runtime. The native request
passes the image/frame anchor and locked audio; it never substitutes T2V. Final
Mix assembles the verified clip mix and never generates narration after picture.
Score and non-speech sound remain versioned Mix stems. Animate likewise requires
an exact character still plus the official preprocessed motion source root; an
unprepared video may not be mislabeled as Animate conditioning.

## Motion rules

- A generic action word is not dispatchable. Motion names a pace, a direction,
  and at least one physical mechanic such as a step, weight shift, hand action,
  eyeline, head turn, breath, or environmental motion.
- Motion is sequential enough to sustain the full approximately five-second
  take and ends in a usable hold or handoff.
- Prefer measured motion. Fast action is decomposed into readable mechanics
  rather than requested as an undirected blur.

## Camera rules

- Exactly one movement family: static, pan, tilt, push-in, pull-back, track,
  crane, handheld, steadicam, or optical zoom.
- Framing, focal length, and T-stop match the typed Shot fields.
- Contradictory pairs such as static + push-in, wide + extreme close-up, or drone
  + close-up are rejected before GPU dispatch.
- Even no movement is explicit: `static camera with no movement`.

## Aesthetic, light, texture, and environment rules

- Aesthetic/Lighting follows Camera and names a motivated light source or ratio.
- Physical texture prevents clean plastic defaults: fabric, skin/material detail,
  atmospheric depth, reflections, grain, weather, or reference-medium texture.
- Environmental motion supports depth without competing with subject motion.
- `character_and_background` keeps a single composition, background, palette,
  medium, and first/last-frame handoff.
- `character` may enter the authored Scene world while its identity, silhouette,
  proportions, wardrobe, and original medium remain locked.

## On-site DeepSeek planning

Planning is batched and visible, never one silent call per shot. At roll,
Producer runs one brain health check, then:

1. a **bible pass** (skipped once the Bible is human-locked) that authors the
   project's own logline and a concrete project-specific visual grammar in
   place of the canned playbook sentence;
2. **draft batches** of up to 8 shots per chat call, each carrying the Bible
   context once plus the five typed layers, identity anchors, and lock flags
   per shot — with an explicit instruction to vary phrasing across the batch;
3. one **critique pass** per productive batch, auditing metadata leakage,
   contradiction with the camera lock and negative conditioning, temporal-beat
   motion, texture, and cross-shot repetition, returning revisions for only
   the layers that failed.

Requests use Blueprint 04 agentic sampling (`temperature 1.0`, `top_p 0.95`),
wire-level `reasoning_effort`, and output budgets sized to the batch. Serving
commands pin `--served-model-name DeepSeek-V4-Flash-0731` so the client model
id resolves without an env override.

Programmatic locks win over model prose:

- camera is copied from the local plan, not accepted from the model;
- subject may be sharpened on unlocked shots only, and every identity anchor
  phrase must survive verbatim, or the local subject stands;
- motion, aesthetic/lighting, and environment dynamics may be enriched;
- the entire candidate must still pass word-budget, motion, camera, lighting,
  and texture checks;
- malformed, weak, contradictory, over-budget, unavailable, or cloud-routed
  output falls back atomically to the deterministic local plan — and the
  fallback is a production activity line, never a silent downgrade.

Reference-locked Shots do not accept generative refinement. Literal ownership
of the supplied pixels is more important than decorative prose.

## Negative conditioning

The default exclusion set covers blur, low quality, anatomy/limb errors,
morphing and inconsistent faces, identity drift, flicker, teleporting props,
frozen motion, text/logo/watermark, JPEG artifacts, plastic/airbrushed texture,
unwanted cuts, illegal broadcast levels, and 720p-as-finished appearance.

The required core terms explicitly cover morphing, warping, face deformation,
identity change, flickering, jitter, static motion, blur/low quality, extra
limbs, distorted hands/fingers, sudden cuts, and teleports.

Camera-specific exclusions suppress unwanted movement on static shots and
random jumps/pans/zooms on moving shots. Reference locks append their strict
medium, framing, background, and identity exclusions. These terms reach Wan's
true negative-conditioning input through the runtime contract.

## AutoQC layer repair

Retake revision is evidence-driven first, keyword-mapped second. When the brain
is reachable, the full QC evidence bundle — failure notes, measured values,
the five typed layers, negative conditioning, and what earlier attempts already
tried — goes to the DeepSeek retake director, which may revise multiple layers
at once and add up to four negative terms, addressing **every** reported
symptom rather than only the first matched keyword. The directive is applied
through the same plan validators; camera stays locked; subject changes require
every identity anchor verbatim and never apply on reference-locked Shots. The
applied directive and its rationale are activity lines. Offline or invalid
direction falls back to the keyword table:

| Evidence | Repair |
|---|---|
| identity, morph, or reference replacement | strengthen subject anchors and identity negatives |
| frozen or stopped motion | replace Motion with sustained pace, direction, mechanics, and completion |
| camera jump or surprise reframe | keep the selected camera and require one uninterrupted take |
| flat/flickering lighting | stabilize Scene lighting and exposure |
| plastic/painted surface | add physical material texture and natural imperfection |
| boundary continuity | replace Motion with an explicit prior-motion handoff |

Every revision is applied **after** the reference-contract refresh and survives
to dispatch: a refresh that would not change the shot contract preserves a
current, dispatch-valid plan instead of recompiling over the repair. Retake
seeds jump to a distant deterministic draw instead of an adjacent one. Every
repair creates a new Take and a new complete prompt receipt. The four-Take cap
remains binding. Unknown failures do not trigger creative prompt churn.

Measured AutoQC also requires non-frozen temporal motion, temporal coherence,
stable lighting, camera cleanliness, media integrity, reference identity,
boundary continuity, and an honest audio-ready strategy. Known measured motion,
coherence, or flicker failures repair the diagnosed layer and mint an immutable
retake under the same four-generation cap.

## Qualified quality adapters

LoRAs are part of the immutable generation receipt only when the prepared site
explicitly qualifies the exact adapter and backend path. The official native
runtime currently accepts `wan2.2-animate-relighting` for Animate through
`--use_relighting_lora`; set `DIONY_WAN_QUALIFIED_LORAS` only after local output
and license qualification. Arbitrary downloaded realism, face, motion, lighting,
Lightning, or speed adapters fail closed. No empty `quality_lora_ids` list is
described as an applied LoRA.

## Acceptance

- Every new Idea Shot has a valid typed plan and empty diagnostics.
- Explicit Scene blocks survive as ordered, scene-specific Shot actions and
  Narration lines rather than collapsing into generic coverage.
- An ordinal reference resolves to the same attachment on first expansion and
  on retake upgrade.
- A reference-led sequence uses I2V for every Shot and prior-final-frame
  continuity after Shot one.
- Narration text schedules the mandatory VoiceProfile/performance/audio job and then S2V when an image or prior-frame anchor exists.
- Recorded on-camera speech or singing plus a still schedules S2V and carries
  both media inputs through the durable job record.
- A sequence longer than eight seconds is assembled from bounded segments; after
  the first print, every segment uses previous-final-frame I2V continuity.
- Every first-pass prompt is 80–140 words in the five-layer order.
- Every Subject contains two to four persisted identity anchors.
- Every plan separates high-noise layout/motion intent from low-noise
  detail/identity intent.
- All sequence Shots share one initial subject layer.
- Every Motion has pace, direction, and mechanics.
- Every Camera has one non-contradictory move, framing, lens, and T-stop.
- Every Scene has lighting plus physical or reference-medium texture.
- DeepSeek cannot rewrite identity or camera facts.
- Camera refuses plan/text drift before invoking Wan.
- Positive, negative, and strategy changes produce different lineage receipts.
- A diagnosed retake repairs one layer and remains valid.
- No prompt, reference, or frame leaves the signed site.
