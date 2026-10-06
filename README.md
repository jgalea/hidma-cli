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
hidma edit <id> --duration 2h --description "..."
hidma delete <id> --yes

hidma today                                   # today's entries
hidma entries                                 # this week, with totals per day and project
hidma entries --from 2026-09-01 --to 2026-09-30 --project acme
hidma last 5                                  # the five most recent entries
hidma week [--date 2026-09-30]                # the week as a grid, like the app's timesheet

hidma report --by client --month 2026-09      # time per client, project, job, day or week
hidma report --by day --week 2026-09-28 --csv
hidma report --unbilled [--client acme]       # hidma's unbilled report: time and cost not yet billed

hidma timer start "Sprint planning" --project acme
hidma timer status
hidma timer pause / resume
hidma timer stop                              # writes the minutes into today's entry
hidma timer discard --yes                     # drop the timer and its time
hidma timers                                  # the week's timers

hidma fav list / add --project acme / remove <id>
hidma client add "Acme" [--type c|s|u] [--vat ...]
hidma client edit acme --state i
hidma project add "Website" --client acme --jobs consulting,planning
hidma project edit website --add-job design --state c
hidma job add "Design" [--type b|i|p]
hidma job edit design --inactive
hidma teams / users
```

`--json` works on every command.

## Why a CLI

hidma's web app is a fine place to review a week and a slow place to log one line. "Log 1h30 on project X" is three clicks, a dialog and a date picker there; here it is one line you can type, alias or pipe. The same calls back an MCP server, so an agent that already knows what you worked on can log it, start a timer or pull last month's hours per client without a browser.

## Setup

```
uv tool install "hidma-cli[login] @ git+https://github.com/jgalea/hidma-cli"
uv run playwright install chromium            # once, for `hidma login`
```

Or from a checkout: `uv tool install -e . --with playwright`. Playwright is only needed for `login`; everything else is plain HTTP with no dependencies.

## How login and auth work

hidma's login form sits behind reCAPTCHA and the API authenticates with cookies, so the CLI never posts a password. `hidma login` opens a dedicated Chromium profile on the app's login page; you sign in there, and as soon as the app is logged in the cookies for hidma.com are copied to `~/.local/share/hidma-cli/session.json` (mode 600). The browser then closes.

Every other command replays those cookies over HTTPS against `api.app.hidma.com`, with the same headers the web app sends. The access token cookie carries an expiry; the CLI refreshes it through the app's own refresh endpoint (`POST auth/tokens/refresh/`) a minute before it runs out, or once after a 401, and saves the new cookies. When the refresh is refused the command exits 4 and tells you to run `hidma login` again. Token values are never printed; `whoami` shows ages and expiry only.

`docs/API.md` records the endpoints and request bodies, taken from the app's JavaScript bundles.

## How time is stored

hidma keeps time in weekly timesheets. A timesheet has rows (a project plus a job), and a row has one cell per day holding minutes, a comment and a count of non-billable minutes. `hidma log` does what the app's manual-entry dialog does: it finds the week's timesheet (creating it when the week has none), finds the row for that project and job (creating it when missing), and adds the day's cell. Logging twice on the same project and day merges into the existing cell: minutes add up and the comment is appended, and the command says so.

`--project` matches the project name or code case-insensitively, by substring; an ambiguous match lists the candidates and exits 2. `--job` picks the job (hidma's activity type); when omitted and there is exactly one business job, that one is used. Internal and personal jobs take no project.

`entries` lists the cells in the range (default: the current Monday to Sunday) with the entry id, then totals per day and per project. `today`, `last` and `week` are views over the same cells. `edit` and `delete` take the entry id and look for it in the hinted `--date` week first, then this week and the previous twelve. `delete` needs `--yes`; when the deleted cell was its row's last one, the row goes too, so the week looks as it did before.

## Reports

`report` sums the cells in a range (`--week`, `--month`, `--from`/`--to`; default this week) per client, project, job, day or week, with billable and non-billable minutes split out. `--csv` writes the same rows as CSV. `report --unbilled` reads hidma's own unbilled report instead: per project and job, the minutes not yet billed and their cost at the configured rates, for all time or `--from`/`--to`.

## Timers

`timer start` does what the app's stopwatch widget does: it creates a timer linked to today's cell for the project and job, creating the row or a zero-minute cell when the day has none, and posts a START event. `pause`, `resume` and `stop` append events. hidma allows one live timer at a time. On `stop` the server adds the tracked minutes to the cell and copies the timer's comment into it; the CLI reads the cell back, writes the minutes itself only when the server left the cell untouched, and prints what the entry holds. `discard` deletes a running or paused timer (the app's cancel); a zero-minute cell the timer created goes with it. Stopped timers stay in the week's list, hidma refuses to delete them.

Event stamps are sent as wall-clock time without a zone, as the app sends them, and the API reads them as the tenant's local time. When your machine is in another zone than the tenant, the stored stamps are off by the difference; the CLI corrects for that using the server's own creation time on each event, so `timer status` shows the real elapsed time.

## Clients, projects and jobs

`client add`, `project add` and `job add` post the same bodies as the app's forms, with the defaults those forms use (active state, company type, the tenant's default tax rate). `edit` reads the record, changes the given fields and puts it back. hidma has no delete for any of the three, so neither does the CLI: close a client or project with `--state c`, retire a job with `--inactive`.

## MCP server

`hidma-mcp` is a stdio MCP server exposing `whoami`, `projects`, `entries`, `today`, `week`, `log`, `report`, `unbilled`, `timer_start`, `timer_status` and `timer_stop`. It needs the `mcp` extra (`uv sync --extra mcp`) and the same saved session. It is not registered anywhere; to use it from Claude Code:

```
claude mcp add hidma -- uv run --directory /path/to/hidma-cli hidma-mcp
```

## Exit codes

| code | meaning |
|---|---|
| 0 | fine |
| 1 | API or transport failure, or a write that did not read back as expected |
| 2 | bad input: unparseable duration, unknown or ambiguous project, missing `--yes` |
| 4 | no session or session expired, run `hidma login` |

## Limits

- Only your own timesheets. There is no bulk import and no team view.
- A day's entry on one project and job is a single cell, so two separate comments on the same day end up in one merged comment.
- Submitted or approved timesheets are locked by hidma; writes into them fail with the API's error.
- Bills are not covered. Budgets, charge-out rates and contacts are left as the app set them when editing.

## Development

```
uv sync --group dev
uv run python -m pytest -q
```
