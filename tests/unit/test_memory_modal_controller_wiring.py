"""memoryModalController.js is loaded on both surfaces that include the modal partial.

This guards against the bug fixed by extracting the inline memory wiring out of
settings_api_keys.html into a shared controller: the global settings modal flow
(avatar -> Settings -> Personalization -> Manage Memory) had the modal markup but
none of the load/Forget Me/tab wiring, so the spinner spun forever and Forget Me
did nothing.
"""
from pathlib import Path

from unittest.mock import patch


CONTROLLER_PATH = Path('app/static/js/memoryModalController.js')
LEGACY_TEMPLATE = Path('app/templates/settings_api_keys.html')
SETTINGS_MODAL_JS = Path('app/static/js/settingsModal.js')


def test_controller_file_exists():
    assert CONTROLLER_PATH.exists(), 'memoryModalController.js must exist'


def test_controller_exposes_public_api():
    src = CONTROLLER_PATH.read_text()
    assert 'window.MemoryModalController' in src
    for method in ('init', 'open', 'close', 'reloadMemories'):
        assert method in src, f'controller missing public method "{method}"'


def test_legacy_page_loads_controller_script(authenticated_client, mock_firestore):
    with authenticated_client.session_transaction() as sess:
        sess['user'] = {'uid': 'u1', 'email': 'someone@example.com'}
    resp = authenticated_client.get('/account/manage')
    assert resp.status_code == 200
    assert b'memoryModalController.js' in resp.data


def test_index_loads_controller_script(authenticated_client, mock_firestore):
    with patch('app.blueprints.pages_routes.fetch_user_preferences', return_value={}, create=True), \
         patch('app.blueprints.pages_routes.task_generator.get_fallback_options', return_value=[]):
        with authenticated_client.session_transaction() as sess:
            sess['user'] = {'uid': 'u1', 'email': 'someone@example.com'}
        resp = authenticated_client.get('/')
    assert resp.status_code == 200
    assert b'memoryModalController.js' in resp.data


def test_legacy_template_does_not_redefine_memory_handlers():
    """The inline JS that used to wire loadMemories/Forget Me/tabs is gone.
    If it comes back, the global settings modal flow will break again."""
    src = LEGACY_TEMPLATE.read_text()
    # These were the unique inline-only function names. The controller has the
    # same logic, so they should not appear in the template anymore.
    forbidden = [
        'function loadMemories',
        'function clearAllMemories',
        'function loadDreamActivity',
        'function fetchConversationMemory',
        'function bindMemorySwitch',
    ]
    for needle in forbidden:
        assert needle not in src, (
            f'legacy template still defines `{needle}` inline; '
            'memory wiring must live in memoryModalController.js'
        )


def test_settings_modal_js_no_longer_owns_memory_modal_close():
    """settingsModal.js used to half-wire the memory modal (open + close only).
    The controller now owns it; settingsModal.js should not duplicate that logic."""
    src = SETTINGS_MODAL_JS.read_text()
    # The old half-wire: setting aria-hidden on #memory-modal directly. That's
    # exactly what made the spinner spin forever and Forget Me do nothing — open
    # without load. If this comes back, the bug comes back.
    assert "getElementById('memory-modal')" not in src, (
        'settingsModal.js is wiring #memory-modal directly again; '
        'delegate to window.MemoryModalController instead'
    )
