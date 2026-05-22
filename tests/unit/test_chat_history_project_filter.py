"""GET /chat/history must accept project_id and ungrouped filters."""
from unittest.mock import patch


def _fake_history(chats=None, next_token=None):
    return {'chat_history': chats or [], 'next_page_token': next_token}


def test_history_default_passes_no_filters(authenticated_client, mock_firestore):
    with patch('app.blueprints.shared.chat_session.get_history',
               return_value=_fake_history()) as m:
        resp = authenticated_client.get('/chat/history')
    assert resp.status_code == 200
    args, kwargs = m.call_args
    # call signature: get_history(user_id, page_size, page_token, project_id=, ungrouped=)
    assert kwargs.get('project_id') in (None,)
    assert kwargs.get('ungrouped') in (False,)


def test_history_ungrouped_query_param_forwarded(authenticated_client, mock_firestore):
    with patch('app.blueprints.shared.chat_session.get_history',
               return_value=_fake_history()) as m, \
         patch('app.blueprints.shared.chat_session.count_grouped', return_value=3):
        resp = authenticated_client.get('/chat/history?ungrouped=1')
    assert resp.status_code == 200
    _, kwargs = m.call_args
    assert kwargs.get('ungrouped') is True
    body = resp.get_json()
    assert body.get('grouped_count') == 3


def test_history_project_id_forwarded(authenticated_client, mock_firestore):
    with patch('app.blueprints.shared.chat_session.get_history',
               return_value=_fake_history()) as m:
        resp = authenticated_client.get('/chat/history?project_id=p-abc')
    assert resp.status_code == 200
    _, kwargs = m.call_args
    assert kwargs.get('project_id') == 'p-abc'


def test_grouped_count_omitted_when_filtering_by_project(authenticated_client, mock_firestore):
    """When project_id is set, the +N hint is not relevant — no grouped_count."""
    with patch('app.blueprints.shared.chat_session.get_history',
               return_value=_fake_history()):
        resp = authenticated_client.get('/chat/history?project_id=p-abc&ungrouped=1')
    body = resp.get_json()
    assert 'grouped_count' not in body
