# Blueprint 11 — Studio walk-through

Status: Day 2  
Implements: start-up and crate-to-system preparation. Does not replace Blueprint 01.

## Surfaces

Studio and Producer share one desk. Studio is a production folio: a dark, properly sized cue rail joined to a flat operator workspace beneath a substantial stage mast. It must not read as a tilted notebook, loose sheet, or novelty 3D prop. Studio type is IBM Plex Sans. Producer keeps Newsreader on the idea page. **Continue** advances every line except the last. The clapboard **Sign** is only on Producer — the lock for this setup. Produce stays the Producer act. The left rail is this site’s order. Line 01 is **COMPUTE** with the instruction “Choose where Diony AI will run. Agent will take it from here.” Local is one group with the option **DGX Quad-Spark Cluster**; the three lease options are **Enverge DGX Dual-Spark Cluster**, **Runpod NVIDIA B300**, and **Runpod NVIDIA B200**. Group labels already communicate Local and Lease, so option names never repeat those words. The Local card shows two pre-selection sketches: the four-node CX-7/QSFP 200G fabric and the separate RJ-45/Cat6 management + vault network. Both groups end with **Connect to Existing Compute**. That action starts read-only discovery, identifies the profile from the connected GPUs, validates the site, repairs only missing setup, signs the site, and opens Producer. At the top of an existing Lease connection, **Runpod quick connect** accepts the full Runpod **SSH over exposed TCP** command and separates Host, SSH user, SSH port, and identity into the same Compute connection contract; the manual fields remain below. Selecting a new-compute option opens **Confirm option?**. After Continue, Local opens the crate rail; a lease opens the transparent setup agent (Blueprint 14). Ready is **Test GPU**. Brain is auto-configured with the model pack. It is not a carousel, not a tooltip tour, and not a substitute for NVIDIA Sync. **Refresh** is on COMPUTE. It clears the Studio process walk and reloads the page. **Start over** is aligned to the right edge of the mast and does the same. Browser refresh does not reset the walk.

## Desk open (laptop / spark-01)

People who should not see `pip` open **START HERE.txt**, then double-click **Open Diony AI** in the repo folder (`Open Diony AI.command` on a Mac, `open-diony.sh` on Linux or DGX OS, `Open Diony AI.bat` on Windows). START HERE names the hardware first: Local Quad-Spark crate, or a lease from Runpod first (`https://www.runpod.io/`) then Enverge (`https://enverge.ai/`). Dual-Spark is a 2-node cluster with fabric (Runpod Instant Clusters, or Enverge **2× DGX Spark GB10**). B300 is one **NVIDIA B300** (Runpod Deploy, or Enverge **NVIDIA B300**). B200 is one **NVIDIA B200** (180 GB; Runpod Deploy, or Enverge **NVIDIA B200**). A lobby page on `:8765` lays `.venv` once (install progress only). Desk then starts Studio (binds `:8787`) and Producer (binds `:8788`), runs `python -m diony_wizard.gateway ensure` (hosts file + `:80` Host router), and **Continue** / first-open go to `http://studio.diony.local`. Open Producer is `http://producer.diony.local`. Do not treat `127.0.0.1:8787` or `:8788` as the operator address. The first open may ask for this computer’s password once. Python 3.11 is required; if it is missing the page sends them to python.org. macOS Gatekeeper: right-click → Open. Leave the Terminal window open until it says you can close it. Engineers may still `diony-desk`.

## Four rails

| Rail | Job |
|---|---|
| Menu | Local Quad-Spark: nine lines. Leased Dual-Spark, Leased B300, and Leased B200: COMPUTE → agent → App → Ready → Producer. Brain is auto with the model pack. Pending lines cannot be opened. |
| Page | One short instruction, required fields, one action. Plans, provider maps, field help, failure notes, commands, and files are collapsed under **Details**, **Activity**, or **Files**. Existing B200/B300 Compute offers the full Runpod SSH-over-exposed-TCP command at the top, separates it into the connection fields, and keeps manual entry below. App **Check Site** probes the connected nodes before any model operation. A complete pack skips Hugging Face; only missing setup is repaired. Ready is **Test GPU**. Producer ends with Sign. After Sign, Producer handoff replaces the Studio document and stops Studio polling; a signed state never reveals the Start / Join entry sheet while state loads. |

## Sequence

Until COMPUTE is recorded, the menu is only COMPUTE. Continue, do not Sign.

Local Quad-Spark: path → unbox → inventory → cables → switch → sync → roce → vault → producer

Leased Dual-Spark: path → connect → app → prove → producer

Leased B300: path → connect → app → prove → producer

Leased B200: path → connect → app → prove → producer

