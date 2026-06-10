"""
Password-reset email sender.

Uses Resend (https://resend.com) HTTP API — works on Railway and all cloud hosts
that block outbound SMTP. No extra dependencies: uses stdlib urllib.

Dev mode (RESEND_API_KEY unset): logs the reset link to stdout so you can test
without an email provider.
"""

import json
import logging
import urllib.request
import urllib.error

from georisk_agent.app.config import settings

logger = logging.getLogger(__name__)

_RESEND_URL = "https://api.resend.com/emails"


def send_password_reset_email(to_email: str, reset_link: str) -> None:
    if not settings.resend_api_key:
        logger.warning(
            "RESEND_API_KEY not configured — password reset link for %s : %s",
            to_email,
            reset_link,
        )
        return

    payload = json.dumps({
        "from": settings.smtp_from,
        "to": [to_email],
        "subject": "Reset your GeoRisk AI password",
        "text": (
            f"Reset your password (expires in 1 hour):\n\n{reset_link}\n\n"
            "If you did not request this, you can ignore this email."
        ),
        "html": (
            f"<p>Click the link below to reset your GeoRisk AI password "
            f"(expires in <strong>1 hour</strong>):</p>"
            f'<p><a href="{reset_link}">{reset_link}</a></p>'
            f"<p>If you did not request this, ignore this email — your password will not change.</p>"
        ),
    }).encode()

    req = urllib.request.Request(
        _RESEND_URL,
        data=payload,
        headers={
            "Authorization": f"Bearer {settings.resend_api_key}",
            "Content-Type": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            logger.info(
                "Password reset email sent to %s (status %s)", to_email, response.status
            )
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")
        logger.exception(
            "Resend API error %s for %s: %s", exc.code, to_email, body
        )
        raise
    except Exception:
        logger.exception("Failed to send password reset email to %s", to_email)
        raise
