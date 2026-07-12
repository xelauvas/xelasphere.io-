"""Regression checks for the Projects/Recents browser wiring."""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _read(relative_path):
    return (ROOT / relative_path).read_text(encoding='utf-8')


def test_active_chat_project_action_uses_chat_manager_source_of_truth():
    source = _read('app/static/js/projectsModule.js')
    assert 'window.chatManager?.activeSessionId' in source
    assert 'window.chatManager?.currentSessionId || window.chatManager?.sessionId' not in source


def test_recents_is_always_requested_as_ungrouped():
    source = _read('app/static/js/startNewChatModule.js')
    assert "params.set('ungrouped', '1')" in source
    assert "params.set('project_id', projectMatch[1])" not in source


def test_chat_rows_supply_the_project_drop_payload():
    source = _read('app/static/js/startNewChatModule.js')
    assert 'li.draggable = true' in source
    assert "setData('application/x-session-id', session.session_id)" in source


def test_project_workspace_consumes_all_history_pages():
    source = _read('app/static/js/projectsPage.js')
    assert "pageToken = body.next_page_token || null" in source
    assert '} while (pageToken);' in source


def test_sidebar_assets_have_fresh_cache_versions():
    template = _read('app/templates/index.html')
    assert "startNewChatModule.js') }}?v=30" in template
    assert "projectsModule.js') }}?v=12" in template
    assert "projectsPage.js') }}?v=7" in template
    assert "projects.css') }}?v=46" in template
    assert "sidebar-chatgpt.css') }}?v=1" in template
    assert "sidebarHandler.js') }}?v=4" in template


def test_sidebar_uses_chatgpt_style_sections_and_real_toggle():
    template = _read('app/templates/index.html')
    handler = _read('app/static/js/sidebarHandler.js')
    assert '<div class="sidebar-section-header">Chats</div>' in template
    assert 'id="sidebar-toggle-button"' in template
    assert "sidebarToggleButton.addEventListener('click'" in handler
