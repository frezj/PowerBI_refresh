"""Shared Power BI auth and HTTP client for the scripts in this repo.

Copy this file next to your own script and import it, no install needed.
"""
import os
import time

import msal
import requests

API_URL = 'https://api.powerbi.com/v1.0/myorg'
SCOPE = ['https://analysis.windows.net/powerbi/api/.default']
HTTP_TIMEOUT = 60  # seconds

# Public client_id for Power BI. Register your own app if your tenant blocks it.
DEFAULT_CLIENT_ID = 'ea0616ba-638b-4df5-95b9-636659ae5121'

RETRY_STATUSES = (429, 500, 502, 503, 504)


class ConfigError(Exception):
    pass


class PowerBIError(Exception):
    def __init__(self, message, status_code=None):
        super().__init__(message)
        self.status_code = status_code


# Optional: read variables from a .env file in the given folder
def load_dotenv_file(folder):
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(os.path.join(folder, '.env'))


def _require(env, names):
    missing = [name for name in names if not env.get(name)]
    if missing:
        raise ConfigError('Missing environment variables: ' + ', '.join(missing))


# Read auth settings from env. Service principal if PBI_CLIENT_SECRET is set.
def load_auth_config(env=None):
    env = os.environ if env is None else env
    if env.get('PBI_CLIENT_SECRET'):
        _require(env, ['PBI_TENANT_ID', 'PBI_CLIENT_ID'])
        return {
            'mode': 'service_principal',
            'tenant_id': env['PBI_TENANT_ID'],
            'client_id': env['PBI_CLIENT_ID'],
            'client_secret': env['PBI_CLIENT_SECRET'],
        }
    _require(env, ['PBI_TENANT_ID', 'PBI_USERNAME', 'PBI_PASSWORD'])
    return {
        'mode': 'password',
        'tenant_id': env['PBI_TENANT_ID'],
        'client_id': env.get('PBI_CLIENT_ID') or DEFAULT_CLIENT_ID,
        'username': env['PBI_USERNAME'],
        'password': env['PBI_PASSWORD'],
    }


def load_workspace_ids(env=None, name='PBI_WORKSPACE_IDS'):
    env = os.environ if env is None else env
    _require(env, [name])
    workspace_ids = [w.strip() for w in env[name].split(',') if w.strip()]
    if not workspace_ids:
        raise ConfigError(f'{name} has no workspace ids')
    return workspace_ids


class TokenProvider:
    """Returns a valid access token. msal caches it and renews it when it expires."""

    def __init__(self, auth):
        self.auth = auth
        authority = f"https://login.microsoftonline.com/{auth['tenant_id']}"
        if auth['mode'] == 'service_principal':
            self.app = msal.ConfidentialClientApplication(
                auth['client_id'], authority=authority, client_credential=auth['client_secret'])
        else:
            self.app = msal.PublicClientApplication(auth['client_id'], authority=authority)

    def __call__(self):
        if self.auth['mode'] == 'service_principal':
            result = self.app.acquire_token_for_client(scopes=SCOPE)
        else:
            result = None
            accounts = self.app.get_accounts(username=self.auth['username'])
            if accounts:
                result = self.app.acquire_token_silent(SCOPE, account=accounts[0])
            if not result:
                result = self.app.acquire_token_by_username_password(
                    self.auth['username'], self.auth['password'], scopes=SCOPE)
        if result and 'access_token' in result:
            return result['access_token']
        result = result or {}
        # error_description has the AADSTS code, e.g. AADSTS50076 when MFA is required
        raise PowerBIError('Failed to obtain access token: {} {}'.format(
            result.get('error'), result.get('error_description')))


class PowerBIClient:
    """Thin wrapper over requests: auth header, timeout, retries, paging.

    GET is retried on 429 and 5xx. Other methods are not retried by default,
    because a repeated POST can start the same job twice.
    """

    def __init__(self, token_provider, base_url=API_URL, timeout=HTTP_TIMEOUT,
                 max_retries=3, backoff=2.0, max_wait=300, session=None, sleep=time.sleep):
        self.token_provider = token_provider
        self.base_url = base_url.rstrip('/')
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff = backoff
        self.max_wait = max_wait  # do not wait longer than this for one retry
        self.session = session or requests.Session()
        self.sleep = sleep

    def url(self, path):
        if path.startswith('https://'):
            return path
        return f"{self.base_url}/{path.lstrip('/')}"

    def _wait_seconds(self, response, attempt):
        retry_after = response.headers.get('Retry-After')
        if retry_after:
            try:
                return float(retry_after)
            except ValueError:
                pass  # HTTP date format, use backoff
        return self.backoff * (2 ** attempt)

    def request(self, method, path, retry=None, **kwargs):
        if retry is None:
            retry = method.upper() == 'GET'
        kwargs.setdefault('timeout', self.timeout)
        url = self.url(path)
        attempt = 0
        while True:
            headers = dict(kwargs.pop('headers', None) or {})
            headers['Authorization'] = f'Bearer {self.token_provider()}'
            headers.setdefault('Content-Type', 'application/json')
            kwargs['headers'] = headers
            try:
                response = self.session.request(method, url, **kwargs)
            except (requests.ConnectionError, requests.Timeout):
                if not retry or attempt >= self.max_retries:
                    raise
                self.sleep(self.backoff * (2 ** attempt))
                attempt += 1
                continue
            if not retry or response.status_code not in RETRY_STATUSES or attempt >= self.max_retries:
                return response
            wait = self._wait_seconds(response, attempt)
            if wait > self.max_wait:
                return response
            print(f'HTTP {response.status_code} from {method} {url}, retry in {wait:.0f} s')
            self.sleep(wait)
            attempt += 1

    def check(self, response, action):
        if 200 <= response.status_code < 300:
            return response
        raise PowerBIError(f'{action}: HTTP {response.status_code} {response.text[:500]}',
                           status_code=response.status_code)

    def get(self, path, action=None, **kwargs):
        response = self.request('GET', path, **kwargs)
        return self.check(response, action or f'GET {path}').json()

    def post(self, path, action=None, **kwargs):
        response = self.request('POST', path, **kwargs)
        return self.check(response, action or f'POST {path}')

    def get_all(self, path, key='value', action=None, max_pages=1000, **kwargs):
        """Yield items from all pages. Follows continuationUri (e.g. admin activity events)."""
        url = path
        for _ in range(max_pages):
            data = self.get(url, action=action, **kwargs)
            for item in data.get(key) or []:
                yield item
            url = data.get('continuationUri')
            if not url or data.get('lastResultSet'):
                return
            kwargs.pop('params', None)  # continuationUri already has the query
        raise PowerBIError(f'{action or path}: more than {max_pages} pages')


def connect(env=None):
    """Client from env vars. Gets the first token right away, so auth errors show early."""
    provider = TokenProvider(load_auth_config(env))
    provider()
    return PowerBIClient(provider)
