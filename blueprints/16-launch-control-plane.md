# 16 — Launch Control Plane and Creator Sovereignty

Status: launch constitution. This Blueprint governs durable state, provider boundaries, tenancy, internal cost analysis, export, and operations behind Studio and Producer. It does **not** change the Studio walkthrough, Producer floor, human gates, fleet profiles, or model pack.

## Product truth

Diony AI is an **Open Source Content Producer** in product spirit: maker-first, inspectable, portable, and hostile to creative lock-in. Every workspace owns its prompts, references, assets, generation history, and final masters. “Open Source Content Producer” describes the production system and creator rights; it does not silently replace the repository’s proprietary `LICENSE` or the upstream licenses in `NOTICE`.

The fixed production contract remains:

- Brain: `deepseek-ai/DeepSeek-V4-Flash-0731` on the signed site.
- Camera: Wan2.2 native 720p negatives.
- Finish: mandatory `diony_sr_v1` 1920×1080 Rec.709 deliverables.
- Human gates: Bible lock and Distribution. Picture and mix remain internal Production work.
- Sites: the four Studio choices and their existing profiles. Hosted providers are never a default or a failover for a missing signed site.

The implementation remains Python 3.11 + FastAPI because that is Diony’s packaged Day 1 runtime on laptops, DGX OS, and leased GPU machines. A TypeScript client may consume the generated JSON Schemas and `/api/v1`; a second server runtime may not become a competing authority.

## Authority and state

One Producer controller owns a signed site. `{vault}/.control/diony.sqlite3` is its launch control-plane database:

- SQLite WAL, full synchronous writes, foreign keys, busy timeout, and idempotent fixed-point cost allocation.
- Heavy media remains in the vault; model tensors and media bytes do not enter SQLite.
- JSON contract documents carry `schema_version`, `workspace_id`, timestamps, and soft-delete state.
- Events, usage measurements, cost allocations, and audit records are append-only.
- Every request and workflow has a correlation ID. Provider calls, usage, audit, and progress reuse it.
- The vault remains the recovery boundary. Copying the vault copies projects, controller state, install encryption key, and portable exports. Protect it like camera originals.

Launch entities are `User`, `Workspace`, `Team`, `Department`, `CostCenter`, `CostGovernance`, `CostRateCard`, `Membership`, versioned `Project`, `Shot`, `Take`, `Asset`, `WorkflowRun`, `GenerationJob`, `UsageEvent`, `CostAllocationEntry`, `AuditEvent`, `ProviderConfig`, and `RenderVersion`. All cross-workspace reads fail closed.

## Durable production run

The configurable order is:

1. Intake
2. Script
3. Breakdown
4. Consistency
5. Input safety and provenance check
6. Optional image
7. Video
8. Required voice identity/performance for spoken intent
9. Optional sound
10. Assembly
11. Finish
12. Output safety and rights check
13. Package

The guided Studio may request the default run. Producer may select stages, edit stage inputs, pause, resume, cancel, or retry. It may not reorder the production constitution. Each `advance` executes at most one durable checkpoint so a process restart never causes a completed stage to repeat. `diony-control-worker` advances eligible runs; a cross-process expiring lease prevents API and worker processes from dispatching the same stage twice.

For every stage:

1. Persist queued state.
2. Resolve a local provider and explicit fallback chain.
3. Resolve the run's Team, Department, and Cost Center dimensions before dispatch.
4. Persist running state and emit `workflow.stage.started`.
5. Record provider, exact model, attempt, external job ID, inputs, and outputs.
6. On success, append measured usage, internal credits, fixed-point cost components, allocation dimensions, and checkpoint lineage idempotently.
7. On failure, persist exponential backoff (`5s`, `10s`, `20s`, capped at `300s`) and actionable recovery choices.
8. After the retry limit, fail visibly. Never invent a successful asset.

Image and non-dialogue sound are optional until explicitly requested. Voice is optional only for a Project with no spoken intent; any narration, dialogue, singing, or on-camera speech makes VoiceProfile, studio-audio, performance metadata, S2V/AV QC, and per-clip package evidence required. Their absence is a visible hold, never a hidden hosted call or skipped stage. DeepSeek, Wan, assembly, finish, packaging, and requested voice are required production capabilities. The launch adapters checkpoint work owned by the existing Brain, FARM, Finish, voice/performance, and vault processes. Until an engine attaches `checkpoint_complete`, output asset IDs, and metadata through the stage checkpoint API, the run stays `waiting_provider`; neither the API nor worker synthesizes fake success or media.

## Provider boundary

Every adapter exposes identity, capabilities, exact models, local/remote status, health, priority, forecast internal credits, invocation, and cancellation. The default registry contains only:

- `diony.deepseek`
- `diony.wan22`
- `diony.assembly`
- `diony.finish`
- `diony.package`

BYOK adapters are opt-in workspace configuration. Keys use AES-256-GCM with workspace-bound authenticated data. Production uses `DIONY_KEY_ENCRYPTION_KEY` from secret management; an install-local `0600` master key exists for an owned single-controller studio. List APIs expose masks only. Exports exclude ciphertext, tokens, and controller keys.

## Cost Center governance and internal credits

Diony has no payment, checkout, subscription, balance, or production paywall in this control plane. Internal credits are a stable workload unit for comparing model and pipeline consumption; they are not currency and are never sold. Budgets are monitoring thresholds only. Crossing one marks a Cost Center `warning` or `over_budget`, emits operational visibility, and never blocks a production run.

