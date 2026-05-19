"""Unit tests for app.services.signed_links.

Tokens are signed with Config.SECRET_KEY + salt 'reminder-snooze' and have
a 24-hour max-age. Verify round-trip, expiry, tampering, ownership."""
import pytest
import time
from app.services.signed_links import (
    make_snooze_token,
    verify_snooze_token,
    SnoozeTokenExpired,
    SnoozeTokenInvalid,
)


class TestRoundTrip:
    def test_make_and_verify(self):
        token = make_snooze_token("rid_abc", "uid_xyz")
        payload = verify_snooze_token(token)
        assert payload == {"rid": "rid_abc", "uid": "uid_xyz"}

    def test_tokens_for_different_reminders_are_different(self):
        t1 = make_snooze_token("rid_1", "uid_a")
        t2 = make_snooze_token("rid_2", "uid_a")
        assert t1 != t2


class TestRejection:
    def test_tampered_token_raises_invalid(self):
        token = make_snooze_token("rid_abc", "uid_xyz")
        tampered = token[:-4] + "AAAA"  # flip last 4 chars
        with pytest.raises(SnoozeTokenInvalid):
            verify_snooze_token(tampered)

    def test_garbage_token_raises_invalid(self):
        with pytest.raises(SnoozeTokenInvalid):
            verify_snooze_token("not-a-token")

    def test_empty_token_raises_invalid(self):
        with pytest.raises(SnoozeTokenInvalid):
            verify_snooze_token("")

    def test_expired_token_raises_expired(self, monkeypatch):
        from app.services import signed_links
        monkeypatch.setattr(signed_links, "_MAX_AGE_SECONDS", 0)
        token = make_snooze_token("rid_abc", "uid_xyz")
        time.sleep(1.1)  # ensure age > 0
        with pytest.raises(SnoozeTokenExpired):
            verify_snooze_token(token)
