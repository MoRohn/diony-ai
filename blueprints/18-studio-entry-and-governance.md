# Blueprint 18 — Studio Entry and Governance

Status: implementation-ready product specification

Owner: Diony AI

Applies to: Desk, Studio, Producer, launch control plane, contracts

Depends on: Blueprints 11, 12, 14, 15, 16, and 17

## Product decision

The first Studio page asks one question before it asks about hardware:

> Are you starting a Diony Studio, or joining one that is already rolling?

Both answers are first-class.

The front-door product promise is exact and persistent across Desk, Studio entry,
and Producer arrival:

> **Own Your Studio, Produce at Cost**

This is a product motto, not a pricing claim. “Own” means the Studio controls its
controller, production site, assets, membership, and lineage. “At Cost” means
transparent internal-credit and fixed-point cost attribution without a Diony
markup, wallet, subscription gate, or budget-based production lock.

- **Start New Diony Studio** is the authoritative setup path. The maker names the Studio, becomes its Owner and first **Cost/Budgetary Admin**, chooses Local Quad-Spark, Leased Dual-Spark, Leased B300, or Leased B200, completes the existing Studio rail, and signs the site.
- **Join a Diony Studio** securely connects a maker to an already configured Producer controller. A successful join creates an active membership and a personal Cost Center in one transaction, records the maker's Profile Name, and hands them into Producer with the permissions granted by the Studio.

In product copy, **Diony Studio** is the human name for the existing `Workspace` authority. It is not a second tenant object. The Producer controller owns Studio membership and cost truth; a GPU node never becomes the public identity or authentication authority.

This feature does not alter Diony's production constitution. DeepSeek remains on the signed site, Wan creates 720p negatives, `diony_sr_v1` creates the mandatory 1080p finish, and Bible lock plus Distribution remain the two human gates.

## 1. First-page dual selection

### Single job

The page lets a maker identify their relationship to a Studio. It does not ask for a cluster type, credentials, a Profile Name, or an invitation on the same screen.

### Layout thesis: the Studio seam

The existing Parchment production folio fills the Studio work area. A single Diony Crimson seam divides it into two equal typographic fields. The seam is a registration mark from production—not a decorative divider—and becomes the progress line after a choice is made.

```text
Desktop, >= 760 CSS px

┌──────────────────────── Diony AI / WIZARD ────────────────────────┐
│  A STUDIO                                                         │
│  STARTS WITH                                                      │
│  A CHOICE.                                                        │
│                                                                  │
│  ┌──────────────────────────┬──────────────────────────────────┐  │
│  │ START NEW                │ JOIN A                           │  │
│  │ DIONY STUDIO             │ DIONY STUDIO                    │  │
│  │                          │                                  │  │
│  │ Name it. Sign the site.  │ Bring an invite. Make with      │  │
│  │ Own the first budget.    │ people already in production.   │  │
│  │                          │                                  │  │
│  │ [ Start the Studio → ]   │ [ Join the Studio → ]           │  │
│  └──────────────────────────┴──────────────────────────────────┘  │
│  Your work stays on the Studio's signed production system.       │
└───────────────────────────────────────────────────────────────────┘
```

Rules:

- Each path receives 50% of the available decision area, the same heading size, one plain-language paragraph, and a full-width action.
- The Start path appears first in DOM order because it creates the authority. Join is not visually muted, marked “secondary,” or placed behind a text link.
- The crimson seam is 2 CSS pixels at rest. Keyboard focus or pointer intent on either side moves a small amber cue along the seam toward that path; it does not resize the halves.
- The Studio Chorus spirit watches the seam. Its optional 180–220 ms eye/spark adjustment is decorative, `aria-hidden`, transform-only, and removed under reduced motion.
- Each field's production illustration may use its cropped light-panel motion in the existing illustration slot. Its near-cream source background is normalized to the Parchment folio color and its empty outer codec edge is clipped so normal browser compositing leaves no visible rectangle or edge. It is muted, inline, decorative, and control-free; it starts only when the dual-choice page is shown, plays once without looping, then restores the matching first-frame still. Reduced-motion users receive the still only. The motion adds no border, icon, or control.
- No gradients, glass cards, stock people, lock illustrations, pricing language, or generic onboarding stepper appear here.
- The footer truth is: **“Your work stays on the Studio's signed production system.”** It links to a short disclosure explaining local-controller and leased-controller routing without exposing cluster credentials.

### Exact content

Page eyebrow: `DIONY STUDIO`

Display: `START ONE. / JOIN ONE.`

Intro: `Set up the production system, or enter a Studio that already has one.`

Start field:

- Label: `START NEW`
- Heading: `DIONY STUDIO`
- Body: `Name the Studio. Sign its production site. You become the first owner and Cost/Budgetary Admin.`
- Action: `Start the Studio`
- Screen-reader action label: `Start a new Diony Studio`

Join field:

- Label: `JOIN`
- Heading: `A DIONY STUDIO`
- Body: `Use an invite from a Studio you trust. Your Profile Name and personal Cost Center travel with your work.`
- Action: `Join the Studio`
- Screen-reader action label: `Join an existing Diony Studio`

### Interaction

- Tab order is Start action, Join action, ownership disclosure, mast controls.
- Arrow keys do not replace Tab. If the two fields are implemented as a radio group, Left/Right and Up/Down follow the ARIA radio pattern and a separate `Continue` commits the choice. If each field is a direct action, use buttons—not radio semantics.
- Activation immediately records only the selected entry mode in session state and moves focus to the next page heading.
- Browser Back returns to the dual choice without destroying completed data. **Start over** explicitly clears the current Studio session after confirmation when work exists.
- Loading the initial decision uses a stable skeleton matching the final text blocks. If entry state cannot load, show the page frame plus `The Studio entry could not be read.` and `Try again`; never show a blank folio.

## 2. Start New Diony Studio flow

### Sequence

```text
Dual selection
  → Studio identity
  → existing Site choice
  → existing site-specific rail
  → optional invitations
  → review authority
  → Sign
  → Producer arrival
```

1. **Studio identity**
   - Required Studio Name, 2–80 Unicode characters after trimming.
   - Suggested URL-safe slug is derived client-side for review, but the controller assigns the canonical unique slug.
   - Required Profile Name, 2–60 Unicode characters after trimming.
   - Optional email is used only for authentication and invitation notices; it is never the public attribution label.
   - Copy: `This is the name makers will see on every call sheet and cost report.`
