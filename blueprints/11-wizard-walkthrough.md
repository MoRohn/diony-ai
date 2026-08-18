# Blueprint 11 — Wizard walk-through

Status: Day 2  
Implements: start-up and crate-to-system preparation. Does not replace Blueprint 01.

## Surfaces

Wizard and Producer share one desk: baize and a single notebook sheet. Wizard type is IBM Plex Sans. Producer keeps Newsreader on the idea page. **Continue** advances every line except the last. The clapboard **Sign** is only on Producer — the lock for this setup. Produce stays the Producer act. The left rail is this site’s order. The page is one line: a sentence, then the report. Site is line 01. After it is recorded, the rail is **this site’s sequence** (Blueprint 14). Local Quad-Spark is the crate. Leased Dual-Spark and Leased B300 are Site → Connect → App → Producer. Brain is auto-configured with the model pack.

It is not a carousel, not a tooltip tour, and not a substitute for NVIDIA Sync.

## Three rails

| Rail | Job |
|---|---|
| Menu | Local Quad-Spark: nine lines. Leased Dual-Spark and Leased B300: four (Site, Connect, App, Producer). Brain is auto with the model pack. Pending lines cannot be opened. |
| Page | One-line why, then the fields. Connect shows a short profile card. App Continue pulls the pack. Producer shows Idea / Dailies / Program / Lock, then Sign. Snippets stay under Notes. |

## Sequence

Until Site is recorded, the menu is only Site. Continue, do not Sign.

Local Quad-Spark: path → unbox → inventory → cables → switch → sync → roce → vault → producer

Leased Dual-Spark: path → connect → app → producer

Leased B300: path → connect → app → producer

Lease Connect records hosts, SSH user, and vault. Dual-Spark Connect is two hosts and CX-7 RoCE. App Continue pulls the pinned DeepSeek 0731 + Wan2.2 A14B + `diony_sr_v1` pack (Brain recipe included) and starts `diony-producer` with vault and bind env on the GPU. Producer records the floors (Idea, Dailies, Program, Lock), **Sign** writes `{vault}/.cluster/site.json` and locks the pack, then the operator opens Producer. The named human on a lease is collected on Site. Sign is the last line only.

Site is unsigned until a human picks **Local Quad-Spark Cluster**, **Leased Dual-Spark Cluster**, or **Leased B300**. Leases are never pre-selected. Unbox is before power on the local path only.

## What the Wizard serves

- Switch snippets from `infra/switch/` when the SKU is chosen
- Active cluster profile, serving notes, vault fstab, and weight manifest as read-only files
- A site pack on Producer (and on the signed report) so prep stays inside Wizard
- `python3 infra/health/check_cluster.py` as a laptop dry-run on the RoCE line and on the signed report
- Live Dual-Spark/B300 App apply: SSH BatchMode (`DIONY_SSH_USER`, `DIONY_SSH_KEY`) or local bash on spark-01, Hugging Face pull, Wan clone, `diony-finish` install, Producer start
- A signed report only after every line is passed — Sign is the last line and writes the site lock
- Recorded lines are reviewable; pending lines cannot be skipped

## Dry-run vs live

Laptop pytest and unsigned fabric stay **dry-run** (`DIONY_DRY_RUN=1`). Netplan remains Week 2+. On Dual-Spark and B300, App Continue is **live apply** when `DIONY_WIZARD_LIVE=1` or `/models` exists: pull the pack, clone Wan2.2, start Producer. How shows the same commands Wizard runs. `DIONY_APPLY_FAKE=1` is tests only. The showrunner opens Producer after Sign. Picture lock then runs `diony_sr_v1` to `master_1080.mov`.

## Failure modes

| Symptom | Cause | Action |
|---|---|---|
| Cannot continue | Required field or quiz wrong | Wizard states the miss; do not skip |
| Quiz hunting | Operator guessing | Learn page cites the Blueprint; answers are in the lesson |
| Powered on too early | Unbox skipped in the field | Site, then Unbox; Wizard will not activate inventory first |
| Finished Wizard, lost Producer | Wrong host | Producer is `http://producer.diony.local` on every site |
| Dual-Spark or B300 skipped App | Connect used to auto-fill weights | App pulls the site pack and starts Producer. Do not skip to Sign |
| Dual-Spark with one host | Copied B300 Connect | Two Sparks, with CX-7 between them |
