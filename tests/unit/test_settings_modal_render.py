"""Settings modal markup renders correctly with all 8 tabs."""
from unittest.mock import patch

EXPECTED_TABS = [
    ('general', 'General'),
    ('notifications', 'Notifications'),
    ('personalization', 'Personalization'),
    ('speech', 'Speech'),
    ('data', 'Data Controls'),
    ('connected', 'Connected apps'),
    ('security', 'Security'),
]


def _render(client, email='regular@example.com'):
    with patch('app.blueprints.pages_routes.fetch_user_preferences', return_value={}, create=True), \
         patch('app.blueprints.pages_routes.task_generator.get_fallback_options', return_value=[]):
        with client.session_transaction() as sess:
            sess['user'] = {'uid': 'u1', 'email': email}
        return client.get('/')


def test_modal_backdrop_present(authenticated_client, mock_firestore):
    resp = _render(authenticated_client)
    assert resp.status_code == 200
    assert b'id="settings-modal"' in resp.data


def test_seven_user_visible_tabs(authenticated_client, mock_firestore):
    resp = _render(authenticated_client)
    for tab_id, label in EXPECTED_TABS:
        assert f'data-tab="{tab_id}"'.encode() in resp.data, f'missing tab {tab_id}'
        assert label.encode() in resp.data, f'missing label {label}'


def test_api_keys_tab_admin_only(authenticated_client, mock_firestore):
    resp_regular = _render(authenticated_client, email='regular@example.com')
    assert b'data-tab="api-keys"' not in resp_regular.data

    resp_admin = _render(authenticated_client, email='a.savu.stefan@gmail.com')
    assert b'data-tab="api-keys"' in resp_admin.data


def test_general_tab_marked_active_by_default(authenticated_client, mock_firestore):
    resp = _render(authenticated_client)
    assert b'data-tab="general"' in resp.data
    # The active rail item carries 'is-active' on the General button
    assert b'is-active' in resp.data


def test_close_button_present(authenticated_client, mock_firestore):
    resp = _render(authenticated_client)
    assert b'data-settings-close' in resp.data


def test_api_keys_form_rendered_inside_modal_for_admin(authenticated_client, mock_firestore):
    resp = _render(authenticated_client, email='a.savu.stefan@gmail.com')
    assert b'data-pane="api-keys"' in resp.data
    assert b'id="api-key-form"' in resp.data
    assert b'id="groq_api_key"' in resp.data
    assert b'id="openrouter_api_key"' in resp.data
    assert b'id="deepseek_api_key"' in resp.data


def test_api_keys_form_omitted_for_non_admin(authenticated_client, mock_firestore):
    resp = _render(authenticated_client, email='regular@example.com')
    assert b'data-pane="api-keys"' not in resp.data
    assert b'id="api-key-form"' not in resp.data