2. **Authority preview**
   - Before hardware, the page states: `You will be the Studio Owner and its first Cost/Budgetary Admin.`
   - It explains that budgets monitor usage and do not buy credits or block a run.
3. **Site choice and setup**
   - Continue into the existing four Site choices and the exact rails in Blueprint 11.
   - Local and leased paths keep their current Connect, App, Ready, Producer, and Sign truth.
   - The chosen Studio identity stays in the mast but never replaces the site-status line.
4. **Optional invitations**
   - After the site is Ready and before Sign, offer `Invite collaborators` and `Not now` with equal clarity.
   - Invitations may be drafted before Sign but cannot be redeemed until the Producer controller is signed and reachable.
   - Draft invitations visibly say `Ready after Sign`.
5. **Review authority**
   - A compact review lists Studio Name, Profile Name, site type, Owner, Cost/Budgetary Admin, invitation count, and controller reachability.
6. **Sign**
   - Sign remains the last Studio line and continues writing `{vault}/.cluster/site.json`.
   - In the same controller transaction, create the Workspace/Studio, Owner membership, administration Team, default Department, Owner personal Cost Center, and `CostGovernance` assignment.
   - If either the site lock or Studio bootstrap fails, Sign fails as a whole from the user's perspective. Re-entry reconciles by idempotency key; it must not create a second Studio or Cost Center.
7. **Arrival**
   - Heading: `THE STUDIO IS YOURS.`
   - Body: `[Studio Name] is signed on [site label]. You hold Studio ownership and the first budget view.`
   - Actions: `Open Producer` and, if no invite exists, `Invite a maker`.

### Owner defaults

- Membership role: `owner`.
- Cost authority: `CostGovernance.admin_user_id = owner user ID` and membership in the configured admin Team.
- Owner Profile Name is stored on the Studio membership, not only the global User, so one person may use a different production credit in another Studio.
- Owner personal Cost Center uses code `PERSONAL-{stable short member id}` and starts in `monitor_only` mode with budgets set to `0` (meaning no threshold configured, not zero permission).
- The existing default Cost Center remains available for imported or unattributed legacy work. New work must resolve the acting member's personal Cost Center unless an authorized producer explicitly assigns another active Cost Center.

## 3. Join a Diony Studio flow

### Accepted entry forms

The Join page accepts one of these user-recognizable credentials:

- a complete Diony invitation link opened from mail or chat;
- a short invitation code pasted into the field;
- an invitation file exported by an air-gapped Studio.

It does not ask the maker to paste an SSH command, GPU IP, bearer token, controller secret, or `site.json`.

### Sequence

```text
Dual selection
  → invitation
  → controller discovery
  → Studio verification
  → authentication
  → Profile Name
  ├─ invitation can admit → atomic membership + Cost Center → arrival
  └─ approval required    → join request → pending → approval
                                      └→ atomic membership + Cost Center → arrival
```

### A. Invitation

- Heading: `BRING THE INVITE.`
- Field label: `Invitation link or code`
- Brand: use the current Diony AI ticket mark, identical to the Studio mast mark.
- Helper: `The Studio checks this invitation. Diony cloud does not.`
- Primary action: `Find the Studio`
- Secondary action: `Back to the two paths`
- Paste is supported. Auto-submit is not; the maker must choose `Find the Studio`.

### B. Controller discovery

Show a non-blocking progress ledger with real states:

1. `Reading invitation`
2. `Finding Producer`
3. `Checking Studio identity`
4. `Opening a secure line`

Use `aria-live="polite"` for step text and a labeled progress element with indeterminate semantics until a bounded phase is known. After 8 seconds, add `This Studio is taking longer to answer.` without declaring failure. At 30 seconds, stop and offer retry or diagnostics.

Security rules:

- The invitation contains a Studio ID, invitation ID, allowed controller origin(s), expiry, nonce, admission mode, and signature. It contains no reusable controller secret.
- The controller stores only a keyed hash of the invitation token. Raw tokens are displayed once and redacted from logs, analytics, URLs after redemption, exports, and browser storage.
- Remote controllers require HTTPS with a valid certificate. A local `.diony.local` controller may use the installed Diony trust/pairing channel; the UI names that local trust explicitly.
- Discovery may contact only origins signed into the invitation. Neither browser nor server performs arbitrary URL fetches from pasted input. Redirects to a different origin fail closed.
- The Studio responds with a short-lived signed challenge containing Studio ID, public Studio Name, site class, controller fingerprint, reachability, invitation status, and policy. The client proves possession through the redemption exchange.
- Cluster SSH, Hugging Face credentials, vault paths, node addresses, and provider secrets never cross the join boundary.

### C. Verify the Studio

Before authentication, show a Studio passport:

| Label | Example |
|---|---|
| Studio | Northlight Pictures |
| Production site | Leased B300 |
| Producer | Online · checked 14 seconds ago |
| Invitation | Valid · admits Creator |
| Approval | Not required / Studio approval required |
| Fingerprint | `DIONY 7A4F … 92C1` |

Copy: `Check the Studio name before you enter. Diony will connect to this Producer controller; it will not give you cluster credentials.`

Actions: `This is my Studio` and `Not this Studio`.

### D. Authenticate

- Invitation possession is not the permanent session. The maker authenticates with the Studio's configured method after verification.
- Supported Day 1 methods are local device passkey or a Studio-issued one-time challenge. Shared controllers may later add explicit identity providers without changing the join state machine.
- The authentication UI names the Studio and the method. It never asks for the Cost/Budgetary Admin's password.
- On shared machines, `Keep me signed in` defaults off. Session expiry and device removal remain visible in Profile.

### E. Profile Name

- Heading: `HOW SHOULD THE CREDITS READ?`
- Field label: `Profile Name`
- Helper: `Used on activity, generations, approvals, exports, and cost attribution in this Studio.`
- Preview: `A take by [Profile Name] · Personal Cost Center`
- Action: `Join [Studio Name]` or `Ask to join [Studio Name]`, depending on policy.

Validation:

