# EasyLV backend

Django REST backend for Sam & Ches' Facebook Live-selling workflow. The
simulated comment source, fake PayMongo provider, and console message sender are
offline defaults; tests block outbound network calls.

## Quickstart: first simulated mine

Requires Python 3.13, `uv`, Docker Desktop, and Docker Compose. From a terminal
with the workspace uv environment active:

```sh
git clone <repository-url> easylv
cd easylv
cp .env.example .env
make up
make migrate
set -a; . ./.env; set +a; uv run --active python manage.py createsuperuser
set -a; . ./.env; set +a; uv run --active python manage.py shell -c 'from decimal import Decimal; from inventory.services import manage_inventory; manage_inventory(action="create", mining_code="101", name="Demo jacket", selling_price=Decimal("100.00"), acquisition_cost=Decimal("40.00"), stock_qty=1)'
set -a; . ./.env; set +a; uv run --active python manage.py simulate_comment --code=101 --user=Juan
```

`simulate_comment` runs offline and exercises the same mining task path. To use
the API, start `make run` in another terminal after this quickstart, then sign
in at `/admin/login/` with a staff user. The Django session is accepted by the
API; browser requests use SessionAuthentication and standard CSRF protection.
Create staff users with the desired EasyLV role.

## Configuration

Copy `.env.example` to `.env`. Never commit `.env` or use development secrets in
a shared environment.

| Variable | Purpose | Default |
| --- | --- | --- |
| `DJANGO_SECRET_KEY` | Django signing secret | Required |
| `DJANGO_DEBUG` | Debug mode; enables the simulator endpoint | `false` |
| `DJANGO_ALLOWED_HOSTS` | Comma-separated allowed hosts | `localhost,127.0.0.1` |
| `DATABASE_URL` | PostgreSQL connection for Django | Required |
| `POSTGRES_DB` | Compose PostgreSQL database | `easylv` |
| `POSTGRES_USER` | Compose PostgreSQL user | `easylv` |
| `POSTGRES_PASSWORD` | Compose PostgreSQL password | Required by Compose |
| `POSTGRES_PORT` | Host PostgreSQL port | `5432` |
| `REDIS_PORT` | Host Redis port | `6379` |
| `CELERY_BROKER_URL` | Celery broker | `redis://localhost:6379/0` |
| `CELERY_RESULT_BACKEND` | Celery result backend | `redis://localhost:6379/1` |
| `COMMENT_SOURCE` | `simulated` or deferred `graph_api` source | `simulated` |
| `FB_APP_SECRET` | Facebook webhook signature secret | Empty |
| `FB_VERIFY_TOKEN` | Facebook webhook subscription token | Empty |
| `FB_PAGE_ACCESS_TOKEN` | Reserved for future Facebook integration | Empty |
| `PAYMENT_PROVIDER` | `fake` or `paymongo` | `fake` |
| `PAYMONGO_SECRET_KEY` | PayMongo credential when enabled | Empty |
| `MESSAGE_SENDER` | `console` or deferred `graph_api` sender | `console` |
| `INVOICE_DELAY_SECONDS` | Post-commit invoice batching delay | `0` |
| `INVOICE_RECONCILIATION_INTERVAL_SECONDS` | Invoice gap reconciliation beat interval | `30` |
| `INVOICE_RECONCILIATION_GRACE_SECONDS` | Grace period before retrying an unchanged invoice gap | `60` |
| `CELERY_VISIBILITY_TIMEOUT` | Redis broker visibility timeout; `0` calculates at least 3600 seconds and exceeds the invoice countdown | `0` |
| `MINING_PATTERN` | Full-message regular expression; capture code in group 1 | See `.env.example` |
| `PAYMENT_WINDOW_HOURS` | Reservation payment window | `24` |

`PAYMENT_PROVIDER=fake` and `MESSAGE_SENDER=console` require no credentials and
never contact external services. PayMongo endpoint/field details and all
Facebook API behavior must be verified before activation.
Run `python manage.py invoice_audit` to read-only report invoice gaps for
unpaid orders with active or confirmed reservations.

## Make targets and tests

| Target | Action |
| --- | --- |
| `make up` | Start PostgreSQL and Redis and wait for health checks. |
| `make down` | Stop containers; it does not delete the database volume. |
| `make migrate` | Sync the active uv environment and apply migrations. |
| `make test` | Create/use pytest's isolated PostgreSQL test database, apply migrations, and run all tests. |
| `make lint` | Run Ruff checks and formatting. |
| `make run` | Run Django's local development server. |
| `make worker` | Run the Celery worker. |
| `make beat` | Run Celery beat; reservation expiry is scheduled every 60 seconds. |
| `make shell` | Open the Django shell. |
| `make simulate` | Run the repeatable four-commenter race scenario. |
| `make demo` | Reset marked demo data and run the short mining story. |
| `make demo-live` | Print the ordered three-terminal live-demo commands. |
| `make demo-worker` | Run the DEBUG-only worker with readable invoice messages. |
| `make demo-detector` | Tail live-demo mining outcomes read-only. |
| `make demo-live-feed` | Reset demo data and play the live comment timeline. |
| `make demo-live-continuous` | Run the comment-count-based sequential showcase. |

