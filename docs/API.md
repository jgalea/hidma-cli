# hidma API, as the web app uses it

Derived from the production Angular bundles of app.hidma.com (main-3F5M64VT.js and chunks, read 2026-10-05 and 2026-10-06). Nothing here comes from documentation; hidma publishes none. Section "Verified live" records what the CLI has exercised against the real API.

## Hosts

| | |
|---|---|
| API | `https://api.app.hidma.com/` |
| App | `https://app.hidma.com/` |
| OAuth (Xero integration only) | `https://oauth.app.hidma.com/` |

Paths below are relative to the API base. The app builds them from a path map (`chunk-BI3LS2NG.js`, `var t`), so every segment ends in `/` unless noted. Cloudflare sits in front of both hosts and answers 403 to non-browser user agents; the CLI sends the user agent of the browser that logged in.

## Auth

Cookie based, not bearer. Every request goes out with `withCredentials: true` (Angular interceptor `_S` in main). The browser holds the session cookies for `api.app.hidma.com`; the JS never sees the access token itself.

Headers the interceptor adds to every call:

| header | value |
|---|---|
| `Accept` | `application/json` |
| `X-Requested-From` | `hidma-frontend` |
| `X-Request-Source` | a label naming the caller, e.g. `TimeTrackersService.getTimers` (`chunk-GL7LQRS6.js`); informational |
| `X-Socket-Id` | Pusher socket id, only when the websocket is up; optional |
| `Idempotency-Key` | on POST/PUT/PATCH/DELETE to the API, see below |

Cookies the app reads from JS (so they are not HttpOnly):

| cookie | meaning |
|---|---|
| `a_t_p` (config `token_payload`) | a JWT whose payload carries `sub` (user id), `jti`, `exp`, `tid` (tenant id). The app decodes the payload for the expiry and the user id. Its presence is how the app decides it is logged in. |
| `s_tid` | selected tenant id, used for the idempotency key and tenant type cache |

Other cookies set by the API (the actual session/access token) are HttpOnly and only visible from the browser profile's cookie jar. The CLI copies the whole jar for the `.app.hidma.com` / `api.app.hidma.com` domains at login.

### Login

`POST auth/login/` with the credentials form. The login form is protected by reCAPTCHA (`captchaSiteKey` in config). The CLI never calls this endpoint; the user signs in in a real browser window and the CLI takes the resulting cookies.

### Refresh

`POST auth/tokens/refresh/` with body `{"uid": "<sub from a_t_p>"}` and the cookies. The response sets fresh cookies (`authService.refreshTokensAsync`, `chunk-5ZM6BD7R.js`). The app schedules a refresh 60 s before `exp` and also retries a 401 once through a refresh.

Session dead: the refresh answers `401` with body `{"error": "invalid_token"}`, or any request answers `419`, or `a_t_p` is gone. The app then forces logout. The CLI maps this to exit code 4.

### Logout

`POST auth/logout/` with no body.

### Idempotency-Key

`chunk-RD3H54Y6.js` (`oc`): for every POST/PUT/PATCH/DELETE to the API base, the app sets `Idempotency-Key` to the SHA-256 hex of

```
userId \n tenantId \n METHOD \n urlWithParams \n canonicalBody
```

where `canonicalBody` is the JSON body with keys sorted recursively, `undefined` values dropped, `createdAt`/`updatedAt` dropped, and Dates as ISO strings. When the body cannot be serialised (Blob) the key is a random UUID. A 409 whose error contains `Idempotency-Key is already being processed` is retried up to 5 times with a 400 ms delay. The CLI computes the same hash so a retried identical request dedupes server-side.

## Response envelope

Responses are JSON objects with one of `error`, `result`, `data` (`chunk-D335H3Z3.js`, `G`). List endpoints paginate as `{data: [...], pageData: {totalElements, count, size, number, lastPage}}`, sometimes nested one level (`data.timesheets.data`). Mutations that succeed return `result: 1`.

## Data model

Time lives in weekly timesheets. A timesheet has rows; a row is a (project, job) pair; a row has at most one cell per day; a cell holds minutes.

