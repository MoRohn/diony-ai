# Blueprint 13 — Idea refs and legacy edit lineage

Status: Idea references active; Edit surface retired
Ideas arrive as words and as files. Producer exposes Idea, Production, and Gallery only.

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

## Retired Edit surface

Producer does not ship an Edit page, a Prompt-to-edit action, or
`POST /api/takes/{id}/edit`. The route must remain absent from the application
and the Producer catalog lists only `idea`, `production`, and `gallery`.

Existing Project files may contain historical `ClipEdit` and child-Take records.
Those records remain readable so old lineage is not corrupted, but Producer does
not create new ones. Immutable Take parents, selected-print history, QC receipts,
and artifact paths remain valid production evidence.

Retakes remain Production behavior and keep the existing four-attempt quality cap.
Gallery clip joining remains non-destructive composition versioning; it is not a
replacement Edit page.

## Vault layout

```
assets/{asset_id}/{filename}
takes/{take_id}/negative_720.mp4   # live camera negative (laptop stub: .txt)
takes/{take_id}/finish_1080.mov    # 1080 take master after Production finish (`06`)
program/{timeline_id}/master_1080.mov  # overlap-stitched 1080 program master
```

Immutable: a Take’s artifact paths do not get reused for a later version. New version, new directory.

## Failure modes

| Symptom | Cause | Action |
|---|---|---|
| Empty bench and empty idea | Brief validator | 400; need words or a file |
| `.exe` on the bench | MIME allow-list | 400 |
| Prompt-to-edit route responds | retired surface still mounted | fail release; route must be 404 |
| Historical child Take cannot load | legacy lineage discarded | preserve the record read-only |
| Previz downloaded as master | wrong can | 1080 finish is labeled; ProRes still requires deliverable |