All list endpoints use page-number pagination: 25 rows by default, configurable
with `page_size` and capped at 100.

## Running the demo

Copy `.env.example` to `.env`, set `DJANGO_DEBUG=true`, and make sure Docker and
the workspace uv environment are available. Run:

```sh
make demo
```

The script resets only marked demo inventory and linked mining/order data, recreates
the three demo items, then processes three comments with fixed timestamps.
It displays the first reservation, backup outcomes, invoice, expiry, and backup
promotion. `DEMO_PAUSE_SECONDS` controls the pauses (default 1 second); use
`DEMO_PAUSE_SECONDS=0 make demo` to skip them. Demo reset refuses to run unless
Django `DEBUG` is enabled and will preserve/refuse removal of demo-linked payment
records.

## Live demo

Set `DJANGO_DEBUG=true` in `.env`, then open three terminals in the backend
directory. Arrange them as player (Terminal 1), detector (Terminal 2), and
worker (Terminal 3). Start the worker first, then detector, then player;
`make demo-live` prints the commands in that startup order:

1. **Terminal 3, worker:** `make demo-worker` — starts Celery with minimal
   console logs and INFO output for invoice messages.
2. **Terminal 2, detector:** `make demo-detector` — polls outcomes every 0.3
   seconds and displays colored results without modifying data.
3. **Terminal 1, player:** `make demo-live-feed` — resets the three demo items,
   then plays the roughly 72-second timeline from `scripts/demo_timeline.json`.

The player supports `--speed` (for example,
`make demo-live-feed ARGS="--speed 100"`), `--jitter` (default off; optionally
pass a maximum delay in seconds), `--seed` (default `0`), and `--no-reset`.
The detector accepts `--once` for a single poll. Normal comments receive their real send time. The timeline's one
late-arriving comment intentionally carries an earlier created time so the
detector can demonstrate `FLAGGED_LATE`. The player runs comments through the
same Celery task path as the simulator; defaults use the offline fake payment
provider and console sender. All demo commands refuse to run unless DEBUG is
enabled.

## Continuous sequential showcase

Run `make demo-live-continuous` for a continuous comment stream (three minutes
by default). It defaults to numeric item codes starting at 100, changes the
showcased item every 30 sent comments (including chatter), expires reservations
after 45 seconds, and uses seed 42. The first 50 demo items are kept active;
older items are recycled by archiving through the inventory service. Generated
inventory uses the `[DEMO] ` name marker, so reset can safely distinguish it
from unrelated numeric codes.

Available flags and defaults: `--start-code 100`, `--comments-per-item 30`,
`--max-items 50`, `--reservation-ttl-seconds 45`, `--seed 42`, `--speed 1`,
`--min-gap 1`, `--max-gap 3`, and `--duration 180` (seconds). `--no-reset`
preserves prior demo rows instead of starting from `demo_reset`.

Override options with `ARGS`, for example:

```sh
make demo-live-continuous ARGS="--start-code 500 --comments-per-item 20 --duration 300 --speed 1.5 --min-gap 0.5 --max-gap 2 --max-items 25 --reservation-ttl-seconds 60"
```

Every chatter line counts toward the showcase rotation. About 85% of mines
target the current code; occasional previous-item late comments and invalid
codes make up the remainder. Bursts of three or four current-code mines appear
every 8–15 seconds. The feed and detector share the current showcase through
`/tmp/easylv_demo_showcase.json`; the detector only reads that file. Both
commands require `DEBUG=True`.

The player pane prints the current item and progress, for example:

```text
===== [ CODE 100 is being showcased: Vintage denim jacket - PHP 350.00 ] =====
comment 1/30 for CODE 100
09:48:23  Ate May: mine 100  [RESERVED]  [showcasing 100]
```

The start and halfway progress lines are repeated as the item advances; after
the 30th comment, the feed prints a showcase-ended line before moving to the
next code.

## API endpoints

Protected API endpoints use SessionAuthentication and the listed server-side
action. `SUPER_ADMIN` can perform every registered action. Anonymous requests
to protected endpoints return HTTP 403 with the common JSON error envelope.
Sign in at `/admin/login/` in development.