Lease Connect records hosts, SSH user (GPU login, the word before `@`), SSH port on B200/B300 (default 22; RunPod TCP `-p`), and vault. Its compact confirmation line is a live preview of the current Host, SSH user, SSH port, and Vault fields; it changes on every input event and never shows an older saved connection while the fields show newer values. Dual-Spark Connect is two hosts and cluster fabric (Runpod Instant Cluster interconnect, or Enverge CX-7 RoCE). B300 / B200 Connect is one host. The RunPod map is collapsed under **Details**: skip HTTP services (Jupyter 8888), skip SSH (`LOGIN@ssh.runpod.io`, no SCP), **use SSH over exposed TCP** (`ssh root@IP -p PORT`), skip Web terminal, same numbers on Direct TCP ports. Vault is Details → Pod volume Mount path `/workspace`. Field map: Blueprint 14. App **Check Site** runs a read-only readiness probe first. It verifies every pinned DeepSeek, Wan, and `diony_sr_v1` component plus `/opt/wan22`; when present, it records **Site already prepared. Model pull skipped.** and does not invoke Hugging Face. It may start Producer without pulling models, or replicate the existing pack to a missing second Spark. Only an incomplete primary pack enters the pull/install recipe. Ready shows compact progress; the full log stays under **Activity**. B200 brain pin is `max_model_len 16384`. Ready **Test GPU** SSHes that Connect line and reads `nvidia-smi` (must match Site). Pytest never opens a socket; a real IP requires a green Test GPU before Continue. Producer records the floors (Idea, Production, Gallery), **Sign** writes `{vault}/.cluster/site.json` (and copies it onto the GPU vault when live) and locks the pack, then the operator opens Producer. The Producer line does not ask the operator to re-enter or confirm the fixed product URL; that address is recorded automatically and appears as the signed-report handoff. The named human on a lease is collected on Site. Sign is the last line only.

Site is unsigned until a human picks **DGX Quad-Spark Cluster**, **Enverge DGX Dual-Spark Cluster**, **Runpod NVIDIA B300**, or **Runpod NVIDIA B200**. Leases are never pre-selected. Unbox is before power on the local path only.

## What the Studio serves

- Switch snippets from `infra/switch/` when the SKU is chosen
- Active cluster profile, serving notes, vault fstab, and weight manifest as read-only files
- A site pack on Producer (and on the signed report) so prep stays inside Studio
- `python3 infra/health/check_cluster.py` as a laptop dry-run on the RoCE line and on the signed report
- Live Dual-Spark/B300/B200 App apply: SSH BatchMode (`DIONY_SSH_USER`, `DIONY_SSH_PORT`, `DIONY_SSH_KEY`) or local bash on the GPU, Hugging Face pull, Wan clone, `diony-finish` install, Producer start. `HF_TOKEN` is pasted on App or Ready from https://huggingface.co/settings/tokens (Create new token +). Not stored in site.json.
- Ready Test GPU: SSH + `nvidia-smi` (must match Site). If Pack is red, **Pull pack** on Ready (same SSH line). Pytest records dry-run rows. Operator click SSHes even when the desk set `DIONY_DRY_RUN=1`, unless `DIONY_PROVE_OFF=1` / `DIONY_APPLY_OFF=1`
- A signed report only after every line is passed — Sign is the last line and writes the site lock
- Recorded lines are reviewable; pending lines cannot be skipped

## Dry-run vs live

Laptop pytest and unsigned fabric stay **dry-run** (`DIONY_DRY_RUN=1`). Netplan remains Week 2+. On Dual-Spark, B300, and B200, App Continue is **live apply** when `DIONY_WIZARD_LIVE=1` or `/models` exists: vault at `/diony/vault` (bind or `/workspace` symlink), venv (PEP 668 fallback), pull the pack, clone Wan2.2, start Producer. Sign writes `site.json` on this Studio’s vault and on `/diony/vault` of the GPU. How shows the same commands Studio runs. `DIONY_APPLY_FAKE=1` is tests only. Open Producer is `http://producer.diony.local` on every site (Day 2 still binds `:8788`). The showrunner opens Producer after Sign. Picture lock then runs `diony_sr_v1` to `master_1080.mov`.

## Failure modes

