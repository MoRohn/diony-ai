# Blueprint 13 — Idea refs, clip edits, version ladder

Status: Day 2  
Producer is a cutting room. Ideas arrive as words and as files. Feedback on a take is an **edit of that take**, not a new generation from a blank prompt.

## Idea refs (cutting bench)

A Brief may be words, files, or both.

| Kind | MIME / suffix | Role |
|---|---|---|
| Image | jpeg, png, webp | I2V keyframe / character still |
| Video | mp4, mov, webm | motion reference (Animate) |
| Document | pdf, txt, md, docx | treatment, lyrics, VO, brand notes |

Rules:

- Idea text may be short when `attachment_ids` is non-empty.
- `.txt` / `.md` extract into the bible world. Other documents keep filename until BRAIN-mode OCR/parse.
- Uploads land in the vault (`apps/producer/data` on a laptop; `/diony/vault` on cluster). Never cloud.
- Cast the studio copies bench assets onto `Project.assets` and wires image ids into `Shot.keyframe_asset_ids` / character stills.

## Clip edit (mark the print)

Showrunner language: **mark the print**. The API is `POST /api/takes/{id}/edit`. The Dailies floor labels this **Prompt to edit**. Optional in/out frames scope VACE to a range. Skipping the instruction is allowed — stitch still uses the selected print.

That call must:

1. Interpret the instruction (preserve identity, wardrobe, lighting, camera, duration unless the instruction relaxes one).
2. Route to **Wan2.2-VACE-Fun-A14B** (Animate only when the shot asked for motion transfer).
3. Create a **child Take** (`origin=edit`, `version=parent+1`, `lineage` includes the parent). Never overwrite the parent negative.
4. Select the child as the current print. Parent stays on the ladder.
5. Record a `ClipEdit` (instruction, VACE prompt, preserve list, optional in/out frames, optional stills).
6. Pin a human note on the new take.

Dry-run (Day 2): stub artifacts, real contracts. Week 2+: VACE on the parent 720 negative.

## Version ladder

Every Shot has a ladder of Takes ordered by `version`.

| Action | Meaning |
|---|---|
| Cut a new print | Child version from the selected take |
| Click an older rung | Restore that print (`selected_take_id`); do not delete children |
| Download a can | Serve `Artifact` bytes (720 negative, 1080 finish, proxy) |
| Picture lock | Refuses further edits. Unlock is a human gate. |

Caps: lineage length 8, then stop for a human (same spirit as max takes). Masters still require `finish=deliverable` and a 1080 artifact.

## Vault layout

```
assets/{asset_id}/{filename}
takes/{take_id}/negative_720.mp4   # live camera negative (laptop stub: .txt)
takes/{take_id}/finish_1080.mov    # 1080 take master after picture lock (`06`)
program/{timeline_id}/master_1080.mov  # overlap-stitched 1080 program master
```

Immutable: a Take’s artifact paths do not get reused for a later version. New version, new directory.

## Failure modes

| Symptom | Cause | Action |
|---|---|---|
| Empty bench and empty idea | Brief validator | 400; need words or a file |
| `.exe` on the bench | MIME allow-list | 400 |
| Regen instead of edit | prompt from scratch | VACE prompt says EDIT existing take; engine is VACE not T2V |
| Parent file gone after edit | overwrite | forbidden; child is a new Take |
| Edit after picture lock | gate skipped | 400 |
| Infinite versions | taste loop | cap 8 then human |
| Previz downloaded as master | wrong can | 1080 finish is labeled; ProRes still requires deliverable |
