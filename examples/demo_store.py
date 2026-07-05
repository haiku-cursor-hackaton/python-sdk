"""A minimal runnable UCP store, to show how little integration code is needed.

Run it::

    pip install -e ".[examples]"
    uvicorn examples.demo_store:app --reload --port 8100

Then discover it at http://127.0.0.1:8100/.well-known/ucp
"""

from __future__ import annotations

import os
from uuid import uuid4

from fastapi import FastAPI

from genko import (
    Buyer,
    LineItem,
    Link,
    MerchantAdapter,
    OrderConfirmation,
    Product,
    Total,
    UCPMerchant,
)

_TEE_KEYWORDS = "shirt t-shirt tshirt tee apparel top clothing"

CATALOG = [
    Product(
        id="tee-black-m",
        title="Black Tee (M)",
        price=1599,
        image_url="https://picsum.photos/seed/tee/400",
        description="Classic black t-shirt, size M.",
        attributes={"keywords": _TEE_KEYWORDS, "color": "black", "size": "M"},
    ),
    Product(
        id="tee-white-l",
        title="White Tee (L)",
        price=1599,
        description="Classic white t-shirt, size L.",
        attributes={"keywords": _TEE_KEYWORDS, "color": "white", "size": "L"},
    ),
    Product(
        id="hoodie-navy-l",
        title="Navy Hoodie (L)",
        price=3999,
        available=False,
        description="Navy hoodie sweatshirt, size L.",
        attributes={"keywords": "hoodie sweatshirt shirt apparel top clothing", "color": "navy", "size": "L"},
    ),
]


class DemoAdapter(MerchantAdapter):
    def __init__(self) -> None:
        self._orders: dict[str, OrderConfirmation] = {}
        self._counter = 0

    def get_products(self) -> list[Product]:
        return CATALOG

    def create_order(
        self,
        *,
        line_items: list[LineItem],
        buyer: Buyer,
        totals: list[Total],
        payment_reference: str | None = None,
    ) -> OrderConfirmation:
        self._counter += 1
        order_id = f"DEMO-{self._counter:04d}-{uuid4().hex[:8].upper()}"
        confirmation = OrderConfirmation(
            id=order_id,
            label=order_id,
            permalink_url=f"https://demo-store.example.com/orders/{order_id}",
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
        self, *, order_id: str, payment_reference: str, amount_minor: int, currency: str, result=None
    ) -> OrderConfirmation | None:
        confirmation = self._orders.get(order_id)
        if confirmation is None:
            return None
        confirmation.payment_status = "paid"
        confirmation.status = "paid"
        return confirmation


def _flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


_BASE_URL = os.getenv("UCP_DEMO_BASE_URL", "http://127.0.0.1:8100").rstrip("/")


# Optional platform accreditation for local testing against ucp-platform-mock:
# set UCP_PLATFORM_URL + UCP_PLATFORM_API_KEY to make the SDK verify + accredit.
ucp = UCPMerchant(
    store_name="Demo Store",
    base_url=_BASE_URL,
    adapter=DemoAdapter(),
    currency="USD",
    require_buyer_fields=("email",),
    enable_order_capability=True,
    platform_url=os.getenv("UCP_PLATFORM_URL") or None,
    platform_api_key=os.getenv("UCP_PLATFORM_API_KEY") or None,
    enable_mcp=True,  # local demo only; production Genko vendors are REST-only
    links=[
        Link(type="privacy_policy", url="https://demo-store.example.com/privacy"),
        Link(type="terms_of_service", url="https://demo-store.example.com/terms"),
    ],
)

app = FastAPI(title="UCP Demo Store")
app.include_router(ucp.rest_router)
app.include_router(ucp.mcp_router)
app.include_router(ucp.well_known_router)
