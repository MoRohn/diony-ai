# Producer

The Finished Clips gallery and the cost ledger's plumbing. Generation happens in the Director; nothing here renders a frame.

```bash
pip install -e packages/contracts -e apps/producer
diony-producer            # http://producer.diony.local (binds :8788)
python -m pytest apps/producer
```

| Part | Job |
|---|---|
| `app.py` | The Gallery API: `/api/gallery` (every clip and film with its record: file, thumbnail, details, delete, stitch), `/api/director-films`, `/api/cloud-production`, `/api/engine`; the correlation and token-auth middlewares |
| `gallery.py` | The Finished Clips inventory read from each Director session's own record in the vault |
| `launch_api.py`, `control_plane.py`, `costs.py`, `workflow.py`, `providers.py`, `worker.py`, `security.py` | Blueprint 16: the durable control plane and cost ledger the Director's receipts read, BYOK secrets, workflows, `/metrics` |
| `face_identity.py`, `quality.py`, `speech_transcript.py`, `semantic_qc.py`, `media_tools.py` | The review services the Director imports: YuNet + SFace identity, sampled-frame quality, whisper transcripts, CLIP adherence, ffmpeg discovery |
| `static/` | The console: the Finished Clips grid, viewer and details; bump `?v=` on every UI change |
