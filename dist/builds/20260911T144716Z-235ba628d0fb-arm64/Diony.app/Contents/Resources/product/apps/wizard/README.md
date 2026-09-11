# Studio

The product's front door. `GET /` serves the opening page: two cards, **Grok Imagine** and **Google Flow**, each with a chip that says whether its key was found in `~/.diony/keys.env`. Choosing one persists to `{vault}/.cluster/engine.json` (`POST /api/opening/engine`) and hands the user to the Director. One camera shoots the whole film; there is no automatic route.

`gateway.py` is the `:80` product-name router: it writes `studio`, `director`, and `producer` `.diony.local` into the hosts file once (with the one-time password prompt) and routes on the Host header. The desk and every surface depend on it.

```bash
pip install -e packages/contracts -e apps/wizard
diony-wizard              # http://studio.diony.local (binds :8787)
python -m pytest apps/wizard
```