- 2–60 Unicode characters after normalization and trimming.
- Preserve diacritics and intended case; collapse line breaks and repeated whitespace to a single space.
- Reject control characters, bidi overrides, markup, URLs presented as names, and names made only of punctuation or emoji.
- Comparison for uniqueness is Unicode case-folded within the Studio. If occupied, do not append a silent number. Say `That Profile Name is already in use here.` and offer an editable suggestion such as `Maya R.`
- Reserved Studio/system labels (`Diony`, `System`, `Producer`, `Admin`, and site labels) cannot be claimed in a confusable form.
- The Profile Name is Studio-scoped and mutable by the member unless the member is suspended. Rename updates future attribution and the current membership display; historical audit and usage rows retain the Profile Name snapshot recorded when the event occurred.

### F. Atomic successful join

A join is successful only when all of the following commit together:

1. create or link the authenticated `User`;
2. create an active `Membership` with the selected Studio-scoped Profile Name and invited role;
3. create the member's personal Team or personal allocation dimension as chosen by the contract migration;
4. create one active personal `CostCenter` in the Studio's designated membership Department;
5. bind `membership.personal_cost_center_id` to that Cost Center;
6. consume the invitation or approved request;
7. append `membership.joined` and `cost_center.created` audit events with one correlation ID;
8. issue a short-lived Producer session and refresh credential for that membership.

The transaction is idempotent on `(studio_id, authenticated_user_id, invitation_or_request_id)`. Retrying after a lost response returns the same membership and Cost Center. It never creates `Name 2`, a second Cost Center, or a second credit balance.

Success receipt:

- Heading: `YOU'RE IN THE STUDIO.`
- Body: `You are now part of [Studio Name] as [Profile Name].`
- Facts: role, site label, personal Cost Center name/code, budget state, and who approves membership.
- Truth note: `Internal credits show production use. They are not money and they do not block a run.`
- Primary action: `Enter Producer`
- Secondary action: `View my Studio profile`

Producer opens on the last authorized bay, or Idea for a first visit. Focus moves to a single arrival heading before the working surface.

## 4. Pending approval, invitation, denial, and removal

### Admission policies

A Studio chooses one explicit policy:

- `invite_admits`: an active invite with a permitted role admits the authenticated maker immediately;
- `admin_approval`: a valid invite creates a pending request after authentication and Profile Name choice;
- `closed`: new redemptions are refused even if an old unconsumed invite exists.

Changing policy never upgrades an existing invitation's role or revives an expired/revoked invitation.

### Admin invitation through email

Email is a complete delivery rail for Studio invitations, not a decorative notification and not the security authority. The Admin flow begins and ends inside Producer:

```text
Producer · Studio · Invitations
  → Invite a maker
  → recipient + role + expiry + note
  → preview the exact email and access terms
  → Send invitation
  → durable email outbox
  → recipient reviews and accepts
  → atomic membership + Cost Center
  → authenticated Producer arrival
  → Admin sees Accepted
```

#### Admin composer

`Invite a maker` opens a focused sheet with:

- **Email address** — required for email delivery; normalize the domain, preserve the local part for delivery, and show the complete address for Admin review.
- **Profile Name suggestion** — optional and explicitly editable by the recipient. It never reserves or silently assigns the final name.
- **Studio role** — defaults to Creator. The form explains the effective permissions in one sentence. Owner or Admin grants require fresh Owner authentication; Cost/Budgetary Admin is transferred through its separate authority flow, never through an ordinary invitation.
- **Admission** — read-only summary of `Joins when verified` or `Needs Studio approval`, derived from current Studio policy.
- **Expires** — defaults to 72 hours; allowed presets are 24 hours, 72 hours, 7 days, or a bounded custom date.
- **Personal note** — optional, plain text, 500 characters maximum. Links are rendered as text, not additional email actions.
- **Send from** — the verified Studio sender identity and reply-to contact.

Beside the form, desktop shows a live email preview; mobile puts `Preview email` before the final action. The preview uses the real Studio Name, inviter Profile Name, role, site class, expiry, and note. It never exposes member counts, budgets, project names, cluster addresses, SSH details, or secrets.

The final action is `Send invitation`, not `Create`. It is rendered as the full-width, high-contrast Studio delivery action, keeps its label readable in hover, focus, and disabled states, and changes only the action label to `Sending…` while delivery is in flight. Before commit, a compact review says:

`[Email] will be invited to [Studio] as [Role]. A personal Cost Center is created only after acceptance.`

The send transaction atomically creates `StudioInvitation`, a queued `InvitationEmailDelivery`, and an audit event. It does not mark the message `sent` until the configured mail transport accepts it. The result page distinguishes:

- `Queued` — safely stored for delivery;
- `Sent` — accepted by the Studio mail transport;
- `Delivered` — provider delivery confirmation received when supported;
- `Delivery unconfirmed` — transport accepted it but supplies no delivery receipt;
- `Bounced` — recipient server rejected it;
- `Complained` — recipient reported the message;
- `Accepted` — the recipient completed Studio admission. This is an invitation state, not an email-delivery state.

Admin actions are `Copy secure link`, `Copy invitation code`, `Send new email`, and `Revoke`. `Send new email` after a bounce, address correction, or suspected exposure revokes the old invitation and issues a new token; it never reuses or reveals a stored raw token. Correcting the recipient address also creates a replacement invitation so the signed identity binding cannot drift.

#### Mail transport and delivery truth

- Producer uses an explicit Studio-owned email adapter: standard authenticated SMTP or a deliberately configured transactional provider. There is no implicit Diony cloud mail service.
- Mail credentials are encrypted through the Blueprint 16 secret boundary. List and diagnostic APIs expose masks and verified sender identity only.
- Publishing shared-Studio invitations requires a configured sender domain and reply-to identity. If email is not ready, `Send invitation` is disabled with `Studio email is not ready.` and `Set up email`; `Copy secure link` remains an honest separate fallback.
- The outbox is durable in the control-plane database. A process restart resumes unsent mail without creating a second invitation.
- Delivery is at-least-once with a stable RFC 5322 `Message-ID` and provider idempotency key. Retry temporary transport failures after 1 minute, 5 minutes, 30 minutes, and 2 hours, then show `Delivery failed` with a manual retry. Permanent bounces and complaints do not retry.
- Provider webhooks are signature-verified, replay-protected, workspace-bound, and idempotent. Unknown or mismatched message IDs fail closed.
- Production sender domains must pass SPF and DKIM. DMARC is required at `p=none` during verified setup and `quarantine` or `reject` for release acceptance. Reply-to must reach a real Studio contact.
- Invitations are transactional. They contain no tracking pixel, ad attribution, marketing unsubscribe theater, or third-party click tracker. Diony records delivery and acceptance from its own signed workflow, not email opens.
- Logs redact recipient email to a bounded mask and never include the raw invitation, verification code, rendered body, or personal note.

