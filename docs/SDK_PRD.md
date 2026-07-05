# PRD — Genko SDK (`genko`)

**Version:** MVP Hackathon
**UCP target:** `2026-04-08`
**MCP target:** `2024-11-05` (JSON-RPC lifecycle) — tools mapped 1:1 to UCP operations
**Runtime:** Python 3.10+ / FastAPI + Pydantic v2
**Reference integration:** Lithe (`Lithe-Hackathon/backend`)
**Companion doc:** [`PLATFORM_INTEGRATION.md`](PLATFORM_INTEGRATION.md) (gateway/infra handoff)

---

## 1. Product objective

Provide a **drop-in library** that makes any Python/FastAPI ecommerce
**UCP-compliant** by implementing a single small adapter class. The SDK owns all
protocol concerns — discovery profile, checkout session lifecycle, envelopes,
error semantics, REST + MCP transports, and the catalog/order capabilities — so a
merchant only writes store-specific logic (products, pricing, order creation).

```
AI agent / harness ──▶ (Genko MCP Gateway) ──UCP REST──▶ Genko SDK ──adapter──▶ store backend
```

The SDK sits at the **merchant tier**. It does not implement a gateway, wallet,
or multi-tenant routing (that is the platform's job — see the gateway PRD). It
exposes one merchant over standard UCP.

### 1.1 Design principles

- **One adapter, everything else handled.** Implement `get_products` +
  `create_order`; get 9 UCP operations over 2 transports for free.
- **Transport-agnostic core.** A single `CheckoutEngine` holds all logic; REST
  and MCP are thin bindings over it. Identical behavior on both.
- **Spec-faithful.** Capability names, envelope shape, message severities, and
  the error model follow UCP `2026-04-08`. Unknown fields are ignored (additive
  versioning).
- **Minor units only.** All money is an integer in ISO-4217 minor units (cents).
  Never floats, never currency conversion.
- **Business outcomes are not transport errors.** Out-of-stock, missing buyer
  info, unknown product → HTTP 200 with a UCP envelope + `messages[]`.

---

## 2. Scope

### In scope
- Discovery profile at `GET /.well-known/ucp`.
- **Catalog** capability: search, lookup, product detail.
- **Checkout** capability: create, get, update, complete, cancel.
- **Order** capability (optional): get order.
- REST transport (RESTful HTTP) and MCP transport (JSON-RPC) — feature-parity.
- Pluggable payment handlers (ships an offline/manual handler).
- **Platform accreditation client** (`PlatformClient`): using a merchant API key,
  verify + accredit (settle) the payment authorization against the platform.
- Rich post-purchase order snapshot (status/payment_status/totals/line_items).
- In-memory checkout session store with TTL expiry.
- Configurable required buyer fields with structured validation messages.
- Pydantic models + `product_to_catalog` mapping helper.
- Test suite + runnable demo store + scripted agent example.

### Out of scope (SDK)
- Multi-tenant gateway / merchant routing (`merchant_url`).
- The simulated **wallet, balance reservation, and settlement** ledger itself —
  that lives platform-side. The SDK only *calls* the platform to verify/accredit.
- Real payment processing / PSP integration (bring your own handler).
- Persistence of checkout sessions across process restarts (in-memory MVP).
- Auth/authorization middleware on UCP routes (host app decides).
- Cart capability, Identity Linking, AP2, refunds.

---

## 3. Architecture / modules

| Module | Responsibility |
| --- | --- |
| `genko/app.py` | `UCPMerchant` — the one-class entry point; builds the 3 routers + profile |
| `genko/adapter.py` | `MerchantAdapter` ABC — the merchant integration contract |
| `genko/engine.py` | `CheckoutEngine` — transport-agnostic core: sessions, lifecycle, catalog/order ops, envelopes |
| `genko/rest.py` | `build_rest_router` — RESTful HTTP binding |
| `genko/mcp.py` | `build_mcp_router` — JSON-RPC (MCP) binding |
| `genko/profile.py` | discovery profile + capability + default payment handler builders |
| `genko/catalog.py` | `product_to_catalog` — SDK `Product` → UCP Catalog Product document |
| `genko/platform.py` | `PlatformClient` + `HttpPlatformClient` — API-key callback to verify/accredit simulated payments |
| `genko/models.py` | Pydantic models + capability/version constants |

Request flow: `HTTP/JSON-RPC → router → CheckoutEngine method → MerchantAdapter callbacks → UCP envelope out`.

---

## 4. Public API — `UCPMerchant`

```python
UCPMerchant(
    *,
    store_name: str,                       # server/display name (MCP serverInfo)
    base_url: str,                         # public base URL; used to build endpoints & handler specs
    adapter: MerchantAdapter,              # your store implementation
    currency: str = "USD",                 # ISO-4217; amounts are minor units
    version: str = "2026-04-08",           # UCP version advertised
    links: list[Link] | None = None,       # e.g. terms_of_service, privacy_policy
    require_buyer_fields: tuple[str, ...] = ("email",),  # gates ready_for_complete
    rest_prefix: str = "/ucp/v1",          # REST mount prefix
    mcp_path: str = "/ucp/mcp",            # MCP endpoint path
    storefront_url: str | None = None,     # base for continue_url
    continue_url_base: str | None = None,  # overrides storefront_url for continue_url
    payment_handlers: dict | None = None,  # advertise real PSP; defaults to offline handler
    session_ttl_hours: int = 6,            # checkout session lifetime
    enable_order_capability: bool = False, # advertise + mount Order capability
    platform_url: str | None = None,       # platform (infra) base URL for payment verify/accredit
    platform_api_key: str | None = None,   # merchant API key issued by the infra (Bearer)
    platform_client: PlatformClient | None = None,  # inject a custom/stub client (tests)
)
```

When `platform_url` + `platform_api_key` are provided (or a `platform_client` is
injected), completing a checkout verifies and accredits the payment against the
platform (see §11.1). When omitted, the SDK behaves as a pure offline handler.

Exposed routers (include into any FastAPI app):

```python
app.include_router(ucp.rest_router)        # REST catalog + checkout (+ order if enabled)
app.include_router(ucp.mcp_router)         # MCP JSON-RPC endpoint
app.include_router(ucp.well_known_router)  # GET /.well-known/ucp
ucp.profile()                              # -> dict, the discovery profile
```

---

## 5. Merchant adapter contract (`MerchantAdapter`)

| Method | Required | Signature | Purpose / default |
| --- | --- | --- | --- |
| `get_products` | **yes** | `() -> list[Product]` | All purchasable products (used by search/lookup + default resolution) |
| `get_product` | no | `(item_id: str) -> Product \| None` | Direct SKU lookup; **default** scans `get_products` |
| `price` | no | `(line_items: list[LineItem]) -> list[Total]` | Totals breakdown; **default** sums subtotals into `subtotal` + `total` |
| `create_order` | **yes** | `(*, line_items, buyer, totals, payment_reference=None) -> OrderConfirmation` | Persist a real order on completion; may `raise ValueError` → `payment_failed` |
| `get_order` | no | `(order_id: str) -> OrderConfirmation \| None` | Resolve an order for the Order capability; **default** returns `None` |

Encode variants (size/color) into `Product.id` — that id is the SKU used
everywhere (`catalog variants[].id` and `checkout line_items[].item.id`).

---

## 6. Data models (Pydantic, minor-unit money)

**`Product`** (SDK-facing, returned by adapter): `id`, `title`, `price:int`,
`currency="USD"`, `image_url?`, `description?`, `available=True`,
`attributes:dict` (e.g. `category`, `url` used by catalog filters/links).

**`Item`**: `id`, `title`, `price:int`, `image_url?`.
**`ItemRef`** (request): `id` (only required), `title?`, `price?`, `image_url?`.
**`LineItemRequest`** (in): `item: ItemRef`, `quantity>=1`, `id?`.
**`LineItem`** (out): `id`, `item: Item`, `quantity`, `totals: [Total]`.
**`Total`**: `type` (e.g. `subtotal`/`total`), `amount:int`, `display_text?`.
**`Buyer`**: `first_name?`, `last_name?`, `email?`, `phone_number?`.
**`Link`**: `type`, `url`, `title?`.
**`Message`**: `type` (`error|warning|info`), `content`, `code?`, `path?`,
`severity?` (`recoverable|requires_buyer_input|requires_buyer_review|unrecoverable`),
`content_type` (`plain|markdown`).
**`PaymentInstrument`**: `id?`, `handler_id`, `type`, `credential?:dict`,
`billing_address?`, `display?`, `selected?`.
**`Payment`**: `instruments: [PaymentInstrument]`.
**`OrderConfirmation`**: `id`, `permalink_url`, `label?`, plus optional
post-purchase snapshot fields so the platform + buyer can confirm success:
`status?`, `payment_status?`, `currency?`, `created_at?` (RFC3339), `totals?:[Total]`,
`line_items?:[LineItem]`, `messages?:[Message]`.
**`PaymentAuthorization`** (platform client): `id`, `status?`, `amount_minor?`,
`currency?`, `checkout_id?`, `raw:dict`.
**`UcpEnvelope`**: `version`, `status` (`success|error`), `capabilities:dict`,
`payment_handlers:dict`.
**`Checkout`**: `ucp`, `id`, `status`, `currency`, `line_items`, `totals`,
`buyer?`, `messages`, `links`, `expires_at?`, `continue_url?`, `payment?`,
`order?`.
**`ErrorResponse`**: `ucp`, `messages`, `continue_url?`.

Constants: `UCP_VERSION="2026-04-08"`,
`CHECKOUT_CAPABILITY="dev.ucp.shopping.checkout"`,
`CATALOG_SEARCH_CAPABILITY="dev.ucp.shopping.catalog.search"`,
`CATALOG_LOOKUP_CAPABILITY="dev.ucp.shopping.catalog.lookup"`,
`ORDER_CAPABILITY="dev.ucp.shopping.order"`, `SHOPPING_SERVICE="dev.ucp.shopping"`.

---

## 7. Endpoint reference (REST)

All paths are relative to `rest_prefix` (default `/ucp/v1`). Business outcomes are
HTTP 200 + envelope; transport/validation failures use HTTP status codes.

### 7.1 Discovery — `GET /.well-known/ucp`
Returns the business profile: UCP version, `services` (REST + MCP transport
endpoints), advertised `capabilities`, and `payment_handlers`. See §9.

### 7.2 Catalog — `POST {prefix}/catalog/search`
Capability: `dev.ucp.shopping.catalog.search`. Read, idempotent.

Request (all optional): `query:str`, `filters:{categories:[str], price:{min,max}}`,
`pagination:{limit:int, cursor:str}`, plus `context`/`signals`/`attribution`
(accepted and ignored).

Behavior: case-insensitive match on product title/id, then category + price-range
filters, then paginate (default limit `DEFAULT_CATALOG_LIMIT`, opaque cursor).

Response (HTTP 200):
```json
{
  "ucp": { "version": "2026-04-08", "status": "success",
           "capabilities": { "dev.ucp.shopping.catalog.search": [...], "dev.ucp.shopping.catalog.lookup": [...] } },
  "products": [ { "id": "...", "title": "...", "description": {"plain": "..."},
                  "price_range": {"min": {"amount": 2500, "currency": "USD"}, "max": {...}},
                  "variants": [ {"id": "<SKU>", "title": "...", "price": {"amount": 2500, "currency": "USD"}, "availability": {"available": true}} ],
                  "url": "https://...", "media": [{"type": "image", "url": "..."}] } ],
  "pagination": { "has_next_page": true, "total_count": 42, "cursor": "<opaque>" }
}
```
`variants[0].id` is the SKU to use as `checkout.line_items[].item.id`. `cursor` is
present only when `has_next_page` is true; pass it back verbatim.

### 7.3 Catalog — `POST {prefix}/catalog/lookup`
Capability: `dev.ucp.shopping.catalog.lookup`. Read, idempotent.

Request: `ids: [str]` (required), `filters?`, `context?`, `signals?`,
`attribution?`.

Response (HTTP 200): `{ "ucp": {...}, "products": [ ...each variant carries "inputs": [{"id": "<requested id>", "match": "exact"}]... ], "messages": [ {"type": "info", "code": "not_found", "content": "<missing id>"} ] }`. Unknown ids are reported as **info messages**, not errors. `messages` is omitted when everything resolved.

### 7.4 Catalog — `POST {prefix}/catalog/product`
Capability: catalog. Read, idempotent.

Request: `id: str` (required), `selected?`, `preferences?`, `filters?`, `context?`.

Response: success → `{ "ucp": {...}, "product": { ...single product... }, "messages": [] }`.
Not found → **HTTP 200** with `ucp.status: "error"` + unrecoverable `not_found` message.

### 7.5 Checkout — `POST {prefix}/checkout-sessions` (create)
Capability: `dev.ucp.shopping.checkout`. Write, non-idempotent.

Request body:
```json
{ "line_items": [ { "item": { "id": "<SKU>" }, "quantity": 2 } ],
  "buyer": { "email": "a@b.com", "phone_number": "+1..." },
  "payment": { "instruments": [ ... ] } }
```
`buyer` and `payment` optional at create. Returns the full checkout envelope (§8).
If no line item resolves → `ErrorResponse` (HTTP 422; `item_unavailable`).

### 7.6 Checkout — `GET {prefix}/checkout-sessions/{id}` (get)
Read, idempotent. Returns the full checkout snapshot. Unknown id → HTTP 404 with
`not_found` envelope. Expired non-terminal sessions read back as `canceled`.

### 7.7 Checkout — `PUT {prefix}/checkout-sessions/{id}` (update)
Write. Replace mutable state. Body: `line_items?`, `buyer?`, `payment?`.
Semantics: `line_items` is a **full replacement** when present; `buyer` fields are
**merged** (non-null overwrites). Updating a `completed`/`canceled` session →
HTTP 409 `not_allowed`. Returns the recomputed checkout.

### 7.8 Checkout — `POST {prefix}/checkout-sessions/{id}/complete`
Buy. Body: `{ "payment": { "instruments": [...] } }` (optional if already set).
- If required info still missing → status `requires_escalation` + `continue_url`;
  **no order created**.
- If `ready_for_complete` → extracts payment reference, calls
  `adapter.price` then `adapter.create_order`, sets status `completed` with an
  `order` block. `adapter` `ValueError` → `payment_failed` (recoverable).
- Idempotent by outcome: completing an already-`completed` session returns the
  same checkout + order (no duplicate order). Completing a `canceled` session →
  `not_allowed`.

### 7.9 Checkout — `POST {prefix}/checkout-sessions/{id}/cancel`
Write. Sets status `canceled`. Already terminal → HTTP 409 `not_allowed`.

### 7.10 Order — `GET {prefix}/orders/{id}` *(only when `enable_order_capability=True`)*
Capability: `dev.ucp.shopping.order`. Read, idempotent.
Success → `{ "ucp": {status:"success", capabilities:{order}}, "order": { id, permalink_url, label? } }`.
Unknown id → **HTTP 200** + `ucp.status:"error"` + `not_found` message.

### 7.11 Non-standard — `GET {prefix}/products`
Convenience feed for debugging/examples only. **Not** part of UCP, **not**
advertised in the profile. Returns `{ "ucp": {version}, "products": [...] }`.

### 7.12 REST status-code mapping
Business `ErrorResponse` → `not_found`=404, `not_allowed`=409, else 422; success=200.
Catalog/order "not found" are business outcomes and stay **200** with an error
envelope (they don't use the 404 mapping).

---

## 8. Checkout envelope & lifecycle

Envelope returned by all checkout operations:
```json
{
  "ucp": { "version": "2026-04-08", "status": "success", "capabilities": {"dev.ucp.shopping.checkout": [...]}, "payment_handlers": {...} },
  "id": "chk_<hex>", "status": "ready_for_complete", "currency": "USD",
  "line_items": [ { "id": "li_1", "item": {...}, "quantity": 2, "totals": [{"type":"subtotal","display_text":"Subtotal","amount":5000}] } ],
  "totals": [ {"type":"subtotal","amount":5000}, {"type":"total","amount":5000} ],
  "buyer": {...}, "messages": [], "links": [{"type":"terms_of_service","url":"..."}],
  "expires_at": "2026-07-04T20:00:00Z", "continue_url": "https://.../checkout/chk_...",
  "payment": null, "order": null
}
```
On `completed`: `order = { "id": "...", "permalink_url": "https://...", "label": "..." }`.

### State machine
```
incomplete ──(all required info present)──▶ ready_for_complete ──(complete)──▶ completed
   │                                              │
   │ missing buyer fields / line items            │ still missing at complete →
   │ surfaced as messages (stays incomplete)      └─▶ requires_escalation (+ continue_url, no order)
canceled ◀── cancel / TTL expiry (from any non-terminal state)
```
Statuses (`CheckoutStatus`): `incomplete`, `requires_escalation`,
`ready_for_complete`, `complete_in_progress`, `completed`, `canceled`. Sessions
are held in-memory (`chk_<hex>` ids) and expire after `session_ttl_hours` (default
6h); expired non-terminal sessions become `canceled` on next read.

### Validation messages emitted
| code | severity | meaning |
| --- | --- | --- |
| `field_required` | `requires_buyer_input` | required buyer field or line item missing → keeps `incomplete` |
| `item_unavailable` | `unrecoverable` | unknown SKU in `line_items` |
| `out_of_stock` | `recoverable` | known SKU, `available=false` |
| `not_found` | `unrecoverable` | unknown checkout/order/product id |
| `not_allowed` | (n/a) | illegal transition (update/cancel terminal) |
| `payment_failed` | `recoverable` | `adapter.create_order` raised `ValueError` |

Required buyer fields are configurable via `require_buyer_fields`
(Lithe uses `("email", "phone_number")`).

---

## 9. Discovery profile shape (`GET /.well-known/ucp`)

```json
{ "ucp": {
  "version": "2026-04-08",
  "services": { "dev.ucp.shopping": [
    { "version": "2026-04-08", "transport": "rest", "endpoint": "{base}/ucp/v1", "spec": "...", "schema": "..." },
    { "version": "2026-04-08", "transport": "mcp",  "endpoint": "{base}/ucp/mcp", "spec": "...", "schema": "..." }
  ] },
  "capabilities": {
    "dev.ucp.shopping.checkout": [ {"version": "2026-04-08", "spec": "...", "schema": "..."} ],
    "dev.ucp.shopping.catalog.search": [ {...} ],
    "dev.ucp.shopping.catalog.lookup": [ {...} ]
    /* + "dev.ucp.shopping.order" when enable_order_capability=True */
  },
  "payment_handlers": {
    "com.genko.offline_payment": [ { "id": "offline", "version": "2026-04-08",
      "spec": "{base}/ucp/payment/offline", "schema": "{base}/ucp/payment/offline.json",
      "available_instruments": [{"type":"offline"}],
      "config": {"instructions": "Provide a payment reference; order is created pending manual payment review."} } ]
  }
} }
```
The REST `endpoint` is the base for all §7 paths. Capability-scoped responses
advertise only the relevant capability (catalog responses advertise catalog
capabilities and omit payment handlers; order responses advertise only order).

---

## 10. MCP transport (`POST {mcp_path}`, default `/ucp/mcp`)

JSON-RPC 2.0. Methods: `initialize`, `tools/list`, `tools/call`.
- `initialize` → `{ protocolVersion: "2024-11-05", capabilities: {tools:{}}, serverInfo: {name: store_name, version} }`.
- `tools/call` output uses **UCP dual output**: `structuredContent` (the UCP
  payload) + `content: [{type:"text", text:<same JSON serialized>}]`.
- Protocol errors → JSON-RPC error: unknown method `-32601`, unknown tool
  `-32601`, invalid arguments `-32602`, parse error `-32700`, invalid request
  `-32600`. Business outcomes come back as normal results (envelope + messages).
- `arguments.meta` (agent metadata) is accepted and ignored.

Tools (names map to UCP operations):

| MCP tool | UCP op / REST call | Required args |
| --- | --- | --- |
| `search_products` | catalog search / `POST /catalog/search` | — (`query?`, `filters?`, `pagination?`) |
| `lookup_products` | catalog lookup / `POST /catalog/lookup` | `ids` |
| `get_product` | catalog product / `POST /catalog/product` | `id` |
| `create_checkout` | `POST /checkout-sessions` | `line_items` (`buyer?`) |
| `get_checkout` | `GET /checkout-sessions/{id}` | `id` |
| `update_checkout` | `PUT /checkout-sessions/{id}` | `id` (`line_items?`, `buyer?`) |
| `complete_checkout` | `POST .../{id}/complete` | `id` (`payment?`) |
| `cancel_checkout` | `POST .../{id}/cancel` | `id` |
| `get_order` *(order cap only)* | `GET /orders/{id}` | `id` |

> Note: the merchant's own MCP tool names use `*_products` for catalog. A gateway
> exposing the PRD's public tool names (`search_catalog`, …) should drive
> merchants over **REST**, so these internal names don't leak.

### Platform gateway MCP (production agents)

End-user agents connect to the **Genko platform backend** (`POST /mcp`), not to
merchant `/ucp/mcp`. The platform exposes **12** public tools — see
[`PLATFORM_INTEGRATION.md`](PLATFORM_INTEGRATION.md) §4:

- Platform-native: `get_user_profile`, `discover_commerces`, `get_purchase_history`
- UCP proxy (require `merchant_url`): `search_catalog`, `lookup_catalog`,
  `get_product`, checkout tools, `get_order`

Merchants implement only the REST surface in §7; they do not mount MCP in production.

---

## 11. Payments

Ships a pluggable **offline/manual** handler
(`com.genko.offline_payment`, instrument `id: "offline"`, `type: "offline"`).

- The platform/gateway sends a payment instrument at `complete` whose
  `credential.reference` (fallback `credential.token`) carries an opaque payment
  reference (bank-transfer ref, gateway authorization id, etc.).
- The SDK extracts that reference and passes it to `adapter.create_order` as
  `payment_reference`. The order is created in a **pending-payment** state for the
  merchant to reconcile.
- No card/CVV/balance data flows through the SDK.
- Provide `payment_handlers=` to advertise a real PSP (Stripe, Google Pay, …); the
  adapter then interprets the corresponding credential.

Instrument the gateway sends at complete:
```json
{ "payment": { "instruments": [ { "id": "offline", "handler_id": "offline", "type": "offline",
  "selected": true, "credential": { "reference": "<payment/authorization id>" } } ] } }
```

### 11.1 Platform verification + accreditation (`PlatformClient`)

When the merchant is configured with a **platform URL + API key** (or an injected
`platform_client`), `complete_checkout` does more than trust the instrument — it
calls the platform back over an authenticated channel to **verify** the payment
authorization and **accredit** (settle) it, so the platform credits the merchant.
This is the SDK realizing the platform PRD's "el comercio verifica autorizaciones
simuladas".

- **Auth:** `Authorization: Bearer <merchant_api_key>` on every call. The key is
  merchant-scoped, read from config/env, never placed in tool params or logs.
- **Client:** `PlatformClient` ABC + stdlib `HttpPlatformClient` (no extra deps);
  injectable/stubbable. Helper `verify_authorization(client, id, *, expected_amount_minor, currency)`.
- **Contract (infra must expose; paths configurable):**
  - `GET {platform}/v1/payment-authorizations/{id}` → `{ id, status, amount_minor, currency, checkout_id? }`
  - `POST {platform}/v1/payment-authorizations/{id}/accredit` body `{ order_id, amount_minor, currency }` (+ `Idempotency-Key`) → `{ status: "completed", transaction_id? }`
  - `POST {platform}/v1/payment-authorizations/{id}/release` body `{ reason? }`

**Flow (configured):** extract `credential.reference` (= authorization id) →
`GET` verify (status ∈ {created,reserved,submitted}, `amount_minor` == authoritative
total, currency match) → `adapter.create_order` → on success `accredit` (idempotency
key = checkout id); on order failure `release` and return `payment_failed`; on
accredit failure keep the order but return a `reconciliation_required` warning.

**Error codes returned to the agent** (all `severity: recoverable` unless noted):
`payment_authorization_invalid`, `payment_amount_mismatch`,
`payment_currency_mismatch`, `payment_declined` (`requires_buyer_input`, missing
reference), `upstream_unreachable` / `upstream_response_invalid` /
`upstream_timeout`, `payment_failed`, and `reconciliation_required` (warning).

---

## 12. Non-functional requirements

| Requirement | SDK behavior |
| --- | --- |
| Currency | Single currency per merchant (default USD); minor units only |
| Session storage | In-memory dict; TTL default 6h; **not** persistent across restarts |
| Idempotency (inbound) | Not header-based; `complete`/`cancel` are idempotent **by outcome** (no duplicate orders) |
| Idempotency (outbound accredit) | SDK sends the checkout id as `Idempotency-Key` on the platform `accredit` call |
| Inbound auth | None built-in on UCP routes; host app / gateway owns authn/authz |
| Outbound auth | Merchant API key (Bearer) on platform verify/accredit/release calls (§11.1) |
| Rate limiting | None built-in; host app owns it (Lithe reuses its public write limit) |
| Transport security | Plain HTTP; deploy behind TLS |
| Platform timeouts | `HttpPlatformClient` default 30s per call; failures map to `upstream_*` codes |
| Headers (`UCP-Agent`, `Request-Id`, `Idempotency-Key`) inbound | Accepted, not required, not validated/echoed |
| Versioning | Additive; unknown request fields ignored |

Known gaps / roadmap: header-level inbound idempotency store, `UCP-Agent`
validation, per-merchant inbound auth middleware, persistent session store,
fulfillment/delivery events in the Order snapshot, self-registration helper
(`python -m genko.register`).

---

## 13. Reference implementation — Lithe

- Constructed in `Lithe-Hackathon/backend/app/main.py`: `store_name="Lithe"`,
  `base_url=PUBLIC_BASE_URL`, `currency=USD`,
  `require_buyer_fields=("email","phone_number")`, `enable_order_capability=True`,
  ToS/privacy links, `continue_url_base={base}/products`.
- Adapter `LitheUCPAdapter` (`app/ucp_adapter.py`): exposes the predesign catalog
  (variant id `"{slug}__{size}__{color}"`), maps a completed checkout to
  `repository.create_order` (order type `predesign`, `pending_payment`), and
  resolves `get_order` via `get_order_by_number` — returning the rich snapshot
  (status, payment_status, currency, created_at, totals, line_items).
- Platform accreditation is configured from env `UCP_PLATFORM_URL` +
  `UCP_PLATFORM_API_KEY`; when unset, Lithe completes checkouts as a pure offline
  handler (still records the payment reference).
- Surfaces all **9** UCP REST operations (+ optional shop-side MCP in demos only).
  UCP orders land in the same admin/tracking pipeline as storefront orders.
- Tests: `backend/tests/test_ucp_flow.py`.

---

## 14. Acceptance criteria

1. `GET /.well-known/ucp` advertises checkout + both catalog capabilities (and
   order when enabled) over rest + mcp transports.
2. Catalog search/lookup/product return UCP catalog documents; variant ids are
   usable as checkout `line_items[].item.id`.
3. A checkout can be created, fetched, updated, completed, and canceled with the
   documented status transitions.
4. Missing required buyer fields keep the checkout `incomplete` with
   `field_required` messages; unknown/out-of-stock SKUs surface the right codes.
5. Completing a `ready_for_complete` checkout calls `create_order` once and returns
   an `order` with `permalink_url`; repeating complete does not duplicate the order.
6. Order capability (when enabled) resolves a real order and returns a `not_found`
   error envelope for unknown ids. The snapshot includes status/payment_status/
   totals/line_items so success is visible to the platform and the buyer.
7. REST and MCP produce identical business results; MCP returns dual output.
8. Business outcomes are HTTP 200 + envelope; only protocol/validation failures use
   error status / JSON-RPC errors.
9. When a platform client is configured, `complete` verifies the authorization
   (status/amount/currency) before placing the order, accredits it on success
   (once, via idempotency key), releases it if order creation fails, and flags
   `reconciliation_required` if accreditation fails after the order exists. When
   not configured, offline behavior is unchanged.
10. `pip install -e .` works; `python -m unittest discover tests` passes; the demo
    store + scripted agent run end to end.

---

## 15. Testing & examples

- `python-sdk/tests/test_checkout_lifecycle.py` — engine lifecycle + REST/MCP
  bindings + discovery.
- `python-sdk/tests/test_catalog.py` — catalog search/lookup/product, order
  capability, catalog→checkout id round-trip.
- `python-sdk/tests/test_platform_accreditation.py` — platform verify/accredit/
  release flow with a stub `PlatformClient` (happy path, amount mismatch,
  non-capturable status, missing reference, order-failure release, accredit-failure
  reconciliation, offline passthrough).
- `python-sdk/examples/` — runnable `demo_store` + `agent_client.py` scripted agent.
- Run: `pip install -e ".[dev]"` then `python -m unittest discover tests -v`.
