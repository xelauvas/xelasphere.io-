"""Verifies _apply_project_context injects Project instructions + files into base_system."""
from unittest.mock import patch, MagicMock

from app.blueprints.chat_routes import _apply_project_context


def test_no_project_id_returns_unchanged():
    base = "ORIGINAL"
    out = _apply_project_context('u1', {'project_id': None}, base)
    assert out == base


def test_project_id_but_project_missing_returns_unchanged():
    base = "ORIGINAL"
    with patch('app.project_service.ProjectService.get', return_value=None):
        out = _apply_project_context('u1', {'project_id': 'p1'}, base)
    assert out == base


def test_with_instructions_only_appends_block(monkeypatch):
    project = {
        'id': 'p1', 'instructions': 'Be warm and Welsh.',
        'files': [],
    }
    with patch('app.project_service.ProjectService.get', return_value=project), \
         patch('app.project_service.ProjectService.list_files', return_value=[]):
        out = _apply_project_context('u1', {'project_id': 'p1'}, 'ORIGINAL')
    assert 'ORIGINAL' in out
    assert '--- Project instructions ---' in out
    assert 'Be warm and Welsh.' in out


def test_with_files_includes_extracted_text():
    project = {
        'id': 'p1', 'instructions': '',
        'files': [{'file_id': 'f1', 'filename': 'note.txt'}],
    }
    file_texts = [{'file_id': 'f1', 'extracted_text': 'secret pizza recipe'}]
    with patch('app.project_service.ProjectService.get', return_value=project), \
         patch('app.project_service.ProjectService.list_files', return_value=file_texts):
        out = _apply_project_context('u1', {'project_id': 'p1'}, 'ORIGINAL')
    assert '--- Project knowledge ---' in out
    assert 'note.txt' in out
    assert 'secret pizza recipe' in out


def test_exception_in_project_lookup_returns_unchanged():
    """If anything goes wrong, project context is degraded gracefully."""
    with patch('app.project_service.ProjectService.get', side_effect=RuntimeError('boom')):
        out = _apply_project_context('u1', {'project_id': 'p1'}, 'ORIGINAL')
    assert out == 'ORIGINAL'
