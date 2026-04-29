"""API Keys form partial renders identical markup to legacy inline form."""


def test_legacy_page_still_renders_form_inputs(authenticated_client, mock_firestore):
    with authenticated_client.session_transaction() as sess:
        sess['user'] = {'uid': 'admin1', 'email': 'a.savu.stefan@gmail.com'}
    resp = authenticated_client.get('/account/manage')
    assert resp.status_code == 200
    # Form action and provider input names survive the partial extraction
    assert b'id="api-key-form"' in resp.data
    assert b'id="groq_api_key"' in resp.data
    assert b'id="openrouter_api_key"' in resp.data
    assert b'id="deepseek_api_key"' in resp.data
    assert b'id="clear-fields"' in resp.data


def test_legacy_page_form_status_pills_render(authenticated_client, mock_firestore):
    with authenticated_client.session_transaction() as sess:
        sess['user'] = {'uid': 'admin1', 'email': 'a.savu.stefan@gmail.com'}
    resp = authenticated_client.get('/account/manage')
    assert resp.status_code == 200
    # Each provider gets a status pill (3 cards, 3 pills)
    assert resp.data.count(b'class="status-pill') == 3
