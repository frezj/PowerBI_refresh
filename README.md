# PowerBI_refresh

Two small Python scripts to refresh Power BI datasets (semantic models) through the Power BI REST API.

- `Refresh_All.py` triggers a refresh for every dataset in the listed workspaces.
- `Check_Not_Refreshed.py` looks at the last refresh of every dataset and triggers a new refresh only where it failed.

Typical use: a source system was late or a gateway was down at night. Run `Check_Not_Refreshed.py` in the morning and only the broken datasets are refreshed again. `Refresh_All.py` is for the case when all reports must show new data now, for example after a manual data fix.

## How it works

1. Get an access token from Microsoft Entra ID with `msal`: service principal (client credentials) or username and password.
2. For each workspace id: `GET /groups/{workspaceId}/datasets`.
3. Skip push datasets (`addRowsAPIEnabled = true`), they cannot be refreshed.
4. `Refresh_All.py`: `POST /groups/{workspaceId}/datasets/{datasetId}/refreshes` for each dataset.
   `Check_Not_Refreshed.py`: first `GET .../refreshes?$top=1`, and only if the last status is `Failed`, send the same POST.
5. Print a summary and exit with a non-zero code if something failed.

The API answers `202 Accepted` and the refresh runs in the background. The scripts do not wait for it to finish.

Auth and HTTP code is in `pbi_client.py`. Both scripts import it, so keep it in the same folder. It does:

- Token from `msal`. The token is cached and renewed when it expires, so long runs over many workspaces do not fail with `401`.
- Timeout of 60 s on every call.
- `GET` is retried up to 3 times on `429` and `5xx`. The wait is the `Retry-After` header if the API sends it, otherwise 2, 4, 8 s. If `Retry-After` is longer than 5 minutes, it does not wait and reports the error.
- `POST` is not retried. A second POST could start the same refresh twice, and a `429` on refresh means the daily limit, so waiting does not help.
- Paging via `continuationUri` (used by admin APIs such as activity events). Lists like datasets come in one page, this does not change anything for them.

You can copy `pbi_client.py` to your own scripts:

```python
from pbi_client import connect

client = connect()  # reads PBI_* env vars
for ws in client.get_all('groups'):
    print(ws['id'], ws['name'])
```

## Setup

Python 3.8 or newer.

```bash
pip install -r requirements.txt
```

Copy `.env.example` to `.env` next to the scripts and fill it in, or set the same variables in the environment. `.env` is in `.gitignore`, do not commit it.

| Variable | Required | Description |
|---|---|---|
| `PBI_TENANT_ID` | yes | Entra ID tenant id (GUID) or domain, e.g. `contoso.onmicrosoft.com` |
| `PBI_WORKSPACE_IDS` | yes | Comma-separated workspace ids. Take them from the workspace URL: `app.powerbi.com/groups/<id>/...` |
| `PBI_CLIENT_ID` | for service principal | Application (client) id of your app registration. For the password flow it is optional, default is the public Power BI client id |
| `PBI_CLIENT_SECRET` | for service principal | Client secret. If set, the scripts use the service principal and ignore user name and password |
| `PBI_USERNAME` | for password flow | Account that has write access to the datasets |
| `PBI_PASSWORD` | for password flow | Password of this account |

Environment variables win over values in `.env`. `python-dotenv` is optional: without it the scripts read only the environment.

### Service principal (recommended)

The username and password flow (ROPC) does not work with MFA, and many tenants require MFA. A service principal has no MFA and no password that expires with a user account.

1. In Entra ID, register an app and create a client secret. Do not add Power BI API permissions to the app. They are not used, and permissions that need admin consent can cause errors.
2. Create a security group and add the app to it.
3. In the Power BI (Fabric) admin portal, Tenant settings, Developer settings, enable *Service principals can call Fabric public APIs* (older name: *Allow service principals to use Power BI APIs*) for this group.
4. Add the app or the group to every workspace in `PBI_WORKSPACE_IDS` as Member or Admin.
5. Set `PBI_TENANT_ID`, `PBI_CLIENT_ID`, `PBI_CLIENT_SECRET`.

