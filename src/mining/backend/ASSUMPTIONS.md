# Assumptions and operational decisions

## API and access

- API lists use page-number pagination with `page_size=25` by default and a
  hard maximum of 100.
- Protected API endpoints use DRF SessionAuthentication. Development staff log
  in at Django's `/admin/login/`; the resulting session can call the API.
- API exceptions use the `{"error": {"code", "detail", "fields?"}}` envelope.
  Reservation/inventory conflicts map to 409, invalid input to 400, and missing
  domain objects to 404. Webhook signature verification remains the endpoint's
  authentication mechanism.
- Activity log reads are SUPER_ADMIN-only. It is an audit record, never a
  general-purpose change history users may edit.
- Inventory creation may set initial stock; subsequent stock changes must use
  the dedicated adjust-stock action. Item deletion is soft archival.
- `LiveComment.message` stores normalized text; `raw_message` retains the exact
  incoming text. Existing comments receive the migration default empty string:
  historical raw text cannot be reconstructed.
- The order invoice endpoint calls the invoice service synchronously and
  returns 202 only after the send service finishes. An ineligible order returns
  409 with its skip reason.

## Mining and reservation lifecycle

- One mining comment reserves quantity one. Candidates use original
  `(created_time, fb_comment_id)` order; a late-arriving earlier comment is
  FLAGGED_LATE and does not displace an existing holder.
- FLAGGED_LATE comments are queued as WAITING backups without changing the
  comment outcome. A short settling delay may reduce webhook-jitter effects.
- Inventory code uniqueness is partial across non-archived, non-SOLD items.
  Reservation uniqueness is also partial: a customer may have at most one
  ACTIVE or CONFIRMED reservation for an item.
- An administrator who wants to move an item to a customer who already holds
  it must cancel or confirm instead of overriding. Override to an existing
  holder is rejected.
- Reservation lifecycle locks follow InventoryItem, Reservation rows in
  ascending id, customer rows in ascending id, then Order rows in ascending id.
  No enqueue/provider/sender calls run while these locks are held.
- Invoice tasks use late acknowledgement and reject on worker loss, so delivery
  is at-least-once: a worker crash after a successful send but before ack may
  cause a duplicate. The invoice service remains idempotent for a sent total.
  Redis visibility timeout defaults to 3600 seconds (or one hour beyond the
  configured invoice countdown, whichever is greater) and must exceed that
  countdown.
- Invoice reconciliation runs every 30 seconds by default (configurable with
  `INVOICE_RECONCILIATION_INTERVAL_SECONDS`). It waits a configurable 60-second
  grace period before resubmitting unchanged gaps, and records the enqueue
  signature so repeated runs during that period are no-ops.
- Invoice rows and order rows are locked only for short database reads/updates.
  Provider and sender calls run after those transactions release their locks;
  holding database locks across network calls would block reservation changes
  and increase deadlock and timeout risk. After sending, the service verifies
  that the order still has the sent total and enqueues a reissue when it changed.
- Invoice preparation locks only the order row even when loading its customer;
  reservation lifecycle transactions lock customers before orders, so a joined
  `SELECT FOR UPDATE` must not also lock the customer in reverse order.
- Expiry affects due ACTIVE reservations only, rechecks payment/order state
  under lock, and never expires CONFIRMED reservations. The future Payment
  Verifier must mark orders PAID before any later expiry decision.
- Reservation expiry retries PostgreSQL deadlock victims up to three times,
  with short backoff, in a fresh transaction. This handles transient lock
  conflicts without hiding non-deadlock failures; exhausted retries remain
  visible as per-reservation errors in the expiry task.
- Empty orders remain UNPAID at Decimal zero. Their SENT invoice becomes
  EXPIRED; invoices for orders with remaining items are refreshed after commit.
- Manual confirmation applies only to IGNORED/INVALID comments. A request code
  overrides a stored parse; FLAGGED_LATE/RESERVED/BACKUP require override.
- Override retains the original qty, deadline, source comment, and agreed unit
  price. Inventory reserved quantity is unchanged.

## Ingestion and invoice integrations

- The default comment source is a process-local simulated queue. Webhook
  extraction currently supports a canonical top-level `comments` array; actual
  Meta event envelopes and field names must be verified.
- Webhook idempotency is keyed by `fb-comment:<comment_id>`. Tasks process the
  canonical comment payload and mark a webhook event complete after success.
- `PAYMENT_PROVIDER=fake` and `MESSAGE_SENDER=console` are fully offline.
  PayMongo details remain verification-only. Invoice workers may rarely
  duplicate delivery under concurrent execution; delivery is at-least-once.
- Invoice deadlines use the earliest ACTIVE reservation deadline for items in
  the order, with the configured payment window as fallback.

## Plugging in the real Graph API later

Implement these behaviors behind `GraphApiCommentSource` and `GraphApiSender`;
do not bypass their interfaces or put provider logic in views:

- Configure the correct Facebook Page and Facebook App.
- Confirm Page/App ownership, access tokens, and required page permissions.
- Implement and verify the webhook subscription for live comments, including
  Meta's actual event envelope and field names.
- Implement polling fallback with a durable, stored cursor and idempotent
  ingestion through the existing task path.
- Decide whether invoice delivery uses a private reply to the source comment
  or a Messenger send, then implement the matching context/recipient mapping.
- Confirm applicable permissions, the 24-hour messaging window, and required
  Facebook App Review before enabling sending.

## Before going live

- Create and verify the PayMongo account; validate all provider endpoint,
  request, response, and link-expiry TODOs before selecting PayMongo.
- Implement the Payment Verifier webhook so verified payments create Payment
  records and transition orders to PAID before reservation expiry can proceed.
- Decide whether and how long to add a settling delay for FLAGGED_LATE.
- Serve only over HTTPS and store production secrets in a managed secret store.
- Set DEBUG off and configure production hosts, logging, and secure cookies.
- Establish and test a PostgreSQL backup and restore plan.
- Never edit or delete audit log rows by hand.

## Admin and audit scope

- Inventory stock counts/status, reservation status, and order totals are
  read-only in Django admin. ActivityLog and WebhookEvent admin deletion is
  disabled; ActivityLog add/change/delete is disabled.
- Other admin edits and `UserManager.create_user()` may still bypass domain
  activity logging. Use the API/services for audited operational changes.
