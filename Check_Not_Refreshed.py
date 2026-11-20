import os
import sys
import traceback

from pbi_client import ConfigError, PowerBIError, connect, load_dotenv_file, load_workspace_ids

load_dotenv_file(os.path.dirname(os.path.abspath(__file__)))


# List of datasets
def get_datasets_in_workspace(client, workspace_id):
    return list(client.get_all(f'groups/{workspace_id}/datasets',
                               action=f'Failed to retrieve datasets in workspace {workspace_id}'))


# Check status
def check_dataset_refresh_status(client, workspace_id, dataset_id):
    data = client.get(f'groups/{workspace_id}/datasets/{dataset_id}/refreshes?$top=1',
                      action='Failed to retrieve refresh history')
    refreshes = data.get('value', [])
    if not refreshes:
        return 'Never', None
    last_refresh = refreshes[0]
    # Status 'Unknown' means in progress, endTime is missing then
    return last_refresh.get('status'), last_refresh.get('endTime')


def is_push_dataset(dataset):
    # Push (streaming) datasets get data via the API and cannot be refreshed
    return bool(dataset.get('addRowsAPIEnabled'))


# Func for refresh
def trigger_dataset_refresh(client, workspace_id, dataset_id, dataset_name):
    try:
        client.post(f'groups/{workspace_id}/datasets/{dataset_id}/refreshes',
                    action=f'Failed to trigger refresh for dataset "{dataset_name}"')
    except PowerBIError as e:
        if e.status_code == 429:
            # Shared capacity allows 8 refreshes per day, scheduled ones included
            print(f'Dataset "{dataset_name}" refresh limit exceeded. Try again later.')
            return False
        raise
    return True


# Re-trigger refresh for datasets whose last refresh failed
def main():
    workspace_ids = load_workspace_ids()
    client = connect()
    triggered, skipped, failed = [], [], []
    for workspace_id in workspace_ids:
        try:
            datasets = get_datasets_in_workspace(client, workspace_id)
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
                status, end_time = check_dataset_refresh_status(client, workspace_id, dataset['id'])
                if status != 'Failed':
                    print(f'Dataset "{dataset_name}" status: {status}. Last refresh end time: {end_time}')
                    skipped.append(dataset_name)
                elif trigger_dataset_refresh(client, workspace_id, dataset['id'], dataset_name):
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
    except PowerBIError as e:
        print(f'Error: {e}')
        exit_code = 1
    except Exception:
        traceback.print_exc()
        exit_code = 1
    # Keep the console open on double-click, do not block cron/Airflow
    if sys.stdin is not None and sys.stdin.isatty():
        input('Press Enter to exit...')
    return exit_code


if __name__ == '__main__':
    sys.exit(run())