#### Invitation email design and exact content

The email is a compact production call sheet, not a marketing newsletter. It uses a 600 px single-column Parchment sheet on Deep Void, one 4 px Diony Crimson registration rule, a live-text Diony AI wordmark, IBM Plex-compatible system fallbacks, and one high-contrast Crimson action. It does not depend on external fonts, remote images, SVG, JavaScript, animation, or background images to communicate anything.

Required envelope:

- From: `[Studio Name] via Diony AI <verified sender>`
- Reply-to: configured Studio contact
- Subject: `[Inviter Profile Name] invited you to [Studio Name] in Diony AI`
- Preheader: `Review the Studio, choose your Profile Name, and enter Producer.`

Required HTML and equivalent plain-text body:

```text
/ DIONY AI

YOU'VE BEEN
CALLED INTO
[STUDIO NAME].

[Inviter Profile Name] invited you to make in [Studio Name]
as a [Role].

WHAT DIONY AI IS
Diony AI is the Studio's content production system: ideas enter
Producer, the signed production site makes the work, and finished
1080p programs return with their lineage intact.

WHAT YOU RECEIVE
• A Studio Profile Name for your credits and activity
• A personal Cost Center created when you join
• [Role] access to Diony AI Producer

PRODUCTION SITE
[Local Quad-Spark / Leased Dual-Spark / Leased B300 / Leased B200]
You join the Studio. You do not receive cluster credentials.

[Optional personal note, visually labeled “A note from Inviter”]

[ REVIEW AND ACCEPT ]

This invitation expires [absolute date, time, and timezone].
Invitation code: [grouped human-readable fallback code]

Only accept if you expected this invitation. Diony AI will never ask
for the Studio Admin's password, an SSH command, or a GPU credential.

You received this service email because [Inviter Profile Name] invited
[masked or full recipient email] to [Studio Name].
```

The HTML action label is `Review and accept invitation`; the plain-text version places the complete signed HTTPS URL directly beneath the same label. The action opens the verified Studio controller or its installed Diony handoff. It does not accept on click.

Email accessibility and client quality:

- semantic reading order, one `h1`, body text at least 16 px, 48 px minimum action height, and meaningful link text;
- live text for the wordmark and headings; any optional ornament has empty alternative text;
- AA contrast in normal, dark-mode, forced-color, and images-off rendering;
- responsive without horizontal scroll at 320 px;
- complete plain-text MIME alternative and UTF-8 names;
- total HTML under 100 KB, inline email-safe CSS, absolute URLs, no form elements, and no content hidden only in images;
- preview and acceptance tests in Apple Mail, Gmail web/mobile, Outlook desktop/web, and one images-disabled/forced-colors pass.

### Recipient Review and Accept flow

The recipient completes one short progressive sheet. A returning maker with a matching verified Studio identity sees three decisions; a new maker sees one additional email-verification field.

```text
Email: Review and accept
  → Studio passport + “What Diony AI is”
  → verify matching email only when needed
  → confirm/edit Profile Name
  → Accept invitation
  → atomic join receipt
  → Enter Producer
```

1. **Review** — Show Studio Name, inviter Profile Name, role, site class, admission policy, expiry, controller fingerprint, and the exact three-part explanation from the email: Producer access, Profile Name attribution, and personal Cost Center creation. Primary action: `Continue to identity check`. Secondary action: `This is not my invitation`.
2. **Verify** — If the current session already has the invitation's verified email identity, skip this field and say `Email verified as [masked address]`. Otherwise send a six-digit one-time code to the bound address. The code expires after 10 minutes, permits five attempts, can be resent after 30 seconds, and is entered in one accessible field that accepts paste. The UI says `Enter the code sent to [masked address]`; it never reveals whether another address belongs to a Studio member.
3. **Profile** — Prefill the Admin suggestion when present, explain that it is editable, validate Studio uniqueness, and preview `A take by [Profile Name] · [Profile Name] Personal`.
4. **Accept** — Show one final, scannable statement: `Join [Studio] as [Profile Name] with [Role] access. Diony creates your personal Cost Center now.` The explicit action is `Accept invitation`. There is no prechecked consent, countdown, or acceptance on pressing an email link.
5. **Arrival** — Run the atomic join transaction, show the existing `YOU'RE IN THE STUDIO.` receipt, issue the Producer session, and activate `Enter Producer`. This action opens the Studio's authenticated Diony AI Producer at Idea for a first visit or the last permitted bay for a returning member.

The invitation link uses the signed controller origin and places the raw invitation secret in the URL fragment or an equivalent non-logged handoff envelope. The entry page immediately removes secret material from visible history before redemption. Email security scanners, link previews, and `GET` requests may load the review shell but can never consume the token, verify identity, create a request, create a Cost Center, or admit the maker. Only authenticated `POST` transitions mutate state.

If Diony AI is installed, the review page may offer `Open in Diony AI`; otherwise the responsive Producer web surface completes the same flow. The handoff transfers a single-use, origin-bound exchange code—not the invitation token or a long-lived session. If app handoff fails, `Continue here` remains available and preserves the verified step.

The success email is a receipt, not the access mechanism:

- Subject: `You're now part of [Studio Name]`
- Body: role, Profile Name, personal Cost Center code, Studio contact, and `Open Diony AI Producer`.
- It never contains a bearer session, refresh credential, budget total, project data, or cluster connection detail.

### Email lifecycle notifications

When email delivery was selected, the full governance lifecycle also sends:

