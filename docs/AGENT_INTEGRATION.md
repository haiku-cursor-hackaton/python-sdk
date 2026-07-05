# Wire Genko SDK into a Store

Guide for AI agents helping a merchant add UCP to an existing Python/FastAPI
ecommerce backend. Works with **any** coding assistant (Cursor, Codex, Claude
Code, ChatGPT, etc.) — plain Markdown, no vendor-specific format.

**Production model:** stores expose **REST only** (`enable_mcp=False`, default).
End-user agents connect to the [Genko platform backend](https://github.com/haiku-cursor-hackaton/backend)
(`POST /mcp`), which proxies to each store's `/ucp/v1/*`.

## Before you start

Read these only if you need detail beyond this guide:

- Adapter + lifecycle: [`genko/adapter.py`](../genko/adapter.py)
- Reference store: [Lithe `ucp_adapter.py`](https://github.com/haiku-cursor-hackaton/Lithe-Hackathon/blob/main/backend/app/ucp_adapter.py)
- Platform contract: [`PLATFORM_INTEGRATION.md`](PLATFORM_INTEGRATION.md)
- Full spec: [`SDK_PRD.md`](SDK_PRD.md)

## Integration checklist

Copy and track progress:

```
- [ ] 1. Install genko-sdk
- [ ] 2. Implement MerchantAdapter (products + create_order)
- [ ] 3. Wire UCPMerchant in FastAPI main (REST + discovery only)
- [ ] 4. Set PUBLIC_BASE_URL and optional env vars
- [ ] 5. Enable order capability if gateway needs get_order
- [ ] 6. Register store on Genko platform (production)
- [ ] 7. Verify /.well-known/ucp and a REST checkout smoke test
```

---

## 1. Install

**Local / monorepo development:**

```bash
pip install -e /path/to/python-sdk
# or from requirements.txt:
# -e ../../python-sdk
```

**Docker / CI (no local SDK path):**

```text
genko-sdk @ git+https://github.com/haiku-cursor-hackaton/python-sdk.git@<commit-sha>
```

Pin a commit SHA in production images so Docker layer caches stay reproducible.

Requirements: Python ≥ 3.10, FastAPI ≥ 0.100, Pydantic ≥ 2.

---

## 2. Implement MerchantAdapter

Create one class (e.g. `app/ucp_adapter.py`) that bridges your catalog and order
pipeline. **Do not** reimplement checkout session logic — the SDK engine owns
that.

### Required methods

| Method | Responsibility |
| --- | --- |
| `get_products()` | Return `list[Product]` — every purchasable SKU |
| `create_order(...)` | Persist a real order; return `OrderConfirmation` |

### Strongly recommended

| Method | When |
| --- | --- |
| `get_product(item_id)` | Large catalogs — avoid scanning all products |
| `get_order(order_id)` | When `enable_order_capability=True` |
| `on_payment_accredited(...)` | When `platform_url` + `platform_api_key` are set — mark order paid |

### Product rules

- **`Product.id`** is the checkout SKU (`line_items[].item.id`). Encode variants
  in the id (e.g. `design-slug__M__black`).
- **`price`** is integer **minor units** (USD cents). Never floats.
- Set **`available=False`** for out-of-stock items (SDK returns `out_of_stock`).
- Add **`description`** and **`attributes`** (e.g. `keywords`, `category`) so
  agent search matches natural queries ("shirt", "tee").

### create_order rules

- Validate buyer fields your store requires (email, phone, etc.) — raise
  `ValueError` with a clear message if missing.
- Use `payment_reference` from the platform's offline instrument credential when
  recording payment metadata.
- Return `OrderConfirmation(id=..., permalink_url=..., status=...,
  payment_status=...)` so agents and the platform see a rich snapshot.
- **Single order-creation path:** call your existing `create_order` repository /
  service — never duplicate order logic for UCP vs storefront.

### Minimal adapter skeleton

```python
from genko import Buyer, LineItem, MerchantAdapter, OrderConfirmation, Product, Total

class MyStoreAdapter(MerchantAdapter):
    def get_products(self) -> list[Product]:
        return [
            Product(
                id="tee-black-m",
                title="Black Tee (M)",
                price=1599,
                currency="USD",
                description="Classic cotton tee, size M.",
                attributes={"keywords": "shirt tee apparel", "category": "apparel"},
            )
        ]

    def create_order(
        self,
        *,
        line_items: list[LineItem],
        buyer: Buyer,
        totals: list[Total],
        payment_reference: str | None = None,
    ) -> OrderConfirmation:
        if not buyer.email:
            raise ValueError("A buyer email is required.")
        order = my_store.create_order_from_ucp(...)  # your existing pipeline
        return OrderConfirmation(
            id=order.number,
            permalink_url=f"https://mystore.com/orders/{order.number}",
            status="pending_payment",
            payment_status="pending",
        )
```

---

## 3. Wire UCPMerchant in FastAPI

Mount **before** any SPA catch-all route so `/.well-known/ucp` and `/ucp/v1/*`
resolve correctly.

```python
from fastapi import Depends, FastAPI, Request
from genko import UCPMerchant

from .ucp_adapter import MyStoreAdapter
from .config import settings

app = FastAPI()

if settings.ucp_enabled:
    genko = UCPMerchant(
        store_name="My Store",
        base_url=settings.public_base_url.rstrip("/"),
        adapter=MyStoreAdapter(settings.public_base_url),
        currency="USD",
        require_buyer_fields=("email", "phone_number"),  # match your store rules
        enable_order_capability=True,   # recommended for Genko gateway (9 tools)
        enable_mcp=False,             # production default — REST only
        platform_url=settings.ucp_platform_url,
        platform_api_key=settings.ucp_platform_api_key,
        api_keys=settings.ucp_gateway_api_keys,  # optional inbound gate
        continue_url_base=f"{settings.public_base_url}/products",
    )
    app.include_router(genko.well_known_router)
    app.include_router(genko.rest_router, dependencies=[Depends(enforce_rate_limit)])
    # Do NOT mount genko.mcp_router in production.
```

### UCPMerchant options (common)

| Parameter | Typical value |
| --- | --- |
| `base_url` | Public HTTPS origin (`PUBLIC_BASE_URL`) |
| `require_buyer_fields` | Fields required before `ready_for_complete` |
| `enable_order_capability` | `True` for full gateway tool parity |
| `enable_mcp` | `False` (production); `True` only for local demos |
| `platform_url` / `platform_api_key` | Genko platform — enables paid flow + accreditation |
| `api_keys` | Vendor inbound key(s) — Genko gateway sends `Authorization: Bearer` |
| `rest_prefix` | Default `/ucp/v1` — rarely change |

---

## 4. Environment variables

Add to `.env` / deployment config:

```env
# Required for correct discovery, permalinks, and image URLs
PUBLIC_BASE_URL=https://your-store.example.com

# Optional: Genko platform (paid agent orders + wallet accreditation)
UCP_PLATFORM_URL=https://your-genko-platform.example.com
UCP_PLATFORM_API_KEY=gk_sdk_...   # from POST /v1/merchants/register

# Optional: restrict /ucp/v1/* to the Genko gateway only
UCP_GATEWAY_API_KEY=gk_vendor_... # comma-separated for rotation
```

| Variable | Direction | Purpose |
| --- | --- | --- |
| `UCP_PLATFORM_*` | Store → platform | SDK verifies + accredits payment on complete |
| `UCP_GATEWAY_API_KEY` | Platform → store | Inbound Bearer gate on REST operations |

Discovery (`GET /.well-known/ucp`) stays **public** even when `api_keys` is set.

When `UCP_PLATFORM_*` is unset, checkout completes as **offline** (order stays
`pending_payment`).

---

## 5. Register on Genko platform (production)

After the store is deployed and `/.well-known/ucp` is reachable:

```http
POST https://<genko-platform>/v1/merchants/register
Authorization: Bearer <supabase-user-jwt>
Content-Type: application/json

{ "root_url": "https://your-store.example.com" }
```

Response includes `sdk_api_key` → set as `UCP_PLATFORM_API_KEY`.

End users connect via `POST /v1/connect/client` → use returned `mcp_url` +
`mcp_api_key` with Codex or `scripts/genko_mcp_stdio.py`.

> **Platform gap:** ensure the platform sends `Authorization: Bearer
> <UCP_GATEWAY_API_KEY>` on outbound REST. Without it, a gated store returns 401.

---

## 6. Verify

Run after wiring:

```bash
# Discovery — should list transport: rest only (no mcp)
curl -s https://your-store.example.com/.well-known/ucp | jq '.ucp.services'

# Catalog search (with vendor key if gated)
curl -s https://your-store.example.com/ucp/v1/catalog/search \
  -H "Authorization: Bearer $UCP_GATEWAY_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"query":"shirt"}'

# SDK unit tests (from python-sdk repo)
pip install -e ".[dev]"
python -m unittest discover tests -v
```

Add store-specific tests (see Lithe `tests/test_ucp_flow.py`) that exercise
create → update buyer → complete → order lookup against your adapter.

---

## Common mistakes

| Mistake | Fix |
| --- | --- |
| Mounting UCP routers after SPA `/{path}` catch-all | Mount UCP routers **first** |
| Mounting `mcp_router` in production | REST only; platform owns MCP |
| Float prices | Use integer minor units |
| Separate UCP order path | Reuse existing order creation |
| Missing `PUBLIC_BASE_URL` in prod | Broken discovery URLs and permalinks |
| Postgres enum `.name` vs `.value` | SQLAlchemy `SqlEnum` needs `values_callable` for PG |
| Forgetting `enable_order_capability` | Gateway `get_order` tool fails capability check |

---

## Scope boundaries

**In scope:** merchant-side SDK install, adapter, FastAPI wiring, env vars,
verification.

**Out of scope:** building the Genko platform backend itself — see
[`haiku-cursor-hackaton/backend`](https://github.com/haiku-cursor-hackaton/backend).

When integrating UCP into a store, follow this checklist end-to-end. Prefer
matching patterns from the Lithe reference implementation over inventing new
abstractions.