```
Timesheet { id, user, status, startDate, endDate, rows: [Row], total, dayTotals }
Row       { id, timesheetId, project: {id, name, client} | null, job: {id, name, type}, cells: [Cell], comments, billedMinutes }
Cell      { id, rowId, date, minutes, notBillable, comments, billedMinutes, pendingOperation }
```

Job types by `type.shortcode`: `b` business (needs a project, which belongs to a client), `i` internal (optional project), `p` personal (no project). The timesheet row dialog (`app-new-timesheet-row-dialog`, `chunk-ZYO3CDLJ.js`) picks a project+job pair from `projects-jobs/autocomplete/`. In list responses `job.type` is the bare type id; the names and shortcodes come from `tenants/types/`.

`minutes` and `notBillable` are integers in minutes. The dialog turns `hh:mm` into minutes (`uc` in `chunk-2EQGT6QG.js`: `h*60+m`, plain numbers pass through). "Not billable" is a minute count inside the cell, not a flag.

`pendingOperation` is a client-side marker with values `ADD`, `EDIT`, `DELETE` (`chunk-M55H2YJN.js`). The row dialog posts cells with `pendingOperation: "ADD"`.

Ids are UUIDs generated client-side (`crypto.randomUUID` equivalent) for new timesheets, rows, cells, timers, events and favourites, then posted. Type tables (states, types, tax rates) use small integer ids.

Timesheet states have shortcodes: `o` open, `s` submitted, `a` approved. A timesheet's `status` is the state id.

## Endpoints the CLI uses

### Me and tenant

| | |
|---|---|
| `GET users/{id}` | `{user, tenants, types, defaultSettingsId}`; `user` has `name`, `surname`, `email`, `teamId`, `roles`, `rights` (`userEffects.fetchUser$`) |
| `GET tenants/types/` | the tenant's type tables: `timesheetStates`, `jobTypes`, `projectTypes`, `projectStates`, `clientTypes`, `clientStates`, `taxRates`, `billingTypes`, `billStates`, ... each a list of `{id, name, shortcode}` (`TenantsEffects.fetchTenantTypes$`). `tenants/{tid}/types/` does not exist (404). |
| `GET tenants/settings/` | `{settings: {timers: {enabled, rounding: {enabled, minutes, direction}}, timesheets: {autoCreate, approvalWorkFlow, ...}, ...}}` |
| `GET teams/` | `{teams: [{id, name, defaultOrganisation}]}` |
| `GET users/?page=1&size=100` | `{data: [{id, name, surname, teamId, active}], pageData, teams}` |

The user id and tenant id come from the `a_t_p` payload (`sub`, `tid`). No timezone is exposed anywhere in these payloads.

### Clients, projects, jobs