| Method and path | Required action / access | Notes |
| --- | --- | --- |
| `GET /api/mining/comments/` | `mining.monitor` | Optional `live_video_id`, ISO `since`, and `outcome`; newest first. Returns normalized `message` and preserved `raw_message`. |
| `POST /api/mining/comments/simulate/` | `mining.confirm`, DEBUG only | Send `code` or `message`, `name`, and optional commenter/time/video fields. |
| `POST /api/mining/mines/{comment_id}/confirm/` | `mining.confirm` | Confirm an `IGNORED`/`INVALID` comment; optional `mining_code`. |
| `GET /api/reservations/` | `reservations.view` | Optional status, item code, and customer filters. |
| `PATCH /api/reservations/{id}/` | `reservations.manage` | Extend a deadline or confirm/cancel. |
| `POST /api/reservations/{id}/override/` | `reservations.override` | SUPER_ADMIN only; requires `new_customer_id` and nonblank `reason`. |
| `GET /api/inventory/items/` | `inventory.view` | Search `search`; filter by `availability_status` and `is_archived`. |
| `POST /api/inventory/items/` | `inventory.manage` | Create an item; optional initial `stock_qty`. |
| `GET /api/inventory/items/{id}/` | `inventory.view` | Item detail. |
| `PUT`, `PATCH /api/inventory/items/{id}/` | `inventory.manage` | Edit descriptive/pricing fields; stock and status fields are rejected. |
| `DELETE /api/inventory/items/{id}/` | `inventory.manage` | Soft-archive; never hard-deletes. |
| `POST /api/inventory/items/{id}/adjust-stock/` | `inventory.manage` | `quantity_delta` is a nonzero signed integer. |
| `GET /api/orders/` | `orders.view` | Optional status/customer filters; includes lines and invoice summary. |
| `POST /api/orders/{id}/send-invoice/` | `orders.send_invoice` | Returns 202 after invoice send; skipped orders return 409. |
| `GET /api/activity-log/` | `activitylog.view` | SUPER_ADMIN only; filter by action, target, user, and `created_from`/`created_to`. |
| `GET`, `POST /api/webhooks/facebook/` | Public; webhook signature/token | GET verifies the subscription token. POST requires `X-Hub-Signature-256`. |

The simulator requires an authenticated user. For manual command-line races use
`make simulate`; no login is needed for the management command.

## Full Docker profile

The default profile starts only PostgreSQL and Redis. To run web, worker, and
beat in Docker, first apply migrations and then start the full profile:

```sh
docker compose run --rm web uv run --frozen python manage.py migrate
docker compose --profile full up --build
```

Set host ports and secrets in `.env` as needed. `docker compose stop` or
`make down` preserves the PostgreSQL volume; do not use volume deletion for
routine cleanup.

## Local signed webhook request

Run the local server with a development-only `FB_APP_SECRET`. The signature
must be calculated over exactly the bytes submitted:

```sh
BODY='{"comments":[]}'
SIGNATURE=$(printf '%s' "$BODY" | openssl dgst -sha256 -hmac "$FB_APP_SECRET" | awk '{print $2}')
curl -X POST http://127.0.0.1:8000/api/webhooks/facebook/ \
  -H 'Content-Type: application/json' \
  -H "X-Hub-Signature-256: sha256=$SIGNATURE" \
  --data-binary "$BODY"
```

Webhook extraction currently accepts the documented canonical `comments`
array, not a verified Meta payload envelope. See `ASSUMPTIONS.md` before
connecting Facebook.

## Interactive chat demo

Needs `DJANGO_DEBUG=true`. Comments flow `demo-say` -> `demo-chat` ->
`demo-fetch` -> worker, handed over through Redis. Use four terminals in the
backend directory, started in this order:

1. `make demo-worker` handles invoice messages for reserved mines.
2. `make demo-fetch` is the detector. It sits idle until `demo-chat` sends it
   comments, then ignores or mines each one and prints the outcome in the
   `demo-detector` style (showcase banner, `RESERVED | item`, `BACKUP #n`).
3. `make demo-chat` resets the demo data and streams the live comments (a
   two-state Markov chain, about 90% chatter and 10% `mine <code>` claims, plus
   what you type) as plain `time  Name: message` lines, with your name in
   yellow. The showcased item rotates every 30 comments. Options via `ARGS`:
   `--speed`, `--start-code`, `--comments-per-item`, `--seed`, `--min-gap`,
   `--max-gap`, `--duration`, `--no-reset`.
4. `make demo-say` prompts for your comments (e.g. `mine 100`) and sends them to
   the chat, so your typing never mixes with the feeds. `ARGS="--name Juan"`
   changes your name.

`make demo-reset` clears the marked demo data without starting a feed.
