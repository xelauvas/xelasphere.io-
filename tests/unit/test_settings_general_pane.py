"""General pane: theme radio, delete-all, log-out wired."""
from unittest.mock import patch


def _render(client):
    with patch('app.blueprints.pages_routes.fetch_user_preferences', return_value={}, create=True), \
         patch('app.blueprints.pages_routes.task_generator.get_fallback_options', return_value=[]):
        with client.session_transaction() as sess:
            sess['user'] = {'uid': 'u1', 'email': 'someone@example.com'}
        return client.get('/')


def test_general_pane_has_three_theme_radio_buttons(authenticated_client, mock_firestore):
    resp = _render(authenticated_client)
    assert b'data-theme-option="system"' in resp.data
    assert b'data-theme-option="dark"' in resp.data
    assert b'data-theme-option="light"' in resp.data


def test_general_pane_has_delete_all_chats_button(authenticated_client, mock_firestore):
    resp = _render(authenticated_client)
    assert b'data-action="delete-all-chats"' in resp.data


def test_general_pane_has_logout_button(authenticated_client, mock_firestore):
    resp = _render(authenticated_client)
    assert b'data-action="logout"' in resp.data


def test_general_pane_archive_buttons_disabled(authenticated_client, mock_firestore):
    """Archived chats / Archive all buttons exist as Coming Soon stubs."""
    resp = _render(authenticated_client)
    # Two disabled "Coming Soon" buttons in the General pane
    assert resp.data.count(b'title="Coming Soon"') >= 2
