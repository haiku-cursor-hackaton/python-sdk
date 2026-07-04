"""A minimal runnable UCP store, to show how little integration code is needed.

Run it::

    pip install -e ".[examples]"
    uvicorn examples.demo_store:app --reload --port 8100

Then discover it at http://127.0.0.1:8100/.well-known/ucp
"""

from __future__ import annotations

from fastapi import FastAPI

from ucp_merchant import (
    Buyer,
    LineItem,
    Link,
    MerchantAdapter,
    OrderConfirmation,
    Product,
    Total,
    UCPMerchant,
)

CATALOG = [
    Product(id="tee-black-m", title="Black Tee (M)", price=1599, image_url="https://picsum.photos/seed/tee/400"),
    Product(id="tee-white-l", title="White Tee (L)", price=1599),
    Product(id="hoodie-navy-l", title="Navy Hoodie (L)", price=3999, available=False),
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
        order_id = f"DEMO-{self._counter:04d}"
        confirmation = OrderConfirmation(
            id=order_id,
            label=order_id,
            permalink_url=f"https://demo-store.example.com/orders/{order_id}",
        )
        self._orders[order_id] = confirmation
        return confirmation

    def get_order(self, order_id: str) -> OrderConfirmation | None:
        return self._orders.get(order_id)


ucp = UCPMerchant(
    store_name="Demo Store",
    base_url="http://127.0.0.1:8100",
    adapter=DemoAdapter(),
    currency="USD",
    require_buyer_fields=("email",),
    links=[
        Link(type="privacy_policy", url="https://demo-store.example.com/privacy"),
        Link(type="terms_of_service", url="https://demo-store.example.com/terms"),
    ],
)

app = FastAPI(title="UCP Demo Store")
app.include_router(ucp.rest_router)
app.include_router(ucp.mcp_router)
app.include_router(ucp.well_known_router)
