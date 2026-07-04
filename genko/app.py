"""The one-class entry point: :class:`UCPMerchant`.

Wire an existing store into UCP in three lines::

    ucp = UCPMerchant(store_name="My Store", base_url="https://mystore.com",
                      adapter=MyAdapter())
    app.include_router(ucp.rest_router)
    app.include_router(ucp.mcp_router)
    app.include_router(ucp.well_known_router)
"""

from __future__ import annotations

from fastapi import APIRouter

from .adapter import MerchantAdapter
from .engine import CheckoutEngine
from .mcp import build_mcp_router
from .models import (
    CATALOG_LOOKUP_CAPABILITY,
    CATALOG_SEARCH_CAPABILITY,
    CHECKOUT_CAPABILITY,
    ORDER_CAPABILITY,
    UCP_VERSION,
    Link,
)
from .platform import HttpPlatformClient, PlatformClient
from .profile import build_business_profile, default_payment_handlers
from .rest import build_rest_router


class UCPMerchant:
    def __init__(
        self,
        *,
        store_name: str,
        base_url: str,
        adapter: MerchantAdapter,
        currency: str = "USD",
        version: str = UCP_VERSION,
        links: list[Link] | None = None,
        require_buyer_fields: tuple[str, ...] = ("email",),
        rest_prefix: str = "/ucp/v1",
        mcp_path: str = "/ucp/mcp",
        storefront_url: str | None = None,
        continue_url_base: str | None = None,
        payment_handlers: dict | None = None,
        session_ttl_hours: int = 6,
        enable_order_capability: bool = False,
        platform_url: str | None = None,
        platform_api_key: str | None = None,
        platform_client: PlatformClient | None = None,
    ) -> None:
        self.store_name = store_name
        self.base_url = base_url.rstrip("/")
        self.version = version
        self.rest_prefix = rest_prefix
        self.mcp_path = mcp_path
        self.enable_order_capability = enable_order_capability
        self._payment_handlers = payment_handlers or default_payment_handlers(self.base_url, version)

        # Platform (infra) callback client, authenticated by a merchant API key.
        # Verifies + accredits the simulated payment against the platform wallet.
        if platform_client is None and platform_url and platform_api_key:
            platform_client = HttpPlatformClient(
                base_url=platform_url, api_key=platform_api_key
            )
        self.platform_client = platform_client

        capabilities = {CHECKOUT_CAPABILITY: [{"version": version}]}
        catalog_capabilities = {
            CATALOG_SEARCH_CAPABILITY: [{"version": version}],
            CATALOG_LOOKUP_CAPABILITY: [{"version": version}],
        }
        order_capabilities = (
            {ORDER_CAPABILITY: [{"version": version}]} if enable_order_capability else {}
        )

        self.engine = CheckoutEngine(
            adapter=adapter,
            currency=currency,
            version=version,
            capabilities=capabilities,
            catalog_capabilities=catalog_capabilities,
            order_capabilities=order_capabilities,
            payment_handlers=self._payment_handlers,
            links=links,
            require_buyer_fields=require_buyer_fields,
            storefront_url=continue_url_base or storefront_url or self.base_url,
            session_ttl_hours=session_ttl_hours,
            platform_client=self.platform_client,
        )

    @property
    def rest_router(self) -> APIRouter:
        return build_rest_router(
            self.engine, prefix=self.rest_prefix, enable_order=self.enable_order_capability
        )

    @property
    def mcp_router(self) -> APIRouter:
        return build_mcp_router(
            self.engine,
            path=self.mcp_path,
            server_name=self.store_name,
            enable_order=self.enable_order_capability,
        )

    @property
    def well_known_router(self) -> APIRouter:
        router = APIRouter(tags=["ucp-discovery"])

        @router.get("/.well-known/ucp")
        def well_known_ucp() -> dict:
            return self.profile()

        return router

    def profile(self) -> dict:
        return build_business_profile(
            base_url=self.base_url,
            rest_prefix=self.rest_prefix,
            mcp_path=self.mcp_path,
            version=self.version,
            payment_handlers=self._payment_handlers,
            enable_order=self.enable_order_capability,
        )