- recipient invitation email;
- recipient verification code when no matching verified session exists;
- recipient request-received receipt when Studio approval is required;
- Admin notification that a named Profile is waiting, with a safe link to Producer Requests and no approve/deny action inside email;
- recipient approval and Producer-access email after atomic admission;
- recipient denial email with only a shareable Admin note;
- recipient invitation-revoked notice when an active delivered invitation is closed before acceptance;
- removed member security notice and Admin removal receipt after access revocation.

Email actions never approve, deny, remove, transfer authority, or expose cost totals. They open an authenticated Producer surface where the decision and consequence are visible. Every notification is idempotent, audit-linked, preference-independent transactional mail, and visible in the invitation/member activity ledger with truthful delivery state.

### Pending request

Pending state is a durable signed request, not a half-created membership.

- No Membership, personal Cost Center, Producer token, or project access exists while pending.
- The request stores authenticated user ID, requested Profile Name, invitation ID, requested role, created/expiry times, and optional maker note.
- Page heading: `THE STUDIO HAS YOUR REQUEST.`
- Body: `[Studio Name] will review [Profile Name]. You can close this window.`
- Show request time, expiry, Studio contact label, and `Check status`.
- The page may poll with exponential backoff while open. Email reports approval or denial when the invitation used email delivery, but it is never the only way to learn the outcome.
- Withdrawing a request is allowed until approval. Copy: `Withdraw request`; confirm that a new invitation may be required.

On approval, the controller runs the same atomic successful-join transaction. On denial, no Cost Center is created.

### Invitation lifecycle

Invitation statuses are exact: `draft`, `active`, `partially_used`, `used`, `expired`, `revoked`. Email delivery status is a separate state machine and never overwrites invitation status.

- Draft exists before Sign and cannot be redeemed.
- Active is redeemable within policy and use count.
- Partially used applies only to explicitly multi-use invitations and shows remaining admissions.
- Used is immutable and cannot be replayed.
- Expired and revoked are terminal. Revocation is immediate for future redemption but does not remove members who previously joined.

Default invitation behavior is one use, Creator role, 72-hour expiry, and optional intended-email binding. Admin/Owner roles require an Owner action and fresh authentication; an ordinary invite cannot silently grant cost authority.

### Denial

Admin denial supports an optional private note to the requester and a required internal reason category: `identity_unverified`, `invite_mismatch`, `studio_capacity`, `not_expected`, or `other`.

- Requester copy: `This Studio did not approve the request.`
- Show the admin note only if the admin marked it shareable.
- Never expose internal member names, budgets, project titles, or security detail.
- Action: `Return to Studio entry`; `Ask again` appears only if policy and invitation still allow it.

### Member removal

Removal is a high-consequence, reversible-in-record action:

1. Admin chooses `Remove from Studio` from the member overflow menu.
2. Confirmation names the member, Profile Name, role, active sessions, owned/in-flight work count, and personal Cost Center.
3. Admin chooses a successor for unowned approval tasks and active project stewardship when required.
4. Exact confirmation is `REMOVE {PROFILE NAME}`.
5. Controller soft-deletes/suspends Membership, revokes all Studio sessions and device credentials, marks the personal Cost Center inactive for new allocation, cancels pending invitations created solely by that member if policy requires, and appends an audit event.

Consequences:

- Historical projects, generations, notes, approvals, usage, exports, Profile Name snapshots, and Cost Center allocations remain attributable and auditable.
- In-flight GPU work is not killed merely because a member is removed. The run continues under its already recorded Studio and Cost Center dimensions; control transfers to the selected successor.
- The removed maker immediately loses Producer and Studio-management access. Their local cache shows `Access removed` and deletes refresh credentials.
- A Cost/Budgetary Admin cannot remove the last Owner, the last active Cost/Budgetary Admin, or themselves without first transferring the relevant authority.
- `Restore membership` creates a reviewed reactivation with the same identity and historical Cost Center by default; it does not erase the removal audit.

## 5. Cost/Budgetary Admin role and Producer tools

### Authority model

**Cost/Budgetary Admin** is an explicit financial-governance scope backed by `CostGovernance.admin_user_id` and/or `admin_team_id`. It is not inferred from the generic `admin` role. To satisfy Studio governance, the initial Owner holds both Owner membership and Cost/Budgetary Admin scope.

Capabilities:

- rename the Studio;
- create, copy, expire, and revoke invitations;
- review, approve, deny, and annotate join requests;
- view active, pending, removed, and suspended members;
- remove or restore members subject to last-authority protections;
- view all personal and shared Cost Centers, high-level usage attribution, internal-credit budgets, cost-model freshness, and unrated usage;
- transfer Cost/Budgetary Admin authority with fresh authentication and an audit receipt.

Generic Studio Admins may manage membership only when the Owner grants that permission. Only configured Cost/Budgetary Admins see organization-wide cost totals. Only Owners can grant Owner, transfer the last Cost/Budgetary Admin, or erase the Studio.

### Producer navigation

Studio context is always visible in the mast as:

`[Studio Name] · [Profile Name]`

The existing site truth remains separately visible as its current site label. Long names truncate visually but expose full text on focus and in the accessible name.

An authorized `Studio` control opens a dedicated management surface without adding another production bay to Idea → Production → Gallery. On desktop it is a right-side stage drawer up to 560 px wide; deep tasks open a full sheet. On mobile it is a full-screen route with a persistent `Back to Producer` action.

Management sections:

1. **Overview** — Studio Name, signed site, controller health, admission policy, Owner, Cost/Budgetary Admin, member count, pending count.
2. **Members** — Profile Name, role, membership status, joined date, personal Cost Center, last Studio activity, overflow actions.
3. **Requests** — newest first; Profile Name, invitation, requested role, age, identity-verification state, note; `Approve` and `Deny` are always labeled.
4. **Invitations** — status, granted role, intended recipient if any, creator, created/expiry times, use count; `Create invite`, `Copy`, and `Revoke`.
5. **Cost Centers** — one row per personal/shared center with Profile Name owner, internal credits, fixed-point cost, configured threshold, state, rated coverage, and last activity.
6. **Audit** — append-only governance events with actor Profile Name snapshot, time, action, subject, result, and correlation ID.

### Trust patterns

