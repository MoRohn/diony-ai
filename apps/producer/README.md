# Producer

The Gallery and the launch control plane. Generation happens in the Director; nothing here renders a frame.

```bash
pip install -e packages/contracts -e apps/producer
diony-producer            # http://producer.diony.local (binds :8788)
python -m pytest apps/producer
```

| Part | Job |
|---|---|
| `app.py` | The Gallery API: `/api/director-films` (list with receipts, stitch, delete, serve), `/api/cloud-production`, `/api/engine`; the correlation and token-auth middlewares |
| `launch_api.py`, `control_plane.py`, `costs.py`, `workflow.py`, `providers.py`, `worker.py` | Blueprint 16: workspaces, members, invitations, Cost Centers, BYOK secrets, durable workflows, the cost ledger the Director's receipts read, `/metrics` |
| `studio_invites.py`, `studio_email.py`, `studio_entry_api.py`, `security.py` | Blueprint 18: invitations, verification, the join surface, signed tokens |
| `face_identity.py`, `quality.py`, `speech_transcript.py`, `semantic_qc.py`, `media_tools.py` | The review services the Director imports: YuNet + SFace identity, sampled-frame quality, whisper transcripts, CLIP adherence, ffmpeg discovery |
| `static/` | The console: the Director films inventory and the Studio governance drawer; bump `?v=` on every UI change |
