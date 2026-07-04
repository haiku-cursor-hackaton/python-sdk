# ucp-merchant

Drop-in [Universal Commerce Protocol (UCP)](https://ucp.dev) merchant server for
any Python / FastAPI ecommerce. Implement **one small adapter class** and your
store instantly gets:

- `GET /.well-known/ucp` — the discovery profile so platforms and AI agents can
  find and negotiate with you
- the UCP **Catalog** capability under `/ucp/v1/catalog/*` (search / lookup /
  product) — the advertised way agents discover your products
- REST checkout endpoints under `/ucp/v1/*` (the five UCP checkout operations)
- an optional **Order** capability (`GET /ucp/v1/orders/{id}`)
- an MCP (JSON-RPC) endpoint at `/ucp/mcp` so AI agents (Claude, Gemini, etc.)
  can shop your store as tools

Targets UCP spec version `2026-04-08` (capabilities
`dev.ucp.shopping.catalog.search`, `dev.ucp.shopping.catalog.lookup`,
`dev.ucp.shopping.checkout`, and the optional `dev.ucp.shopping.order`; REST +
MCP transports). All amounts are integers in the currency's minor unit (cents).

## Install

```bash
pip install -e .            # from this folder
# or, in another project:
pip install -e /path/to/python-sdk
```

## Quickstart

The only thing you write is an adapter that exposes your products and knows how
to turn a completed checkout into a real order:

```python
from fastapi import FastAPI
from ucp_merchant import (
    UCPMerchant, MerchantAdapter, Product, OrderConfirmation,
)

class MyAdapter(MerchantAdapter):
    def get_products(self):
        return [Product(id="tee-black-m", title="Black Tee (M)", price=1599)]

    def create_order(self, *, line_items, buyer, totals, payment_reference=None):
        order = my_store.place_order(buyer.email, line_items)  # your code
        return OrderConfirmation(
            id=order.id, permalink_url=f"https://mystore.com/orders/{order.id}",
        )

ucp = UCPMerchant(
    store_name="My Store",
    base_url="https://mystore.com",
    adapter=MyAdapter(),
    currency="USD",
    require_buyer_fields=("email",),
)

app = FastAPI()
app.include_router(ucp.rest_router)         # /ucp/v1/*
app.include_router(ucp.mcp_router)          # /ucp/mcp
app.include_router(ucp.well_known_router)   # /.well-known/ucp
```

That's it — your store is now UCP-compliant.

## The adapter contract

`MerchantAdapter` (see [`ucp_merchant/adapter.py`](ucp_merchant/adapter.py)):

| Method | Required | Purpose |
| --- | --- | --- |
| `get_products()` | yes | Return all purchasable `Product`s |
| `get_product(item_id)` | no | Direct SKU lookup (default scans `get_products`) |
| `price(line_items)` | no | Return `totals` breakdown (default sums subtotals) |
| `create_order(...)` | yes | Persist a real order on checkout completion |
| `get_order(order_id)` | no | Resolve a previously created order |

Encode variants (size/color) into the `Product.id` — that id is the SKU both the
platform and your store recognize.

## Checkout lifecycle

The SDK owns the UCP status lifecycle so you don't have to:

```
incomplete ──(all required info)──▶ ready_for_complete ──(complete)──▶ completed
    │                                                                     
    └── missing buyer fields / line items surface as `messages`           
canceled ◀── cancel / expiry (from any non-terminal state)
```

Unknown or out-of-stock items produce standard UCP error messages
(`item_unavailable`, `out_of_stock`). Missing required buyer fields produce
`field_required` messages and keep the checkout `incomplete`.

## Payments

Ships with a pluggable **offline payment handler** (`com.ucp-merchant.offline_payment`):
the platform passes an opaque payment reference as the credential and the order
is created in a pending-payment state for the merchant to reconcile. Pass your
own `payment_handlers=` to advertise a real PSP (Stripe, Google Pay, ...).

**Platform accreditation (optional).** If you're running behind a platform/gateway
that issues you an API key, configure `platform_url=` + `platform_api_key=`. On
`complete`, the SDK verifies the platform's payment authorization (status, amount,
currency) and, once your order is placed, **accredits** it so the platform credits
your balance — releasing the reservation if order creation fails. Without these,
the SDK stays a pure offline handler. See
[`docs/PLATFORM_INTEGRATION.md`](docs/PLATFORM_INTEGRATION.md) §7b.

## Try it end to end

```bash
pip install -e ".[examples]"
uvicorn examples.demo_store:app --port 8100      # terminal 1
python examples/agent_client.py                  # terminal 2 — scripted agent
```

The agent discovers the profile, lists tools, searches products, creates a
checkout, adds buyer details, and completes the order — all over MCP.

## Tests

```bash
pip install -e ".[dev]"
python -m unittest discover tests -v
```

## Transports

| Transport | Endpoint | Notes |
| --- | --- | --- |
| REST (catalog) | `POST /ucp/v1/catalog/{search,lookup,product}` | Official Catalog capability; all business outcomes are HTTP 200 |
| REST (checkout) | `POST/GET/PUT /ucp/v1/checkout-sessions[/{id}][/complete|/cancel]` | Standard HTTP verbs + JSON |
| REST (order) | `GET /ucp/v1/orders/{id}` | Only when `enable_order_capability=True` |
| MCP | `POST /ucp/mcp` | JSON-RPC `initialize`, `tools/list`, `tools/call`; dual `structuredContent` + `content[]` output |
| Discovery | `GET /.well-known/ucp` | Business profile with services, capabilities, payment handlers |

MCP tools: `search_products`, `lookup_products`, `get_product`,
`create_checkout`, `get_checkout`, `update_checkout`, `complete_checkout`,
`cancel_checkout` (+ `get_order` when the Order capability is enabled).

## Full spec

- **[`docs/SDK_PRD.md`](docs/SDK_PRD.md)** — the complete product spec: every
  endpoint (REST + MCP) with request/response shapes, the data models, the
  checkout lifecycle/state machine, payments, config options, and acceptance
  criteria.

## Building a platform / gateway on top of this?

If you're building the multi-tenant **MCP Gateway / infra** that fans out to
many merchants, read the handoff contract:
**[`docs/PLATFORM_INTEGRATION.md`](docs/PLATFORM_INTEGRATION.md)**. It documents
the final REST surface, the MCP tool ↔ REST mapping, request/response shapes, the
simulated-payment instrument contract, and exactly what is / isn't enforced yet.

### Catalog capability

`POST /ucp/v1/catalog/search` accepts `query`, `filters` (`categories`,
`price.{min,max}` in minor units) and `pagination` (`limit`, opaque `cursor`),
returning `{ ucp, products, pagination }`. `POST /ucp/v1/catalog/lookup` takes
`ids` and returns the matches (each variant annotated with `inputs`), reporting
unknown ids as `info`/`not_found` messages. `POST /ucp/v1/catalog/product` takes
a single `id` and returns a singular `product`, or a `status: "error"` envelope
with an unrecoverable `not_found` message (still HTTP 200). Each SDK `Product`
maps to a Catalog Product with one variant whose `id` is the checkout
`line_items[].item.id`; use `product_to_catalog(product, inputs=...)` to build
the document.

`GET /ucp/v1/products` remains as a **non-standard** convenience feed for
debugging/examples and is **not** advertised in the discovery profile.

## License

Apache-2.0.
