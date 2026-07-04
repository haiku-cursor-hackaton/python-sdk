"""Platform client — the merchant → platform (infra) callback channel.

When a UCP checkout is completed, the platform/gateway injects a payment
instrument referencing a **payment authorization** it created on its simulated
wallet. Before placing the order the merchant should *verify* that authorization
against the platform, and after the order is created it should *accredit*
(capture/settle) it so the platform credits the merchant's balance.

All of these calls are authenticated with a **merchant API key** issued by the
platform to the store. The key is sent as ``Authorization: Bearer <api_key>``.

This module ships a stdlib-only :class:`HttpPlatformClient` (no extra deps) plus
an abstract :class:`PlatformClient` so the client can be stubbed in tests or
swapped for an ``httpx``/async implementation.

HTTP contract the platform (infra) must expose (paths are configurable):

    GET  {base}/v1/payment-authorizations/{id}
        -> 200 { "id", "status", "amount_minor", "currency", "checkout_id"?, "merchant_id"? }

    POST {base}/v1/payment-authorizations/{id}/accredit
        body { "order_id", "amount_minor", "currency" }
        -> 200 { "status": "completed", "transaction_id"? }

    POST {base}/v1/payment-authorizations/{id}/release   (best-effort on failure)
        body { "reason"? }
        -> 200

Authorization ``status`` values follow the platform PRD:
``created | reserved | submitted | completed | released | reconciliation_required``.
An authorization is usable for capture while in a pre-capture state
(``created | reserved | submitted``).
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

# States in which an authorization may still be captured/accredited.
PRE_CAPTURE_STATES = frozenset({"created", "reserved", "submitted", "authorized"})


class PlatformError(Exception):
    """A recoverable/technical failure talking to the platform.

    ``code`` maps to a UCP message code surfaced back to the agent (e.g.
    ``payment_authorization_invalid``, ``payment_amount_mismatch``,
    ``upstream_unreachable``).
    """

    def __init__(self, code: str, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.status = status


@dataclass
class PaymentAuthorization:
    id: str
    status: str | None = None
    amount_minor: int | None = None
    currency: str | None = None
    checkout_id: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


class PlatformClient(ABC):
    """Interface the SDK uses to verify + accredit simulated payments."""

    @abstractmethod
    def get_authorization(self, authorization_id: str) -> PaymentAuthorization:
        """Fetch the current state of a payment authorization."""

    @abstractmethod
    def accredit(
        self,
        authorization_id: str,
        *,
        order_id: str,
        amount_minor: int,
        currency: str,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Capture/settle the authorization now that the order exists."""

    def release(self, authorization_id: str, *, reason: str | None = None) -> None:
        """Best-effort release of a reserved authorization. Default: no-op."""
        return None


def _request(
    method: str,
    url: str,
    api_key: str,
    *,
    body: dict | None = None,
    timeout: float = 30.0,
    extra_headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Authorization", f"Bearer {api_key}")
    request.add_header("Accept", "application/json")
    if data is not None:
        request.add_header("Content-Type", "application/json")
    for key, value in (extra_headers or {}).items():
        request.add_header(key, value)

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read()
    except urllib.error.HTTPError as error:  # non-2xx
        detail = ""
        try:
            detail = error.read().decode("utf-8", "ignore")[:200]
        except Exception:  # noqa: BLE001
            pass
        raise PlatformError(
            "upstream_response_invalid",
            f"Platform returned HTTP {error.code}. {detail}".strip(),
            status=error.code,
        ) from error
    except urllib.error.URLError as error:
        raise PlatformError("upstream_unreachable", f"Platform unreachable: {error.reason}") from error
    except TimeoutError as error:
        raise PlatformError("upstream_timeout", "Platform request timed out.") from error

    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError as error:
        raise PlatformError("upstream_response_invalid", "Platform returned non-JSON body.") from error


class HttpPlatformClient(PlatformClient):
    """Default stdlib (``urllib``) platform client authenticated by API key."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        timeout: float = 30.0,
        authorization_path: str = "/v1/payment-authorizations/{id}",
        accredit_path: str = "/v1/payment-authorizations/{id}/accredit",
        release_path: str = "/v1/payment-authorizations/{id}/release",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.authorization_path = authorization_path
        self.accredit_path = accredit_path
        self.release_path = release_path

    def _url(self, template: str, authorization_id: str) -> str:
        return self.base_url + template.replace("{id}", urllib.parse.quote(authorization_id, safe=""))

    def get_authorization(self, authorization_id: str) -> PaymentAuthorization:
        data = _request(
            "GET", self._url(self.authorization_path, authorization_id), self.api_key, timeout=self.timeout
        )
        return PaymentAuthorization(
            id=str(data.get("id", authorization_id)),
            status=data.get("status"),
            amount_minor=data.get("amount_minor"),
            currency=data.get("currency"),
            checkout_id=data.get("checkout_id"),
            raw=data,
        )

    def accredit(
        self,
        authorization_id: str,
        *,
        order_id: str,
        amount_minor: int,
        currency: str,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        headers = {"Idempotency-Key": idempotency_key} if idempotency_key else None
        return _request(
            "POST",
            self._url(self.accredit_path, authorization_id),
            self.api_key,
            body={"order_id": order_id, "amount_minor": amount_minor, "currency": currency},
            timeout=self.timeout,
            extra_headers=headers,
        )

    def release(self, authorization_id: str, *, reason: str | None = None) -> None:
        _request(
            "POST",
            self._url(self.release_path, authorization_id),
            self.api_key,
            body={"reason": reason} if reason else {},
            timeout=self.timeout,
        )


def verify_authorization(
    client: PlatformClient,
    authorization_id: str,
    *,
    expected_amount_minor: int | None,
    currency: str | None,
) -> PaymentAuthorization:
    """Fetch + validate an authorization before order placement.

    Raises :class:`PlatformError` (with a specific ``code``) when the
    authorization is in a non-capturable state, or when the amount/currency does
    not match the authoritative order total.
    """
    auth = client.get_authorization(authorization_id)

    if auth.status and auth.status.lower() not in PRE_CAPTURE_STATES:
        raise PlatformError(
            "payment_authorization_invalid",
            f"Authorization '{authorization_id}' is '{auth.status}', not usable for capture.",
        )
    if (
        auth.amount_minor is not None
        and expected_amount_minor is not None
        and int(auth.amount_minor) != int(expected_amount_minor)
    ):
        raise PlatformError(
            "payment_amount_mismatch",
            f"Authorization amount {auth.amount_minor} != order total {expected_amount_minor}.",
        )
    if auth.currency and currency and auth.currency.upper() != currency.upper():
        raise PlatformError(
            "payment_currency_mismatch",
            f"Authorization currency {auth.currency} != order currency {currency}.",
        )
    return auth
