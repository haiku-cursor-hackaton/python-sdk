"""REST transport binding for the catalog + checkout capabilities.

Maps the abstract UCP operations to RESTful routes:

- ``POST   {prefix}/catalog/search``               Catalog Search
- ``POST   {prefix}/catalog/lookup``               Catalog Lookup
- ``POST   {prefix}/catalog/product``              Catalog Product (detail)
- ``POST   {prefix}/checkout-sessions``            Create Checkout
- ``GET    {prefix}/checkout-sessions/{id}``       Get Checkout
- ``PUT    {prefix}/checkout-sessions/{id}``       Update Checkout
- ``POST   {prefix}/checkout-sessions/{id}/complete``  Complete Checkout
- ``POST   {prefix}/checkout-sessions/{id}/cancel``    Cancel Checkout
- ``GET    {prefix}/orders/{id}``                  Get Order (optional capability)
- ``GET    {prefix}/products``                     Non-standard convenience feed
"""

from __future__ import annotations

from fastapi import APIRouter, Response
from pydantic import BaseModel, ConfigDict, Field

from .engine import CheckoutEngine
from .models import Buyer, ErrorResponse, LineItemRequest, Payment


class CreateCheckoutBody(BaseModel):
    line_items: list[LineItemRequest] = Field(default_factory=list)
    buyer: Buyer | None = None
    payment: Payment | None = None


class UpdateCheckoutBody(BaseModel):
    line_items: list[LineItemRequest] | None = None
    buyer: Buyer | None = None
    payment: Payment | None = None


class CompleteCheckoutBody(BaseModel):
    payment: Payment | None = None


class CatalogSearchBody(BaseModel):
    # ``context``/``signals``/``attribution`` are accepted and ignored.
    model_config = ConfigDict(extra="ignore")

    query: str | None = None
    filters: dict | None = None
    pagination: dict | None = None
    context: dict | None = None
    signals: dict | None = None
    attribution: dict | None = None


class CatalogLookupBody(BaseModel):
    model_config = ConfigDict(extra="ignore")

    ids: list[str]
    filters: dict | None = None
    context: dict | None = None
    signals: dict | None = None
    attribution: dict | None = None


class CatalogProductBody(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str
    selected: dict | None = None
    preferences: dict | None = None
    filters: dict | None = None
    context: dict | None = None


def _status_code(result) -> int:
    """Map a UCP result to an HTTP status per the spec's error handling."""
    if isinstance(result, ErrorResponse):
        code = result.messages[0].code if result.messages else None
        if code == "not_found":
            return 404
        if code == "not_allowed":
            return 409
        return 422
    return 200


def build_rest_router(
    engine: CheckoutEngine, *, prefix: str, enable_order: bool = False
) -> APIRouter:
    router = APIRouter(prefix=prefix, tags=["ucp"])

    # ------------------------------------------------------------------ #
    # Catalog capability (search + lookup)
    # ------------------------------------------------------------------ #
    @router.post("/catalog/search")
    def catalog_search(body: CatalogSearchBody) -> dict:
        return engine.search_catalog(
            query=body.query, filters=body.filters, pagination=body.pagination
        )

    @router.post("/catalog/lookup")
    def catalog_lookup(body: CatalogLookupBody) -> dict:
        return engine.lookup_catalog(body.ids, filters=body.filters)

    @router.post("/catalog/product")
    def catalog_product(body: CatalogProductBody) -> dict:
        # Business "not found" is HTTP 200 with an error envelope (see engine).
        return engine.get_product_detail(
            body.id, selected=body.selected, preferences=body.preferences
        )

    # NON-STANDARD convenience feed. This is NOT part of the UCP standard and is
    # deliberately not advertised in the discovery profile; UCP discovery happens
    # via ``POST /catalog/search`` and ``/catalog/lookup``. Kept for examples and
    # simple debugging only.
    @router.get("/products")
    def list_products() -> dict:
        return {
            "ucp": {"version": engine.version},
            "products": [p.model_dump(exclude_none=True) for p in engine.adapter.get_products()],
        }

    @router.post("/checkout-sessions")
    def create_checkout(body: CreateCheckoutBody, response: Response) -> dict:
        result = engine.create_checkout(line_items=body.line_items, buyer=body.buyer)
        response.status_code = _status_code(result)
        return result.model_dump(exclude_none=True)

    @router.get("/checkout-sessions/{checkout_id}")
    def get_checkout(checkout_id: str, response: Response) -> dict:
        result = engine.get_checkout(checkout_id)
        response.status_code = _status_code(result)
        return result.model_dump(exclude_none=True)

    @router.put("/checkout-sessions/{checkout_id}")
    def update_checkout(checkout_id: str, body: UpdateCheckoutBody, response: Response) -> dict:
        result = engine.update_checkout(
            checkout_id, line_items=body.line_items, buyer=body.buyer
        )
        response.status_code = _status_code(result)
        return result.model_dump(exclude_none=True)

    @router.post("/checkout-sessions/{checkout_id}/complete")
    def complete_checkout(checkout_id: str, body: CompleteCheckoutBody, response: Response) -> dict:
        result = engine.complete_checkout(checkout_id, payment=body.payment)
        response.status_code = _status_code(result)
        return result.model_dump(exclude_none=True)

    @router.post("/checkout-sessions/{checkout_id}/cancel")
    def cancel_checkout(checkout_id: str, response: Response) -> dict:
        result = engine.cancel_checkout(checkout_id)
        response.status_code = _status_code(result)
        return result.model_dump(exclude_none=True)

    # ------------------------------------------------------------------ #
    # Order capability (optional; only mounted when advertised)
    # ------------------------------------------------------------------ #
    if enable_order:

        @router.get("/orders/{order_id}")
        def get_order(order_id: str) -> dict:
            # Missing orders return HTTP 200 with an error envelope per spec.
            return engine.get_order_detail(order_id)

    return router
