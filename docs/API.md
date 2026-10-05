# hidma API, as the web app uses it

Derived from the production Angular bundles of app.hidma.com (main-3F5M64VT.js and chunks, read 2026-10-05). Nothing here comes from documentation; hidma publishes none. Section "Verified live" records what the CLI has exercised against the real API.

## Hosts

| | |
|---|---|
| API | `https://api.app.hidma.com/` |
| App | `https://app.hidma.com/` |
| OAuth (Xero integration only) | `https://oauth.app.hidma.com/` |

Paths below are relative to the API base. The app builds them from a path map (`chunk-BI3LS2NG.js`, `var t`), so every segment ends in `/` unless noted.

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

Responses are JSON objects with one of `error`, `result`, `data` (`chunk-D335H3Z3.js`, `G`). List endpoints paginate as `{data: [...], pageData: {totalElements, size, number, lastPage}}`, sometimes nested one level (`data.timesheets.data`). Mutations that succeed return `result: 1`.

## Data model

Time lives in weekly timesheets. A timesheet has rows; a row is a (project, job) pair; a row has at most one cell per day; a cell holds minutes.

```
Timesheet { id, user, status, startDate, endDate, rows: [Row], total, dayTotals }
Row       { id, timesheetId, project: {id, name, client} | null, job: {id, name, type}, cells: [Cell], comments, billedMinutes }
Cell      { id, rowId, date, minutes, notBillable, comments, billedMinutes, pendingOperation }
```

Job types by `type.shortcode`: `b` business (needs a project, which belongs to a client), `i` internal (optional project), `p` personal (no project). The timesheet row dialog (`app-new-timesheet-row-dialog`, `chunk-ZYO3CDLJ.js`) picks a project+job pair from `projects-jobs/autocomplete/`.

`minutes` and `notBillable` are integers in minutes. The dialog turns `hh:mm` into minutes (`uc` in `chunk-2EQGT6QG.js`: `h*60+m`, plain numbers pass through). "Not billable" is a minute count inside the cell, not a flag.

`pendingOperation` is a client-side marker with values `ADD`, `EDIT`, `DELETE` (`chunk-M55H2YJN.js`). The row dialog posts cells with `pendingOperation: "ADD"`.

Ids are UUIDs generated client-side (`crypto.randomUUID` equivalent) for new timesheets, rows and cells, then posted.

Timesheet states have shortcodes: `o` open, `s` submitted, `a` approved.

## Endpoints the CLI uses

### Me

| | |
|---|---|
| `GET users/{id}` | one user (`userEffects.fetchUser$`) |
| `GET tenants/settings/id` | current tenant settings id |

The user id and tenant id come from the `a_t_p` payload (`sub`, `tid`).

### Clients, projects, jobs

| | |
|---|---|
| `GET clients/?page=1&size=100&filter=...` | client list; also `&sortKey=&sortOrder=&types=[...]&selected=1` |
| `GET clients/stats/?page=1&size=&sortOrder=&sortKey=&filter=&year=all&states=[]&clientTypes=` | list with stats (clients page) |
| `GET clients/autocomplete/?term=` | |
| `GET projects/?page=&size=&term=&clients=[...]&projectTypes=[...]` | project list (`fetchProjectBatch`) |
| `GET projects/select/?page=&size=&type=&term=&clientIds=` | lighter select list |
| `GET projects/stats/?page=1&size=&filter=&...` | list with stats (projects page) |
| `GET projects/autocomplete/?term=&page=` | |
| `GET projects-jobs/autocomplete/?term=&client=&type=&size=25&page=` | project+job pairs, what the row dialog uses |
| `GET jobs/?page=1&size=&sortOrder=&sortKey=&filter=&types=[...]&jobStates=[...]` | job list |
| `GET jobs/autocomplete/?term=` | |

Page numbers in `page=` are 1-based on the list endpoints the pages use (`e.page + 1`), 0-based on `projects/select/`.

### Timesheets

| | |
|---|---|
| `GET users/{uid}/timesheets?startDate=YYYY-MM-DD` | the week's timesheet(s) for a user, rows and cells included; `[]` when none exists |
| `GET users/{uid}/timesheets?active=true` | the active one |
| `GET users/{uid}/timesheets?allRowsAndCells=false` | all the user's timesheets, headers only |
| `GET timesheets/{id}?withUser=1` | one timesheet in full |
| `GET timesheets/?page=1&size=&statusId=&startDate=&endDate=&teamId=&userId=&filter=&sortOrder=&sortKey=` | paged list (`data.timesheets`) |
| `POST users/{uid}/timesheets/` | create a timesheet. Body: `{id, startDate, endDate, status: <open status id>, rows: [], user: uid, timesheetLoaded: true}` (provisional timesheet, saved the moment the first row is added) |
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
| `GET timesheets/rows/cells/?userIds=[...]&job=&project=&startDate=&endDate=` | cells across timesheets for reports |

The row dialog's logic: if a row with the same (project, job) already exists in the week, add a cell to it (or update the day's cell if one exists); otherwise post a new row with the cell inside.

### Timers (stopwatch)

| | |
|---|---|
| `GET timesheets/{tsId}/timers/` | timers of a timesheet |
| `GET users/{uid}/timers?startDate=YYYY-MM-DD` | `{timers, timesheet}` for the week |
| `POST timesheets/{tsId}/timers/` | create. Body is the timer with `timesheetRowCellId` (or a `timesheetRow`/`timesheetRowCell` to create), `comments`, `notBillable`, `timerEvents: [{id, eventType, startTime}]` |
| `POST timesheets/{tsId}/timers/{timerId}/events/` | `{id, eventType, startTime}` append start/pause/stop |
| `PUT timesheets/{tsId}/timers/{timerId}` | update |
| `DELETE timesheets/{tsId}/timers/{timerId}` | cancel |
| `GET/POST/DELETE favourite-timers` | |

On stop, the app rounds the accumulated seconds per the tenant's `timers.rounding` setting and writes the minutes into the linked cell.

## Verified live

Not yet. Two `hidma login` windows (2026-10-05 09:37 and 09:42) closed unanswered, so no endpoint above has been exercised by the CLI. The unit tests cover the request shapes against a fake server only. First live run should check: the cookie names the API sets at login, the exact `GET users/{uid}/timesheets?startDate=` response shape, the cell `date` format on read, and where the tenant's timesheet states come from (the CLI tries `tenants/{tid}/types/`).
