from app.chat_session import ChatSession


def test_normal_chat_stays_in_recents():
    assert ChatSession._is_visible_ungrouped({'status': 'active'}, {'p1'})
    assert ChatSession._is_visible_ungrouped({'status': 'active', 'project_id': None}, {'p1'})


def test_only_chat_in_existing_project_is_hidden():
    assert not ChatSession._is_visible_ungrouped(
        {'status': 'active', 'project_id': 'p1'}, {'p1'}
    )


def test_stale_project_reference_stays_in_recents():
    assert ChatSession._is_visible_ungrouped(
        {'status': 'active', 'project_id': 'deleted-project'}, {'p1'}
    )


def test_archived_and_temporary_chats_stay_hidden():
    assert not ChatSession._is_visible_ungrouped({'status': 'archived'}, set())
    assert not ChatSession._is_visible_ungrouped({'status': 'temporary'}, set())
    assert not ChatSession._is_visible_ungrouped({'status': 'active', 'is_temporary': True}, set())
