# EasyLV alpha test guide: the mining feature

**Mining** is how a buyer claims an item during a Facebook Live: they comment
"mine 101" and the system decides what that comment is.

| Outcome | Meaning |
| --- | --- |
| `RESERVED` | First valid claim. The item is held for them, an order is created, and an invoice message is sent. |
| `BACKUP #n` | The item is already taken. They wait in line, ranked by original comment time. |
| `FLAGGED_LATE` | Their comment was written *earlier* than the current holder's but arrived later. The holder keeps the item and this person is queued as a backup. |
| `IGNORED` | Not a claim ("hm po?"), or a repeat claim from someone who already holds or waits for the item. |
| `INVALID` | Looks like a claim but the code matches no available item. |

When a holder's reservation expires or is cancelled, the first backup is
promoted automatically and gets their own order and invoice.

This alpha is **fully offline**: the comments are simulated, payment links are
fake, and invoice messages are printed in a terminal. Nothing contacts Facebook
or PayMongo. You don't need to sign in or create any accounts: everything below
runs from the terminal.

Mark each checkbox as you go. Report problems with the template at the end.

---

## 1. Setup (about 10 minutes)

You need Python 3.13, `uv`, and Docker Desktop (running). Open a terminal in the
`backend` folder, with the project's virtual environment activated.

```sh
cp .env.example .env        # DJANGO_DEBUG=true is already set in the example
make up                     # starts PostgreSQL and Redis
make migrate
```

All the demo commands need `DJANGO_DEBUG=true` and refuse to run without it.

For the commands in sections 3 and 4, load the environment in that terminal
first:

```sh
set -a; . ./.env; set +a
```

---

## 2. Live demo

A simulated live comment section with a detector and a worker.

Open **four terminals** in the `backend` folder and start them in this order:

| # | Command | What it is |
| --- | --- | --- |
| 1 | `make demo-worker` | Handles invoices. Prints each invoice message that is "sent". |
| 2 | `make demo-fetch` | The detector. Idle until chat sends it comments. Decides each comment's outcome and prints it. |
| 3 | `make demo-chat` | The live comment stream. Resets demo data, shows the comments, relays them to the detector. |
| 4 | `make demo-say` | Your own typing. Sends your comment into the chat. |

Tips: `make demo-chat ARGS="--speed 3"` makes it three times faster. Cmd+K clears
a terminal. Ctrl+C stops anything. `make demo-reset` wipes the demo data
(restarting `demo-chat` does too).

### What you should see

- **demo-chat:** a banner such as
  `CODE 100 is being showcased: Vintage denim jacket - PHP 350.00`, then lines
  `time  Name: message`. Your name is yellow. The item changes every 30
  comments.
- **demo-fetch:** the same comments, each with its outcome (`IGNORED`,
  `INVALID`, `RESERVED | item`, `BACKUP #n | item`, `FLAGGED_LATE`).
- **demo-worker:** an invoice message block each time someone gets a
  reservation.
- **demo-say:** a prompt like `[mine 100] > ` showing the code on showcase.

### Test cases

| # | Do this | Expected | Pass |
| --- | --- | --- | --- |
| D1 | Start the four terminals in order and watch for about a minute. | Chat shows comments. Fetch mirrors them with outcomes. Most (about 90%) are chatter shown as `IGNORED`. | [ ] |
| D2 | Before starting `demo-chat`, look at `demo-fetch`. | It only says it is waiting. Nothing appears. | [ ] |
| D3 | In `demo-say`, type `mine 100` (use the code on the banner). | Chat shows your line in yellow. Fetch shows it as `RESERVED` (or `BACKUP #n` if a bot got it first), ending with `<- you`. | [ ] |
| D4 | If you got `RESERVED`, check the worker terminal. | An invoice with the item, price, total, a fake payment link, and a deadline appears. | [ ] |
| D5 | Type the same `mine <code>` again. | `IGNORED` (you already hold or wait for it). | [ ] |
| D6 | Type `mine 999`. | `INVALID` (no such item). | [ ] |
| D7 | Type `hello po, hm?`. | `IGNORED`. | [ ] |
| D8 | On a fresh item, try the code written other ways: `MINE101`, `101`, `mine   101` (extra spaces). | Each is read as a claim for that code (only the first can win; repeats from you are `IGNORED`). | [ ] |
| D9 | Let the item rotate (30 comments, or restart with `--comments-per-item 5`). | A "showcase ended" line, then a new banner and the next code. The `demo-say` prompt shows the new code. | [ ] |
| D10 | Restart `demo-chat` with `ARGS="--speed 5"`. | Comments arrive about five times faster. Fetch keeps up. | [ ] |
| D11 | Stop `demo-chat` and watch `demo-fetch`. | Fetch goes quiet again; nothing is repeated. | [ ] |

