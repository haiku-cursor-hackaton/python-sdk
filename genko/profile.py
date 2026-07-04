"""Discovery profile generation for ``/.well-known/ucp``.

The business profile advertises the protocol version, transport bindings
(``services``), supported ``capabilities``, and ``payment_handlers`` so that
platforms/agents can negotiate and know where to send requests.
"""

from __future__ import annotations

from .models import (
    CATALOG_LOOKUP_CAPABILITY,
    CATALOG_SEARCH_CAPABILITY,
    CHECKOUT_CAPABILITY,
    ORDER_CAPABILITY,
    SHOPPING_SERVICE,
    UCP_VERSION,
)


def default_payment_handlers(base_url: str, version: str) -> dict:
    """A pluggable offline / manual payment handler.

    The buyer's platform passes an opaque payment reference (bank transfer
    reference, COD keyword, etc.) as the credential; the order is created in a
    pending-payment state for the merchant to reconcile. Replace with a real
    PSP handler (Stripe, Google Pay, ...) when available.
    """

    return {
        "com.genko.offline_payment": [
            {
                "id": "offline",
                "version": version,
                "spec": f"{base_url}/ucp/payment/offline",
                "schema": f"{base_url}/ucp/payment/offline.json",
                "available_instruments": [{"type": "offline"}],
                "config": {
                    "instructions": "Provide a payment reference; order is created pending manual payment review.",
                },
            }
        ]
    }


def build_capabilities(version: str, *, enable_order: bool = False) -> dict:
    """Assemble the advertised UCP capabilities for the discovery profile."""
    capabilities = {
        CHECKOUT_CAPABILITY: [
            {
                "version": version,
                "spec": "https://ucp.dev/specification/checkout",
                "schema": f"https://ucp.dev/{version}/schemas/shopping/checkout.json",
            }
        ],
        CATALOG_SEARCH_CAPABILITY: [
            {
                "version": version,
                "spec": f"https://ucp.dev/{version}/specification/catalog/search",
                "schema": f"https://ucp.dev/{version}/schemas/shopping/catalog_search.json",
            }
        ],
        CATALOG_LOOKUP_CAPABILITY: [
            {
                "version": version,
                "spec": f"https://ucp.dev/{version}/specification/catalog/lookup",
                "schema": f"https://ucp.dev/{version}/schemas/shopping/catalog_lookup.json",
            }
        ],
    }
    if enable_order:
        capabilities[ORDER_CAPABILITY] = [
            {
                "version": version,
                "spec": f"https://ucp.dev/{version}/specification/order",
                "schema": f"https://ucp.dev/{version}/schemas/shopping/order.json",
            }
        ]
    return capabilities


def build_business_profile(
    *,
    base_url: str,
    rest_prefix: str,
    mcp_path: str,
    version: str = UCP_VERSION,
    payment_handlers: dict | None = None,
    enable_order: bool = False,
) -> dict:
    base_url = base_url.rstrip("/")
    rest_endpoint = f"{base_url}{rest_prefix}"
    mcp_endpoint = f"{base_url}{mcp_path}"
    handlers = payment_handlers or default_payment_handlers(base_url, version)

    return {
        "ucp": {
            "version": version,
            "services": {
                SHOPPING_SERVICE: [
                    {
                        "version": version,
                        "spec": "https://ucp.dev/specification/overview",
                        "transport": "rest",
                        "endpoint": rest_endpoint,
                        "schema": f"https://ucp.dev/{version}/services/shopping/rest.openapi.json",
                    },
                    {
                        "version": version,
                        "spec": "https://ucp.dev/specification/overview",
                        "transport": "mcp",
                        "endpoint": mcp_endpoint,
                        "schema": f"https://ucp.dev/{version}/services/shopping/mcp.openrpc.json",
                    },
                ]
            },
            "capabilities": build_capabilities(version, enable_order=enable_order),
            "payment_handlers": handlers,
        }
    }