| | |
|---|---|
| `GET clients/?page=1&size=100&filter=...` | client list; also `&sortKey=&sortOrder=&types=[...]&selected=1` |
| `GET clients/{id}` | one client with contacts and addresses (`clientEffects.fetchClientBasicDetails$`) |
| `POST clients/` | create. Body: `{id, avatarColour: null, name, status: <clientStates id, active = 1>, type: <clientTypes id>, defaultTaxRate: null, vatNumber, contacts: [], addresses: [], comments: [], chargeOutRates: null}` (`chunk-JRE2KU2S.js` clientForm) |
| `PUT clients/{id}` | update, same shape with `status`/`type` as ids |
| `GET clients/stats/?page=1&size=&sortOrder=&sortKey=&filter=&year=all&states=[]&clientTypes=` | list with stats (clients page) |
| `GET clients/autocomplete/?term=` | |
| `GET projects/?page=&size=&term=&clients=[...]&projectTypes=[...]` | project list, `{id, name, client: {id, name}}` (`fetchProjectBatch`) |
| `GET projects/stats/{id}/` | one project with `jobs`, `client`, `team` (id), `status`, `type`, `billingTypeId`, `budgets`, `stats` (`projectsEffects.fetchProjectStats$`). `GET projects/{id}` does not exist. |
| `POST projects/` | create. Body: `{id, name, description, type: <projectTypes id>, jobs: [ids], status: <projectStates id>, team: <team id>, client: <client id> \| null, budgets: [], targetRecoverability, startDate, budgetNotificationsEnabled, budgetNotifications: [], comments: [], template: null}` (`chunk-MZ74N55M.js` loadProjectFromForm) |
| `PUT projects/{id}` | update, same shape plus `billingType` |
| `GET projects/select/?page=&size=&type=&term=&clientIds=` | lighter select list |
| `GET projects/autocomplete/?term=&page=` | |
| `GET projects-jobs/autocomplete/?term=&client=&type=&size=25&page=` | project+job pairs, what the row dialog uses |
| `GET jobs/?page=1&size=&sortOrder=&sortKey=&filter=&types=[...]&jobStates=[...]` | job list, `type` is the bare id |
| `GET jobs/{id}/` | one job with `teams` as objects (`jobsEffects.fetchJob$`) |
| `POST jobs/` | create. Body: `{id, name, active: true, type: <jobTypes id>, description, teams: [ids] \| null, projects: null, defaultTaxRate: 1, taxRates: 1, defaultOrganisation: null, chargeOutRates: null, comments: []}` (`chunk-BELRKIDE.js` loadJobFromForm; the form's default tax rate is `1`) |
| `PUT jobs/{id}` | update, same shape without `projects`/`comments` |
| `GET jobs/autocomplete/?term=` | |

There is no DELETE for clients, projects or jobs anywhere in the app. Clients and projects are closed through their state, jobs through `active`.

Page numbers in `page=` are 1-based on the list endpoints the pages use (`e.page + 1`), 0-based on `projects/select/` and `reports/raw/`.

### Timesheets

| | |
|---|---|
| `GET users/{uid}/timesheets?startDate=YYYY-MM-DD` | the week's timesheet for a user as `{data: {timesheet: {...}}}`, rows and cells included; `[]` when none exists |
| `GET users/{uid}/timesheets?active=true` | the active one |
| `GET users/{uid}/timesheets?allRowsAndCells=false` | all the user's timesheets, headers only |
| `GET timesheets/{id}?withUser=1` | one timesheet in full |
| `GET timesheets/?page=1&size=&statusId=&startDate=&endDate=&teamId=&userId=&filter=&sortOrder=&sortKey=` | paged list (`data.timesheets`) |
| `POST users/{uid}/timesheets/` | create a timesheet. Body: `{id, startDate, endDate, status: <open status id>, rows: [], user: uid, timesheetLoaded: true}` (provisional timesheet, saved the moment the first row is added). There is no DELETE for timesheets. |
| `PUT timesheets/{id}` | update a timesheet |
| `PUT timesheets/status/{statusId}` | `{id, timesheetIds, reason, message}` submit/approve/revert |

The week starts on Monday (`weekStartsOn: 1`). Dates travel as ISO strings.

### Rows and cells

| | |
|---|---|
| `POST timesheets/{tsId}/rows/` | add a row. Body: `{id, timesheetId, project: {id} \| null, job: {id}, cells: [{id, date, minutes, notBillable, comments, pendingOperation: "ADD"}], comments: null, billedMinutes: 0}` |
| `PUT timesheets/{tsId}/rows/{rowId}` | update a row; body is the row with `project` and `job` as bare ids |
| `DELETE timesheets/{tsId}/rows/?rowIds=["..."]` | delete rows |
| `POST timesheets/rows/{rowId}/cells/` | add a cell to an existing row. Body: the cell with `rowId` |
| `PUT timesheets/{tsId}/rows/{rowId}/cells/{cellId}` | update a cell: `{...cell, minutes, notBillable, comments, rowId}` |
| `PUT timesheets/{tsId}/cells/` | `{cells: [...]}` batch update |
| `DELETE timesheets/{tsId}/rows/cells/?cellIds=["..."]` | delete cells |
| `PUT timesheets/{tsId}/rows/{rowId}/cells/{cellId}/restore/` | undo a delete |
| `GET timesheets/rows/cells/?userIds=[...]&job=&project=&startDate=&endDate=` | cells across timesheets; `job` is required (422 without it) |

The row dialog's logic: if a row with the same (project, job) already exists in the week, add a cell to it (or update the day's cell if one exists); otherwise post a new row with the cell inside.