- Mutations use optimistic UI only after the server has persisted an idempotency key. Until then, the button says the exact present-tense action: `Approving…`, `Revoking…`.
- Success toasts name the result: `Maya Chen joined Northlight Pictures.`
- Destructive actions never hide behind swipe-only gestures.
- Revoking an invite is immediately visible and offers `Create replacement`; there is no fake undo after the token is invalidated.
- Renaming the Studio offers a 10-second local undo only if the server supports an idempotent rename-back operation. Otherwise use a confirmation and edit again.
- Empty Requests: `No one is waiting at the door.`
- Empty Invitations: `No open invitations. Create one when the next maker is ready.`
- Empty Cost activity: `No production use has landed on this Cost Center yet.`

## 6. Credit and Cost Center behavior

- Internal credits are workload attribution, never a purchasable wallet, currency, subscription, or paywall.
- Every joined maker gets exactly one active personal Cost Center on successful admission. Pending and denied requests get none.
- A Studio may have any number of active personal and named shared Cost Centers. A member may be assigned to multiple centers, while their personal center remains permanently included until membership removal.
- Only the configured Cost/Budgetary Admin may create or rename shared Cost Centers or change a member's Cost Center assignments. A Creator, Producer, Viewer, or generic membership Admin cannot self-assign budget scope unless that identity is also the configured Cost/Budgetary Admin.
- Shared Cost Center names are required, human-readable Studio labels. Renaming changes the current display name but never rewrites historical allocation snapshots or the stable Cost Center ID/code.
- The Cost Center display name defaults to `[Profile Name] · Personal`; its stable code never changes when the Profile Name changes.
- Budget fields default to unconfigured (`0` in the existing v1 contract) and the UI says `No threshold set`, not `$0 remaining`.
- Budget warnings are monitor-only. A warning or over-budget state never disables Produce, Retake, Finish, export, or Distribution.
- Usage resolves the authenticated acting membership first, then its `personal_cost_center_id`. A Producer with permission may choose a shared project Cost Center before a run. The chosen center is frozen onto each usage event at dispatch.
- Credits and fixed-point cost are appended to usage and allocation records. Historical entries keep `profile_name_snapshot`, `cost_center_name_snapshot`, exact model/provider, project/run/job IDs, and measurement source.
- If cost allocation cannot resolve, the production action fails before provider dispatch with `COST_CENTER_UNRESOLVED`; it never sends work and attributes it later by guess.
- If rate data is missing, work may proceed and remains visibly `unrated` per Blueprint 16.
- Removing a member closes the personal Cost Center to new work but retains all allocations and reconciliation.

## 7. Data contracts and API integration

### Contract changes

Extend existing launch contracts; do not create parallel Studio-only versions.

| Contract | Required change |
|---|---|
| `Workspace` | Treat as Studio; add `admission_policy`, public controller identity/fingerprint metadata, and rename audit semantics. |
| `Membership` | Add `profile_name`, `profile_name_key`, `personal_cost_center_id`, `assigned_cost_center_ids`, `status`, `joined_at`, `removed_at`, and attribution-safe snapshots where needed. |
| `User` | Remains identity-level. `display_name` is not the Studio Profile Name authority. |
| `CostCenter` | Add member ownership/reference and lifecycle reason, or an equivalent typed association. Keep `monitor_only`. |
| New `StudioInvitation` | ID, Studio ID, token hash, role, admission mode, intended identity binding, status, use limit/count, expiry, creator, timestamps. |
| New `StudioJoinRequest` | Studio/user/invitation IDs, requested Profile Name/key and role, status, maker/admin notes, decision actor, timestamps. |
| New `StudioSession` or security record | Studio membership, device, token family/hash, issued/expiry/revoked times. Never serialize secrets to exports. |
| `AuditEvent` | Add invitation, join-request, membership, Profile Name, Studio rename, authority transfer, and Cost Center lifecycle actions. |
| `BootstrapContext` | Return Studio display, membership Profile Name, personal Cost Center summary, permissions, and pending-state absence explicitly. |

All new contracts are Pydantic v2 models with generated JSON Schema, UTC timestamps, soft-delete/status semantics, bounded strings, and closed enums. Migration backfills the local Owner with Profile Name `Local maker` and binds the existing `local-production` center as their personal center without duplicating it.

### API shape

Public pre-session routes expose only the minimum join exchange. Authenticated governance stays under `/api/v1`.

| Method and route | Purpose | Authority |
|---|---|---|
| `POST /api/studio-entry/discover` | Validate invitation envelope and return signed Studio passport/challenge | invitation proof; strict rate limit |
| `POST /api/studio-entry/authenticate` | Complete Studio-configured identity challenge | signed challenge |
| `POST /api/studio-entry/join` | Validate Profile Name; admit atomically or create request | authenticated join session |
| `GET /api/studio-entry/requests/{receipt}` | Read only that request's status | opaque request receipt |
| `DELETE /api/studio-entry/requests/{receipt}` | Withdraw a pending request | opaque request receipt + auth proof |
| `GET /api/v1/studio` | Studio context and effective permissions | member |
| `PATCH /api/v1/studio` | Rename Studio/change policy | Cost/Budgetary Admin; Owner for closed/open authority changes |
| `GET/POST /api/v1/studio/invitations` | List/create invitations | permitted Admin |
| `POST /api/v1/studio/invitations/{id}/revoke` | Revoke active invitation | permitted Admin |
| `GET /api/v1/studio/join-requests` | Review queue | permitted Admin |
| `POST /api/v1/studio/join-requests/{id}/approve` | Atomic membership + Cost Center creation | permitted Admin |
| `POST /api/v1/studio/join-requests/{id}/deny` | Deny with reason/note | permitted Admin |
| `PATCH /api/v1/studio/members/{id}` | Role or Profile Name change within authority | self or permitted Admin, field-scoped |
| `POST /api/v1/studio/members/{id}/remove` | Remove with successor and confirmation | permitted Admin |
| `POST /api/v1/studio/members/{id}/restore` | Reviewed reactivation | permitted Admin |
| `PUT /api/v1/studio/members/{id}/cost-centers` | Replace the member's active assignment set while retaining their personal center | configured Cost/Budgetary Admin |
| `PATCH /api/v1/cost-centers/{id}` | Rename a current personal/shared Cost Center without rewriting historical snapshots | configured Cost/Budgetary Admin |
| `GET /api/v1/studio/cost-centers` | Member-scoped or organization rollup | existing cost scope |

