"""Genko Gear — second reference merchant (mugs, posters, stickers; not apparel).

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
        id="mug-ceramic-white",
        title="Ceramic Mug (12oz)",
        price=1299,
        currency="USD",
        image_url="https://picsum.photos/seed/mug/400",
        description="White ceramic mug, dishwasher safe.",
        attributes={"keywords": "mug cup drink coffee tea kitchen gift", "category": "home"},
    ),
    Product(
        id="poster-anime-city",
        title="Cityscape Poster (18x24)",
        price=2499,
        currency="USD",
        image_url="https://picsum.photos/seed/poster/400",
        description="Matte art poster, ships rolled in a tube.",
        attributes={"keywords": "poster print wall art decor", "category": "art"},
    ),
    Product(
        id="stickers-pack-vinyl",
        title="Vinyl Sticker Pack (5)",
        price=899,
        currency="USD",
        image_url="https://picsum.photos/seed/stickers/400",
        description="Five weatherproof vinyl stickers.",
        attributes={"keywords": "stickers vinyl laptop decal", "category": "accessories"},
    ),
    Product(
        id="notebook-dot-grid",
        title="Dot Grid Notebook",
        price=1499,
        currency="USD",
        image_url="https://picsum.photos/seed/notebook/400",
        description="A5 dot-grid notebook, 120 pages.",
        attributes={"keywords": "notebook journal stationery writing", "category": "stationery"},
    ),
    Product(
        id="tote-canvas-natural",
        title="Canvas Tote Bag",
        price=1899,
        currency="USD",
        image_url="https://picsum.photos/seed/tote/400",
        description="Natural canvas tote with long handles.",
        attributes={"keywords": "tote bag canvas carry shopping", "category": "accessories"},
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


class GearAdapter(MerchantAdapter):
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
        order_id = f"GEAR-{self._counter:04d}"
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


public_base = (_env("PUBLIC_BASE_URL") or "http://127.0.0.1:8120").rstrip("/")

ucp = UCPMerchant(
    store_name="Genko Gear",
    base_url=public_base,
    adapter=GearAdapter(public_base),
    currency="USD",
    require_buyer_fields=("email",),
    enable_order_capability=True,
    enable_mcp=False,
    platform_url=_env("UCP_PLATFORM_URL"),
    platform_api_key=_env("UCP_PLATFORM_API_KEY"),
    api_keys=_env_csv("UCP_GATEWAY_API_KEY"),
)

app = FastAPI(title="Genko Gear")
app.include_router(ucp.rest_router)
app.include_router(ucp.well_known_router)
