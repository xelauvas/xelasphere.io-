"""index.html renders is_admin flag based on session user.

Skipped until Task 1.3 lands the modal partial that consumes is_admin.
"""
import pytest
from unittest.mock import patch

pytestmark = pytest.mark.skip(reason='Unblocked by Task 1.3 (modal partial)')


def test_index_renders_is_admin_true_for_admin(authenticated_client, mock_firestore):
    with patch('app.blueprints.pages_routes.fetch_user_preferences', return_value={}), \
         patch('app.blueprints.pages_routes.task_generator.get_fallback_options', return_value=[]):
        with authenticated_client.session_transaction() as sess:
            sess['user'] = {'uid': 'admin1', 'email': 'a.savu.stefan@gmail.com'}
        resp = authenticated_client.get('/')
    assert resp.status_code == 200
    assert b'data-tab="api-keys"' in resp.data


def test_index_omits_api_keys_tab_for_non_admin(authenticated_client, mock_firestore):
    with patch('app.blueprints.pages_routes.fetch_user_preferences', return_value={}), \
         patch('app.blueprints.pages_routes.task_generator.get_fallback_options', return_value=[]):
        with authenticated_client.session_transaction() as sess:
            sess['user'] = {'uid': 'regular1', 'email': 'regular@example.com'}
        resp = authenticated_client.get('/')
    assert resp.status_code == 200
    assert b'data-tab="api-keys"' not in resp.data
