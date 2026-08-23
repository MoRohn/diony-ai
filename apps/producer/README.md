# Producer

Idea-to-Studio floor. Customers: feature, series, commercial, music video, education, social hero.

First open: **START HERE.txt**, then double-click **Open Diony AI** in the repo folder. START HERE names the crate, or a lease from Runpod then Enverge. Then:

```bash
pip install -e packages/contracts -e apps/wan -e apps/finish -e apps/producer
# Laptop without weights: stubs
diony-producer   # http://producer.diony.local
# Dual-Spark spark-01 after Studio App Continue (weights in /models, Wan2.2 in /opt/wan22)
export DIONY_VAULT=/diony/vault
export DIONY_BIND=0.0.0.0
export DIONY_WAN_REPO=/opt/wan22
export PYTHONPATH=/opt/wan22${PYTHONPATH:+:$PYTHONPATH}
export DIONY_PYTHON=$(command -v python3)
unset DASH_API_KEY DASHSCOPE_API_KEY
diony-producer
```

Sequential Dual-Spark: do not start `diony-wan` and brain together. Produce starts Wan for FARM. Production starts `diony-finish` for 1080.

Pages: **Idea** → **Production** → **Gallery**. Idea has a **Length** goal: 30s, 1m, 3m, 5m, 10m, 15m, 20m, 30m, 40m, 1hr (default 5m). That is a program TRT goal, not wall-clock. Each take stays on the Wan **5s quality lane**. **Sample** uses the current idea and attached references to make a selected 5s or 10s clip through the same camera, AutoQC, 1080 finish, mix, and verified Gallery path. Producer compiles Idea input autonomously into typed 80–140 word Wan plans in Subject → Motion → Camera → Aesthetic/Lighting → Environment Dynamics order; on-site DeepSeek may enrich motion, lighting, and environment dynamics but cannot rewrite subject or camera locks. Sequences longer than eight seconds use bounded segments and previous-final-frame I2V after the opening print. After Studio Sign the mast shows the site. Dual-Spark / B300 / B200 are sequential SPLIT — do not quote the Quad-Spark 2–5 day SLA. **Produce** farms Wan2.2 **720p negatives** (not 1080 masters) and opens Production. On a signed lease, Wan and `diony_sr_v1` run on GPU Producer via the Connect SSH line — not `/models` on this desk. Production then writes `finish_1080.mov` per take and `master_1080.mov` for the program or Sample, then mix. **Gallery** lists those finished clips. **Retake** cap 4. See Blueprints `04`, `06`, `12`, `13`, `14`, and `20`.

Production also has a durable **STOP** control. Its modal offers **Keep** (save
QC-passed completed clips as a stitched 720p incomplete result) or **Delete**
(remove generated media in the current Project namespace). Both choices stop
future negative generation and survive Producer restarts.

`POST /api/production/restart` is the explicit same-Project recalibration path
after STOP. It retains the old Takes and incomplete-result receipt, recompiles
the current script/reference contract, creates a fresh Take for every Shot, and
starts FARM. It never relabels an old negative as output from a newer prompt or
reference strategy.

`POST /api/production/pause-after-current` writes a durable Take-boundary drain
receipt; restart and farm recovery must honor it before dispatch. Polling
`GET /api/project` is read-only. An existing vault Project is selected only by
`POST /api/projects/{project_id}/activate`. When a service interruption writes
a STOP during a restart, `POST /api/production/recover-infrastructure` records
that receipt, clears the interruption, and reuses the same selected Take IDs and
seeds without consuming a creative retake attempt.

Projects persist under `{vault}/projects/{id}/project.json`. Sample uses the live Idea and reference bench; `/api/reset` remains a development fixture only. 720p Wan is camera negative. 1080 is Diony finish.

When Producer reaches a remote `diony-wan`, camera work uses the durable
`POST /v1/jobs` contract and polls the Diony-owned job record through terminal
state. A ComfyUI `prompt_id`, node error, or restart recovery therefore remains
attached to the shot run; Producer never promotes an HTTP acknowledgement into
a landed 720p negative. The existing finish/mix path starts only after the
negative file is verified on the shared vault.

