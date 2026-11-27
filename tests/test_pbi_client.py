import pytest
import requests
from responses import matchers

import pbi_client
from pbi_client import API_URL, ConfigError, PowerBIClient, PowerBIError, TokenProvider

URL = f'{API_URL}/groups'


# Config

def test_service_principal_when_secret_is_set():
    auth = pbi_client.load_auth_config({
        'PBI_TENANT_ID': 't', 'PBI_CLIENT_ID': 'c', 'PBI_CLIENT_SECRET': 's',
        'PBI_USERNAME': 'u', 'PBI_PASSWORD': 'p',
    })
    assert auth == {'mode': 'service_principal', 'tenant_id': 't', 'client_id': 'c', 'client_secret': 's'}


def test_password_flow_uses_default_client_id():
    auth = pbi_client.load_auth_config({'PBI_TENANT_ID': 't', 'PBI_USERNAME': 'u', 'PBI_PASSWORD': 'p'})
    assert auth['mode'] == 'password'
    assert auth['client_id'] == pbi_client.DEFAULT_CLIENT_ID


def test_missing_variables_are_listed():
    with pytest.raises(ConfigError, match='PBI_USERNAME, PBI_PASSWORD'):
        pbi_client.load_auth_config({'PBI_TENANT_ID': 't'})


def test_service_principal_needs_client_id():
    with pytest.raises(ConfigError, match='PBI_CLIENT_ID'):
        pbi_client.load_auth_config({'PBI_TENANT_ID': 't', 'PBI_CLIENT_SECRET': 's'})


def test_workspace_ids_are_split_and_stripped():
    ids = pbi_client.load_workspace_ids({'PBI_WORKSPACE_IDS': ' a, b ,,c '})
    assert ids == ['a', 'b', 'c']


@pytest.mark.parametrize('value', ['', ' , ,'])
def test_empty_workspace_ids(value):
    with pytest.raises(ConfigError):
        pbi_client.load_workspace_ids({'PBI_WORKSPACE_IDS': value})


# Requests and retries

def test_auth_header(api, client):
    api.get(URL, json={'value': []}, match=[matchers.header_matcher({'Authorization': 'Bearer test-token'})])
    assert client.get('groups') == {'value': []}


def test_new_token_for_each_attempt(api, sleeps):
    tokens = iter(['t1', 't2'])
    client = PowerBIClient(lambda: next(tokens), sleep=sleeps.append)
    api.get(URL, status=503, match=[matchers.header_matcher({'Authorization': 'Bearer t1'})])
    api.get(URL, json={}, match=[matchers.header_matcher({'Authorization': 'Bearer t2'})])
    client.get('groups')


def test_absolute_url_is_used_as_is(client):
    assert client.url('https://example.test/x?a=1') == 'https://example.test/x?a=1'
    assert client.url('/groups') == URL


def test_get_retries_429_with_retry_after(api, client, sleeps):
    api.get(URL, status=429, headers={'Retry-After': '7'})
    api.get(URL, json={'value': [1]})
    assert client.get('groups') == {'value': [1]}
    assert sleeps == [7.0]


def test_get_retries_5xx_with_backoff(api, client, sleeps):
    api.get(URL, status=503)
    api.get(URL, status=500)
    api.get(URL, json={})
    client.get('groups')
    assert sleeps == [2.0, 4.0]


def test_retry_after_http_date_falls_back_to_backoff(api, client, sleeps):
    api.get(URL, status=429, headers={'Retry-After': 'Wed, 21 Oct 2026 07:28:00 GMT'})
    api.get(URL, json={})
    client.get('groups')
    assert sleeps == [2.0]


def test_get_gives_up_after_max_retries(api, client, sleeps):
    for _ in range(4):
        api.get(URL, status=500, body='boom')
    with pytest.raises(PowerBIError) as err:
        client.get('groups', action='List workspaces')
    assert err.value.status_code == 500
    assert 'List workspaces: HTTP 500 boom' in str(err.value)
    assert len(api.calls) == 4
    assert sleeps == [2.0, 4.0, 8.0]


def test_long_retry_after_is_not_waited(api, client, sleeps):
    api.get(URL, status=429, headers={'Retry-After': '3600'})
    with pytest.raises(PowerBIError) as err:
        client.get('groups')
    assert err.value.status_code == 429
    assert sleeps == []


