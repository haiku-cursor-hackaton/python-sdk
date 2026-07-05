"""Genko Basics — blank essentials apparel (tees, hoodies, socks).

Production REST-only UCP store for multi-merchant platform testing.
"""

from __future__ import annotations

import os

from fastapi import FastAPI

from genko import (
    Buyer,
    LineItem,
    MerchantAdapter,
    OrderConfirmation,
    Product,
    Total,
    UCPMerchant,
)

CATALOG = [
    Product(
        id="tee-heather-grey-m",
        title="Heather Grey Tee (M)",
        price=1799,
        currency="USD",
        image_url="https://picsum.photos/seed/basics-tee/400",
        description="Soft ringspun cotton tee, heather grey, size M.",
        attributes={"keywords": "shirt tee tshirt apparel basics clothing grey", "category": "apparel"},
    ),
    Product(
        id="hoodie-charcoal-xl",
        title="Charcoal Hoodie (XL)",
        price=4499,
        currency="USD",
        image_url="https://picsum.photos/seed/basics-hoodie/400",
        description="Midweight fleece hoodie, charcoal, size XL.",
        attributes={"keywords": "hoodie sweatshirt apparel basics clothing charcoal", "category": "apparel"},
    ),
    Product(
        id="socks-athletic-3pk",
        title="Athletic Socks (3-pack)",
        price=1199,
        currency="USD",
        image_url="https://picsum.photos/seed/basics-socks/400",
        description="Cushioned crew socks, one size fits most.",
        attributes={"keywords": "socks athletic basics clothing", "category": "apparel"},
    ),
    Product(
        id="cap-dad-navy",
        title="Dad Cap (Navy)",
        price=2199,
        currency="USD",
        image_url="https://picsum.photos/seed/basics-cap/400",
        description="Unstructured cotton dad cap with adjustable strap.",
        attributes={"keywords": "cap hat navy basics accessories", "category": "accessories"},
    ),
]


def _env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name, default)
    if value is None:
        return None
    text = value.strip()
    return text or None


def _env_csv(name: str) -> list[str] | None:
    raw = _env(name)
    if not raw:
        return None
    keys = [part.strip() for part in raw.split(",") if part.strip()]
    return keys or None


class BasicsAdapter(MerchantAdapter):
    def __init__(self, base_url: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._orders: dict[str, OrderConfirmation] = {}
        self._counter = 0

    def get_products(self) -> list[Product]:
        return CATALOG

    def get_product(self, item_id: str) -> Product | None:
        for product in CATALOG:
            if product.id == item_id:
                return product
        return None

    def create_order(
        self,
        *,
        line_items: list[LineItem],
        buyer: Buyer,
        totals: list[Total],
        payment_reference: str | None = None,
    ) -> OrderConfirmation:
        if not buyer.email:
            raise ValueError("A buyer email is required.")
        self._counter += 1
        order_id = f"BASIC-{self._counter:04d}"
        confirmation = OrderConfirmation(
            id=order_id,
            label=order_id,
            permalink_url=f"{self._base_url}/orders/{order_id}",
            status="created",
            payment_status="pending",
            currency="USD",
            totals=totals,
        )
        self._orders[order_id] = confirmation
        return confirmation

    def get_order(self, order_id: str) -> OrderConfirmation | None:
        return self._orders.get(order_id)

    def on_payment_accredited(
        self,
        *,
        order_id: str,
        payment_reference: str,
        amount_minor: int,
        currency: str,
        result=None,
    ) -> OrderConfirmation | None:
        confirmation = self._orders.get(order_id)
        if confirmation is None:
            return None
        confirmation.payment_status = "paid"
        confirmation.status = "paid"
        return confirmation


public_base = (_env("PUBLIC_BASE_URL") or "http://127.0.0.1:8121").rstrip("/")

ucp = UCPMerchant(
    store_name="Genko Basics",
    base_url=public_base,
    adapter=BasicsAdapter(public_base),
    currency="USD",
    require_buyer_fields=("email",),
    enable_order_capability=True,
    enable_mcp=False,
    platform_url=_env("UCP_PLATFORM_URL"),
    platform_api_key=_env("UCP_PLATFORM_API_KEY"),
    api_keys=_env_csv("UCP_GATEWAY_API_KEY"),
)

app = FastAPI(title="Genko Basics")
app.include_router(ucp.rest_router)
app.include_router(ucp.well_known_router)