## Launch control plane

Producer also exposes `/api/v1` for durable multi-workspace production. The controller database is `{vault}/.control/diony.sqlite3`; it stores workflow checkpoints, generation jobs, replayable progress, measured usage, Team/Department/Cost Center allocations, and audit history. Internal credits are workload-analysis units, never a sold balance or production gate. Media remains in the vault. `GET /api/v1/workflows/{run}/events/stream` is the live SSE rail and `/metrics` is the authenticated Prometheus scrape target.

Run `diony-control-worker` beside Producer to advance queued runs one persisted stage at a time. Cross-process run leases prevent duplicate dispatch. Brain, FARM, Finish, and packaging attach their real output proof at `POST /api/v1/workflows/{run}/stages/{stage}/checkpoint`; without that proof a required local stage remains `waiting_provider` and no media is invented.

Workspace owners can manage teams and memberships through `/api/v1/teams` and `/api/v1/members`. Project erasure is deliberately two-step: inspect `/api/v1/projects/{id}/erasure-plan`, switch away if it is active, then post the exact confirmation to `/api/v1/projects/{id}/erase`. Shared media is retained; controller lineage, local files, exports, and configured mirror objects are erased with an audit receipt.

Owned single-controller installs default to `DIONY_AUTH_MODE=local`. Team deployments set `DIONY_AUTH_MODE=token`, a 24+ byte `DIONY_AUTH_SECRET`, and persisted memberships. BYOK values are AES-256-GCM encrypted; set a base64 32-byte `DIONY_KEY_ENCRYPTION_KEY` from secret management in shared/production installs.

## Studio invitations and email

The Producer `Studio` drawer lets the configured Admin invite multiple members,
approve governed joins, create and name shared Cost Centers, and assign each
member to one or more centers. Acceptance verifies the invited email, records a
unique Profile Name, creates the personal Cost Center atomically, and opens an
authenticated Producer session. Invitation secrets are carried in the URL
fragment; opening or scanning a link never accepts it.

For a real delivery service, configure SMTP before starting Producer:

```bash
export DIONY_EMAIL_TRANSPORT=smtp
export DIONY_EMAIL_FROM='Diony AI Studio <studio@example.com>'
export DIONY_EMAIL_REPLY_TO='studio@example.com'
export DIONY_SMTP_HOST='smtp.example.com'
export DIONY_SMTP_PORT=587
export DIONY_SMTP_USERNAME='...'
export DIONY_SMTP_PASSWORD='...'
export DIONY_SMTP_STARTTLS=true
export DIONY_PRODUCER_PUBLIC_URL='https://producer.example.com'
```

`DIONY_EMAIL_TRANSPORT=capture` writes private `.eml` acceptance evidence under
`{vault}/.control/mail-capture` for local testing. `disabled` is the safe default:
the invitation is retained, but its delivery record visibly fails instead of
pretending an email was sent. Production domains should publish SPF, DKIM, and
DMARC through their mail provider.

Cost Center budgets are monitor-only. Makers see assigned-team totals, Department managers see managed departments, and only the configured cost-admin account/team can read `/api/v1/costs/organization`. Versioned rate cards and integer USD micros keep model, provider, energy, storage, and infrastructure attribution reproducible and reconcilable.

Object storage is an explicit adapter, never a production dependency:

```bash
# Optional AWS S3 / Cloudflare R2 mirror
export DIONY_OBJECT_BUCKET=...
export AWS_ENDPOINT_URL=...       # omit for AWS S3; R2 uses its account endpoint
export AWS_REGION=auto            # R2; use the AWS region for S3
export AWS_ACCESS_KEY_ID=...
export AWS_SECRET_ACCESS_KEY=...
```

`POST /api/v1/projects/{id}/export` writes a verified creator-owned `.diony.zip`. Secrets and controller keys are excluded. Full contract and recovery table: Blueprint `16`.
