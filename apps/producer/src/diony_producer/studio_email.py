"""Accessible transactional email for Studio invitations and access receipts."""

from __future__ import annotations

import html
import json
import os
import re
import smtplib
import time
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from pathlib import Path

from diony_contracts.launch import (
    EmailDeliveryStatus,
    InvitationEmailDelivery,
    StudioInvitation,
    Workspace,
    utc_now,
)
from diony_producer.control_plane import ControlPlane
from diony_producer.security import SecretVault


@dataclass(frozen=True)
class RenderedEmail:
    to: str
    subject: str
    text: str
    html: str


def recipient_mask(address: str) -> str:
    local, _, domain = address.partition("@")
    if not local or not domain:
        return "•••"
    return f"{local[:1]}•••@{domain}"


def _safe(value: str) -> str:
    return html.escape(value, quote=True)


def _email_shell(*, preheader: str, heading: str, body: str, action: str, url: str) -> str:
    return f"""<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>{_safe(heading)}</title></head>
<body style="margin:0;background:#16191F;color:#16191F;font-family:Arial,Helvetica,sans-serif;">
  <div style="display:none;max-height:0;overflow:hidden;opacity:0;">{_safe(preheader)}</div>
  <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#16191F;padding:24px 12px;">
    <tr><td align="center">
      <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="max-width:600px;background:#F4F0E6;border-top:4px solid #C41E3A;">
        <tr><td style="padding:30px 32px 10px;color:#C41E3A;font-weight:700;letter-spacing:.08em;">/ DIONY AI</td></tr>
        <tr><td style="padding:0 32px 10px;color:#716F6A;font-size:13px;font-weight:700;">Own Your Studio, Produce at Cost</td></tr>
        <tr><td style="padding:10px 32px 32px;">
          <h1 style="margin:0 0 24px;font-family:Impact,'Arial Black',sans-serif;font-size:42px;line-height:.98;letter-spacing:-.03em;color:#16191F;">{_safe(heading)}</h1>
          {body}
          <table role="presentation" cellspacing="0" cellpadding="0" style="margin:28px 0 22px;"><tr><td bgcolor="#C41E3A">
            <a href="{_safe(url)}" style="display:inline-block;min-height:48px;line-height:48px;padding:0 24px;color:#F4F0E6;text-decoration:none;font-weight:700;">{_safe(action)}</a>
          </td></tr></table>
          <p style="margin:24px 0 0;font-size:13px;line-height:1.55;color:#716F6A;">Only accept if you expected this invitation. Diony AI will never ask for the Studio Admin's password, an SSH command, or a GPU credential.</p>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""


def render_invitation_email(
    *,
    workspace: Workspace,
    invitation: StudioInvitation,
    inviter_profile_name: str,
    accept_url: str,
    site_label: str,
) -> RenderedEmail:
    studio = workspace.name
    role = invitation.role.value.title()
    subject = f"{inviter_profile_name} invited you to {studio} in Diony AI"
    preheader = "Review the Studio, choose your Profile Name, and enter Producer."
    note_text = invitation.note.strip()
    note_html = (
        f'<div style="margin:22px 0;padding:16px;border-left:3px solid #E5AC48;"><p style="margin:0 0 6px;font-size:12px;font-weight:700;letter-spacing:.08em;">A NOTE FROM {_safe(inviter_profile_name).upper()}</p><p style="margin:0;font-size:16px;line-height:1.55;white-space:pre-wrap;">{_safe(note_text)}</p></div>'
        if note_text
        else ""
    )
    body = f"""
<p style="font-size:18px;line-height:1.55;margin:0 0 24px;"><strong>{_safe(inviter_profile_name)}</strong> invited you to make in <strong>{_safe(studio)}</strong> as a <strong>{_safe(role)}</strong>.</p>
<p style="font-size:12px;font-weight:700;letter-spacing:.08em;margin:0 0 6px;">WHAT DIONY AI IS</p>
<p style="font-size:16px;line-height:1.55;margin:0 0 22px;">Diony AI is the Studio's content production system: ideas enter Producer, the signed production site makes the work, and finished 1080p programs return with their lineage intact.</p>
<p style="font-size:12px;font-weight:700;letter-spacing:.08em;margin:0 0 6px;">WHAT YOU RECEIVE</p>
<ul style="font-size:16px;line-height:1.65;margin:0 0 22px;padding-left:22px;"><li>A Studio Profile Name for your credits and activity</li><li>A personal Cost Center created when you join</li><li>{_safe(role)} access to Diony AI Producer</li></ul>
<p style="font-size:12px;font-weight:700;letter-spacing:.08em;margin:0 0 6px;">PRODUCTION SITE</p>
<p style="font-size:16px;line-height:1.55;margin:0;"><strong>{_safe(site_label)}</strong><br>You join the Studio. You do not receive cluster credentials.</p>
{note_html}
<p style="font-size:14px;line-height:1.55;margin:18px 0 0;">This invitation expires <strong>{_safe(invitation.expires_at.strftime('%B %d, %Y at %H:%M UTC'))}</strong>.</p>"""
    heading = f"YOU'VE BEEN CALLED INTO {studio.upper()}."
    html_body = _email_shell(
        preheader=preheader,
        heading=heading,
        body=body,
        action="Review and accept invitation",
        url=accept_url,
    )
    text = f"""/ DIONY AI

