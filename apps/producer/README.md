# Producer

The Finished Clips gallery and the cost ledger's plumbing. Generation happens in the Director; nothing here renders a frame.

```bash
pip install -e packages/contracts -e apps/producer
diony-producer            # http://127.0.0.1:8788
python -m pytest apps/producer
```

| Part | Job |
|---|---|
| `app.py` | The Gallery API: `/api/gallery` (every clip and film with its record: file, thumbnail, details, bulk delete, background ZIP, stitch), `/api/director-films`, `/api/cloud-production`, `/api/engine`; the correlation and token-auth middlewares |
| `gallery.py` | The Finished Clips inventory read from each Director session's own record in the vault; archive creation stores MP4s without recompression and includes a manifest |
| `launch_api.py`, `control_plane.py`, `costs.py`, `workflow.py`, `providers.py`, `worker.py`, `security.py` | Blueprint 16: the durable control plane and cost ledger the Director's receipts read, BYOK secrets, workflows, `/metrics` |
| `face_identity.py`, `quality.py`, `speech_transcript.py`, `semantic_qc.py`, `media_tools.py` | The review services the Director imports: YuNet + SFace identity, sampled-frame quality, whisper transcripts, CLIP adherence, ffmpeg discovery |
| `static/` | The console: the Finished Clips ten-across contact sheet with collapsed, expandable prompt rows; viewer and details; Select all / Clear all; one-ZIP download; and two-step bulk delete; bump `?v=` on every UI change |
