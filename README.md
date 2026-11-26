# PowerBI_refresh

Two small Python scripts to refresh Power BI datasets (semantic models) through the Power BI REST API.

- `Refresh_All.py` triggers a refresh for every dataset in the listed workspaces.
- `Check_Not_Refreshed.py` looks at the last refresh of every dataset and triggers a new refresh only where it failed.

Typical use: a source system was late or a gateway was down at night. Run `Check_Not_Refreshed.py` in the morning and only the broken datasets are refreshed again. `Refresh_All.py` is for the case when all reports must show new data now, for example after a manual data fix.

## How it works

1. Get an access token from Microsoft Entra ID with `msal` (username and password flow).
2. For each workspace id: `GET /groups/{workspaceId}/datasets`.
3. Skip push datasets (`addRowsAPIEnabled = true`), they cannot be refreshed.
4. `Refresh_All.py`: `POST /groups/{workspaceId}/datasets/{datasetId}/refreshes` for each dataset.
   `Check_Not_Refreshed.py`: first `GET .../refreshes?$top=1`, and only if the last status is `Failed`, send the same POST.
5. Print a summary and exit with a non-zero code if something failed.

The API answers `202 Accepted` and the refresh runs in the background. The scripts do not wait for it to finish.

## Setup

Python 3.8 or newer.

```bash
pip install -r requirements.txt
```

Copy `.env.example` to `.env` next to the scripts and fill it in, or set the same variables in the environment. `.env` is in `.gitignore`, do not commit it.

| Variable | Required | Description |
|---|---|---|
| `PBI_TENANT_ID` | yes | Entra ID tenant id (GUID) or domain, e.g. `contoso.onmicrosoft.com` |
| `PBI_USERNAME` | yes | Account that has write access to the datasets |
| `PBI_PASSWORD` | yes | Password of this account |
| `PBI_WORKSPACE_IDS` | yes | Comma-separated workspace ids. Take them from the workspace URL: `app.powerbi.com/groups/<id>/...` |
| `PBI_CLIENT_ID` | no | Client id of your app registration. Default is the public Power BI client id |

Environment variables win over values in `.env`. `python-dotenv` is optional: without it the scripts read only the environment.

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
| 1 | At least one workspace or dataset failed, or the token request failed |
| 2 | Config error, e.g. a missing variable |

## Gotchas

- **MFA.** The username and password flow (ROPC) does not work for accounts with MFA or with Conditional Access policies that require it. You get an error like `AADSTS50076` or `AADSTS50079`. Use a separate service account without MFA, or a service principal. Service principal support is planned for these scripts.
- **Do not use `USERNAME`.** Older versions read `TENANT_ID`, `USERNAME`, `PASSWORD`, `WORKSPACE_IDS`. On Windows `USERNAME` is always set to your Windows login, so the script sent the wrong user. All variables now have the `PBI_` prefix.
- **Refresh limit on shared capacity.** A dataset in a Pro (shared) workspace can be refreshed 8 times per day, scheduled refreshes included. After that the API returns `429` and the script reports the dataset as failed. Premium and Fabric capacities have no fixed daily limit.
- **Permissions.** The account needs write permission on the dataset (Contributor or higher in the workspace). Reading refresh history also needs write permission. Without it you get `401` or `403` for that workspace or dataset.
- **Re-trigger does not fix everything.** If a refresh failed because of expired data source credentials or a gateway that is offline, `Check_Not_Refreshed.py` will start it again and it will fail again. Look at the error in the Power BI service first.
- **In-progress refresh.** The refresh history shows status `Unknown` while a refresh is running. `Check_Not_Refreshed.py` does not touch such datasets.
- **Client id.** The default client id is a public Microsoft app. Some tenants block it or require admin consent. In that case register your own public client app in Entra ID with the delegated permission `Dataset.ReadWrite.All` for Power BI Service and put its id in `PBI_CLIENT_ID`.

## Links

- [Refresh Dataset In Group](https://learn.microsoft.com/en-us/rest/api/power-bi/datasets/refresh-dataset-in-group)
- [Get Refresh History In Group](https://learn.microsoft.com/en-us/rest/api/power-bi/datasets/get-refresh-history-in-group)
- [Data refresh in Power BI](https://learn.microsoft.com/en-us/power-bi/connect-data/refresh-data)

## License

Public domain (The Unlicense), see [LICENSE](LICENSE).
