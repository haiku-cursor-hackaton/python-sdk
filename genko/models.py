"""Pydantic models for the UCP shopping/checkout capability.

These follow the Universal Commerce Protocol specification (version
``2026-04-08``). Only the subset required for catalog discovery and the
checkout lifecycle is modelled; the spec allows additive fields, so unknown
keys are ignored on input.

All monetary amounts are integers in the currency's minor unit (cents).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

UCP_VERSION = "2026-04-08"
CHECKOUT_CAPABILITY = "dev.ucp.shopping.checkout"
CATALOG_SEARCH_CAPABILITY = "dev.ucp.shopping.catalog.search"
CATALOG_LOOKUP_CAPABILITY = "dev.ucp.shopping.catalog.lookup"
ORDER_CAPABILITY = "dev.ucp.shopping.order"
SHOPPING_SERVICE = "dev.ucp.shopping"

CheckoutStatus = Literal[
    "incomplete",
    "requires_escalation",
    "ready_for_complete",
    "complete_in_progress",
    "completed",
    "canceled",
]

MessageType = Literal["error", "warning", "info"]
Severity = Literal[
    "recoverable",
    "requires_buyer_input",
    "requires_buyer_review",
    "unrecoverable",
]


class UCPBase(BaseModel):
    """Base model that ignores unknown fields (UCP is additively versioned)."""

    model_config = ConfigDict(extra="ignore", populate_by_name=True)


# --------------------------------------------------------------------------- #
# Catalog / item primitives
# --------------------------------------------------------------------------- #
class Item(UCPBase):
    id: str
    title: str
    price: int = Field(description="Unit price in ISO 4217 minor units.")
    image_url: str | None = None


class ItemRef(UCPBase):
    """Item reference used on create/update requests (only ``id`` is required)."""

    id: str
    title: str | None = None
    price: int | None = None
    image_url: str | None = None


class Total(UCPBase):
    type: str
    amount: int
    display_text: str | None = None


class LineItemRequest(UCPBase):
    """A line item as supplied by the platform when creating/updating."""

    item: ItemRef
    quantity: int = Field(ge=1)
    id: str | None = None


class LineItem(UCPBase):
    """A resolved line item returned by the business."""

    id: str
    item: Item
    quantity: int = Field(ge=1)
    totals: list[Total] = Field(default_factory=list)


class Buyer(UCPBase):
    first_name: str | None = None
    last_name: str | None = None
    email: str | None = None
    phone_number: str | None = None


class Link(UCPBase):
    type: str
    url: str
    title: str | None = None


class Message(UCPBase):
    type: MessageType
    content: str
    code: str | None = None
    path: str | None = None
    severity: Severity | None = None
    content_type: Literal["plain", "markdown"] = "plain"


class PaymentInstrument(UCPBase):
    id: str | None = None
    handler_id: str
    type: str
    credential: dict[str, Any] | None = None
    billing_address: dict[str, Any] | None = None
    display: dict[str, Any] | None = None
    selected: bool | None = None


class Payment(UCPBase):
    instruments: list[PaymentInstrument] = Field(default_factory=list)


class OrderConfirmation(UCPBase):
    id: str
    permalink_url: str
    label: str | None = None
    # Post-purchase snapshot so the platform (infra) and the buyer's client can
    # confirm the order was placed successfully. All fields optional/additive.
    status: str | None = None
    payment_status: str | None = None
    currency: str | None = None
    created_at: str | None = None
    totals: list[Total] | None = None
    line_items: list[LineItem] | None = None
    messages: list[Message] | None = None


class UcpEnvelope(UCPBase):
    version: str = UCP_VERSION
    status: Literal["success", "error"] = "success"
    capabilities: dict[str, Any] = Field(default_factory=dict)
    payment_handlers: dict[str, Any] = Field(default_factory=dict)


class Checkout(UCPBase):
    ucp: UcpEnvelope
    id: str
    status: CheckoutStatus
    currency: str
    line_items: list[LineItem] = Field(default_factory=list)
    totals: list[Total] = Field(default_factory=list)
    buyer: Buyer | None = None
    messages: list[Message] = Field(default_factory=list)
    links: list[Link] = Field(default_factory=list)
    expires_at: str | None = None
    continue_url: str | None = None
    payment: Payment | None = None
    order: OrderConfirmation | None = None


class ErrorResponse(UCPBase):
    ucp: UcpEnvelope
    messages: list[Message] = Field(default_factory=list)
    continue_url: str | None = None


# --------------------------------------------------------------------------- #
# SDK-facing catalog model (what a merchant adapter returns)
# --------------------------------------------------------------------------- #
class Product(UCPBase):
    """A purchasable product exposed by a merchant adapter.

    ``id`` is the SKU that both the platform and business recognize. For stores
    with variants (size/color), encode the variant into the id.
    """

    id: str
    title: str
    price: int = Field(description="Unit price in minor units (cents).")
    currency: str = "USD"
    image_url: str | None = None
    description: str | None = None
    available: bool = True
    attributes: dict[str, Any] = Field(default_factory=dict)

    def to_item(self) -> Item:
        return Item(id=self.id, title=self.title, price=self.price, image_url=self.image_url)
