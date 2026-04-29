"""Personalization pane: 3 memory toggles + Manage memory button."""
from unittest.mock import patch


def _render(client):
    with patch('app.blueprints.pages_routes.fetch_user_preferences', return_value={}, create=True), \
         patch('app.blueprints.pages_routes.task_generator.get_fallback_options', return_value=[]):
        with client.session_transaction() as sess:
            sess['user'] = {'uid': 'u1', 'email': 'someone@example.com'}
        return client.get('/')


def test_personalization_has_three_memory_toggles(authenticated_client, mock_firestore):
    resp = _render(authenticated_client)
    assert b'data-memory-toggle="master"' in resp.data
    assert b'data-memory-toggle="ref_v2"' in resp.data
    assert b'data-memory-toggle="ref_mempalace"' in resp.data


def test_personalization_has_manage_memory_button(authenticated_client, mock_firestore):
    resp = _render(authenticated_client)
    assert b'data-action="open-memory-modal"' in resp.data


def test_personalization_pane_in_modal(authenticated_client, mock_firestore):
    resp = _render(authenticated_client)
    assert b'data-pane="personalization"' in resp.data