### Timers (stopwatch)

| | |
|---|---|
| `GET timesheets/{tsId}/timers/` | timers of a timesheet |
| `GET users/{uid}/timers?startDate=YYYY-MM-DD` | `{timers: [...], timesheet: {id, startDate, endDate} \| null}` for the week |
| `POST timesheets/{tsId}/timers/` | create. Body (`chunk-M55H2YJN.js` `Is`): `{id, comments, notBillable, timerEvents: [{id, eventType: "START", startTime}]}` plus one of `timesheetRowCellId` (link to today's existing cell), `timesheetRow: {id, project, job, cells: [{id, minutes: 0, date}]}` (no row for the pair yet) or `timesheetRowCell: {id, rowId, minutes: 0, date}` (row exists, no cell today) |
| `POST timesheets/{tsId}/timers/{timerId}/events/` | `{id, eventType, startTime}` append `PAUSE`, `RESUME` or `STOP` |
| `PUT timesheets/{tsId}/timers/{timerId}` | update `comments` / `notBillable` |
| `DELETE timesheets/{tsId}/timers/{timerId}` | cancel a running or paused timer; its zero-minute cell goes too. A stopped timer answers 400 "You cannot delete a timer that has already been stopped." |
| `GET favourite-timers` | `[{id, user, createdAt, project: {id, name, client}, job: {id, code, name}}]` |
| `POST favourite-timers` | `{id, projectId, jobId}` |
| `DELETE favourite-timers/{id}` | |

A timer read back carries `timesheetRowCellId`, `timesheetRowCell`, `timesheetId`, `comments`, `notBillable` and `timerEvents`, each event `{id, eventType, startTime, createdAt}`. Event types: `START`, `PAUSE`, `RESUME`, `STOP` (`chunk-M55H2YJN.js` `g`). Elapsed time is the sum of START/RESUME to the next PAUSE/STOP, plus a running tail (`hs`); the app rounds it per `settings.timers.rounding` (`Ge`).

Stamps: the app sends `startTime` as the browser's wall-clock time with no zone (`gs`). The API reads that as the tenant's local time and returns UTC with a `Z`. `createdAt` is the server's real UTC. When the browser's zone differs from the tenant's the two disagree by that difference, which is how the CLI recovers real time.

On STOP the server itself adds the tracked minutes to the linked cell (rounded up to the minute on the tenant tested) and copies the timer's comment into the cell's comment.

### Reports

| | |
|---|---|
| `GET reports/raw/?page=0&size=100&sortOrder=asc&sortKey=project&type=unbilled&billed=false&filters={"reporting":{"dateRange":{"start","end"},"clients":[...],"projects":[...]}}` | the Reports page table (`reports.service.rawReportPage`). `type` is one of `unbilled`, `notBillable`, `charges`, `internal`, `personal`. Response `{data: [...], pageData, totals: {totalMinutes, totalCost}}`; each row `{project: {id, name, client}, job: {id, code, name, type}, minutes, cost, rowIds, users, ...}`. `cost` is in minor currency units. |
| `GET bills/?page=1&size=100` | `{data: [...], pageData, meta: {sumTotal, ...}}`; row shape unverified (the tenant tested has no bills), so the CLI does not use it |

## Verified live

Exercised by the CLI against a real tenant on 2026-10-05 and 2026-10-06:

- cookies and refresh; `users/{id}`; `tenants/types/`; `tenants/settings/`; `teams/`; `users/`
- `clients/`, `projects/`, `projects/stats/{id}/`, `jobs/`, `jobs/{id}/` (reads)
- the week timesheet envelope `{data: {timesheet}}`, cell dates as ISO midnight, row and cell create/update/delete
- timers: create with `timesheetRowCell`, PAUSE, RESUME, STOP (server writes the cell), DELETE of a live timer (cell removed too), DELETE of a stopped timer refused
- favourites: create, list, delete
- `reports/raw/` type `unbilled` with totals

Not exercised live: `POST`/`PUT` on clients, projects and jobs (the tenant has no way to delete test records, so those are covered by unit tests against the form bodies above), `timesheets/status/`, bills.
