"""Transactional email through Amazon SES: password resets and invites.

The server sends with the EC2 instance role (no mail credentials anywhere); the
role may send only from the dexio.wiki identity. DEXIO_MAIL=ses turns sending
on; without it (local runs, tests) messages are logged instead of sent.
Nothing here sends marketing email.

Every message goes out as HTML with a plain-text alternative. The HTML matches
the sign-in pages: the app icon and the wordmark over a white card on the pale
page colour, one blue button, and the link written out under it for clients
that block the button. Email clients ignore most CSS, so the layout is tables
with inline styles. The wordmark is live text rather than an image, so clients
that recolour a message for dark mode (Gmail's apps, Outlook) keep it legible;
Apple Mail, which honours `prefers-color-scheme`, gets the app's dark colours.
The icon is a PNG on dexio.wiki because Gmail shows neither SVG nor data: images.
"""
from __future__ import annotations

import html
import logging
import os

logger = logging.getLogger(__name__)

SITE = "https://dexio.wiki"
ICON = SITE + "/brand/dexio-email-icon.png"   # the app icon, 120 px, shown at 32
FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"
SERIF = "'Libertinus Serif',Georgia,'Times New Roman',serif"   # the wordmark's face

# The app's light and dark colours (themes.py), which email CSS cannot read.
INK, MUTED, LINK, BTN = "#1b1f23", "#5b646c", "#0077c8", "#0077c8"
PAGE, CARD, LINE = "#f6f8fa", "#ffffff", "#d3d8dd"


def enabled() -> bool:
    return os.environ.get("DEXIO_MAIL", "") == "ses"


def sender() -> str:
    return os.environ.get("DEXIO_MAIL_FROM", "Dexio <no-reply@dexio.wiki>")


def reply_to() -> str:
    return os.environ.get("DEXIO_MAIL_REPLY_TO", "support@dexio.wiki")


def contact_to() -> str:
    """Where messages from the in-app contact form go."""
    return os.environ.get("DEXIO_CONTACT_TO", "support@dexio.wiki")


def send(to: str, subject: str, text: str, html_body: str | None = None,
         reply_to_addr: str | None = None) -> bool:
    """Send one message, plain text plus an optional HTML version. True if SES
    accepted it. Never raises: a failed send is logged and the caller shows the
    same page either way. reply_to_addr replaces the usual Reply-To (the contact
    form's messages are answered to the person who wrote them)."""
    if not enabled():
        logger.info("mail disabled; would send %r to %s", subject, to)
        return False
    try:
        import boto3  # server extra; imported here so the engine stays dependency-free

        body = {"Text": {"Data": text, "Charset": "UTF-8"}}
        if html_body:
            body["Html"] = {"Data": html_body, "Charset": "UTF-8"}
        client = boto3.client("sesv2", region_name=os.environ.get("DEXIO_SES_REGION",
                                                                   "us-east-1"))
        client.send_email(
            FromEmailAddress=sender(),
            Destination={"ToAddresses": [to]},
            ReplyToAddresses=[reply_to_addr or reply_to()],
            Content={"Simple": {
                "Subject": {"Data": subject, "Charset": "UTF-8"},
                "Body": body,
            }},
        )
        logger.info("mail sent: %r to %s", subject, to)
        return True
    except Exception as e:  # SES errors, throttling, sandbox refusals
        logger.warning("mail to %s failed: %s", to, e)
        return False


def esc(value: str) -> str:
    return html.escape(value, quote=True)


