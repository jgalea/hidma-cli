<h1 align="center">hidma-cli</h1>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/LICENSE-MIT-5C9E31?style=for-the-badge" alt="License"></a>
  <a href="https://github.com/jgalea"><img src="https://img.shields.io/badge/BUILT%20BY-JGALEA-8A2BE2?style=for-the-badge&logo=github&logoColor=white" alt="Built by jgalea"></a>
</p>

<p align="center"><strong>Log and read your hidma timesheets from the terminal, or let an AI agent do it over MCP.</strong></p>

An unofficial client for [hidma](https://hidma.com), the time tracking and billing app. It uses the same API as the hidma web app.

```
hidma login                                   # one-time, opens a browser window
hidma whoami
hidma clients
hidma projects [--client X]
hidma jobs
hidma log 1h30 "Sprint planning" --project acme
hidma log 45m "Admin" --job internal --date 2026-10-03 --no-billable
hidma entries                                 # this week, with totals per day and project
hidma entries --from 2026-09-01 --to 2026-09-30 --project acme
hidma edit <id> --duration 2h --description "..."
hidma delete <id> --yes
hidma timers                                  # the week's stopwatch timers, read-only
```

`--json` works on every command.

## Setup

```
uv tool install -e . --with playwright       # or: uv run hidma ...
uv run playwright install chromium            # once, for `hidma login`
```

Playwright is only needed for `login`; everything else is plain HTTP with no dependencies.

## How login and auth work

hidma's login form sits behind reCAPTCHA and the API authenticates with cookies, so the CLI never posts a password. `hidma login` opens a dedicated Chromium profile on the app's login page; you sign in there, and as soon as the app is logged in the cookies for hidma.com are copied to `~/.local/share/hidma-cli/session.json` (mode 600). The browser then closes.

Every other command replays those cookies over HTTPS against `api.app.hidma.com`, with the same headers the web app sends. The access token cookie carries an expiry; the CLI refreshes it through the app's own refresh endpoint (`POST auth/tokens/refresh/`) a minute before it runs out, or once after a 401, and saves the new cookies. When the refresh is refused the command exits 4 and tells you to run `hidma login` again. Token values are never printed; `whoami` shows ages and expiry only.

`docs/API.md` records the endpoints and request bodies, taken from the app's JavaScript bundles.

## How time is stored

hidma keeps time in weekly timesheets. A timesheet has rows (a project plus a job), and a row has one cell per day holding minutes, a comment and a count of non-billable minutes. `hidma log` does what the app's manual-entry dialog does: it finds the week's timesheet (creating it when the week has none), finds the row for that project and job (creating it when missing), and adds the day's cell. Logging twice on the same project and day merges into the existing cell: minutes add up and the comment is appended, and the command says so.

`--project` matches the project name or code case-insensitively, by substring; an ambiguous match lists the candidates and exits 2. `--job` picks the job (hidma's activity type); when omitted and there is exactly one business job, that one is used. Internal and personal jobs take no project.

`entries` lists the cells in the range (default: the current Monday to Sunday) with the entry id, then totals per day and per project. `edit` and `delete` take that id and look for it in the hinted `--date` week first, then this week and the previous twelve. `delete` needs `--yes`; when the deleted cell was its row's last one, the row goes too, so the week looks as it did before.

## MCP server

`hidma-mcp` is a stdio MCP server exposing `whoami`, `projects`, `entries` and `log`. It needs the `mcp` extra (`uv sync --extra mcp`) and the same saved session. It is not registered anywhere; to use it from Claude Code:

```
claude mcp add hidma -- uv run --directory /path/to/hidma-cli hidma-mcp
```

## Exit codes

| code | meaning |
|---|---|
| 0 | fine |
| 1 | API or transport failure |
| 2 | bad input: unparseable duration, unknown or ambiguous project, missing `--yes` |
| 4 | no session or session expired, run `hidma login` |

## Limits

- Only your own timesheets. There is no bulk import and no team view.
- Timers are read-only here; start and stop them in the app.
- A day's entry on one project and job is a single cell, so two separate comments on the same day end up in one merged comment.
- Submitted or approved timesheets are locked by hidma; writes into them fail with the API's error.

## Development

```
uv sync --group dev
uv run python -m pytest -q
```
