# Studio

The product's front door. `GET /` serves the opening page: two cards, **Grok Imagine** and **Google Flow**, each with a chip that says whether its key was found in `~/.diony/keys.env`. Choosing one persists to `{vault}/.cluster/engine.json` (`POST /api/opening/engine`) and hands the user to the Director. One camera shoots the whole film; there is no automatic route.

```bash
pip install -e packages/contracts -e apps/wizard
diony-wizard              # http://127.0.0.1:8787
python -m pytest apps/wizard
```

The normal product launcher starts Studio with Director and Producer through
`scripts/run_local.py`. `DIONY_STUDIO_URL` overrides the browser-facing URL.
