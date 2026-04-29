"""Personalization pane structure — 8 sections, dropdowns, toggles, inputs, collapsible."""
from unittest.mock import patch


def _render(client, email='regular@example.com'):
    with patch('app.blueprints.pages_routes.fetch_user_preferences', return_value={}, create=True), \
         patch('app.blueprints.pages_routes.task_generator.get_fallback_options', return_value=[]):
        with client.session_transaction() as sess:
            sess['user'] = {'uid': 'u1', 'email': email}
        return client.get('/')


def test_personalization_pane_present(authenticated_client, mock_firestore):
    resp = _render(authenticated_client)
    assert resp.status_code == 200
    assert b'data-pane="personalization"' in resp.data


def test_base_style_dropdown(authenticated_client, mock_firestore):
    resp = _render(authenticated_client)
    assert b'data-dropdown="p-base-style"' in resp.data
    # All 5 base-style options
    for opt in (b'concise', b'friendly', b'professional', b'playful', b'default'):
        assert b'data-dropdown-option="' + opt + b'"' in resp.data


def test_characteristics_dropdowns(authenticated_client, mock_firestore):
    resp = _render(authenticated_client)
    for key in (b'p-warm', b'p-enthusiastic', b'p-headers-lists', b'p-emoji'):
        assert b'data-dropdown="' + key + b'"' in resp.data
    # Less / More options for characteristics
    assert b'data-dropdown-option="less"' in resp.data
    assert b'data-dropdown-option="more"' in resp.data


def test_fast_answers_toggle(authenticated_client, mock_firestore):
    resp = _render(authenticated_client)
    assert b'data-personalization-toggle="p-fast-answers"' in resp.data


def test_custom_instructions_input(authenticated_client, mock_firestore):
    resp = _render(authenticated_client)
    assert b'data-personalization-input="p-custom-instructions"' in resp.data


def test_about_you_inputs(authenticated_client, mock_firestore):
    resp = _render(authenticated_client)
    for key in (b'p-nickname', b'p-occupation', b'p-more-about-you'):
        assert b'data-personalization-input="' + key + b'"' in resp.data


def test_memory_section_keeps_master_and_two_refs(authenticated_client, mock_firestore):
    """Master is kept (decision 1a) — privacy kill-switch must remain reachable."""
    resp = _render(authenticated_client)
    for key in (b'master', b'ref_v2', b'ref_mempalace'):
        assert b'data-memory-toggle="' + key + b'"' in resp.data


def test_memory_section_manage_button(authenticated_client, mock_firestore):
    resp = _render(authenticated_client)
    assert b'data-action="open-memory-modal"' in resp.data


def test_record_mode_toggle(authenticated_client, mock_firestore):
    resp = _render(authenticated_client)
    assert b'data-personalization-toggle="p-record-history"' in resp.data


def test_advanced_collapsible(authenticated_client, mock_firestore):
    resp = _render(authenticated_client)
    assert b'id="personalization-advanced"' in resp.data
    assert b'data-collapsible-trigger' in resp.data
    assert b'aria-expanded="false"' in resp.data  # default closed


def test_advanced_panel_toggles(authenticated_client, mock_firestore):
    resp = _render(authenticated_client)
    for key in (b'p-web-search', b'p-canvas', b'p-voice', b'p-advanced-voice', b'p-connector-search'):
        assert b'data-personalization-toggle="' + key + b'"' in resp.data


def test_uses_button_role_switch_pattern_only(authenticated_client, mock_firestore):
    """All toggles must use the button[role=switch] pattern (PR #151 / #157)."""
    resp = _render(authenticated_client)
    # No legacy <label class="settings-toggle"> with input checkbox in pane
    assert b'<label class="settings-toggle">' not in resp.data
    assert b'<input type="checkbox" data-personalization-toggle' not in resp.data
    assert b'<input type="checkbox" data-memory-toggle' not in resp.data
