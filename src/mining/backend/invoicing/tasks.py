"""Celery task entry point for invoice delivery."""

import logging

from celery import Task, shared_task
from django.db.utils import InterfaceError, OperationalError

from invoicing.providers import PaymentProviderError
from invoicing.senders import MessageSendError
from invoicing.services import send_invoice as send_invoice_service

logger = logging.getLogger("invoicing")

_RETRYABLE_EXCEPTIONS = (
    PaymentProviderError,
    MessageSendError,
    OperationalError,
    InterfaceError,
)


@shared_task(
    bind=True,
    autoretry_for=_RETRYABLE_EXCEPTIONS,
    retry_backoff=True,
    retry_jitter=True,
    max_retries=5,
    acks_late=True,
    reject_on_worker_lost=True,
)
def send_invoice(self: Task, order_id: int) -> dict[str, int | str] | None:
    """Deliver an invoice, retrying provider, sender, and transient DB failures."""
    logger.info("task start order_id=%s", order_id)
    try:
        invoice = send_invoice_service(order_id)
    except Exception as exc:
        max_retries = self.max_retries or 0
        if (
            isinstance(exc, _RETRYABLE_EXCEPTIONS)
            and self.request.retries < max_retries
        ):
            logger.warning(
                "retry order_id=%s retry=%s/%s reason=%s",
                order_id,
                self.request.retries + 1,
                max_retries,
                exc,
            )
        else:
            logger.exception(
                "final failure order_id=%s retries=%s reason=%s",
                order_id,
                self.request.retries,
                exc,
            )
        raise
    if invoice is None:
        return None
    return {
        "invoice_id": invoice.pk,
        "order_id": invoice.order_id,
        "status": invoice.status,
    }


@shared_task(name="invoicing.reconcile_unpaid_invoices")
def reconcile_unpaid_invoices() -> dict[str, int]:
    """Enqueue invoice tasks for mature gaps in unpaid orders."""
    from invoicing.reconciliation import reconcile_unpaid_invoice_gaps

    return reconcile_unpaid_invoice_gaps()
