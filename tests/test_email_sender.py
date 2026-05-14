import pytest
from unittest.mock import patch, MagicMock
from app.services.email_sender import (
    send_transactional_email, EmailSendError, EmailSendResult,
)


class TestSendTransactional:
    @patch("app.services.email_sender.SendGridAPIClient")
    def test_success_returns_result(self, mock_client_cls):
        mock_client = MagicMock()
        mock_response = MagicMock(status_code=202, body=b"", headers={})
        mock_client.send.return_value = mock_response
        mock_client_cls.return_value = mock_client

        with patch("app.services.email_sender.Config") as cfg:
            cfg.SENDGRID_API_KEY = "SG.test"
            cfg.SENDGRID_FROM_EMAIL = "noreply@xelas.io"
            cfg.SENDGRID_FROM_NAME = "Test"
            result = send_transactional_email(
                to_email="user@example.com",
                subject="hi",
                body_text="body",
            )
        assert isinstance(result, EmailSendResult)
        assert result.status_code == 202
        assert result.retryable is False

    @patch("app.services.email_sender.SendGridAPIClient")
    def test_5xx_raises_retryable_error(self, mock_client_cls):
        mock_client = MagicMock()
        mock_response = MagicMock(status_code=503, body=b"upstream down", headers={})
        mock_client.send.return_value = mock_response
        mock_client_cls.return_value = mock_client

        with patch("app.services.email_sender.Config") as cfg:
            cfg.SENDGRID_API_KEY = "SG.test"
            cfg.SENDGRID_FROM_EMAIL = "noreply@xelas.io"
            cfg.SENDGRID_FROM_NAME = "Test"
            with pytest.raises(EmailSendError) as exc:
                send_transactional_email(to_email="u@e.com", subject="s", body_text="b")
        assert exc.value.retryable is True
        assert exc.value.status_code == 503

    @patch("app.services.email_sender.SendGridAPIClient")
    def test_4xx_raises_non_retryable_error(self, mock_client_cls):
        mock_client = MagicMock()
        mock_response = MagicMock(status_code=403, body=b"forbidden", headers={})
        mock_client.send.return_value = mock_response
        mock_client_cls.return_value = mock_client

        with patch("app.services.email_sender.Config") as cfg:
            cfg.SENDGRID_API_KEY = "SG.test"
            cfg.SENDGRID_FROM_EMAIL = "noreply@xelas.io"
            cfg.SENDGRID_FROM_NAME = "Test"
            with pytest.raises(EmailSendError) as exc:
                send_transactional_email(to_email="u@e.com", subject="s", body_text="b")
        assert exc.value.retryable is False
        assert exc.value.status_code == 403

    @patch("app.services.email_sender.SendGridAPIClient")
    def test_network_error_is_retryable(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client.send.side_effect = ConnectionError("dns failed")
        mock_client_cls.return_value = mock_client

        with patch("app.services.email_sender.Config") as cfg:
            cfg.SENDGRID_API_KEY = "SG.test"
            cfg.SENDGRID_FROM_EMAIL = "noreply@xelas.io"
            cfg.SENDGRID_FROM_NAME = "Test"
            with pytest.raises(EmailSendError) as exc:
                send_transactional_email(to_email="u@e.com", subject="s", body_text="b")
        assert exc.value.retryable is True

    @patch("app.services.email_sender.SendGridAPIClient")
    def test_sdk_httperror_4xx_is_non_retryable(self, mock_client_cls):
        """Regression test: SendGrid SDK raises HTTPError instead of returning
        a Response on non-2xx. Caught in M0 prod deploy — a 403 was being
        routed through the generic-Exception branch and marked retryable."""
        from python_http_client.exceptions import HTTPError as SGHTTPError
        mock_client = MagicMock()
        # Simulate the 4-arg HTTPError constructor: (status, reason, body, headers)
        mock_client.send.side_effect = SGHTTPError(403, "Forbidden", b"unverified", {})
        mock_client_cls.return_value = mock_client

        with patch("app.services.email_sender.Config") as cfg:
            cfg.SENDGRID_API_KEY = "SG.t"
            cfg.SENDGRID_FROM_EMAIL = "n@e.io"
            cfg.SENDGRID_FROM_NAME = "T"
            with pytest.raises(EmailSendError) as exc:
                send_transactional_email(to_email="u@e.com", subject="s", body_text="b")
        assert exc.value.status_code == 403
        assert exc.value.retryable is False

    @patch("app.services.email_sender.SendGridAPIClient")
    def test_sdk_httperror_5xx_is_retryable(self, mock_client_cls):
        from python_http_client.exceptions import HTTPError as SGHTTPError
        mock_client = MagicMock()
        mock_client.send.side_effect = SGHTTPError(503, "Service Unavailable", b"down", {})
        mock_client_cls.return_value = mock_client

        with patch("app.services.email_sender.Config") as cfg:
            cfg.SENDGRID_API_KEY = "SG.t"
            cfg.SENDGRID_FROM_EMAIL = "n@e.io"
            cfg.SENDGRID_FROM_NAME = "T"
            with pytest.raises(EmailSendError) as exc:
                send_transactional_email(to_email="u@e.com", subject="s", body_text="b")
        assert exc.value.status_code == 503
        assert exc.value.retryable is True

    def test_missing_api_key_raises_non_retryable(self):
        with patch("app.services.email_sender.Config") as cfg:
            cfg.SENDGRID_API_KEY = ""
            with pytest.raises(EmailSendError) as exc:
                send_transactional_email(to_email="u@e.com", subject="s", body_text="b")
        assert exc.value.retryable is False
        assert "api key" in str(exc.value).lower()

    @patch("app.services.email_sender.SendGridAPIClient")
    def test_html_body_added_when_provided(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client.send.return_value = MagicMock(status_code=202, body=b"", headers={})
        mock_client_cls.return_value = mock_client

        with patch("app.services.email_sender.Config") as cfg:
            cfg.SENDGRID_API_KEY = "SG.test"
            cfg.SENDGRID_FROM_EMAIL = "noreply@xelas.io"
            cfg.SENDGRID_FROM_NAME = "Test"
            send_transactional_email(
                to_email="u@e.com", subject="s",
                body_text="plain text", body_html="<p>html</p>",
            )
        # Verify .send() was called once with a Mail object that has both content types
        args, _ = mock_client.send.call_args
        mail = args[0]
        # Inspect the Mail object's `contents` list — Mail.contents is a list of Content
        contents = [c.get() if hasattr(c, "get") else c for c in mail.contents]
        # Each Content has a 'type' field; we just check both mime types are present
        types_present = [c["type"] for c in contents if isinstance(c, dict)]
        assert "text/plain" in types_present
        assert "text/html" in types_present