Every mutation accepts `Idempotency-Key`, returns `X-Correlation-ID`, and writes an audit event. Authentication, invitation redemption, approval, denial, rename, authority transfer, and removal have stricter per-IP and per-invitation rate limits than ordinary control-plane reads.

### Studio and Producer boundaries

- Studio owns entry, new-Studio bootstrap, join discovery, authentication, pending status, and handoff.
- Producer owns the signed Studio authority, sessions, membership, Cost Centers, governance UI, audit, and authorization.
- Desk opens Studio when no usable Studio session exists. If exactly one valid Studio session exists, Desk may offer `Continue to [Studio]` plus `Use another Studio`; it must not silently skip a pending or failed join state.
- A Local Studio is reached through its Producer controller on the trusted local network. A leased Studio is reached through the controller address signed into the invitation. In both cases the UI and contracts are identical.
- The controller may proxy production to a signed leased GPU through the existing Connect line. A remote member never receives that line.
- `site.json` proves production site configuration. It is not a member credential or invitation.

## 8. Failure and recovery matrix

Every failure page keeps the verified Studio Name only when identity verification succeeded. It never implies membership before the atomic join completes.

| Code/state | Maker-facing copy | Recovery |
|---|---|---|
| `INVITE_MALFORMED` | `This is not a Diony Studio invitation.` | Paste the full link/code; return to entry. |
| `INVITE_EXPIRED` | `This invitation expired on [date].` | `Ask for a new invite`; no retry loop. |
| `INVITE_REVOKED` | `This Studio closed the invitation.` | Ask the Studio Admin for a replacement. |
| `INVITE_USED` | `This one-use invitation has already brought in a maker.` | Sign in if already joined; otherwise request a new invite. |
| `INVITE_IDENTITY_MISMATCH` | `This invitation was made for a different identity.` | Authenticate as the intended identity or contact Admin. Do not reveal the intended address in full. |
| `STUDIO_CLOSED` | `[Studio Name] is not accepting joins right now.` | Return to entry; contact Studio. |
| `STUDIO_OFFLINE` | `[Studio Name] is offline or cannot be reached.` | Check network/VPN/local controller; `Try again`; open diagnostics. |
| `TLS_IDENTITY_FAILED` | `The Producer identity does not match the invitation.` | Stop. Do not offer bypass. Ask Admin for a fresh invitation. |
| `NETWORK_INTERRUPTED` | `The line dropped before the Studio answered.` | Safe retry with same idempotency key. |
| `AUTH_FAILED` | `The Studio could not verify this sign-in.` | Retry method; recover identity; do not erase verified passport. |
| `PERMISSION_DENIED` | `This invitation cannot grant the requested Studio role.` | Join with the granted role or ask an Owner. |
| `PROFILE_NAME_TAKEN` | `That Profile Name is already in use here.` | Edit with specific suggestion. |
| `JOIN_PENDING` | `The Studio has your request.` | Check/withdraw; no Producer access. |
| `JOIN_DENIED` | `This Studio did not approve the request.` | Show shareable note; return to entry. |
| `JOIN_COMMIT_UNKNOWN` | `The Studio may have finished your join.` | `Check my place` performs idempotent lookup before any retry. |
| `COST_CENTER_CREATE_FAILED` | `Your Studio place was not opened. No production access was granted.` | Admin sees correlation ID; retry atomic transaction. Never leave orphan membership. |
| `SESSION_EXPIRED` | `Your Studio session ended.` | Re-authenticate; preserve unsent local form text. |
| `MEMBERSHIP_REMOVED` | `[Studio Name] removed this Studio access.` | Clear credentials; show contact label and export rights already granted. |
| `RATE_LIMITED` | `Too many attempts reached this Studio.` | Show exact retry time; preserve non-secret fields. |

Diagnostics show controller origin label, timestamps, phase, public fingerprint, correlation ID, and network hints. They never show raw tokens, cookies, email in full, SSH details, vault paths, stack traces, or provider credentials. `Copy diagnostics` applies the same redaction.

Offline join is not faked. An air-gapped invitation file can carry identity and trust material, but admission still requires a reachable signed Producer controller on an allowed local path.

## 9. Responsive and accessibility contract

### Responsive

- At 760 CSS px and above, the entry halves sit side by side with equal dimensions.
- Below 760 px, they stack in DOM order with a horizontal crimson seam. Each action remains visible without horizontal scrolling at 320 CSS px.
- The mobile display reduces in size; it does not truncate Start/Join meaning or turn Join into a link.
- Studio management becomes a full-screen route. Tables transform into labeled definition rows; action menus remain buttons with text alternatives.
- Fixed footers account for safe-area insets and never cover error, consent, or Continue controls.
- Virtual keyboards must not move the Profile Name preview out of reach or hide the primary action.

### Keyboard and assistive technology

- All flows work without pointer, drag, hover, long-press, or clipboard access.
- On route changes, focus moves to the page `h1`; on inline errors, focus remains in the field and the error is referenced by `aria-describedby`.
- Loading, pending, approval, denial, revocation, and arrival changes announce one concise message through the appropriate polite/assertive live region.
- Connection progress exposes text; animation and color are never the only state.
- Confirmation dialogs use native dialog semantics, trap focus while open, restore focus to the invoking control, close with Escape unless a commit is in progress, and name consequences before the action.
- Invite codes may be grouped visually but are read and copied as one token. Never force character-by-character entry.
- Profile Name uniqueness results are announced after validation without announcing on every keystroke.
- Minimum 44×44 CSS-pixel touch targets. Working text meets WCAG 2.2 AA; major display/actions target AAA where practical.
- Stage Amber provides the visible focus ring on both Parchment and Deep Void. Status always includes text/icon shape in addition to color.

### Motion and cognition

- One 620–700 ms entrance is allowed; decision and input controls are usable immediately.
- Seam/Chorus feedback is 120–220 ms; connection state changes are 220–420 ms only when movement explains progress.
- `prefers-reduced-motion: reduce` removes seam travel, Chorus adjustment, drawer slide, and success flourish. It keeps instant state text and focus.
- No countdown auto-redirect after joining. `Enter Producer` is an explicit action, with an optional calm automatic handoff only after the arrival receipt has been announced and when the user has not requested reduced motion.
- Plain verbs and short paragraphs keep the system comprehensible under production pressure.

