"""SendGrid wrapper for transactional email.

Reusable for reminders, verification, password reset, etc. M0 only hooks
the reminder use case.
"""
import logging
from dataclasses import dataclass
from typing import Optional

from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail, Email, To, Content
from python_http_client.exceptions import HTTPError as _SGHTTPError

from app.config import Config

logger = logging.getLogger(__name__)


class EmailSendError(Exception):
    """Raised when SendGrid returns non-2xx or the request itself fails.

    `retryable` distinguishes transient failures (network, 5xx) from
    permanent failures (4xx — bad email, blocked sender).
    """
    def __init__(self, message: str, *, retryable: bool, status_code: Optional[int] = None):
        super().__init__(message)
        self.retryable = retryable
        self.status_code = status_code


@dataclass
class EmailSendResult:
    status_code: int
    retryable: bool = False  # always False for success


def send_transactional_email(
    *,
    to_email: str,
    subject: str,
    body_text: str,
    body_html: Optional[str] = None,
) -> EmailSendResult:
    """Send a transactional email via SendGrid.

    Raises EmailSendError on failure (with retryable flag set).
    """
    if not Config.SENDGRID_API_KEY:
        raise EmailSendError("SendGrid API key not configured.", retryable=False)

    message = Mail(
        from_email=Email(Config.SENDGRID_FROM_EMAIL, Config.SENDGRID_FROM_NAME),
        to_emails=[To(to_email)],
        subject=subject,
    )
    message.add_content(Content("text/plain", body_text))
    if body_html:
        message.add_content(Content("text/html", body_html))

    try:
        client = SendGridAPIClient(Config.SENDGRID_API_KEY)
        response = client.send(message)
    except EmailSendError:
        raise
    except _SGHTTPError as exc:
        # SendGrid SDK raises HTTPError for any non-2xx instead of returning
        # a Response object — classify by status code, not exception class.
        # Caught live in M0 prod deploy: a 403 (unverified domain) was being
        # routed through the generic-Exception branch and marked retryable.
        status = getattr(exc, "status_code", 0)
        body = getattr(exc, "body", b"")
        retryable = 500 <= status < 600
        raise EmailSendError(
            f"SendGrid HTTP {status}: {body!r}",
            retryable=retryable,
            status_code=status,
        ) from exc
    except Exception as exc:
        # Network errors, DNS failures, timeouts — always retryable
        raise EmailSendError(f"SendGrid request failed: {exc}", retryable=True) from exc

    # Defensive: future SDK versions may return a Response object instead of raising
    status = getattr(response, "status_code", 0)
    if 200 <= status < 300:
        return EmailSendResult(status_code=status)
    if 500 <= status < 600:
        raise EmailSendError(
            f"SendGrid 5xx: {status} body={getattr(response, 'body', b'')!r}",
            retryable=True,
            status_code=status,
        )
    raise EmailSendError(
        f"SendGrid {status}: {getattr(response, 'body', b'')!r}",
        retryable=False,
        status_code=status,
    )
