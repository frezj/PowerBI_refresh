import os
import sys
import traceback

import msal
import requests

# Optional: read variables from a .env file next to the script
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env'))
except ImportError:
    pass

API_URL = 'https://api.powerbi.com/v1.0/myorg'
SCOPE = ['https://analysis.windows.net/powerbi/api/.default']
HTTP_TIMEOUT = 60  # seconds

# Public client_id for Power BI. Register your own app if your tenant blocks it.
DEFAULT_CLIENT_ID = 'ea0616ba-638b-4df5-95b9-636659ae5121'
REQUIRED_VARS = ['PBI_TENANT_ID', 'PBI_USERNAME', 'PBI_PASSWORD', 'PBI_WORKSPACE_IDS']


class ConfigError(Exception):
    pass


class PowerBIError(Exception):
    pass


# Read config from environment variables
def load_config():
    missing = [name for name in REQUIRED_VARS if not os.getenv(name)]
    if missing:
        raise ConfigError('Missing environment variables: ' + ', '.join(missing))
    workspace_ids = [w.strip() for w in os.environ['PBI_WORKSPACE_IDS'].split(',') if w.strip()]
    if not workspace_ids:
        raise ConfigError('PBI_WORKSPACE_IDS has no workspace ids')
    return {
        'tenant_id': os.environ['PBI_TENANT_ID'],
        'client_id': os.getenv('PBI_CLIENT_ID') or DEFAULT_CLIENT_ID,
        'username': os.environ['PBI_USERNAME'],
        'password': os.environ['PBI_PASSWORD'],
        'workspace_ids': workspace_ids,
    }


def api_error(action, response):
    return PowerBIError(f'{action}: HTTP {response.status_code} {response.text[:500]}')


def auth_headers(access_token):
    return {
        'Authorization': f'Bearer {access_token}',
        'Content-Type': 'application/json'
    }


# Get token
def get_access_token(tenant_id, client_id, username, password):
    authority = f'https://login.microsoftonline.com/{tenant_id}'
    app = msal.PublicClientApplication(client_id, authority=authority)
    token_response = app.acquire_token_by_username_password(username, password, scopes=SCOPE)
    if 'access_token' in token_response:
        return token_response['access_token']
    # error_description has the AADSTS code, e.g. AADSTS50076 when MFA is required
    raise PowerBIError('Failed to obtain access token: {} {}'.format(
        token_response.get('error'), token_response.get('error_description')))


# List of datasets
def get_datasets_in_workspace(access_token, workspace_id):
    url = f'{API_URL}/groups/{workspace_id}/datasets'
    response = requests.get(url, headers=auth_headers(access_token), timeout=HTTP_TIMEOUT)
    if response.status_code == 200:
        return response.json().get('value', [])
    raise api_error(f'Failed to retrieve datasets in workspace {workspace_id}', response)


# Check status
def check_dataset_refresh_status(access_token, workspace_id, dataset_id):
    url = f'{API_URL}/groups/{workspace_id}/datasets/{dataset_id}/refreshes?$top=1'
    response = requests.get(url, headers=auth_headers(access_token), timeout=HTTP_TIMEOUT)
    if response.status_code != 200:
        raise api_error('Failed to retrieve refresh history', response)
    refreshes = response.json().get('value', [])
    if not refreshes:
        return 'Never', None
    last_refresh = refreshes[0]
    # Status 'Unknown' means in progress, endTime is missing then
    return last_refresh.get('status'), last_refresh.get('endTime')


def is_push_dataset(dataset):
    # Push (streaming) datasets get data via the API and cannot be refreshed
    return bool(dataset.get('addRowsAPIEnabled'))


# Func for refresh
def trigger_dataset_refresh(access_token, workspace_id, dataset_id, dataset_name):
    url = f'{API_URL}/groups/{workspace_id}/datasets/{dataset_id}/refreshes'
    response = requests.post(url, headers=auth_headers(access_token), timeout=HTTP_TIMEOUT)
    if response.status_code == 202:
        return True
    if response.status_code == 429:
        # Shared capacity allows 8 refreshes per day, scheduled ones included
        print(f'Dataset "{dataset_name}" refresh limit exceeded. Try again later.')
        return False
    raise api_error(f'Failed to trigger refresh for dataset "{dataset_name}"', response)


# Re-trigger refresh for datasets whose last refresh failed
def main():
    config = load_config()
    access_token = get_access_token(config['tenant_id'], config['client_id'], config['username'], config['password'])
    triggered, skipped, failed = [], [], []
    for workspace_id in config['workspace_ids']:
        try:
            datasets = get_datasets_in_workspace(access_token, workspace_id)
        except PowerBIError as e:
            print(f'Error: {e}')
            failed.append(f'workspace {workspace_id}')
            continue
        for dataset in datasets:
            dataset_name = dataset['name']
            if is_push_dataset(dataset):
                print(f'Skipping dataset "{dataset_name}" as it is a push dataset')
                skipped.append(dataset_name)
                continue
            try:
                status, end_time = check_dataset_refresh_status(access_token, workspace_id, dataset['id'])
                if status != 'Failed':
                    print(f'Dataset "{dataset_name}" status: {status}. Last refresh end time: {end_time}')
                    skipped.append(dataset_name)
                elif trigger_dataset_refresh(access_token, workspace_id, dataset['id'], dataset_name):
                    print(f'Dataset "{dataset_name}" refresh triggered. Failed refresh end time: {end_time}')
                    triggered.append(dataset_name)
                else:
                    failed.append(dataset_name)
            except PowerBIError as e:
                print(f'Error processing dataset "{dataset_name}": {e}')
                failed.append(dataset_name)
            except Exception:
                print(f'Error processing dataset "{dataset_name}":')
                traceback.print_exc()
                failed.append(dataset_name)

    print(f'Done. Triggered: {len(triggered)}, skipped: {len(skipped)}, failed: {len(failed)}')
    for name in failed:
        print(f'  failed: {name}')
    return 1 if failed else 0


def run():
    try:
        exit_code = main()
    except ConfigError as e:
        print(f'Config error: {e}')
        exit_code = 2
    except Exception:
        traceback.print_exc()
        exit_code = 1
    # Keep the console open on double-click, do not block cron/Airflow
    if sys.stdin is not None and sys.stdin.isatty():
        input('Press Enter to exit...')
    return exit_code


if __name__ == '__main__':
    sys.exit(run())
