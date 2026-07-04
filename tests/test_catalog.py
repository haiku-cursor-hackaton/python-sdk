from __future__ import annotations

import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from ucp_merchant import (
    Buyer,
    MerchantAdapter,
    OrderConfirmation,
    Product,
    UCPMerchant,
)


class CatalogAdapter(MerchantAdapter):
    def __init__(self) -> None:
        self.created: list[dict] = []
        self.orders: dict[str, OrderConfirmation] = {}

    def get_products(self) -> list[Product]:
        return [
            Product(
                id="tee-black-m",
                title="Black Tee",
                price=1599,
                description="A black tee.",
                image_url="https://store.example/img/tee.png",
                attributes={"category": "apparel", "url": "https://store.example/p/tee"},
            ),
            Product(
                id="mug-white",
                title="White Mug",
                price=900,
                attributes={"category": "drinkware"},
            ),
            Product(
                id="sticker-pack",
                title="Sticker Pack",
                price=300,
                available=False,
                attributes={"category": "misc"},
            ),
        ]

    def create_order(self, *, line_items, buyer, totals, payment_reference=None) -> OrderConfirmation:
        confirmation = OrderConfirmation(
            id="ORD-9",
            label="ORD-9",
            permalink_url="https://store.example/orders/ORD-9",
        )
        self.created.append({"buyer": buyer.email})
        self.orders[confirmation.id] = confirmation
        return confirmation

    def get_order(self, order_id: str) -> OrderConfirmation | None:
        return self.orders.get(order_id)


def make_client(*, enable_order: bool = False) -> tuple[TestClient, CatalogAdapter]:
    adapter = CatalogAdapter()
    merchant = UCPMerchant(
        store_name="Catalog Store",
        base_url="https://store.example",
        adapter=adapter,
        require_buyer_fields=("email",),
        enable_order_capability=enable_order,
    )
    app = FastAPI()
    app.include_router(merchant.rest_router)
    app.include_router(merchant.mcp_router)
    app.include_router(merchant.well_known_router)
    return TestClient(app), adapter


class CatalogRestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client, self.adapter = make_client()

    def test_search_query_match(self) -> None:
        resp = self.client.post("/ucp/v1/catalog/search", json={"query": "tee"})
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["ucp"]["status"], "success")
        caps = body["ucp"]["capabilities"]
        self.assertIn("dev.ucp.shopping.catalog.search", caps)
        self.assertIn("dev.ucp.shopping.catalog.lookup", caps)
        self.assertEqual(len(body["products"]), 1)
        product = body["products"][0]
        self.assertEqual(product["id"], "tee-black-m")
        self.assertEqual(product["price_range"]["min"], {"amount": 1599, "currency": "USD"})
        self.assertEqual(product["variants"][0]["id"], "tee-black-m")
        self.assertEqual(product["url"], "https://store.example/p/tee")
        self.assertEqual(product["media"][0]["type"], "image")

    def test_search_empty_result_has_no_error(self) -> None:
        resp = self.client.post("/ucp/v1/catalog/search", json={"query": "nonexistent-xyz"})
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["products"], [])
        self.assertNotIn("messages", body)
        self.assertEqual(body["ucp"]["status"], "success")

    def test_search_price_and_category_filters(self) -> None:
        resp = self.client.post(
            "/ucp/v1/catalog/search",
            json={"filters": {"categories": ["drinkware"], "price": {"max": 1000}}},
        )
        body = resp.json()
        ids = {p["id"] for p in body["products"]}
        self.assertEqual(ids, {"mug-white"})

    def test_search_pagination_cursor(self) -> None:
        first = self.client.post(
            "/ucp/v1/catalog/search", json={"pagination": {"limit": 2}}
        ).json()
        self.assertEqual(len(first["products"]), 2)
        self.assertTrue(first["pagination"]["has_next_page"])
        self.assertEqual(first["pagination"]["total_count"], 3)
        cursor = first["pagination"]["cursor"]

        second = self.client.post(
            "/ucp/v1/catalog/search", json={"pagination": {"limit": 2, "cursor": cursor}}
        ).json()
        self.assertEqual(len(second["products"]), 1)
        self.assertFalse(second["pagination"]["has_next_page"])

    def test_lookup_partial_not_found(self) -> None:
        resp = self.client.post(
            "/ucp/v1/catalog/lookup",
            json={"ids": ["tee-black-m", "does-not-exist"]},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(len(body["products"]), 1)
        variant = body["products"][0]["variants"][0]
        self.assertEqual(variant["inputs"], [{"id": "tee-black-m", "match": "exact"}])
        self.assertTrue(
            any(
                m["type"] == "info" and m["code"] == "not_found" and m["content"] == "does-not-exist"
                for m in body["messages"]
            )
        )

    def test_lookup_all_not_found_is_200(self) -> None:
        resp = self.client.post("/ucp/v1/catalog/lookup", json={"ids": ["nope-1", "nope-2"]})
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["products"], [])
        self.assertEqual(len(body["messages"]), 2)

    def test_product_found_singular(self) -> None:
        resp = self.client.post("/ucp/v1/catalog/product", json={"id": "mug-white"})
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertIn("product", body)
        self.assertNotIn("products", body)
        self.assertEqual(body["product"]["id"], "mug-white")
        self.assertEqual(body["ucp"]["status"], "success")

    def test_product_not_found_error_envelope(self) -> None:
        resp = self.client.post("/ucp/v1/catalog/product", json={"id": "ghost"})
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["ucp"]["status"], "error")
        self.assertNotIn("product", body)
        message = body["messages"][0]
        self.assertEqual(message["code"], "not_found")
        self.assertEqual(message["severity"], "unrecoverable")

    def test_missing_ids_is_validation_error(self) -> None:
        resp = self.client.post("/ucp/v1/catalog/lookup", json={})
        self.assertEqual(resp.status_code, 422)

    def test_catalog_variant_id_completes_checkout(self) -> None:
        search = self.client.post("/ucp/v1/catalog/search", json={"query": "tee"}).json()
        variant_id = search["products"][0]["variants"][0]["id"]

        create = self.client.post(
            "/ucp/v1/checkout-sessions",
            json={
                "line_items": [{"item": {"id": variant_id}, "quantity": 1}],
                "buyer": {"email": "buyer@example.com"},
            },
        )
        self.assertEqual(create.status_code, 200, create.text)
        checkout_id = create.json()["id"]
        self.assertEqual(create.json()["status"], "ready_for_complete")

        complete = self.client.post(
            f"/ucp/v1/checkout-sessions/{checkout_id}/complete",
            json={
                "payment": {
                    "instruments": [
                        {"handler_id": "offline", "type": "offline", "credential": {"reference": "R"}}
                    ]
                }
            },
        )
        self.assertEqual(complete.status_code, 200, complete.text)
        self.assertEqual(complete.json()["status"], "completed")
        self.assertEqual(complete.json()["order"]["id"], "ORD-9")


