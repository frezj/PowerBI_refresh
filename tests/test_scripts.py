import io

import pytest
from responses import matchers

import Check_Not_Refreshed
import Refresh_All
from pbi_client import API_URL

WS = f'{API_URL}/groups'
TOP1 = [matchers.query_param_matcher({'$top': '1'})]


@pytest.fixture
def env(monkeypatch, client):
    monkeypatch.setenv('PBI_WORKSPACE_IDS', 'ws1,ws2')
    # Not a console, so run() must not wait for Enter
    monkeypatch.setattr('sys.stdin', io.StringIO())
    for module in (Refresh_All, Check_Not_Refreshed):
        monkeypatch.setattr(module, 'connect', lambda: client)


def datasets(api, workspace, *items, status=200):
    api.get(f'{WS}/{workspace}/datasets', status=status, json={'value': list(items)})


def ds(dataset_id, name, push=False):
    return {'id': dataset_id, 'name': name, 'addRowsAPIEnabled': push}


def last_refresh(api, workspace, dataset_id, *refreshes, status=200):
    api.get(f'{WS}/{workspace}/datasets/{dataset_id}/refreshes', match=TOP1, status=status,
            json={'value': list(refreshes)})


def refresh(api, workspace, dataset_id, status=202):
    api.post(f'{WS}/{workspace}/datasets/{dataset_id}/refreshes', status=status)


def posted(api):
    return [c.request.url for c in api.calls if c.request.method == 'POST']


# Refresh_All

def test_refresh_all_triggers_every_dataset(api, env, capsys):
    datasets(api, 'ws1', ds('d1', 'Sales'), ds('d2', 'Live KPI', push=True))
    datasets(api, 'ws2', ds('d3', 'Finance'))
    refresh(api, 'ws1', 'd1')
    refresh(api, 'ws2', 'd3')
    assert Refresh_All.main() == 0
    assert posted(api) == [f'{WS}/ws1/datasets/d1/refreshes', f'{WS}/ws2/datasets/d3/refreshes']
    assert 'Triggered: 2, skipped: 1, failed: 0' in capsys.readouterr().out


def test_refresh_all_continues_after_errors(api, env, capsys):
    datasets(api, 'ws1', status=403)
    datasets(api, 'ws2', ds('d1', 'Sales'), ds('d2', 'Finance'), ds('d3', 'HR'))
    refresh(api, 'ws2', 'd1', status=429)  # daily limit
    refresh(api, 'ws2', 'd2', status=400)
    refresh(api, 'ws2', 'd3')
    assert Refresh_All.main() == 1
    out = capsys.readouterr().out
    assert 'Dataset "Sales" refresh limit exceeded' in out
    assert 'Triggered: 1, skipped: 0, failed: 3' in out
    for name in ('workspace ws1', 'Sales', 'Finance'):
        assert f'  failed: {name}' in out


# Check_Not_Refreshed

def test_status_of_last_refresh(api, client):
    last_refresh(api, 'ws1', 'd1', {'status': 'Failed', 'endTime': '2026-11-27T03:00:00Z'})
    last_refresh(api, 'ws1', 'd2')
    assert Check_Not_Refreshed.check_dataset_refresh_status(client, 'ws1', 'd1') == ('Failed', '2026-11-27T03:00:00Z')
    assert Check_Not_Refreshed.check_dataset_refresh_status(client, 'ws1', 'd2') == ('Never', None)


def test_check_refreshes_only_failed(api, env, capsys):
    datasets(api, 'ws1', ds('d1', 'Sales'), ds('d2', 'Finance'), ds('d3', 'Running'), ds('d4', 'New'),
             ds('d5', 'Live KPI', push=True))
    datasets(api, 'ws2')
    last_refresh(api, 'ws1', 'd1', {'status': 'Failed', 'endTime': '2026-11-27T03:00:00Z'})
    last_refresh(api, 'ws1', 'd2', {'status': 'Completed', 'endTime': '2026-11-27T03:05:00Z'})
    last_refresh(api, 'ws1', 'd3', {'status': 'Unknown'})  # in progress
    last_refresh(api, 'ws1', 'd4')
    refresh(api, 'ws1', 'd1')
    assert Check_Not_Refreshed.main() == 0
    assert posted(api) == [f'{WS}/ws1/datasets/d1/refreshes']
    assert 'Triggered: 1, skipped: 4, failed: 0' in capsys.readouterr().out


def test_check_continues_after_history_error(api, env, capsys):
    datasets(api, 'ws1', ds('d1', 'Sales'), ds('d2', 'Finance'))
    datasets(api, 'ws2')
    last_refresh(api, 'ws1', 'd1', status=401)
    last_refresh(api, 'ws1', 'd2', {'status': 'Failed'})
    refresh(api, 'ws1', 'd2')
    assert Check_Not_Refreshed.main() == 1
    out = capsys.readouterr().out
    assert 'Triggered: 1, skipped: 0, failed: 1' in out
    assert '  failed: Sales' in out


# run(): exit codes and pause

@pytest.mark.parametrize('module', [Refresh_All, Check_Not_Refreshed])
def test_missing_config_exits_with_2(module, env, monkeypatch, capsys):
    monkeypatch.delenv('PBI_WORKSPACE_IDS')
    assert module.run() == 2
    assert 'Config error: Missing environment variables: PBI_WORKSPACE_IDS' in capsys.readouterr().out


@pytest.mark.parametrize('module', [Refresh_All, Check_Not_Refreshed])
def test_unexpected_error_prints_traceback(module, env, monkeypatch, capsys):
    def broken():
        raise RuntimeError('boom')
    monkeypatch.setattr(module, 'connect', broken)
    assert module.run() == 1
    assert 'RuntimeError: boom' in capsys.readouterr().err


class Console(io.StringIO):
    def isatty(self):
        return True


@pytest.mark.parametrize('module', [Refresh_All, Check_Not_Refreshed])
def test_pause_only_in_console(module, env, monkeypatch):
    prompts = []
    monkeypatch.setattr('builtins.input', prompts.append)
    monkeypatch.delenv('PBI_WORKSPACE_IDS')
    module.run()
    assert prompts == []
    monkeypatch.setattr('sys.stdin', Console())
    module.run()
    assert prompts == ['Press Enter to exit...']
