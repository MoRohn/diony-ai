# Blueprint 30 — The Google Flow 1080p Finish

Status: implemented on the Director (cloud) lane, Google Flow route only

Owner: Diony AI

Applies to: the Flow adapter (`POST /videos/upscale`), `DirectorOp` UPSCALE ops, the submitter waves, the assemble endpoint, the production board, the batch bar

Depends on: Blueprints 25/26 (Director pivot), 28 (post-assembly narration), 29 (Look Bible)

## Product decision

Every Director-lane clip is generated at the provider's native raster (720p on
Omni 1.1 Flash and Veo; 720p on Grok). The quality mandate (Blueprint 10) is
1080p finish-intent for every deliverable, and the Director has no local
super-resolution lane — `diony_sr_v1` lives on the signed GPU cluster. Google
Flow exposes the finish the Director can use: a server-side upscale of a
Flow-generated video to 720p, 1080p or 4K, by `mediaGenerationId`.

**The finish is one batch option, not a per-clip chore.** The batch bar gained
**Run, Assemble & Upscale All** beside Run & Assemble All. It rolls every clip,
finishes every printed Flow take at 1080p, and cuts the film at 1920×1080
(1080×1920 portrait). Grok takes have no server-side lane; they are named and
conformed up locally at the cut.

## The API contract (useapi.net Google Flow API v1, verified 2026-09-03)

`POST /videos/upscale` with `{mediaGenerationId, resolution, async: true}`:

- `mediaGenerationId` names a video generated through `POST /videos` (or its
  extension). A local file or URL cannot be upscaled — the adapter refuses
  before any request leaves.
- `resolution` is `720p` (promotes a 360p Omni clip), `1080p` (default) or
  `4K` (Google AI Ultra only). Anything else is refused before the request.
- `async: true` answers **201** with a lowercase `jobid`; `GET /jobs/{jobid}`
  reports `created → started → completed | failed` with `media[].videoUrl`.
- No `email` parameter: the endpoint 400s on it (verified live); the media id
  already names the owning account.
- **Pricing:** a paid Google AI plan is required (free accounts cannot upscale
  at all); 720p and 1080p spend no credits; 4K costs 50 credits. The rate card
  carries `video-upscale` at `generation:720p`, `generation:1080p` and
  `generation:4K`; the estimate still carries the captcha solve, and actuals
  reconcile from the `remainingCredits` delta when the meter reports it.
- **Caching:** upscaling the same video twice returns the cached result. A
  200 answer with media in the body is captured on the handle so the op
  completes without a poll.
- **Missing media URLs:** a job that reports `completed` with no `videoUrl` is
  Google's link endpoint throttling, not a failed generation. The adapter
  reports it as `running` with `poll_after_s: 60`; the advance loop honours
  that window (`poll_not_before`), because a tight retry loop lengthens the
  block. Only a media item whose `mediaGenerationStatus` is not successful is
  terminal.
- 429 is quota (the existing time-gated hold, never a failure); the docs ask
  for fewer parallel requests, so finishes roll a few at a time.

## The law

1. **A finish is not a take.** An UPSCALE op carries `artifact_refs.source_op`
   naming the take it finishes. The take stays the unit of record — review,
   narration cue, EDL, receipt line. At the cut, the usable finish supplies
   the pixels; nothing else about the take changes.
2. **A finish is measured before it may replace its take.** On completion the
   print is probed: it must carry at least the lines it was booked for and run
   within 0.5s of the take. A short or drifted print fails its `finish` review
   and the take's own print stays in the cut. A failed finish job, or a failed
   finish review, **never** enters the fix ladder — nothing is re-shot; the
   finish alone retries.
3. **Idempotent by construction.** `upscale_all` keeps a usable finish, leaves
   a rolling one alone, re-targets a drafted one, and drafts anew only for a
   missing or failed finish. A per-clip proposal 409s while a finish already
   exists or rolls.
4. **Waves release a few finishes at a time.** Targeted ops that ride no scene
   chain are released after the bibles settle, capped by
   `DIONY_FLOW_UPSCALE_PARALLEL` (default 4) in flight at once.
5. **The cut names what it did.** `assemble` takes `finish`:
   - `auto` (default) cuts every usable finish; one finished print lifts the
     whole raster to 1080 lines, and takes without a finish are conformed up
     locally and named under `finish.conformed_up`.
   - `1080p` refuses (409, naming the takes and why) while any Flow take in
     the cut lacks its usable finish — the batch option's cut, so a failed
     finish can never ship silently as a 720p film labelled 1080.
   - `720p` cuts the takes' own prints and ignores every finish.
   The response carries `raster` and `finish: {mode, resolution, finished,
   native, conformed_up}`.
6. **The board shows the finish beside the take.** Each shot row carries its
   finish chip (`1080p ✓`, `1080p finishing…`, `1080p finish failed` with the
   reason and a Retry), and `can_upscale` is false while a usable finish
   exists or one rolls.

## Endpoints

| Route | Role |
|---|---|
| `GET /api/director/upscale/rate?resolution=1080p` | One finish's modeled cost on the user's plan; the bar prices the option from it |
| `POST /api/director/sessions/{sid}/upscale_all` `{resolution}` | Finish every printed Flow take of the cut; returns `targeted`, `already`, `rolling`, `skipped[{clip_id, reason}]`, `est_usd_micros` |
| `POST /api/director/ops/{op_id}/upscale` `{resolution}` | One take's finish (the board button); 409 while one exists or rolls |
| `POST /api/director/sessions/{sid}/assemble` `{finish}` | The cut, per law 5 |

## Failure modes

| Symptom | Cause | Response |
|---|---|---|
| `refusing to cut a 1080p film while these Flow takes lack their 1080p finish` | A finish failed or never rolled | Retry the finish from SHOTS, or "Assemble with the prints that exist" (finish=auto) |
| Finish review fail "below the 1080-line target" | Flow returned a print without the booked lines | The take's print stays; Upscale 1080p is offered again |
| Finish held at `link pending` | Google's link throttling | Wait; the op polls once a minute and stalls loudly after 30 minutes |
| `PUBLIC_ERROR_USER_REQUESTS_THROTTLED` | Too many parallel Flow requests | The run pauses on the quota hold; lower `DIONY_FLOW_UPSCALE_PARALLEL` |
| Grok takes listed under `skipped` | No server-side lane on Grok | Expected; they are conformed up at the cut and named in the receipt |
