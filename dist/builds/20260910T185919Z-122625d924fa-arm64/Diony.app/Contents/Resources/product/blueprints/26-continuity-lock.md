# Blueprint 26 — The Continuity Lock

Status: in force since 2026-08-31; the production-package contracts and the Director's plan → shoot → review → fix machinery. Revised by Blueprints 27 (master prompt), 29 (Look Bible), 31 (production style), 32 (Cast Lock), 33 (long takes)

Owner: Diony AI

Applies to: `packages/contracts/production.py`, `diony_director.{screenwriter,splitter,compiler,packagebuilder,review,revisions,assembly,artifacts}`, the Director's bible endpoints, state.json

Depends on: Blueprint 25 (Director pivot), Blueprint 24 (montage intelligence)

## Product decision

Cloud clips are independent generations. Nothing about a second call remembers the
first: not the face, not the room, not the light, not the voice. A film is a hundred
of them. The Continuity Lock is the set of contracts and mechanisms that make a
hundred independent clips read as one film, and the doctrine for what to do when one
does not.

## 1. The data model

All in `diony_contracts.production`, schema-exported.

| Contract | Holds |
|---|---|
| `Script` | the locked screenplay: `Scene`s of typed `ScriptLine`s (dialogue, narration, action, `off_screen`), a lock gate — nothing prices until the script is locked |
| `CharacterProfile` | name, description, wardrobe, `hero_stills` (anchor and board slots), `grok_ref_pack`, `flow_media_ids`, `identity_card` (Blueprint 32), `approved` |
| `SettingProfile` | name, description, `plate_asset_id` (the establishing still every scene in it opens from) |
| `VoiceProfile` | a character's voice: a locked provider voice id or the narrator's cast read |
| `ClipUnit` | one take: scene, beats, the authored shot, `natural_s` (paced) and the booked grid duration, provider and model, `AudioPacket` (voicing, the line, `line_at_s`), `ContinuityLock` (recipe, references, plate, previous clip), `PromptVersion`s (append-only) |
| `ClipReview` | the verdict per family with issues, the fix planned |
| `AssemblyMap` | what the cut actually did: order, trims, fades, mutes, narration cues |
| `ProductionPackage` | the priced whole: clips, bible ops, allowance, `production_style`, `estimate` |

## 2. Lock recipes

The `ContinuityLock` on a clip names how its identity is held:

| Recipe | Mechanism | When |
|---|---|---|
| `still` | the clip opens on a still (`start_image`): a setting plate for the first clip of a scene, the previous clip's last frame for every later one (a bridge frame extracted at `-0.2 s`) | every chained clip and every scene opener |
| `refs` | the character's reference pack rides the request (Grok: up to 3 `reference_images`; Flow: registered characters and `<IMAGE_n>` markers) | a character clip that does not open on a frame (frames and refs are exclusive on every model) |
| `style` | the Look Bible clause and a style frame (Blueprint 29) | every clip; a style frame prints at each scene change |
| `identity_card` | the verbatim `IdentityCard` at priority 0 (Blueprint 32) | Cast Locked sessions |
| `voice` | a locked provider voice id on Omni; the cast narrator otherwise | Dialogue and Mix styles |

Locks are late-bound at submit, never at split: the plate and the bridge frame do not
exist when the script is split, so `_late_bind_locks` recomputes the references and
extracts the bridge frame at dispatch, and the compiler reads the live setting, never
the snapshot.

## 3. Bibles before pictures

Locking a script plans the bible inside the lock:

1. **Plates.** One establishing still per setting (`plan_plate_ops`), registered on the
   setting as `plate_asset_id`.
2. **Character sheets.** A five-slot board per character (`SHEET_SLOTS`: front, three-
   quarter, profile, full, expression), reference-chained from the anchor, as priced
   image ops. Under Cast Lock the anchor and board are measured with YuNet + SFace
   and a failed slot is redrawn.
3. **The face gate.** No character clip rolls until a human approves the face
   (`POST …/bible/approve/{char}`, or `approve_all`). The cast dock (Blueprint 33) is
   where that happens.
