# Director

The primary Diony surface: a chat-first filmmaker that turns one line into a priced, reviewed, narrated film on Grok Imagine (xAI) and Google Flow (through useapi.net). Blueprints 25–33.

```bash
pip install -e packages/contracts -e apps/producer -e apps/director
diony-director            # http://127.0.0.1:8790 (DIONY_DIRECTOR_PORT)
diony-director-check      # readiness report; --live probes; --roll-one grok|flow --yes; --stage-face-models; --reconnect
python -m pytest apps/director
```

Keys load from `~/.diony/keys.env` (`DIONY_KEYS_FILE`): `XAI_API_KEY`, `USEAPI_TOKEN`, `GEMINI_API_KEY`, `DIONY_FLOW_EMAIL`, `DIONY_FLOW_PLAN`. Flow captcha solver keys are configured directly on the useapi.net account; `diony-director-check --live` verifies masked solver readiness without spending. The full variable reference is in the root [README](../../README.md#environment-variables).

## Layout

| Module | Job |
|---|---|
| `app.py` | FastAPI service and every endpoint; the run guards (engine, dry run, provisional prompt) |
| `session.py` | Room state, sequencing (bible first, then chains), the submitter and advance workers, persistence (`state.json`) |
| `screenwriter.py` | The screenplay brain (OpenAI-compatible; template writer when no brain) |
| `master_prompt.py` | The Master Video Prompt stage (Blueprint 27) |
| `splitter.py` | Beat packing into route-length takes (Blueprint 33) |
| `compiler.py` | Three prompt dialects, the priority fit to the 4096 limit |
| `capabilities.py` | What each model accepts; refused before submit |
| `providers/` | `grok.py`, `flow.py`, `base.py` (download-and-hash, quota errors, prompt limit); module-level `httpx` is the test seam |
| `ratecards.py`, `ratecards/*.json`, `estimate.py` | Pricing as documents; locked estimates |
| `ledger.py` | Forecasts, actuals, receipts through the Producer's `CostAnalyticsService` |
| `review.py`, `revisions.py` | Review families and the fix ladder (Blueprint 26) |
| `assembly.py`, `narration.py`, `deliverables.py` | The cut, the post narrator, the sidecars (Blueprints 28, 30, 31) |
| `artifacts.py`, `cast.py`, `likeness.py`, `look.py` | Plates and sheets, Cast Lock, the likeness guard, the Look Bible |
| `flow_lexicon.py`, `grok_lexicon.py`, `styles.py` | Prompt lexicons and production styles |
| `live_check.py` | The `diony-director-check` CLI |
| `static/` | The console (`index.html`, `app.js`, `app.css`); bump `?v=` on every UI change |

## Rules

- Nothing slow rides a request: brain calls and submits run in workers and are polled.
- No priced op without a reconciling estimate; pricing never in code.
- Every per-op error is named on the op. Never `window.confirm`.
- The suite runs offline; `DIONY_DIRECTOR_DRY_RUN=1` makes a host refuse every paid run.
- At the Cast/BIBLES gate, **Use your image as anchor** accepts PNG, JPEG, or WebP (10 MB max) for one character. Diony stores and hashes it locally, then redraws that character's derived sheet from the supplied face before approval.
- **Use existing Flow character** lists the relay-visible Flow character library and also accepts a Google Flow character page URL or API reference; it verifies the choice against `DIONY_FLOW_EMAIL` and binds the account-scoped reusable character reference to every compatible clip for that role without creating another Flow asset.
