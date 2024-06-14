import os
import requests
import msal

# Retrieve sensitive information from environment variables
TENANT_ID = os.getenv('TENANT_ID')
CLIENT_ID = os.getenv('CLIENT_ID')
USERNAME = os.getenv('USERNAME')
PASSWORD = os.getenv('PASSWORD')
WORKSPACE_IDS = os.getenv('WORKSPACE_IDS').split(',')

# Function to get access token using username and password
def get_access_token(tenant_id, client_id, username, password):
    authority = f'https://login.microsoftonline.com/{tenant_id}'
    app = msal.PublicClientApplication(client_id, authority=authority)
    scope = ['https://analysis.windows.net/powerbi/api/.default']
    token_response = app.acquire_token_by_username_password(username, password, scopes=scope)
    if 'access_token' in token_response:
        return token_response['access_token']
    else:
        raise Exception('Failed to obtain access token')

# Function to get datasets in a workspace
def get_datasets_in_workspace(access_token, workspace_id):
    url = f'https://api.powerbi.com/v1.0/myorg/groups/{workspace_id}/datasets'
    headers = {
        'Authorization': f'Bearer {access_token}',
        'Content-Type': 'application/json'
    }
    response = requests.get(url, headers=headers)
    if response.status_code == 200:
        return response.json().get('value', [])
    else:
        raise Exception(f'Failed to retrieve datasets: {response.text}')

# Function to determine if dataset is model-based
def is_model_based_dataset(dataset):
    return dataset.get('dataflow') is None

# Function to trigger dataset refresh
def trigger_dataset_refresh(access_token, workspace_id, dataset_id, dataset_name):
    url = f'https://api.powerbi.com/v1.0/myorg/groups/{workspace_id}/datasets/{dataset_id}/refreshes'
    headers = {
        'Authorization': f'Bearer {access_token}',
        'Content-Type': 'application/json'
    }
    response = requests.post(url, headers=headers)
    if response.status_code == 202:
        return True
    elif response.status_code == 429:
        print(f'Dataset "{dataset_name}" refresh limit exceeded. Try again later.')
        return False
    else:
        raise Exception(f'Failed to trigger refresh for dataset "{dataset_name}": {response.text}')

# Main function
def main():
    try:
        access_token = get_access_token(TENANT_ID, CLIENT_ID, USERNAME, PASSWORD)
        for workspace_id in WORKSPACE_IDS:
            datasets = get_datasets_in_workspace(access_token, workspace_id)
            for dataset in datasets:
                dataset_id = dataset['id']
                dataset_name = dataset['name']
                if is_model_based_dataset(dataset):
                    try:
                        success = trigger_dataset_refresh(access_token, workspace_id, dataset_id, dataset_name)
                        if success:
                            print(f'Dataset "{dataset_name}" refresh triggered.')
                        else:
                            print(f'Dataset "{dataset_name}" refresh limit exceeded.')
                    except Exception as e:
                        print(f'Error processing dataset "{dataset_name}": {e}')
                else:
                    print(f'Skipping dataset "{dataset_name}" as it is a dataflow')
    except Exception as e:
        print(f'Error: {e}')

if __name__ == '__main__':
    main()
