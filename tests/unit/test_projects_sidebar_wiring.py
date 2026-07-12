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
    assert "startNewChatModule.js') }}?v=32" in template
    assert "projectsModule.js') }}?v=13" in template
    assert "projectsPage.js') }}?v=7" in template
    assert "projects.css') }}?v=48" in template
    assert "sidebarContent.css') }}?v=54" in template
    assert "sidebar-chatgpt.css') }}?v=4" in template
    assert "sidebarHandler.js') }}?v=6" in template


def test_sidebar_uses_chatgpt_style_sections_and_incognito_action():
    template = _read('app/templates/index.html')
    assert '<div class="sidebar-section-header">Chats</div>' in template
    assert 'class="sidebar-header-ghost"' in template
    assert 'temporary: true' in template
    assert 'id="sidebar-toggle-button" class="sidebar-header-collapse"' in template
    assert 'id="action-chats" aria-label="Chats" aria-pressed="false"' in template
    assert "profileHeader.addEventListener('keydown'" in template


def test_sidebar_collapse_state_is_persisted():
    handler = _read('app/static/js/sidebarHandler.js')
    chat = _read('app/static/js/startNewChatModule.js')
    assert "localStorage.getItem('sidebar:collapsed')" in handler
    assert "localStorage.setItem('sidebar:collapsed'" in handler
    assert "sidebarToggleButton?.addEventListener('click'" in chat
    assert "sidebarToggleButton.addEventListener('click'" not in handler


def test_add_to_project_picker_is_accessible_and_viewport_aware():
    source = _read('app/static/js/startNewChatModule.js')
    assert "heading.textContent = 'Move to project'" in source
    assert "create.innerHTML = '<span aria-hidden=\"true\">＋</span><span>New project</span>'" in source
    assert "if (left + pickerRect.width > window.innerWidth - gap)" in source
    assert "event.key === 'ArrowDown'" in source
    assert "event.key === 'Escape'" in source


def test_new_project_modal_has_polished_header_and_global_escape():
    template = _read('app/templates/index.html')
    source = _read('app/static/js/projectsModule.js')
    assert 'class="project-modal-icon"' in template
    assert 'class="project-modal-subtitle"' in template
    assert 'class="project-modal-close"' in template
    assert "e.key === 'Escape' && !els.modal.hidden" in source