YOU'VE BEEN CALLED INTO {studio.upper()}.

{inviter_profile_name} invited you to make in {studio} as a {role}.

WHAT DIONY AI IS
Diony AI is the Studio's content production system: ideas enter Producer, the signed production site makes the work, and finished 1080p programs return with their lineage intact.

WHAT YOU RECEIVE
- A Studio Profile Name for your credits and activity
- A personal Cost Center created when you join
- {role} access to Diony AI Producer

PRODUCTION SITE
{site_label}
You join the Studio. You do not receive cluster credentials.

{('A note from ' + inviter_profile_name + ': ' + note_text) if note_text else ''}

Review and accept invitation:
{accept_url}

This invitation expires {invitation.expires_at.strftime('%B %d, %Y at %H:%M UTC')}.

Only accept if you expected this invitation. Diony AI will never ask for the Studio Admin's password, an SSH command, or a GPU credential.
"""
    return RenderedEmail(to=invitation.email, subject=subject, text=text, html=html_body)


def render_verification_email(
    *, workspace: Workspace, invitation: StudioInvitation, code: str
) -> RenderedEmail:
    subject = f"Your {workspace.name} verification code"
    text = f"Your Diony AI verification code for {workspace.name} is {code}. It expires in 10 minutes."
    body = f'<p style="font-size:18px;line-height:1.55;">Use this one-time code to verify the invitation for <strong>{_safe(workspace.name)}</strong>.</p><p style="font-family:monospace;font-size:34px;letter-spacing:.18em;font-weight:700;">{_safe(code)}</p><p style="font-size:15px;line-height:1.55;">It expires in 10 minutes. If you did not request it, leave the invitation closed.</p>'
    html_body = _email_shell(
        preheader=f"Your verification code for {workspace.name}",
        heading="VERIFY THE INVITATION.",
        body=body,
        action="Return to the invitation",
        url=os.environ.get("DIONY_PRODUCER_PUBLIC_URL", "http://producer.diony.local"),
    )
    return RenderedEmail(to=invitation.email, subject=subject, text=text, html=html_body)


def render_access_ready_email(
    *,
    workspace: Workspace,
    invitation: StudioInvitation,
    profile_name: str,
    cost_center_code: str,
    producer_url: str,
) -> RenderedEmail:
    subject = f"You're now part of {workspace.name}"
    text = f"""/ DIONY AI

YOU'RE IN THE STUDIO.

You are now part of {workspace.name} as {profile_name}.

Role: {invitation.role.value.title()}
Personal Cost Center: {cost_center_code}

Open Diony AI Producer:
{producer_url}

