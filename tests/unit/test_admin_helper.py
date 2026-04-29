"""Admin gating: only the configured admin email returns True."""
import os
from unittest.mock import patch

from app.blueprints.shared import is_admin_user


def test_admin_user_for_configured_email():
    user = {'uid': 'abc', 'email': 'a.savu.stefan@gmail.com'}
    assert is_admin_user(user) is True


def test_non_admin_for_other_email():
    user = {'uid': 'xyz', 'email': 'someone.else@example.com'}
    assert is_admin_user(user) is False


def test_returns_false_when_user_is_none():
    assert is_admin_user(None) is False


def test_returns_false_when_email_missing():
    user = {'uid': 'abc'}
    assert is_admin_user(user) is False


def test_admin_emails_env_override():
    user = {'uid': 'abc', 'email': 'override@test.com'}
    with patch.dict(os.environ, {'ADMIN_EMAILS': 'override@test.com'}):
        assert is_admin_user(user) is True


def test_admin_emails_env_csv_multiple():
    """ADMIN_EMAILS as comma-separated list — any matching email is admin."""
    with patch.dict(os.environ, {'ADMIN_EMAILS': 'one@test.com, two@test.com'}):
        assert is_admin_user({'uid': 'a', 'email': 'two@test.com'}) is True
        assert is_admin_user({'uid': 'b', 'email': 'three@test.com'}) is False


def test_returns_false_for_non_dict_user():
    assert is_admin_user("not-a-dict") is False
    assert is_admin_user(["list"]) is False