Note: some one-word chatter such as `dibs` or `following` is read as an item code
and shows `INVALID` rather than `IGNORED`. That is expected.

---

## 3. Claim rules (scripted scenarios)

The bots in the demo are random, so use these commands to check the rules
exactly. Run them in a terminal where you loaded the environment (section 1).

### Scenario A: first claim wins, the rest are backups

```sh
make simulate
```

Four people comment on one item at almost the same time.

| # | Expected | Pass |
| --- | --- | --- |
| A1 | Exactly one `RESERVED`; the other three are `BACKUP`. | [ ] |
| A2 | `Order total` equals the item price (100.00). | [ ] |
| A3 | Running `make simulate` again also gives exactly one winner. | [ ] |

### Scenario B: a late-arriving earlier comment is flagged

```sh
uv run --active python manage.py simulate_comment --code=ALPHA-1 --user=Juan --comment-id=b1 --ts=2026-11-11T09:00:05+08:00
uv run --active python manage.py simulate_comment --code=ALPHA-1 --user=Maria --comment-id=b2 --ts=2026-11-11T09:00:00+08:00 --no-prepare
```

| # | Expected | Pass |
| --- | --- | --- |
| B0 | To repeat it, use a new item code (e.g. `ALPHA-2`) and new `--comment-id` values. Reusing them gives different results. | [ ] |
| B1 | Juan: `RESERVED`. Maria: `FLAGGED_LATE`. Juan keeps the item. | [ ] |
| B2 | Maria is queued as a backup ahead of anyone who commented after 09:00:00. | [ ] |

### Scenario C: the scripted story

```sh
make demo
```

Resets demo data, runs three comments, sends an invoice, expires the first
reservation, and promotes the first backup. Use `DEMO_PAUSE_SECONDS=0 make demo`
to skip the pauses.

| # | Expected | Pass |
| --- | --- | --- |
| C1 | First commenter `RESERVED`, the next two `BACKUP`. | [ ] |
| C2 | An invoice is created and sent for the first buyer. | [ ] |
| C3 | After expiry, the first backup gets an active reservation and their own invoice. | [ ] |

### Scenario D: expiry and promotion by hand

After Scenario B (Juan holds `ALPHA-1`, Maria is waiting), open `make shell` and
run:

```python
from datetime import timedelta
from django.utils import timezone
from mining.expiry import expire_due_reservations
from mining.models import Reservation
r = Reservation.objects.get(item__mining_code="ALPHA-1", status="ACTIVE")
r.payment_deadline = timezone.now() - timedelta(minutes=1)
r.save(update_fields=["payment_deadline"])
print(expire_due_reservations())
```

| # | Expected | Pass |
| --- | --- | --- |
| E1 | It prints `{'candidates': 1, 'expired': 1, 'errors': 0}`. Juan's reservation is now `EXPIRED`. | [ ] |
| E2 | Maria now has an `ACTIVE` reservation for `ALPHA-1` (check with `Reservation.objects.filter(item__mining_code="ALPHA-1").values("customer__full_name", "status")`). | [ ] |
| E3 | Running `expire_due_reservations()` again does nothing (`'expired': 0`). | [ ] |

---

## 4. Adding more test cases

Pick whichever fits what you want to check.

### a) More comment variety (live demo)

Add lines to `mining/demo_comments.txt`, one comment per line. The chat picks
randomly from this pool for the non-claim 90% of the stream. Restart
`demo-chat` to load them. Avoid lines that read as claims for a code on
showcase (for example `mine 100`); an automated test guards against that.

### b) A scripted live timeline

