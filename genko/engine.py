"""Transport-agnostic checkout engine.

Owns the checkout session store and the status lifecycle
(``incomplete`` -> ``ready_for_complete`` -> ``completed`` / ``canceled``).
Both the REST and MCP transports call into this engine, so protocol behavior
stays consistent across surfaces.
"""

from __future__ import annotations

import base64
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .adapter import MerchantAdapter
from .catalog import product_to_catalog
from .platform import PlatformClient, PlatformError, verify_authorization
from .models import (
    Buyer,
    Checkout,
    ErrorResponse,
    Item,
    LineItem,
    LineItemRequest,
    Link,
    Message,
    OrderConfirmation,
    Payment,
    Total,
    UcpEnvelope,
)

DEFAULT_CATALOG_LIMIT = 10


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _rfc3339(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _encode_cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(str(offset).encode()).decode()


def _decode_cursor(cursor: str | None) -> int:
    if not cursor:
        return 0
    try:
        return max(0, int(base64.urlsafe_b64decode(cursor.encode()).decode()))
    except (ValueError, TypeError):
        return 0


@dataclass
class _Session:
    id: str
    line_items: list[LineItem]
    buyer: Buyer
    status: str
    created_at: datetime
    expires_at: datetime
    order: OrderConfirmation | None = None
    payment: Payment | None = None
    messages: list[Message] = field(default_factory=list)


class CheckoutEngine:
    """In-memory checkout session manager.

    For a hackathon / single-process deployment an in-memory store is fine.
    Swap :attr:`_sessions` for a shared store (Redis, DB) to scale horizontally.
    """

    def __init__(
        self,
        *,
        adapter: MerchantAdapter,
        currency: str,
        version: str,
        capabilities: dict,
        payment_handlers: dict,
        catalog_capabilities: dict | None = None,
        order_capabilities: dict | None = None,
        links: list[Link] | None = None,
        require_buyer_fields: tuple[str, ...] = ("email",),
        storefront_url: str | None = None,
        session_ttl_hours: int = 6,
        platform_client: PlatformClient | None = None,
    ) -> None:
        self.adapter = adapter
        self.currency = currency
        self.version = version
        self.capabilities = capabilities
        self.catalog_capabilities = catalog_capabilities or {}
        self.order_capabilities = order_capabilities or {}
        self.payment_handlers = payment_handlers
        self.links = links or []
        self.require_buyer_fields = require_buyer_fields
        self.storefront_url = storefront_url.rstrip("/") if storefront_url else None
        self.session_ttl = timedelta(hours=session_ttl_hours)
        self.platform_client = platform_client
        self._sessions: dict[str, _Session] = {}

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    def _envelope(self) -> UcpEnvelope:
        return UcpEnvelope(
            version=self.version,
            status="success",
            capabilities=self.capabilities,
            payment_handlers=self.payment_handlers,
        )

    def _error_envelope(self) -> UcpEnvelope:
        return UcpEnvelope(
            version=self.version,
            status="error",
            capabilities={},
            payment_handlers=self.payment_handlers,
        )

    def catalog_envelope(self, status: str = "success") -> dict:
        """UCP envelope for catalog responses.

        Per the spec's "Response Capability Selection", a catalog response only
        advertises the catalog capabilities (search + lookup) and omits payment
        handlers (they are not relevant to discovery).
        """
        return {
            "version": self.version,
            "status": status,
            "capabilities": self.catalog_capabilities,
        }

    def order_envelope(self, status: str = "success") -> dict:
        """UCP envelope for order responses (only the order capability)."""
        return {
            "version": self.version,
            "status": status,
            "capabilities": self.order_capabilities,
        }

    def _continue_url(self, session_id: str | None = None) -> str | None:
        if not self.storefront_url:
            return None
        if session_id:
            return f"{self.storefront_url}/checkout/{session_id}"
        return self.storefront_url

    def _error(self, code: str, content: str, severity: str = "unrecoverable") -> ErrorResponse:
        return ErrorResponse(
            ucp=self._error_envelope(),
            messages=[Message(type="error", code=code, content=content, severity=severity)],
            continue_url=self._continue_url(),
        )

    @staticmethod
    def _coerce_line_items(requested) -> list[LineItemRequest]:
        return [
            item if isinstance(item, LineItemRequest) else LineItemRequest.model_validate(item)
            for item in (requested or [])
        ]

    @staticmethod
    def _coerce_buyer(buyer) -> Buyer | None:
        if buyer is None:
            return None
        return buyer if isinstance(buyer, Buyer) else Buyer.model_validate(buyer)

    def _resolve_line_items(
        self, requested: list[LineItemRequest]
    ) -> tuple[list[LineItem], list[Message]]:
        resolved: list[LineItem] = []
        messages: list[Message] = []
        for index, req in enumerate(requested):
            product = self.adapter.get_product(req.item.id)
            if product is None:
                messages.append(
                    Message(
                        type="error",
                        code="item_unavailable",
                        path=f"$.line_items[{index}]",
                        content=f"Item '{req.item.id}' is not available.",
                        severity="unrecoverable",
                    )
                )
                continue
            if not product.available:
                messages.append(
                    Message(
                        type="error",
                        code="out_of_stock",
                        path=f"$.line_items[{index}]",
                        content=f"'{product.title}' is currently out of stock.",
                        severity="recoverable",
                    )
                )
                continue
            resolved.append(
                LineItem(
                    id=f"li_{index + 1}",
                    item=product.to_item(),
                    quantity=req.quantity,
                    totals=[
                        Total(
                            type="subtotal",
                            display_text="Subtotal",
                            amount=product.price * req.quantity,
                        )
                    ],
                )
            )
        return resolved, messages

    def _missing_buyer_messages(self, buyer: Buyer) -> list[Message]:
        messages: list[Message] = []
        for field_name in self.require_buyer_fields:
            if not getattr(buyer, field_name, None):
                messages.append(
                    Message(
                        type="error",
                        code="field_required",
                        path=f"$.buyer.{field_name}",
                        content=f"Buyer {field_name.replace('_', ' ')} is required.",
                        severity="requires_buyer_input",
                    )
                )
        return messages

    def _recompute(self, session: _Session) -> None:
        """Recompute totals, messages and status for a session."""
        messages: list[Message] = []
        if not session.line_items:
            messages.append(
                Message(
                    type="error",
                    code="field_required",
                    path="$.line_items",
                    content="At least one line item is required.",
                    severity="requires_buyer_input",
                )
            )
        messages.extend(self._missing_buyer_messages(session.buyer))
        session.messages = messages

        has_input_error = any(m.severity == "requires_buyer_input" for m in messages)
        if has_input_error:
            session.status = "incomplete"
        else:
            session.status = "ready_for_complete"

    def _to_checkout(self, session: _Session) -> Checkout:
        totals = self.adapter.price(session.line_items) if session.line_items else []
        continue_url = None
        if session.status not in {"completed", "canceled"}:
            continue_url = self._continue_url(session.id)
        return Checkout(
            ucp=self._envelope(),
            id=session.id,
            status=session.status,
            currency=self.currency,
            line_items=session.line_items,
            totals=totals,
            buyer=session.buyer,
            messages=session.messages,
            links=self.links,
            expires_at=_rfc3339(session.expires_at),
            continue_url=continue_url,
            payment=session.payment,
            order=session.order,
        )

    # ------------------------------------------------------------------ #
    # Catalog operations
    # ------------------------------------------------------------------ #
    @staticmethod
    def _matches_query(product, tokens: list[str]) -> bool:
        """Match a search query against title, id, description and attributes.

        All whitespace-separated tokens must appear (AND semantics) somewhere in
        the product's searchable text. URL-like attribute values are skipped so
        storefront links don't create spurious matches.
        """
        parts = [product.title, product.id, product.description or ""]
        for value in (product.attributes or {}).values():
            if isinstance(value, str) and not value.startswith(("http://", "https://", "/")):
                parts.append(value)
        haystack = " ".join(parts).lower()
        return all(token in haystack for token in tokens)

    @staticmethod
    def _apply_catalog_filters(products, filters: dict | None):
        filters = filters or {}

        categories = filters.get("categories")
        if categories:
            wanted = {str(c).lower() for c in categories}
            products = [
                p
                for p in products
                if str(p.attributes.get("category", "")).lower() in wanted
            ]

        price = filters.get("price") or {}
        minimum = price.get("min")
        maximum = price.get("max")
        if minimum is not None:
            products = [p for p in products if p.price >= minimum]
        if maximum is not None:
            products = [p for p in products if p.price <= maximum]

        return products

    def search_catalog(
        self,
        query: str | None = None,
        filters: dict | None = None,
        pagination: dict | None = None,
    ) -> dict:
        """Catalog Search: case-insensitive keyword match + filters + paging."""
        products = self.adapter.get_products()

        if query:
            tokens = [t for t in query.strip().lower().split() if t]
            products = [p for p in products if self._matches_query(p, tokens)]

        products = self._apply_catalog_filters(products, filters)

        pagination = pagination or {}
        limit = pagination.get("limit") or DEFAULT_CATALOG_LIMIT
        offset = _decode_cursor(pagination.get("cursor"))
        total = len(products)
        page = products[offset : offset + limit]

        next_offset = offset + limit
        has_next_page = next_offset < total
        page_info: dict = {"has_next_page": has_next_page, "total_count": total}
        if has_next_page:
            page_info["cursor"] = _encode_cursor(next_offset)

        return {
            "ucp": self.catalog_envelope(),
            "products": [product_to_catalog(p) for p in page],
            "pagination": page_info,
        }

    def lookup_catalog(self, ids: list[str], filters: dict | None = None) -> dict:
        """Catalog Lookup: resolve a set of ids, reporting misses as info messages."""
        products: list[dict] = []
        messages: list[dict] = []
        for requested_id in ids:
            product = self.adapter.get_product(requested_id)
            if product is None:
                messages.append(
                    {"type": "info", "code": "not_found", "content": requested_id}
                )
                continue
            products.append(
                product_to_catalog(
                    product, inputs=[{"id": requested_id, "match": "exact"}]
                )
            )

        result: dict = {"ucp": self.catalog_envelope(), "products": products}
        if messages:
            result["messages"] = messages
        return result

    def get_product_detail(
        self,
        product_id: str,
        selected: dict | None = None,
        preferences: dict | None = None,
    ) -> dict:
        """Catalog Product detail: singular ``product`` on success, error envelope otherwise."""
        product = self.adapter.get_product(product_id)
        if product is None:
            return {
                "ucp": self.catalog_envelope(status="error"),
                "messages": [
                    {
                        "type": "error",
                        "code": "not_found",
                        "content": f"Product not found: {product_id}",
                        "severity": "unrecoverable",
                    }
                ],
            }
        return {
            "ucp": self.catalog_envelope(),
            "product": product_to_catalog(product),
            "messages": [],
        }

    # ------------------------------------------------------------------ #
    # Order operations (optional capability)
    # ------------------------------------------------------------------ #
    def get_order_detail(self, order_id: str) -> dict:
        """Resolve a previously created order via ``adapter.get_order``."""
        confirmation = self.adapter.get_order(order_id)
        if confirmation is None:
            return {
                "ucp": self.order_envelope(status="error"),
                "messages": [
                    {
                        "type": "error",
                        "code": "not_found",
                        "content": f"Order not found: {order_id}",
                        "severity": "unrecoverable",
                    }
                ],
            }
        return {
            "ucp": self.order_envelope(),
            "order": confirmation.model_dump(exclude_none=True),
        }

    # ------------------------------------------------------------------ #
    # Operations
    # ------------------------------------------------------------------ #
    def create_checkout(
        self,
        *,
        line_items: list[LineItemRequest],
        buyer: Buyer | None = None,
    ) -> Checkout | ErrorResponse:
        line_items = self._coerce_line_items(line_items)
        buyer = self._coerce_buyer(buyer)
        resolved, resolution_messages = self._resolve_line_items(line_items)
        if not resolved:
            return ErrorResponse(
                ucp=self._error_envelope(),
                messages=resolution_messages
                or [
                    Message(
                        type="error",
                        code="item_unavailable",
                        content="No purchasable items in request.",
                        severity="unrecoverable",
                    )
                ],
                continue_url=self._continue_url(),
            )

        session = _Session(
            id=f"chk_{secrets.token_hex(12)}",
            line_items=resolved,
            buyer=buyer or Buyer(),
            status="incomplete",
            created_at=_now(),
            expires_at=_now() + self.session_ttl,
        )
        self._recompute(session)
        session.messages = resolution_messages + session.messages
        self._sessions[session.id] = session
        return self._to_checkout(session)

    def _get_session(self, checkout_id: str) -> _Session | None:
        session = self._sessions.get(checkout_id)
        if session is None:
            return None
        if session.status not in {"completed", "canceled"} and _now() > session.expires_at:
            session.status = "canceled"
        return session

    def get_checkout(self, checkout_id: str) -> Checkout | ErrorResponse:
        session = self._get_session(checkout_id)
        if session is None:
            return self._error("not_found", f"Checkout '{checkout_id}' not found.")
        return self._to_checkout(session)

    def update_checkout(
        self,
        checkout_id: str,
        *,
        line_items: list[LineItemRequest] | None = None,
        buyer: Buyer | None = None,
    ) -> Checkout | ErrorResponse:
        session = self._get_session(checkout_id)
        if session is None:
            return self._error("not_found", f"Checkout '{checkout_id}' not found.")
        if session.status in {"completed", "canceled"}:
            return self._error(
                "not_allowed",
                f"Checkout is {session.status} and can no longer be updated.",
            )

        resolution_messages: list[Message] = []
        if line_items is not None:
            resolved, resolution_messages = self._resolve_line_items(
                self._coerce_line_items(line_items)
            )
            session.line_items = resolved
        if buyer is not None:
            buyer = self._coerce_buyer(buyer)
            merged = session.buyer.model_dump()
            merged.update({k: v for k, v in buyer.model_dump().items() if v is not None})
            session.buyer = Buyer(**merged)

        self._recompute(session)
        session.messages = resolution_messages + session.messages
        return self._to_checkout(session)

    def complete_checkout(
        self,
        checkout_id: str,
        *,
        payment: Payment | None = None,
    ) -> Checkout | ErrorResponse:
        session = self._get_session(checkout_id)
        if session is None:
            return self._error("not_found", f"Checkout '{checkout_id}' not found.")
        if session.status == "completed":
            return self._to_checkout(session)
        if session.status == "canceled":
            return self._error("not_allowed", "Checkout is canceled.")

        if payment is not None:
            session.payment = payment
        self._recompute(session)
        if session.status != "ready_for_complete":
            # Still missing required info; surface as escalation for buyer handoff.
            session.status = "requires_escalation"
            checkout = self._to_checkout(session)
            checkout.continue_url = self._continue_url(session.id)
            return checkout

        payment_reference = self._extract_payment_reference(session.payment)
        totals = self.adapter.price(session.line_items)
        total_minor = self._total_amount(totals)

        # 1. Verify the platform-issued payment authorization (if configured).
        if self.platform_client is not None:
            if not payment_reference:
                return self._error(
                    "payment_declined",
                    "A payment authorization reference is required to complete this order.",
                    severity="requires_buyer_input",
                )
            try:
                verify_authorization(
                    self.platform_client,
                    payment_reference,
                    expected_amount_minor=total_minor,
                    currency=self.currency,
                )
            except PlatformError as error:
                return self._error(error.code, str(error), severity="recoverable")

        # 2. Place the real order.
        try:
            confirmation = self.adapter.create_order(
                line_items=session.line_items,
                buyer=session.buyer,
                totals=totals,
                payment_reference=payment_reference,
            )
        except ValueError as error:
            if self.platform_client is not None and payment_reference:
                self._safe_release(payment_reference, reason="order_creation_failed")
            return self._error("payment_failed", str(error), severity="recoverable")

        # 3. Accredit / settle the payment so the platform credits the merchant.
        if self.platform_client is not None and payment_reference:
            try:
                accredit_result = self.platform_client.accredit(
                    payment_reference,
                    order_id=confirmation.id,
                    amount_minor=total_minor or 0,
                    currency=self.currency,
                    idempotency_key=session.id,
                )
            except PlatformError as error:
                # The order exists but settlement failed: complete the checkout and
                # flag for reconciliation rather than losing the placed order.
                session.order = confirmation
                session.status = "completed"
                session.messages = [
                    Message(
                        type="warning",
                        code="reconciliation_required",
                        content=f"Order placed but payment accreditation failed: {error}",
                        severity="requires_buyer_review",
                    )
                ]
                return self._to_checkout(session)

            # 4. Settlement confirmed: let the store reconcile (e.g. mark paid).
            try:
                updated = self.adapter.on_payment_accredited(
                    order_id=confirmation.id,
                    payment_reference=payment_reference,
                    amount_minor=total_minor or 0,
                    currency=self.currency,
                    result=accredit_result if isinstance(accredit_result, dict) else None,
                )
                if updated is not None:
                    confirmation = updated
            except Exception as error:  # noqa: BLE001 - never lose a placed+settled order
                session.order = confirmation
                session.status = "completed"
                session.messages = [
                    Message(
                        type="warning",
                        code="order_update_deferred",
                        content=f"Payment settled but local order update failed: {error}",
                        severity="requires_buyer_review",
                    )
                ]
                return self._to_checkout(session)

        session.order = confirmation
        session.status = "completed"
        session.messages = []
        return self._to_checkout(session)

    def _safe_release(self, authorization_id: str, *, reason: str) -> None:
        """Best-effort release of a reserved authorization; never raises."""
        if self.platform_client is None:
            return
        try:
            self.platform_client.release(authorization_id, reason=reason)
        except PlatformError:
            pass

    @staticmethod
    def _total_amount(totals: list[Total]) -> int | None:
        """Authoritative order total in minor units from the totals breakdown."""
        if not totals:
            return None
        for total in totals:
            if total.type == "total":
                return total.amount
        return sum(t.amount for t in totals if t.type == "subtotal") or None

    def cancel_checkout(self, checkout_id: str) -> Checkout | ErrorResponse:
        session = self._get_session(checkout_id)
        if session is None:
            return self._error("not_found", f"Checkout '{checkout_id}' not found.")
        if session.status in {"completed", "canceled"}:
            return self._error(
                "not_allowed",
                f"Checkout is already {session.status} and cannot be canceled.",
            )
        session.status = "canceled"
        session.messages = []
        return self._to_checkout(session)

    @staticmethod
    def _extract_payment_reference(payment: Payment | None) -> str | None:
        if payment is None or not payment.instruments:
            return None
        for instrument in payment.instruments:
            credential = instrument.credential or {}
            reference = credential.get("reference") or credential.get("token")
            if reference:
                return str(reference)
        return None
