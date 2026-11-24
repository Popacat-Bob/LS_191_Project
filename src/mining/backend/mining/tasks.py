"""Celery tasks for reservation lifecycle processing."""

from celery import shared_task

from mining.expiry import expire_due_reservations


@shared_task(name="mining.expire_reservations")
def expire_reservations() -> dict[str, int]:
    """Expire due reservations independently and promote eligible backups."""
    return expire_due_reservations()