| Symptom | Cause | Action |
|---|---|---|
| Cannot continue | Required field or quiz wrong | Studio states the miss; do not skip |
| Quiz hunting | Operator guessing | Learn page cites the Blueprint; answers are in the lesson |
| Powered on too early | Unbox skipped in the field | Site, then Unbox; Studio will not activate inventory first |
| Finished Studio, lost Producer | Wrong host | Producer is `http://producer.diony.local` on every site |
| studio.diony.local does not open | Names missing from hosts, `:80` blocked, or the password dialog was cancelled | Approve the one-time password. Desk runs `python -m diony_wizard.gateway ensure` (install writes hosts, then a detached `:80` Host router — not `nohup` inside the password helper). If you cancelled, Continue on the lobby retries. Do not switch to `127.0.0.1:8787` |
| Double-click blocked on a Mac | Gatekeeper | Right-click Open Diony AI.command, then Open |
| Page asks for Python | No 3.11 on the machine | Install Python 3.11, then double-click again |
| First open died mid-way | Terminal closed during install | Double-click Open Diony AI again. Leave it open until Continue |
| Dual-Spark, B300, or B200 skipped App | Connect used to auto-fill weights | App pulls the site pack and starts Producer. Do not skip to Sign |
| Dual-Spark with one host | Copied B300/B200 Connect, or launched a single Spark / one Runpod Pod | Two nodes with fabric: Runpod Instant Clusters (2 nodes), or Enverge **2× DGX Spark GB10** |
| Wrong lease SKU | RTX / H100 / H200 / single GB10 | START HERE: Dual-Spark is a 2-node GB10 pair; B300 is one B300; B200 is one B200 |
| B200 vs B300 site | Picked the other Blackwell GPU | Fail Site. Match the GPU you Deployed |
| B200 Instant Cluster as Dual-Spark | Recorded a B200 cluster as Dual-Spark | Fail. Dual-Spark is GB10 |
| Runpod Deploy at 30 GB / Volume disk 50 GB / Network Volume 10 GB | Left Storage defaults | Container disk ≥800 GB. Dual-Spark Network Volume ≥1000 GB, or B200/B300 Volume disk ≥1000 GB at `/workspace`. Stop wipes `/models`. Terminate wipes Volume disk |
| Runpod every GPU Out of capacity after Network Volume | Volume is in a data center with no GPU right now | START HERE §3: B200/B300 skip Network Volume and use Volume disk. Dual-Spark still needs a shared Network Volume in that DC. Do not substitute RTX |
| Pasted `ssh root@IP -p PORT -i KEY` | Full Runpod TCP line | Paste it into **Runpod quick connect**. Studio separates the connection values. Never paste it into one manual field |
| ssh.runpod.io as Host | Used the proxy SSH line | Host is the IP before `-p` on **SSH over exposed TCP** |
| App rsync fails on port 22 | Left SSH port at 22 when TCP SSH maps `-p 28231` | SSH port = the `-p` number. Jupyter 8888 is not that port |
| Connection refused on SSH port | Pod Stopped, or RunPod remapped TCP IP/`-p` after Resume | Resume the pod. Open Connect (02). Copy the new **SSH over exposed TCP** IP and `-p`. Continue on Connect, then **Pull pack** |
| Code edits do not appear | Desk left a listening `:8787` serving old HTML | Desk compares `GET /` to the on-disk `app.js?v=` stamp and restarts a stale Studio. `GET /api/state` not 200 also restarts. Double-click Open Diony AI. Then hard-refresh `http://studio.diony.local` (Safari: Option-Command-R). `DIONY_WIZARD_RELOAD=1` is optional |
| Ready Continue blocked | Host is a real IP and Test GPU was not clicked, or a row is red | Pack red: **Pull pack** on Ready (not App Continue). Then Test GPU. Other red rows: fix SSH / GPU name / vault / Dashscope |
| App Continue stays on App / Studio stops answering | Pack pull is hours long, and a second Continue nested-locked the apply mutex (event loop froze) | App Continue starts the pull and opens Ready. Pack progress is on Ready. Do not click Continue again on App. Hard-refresh after a Studio restart |
| Returning to a prepared Site starts another model pull | The App line did not probe the connected node before setup | **Check Site** must run the read-only readiness probe first. A complete primary pack skips Hugging Face and reports that no pull ran. |
| Pack red on Ready | Pull still running, no `HF_TOKEN`, or App was dry-run with no SSH | Open https://huggingface.co/settings/tokens, Create new token +, paste **HF_TOKEN** on App or Ready. Then **Pull pack**. Then Test GPU |
| `mount: /diony/vault: permission denied` | RunPod (and similar) deny `mount --bind` onto `/diony/vault`; `ln -sfn` into an existing directory becomes `/diony/vault/workspace` | App moves the directory aside and links `/workspace` → `/diony/vault`. Product path stays `/diony/vault`. Re-run **Pull pack** |
| `Requirement already satisfied: huggingface_hub` as Pack FAIL | Apply ran `pip install -U pip huggingface_hub` every time; pip exited non-zero after listing packages already in the venv | Skip pip when `snapshot_download` imports. Retry **Pull pack**. Hugging Face then starts. |
| Refresh did not reset Studio | Walk, apply log, and Connect SSH port live in the Studio process | Click **Refresh** on Site, or **Start over** in the mast. Then Site → Connect with the current RunPod TCP line. A browser refresh does not clear uvicorn memory |
| Start over still shows pack FAIL | An in-flight pack thread wrote the old job after reset | **Refresh** / **Start over** discards that thread. Click again, then Connect with the current IP and `-p` |
| Apply error on Connect | Failed App log was painted on every page | Apply log and error stay on App / Ready only |
| Could not load Studio state | `:8787` is a stale Studio (uvicorn started with `reload=False`) or a lesson import failed (`SSH_PORT_DEFAULT` / `RUNPOD_CONNECT_DIAGRAM` missing on the in-memory prep) | Hard-refresh `http://studio.diony.local`. Use **Retry** on the sheet — not Continue. Desk probes `GET /api/state` and restarts Studio if that is not 200. Double-click Open Diony AI is not enough if a dead process still owns `:8787` |
| step is not active after Site Continue | Site already passed; the UI retried while `/api/state` was 500 | Retry reloads state onto Connect. Continue on an already-passed line is a no-op (200, stay on the next line) |
