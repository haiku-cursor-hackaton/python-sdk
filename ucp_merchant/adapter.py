"""The merchant integration contract.

To make an existing store UCP-compliant, subclass :class:`MerchantAdapter` and
implement (at minimum) :meth:`get_products` and :meth:`create_order`. Everything
else in the SDK (session lifecycle, protocol envelopes, REST + MCP transports,
discovery profile) is handled for you.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from .models import Buyer, LineItem, OrderConfirmation, Product, Total


class MerchantAdapter(ABC):
    """Interface a store implements to expose itself over UCP."""

    @abstractmethod
    def get_products(self) -> list[Product]:
        """Return the full list of purchasable products."""

    def get_product(self, item_id: str) -> Product | None:
        """Resolve a single product by id.

        Default implementation scans :meth:`get_products`. Override for stores
        with large catalogs where a direct lookup is cheaper.
        """
        for product in self.get_products():
            if product.id == item_id:
                return product
        return None

    def price(self, line_items: list[LineItem]) -> list[Total]:
        """Compute the ``totals`` breakdown for the given line items.

        Default implementation sums line-item subtotals. Override to add tax,
        shipping, discounts, or other charges.
        """
        subtotal = sum(li.item.price * li.quantity for li in line_items)
        return [
            Total(type="subtotal", display_text="Subtotal", amount=subtotal),
            Total(type="total", display_text="Total", amount=subtotal),
        ]

    @abstractmethod
    def create_order(
        self,
        *,
        line_items: list[LineItem],
        buyer: Buyer,
        totals: list[Total],
        payment_reference: str | None = None,
    ) -> OrderConfirmation:
        """Persist a real order and return its confirmation.

        Called by the engine when a checkout is completed. Implementations
        should create the order in the store's own system and return an
        :class:`OrderConfirmation` with a stable id and a permalink the buyer
        can use to view/track the order.
        """

    def get_order(self, order_id: str) -> OrderConfirmation | None:  # noqa: ARG002
        """Optional: resolve a previously created order. Returns ``None`` by default."""
        return None
