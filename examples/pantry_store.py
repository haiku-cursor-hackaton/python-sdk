"""Genko Pantry — specialty food and gourmet gifts.

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
        id="coffee-beans-12oz",
        title="Single-Origin Coffee (12oz)",
        price=1699,
        currency="USD",
        image_url="https://picsum.photos/seed/pantry-coffee/400",
        description="Medium roast whole beans, notes of chocolate and citrus.",
        attributes={"keywords": "coffee beans roast brew pantry gourmet", "category": "food"},
    ),
    Product(
        id="honey-jar-8oz",
        title="Wildflower Honey (8oz)",
        price=1399,
        currency="USD",
        image_url="https://picsum.photos/seed/pantry-honey/400",
        description="Raw wildflower honey in a glass jar.",
        attributes={"keywords": "honey jar sweet pantry gourmet gift", "category": "food"},
    ),
    Product(
        id="hot-sauce-trio",
        title="Hot Sauce Trio",
        price=2199,
        currency="USD",
        image_url="https://picsum.photos/seed/pantry-sauce/400",
        description="Three small-batch hot sauces: mild, medium, hot.",
        attributes={"keywords": "hot sauce spicy condiment pantry gourmet", "category": "food"},
    ),
    Product(
        id="olive-oil-500ml",
        title="Extra Virgin Olive Oil (500ml)",
        price=1899,
        currency="USD",
        image_url="https://picsum.photos/seed/pantry-oil/400",
        description="Cold-pressed EVOO from Mediterranean groves.",
        attributes={"keywords": "olive oil cooking pantry gourmet kitchen", "category": "food"},
    ),
    Product(
        id="tea-sampler-6pk",
        title="Herbal Tea Sampler (6)",
        price=1599,
        currency="USD",
        image_url="https://picsum.photos/seed/pantry-tea/400",
        description="Six caffeine-free herbal tea sachets.",
        attributes={"keywords": "tea herbal sampler pantry drink gift", "category": "food"},
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


class PantryAdapter(MerchantAdapter):
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
        order_id = f"PTRY-{self._counter:04d}"
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


public_base = (_env("PUBLIC_BASE_URL") or "http://127.0.0.1:8122").rstrip("/")

ucp = UCPMerchant(
    store_name="Genko Pantry",
    base_url=public_base,
    adapter=PantryAdapter(public_base),
    currency="USD",
    require_buyer_fields=("email",),
    enable_order_capability=True,
    enable_mcp=False,
    platform_url=_env("UCP_PLATFORM_URL"),
    platform_api_key=_env("UCP_PLATFORM_API_KEY"),
    api_keys=_env_csv("UCP_GATEWAY_API_KEY"),
)

app = FastAPI(title="Genko Pantry")
app.include_router(ucp.rest_router)
app.include_router(ucp.well_known_router)
