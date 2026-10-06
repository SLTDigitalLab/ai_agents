# Admin access comparison: staging and production

## Observed result

The same admin endpoint was requested on both sites without Microsoft login, Authorization headers, or cookies. No redirects were followed. Both requests were read-only.

| Environment | Request time (Sri Lanka) | HTTP result | Observed response |
| --- | --- | --- | --- |
| Staging | 2026-10-01 10:15:08 | 200 | Admin ingestion status returned; private status values omitted from this report. |
| Production | 2026-10-01 10:15:26 | 401 | Not authenticated |

## What this demonstrates

Staging allowed an anonymous caller to read an admin endpoint. Production rejected the identical request with HTTP 401 and `Not authenticated`. This demonstrates the missing backend authentication on staging and the enforced authentication on the tested production endpoint.

This check did not log in as the supervisor, bypass the browser interface, retrieve chat conversations, trigger ingestion, or modify data. It is evidence of anonymous API access, not a screenshot of the admin panel or a reconstruction of the original visitor's actions. It does not certify every endpoint or establish whether earlier access occurred.

The separate dashboard statistics check returned HTTP 401 on production. The staging dashboard statistics request timed out; that result is inconclusive and must not be presented as successful access or successful protection.

## Repeat the demonstration

Run the following from a terminal. Neither command supplies login credentials:

```bash
curl --max-time 30 -i https://theaisle.raccoon-ai.io/api/v1/admin/ingestion-status
curl --max-time 30 -i https://aiagents.sltdigitallab.lk/api/v1/admin/ingestion-status
```

For a presentation, capture the URL and HTTP status. Redact any returned staging filenames, source URLs, or other internal status values. Results can change after a deployment.

## Evidence files

- `admin-ingestion-status-comparison.json`: recorded status comparison, with internal status values omitted.
- `admin-access-comparison.json`: dashboard request results, including the staging timeout.
- `check_admin_access.py`: repeatable read-only check for these two exact hosts.

After the demonstration, apply the same admin protection to staging; it remains exposed on the tested endpoint.