def test_post_is_not_retried(api, client, sleeps):
    api.post(f'{URL}/ws/datasets/d/refreshes', status=429)
    with pytest.raises(PowerBIError) as err:
        client.post('groups/ws/datasets/d/refreshes')
    assert err.value.status_code == 429
    assert len(api.calls) == 1
    assert sleeps == []


def test_4xx_is_not_retried(api, client, sleeps):
    api.get(URL, status=403)
    with pytest.raises(PowerBIError):
        client.get('groups')
    assert len(api.calls) == 1


def test_connection_error_is_retried_for_get(api, client, sleeps):
    api.get(URL, body=requests.ConnectionError('reset'))
    api.get(URL, json={})
    client.get('groups')
    assert sleeps == [2.0]


def test_connection_error_is_raised_for_post(api, client):
    api.post(URL, body=requests.ConnectionError('reset'))
    with pytest.raises(requests.ConnectionError):
        client.post('groups')


# Paging

def test_get_all_follows_continuation_uri(api, client):
    next_url = f'{API_URL}/admin/activityevents?continuationToken=abc'
    api.get(f'{API_URL}/admin/activityevents',
            match=[matchers.query_param_matcher({'startDateTime': "'2026-01-01T00:00:00Z'"})],
            json={'activityEventEntities': [1, 2], 'continuationUri': next_url, 'lastResultSet': False})
    # Second page: params must not be sent again
    api.get(next_url, match=[matchers.query_param_matcher({'continuationToken': 'abc'})],
            json={'activityEventEntities': [3], 'continuationUri': next_url + '2', 'lastResultSet': True})
    items = list(client.get_all('admin/activityevents', key='activityEventEntities',
                                params={'startDateTime': "'2026-01-01T00:00:00Z'"}))
    assert items == [1, 2, 3]


def test_get_all_single_page(api, client):
    api.get(URL, json={'value': [{'id': 'a'}]})
    assert list(client.get_all('groups')) == [{'id': 'a'}]


def test_get_all_stops_at_max_pages(api, client):
    api.get(URL, json={'value': [1], 'continuationUri': URL})
    with pytest.raises(PowerBIError, match='more than 2 pages'):
        list(client.get_all('groups', max_pages=2))


# Token

class FakeApp:
    def __init__(self, result, accounts=()):
        self.result = result
        self.accounts = list(accounts)
        self.calls = []

    def acquire_token_for_client(self, scopes):
        self.calls.append('client')
        return self.result

    def get_accounts(self, username=None):
        return self.accounts

    def acquire_token_silent(self, scopes, account):
        self.calls.append('silent')
        return self.result

    def acquire_token_by_username_password(self, username, password, scopes):
        self.calls.append('password')
        return self.result


SP_AUTH = {'mode': 'service_principal', 'tenant_id': 't', 'client_id': 'c', 'client_secret': 's'}
PW_AUTH = {'mode': 'password', 'tenant_id': 't', 'client_id': 'c', 'username': 'u', 'password': 'p'}


def make_provider(monkeypatch, auth, app):
    monkeypatch.setattr(pbi_client.msal, 'ConfidentialClientApplication', lambda *a, **k: app)
    monkeypatch.setattr(pbi_client.msal, 'PublicClientApplication', lambda *a, **k: app)
    return TokenProvider(auth)


def test_service_principal_token(monkeypatch):
    app = FakeApp({'access_token': 'abc'})
    assert make_provider(monkeypatch, SP_AUTH, app)() == 'abc'
    assert app.calls == ['client']


def test_password_flow_uses_cache_first(monkeypatch):
    app = FakeApp({'access_token': 'abc'}, accounts=[{'username': 'u'}])
    assert make_provider(monkeypatch, PW_AUTH, app)() == 'abc'
    assert app.calls == ['silent']


def test_password_flow_without_cache(monkeypatch):
    app = FakeApp({'access_token': 'abc'})
    make_provider(monkeypatch, PW_AUTH, app)()
    assert app.calls == ['password']


def test_token_error_shows_aadsts_code(monkeypatch):
    app = FakeApp({'error': 'invalid_grant', 'error_description': 'AADSTS50076: MFA required'})
    with pytest.raises(PowerBIError, match='AADSTS50076'):
        make_provider(monkeypatch, PW_AUTH, app)()