4. **Registration.** Flow characters self-register from the approved stills (max 2
   images); Grok carries the ref pack per request.

Bible ops are sequenced first; scene chains wait for them. The lock reply's total is
clips + bible + allowance, exactly.

## 4. Splitting

The splitter turns a scene into takes (Blueprint 33 revised the unit from one line
per clip to beat-packed takes): the fewest takes the route's longest legal clip
allows (Grok 15 s, Omni 10 s), balanced, one spoken line per take, paced to the
scene's even share, quantized up. Dialogue routes to the audio-native lane under the
Dialogue and Mix styles; picture routes by the engine choice. Every take carries the
authored shot as a progression and `line_at_s` for the words.

## 5. Review families

Every completed clip is reviewed before it may enter a cut. Families are generator-
agnostic (they came from the Producer lane and are shared code):

| Family | Measures | Fail means |
|---|---|---|
| `delivery` | the file exists, decodes, has picture | provider delivered nothing usable |
| `duration` | booked length reached | a short print |
| `audio` | a track exists where one is expected | mute delivery |
| `frames` | sampled frames: black, flicker, dead picture | the picture died mid-clip |
| `identity` | SFace cosine against the approved sheet (≥ 0.363), never against the previous shot | drift |
| `speech` | whisper transcript on a location track that must be speechless | a provider-invented voice |
| `line` | transcript against the authored line on a spoken clip | mute or wrong words |
| `adherence` | CLIP prompt adherence when a CLIP model is staged | the picture is not the prompt |
| `finish` | an upscale against its take | a short or broken finish |

A family the host cannot measure reports `unmeasured`, and on a character clip that
caps the verdict at `warn`. Unmeasured is honest; it is never `pass`.

## 6. The fix ladder

`diony_director.revisions` plans the retake for a failed review, cheapest first, with
a per-clip hard cap and a memory of what already failed (`failure_key`):

```
edit  →  extend  →  regenerate from the same stills  →  new stills  →  provider swap
```

- `fix-audio`: the track is regenerated silent (or muted at the cut; Blueprint 28).
- `fix-line`: the line, clearly, once, and nothing else (Blueprint 31).
- A repeat of the same failure on the same provider swaps providers.
- A refusal (moderation, prominent person) redraws the opening frame without the
  recognisable face, then composes from the user's stills alone; three redraws and
  the retry refuses, naming the prompt as what the filter matches.
- A finish failure retries the finish alone; nothing is re-shot.

Retakes reopen the cut; the run does the cut again.

## 7. Assembly

`assembly.assemble()` cuts the reviewed takes in script order with a 0.15 s fade at
each join, a silent bed under any muted clip, a room-tone floor (−44 dB) that joins
after the loudness lock so it is never normalised up, `dynaudnorm` before `loudnorm`
at −16 LUFS (streaming) or −24 LUFS (broadcast), and an `AssemblyMap` that records
every decision. The speech guard runs before the cut (Blueprint 31). Narration is
mixed after it (Blueprint 28). The Flow 1080p finish lifts the raster when every
Flow take has one (Blueprint 30).

## 8. Pass criteria

- A chained film's first clip opens on its setting plate and every later clip on
  its predecessor's last frame (`plated > 0`, `bridged > 0`, pinned by test).
- Identity is measured against the sheet, never the previous shot.
- A clip that fails review never reaches the cut without a human Accept.
- The receipt names every retake and its reason.

## Failure modes

| Symptom | Cause | Fix |
|---|---|---|
| Faces change between clips | no sheet, or refs dropped because a frame was set | approve the sheet; Cast Lock; check the recipe |
| The film reads as eight visual languages | style carried in text only | Blueprint 29 |
| Two voices talk at once | a provider voiced the on-camera speaker under the narrator | the speech guard; Dialogue style if that voice is wanted |
| Every retake fails the same way | the failure key is not remembered | the ladder swaps provider on repeat |
| A scene opens black | the prompt scripted a black frame | advisory only; the chain rolls on |
