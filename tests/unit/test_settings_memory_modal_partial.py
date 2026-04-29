"""Memory child-modal partial renders identical markup on both surfaces."""
from unittest.mock import patch


def test_legacy_page_renders_memory_modal_markup(authenticated_client, mock_firestore):
    with authenticated_client.session_transaction() as sess:
        sess['user'] = {'uid': 'u1', 'email': 'someone@example.com'}
    resp = authenticated_client.get('/account/manage')
    assert resp.status_code == 200
    assert b'id="memory-modal"' in resp.data
    assert b'id="clear-memory-confirm-modal"' in resp.data
    assert b'id="memory-toggle-master"' in resp.data
    assert b'id="memory-toggle-ref-v2"' in resp.data
    assert b'id="memory-toggle-ref-mempalace"' in resp.data


def test_index_renders_memory_modal_globally(authenticated_client, mock_firestore):
    with patch('app.blueprints.pages_routes.fetch_user_preferences', return_value={}, create=True), \
         patch('app.blueprints.pages_routes.task_generator.get_fallback_options', return_value=[]):
        with authenticated_client.session_transaction() as sess:
            sess['user'] = {'uid': 'u1', 'email': 'someone@example.com'}
        resp = authenticated_client.get('/')
    assert resp.status_code == 200
    assert b'id="memory-modal"' in resp.data
    assert b'id="clear-memory-confirm-modal"' in resp.data


def test_no_duplicate_modal_markup(authenticated_client, mock_firestore):
    """Each page should render exactly one #memory-modal."""
    with authenticated_client.session_transaction() as sess:
        sess['user'] = {'uid': 'u1', 'email': 'someone@example.com'}
    resp = authenticated_client.get('/account/manage')
    assert resp.data.count(b'id="memory-modal"') == 1
