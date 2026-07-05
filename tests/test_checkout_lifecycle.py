from __future__ import annotations

import json
import unittest

from fastapi.testclient import TestClient
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


class FakeAdapter(MerchantAdapter):
    def __init__(self) -> None:
        self.created: list[dict] = []

    def get_products(self) -> list[Product]:
        return [
            Product(id="sku-1", title="Widget", price=1000),
            Product(id="sku-2", title="Gadget", price=2500),
            Product(id="sku-oos", title="Sold Out", price=500, available=False),
        ]

    def create_order(self, *, line_items, buyer, totals, payment_reference=None) -> OrderConfirmation:
        self.created.append(
            {
                "buyer": buyer.email,
                "reference": payment_reference,
                "quantity": sum(li.quantity for li in line_items),
            }
        )
        return OrderConfirmation(
            id="ORD-1", label="ORD-1", permalink_url="https://store.example/orders/ORD-1"
        )


def make_merchant(require=("email",), *, enable_mcp: bool = False) -> tuple[UCPMerchant, FakeAdapter]:
    adapter = FakeAdapter()
    merchant = UCPMerchant(
        store_name="Test Store",
        base_url="https://store.example",
        adapter=adapter,
        require_buyer_fields=require,
        enable_mcp=enable_mcp,
    )
    return merchant, adapter


class EngineLifecycleTests(unittest.TestCase):
    def test_create_incomplete_when_missing_buyer(self):
        merchant, _ = make_merchant()
        result = merchant.engine.create_checkout(
            line_items=[{"item": {"id": "sku-1"}, "quantity": 2}]  # type: ignore[list-item]
        )
        self.assertEqual(result.status, "incomplete")
        codes = {m.code for m in result.messages}
        self.assertIn("field_required", codes)

    def test_totals_math(self):
        merchant, _ = make_merchant()
        checkout = merchant.engine.create_checkout(
            line_items=[
                {"item": {"id": "sku-1"}, "quantity": 2},  # 2000
                {"item": {"id": "sku-2"}, "quantity": 1},  # 2500
            ],  # type: ignore[list-item]
            buyer=Buyer(email="a@b.com"),
        )
        self.assertEqual(checkout.status, "ready_for_complete")
        total = next(t for t in checkout.totals if t.type == "total")
        self.assertEqual(total.amount, 4500)

    def test_out_of_stock_message(self):
        merchant, _ = make_merchant()
        result = merchant.engine.create_checkout(
            line_items=[
                {"item": {"id": "sku-oos"}, "quantity": 1},
                {"item": {"id": "sku-1"}, "quantity": 1},
            ],  # type: ignore[list-item]
            buyer=Buyer(email="a@b.com"),
        )
        codes = {m.code for m in result.messages}
        self.assertIn("out_of_stock", codes)

    def test_unknown_item_error_response(self):
        merchant, _ = make_merchant()
        result = merchant.engine.create_checkout(
            line_items=[{"item": {"id": "does-not-exist"}, "quantity": 1}]  # type: ignore[list-item]
        )
        # ErrorResponse has no 'status' attribute
        self.assertFalse(hasattr(result, "status"))
        self.assertEqual(result.ucp.status, "error")

    def test_full_happy_path(self):
        merchant, adapter = make_merchant(require=("email", "phone_number"))
        checkout = merchant.engine.create_checkout(
            line_items=[{"item": {"id": "sku-1"}, "quantity": 3}],  # type: ignore[list-item]
        )
        self.assertEqual(checkout.status, "incomplete")

        checkout = merchant.engine.update_checkout(
            checkout.id, buyer=Buyer(email="buyer@example.com", phone_number="+15551112222")
        )
        self.assertEqual(checkout.status, "ready_for_complete")

        from genko.models import Payment, PaymentInstrument

        completed = merchant.engine.complete_checkout(
            checkout.id,
            payment=Payment(
                instruments=[
                    PaymentInstrument(
                        handler_id="offline", type="offline", credential={"reference": "REF-9"}
                    )
                ]
            ),
        )
        self.assertEqual(completed.status, "completed")
        self.assertIsNotNone(completed.order)
        self.assertEqual(completed.order.id, "ORD-1")
        self.assertEqual(adapter.created[0]["reference"], "REF-9")
        self.assertEqual(adapter.created[0]["quantity"], 3)

    def test_cancel(self):
        merchant, _ = make_merchant()
        checkout = merchant.engine.create_checkout(
            line_items=[{"item": {"id": "sku-1"}, "quantity": 1}],  # type: ignore[list-item]
            buyer=Buyer(email="a@b.com"),
        )
        canceled = merchant.engine.cancel_checkout(checkout.id)
        self.assertEqual(canceled.status, "canceled")
        # Cannot cancel twice
        again = merchant.engine.cancel_checkout(checkout.id)
        self.assertEqual(again.ucp.status, "error")


class TransportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.merchant, self.adapter = make_merchant(require=("email",), enable_mcp=True)
        app = FastAPI()
        app.include_router(self.merchant.rest_router)
        app.include_router(self.merchant.mcp_router)
        app.include_router(self.merchant.well_known_router)
        self.client = TestClient(app)

    def test_well_known_profile(self):
        resp = self.client.get("/.well-known/ucp")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertIn("dev.ucp.shopping.checkout", body["ucp"]["capabilities"])
        transports = {s["transport"] for s in body["ucp"]["services"]["dev.ucp.shopping"]}
        self.assertEqual(transports, {"rest", "mcp"})

    def test_rest_only_profile_when_mcp_disabled(self):
        merchant, _ = make_merchant(enable_mcp=False)
        app = FastAPI()
        app.include_router(merchant.well_known_router)
        client = TestClient(app)
        body = client.get("/.well-known/ucp").json()
        transports = {s["transport"] for s in body["ucp"]["services"]["dev.ucp.shopping"]}
        self.assertEqual(transports, {"rest"})

    def test_rest_happy_path(self):
        create = self.client.post(
            "/ucp/v1/checkout-sessions",
            json={
                "line_items": [{"item": {"id": "sku-1"}, "quantity": 2}],
                "buyer": {"email": "rest@example.com"},
            },
        )
        self.assertEqual(create.status_code, 200)
        checkout_id = create.json()["id"]
        self.assertEqual(create.json()["status"], "ready_for_complete")

        complete = self.client.post(
            f"/ucp/v1/checkout-sessions/{checkout_id}/complete",
            json={
                "payment": {
                    "instruments": [
                        {"handler_id": "offline", "type": "offline", "credential": {"reference": "R1"}}
                    ]
                }
            },
        )
        self.assertEqual(complete.status_code, 200)
        self.assertEqual(complete.json()["status"], "completed")
        self.assertEqual(complete.json()["order"]["id"], "ORD-1")

    def test_rest_not_found(self):
        resp = self.client.get("/ucp/v1/checkout-sessions/nope")
        self.assertEqual(resp.status_code, 404)

    def test_mcp_dual_output(self):
        list_resp = self.client.post(
            "/ucp/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        )
        self.assertEqual(list_resp.status_code, 200)
        names = {t["name"] for t in list_resp.json()["result"]["tools"]}
        self.assertIn("create_checkout", names)

        call_resp = self.client.post(
            "/ucp/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "search_products", "arguments": {}},
            },
        )
        result = call_resp.json()["result"]
        self.assertIn("structuredContent", result)
        self.assertIn("content", result)
        # content[] is a serialized copy of structuredContent
        serialized = json.loads(result["content"][0]["text"])
        self.assertEqual(serialized, result["structuredContent"])


if __name__ == "__main__":
    unittest.main()