Internal credits show production use. They are not money and they do not block a run.
"""
    body = f'<p style="font-size:18px;line-height:1.55;">You are now part of <strong>{_safe(workspace.name)}</strong> as <strong>{_safe(profile_name)}</strong>.</p><p style="font-size:16px;line-height:1.6;"><strong>Role</strong><br>{_safe(invitation.role.value.title())}<br><br><strong>Personal Cost Center</strong><br>{_safe(cost_center_code)}</p><p style="font-size:14px;line-height:1.55;color:#716F6A;">Internal credits show production use. They are not money and they do not block a run.</p>'
    html_body = _email_shell(
        preheader=f"Your Producer access to {workspace.name} is ready.",
        heading="YOU'RE IN THE STUDIO.",
        body=body,
        action="Open Diony AI Producer",
        url=producer_url,
    )
    return RenderedEmail(
        to=invitation.email, subject=subject, text=text, html=html_body
    )


def render_membership_status_email(
    *,
    to: str,
    workspace: Workspace,
    profile_name: str,
    status_kind: str,
    producer_url: str,
    note: str = "",
) -> RenderedEmail:
    if status_kind == "approval_pending":
        subject = f"{workspace.name} received your Studio request"
        heading = "YOUR REQUEST IS WITH THE STUDIO."
        summary = (
            f"{workspace.name} received your request to join as {profile_name}. "
            "An Admin will review it. No Producer access or Cost Center exists until approval."
        )
        action = "Return to Diony AI"
    elif status_kind == "denied":
        subject = f"An update on your {workspace.name} request"
        heading = "THE STUDIO DID NOT OPEN ACCESS."
        summary = (
            f"{workspace.name} did not approve the request for {profile_name}. "
            "No membership or Cost Center was created."
        )
        action = "Return to Diony AI"
    elif status_kind == "removed":
        subject = f"Your {workspace.name} access changed"
        heading = "YOUR STUDIO ACCESS HAS CLOSED."
        summary = (
            f"{profile_name} no longer has access to {workspace.name}. "
            "Existing work, credits, and cost history remain attributed; new production access is closed."
        )
        action = "Open Diony AI"
    else:
        raise ValueError(f"Unknown membership email status: {status_kind}")
    clean_note = note.strip()
    note_text = f"\n\nA note from the Studio Admin:\n{clean_note}" if clean_note else ""
    text = f"/ DIONY AI\n\n{heading}\n\n{summary}{note_text}\n\n{action}:\n{producer_url}\n"
    note_html = (
        f'<div style="margin:22px 0;padding:16px;border-left:3px solid #E5AC48;"><p style="margin:0 0 6px;font-size:12px;font-weight:700;letter-spacing:.08em;">A NOTE FROM THE STUDIO ADMIN</p><p style="margin:0;font-size:16px;line-height:1.55;white-space:pre-wrap;">{_safe(clean_note)}</p></div>'
        if clean_note
        else ""
    )
    body = f'<p style="font-size:18px;line-height:1.55;">{_safe(summary)}</p>{note_html}'
    return RenderedEmail(
        to=to,
        subject=subject,
        text=text,
        html=_email_shell(
            preheader=summary,
            heading=heading,
            body=body,
            action=action,
            url=producer_url,
        ),
    )


class StudioMailer:
    def __init__(self, plane: ControlPlane) -> None:
        self.plane = plane

    def deliver(
        self, delivery: InvitationEmailDelivery, rendered: RenderedEmail
    ) -> InvitationEmailDelivery:
        vault = SecretVault(self.plane)
        payload = json.dumps(rendered.__dict__, separators=(",", ":"))
        vault.put(delivery.workspace_id, "studio-email-outbox", delivery.id, payload)
        delivery.attempts += 1
        delivery.updated_at = utc_now()
        transport = os.environ.get("DIONY_EMAIL_TRANSPORT", "disabled").strip().lower()
        delivery.transport = transport
        try:
            if transport == "capture":
                message_id = self._capture(delivery, rendered)
            elif transport == "smtp":
                message_id = self._smtp(delivery, rendered)
            else:
                raise RuntimeError("Studio email is not ready.")
        except Exception as exc:
            delivery.status = EmailDeliveryStatus.FAILED
            delivery.error = re.sub(r"\s+", " ", str(exc)).strip()[:240]
        else:
            delivery.status = EmailDeliveryStatus.SENT
            delivery.provider_message_id = message_id
            delivery.error = ""
            delivery.sent_at = utc_now()
            self.plane.delete_encrypted_secret(
                delivery.workspace_id, "studio-email-outbox", delivery.id
            )
        delivery.updated_at = utc_now()
        self.plane.put_document(
            "invitation_email_delivery",
            delivery.id,
            delivery.workspace_id,
            delivery.model_dump(mode="json"),
        )
        return delivery

    def retry(self, delivery: InvitationEmailDelivery) -> InvitationEmailDelivery:
        vault = SecretVault(self.plane)
        raw = vault.get(delivery.workspace_id, "studio-email-outbox", delivery.id)
        rendered = RenderedEmail(**json.loads(raw))
        return self.deliver(delivery, rendered)

    @staticmethod
    def _message(delivery: InvitationEmailDelivery, rendered: RenderedEmail) -> EmailMessage:
        message = EmailMessage()
        message["Date"] = formatdate(localtime=False)
        message["Message-ID"] = make_msgid(idstring=delivery.id, domain="diony.local")
        message["From"] = os.environ.get(
            "DIONY_EMAIL_FROM", "Diony AI <invites@diony.local>"
        )
        reply_to = os.environ.get("DIONY_EMAIL_REPLY_TO", "").strip()
        if reply_to:
            message["Reply-To"] = reply_to
        message["To"] = rendered.to
        message["Subject"] = rendered.subject
        message.set_content(rendered.text)
        message.add_alternative(rendered.html, subtype="html")
        return message

    def _capture(self, delivery: InvitationEmailDelivery, rendered: RenderedEmail) -> str:
        message = self._message(delivery, rendered)
        root = self.plane.path.parent / "mail-capture"
        root.mkdir(parents=True, exist_ok=True)
        path = root / f"{time.time_ns()}-{delivery.id}.eml"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        fd = os.open(path, flags, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(message.as_bytes())
        return str(message["Message-ID"])

    def _smtp(self, delivery: InvitationEmailDelivery, rendered: RenderedEmail) -> str:
        host = os.environ.get("DIONY_SMTP_HOST", "").strip()
        if not host:
            raise RuntimeError("DIONY_SMTP_HOST is required for Studio email.")
        port = int(os.environ.get("DIONY_SMTP_PORT", "587"))
        user = os.environ.get("DIONY_SMTP_USERNAME", "") or os.environ.get(
            "DIONY_SMTP_USER", ""
        )
        password = os.environ.get("DIONY_SMTP_PASSWORD", "")
        message = self._message(delivery, rendered)
        with smtplib.SMTP(host, port, timeout=20) as client:
            if os.environ.get("DIONY_SMTP_STARTTLS", "true").strip().lower() not in {
                "0",
                "false",
                "no",
                "off",
            }:
                client.starttls()
            if user:
                client.login(user, password)
            refused = client.send_message(message)
            if refused:
                raise RuntimeError("The recipient server refused this invitation.")
        return str(message["Message-ID"])