def layout(*, subject: str, preview: str, heading: str, body: str, button: str,
           link: str, note: str, footer: str) -> str:
    """One message in the house layout. heading, body, note and footer are HTML
    the caller has already escaped; button, link, subject and preview are plain
    text and are escaped here."""
    p = f"margin:0;font-family:{FONT};"
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="x-apple-disable-message-reformatting">
<meta name="color-scheme" content="light dark">
<meta name="supported-color-schemes" content="light dark">
<title>{esc(subject)}</title>
<style>
  @font-face {{ font-family:'Libertinus Serif'; font-weight:600; font-style:normal;
    src:url({SITE}/fonts/libertinus-serif-semibold.woff2) format('woff2'); }}
  body {{ margin:0; padding:0; -webkit-text-size-adjust:100%; }}
  @media (max-width:600px) {{
    .outer {{ padding:24px 12px !important; }}
    .card {{ padding:28px 22px !important; }}
  }}
  @media (prefers-color-scheme:dark) {{
    .page {{ background:#0d1117 !important; }}
    .card {{ background:#161b22 !important; border-color:#3d444d !important; }}
    .ink {{ color:#e6edf3 !important; }}
    .muted {{ color:#9198a1 !important; }}
    .link {{ color:#38bdf8 !important; }}
    .rule {{ border-color:#30363d !important; }}
  }}
</style>
</head>
<body class="page" style="margin:0;padding:0;background:{PAGE};">
<div style="display:none;font-size:1px;line-height:1px;max-height:0;max-width:0;opacity:0;overflow:hidden;mso-hide:all;">{esc(preview)}{"&#8199;&#847; " * 60}</div>
<table role="presentation" class="page" width="100%" cellpadding="0" cellspacing="0" border="0" style="background:{PAGE};">
<tr><td class="outer" align="center" style="padding:40px 16px;">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="max-width:520px;">
<tr><td style="padding:0 2px 20px;">
  <a href="{SITE}" style="text-decoration:none;"><table role="presentation" cellpadding="0" cellspacing="0" border="0"><tr>
    <td style="vertical-align:middle;"><img src="{ICON}" width="32" height="32" alt="" style="display:block;border:0;outline:none;border-radius:7px;"></td>
    <td class="ink" style="vertical-align:middle;padding-left:10px;font-family:{SERIF};font-size:25px;font-weight:600;line-height:32px;color:{INK};">Dexio</td>
  </tr></table></a>
</td></tr>
<tr><td class="card" style="background:{CARD};border:1px solid {LINE};border-radius:10px;padding:32px;">
  <h1 class="ink" style="{p}font-size:20px;line-height:28px;font-weight:600;color:{INK};">{heading}</h1>
  <p class="ink" style="{p}padding-top:12px;font-size:15px;line-height:24px;color:{INK};">{body}</p>
  <table role="presentation" cellpadding="0" cellspacing="0" border="0" style="margin-top:24px;"><tr>
    <td bgcolor="{BTN}" style="border-radius:6px;background:{BTN};">
      <a href="{esc(link)}" style="display:inline-block;padding:12px 22px;font-family:{FONT};font-size:15px;font-weight:600;line-height:20px;color:#ffffff;text-decoration:none;border-radius:6px;">{esc(button)}</a>
    </td>
  </tr></table>
  <p class="muted" style="{p}padding-top:24px;font-size:13px;line-height:20px;color:{MUTED};">{note}</p>
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-top:24px;"><tr>
    <td class="rule" style="border-top:1px solid {LINE};padding-top:16px;">
      <p class="muted" style="{p}font-size:12px;line-height:18px;color:{MUTED};">If the button doesn't work, paste this link into your browser:<br>
      <a class="link" href="{esc(link)}" style="color:{LINK};word-break:break-all;">{esc(link)}</a></p>
    </td>
  </tr></table>
</td></tr>
<tr><td class="muted" style="padding:20px 2px 0;font-family:{FONT};font-size:12px;line-height:18px;color:{MUTED};">
  {footer}<br>
  <a class="link" href="{SITE}" style="color:{LINK};text-decoration:none;">Dexio</a>: one wiki for all your agents. Questions? Reply to this email.
</td></tr>
</table>
</td></tr>
</table>
</body>
</html>
"""


def password_reset(to: str, link: str) -> bool:
    subject = "Reset your Dexio password"
    text = f"""Someone asked to reset the password for {to} on Dexio.

To choose a new password, open this link within an hour:

{link}

It works once. If you did not ask for this, ignore this email; your password stays as it is.

Dexio
https://dexio.wiki
"""
    page = layout(
        subject=subject,
        preview="Choose a new password. The link works once, for the next hour.",
        heading="Reset your password",
        body=f"Someone asked to reset the password for <b>{esc(to)}</b> on Dexio. "
             "Choose a new one with the button below.",
        button="Choose a new password",
        link=link,
        note="The link works once and expires in an hour. If you didn't ask for this, "
             "you can ignore this email; your password stays as it is.",
        footer=f"Sent to {esc(to)} because a password reset was requested on Dexio.")
    return send(to, subject, text, page)


def read_only(to: str, workspace: str, plan: str, members: int, link: str) -> bool:
    """To each owner when a paid plan ends without anyone choosing Free (Stripe gave
    up on the card) and the workspace, now over Free's one person, goes read-only."""
    subject = f"{workspace} is read-only until you choose a plan or remove members"
    text = f"""The {plan} plan for the workspace "{workspace}" on Dexio has ended. This usually means Stripe could not take the payment.

The workspace is now on Free, which is for one person, and it has {members} members, so it is read-only. Everyone can still read it, but nobody, and no agent, can change it. Nothing has been deleted.

To start writing again, choose a plan, or remove everyone but yourself:

{link}

Dexio
https://dexio.wiki
"""
    page = layout(
        subject=subject,
        preview=f"The {plan} plan ended. {workspace} can be read, not changed, until you act.",
        heading=f"{esc(workspace)} is read-only",
        body=f"The <b>{esc(plan)}</b> plan for <b>{esc(workspace)}</b> has ended; this usually"
             f" means Stripe could not take the payment. The workspace is now on Free, which is"
             f" for one person, and it has {members} members, so everyone can read it but"
             f" nobody, and no agent, can change it. Nothing has been deleted.",
        button="Choose a plan",
        link=link,
        note="Or remove everyone but yourself under Settings, Members, and keep using it on Free.",
        footer=f"Sent to {esc(to)} because you are an owner of {esc(workspace)}.")
    return send(to, subject, text, page)


def invite(to: str, inviter: str, workspace: str, link: str,
           inviter_email: str = "") -> bool:
    """inviter is how the person who sent it is shown (their name, else their
    email); inviter_email, when it differs, is added so the recipient can tell
    who it really is."""
    subject = f"{inviter} invited you to {workspace} on Dexio"
    who = inviter
    if inviter_email and inviter_email.lower() != inviter.lower():
        who = f"{inviter} ({inviter_email})"
    text = f"""{who} invited you to join the workspace "{workspace}" on Dexio, where their agents keep a shared wiki.

Accept the invite:

{link}

The link works once and expires in 7 days. If you do not have a Dexio account yet, you can create one from the same link.

Dexio
https://dexio.wiki
"""
    named = f"<b>{esc(inviter)}</b>"
    if who != inviter:
        named += f" ({esc(inviter_email)})"
    page = layout(
        subject=subject,
        preview=f"Join {workspace}, where {inviter}'s agents keep a shared wiki.",
        heading=f"Join {esc(workspace)} on Dexio",
        body=f"{named} invited you to the <b>{esc(workspace)}</b> workspace on Dexio, "
             "where their agents keep a shared wiki.",
        button="Accept invite",
        link=link,
        note="The link works once and expires in 7 days. New to Dexio? You can create "
             "a free account from the same link.",
        footer=f"Sent to {esc(to)} because {esc(inviter)} invited this address. "
               "If you weren't expecting it, you can ignore it.")
    return send(to, subject, text, page)


def share(to: str, sharer: str, what: str, title: str, workspace: str, link: str,
          sharer_email: str = "") -> bool:
    """Something in a wiki was shared with this address to view (shares.py).
    what: "page", "folder" or "wiki"; title: the page's title, the folder's path
    or, for the wiki, the workspace's name."""
    thing = {"page": "a page", "folder": "a folder", "wiki": "the wiki"}.get(what, "a page")
    subject = f"{sharer} shared \"{title}\" with you on Dexio"
    who = sharer
    if sharer_email and sharer_email.lower() != sharer.lower():
        who = f"{sharer} ({sharer_email})"
    where = "" if what == "wiki" else f" from {workspace}"
    text = f"""{who} shared {thing}{where} with you on Dexio: "{title}". You can view it.

Open it:

{link}

The link is for you: it opens with the Dexio account for {to}, or one you sign in to with Google or GitHub as that address. If you do not have a Dexio account yet, you can create a free one with this address from the same link.

Dexio
https://dexio.wiki
"""
    named = f"<b>{esc(sharer)}</b>"
    if who != sharer:
        named += f" ({esc(sharer_email)})"
    page = layout(
        subject=subject,
        preview=f"{sharer} shared {thing}{where} with you. You can view it.",
        heading=esc(title),
        body=f"{named} shared {thing}" + (f" from <b>{esc(workspace)}</b>" if where else "")
             + " with you on Dexio. You can view it.",
        button="Open",
        link=link,
        note=f"The link is for you: it opens with the Dexio account for {esc(to)}, or one"
             " you sign in to with Google or GitHub as that address. New to Dexio? Create"
             " a free account with this address from the same link.",
        footer=f"Sent to {esc(to)} because {esc(sharer)} shared something with this address. "
               "If you weren't expecting it, you can ignore it.")
    return send(to, subject, text, page)


def _one_line(value: str, limit: int = 150) -> str:
    return " ".join(str(value or "").split())[:limit]


def contact(from_email: str, name: str, workspace: str, workspace_id: int, plan: str,
            members: int, about: str, message: str, link: str) -> bool:
    """A message from the contact form in Settings, Plan, to contact_to(), with
    Reply-To set to the person who wrote it so answering the email answers them.
    Plain text: it is read by us, not sent to anyone outside."""
    who = f"{_one_line(name)} <{from_email}>" if name else from_email
    subject = _one_line(f"Dexio {about.lower()}: {workspace}")
    people = f"{members} member{'s' if members != 1 else ''}"
    text = (f"About: {about}\nFrom: {who}\n"
            f"Workspace: {_one_line(workspace)} (id {workspace_id}), {plan}, {people}\n"
            f"Plan page: {link}\n\n{message}\n\n-- \nReply to this email to answer them.\n")
    return send(contact_to(), subject, text, reply_to_addr=from_email)