Limits: a service principal cannot access *My workspace*, and some read-only APIs do not support it. A client secret has an expiry date, set a reminder to renew it.

### Username and password (fallback)

Set `PBI_USERNAME` and `PBI_PASSWORD` and leave `PBI_CLIENT_SECRET` empty. Use an account without MFA. See Gotchas.

## Run

```bash
python Refresh_All.py
python Check_Not_Refreshed.py
```

On Windows you can also double-click the file. The window stays open until you press Enter, so you can read the output. When the script runs without a console (Task Scheduler, cron, Airflow) it does not wait for input.

Example output:

```
Dataset "Sales" refresh triggered. Failed refresh end time: 2024-05-14T03:12:40.103Z
Dataset "Finance" status: Completed. Last refresh end time: 2024-05-14T03:05:11.870Z
Skipping dataset "Live KPI" as it is a push dataset
Done. Triggered: 1, skipped: 2, failed: 0
```

Exit codes, useful for schedulers:

| Code | Meaning |
|---|---|
| 0 | All datasets processed |
| 1 | At least one workspace or dataset failed after retries, or the token request failed |
| 2 | Config error, e.g. a missing variable |

## Gotchas

- **MFA.** The username and password flow (ROPC) does not work for accounts with MFA or with Conditional Access policies that require it. You get an error like `AADSTS50076` or `AADSTS50079`. Use a service principal (see above) or a separate service account without MFA.
- **Do not use `USERNAME`.** Older versions read `TENANT_ID`, `USERNAME`, `PASSWORD`, `WORKSPACE_IDS`. On Windows `USERNAME` is always set to your Windows login, so the script sent the wrong user. All variables now have the `PBI_` prefix.
- **Refresh limit on shared capacity.** A dataset in a Pro (shared) workspace can be refreshed 8 times per day, scheduled refreshes included. After that the API returns `429` and the script reports the dataset as failed. Premium and Fabric capacities have no fixed daily limit.
- **Permissions.** The account needs write permission on the dataset (Contributor or higher in the workspace). Reading refresh history also needs write permission. Without it you get `401` or `403` for that workspace or dataset.
- **Re-trigger does not fix everything.** If a refresh failed because of expired data source credentials or a gateway that is offline, `Check_Not_Refreshed.py` will start it again and it will fail again. Look at the error in the Power BI service first.
- **In-progress refresh.** The refresh history shows status `Unknown` while a refresh is running. `Check_Not_Refreshed.py` does not touch such datasets.
- **Client id.** The default client id is a public Microsoft app. Some tenants block it or require admin consent. In that case register your own public client app in Entra ID with the delegated permission `Dataset.ReadWrite.All` for Power BI Service and put its id in `PBI_CLIENT_ID`.

## Tests

The tests do not call Power BI or Entra ID. HTTP is mocked with [responses](https://github.com/getsentry/responses) and `msal` is replaced with a fake, so no credentials are needed.

```bash
pip install -r requirements-dev.txt
ruff check .
pytest
```

What is covered:

- `pbi_client.py`: config from env vars, retries on `429` and `5xx` with `Retry-After` and backoff, no retry for `POST`, paging via `continuationUri`, token errors.
- `Refresh_All.py` and `Check_Not_Refreshed.py`: which datasets get a refresh, push datasets, errors in one workspace or dataset do not stop the run, exit codes, the Enter pause only in a console.

GitHub Actions runs the same checks on every push to `main` and on pull requests, on Python 3.9 and 3.13.

## Links

- [Refresh Dataset In Group](https://learn.microsoft.com/en-us/rest/api/power-bi/datasets/refresh-dataset-in-group)
- [Get Refresh History In Group](https://learn.microsoft.com/en-us/rest/api/power-bi/datasets/get-refresh-history-in-group)
- [Data refresh in Power BI](https://learn.microsoft.com/en-us/power-bi/connect-data/refresh-data)
- [Service principal setup](https://learn.microsoft.com/en-us/power-bi/developer/embedded/embed-service-principal)
- [Power BI REST API throttling](https://learn.microsoft.com/en-us/rest/api/power-bi/)

## License

Public domain (The Unlicense), see [LICENSE](LICENSE).
