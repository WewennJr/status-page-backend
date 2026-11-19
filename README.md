# Status Page — Backend

FastAPI + SQLite backend for a self-hosted status page. It runs HTTP checks on
your services, records the results, opens/resolves incidents automatically and
exposes a public read-only API plus a token-protected admin API.

- **Stack**: Python 3.12, FastAPI, SQLAlchemy (async) + aiosqlite, httpx, bcrypt
- **Package manager**: [uv](https://docs.astral.sh/uv/)
- **Server**: uvicorn on `127.0.0.1:8002` (meant to sit behind nginx)

## Quick start

```bash
cd status-backend
uv sync
cp .env.example .env          # optional, defaults to development
uv run status-page-backend    # http://127.0.0.1:8002
```

Interactive API docs are available at `/api/docs` in development.
On first run, create the admin account from the frontend (`/setup`); the setup
endpoint is permanently locked (403) as soon as an admin exists.

In production, set `STATUS_ENVIRONMENT=production`: uvicorn reload, CORS and the
API docs are disabled.

## Configuration

Settings are read from `status-backend/.env` (loaded automatically) or from the
process environment.

| Variable             | Default       | Description                                                    |
| -------------------- | ------------- | -------------------------------------------------------------- |
| `STATUS_ENVIRONMENT` | `development` | `production` disables reload, CORS and `/api/docs`.            |
| `STATUS_DATA_DIR`    | `./data`      | Directory holding `status.db` (created on startup).            |

The listen address/port are fixed in `status_backend/__init__.py`.
The SQLite database lives in `<backend>/data/status.db`.

## How it works

- A scheduler loop wakes up every **10 s** and checks each active monitor when
  its own `interval_seconds` is due (60 s by default).
- A check is a `GET` (redirects followed) with a **10 s** timeout; any status
  code in **200–399** is considered up. Response time is stored for successful
  checks only.
- When a monitor goes down, an automatic incident is created
  (`<name> is unreachable`); when it recovers, the incident is resolved with an
  update. Manual incidents are never touched by the scheduler.
- Every hour, checks older than `retention_days` (default 90, configurable from
  the admin settings) and expired sessions are deleted.
- Checks run sequentially per monitor but concurrently within a scan pass, with
  a shared `httpx.AsyncClient`.

## Data model

| Table            | Purpose                                                              |
| ---------------- | -------------------------------------------------------------------- |
| `admins`         | Admin account (bcrypt password hash)                                 |
| `session_tokens` | Opaque session tokens (hashed-URL-safe), expiry, admin link          |
| `monitors`       | Checked sites: name, URL, group, position, interval, contact email   |
| `checks`         | One row per check (status, response time, error, timestamp)          |
| `incidents`      | Manual or automatic incidents, status, resolution time               |
| `incident_updates` | Timeline entries for an incident                                   |
| `settings`       | Key/value site settings and defaults                                 |

## API

All routes are prefixed with `/api`. Public routes are read-only; admin routes
require the `sp_session` cookie.

### Public

| Method | Path                              | Description                                   |
| ------ | --------------------------------- | --------------------------------------------- |
| GET    | `/api/public/overview`            | Site settings, monitors, uptime bars, incidents |
| GET    | `/api/public/incidents`           | Incident history                              |
| GET    | `/api/public/monitor/{id}/history`| Aggregated history for one monitor            |

### Setup & auth

| Method | Path                | Description                                            |
| ------ | ------------------- | ------------------------------------------------------ |
| GET    | `/api/setup-required` | Whether the first-run setup is still needed          |
| POST   | `/api/setup`        | Create the admin account (403 once it exists)          |
| POST   | `/api/auth/login`   | Log in, sets the session cookie                        |
| POST   | `/api/auth/logout`  | Log out, clears the cookie                             |
| GET    | `/api/auth/me`      | Current authenticated state                            |

### Admin (cookie required)

| Method | Path                                   | Description                                  |
| ------ | -------------------------------------- | -------------------------------------------- |
| GET    | `/api/admin/monitors`                  | List monitors                                |
| POST   | `/api/admin/monitors`                  | Create a monitor                             |
| PUT    | `/api/admin/monitors/{id}`             | Update a monitor                             |
| DELETE | `/api/admin/monitors/{id}`             | Delete a monitor and its checks              |
| POST   | `/api/admin/monitors/reorder`          | Bulk reorder / regroup monitors              |
| GET    | `/api/admin/incidents`                 | List incidents (`limit` 1–200)               |
| POST   | `/api/admin/incidents`                 | Create a manual incident                     |
| PUT    | `/api/admin/incidents/{id}`            | Update an incident                           |
| POST   | `/api/admin/incidents/{id}/updates`    | Add a timeline update                        |
| DELETE | `/api/admin/incidents/{id}`            | Delete an incident                           |
| GET    | `/api/admin/settings`                  | Read all site settings                       |
| PUT    | `/api/admin/settings`                  | Update site settings                         |
| GET    | `/api/admin/account`                   | Read the admin username                      |
| PUT    | `/api/admin/account`                   | Change username/password                     |
| GET    | `/api/admin/stats`                     | Dashboard stats (uptime/response series…)    |

The reorder payload is:

```json
{ "items": [{ "id": 1, "group_name": "Web", "position": 0 }] }
```

Duplicated ids are rejected with `422`, unknown ids with `404`.

## Security

- Passwords hashed with **bcrypt**; a dummy hash is checked for unknown users to
  keep login timing constant.
- Sessions are random tokens stored in SQLite and sent through an `HttpOnly`,
  `SameSite=Lax` cookie (14-day expiry), flagged `Secure` when the request
  arrives over HTTPS (via nginx `X-Forwarded-Proto`).
- **Setup** uses a single atomic `INSERT … WHERE NOT EXISTS`, so concurrent
  requests cannot create two admins.
- **Rate limiting** per IP for login (10/min) and setup (20/min).
- **CSRF**: `SameSite=Lax` plus an `Origin` check on POST/PUT/PATCH/DELETE
  (localhost is accepted in development only).
- **Input validation**: monitor `url`, `site_url` and `favicon_url` must match
  `^https?://\S+$`; colors are validated as hex; usernames/passwords are length
  bounded.
- **Security headers**: `X-Content-Type-Options`, `X-Frame-Options`,
  `Referrer-Policy`, `Permissions-Policy`, HSTS behind HTTPS, and
  `Cache-Control: no-store` on auth/admin routes.
- Changing the admin password invalidates all other sessions.

## Production

```bash
uv sync --no-dev
printf 'STATUS_ENVIRONMENT=production\n' > .env
uv run status-page-backend
```

Run it under systemd (dedicated user, `Restart=on-failure`,
`WorkingDirectory=/path/to/status-backend`) behind nginx, which serves the
frontend build and proxies `/api/` to `127.0.0.1:8002`. See the
`status-frontend/deploy/nginx-status-page.conf` template for headers, CSP and
proxy settings.

## Project layout

```
src/status_backend/
  __init__.py        # entry point (uvicorn, port 8002)
  app.py             # create_app() factory, middleware, lifespan
  checker.py         # check scheduler + automatic incidents + cleanup
  auth.py            # password hashing, session helpers
  ratelimit.py       # in-memory per-IP limiter
  database.py        # async engine/session, schema creation, indexes
  models.py          # SQLAlchemy models
  schemas.py         # Pydantic request/response models
  settings.py        # site settings defaults
  core/config.py     # .env loading
  routers/
    public.py        # read-only public API
    admin.py         # setup/auth + admin API
```
