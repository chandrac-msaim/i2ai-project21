"""Email tool: plain-text SMTP (Mailpit in development, inbox http://localhost:8025). Never raises."""
import smtplib
from email.message import EmailMessage

from pydantic import BaseModel

from app.config import get_settings


class EmailResult(BaseModel):
    sent: bool
    error: str | None = None


def send_email(to: str, subject: str, body: str, from_addr: str | None = None,
               timeout_s: float = 10.0, html: str | None = None, cc: str | None = None) -> EmailResult:
    settings = get_settings()
    message = EmailMessage()
    message["From"] = from_addr or settings.mail_from
    message["To"] = to
    message["Subject"] = subject
    if cc:
        message["Cc"] = cc
    message.set_content(body)
    if html:
        message.add_alternative(html, subtype="html")  # plain text stays as the fallback part
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=timeout_s) as smtp:
            smtp.send_message(message)
        return EmailResult(sent=True)
    except (OSError, smtplib.SMTPException) as exc:
        return EmailResult(sent=False, error=f"{type(exc).__name__}: {exc}"[:500])
