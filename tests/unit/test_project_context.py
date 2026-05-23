"""Pure helper that assembles the Project context block for system prompt injection."""
import pytest
from app.project_context import build_project_context_block, INJECTION_BUDGET_CHARS


def test_empty_project_returns_empty_string():
    assert build_project_context_block(instructions='', files=[]) == ''


def test_instructions_only():
    block = build_project_context_block(instructions='Be warm.', files=[])
    assert '--- Project instructions ---' in block
    assert 'Be warm.' in block
    assert '--- Project knowledge ---' not in block


def test_one_file_under_budget():
    files = [{'filename': 'a.txt', 'extracted_text': 'hello'}]
    block = build_project_context_block(instructions='', files=files)
    assert '--- Project knowledge ---' in block
    assert 'a.txt' in block
    assert 'hello' in block


def test_files_with_instructions_have_correct_order():
    block = build_project_context_block(
        instructions='Be terse.',
        files=[{'filename': 'a.txt', 'extracted_text': 'hello'}],
    )
    assert block.index('--- Project instructions ---') < block.index('--- Project knowledge ---')


def test_budget_truncates_when_first_file_fills_budget():
    big = 'x' * INJECTION_BUDGET_CHARS  # one file fills the whole budget
    files = [
        {'filename': 'a.txt', 'extracted_text': big},
        {'filename': 'b.txt', 'extracted_text': 'world'},
    ]
    block = build_project_context_block(instructions='', files=files)
    # a.txt is included (at least partially); b.txt is dropped.
    assert 'a.txt' in block
    assert 'Some project files were truncated' in block


def test_budget_with_partial_overflow():
    """Second file exceeds remaining budget — its overflow is truncated."""
    chunk = 'x' * (INJECTION_BUDGET_CHARS // 2)
    files = [
        {'filename': 'a.txt', 'extracted_text': chunk},
        {'filename': 'b.txt', 'extracted_text': chunk + 'overflow'},
    ]
    block = build_project_context_block(instructions='', files=files)
    assert 'a.txt' in block
    assert 'truncated' in block.lower()


def test_empty_text_files_are_skipped():
    files = [
        {'filename': 'empty.txt', 'extracted_text': ''},
        {'filename': 'real.txt', 'extracted_text': 'real content'},
    ]
    block = build_project_context_block(instructions='', files=files)
    assert 'real.txt' in block
    assert 'empty.txt' not in block
