"""Offline and deferred payment-link providers."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from urllib.parse import quote

import requests
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


class PaymentProviderError(Exception):
    """Raised when a payment provider cannot create a payment link."""


@dataclass(frozen=True)
class PaymentLink:
    """A provider-hosted payment link and its provider identifier."""

    link_id: str
    url: str


class PaymentProvider:
    """Interface implemented by payment-link providers."""

    def create_payment_link(
        self, *, amount: Decimal, description: str, reference: str
    ) -> PaymentLink:
        """Create a payment link for an exact decimal amount."""
        raise NotImplementedError


class FakePaymentProvider(PaymentProvider):
    """Return a deterministic link without performing network I/O."""

    def create_payment_link(
        self, *, amount: Decimal, description: str, reference: str
    ) -> PaymentLink:
        """Build a stable offline link for the supplied reference."""
        del amount, description
        safe_reference = quote(reference, safe="")
        return PaymentLink(
            link_id=f"fake-{safe_reference}",
            url=f"https://fake-pay.local/links/{safe_reference}",
        )


class PayMongoProvider(PaymentProvider):
    """Create links through PayMongo's Links API.

    TODO(verify): Confirm the endpoint, request field names, response shape,
    reference field, and amount semantics against current provider documentation.
    """

    endpoint = "https://api.paymongo.com/v1/links"
    request_timeout = 10

    def __init__(self) -> None:
        self.secret_key = settings.PAYMONGO_SECRET_KEY
        if not self.secret_key:
            raise ImproperlyConfigured(
                "PAYMONGO_SECRET_KEY is required for PayMongoProvider."
            )

    def create_payment_link(
        self, *, amount: Decimal, description: str, reference: str
    ) -> PaymentLink:
        """Create a PayMongo link using integer centavos and basic auth."""
        centavos = int(
            (
                amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) * 100
            ).to_integral_exact()
        )
        try:
            response = requests.post(
                self.endpoint,
                json={
                    "data": {
                        "attributes": {
                            "amount": centavos,
                            "description": description,
                            "reference_number": reference,
                        }
                    }
                },
                auth=(self.secret_key, ""),
                timeout=self.request_timeout,
            )
            response.raise_for_status()
            payload = response.json()
            attributes = payload["data"]["attributes"]
            link_id = payload["data"]["id"]
            url = attributes["checkout_url"]
            if not isinstance(link_id, str) or not link_id:
                raise ValueError("Provider response has no link id.")
            if not isinstance(url, str) or not url:
                raise ValueError("Provider response has no checkout URL.")
            return PaymentLink(
                link_id=link_id,
                url=url,
            )
        except (
            requests.RequestException,
            KeyError,
            TypeError,
            ValueError,
        ) as exc:
            raise PaymentProviderError(
                "PayMongo could not create a payment link."
            ) from exc


def get_payment_provider() -> PaymentProvider:
    """Build the provider selected by PAYMENT_PROVIDER."""
    provider_name = settings.PAYMENT_PROVIDER.strip().lower()
    if provider_name == "fake":
        return FakePaymentProvider()
    if provider_name == "paymongo":
        return PayMongoProvider()
    raise ImproperlyConfigured(f"Unknown PAYMENT_PROVIDER: {provider_name}.")