Edit `scripts/demo_timeline.json`. Each entry is one comment:

```json
{ "at_seconds": 12.0, "name": "Ana", "message": "mine 102" }
```

- `at_seconds`: when it is sent (entries must be in time order).
- `name` and `message`: who and what. A bare 3-digit code like `mine 101`
  means the demo item `DEMO-101` (items 101, 102, 103 exist, one unit each).
- `created_time_seconds_before_send` (optional): makes the comment *claim* to
  have been written earlier. Use it to create `FLAGGED_LATE` cases.

Then run, in three terminals (worker first): `make demo-worker`,
`make demo-detector`, and `make demo-live-feed` (add
`ARGS="--speed 100"` to play it fast).

### c) A one-off comment with a chosen time

```sh
uv run --active python manage.py simulate_comment --code=<item code> --user=<name> --comment-id=<unique id> --ts=<ISO time>
```

- `--ts` is the time the comment was *written* (for example
  `2026-11-11T09:00:05+08:00`). Earlier times rank ahead.
- `--comment-id` must be unique; repeating one returns the earlier result
  (this is how duplicates are ignored).
- `--no-prepare` stops the command from creating or restocking the item. Use it
  on every call after the first for the same item.
- The commenter is identified by `--user`, so use different names for different
  buyers.

### d) A new automated test

Automated tests live in `tests/mining/`. Copy this into a new file such as
`tests/mining/test_alpha_cases.py` and change it:

```python
import pytest
from django.utils import timezone

from mining.models import LiveComment, Reservation
from mining.services import process_comment
from tests.factories import InventoryItemFactory

pytestmark = pytest.mark.django_db


def _comment(comment_id, buyer, message="mine 101"):
    return {
        "fb_comment_id": comment_id,
        "live_video_id": "alpha",
        "message": message,
        "fb_commenter_id": buyer,
        "fb_commenter_name": buyer,
        "created_time": timezone.now(),
    }


def test_second_buyer_is_a_backup():
    InventoryItemFactory(mining_code="101", stock_qty=1)

    first = process_comment(_comment("c1", "buyer-1"))
    second = process_comment(_comment("c2", "buyer-2"))

    assert isinstance(first, Reservation)
    assert second.outcome == LiveComment.Outcome.BACKUP
```

A reserved comment returns a `Reservation`; every other result has an
`.outcome`. See `tests/mining/test_services.py` for more examples (late
arrivals, duplicates, ranking). Run your test with:

```sh
set -a; . ./.env; set +a; uv run --active pytest tests/mining/test_alpha_cases.py -q
```

and the whole suite with `make test`.

### e) A different claim format

The pattern that decides what counts as a claim is `MINING_PATTERN` in `.env`
(the code must be in the first capture group). Change it, restart the demo
commands, and check that your new wording is read as a claim and the old
wording behaves as you expect.

### Recording your new case in this guide

Add a row to the table of the section it belongs to (or start a new section)
with a number, the exact steps, the expected result, and a checkbox:

```
| X1 | Type `mine 101 po` in demo-say. | `IGNORED` (extra word, not a claim). | [ ] |
```

---

## 5. Known limitations (please don't report these)

- No front end and no real Facebook connection. Comments are simulated.
- Payment links are fake and there is no payment verification yet, so orders are
  never marked paid automatically.
- Demo commands work only with `DJANGO_DEBUG=true`.
- Single-word chatter like `dibs` can show as `INVALID`.
- Typing in the `demo-chat` terminal does nothing useful; use `demo-say`.
- `make simulate` and the scripted scenarios leave test items (`ALPHA-1`,
  `ALPHA-RACE`, ...) in the database. `make demo-reset` does not remove them.

---

## 6. Reporting a bug

Please include:

```
Title:
Test case number (e.g. D3, B1):
Steps (what you did, in order):
Expected:
What actually happened:
Terminal output or error text (copy/paste, or a screenshot):
Command and options used (e.g. make demo-chat ARGS="--speed 3"):
Date/time:
```

## 7. Sign-off

| Tester | Date | Sections done | Blocking bugs found | Overall (pass / fail) |
| --- | --- | --- | --- | --- |
| | | | | |
