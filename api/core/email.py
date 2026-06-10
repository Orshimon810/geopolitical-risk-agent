"""
Password-reset email sender.

Dev mode (SMTP_HOST unset): logs the reset link to stdout so you can test
without an email provider. Set SMTP_HOST + credentials to send real emails.
"""

import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from georisk_agent.app.config import settings

logger = logging.getLogger(__name__)


def send_password_reset_email(to_email: str, reset_link: str) -> None:
    if not settings.smtp_host:
        logger.warning(
            "SMTP not configured — password reset link for %s : %s",
            to_email,
            reset_link,
        )
        return

    msg = MIMEMultipart("alternative")
    msg["Subject"] = "Reset your GeoRisk AI password"
    msg["From"] = settings.smtp_from
    msg["To"] = to_email
    msg.attach(MIMEText(
        f"Reset your password (expires in 1 hour):\n\n{reset_link}\n\n"
        "If you did not request this, you can ignore this email.",
        "plain",
    ))
    msg.attach(MIMEText(
        f"<p>Click the link below to reset your GeoRisk AI password "
        f"(expires in <strong>1 hour</strong>):</p>"
        f'<p><a href="{reset_link}">{reset_link}</a></p>'
        f"<p>If you did not request this, ignore this email — your password will not change.</p>",
        "html",
    ))

    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port) as server:
            server.ehlo()
            server.starttls()
            if settings.smtp_user and settings.smtp_password:
                server.login(settings.smtp_user, settings.smtp_password)
            server.sendmail(settings.smtp_from, to_email, msg.as_string())
        logger.info("Password reset email sent to %s", to_email)
    except Exception:
        logger.exception("Failed to send password reset email to %s", to_email)
        raise