## 10. Microcopy voice

### Voice rules

- Speak maker to maker: `Studio`, `maker`, `production`, `invite`, `Profile Name`, `Cost Center`, `use`.
- Name the authority honestly: `Owner`, `Cost/Budgetary Admin`, `Producer controller`, `signed site`.
- Say what happened and what can happen next. Never use `Something went wrong`, `Oops`, `Submit`, `onboard`, `seat`, `tenant`, `resource allocation`, or `billing wallet` in maker-facing copy.
- Ownership is factual, not promotional: `Your Studio owns the project files and lineage.`
- Join is active and legitimate: `Join the Studio`, never `Request guest access` unless the Studio policy actually requires approval.
- Separate site from Studio: a Studio has a production site; a person joins the Studio, not the B300.
- Separate Profile Name from sign-in identity and Cost Center from money.

### Example language

| Moment | Copy |
|---|---|
| Start support | `Build the production system and hold its first budget view.` |
| Join support | `Bring an invitation. Your work arrives with your name and Cost Center attached.` |
| Discovery | `Finding the Producer that signed this invitation…` |
| Verification | `Check the Studio before you enter.` |
| Profile | `This is how your work reads in the room.` |
| Pending | `The Studio has your request. Nothing has been charged or created yet.` |
| Approval toast | `Maya Chen joined Northlight Pictures.` |
| Invite revoke toast | `Invitation revoked. It cannot open the Studio now.` |
| Cost warning | `This Cost Center crossed its monitoring threshold. Production remains open.` |
| Removal confirm | `Maya loses Studio access now. Their finished work and cost history stay attributed.` |
| Offline | `The Studio is not answering on its signed line.` |
| Arrival | `You are now part of Northlight Pictures.` |

## Implementation sequence

Contracts first:

1. Add invitation, join-request, membership Profile Name/personal Cost Center, lifecycle, and session contracts plus JSON Schemas.
2. Write migrations and idempotent service tests, including local bootstrap backfill.
3. Implement token hashing, signed discovery challenge, admission policy, atomic join/approval, session revocation, and authority protections.
4. Add versioned API routes with authorization, rate limits, redaction, audit, and correlation IDs.
5. Build the Studio dual entry and full Start/Join state machine.
6. Add Producer Studio context and governance surfaces.
7. Bind acting membership to workflow Cost Center resolution and immutable attribution snapshots.
8. Verify keyboard, screen-reader semantics, responsive layout, reduced motion, and rendered Diony Chorus quality.
9. Prove local controller, leased controller, interrupted transaction, expired/revoked invite, pending approval, removal, and session-revocation paths separately.

No runtime implementation is complete until the Blueprint 16 control plane—not browser local storage—owns the durable truth.

## Final quality checklist

### Product coherence

- [ ] The first page asks Start or Join before hardware and nothing else.
- [ ] Start and Join have equal visual and semantic weight.
- [ ] Start preserves every existing site-specific Studio rail and Sign boundary.
- [ ] Studio, site, controller, Profile Name, role, and Cost Center are never conflated.
- [ ] Local and leased Studios present the same join states and permissions.
- [ ] Joined makers arrive in the existing Idea → Production → Gallery product with correct permissions.

### Security and authority

- [ ] Raw invitation/session tokens never persist in logs, exports, analytics, or local storage.
- [ ] Discovery cannot become an SSRF/open-redirect path.
- [ ] Controller identity is verified before authentication or Profile Name submission.
- [ ] Membership plus personal Cost Center creation is atomic and idempotent.
- [ ] Pending/denied requests have no Producer access or Cost Center.
- [ ] Generic Admin and Cost/Budgetary Admin remain distinct scopes.
- [ ] Last Owner and last Cost/Budgetary Admin protections are enforced server-side.
- [ ] Removal revokes Studio sessions immediately and keeps history attributable.

### Cost truth

- [ ] Every successful member has exactly one active personal Cost Center.
- [ ] `0` budget renders as `No threshold set`.
- [ ] Internal credits never appear as purchasable money or block production.
- [ ] Usage freezes Profile Name and Cost Center attribution at event time.
- [ ] Unrated and over-budget states remain honest and non-blocking.
- [ ] Organization rollups remain restricted to configured cost authority and reconcile to zero difference.

### Experience craft

- [ ] Exact copy is active, specific, and maker-oriented.
- [ ] Empty, loading, pending, partial, success, expired, revoked, offline, denied, and removed states are implemented.
- [ ] No blank page, vague error, decorative spinner-only state, or silent retry exists.
- [ ] The Studio seam is the one new signature; no extra decorative system competes with it.
- [ ] Profile Name appears consistently on activity, generations, approvals, exports, and cost attribution.
- [ ] Arrival names the Studio, Profile Name, role, site, and personal Cost Center.

### Accessibility and responsive proof

- [ ] Complete Start and Join flows work at 320 px, desktop, 200% zoom, keyboard only, and reduced motion.
- [ ] Screen-reader announcements are concise and ordered.
- [ ] Focus is visible, restored after dialogs, and moved intentionally on route changes.
- [ ] Status is never color-only and all working text meets WCAG 2.2 AA.
- [ ] Touch targets, safe areas, virtual keyboards, and long localized names are verified.

### Evidence layers

- [ ] Contract and service tests prove state, uniqueness, atomicity, idempotency, authority, revocation, allocation, and redaction.
- [ ] API integration tests prove status codes, audit/correlation records, rate limits, and cross-Studio isolation.
- [ ] Browser tests prove both entry flows and every recovery state with no console errors.
- [ ] Rendered desktop/mobile screenshots are reviewed against Blueprint 15.
- [ ] A live local Studio proves trusted-network discovery and joining.
- [ ] A live signed leased Studio proves remote HTTPS discovery, Producer handoff, and GPU credential isolation.
- [ ] Physical multi-user/session revocation proof is recorded separately from laptop pytest.
- [ ] No claim of “complete” merges local tests, deployed-controller health, live lease, or device evidence into one proof layer.

The release bar is simple: a maker always knows which Studio they are entering, what authority they have, whose production system will run the work, and where that work's use is attributed.