class CatalogDiscoveryTests(unittest.TestCase):
    def test_profile_advertises_catalog_capabilities(self) -> None:
        client, _ = make_client()
        profile = client.get("/.well-known/ucp").json()
        caps = profile["ucp"]["capabilities"]
        self.assertIn("dev.ucp.shopping.checkout", caps)
        self.assertIn("dev.ucp.shopping.catalog.search", caps)
        self.assertIn("dev.ucp.shopping.catalog.lookup", caps)
        self.assertNotIn("dev.ucp.shopping.order", caps)

    def test_order_capability_disabled_by_default(self) -> None:
        client, _ = make_client()
        # Route is not mounted; SPA-less app returns 404 for the order path.
        resp = client.get("/ucp/v1/orders/ORD-9")
        self.assertEqual(resp.status_code, 404)


class CatalogMcpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client, _ = make_client()

    def _call(self, name: str, arguments: dict, request_id: int = 1) -> dict:
        resp = self.client.post(
            "/ucp/mcp",
            json={
                "jsonrpc": "2.0",
                "id": request_id,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            },
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        return resp.json()

    def test_tools_list_has_catalog_tools(self) -> None:
        resp = self.client.post(
            "/ucp/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        )
        names = {t["name"] for t in resp.json()["result"]["tools"]}
        self.assertTrue({"search_products", "lookup_products", "get_product"} <= names)
        self.assertTrue({"create_checkout", "complete_checkout"} <= names)

    def test_search_products_returns_catalog_shape(self) -> None:
        structured = self._call("search_products", {"query": "tee"})["result"]["structuredContent"]
        self.assertEqual(structured["products"][0]["id"], "tee-black-m")
        self.assertIn("price_range", structured["products"][0])

    def test_lookup_products_tool(self) -> None:
        structured = self._call("lookup_products", {"ids": ["mug-white", "ghost"]})[
            "result"
        ]["structuredContent"]
        self.assertEqual(len(structured["products"]), 1)
        self.assertEqual(structured["messages"][0]["code"], "not_found")

    def test_get_product_tool(self) -> None:
        structured = self._call("get_product", {"id": "mug-white"})["result"]["structuredContent"]
        self.assertEqual(structured["product"]["id"], "mug-white")


class OrderCapabilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client, self.adapter = make_client(enable_order=True)

    def test_profile_advertises_order(self) -> None:
        profile = self.client.get("/.well-known/ucp").json()
        self.assertIn("dev.ucp.shopping.order", profile["ucp"]["capabilities"])

    def test_get_order_found(self) -> None:
        # Place an order first so the adapter can resolve it.
        create = self.client.post(
            "/ucp/v1/checkout-sessions",
            json={
                "line_items": [{"item": {"id": "tee-black-m"}, "quantity": 1}],
                "buyer": {"email": "buyer@example.com"},
            },
        )
        checkout_id = create.json()["id"]
        self.client.post(
            f"/ucp/v1/checkout-sessions/{checkout_id}/complete",
            json={
                "payment": {
                    "instruments": [
                        {"handler_id": "offline", "type": "offline", "credential": {"reference": "R"}}
                    ]
                }
            },
        )
        resp = self.client.get("/ucp/v1/orders/ORD-9")
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["ucp"]["status"], "success")
        self.assertEqual(body["order"]["id"], "ORD-9")

    def test_get_order_not_found_error_envelope(self) -> None:
        resp = self.client.get("/ucp/v1/orders/UNKNOWN")
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["ucp"]["status"], "error")
        self.assertEqual(body["messages"][0]["code"], "not_found")


if __name__ == "__main__":
    unittest.main()
