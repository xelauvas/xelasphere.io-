from datetime import datetime, timedelta
from unittest.mock import MagicMock

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


class _Doc:
    def __init__(self, doc_id, updated):
        self.id = doc_id
        self._data = {
            'status': 'active',
            'session_name': doc_id,
            'last_updated': updated,
            'created_at': updated,
            'project_id': 'p1',
        }

    def to_dict(self):
        return self._data.copy()


def test_project_history_pagination_does_not_skip_lookahead_chat():
    now = datetime.utcnow()
    docs = [_Doc('c1', now), _Doc('c2', now - timedelta(seconds=1)),
            _Doc('c3', now - timedelta(seconds=2))]
    project_query = MagicMock()
    project_query.stream.side_effect = lambda: iter(docs)
    sessions_collection = MagicMock()
    sessions_collection.where.return_value = project_query
    user_document = MagicMock()
    user_document.collection.return_value = sessions_collection
    users_collection = MagicMock()
    users_collection.document.return_value = user_document
    db = MagicMock()
    db.collection.return_value = users_collection
    chat_session = ChatSession(db)

    first = chat_session.get_history('u1', page_size=2, project_id='p1')
    second = chat_session.get_history(
        'u1', page_size=2, page_token=first['next_page_token'], project_id='p1'
    )

    assert [chat['session_id'] for chat in first['chat_history']] == ['c1', 'c2']
    assert first['next_page_token'] == 'c2'
    assert [chat['session_id'] for chat in second['chat_history']] == ['c3']
