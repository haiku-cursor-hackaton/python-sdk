from __future__ import annotations

import unittest

from ucp_merchant import (
    MerchantAdapter,
    OrderConfirmation,
    Payment,
    PaymentAuthorization,
    PlatformClient,
    PlatformError,
    Product,
    UCPMerchant,
)


class FakeAdapter(MerchantAdapter):
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.created = 0

    def get_products(self) -> list[Product]:
        return [Product(id="sku-1", title="Widget", price=1000)]

    def create_order(self, *, line_items, buyer, totals, payment_reference=None) -> OrderConfirmation:
        if self.fail:
            raise ValueError("warehouse offline")
        self.created += 1
        return OrderConfirmation(id="ORD-1", permalink_url="https://store.example/orders/ORD-1")


class StubPlatform(PlatformClient):
    """In-memory platform double recording verify/accredit/release calls."""

    def __init__(self, *, status="reserved", amount_minor=2000, currency="USD") -> None:
        self.auth = PaymentAuthorization(
            id="auth_1", status=status, amount_minor=amount_minor, currency=currency
        )
        self.accredited: list[dict] = []
        self.released: list[str] = []
        self.accredit_should_fail = False

    def get_authorization(self, authorization_id: str) -> PaymentAuthorization:
        return self.auth

    def accredit(self, authorization_id, *, order_id, amount_minor, currency, idempotency_key=None):
        if self.accredit_should_fail:
            raise PlatformError("upstream_timeout", "settle timed out")
        self.accredited.append(
            {"auth": authorization_id, "order": order_id, "amount": amount_minor, "currency": currency}
        )
        return {"status": "completed", "transaction_id": "txn_1"}

    def release(self, authorization_id, *, reason=None) -> None:
        self.released.append(authorization_id)


def make_merchant(platform: PlatformClient | None, *, fail_order=False):
    adapter = FakeAdapter(fail=fail_order)
    merchant = UCPMerchant(
        store_name="Test Store",
        base_url="https://store.example",
        adapter=adapter,
        require_buyer_fields=(),
        platform_client=platform,
    )
    return merchant, adapter


def _completed_checkout(merchant, *, reference="auth_1"):
    checkout = merchant.engine.create_checkout(
        line_items=[{"item": {"id": "sku-1"}, "quantity": 2}]  # type: ignore[list-item]
    )
    payment = {"instruments": [{"handler_id": "offline", "type": "offline", "credential": {"reference": reference}}]}
    return merchant.engine.complete_checkout(checkout.id, payment=Payment.model_validate(payment))


class PlatformAccreditationTests(unittest.TestCase):
    def test_happy_path_verifies_and_accredits(self):
        platform = StubPlatform(amount_minor=2000)
        merchant, adapter = make_merchant(platform)
        result = _completed_checkout(merchant)

        self.assertEqual(result.status, "completed")
        self.assertEqual(adapter.created, 1)
        self.assertEqual(len(platform.accredited), 1)
        self.assertEqual(platform.accredited[0]["order"], "ORD-1")
        self.assertEqual(platform.accredited[0]["amount"], 2000)
        self.assertEqual(platform.released, [])

    def test_amount_mismatch_blocks_order(self):
        platform = StubPlatform(amount_minor=999)  # != 2000 order total
        merchant, adapter = make_merchant(platform)
        result = _completed_checkout(merchant)

        # ErrorResponse, no order placed, no accreditation.
        self.assertEqual(result.ucp.status, "error")
        self.assertTrue(any(m.code == "payment_amount_mismatch" for m in result.messages))
        self.assertEqual(adapter.created, 0)
        self.assertEqual(platform.accredited, [])

    def test_non_capturable_status_blocks_order(self):
        platform = StubPlatform(status="completed")
        merchant, adapter = make_merchant(platform)
        result = _completed_checkout(merchant)
        self.assertEqual(result.ucp.status, "error")
        self.assertTrue(any(m.code == "payment_authorization_invalid" for m in result.messages))
        self.assertEqual(adapter.created, 0)

    def test_missing_reference_is_declined(self):
        platform = StubPlatform()
        merchant, _ = make_merchant(platform)
        checkout = merchant.engine.create_checkout(
            line_items=[{"item": {"id": "sku-1"}, "quantity": 2}]  # type: ignore[list-item]
        )
        result = merchant.engine.complete_checkout(checkout.id)  # no payment
        self.assertEqual(result.ucp.status, "error")
        self.assertTrue(any(m.code == "payment_declined" for m in result.messages))

    def test_order_failure_releases_authorization(self):
        platform = StubPlatform()
        merchant, adapter = make_merchant(platform, fail_order=True)
        result = _completed_checkout(merchant)
        self.assertEqual(result.ucp.status, "error")
        self.assertTrue(any(m.code == "payment_failed" for m in result.messages))
        self.assertEqual(platform.released, ["auth_1"])
        self.assertEqual(platform.accredited, [])

    def test_accredit_failure_completes_but_flags_reconciliation(self):
        platform = StubPlatform()
        platform.accredit_should_fail = True
        merchant, adapter = make_merchant(platform)
        result = _completed_checkout(merchant)
        # Order was placed; checkout completes with a reconciliation warning.
        self.assertEqual(result.status, "completed")
        self.assertEqual(adapter.created, 1)
        self.assertTrue(any(m.code == "reconciliation_required" for m in result.messages))

    def test_no_platform_client_keeps_offline_behavior(self):
        merchant, adapter = make_merchant(None)
        result = _completed_checkout(merchant)
        self.assertEqual(result.status, "completed")
        self.assertEqual(adapter.created, 1)


if __name__ == "__main__":
    unittest.main()
