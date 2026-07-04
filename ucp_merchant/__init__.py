"""ucp-merchant: drop-in Universal Commerce Protocol server for Python stores."""

from __future__ import annotations

from .adapter import MerchantAdapter
from .app import UCPMerchant
from .catalog import product_to_catalog
from .engine import CheckoutEngine
from .platform import (
    HttpPlatformClient,
    PaymentAuthorization,
    PlatformClient,
    PlatformError,
    verify_authorization,
)
from .models import (
    CATALOG_LOOKUP_CAPABILITY,
    CATALOG_SEARCH_CAPABILITY,
    CHECKOUT_CAPABILITY,
    ORDER_CAPABILITY,
    UCP_VERSION,
    Buyer,
    Checkout,
    ErrorResponse,
    Item,
    LineItem,
    LineItemRequest,
    Link,
    Message,
    OrderConfirmation,
    Payment,
    PaymentInstrument,
    Product,
    Total,
    UcpEnvelope,
)

__version__ = "0.1.0"

__all__ = [
    "UCPMerchant",
    "MerchantAdapter",
    "CheckoutEngine",
    "PlatformClient",
    "HttpPlatformClient",
    "PaymentAuthorization",
    "PlatformError",
    "verify_authorization",
    "UCP_VERSION",
    "CHECKOUT_CAPABILITY",
    "CATALOG_SEARCH_CAPABILITY",
    "CATALOG_LOOKUP_CAPABILITY",
    "ORDER_CAPABILITY",
    "product_to_catalog",
    "Product",
    "Item",
    "LineItem",
    "LineItemRequest",
    "Total",
    "Buyer",
    "Link",
    "Message",
    "Payment",
    "PaymentInstrument",
    "Checkout",
    "OrderConfirmation",
    "ErrorResponse",
    "UcpEnvelope",
]