Every usage event carries a Team, Department, Cost Center, project, run, job, capability, provider, exact model, quantity in integer microunits, internal credits, and a fixed-point USD cost breakdown. USD values use integer micros—not binary floating point—and reconcile from compute, energy, storage, network, license, and other components to a total. A measured provider result wins; otherwise the effective versioned rate card calculates the amount. Unrated usage remains visible as a data-quality exception instead of silently becoming zero-quality truth.

`CostAllocationEntry` is append-only and idempotent per usage event. Rollups must reconcile exactly from allocation → Cost Center → Department/Team → organization. The organization summary reports totals, distributions, budget utilization in basis points, rated-usage coverage, and a reconciliation difference that must remain zero.

Cost visibility is least-privilege. Makers see their assigned teams; Department managers see their managed departments. Only the explicitly configured Cost Center admin account or admin team can read organization-wide totals, distributions, rate cards, and rollups. Holding a generic application `admin` role alone does not grant that financial-operations scope.

## Media and delivery

The local vault is canonical. Assets record their vault path, portable storage key, thumbnails/proxies, source job, source assets, checksums, and metadata. Object keys use:

`workspaces/{workspace}/projects/{project}/v{version}/{category}/{filename}`

An optional Boto3 S3-compatible mirror supports AWS S3 and Cloudflare R2, uploads with content type and lineage metadata, and generates bounded signed GET URLs. Enable it only with explicit object-storage configuration. Losing an object mirror must not erase the vault original.

Retention is policy, never surprise deletion. A cleanup worker may remove expired proxies, thumbnails, or soft-deleted media only after a logged retention decision. Camera negatives and masters default to retained. GDPR erasure is a workspace-owner action with a preview plan and exact `ERASE {project_id}` confirmation. It refuses the active production, retains files referenced by another project, deletes configured S3/R2 objects before local files, removes vault media/exports/controller lineage, and leaves one hash-only erasure receipt. Export first when the creator wants an archive.

## API and live progress

The stable control surface is `/api/v1`:

- `session`, `providers`, `health`
- project inventory plus team and membership administration
- workflow create/list/get/advance/pause/resume/cancel/retry
- signed-site engine checkpoint attachment per workflow stage
- replayable event list and Server-Sent Events stream
- scoped usage and Cost Center summaries, plus organization rollups for the configured cost admin account/team
- encrypted provider-secret writes and masked catalog
- Department, Cost Center, and versioned rate-card administration
- project package export and authenticated download
- admin operations for stuck runs, queue depth, cost, and provider health

`/metrics` exports Prometheus text, including success rate, job latency, queue depth, internal credits, total allocated cost, unrated usage, and cost per generated video minute. Controller requests and workflow transitions emit redaction-safe JSON logs. Every response returns `X-Correlation-ID`. Event payloads name stage, percent, current action, provider/model, cost, output asset IDs, severity, and available recovery actions. The event table is the replay source when an SSE connection drops.

Local owned studios use `DIONY_AUTH_MODE=local`. Shared controllers use `DIONY_AUTH_MODE=token`, a secret of at least 24 bytes, expiring `diony1` HMAC tokens, and persisted membership roles: viewer, creator, producer, admin, owner. Control-plane requests are rate limited; deployment ingress remains the outer abuse-control layer. Legacy Producer endpoints stay on the existing signed-site rail until their UI calls migrate to `/api/v1`.

## Portable `.diony.zip`

An export contains:

- the complete versioned Project contract;
- workflow runs, generation jobs, progress events, usage, cost allocations, relevant cost-dimension snapshots, and audit history;
- all vault media referenced by assets, takes, timeline, and deliverables;
- production contract and human-gate identity;
- SHA-256 manifest and verification file;
- maker-facing README.

The importer rejects traversal paths, missing required files, checksum failures, and an invalid Project contract. Credentials never enter a package. This is the exit door: a creator can inspect, archive, move, or build tooling around the entire production lineage.

## Failure modes

| Symptom | Meaning | Recovery |
|---|---|---|
| `PROVIDER_NOT_CONFIGURED` | Required capability has no signed-site adapter | Restore that engine or explicitly configure a BYOK adapter |
| `retry_wait` | Provider attempt failed before its cap | Wait, retry now, edit prompt, or switch an explicitly configured provider |
| HTTP 401/403 | Token missing or membership is too weak | Issue a workspace token or change membership; never bypass isolation |
| Cost Center `over_budget` | Monitoring threshold was exceeded | Review the team/department distribution and rate-card assumptions; production remains available |
| Unrated usage | No actual measurement or effective rate card matched | Add a versioned rate card or ingest measured cost; never hide it from data-quality totals |
| Reconciliation difference is non-zero | Allocation and rollup totals diverged | Freeze reporting for that interval, replay the idempotent allocation audit, and repair before publishing totals |
| Package verification failure | Missing or changed bytes | Keep the source vault, rebuild the package, and investigate storage integrity |
| Object mirror unavailable | Optional S3/R2 delivery path is down | Serve/retry from the local vault; do not delete the source |
| Controller restart | Process ended mid-run | Restart Producer and advance from persisted state; completed stages are idempotent |

## Launch proof

Laptop pytest proves contracts, workspace isolation, role and Cost Center admin gates, encryption at rest, fixed-point calculation, idempotent allocation, scoped and organization rollups, reconciliation, retry/resume, provider fallback, events, export checksums, and API behavior. It does not prove live GPU throughput, measured energy/lease rates from a signed site, an external S3/R2 bucket, physical-device networking, or a multi-day one-hour production. Those are separate staging and signed-site acceptance layers.
