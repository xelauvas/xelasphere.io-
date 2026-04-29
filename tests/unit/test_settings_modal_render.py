"""Settings modal markup renders correctly with all 9 tabs (+ admin API Keys)."""
from unittest.mock import patch

EXPECTED_TABS = [
    ('general', 'General'),
    ('notifications', 'Notifications'),
    ('personalization', 'Personalization'),
    ('apps', 'Apps'),
    ('schedules', 'Schedules'),
    ('data', 'Data controls'),
    ('security', 'Security'),
    ('parental', 'Parental controls'),
    ('account', 'Account'),
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


def test_nine_user_visible_tabs(authenticated_client, mock_firestore):
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
    assert b'is-active' in resp.data


def test_close_button_present(authenticated_client, mock_firestore):
    resp = _render(authenticated_client)
    assert b'data-settings-close' in resp.data


def test_api_keys_form_rendered_inside_modal_for_admin(authenticated_client, mock_firestore):
    resp = _render(authenticated_client, email='a.savu.stefan@gmail.com')
    assert b'data-pane="api-keys"' in resp.data
    assert b'id="api-key-form"' in resp.data
    assert b'id="groq_api_key"' in resp.data


def test_api_keys_form_omitted_for_non_admin(authenticated_client, mock_firestore):
    resp = _render(authenticated_client, email='regular@example.com')
    assert b'data-pane="api-keys"' not in resp.data
    assert b'id="api-key-form"' not in resp.data


def test_mfa_promo_card_present_in_general(authenticated_client, mock_firestore):
    resp = _render(authenticated_client)
    assert b'id="settings-mfa-card"' in resp.data
    assert b'Secure your account' in resp.data
    assert b'data-action="setup-mfa"' in resp.data
    assert b'data-action="dismiss-mfa"' in resp.data


def test_general_pane_has_appearance_dropdown(authenticated_client, mock_firestore):
    resp = _render(authenticated_client)
    assert b'data-dropdown="appearance"' in resp.data
    # All three options present
    assert b'data-dropdown-option="system"' in resp.data
    assert b'data-dropdown-option="dark"' in resp.data
    assert b'data-dropdown-option="light"' in resp.data


def test_general_pane_has_voice_dropdown_with_play_button(authenticated_client, mock_firestore):
    resp = _render(authenticated_client)
    assert b'data-dropdown="voice"' in resp.data
    assert b'data-action="play-voice-preview"' in resp.data
    # Voice options
    for voice in [b'ember', b'maple', b'sol', b'vale', b'arbor']:
        assert b'data-dropdown-option="' + voice + b'"' in resp.data


def test_general_pane_has_separate_voice_toggle(authenticated_client, mock_firestore):
    resp = _render(authenticated_client)
    assert b'data-pref-toggle="separate_voice"' in resp.data


def test_general_pane_has_delete_all_and_logout(authenticated_client, mock_firestore):
    resp = _render(authenticated_client)
    assert b'data-action="delete-all-chats"' in resp.data
    assert b'data-action="logout"' in resp.data
