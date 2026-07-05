# Platform Integration Contract — Genko SDK ↔ Genko Platform

**Audience:** the team building the **Genko platform backend** (MCP gateway,
wallet, dashboard) and anyone wiring a merchant store (e.g. Lithe) into it.

**Platform repo:** [`haiku-cursor-hackaton/backend`](https://github.com/haiku-cursor-hackaton/backend)
— FastAPI MCP gateway at `POST /mcp`, merchant registration, simulated wallet,
and payment-authorization callbacks the SDK calls on checkout completion.

**Purpose:** this is the source-of-truth handoff. It tells you exactly what a
merchant running the Genko SDK exposes, the final REST endpoints, the
MCP tool mapping (on the **platform**, not the store), request/response shapes,
the payment-instrument contract, and the honest list of what is / isn't enforced
yet. You can start building the gateway against this without reading the SDK source.

- UCP target version: **`2026-04-08`**
- MCP target version: **`2025-11-25`**
- Money: **integer minor units** (USD cents). Never floats. The gateway MUST NOT
  convert or reformat amounts (per PRD §4 transparency rule).

---

## 1. Where the gateway sits

```
Agent (Codex, etc.)
    │  MCP + gk_mcp_* user key
    ▼
Genko platform backend  —  POST /mcp  (12 tools: 3 platform + 9 UCP proxy)
    │  UCP REST + optional Authorization: Bearer gk_vendor_*
    ▼
Merchant store (Genko SDK)  —  POST/GET /ucp/v1/*  (REST only in production)
    │  verify + accredit on complete
    │  Authorization: Bearer gk_sdk_*
    ▼
Genko platform backend  —  GET/POST /v1/payment-authorizations/*
    │  simulated wallet (reserve → capture / release)
    ▼
Supabase
```

- **Merchant tier (Genko SDK + Lithe):** each store exposes **UCP REST only**
  (`enable_mcp=False` by default). Discovery (`/.well-known/ucp`) advertises a
  single `transport: "rest"` service. Optional inbound gate:
  `UCPMerchant(api_keys=...)` / Lithe `UCP_GATEWAY_API_KEY`.
- **Platform tier ([`backend`](https://github.com/haiku-cursor-hackaton/backend)):**
  the **only** public MCP surface for end users. It authenticates the user
  (`gk_mcp_*`), resolves `merchant_url` → a registered merchant, reads
  `/.well-known/ucp`, and **translates MCP tool calls into the merchant's UCP
  REST calls**. It owns the simulated wallet/settlement and issues SDK keys
  (`gk_sdk_*`) for merchant→platform payment callbacks.

**No agent bypass.** User agents must not call merchant `/ucp/v1/*` REST
directly, even if they possess a vendor key. Shopping always flows through
`POST /mcp` on the platform.

**Key alignment point:** the gateway drives merchants over their **REST** surface
(the table in §4). Per-merchant MCP (`/ucp/mcp`) is **opt-in** in the SDK for
local demos only — production Genko vendors do not mount it.

---

## 2. Discovery — `GET /.well-known/ucp`

Every merchant serves this profile. Resolve it, cache it (PRD's
`merchant_connections`), and use `services[...].endpoint` as the REST base URL.

Exact shape emitted by the SDK when `enable_mcp=False` (production default):

```json
{
  "ucp": {
    "version": "2026-04-08",
    "services": {
      "dev.ucp.shopping": [
        {
          "version": "2026-04-08",
          "transport": "rest",
          "endpoint": "https://store.example.com/ucp/v1",
          "spec": "https://ucp.dev/specification/overview",
          "schema": "https://ucp.dev/2026-04-08/services/shopping/rest.openapi.json"
        }
      ]
    },
    "capabilities": {
      "dev.ucp.shopping.checkout": [ { "version": "2026-04-08", "spec": "...", "schema": "..." } ],
      "dev.ucp.shopping.catalog.search": [ { "version": "2026-04-08", "spec": "...", "schema": "..." } ],
      "dev.ucp.shopping.catalog.lookup": [ { "version": "2026-04-08", "spec": "...", "schema": "..." } ]
    },
    "payment_handlers": {
      "com.genko.offline_payment": [
        {
          "id": "offline",
          "version": "2026-04-08",
          "spec": "https://store.example.com/ucp/payment/offline",
          "schema": "https://store.example.com/ucp/payment/offline.json",
          "available_instruments": [ { "type": "offline" } ],
          "config": { "instructions": "Provide a payment reference; order is created pending manual payment review." }
        }
      ]
    }
  }
}
```

When `enable_mcp=True` (local demos only), a second service entry with
`transport: "mcp"` and `endpoint: "{base_url}/ucp/mcp"` is also advertised.

**Gateway MUST:**
1. Read the REST `endpoint` — treat it as `ucp_base_url`. All REST paths in §4
   are **relative to this**, so `POST {endpoint}/catalog/search`, etc.
2. Validate `capabilities` before calling a tool (PRD §5 "validar capability").
   Return your `ucp_capability_unsupported` error if a capability is absent.
3. Read `payment_handlers` to learn what instrument to send at complete (see §7).
   The advertised handler id here is what you put in the instrument you inject.

> ⚠️ **Order capability visibility.** The SDK supports the Order capability, but
> a merchant only advertises `dev.ucp.shopping.order` (and mounts
> `GET /ucp/v1/orders/{id}`) when it opts in. **Lithe now advertises it** (all 9
> operations) — see §8. Other merchants may not: if `dev.ucp.shopping.order` is
> absent from a profile, the gateway's `get_order` tool must return
> `ucp_capability_unsupported` for that merchant rather than calling the endpoint.

---

## 3. `merchant_url` → base URL resolution (what we assume you do)

The PRD's routing extension is `merchant_url`. On our side there is nothing to
do per-request; we only note the contract so both sides agree:

- The gateway extracts the hostname from `merchant_url`, looks it up in
  `merchant_domains`, loads the cached `ucp_base_url` from `merchant_connections`,
  and **never** issues an HTTP request to an unregistered/arbitrary host
  (PRD §6). Our SDK does not participate in this; it just answers on its own
  base URL.

### Registering a merchant (Genko platform)

Use the platform backend ([`backend`](https://github.com/haiku-cursor-hackaton/backend)):

```http
POST /v1/merchants/register
Authorization: Bearer <supabase-user-jwt>
Content-Type: application/json

{ "root_url": "https://lithe-production.up.railway.app" }
```

The platform `GET`s `/.well-known/ucp`, extracts the REST `endpoint`, stores the
domain mapping, and returns `sdk_api_key` (`gk_sdk_*`). Set on the store:

| Store env var | Value |
|---|---|
| `UCP_PLATFORM_URL` | Genko platform base URL |
| `UCP_PLATFORM_API_KEY` | `sdk_api_key` from registration |
| `UCP_GATEWAY_API_KEY` | Vendor inbound key the platform sends as `Authorization: Bearer` |

End users connect via `POST /v1/connect/client` → `mcp_url` + `mcp_api_key`
(`gk_mcp_*`) for Codex (HTTP MCP) or `scripts/genko_mcp_stdio.py` (stdio bridge).

**Codex (recommended):** configure HTTP MCP in `~/.codex/config.toml`:

```toml
[mcp_servers.genko]
url = "https://genko-platform-production.up.railway.app/mcp"
bearer_token_env_var = "GENKO_MCP_API_KEY"
enabled = true
```

Set `GENKO_MCP_API_KEY` in Codex secrets. After a deploy that adds tools, disable
and re-enable the MCP server so Codex refreshes `tools/list`.

**Multi-merchant:** agents call `discover_commerces` to list registered stores,
then pass the chosen `merchant_url` into catalog/checkout tools. One checkout =
one merchant; multiple line items within the same store are OK.

The platform stores the vendor inbound key at registration (`ucp_inbound_api_key`
on `POST /v1/merchants/register`) and sends `Authorization: Bearer <vendor_key>`
on every outbound merchant REST request via `UcpRestClient`.

---

## 4. MCP tools and REST mapping

The platform exposes **12** public MCP tools. Three are **platform-native**
(Supabase — no merchant REST). Nine **proxy** a registered merchant's UCP REST
surface (require `merchant_url` on catalog/checkout/order tools).

### Platform-native tools

| MCP tool | Data source | Notes |
| --- | --- | --- |
| `get_user_profile` | Client profile + wallet | No args |
| `discover_commerces` | `businesses` table (active) | Optional `query`, `filters.categories`, `pagination` |
| `get_purchase_history` | Platform `orders` for signed-in client | Optional `filters.merchant_url`, `status`, date range |

`discover_commerces` returns `{ commerces: [{ business_id, name, category, description, merchant_url, status }], pagination }`.
Use `merchant_url` from a commerce row as the `merchant_url` argument on catalog/checkout tools.

### UCP proxy tools (merchant REST)

All REST paths below are **relative to the profile's REST `endpoint`**
(e.g. `https://store.example.com/ucp/v1`). This matches the PRD §5 table 1:1.

| MCP tool (PRD)      | Merchant REST call                                | Capability gating                     | Kind   |
| ------------------- | ------------------------------------------------- | ------------------------------------- | ------ |
| `search_catalog`    | `POST /catalog/search`                            | `dev.ucp.shopping.catalog.search`     | read   |
| `lookup_catalog`    | `POST /catalog/lookup`                            | `dev.ucp.shopping.catalog.lookup`     | read   |
| `get_product`       | `POST /catalog/product`                           | `dev.ucp.shopping.catalog.search`\*   | read   |
| `create_checkout`   | `POST /checkout-sessions`                         | `dev.ucp.shopping.checkout`           | write  |
| `get_checkout`      | `GET  /checkout-sessions/{id}`                    | `dev.ucp.shopping.checkout`           | read   |
| `update_checkout`   | `PUT  /checkout-sessions/{id}`                    | `dev.ucp.shopping.checkout`           | write  |
| `complete_checkout` | `POST /checkout-sessions/{id}/complete`           | `dev.ucp.shopping.checkout`           | buy    |
| `cancel_checkout`   | `POST /checkout-sessions/{id}/cancel`             | `dev.ucp.shopping.checkout`           | write  |
| `get_order`         | `GET  /orders/{id}`                               | `dev.ucp.shopping.order` (opt-in)     | read   |

\* `get_product` is served by the catalog capability; there is no separate
capability flag for product-detail in the profile.

**Non-standard:** `GET /ucp/v1/products` exists as a plain convenience feed for
debugging only. It is **not** part of UCP and is **not** advertised. The gateway
must ignore it and discover products via `POST /catalog/search`.

**Naming note for the gateway's own MCP tools:** the SDK's *own* per-merchant MCP
endpoint names the catalog tools `search_products` / `lookup_products` /
`get_product`. The PRD standardizes the gateway's public tools as
`search_catalog` / `lookup_catalog` / `get_product`. That's fine — the gateway
talks to merchants over **REST** (the paths above), so the merchant's MCP tool
names don't leak through. Use the PRD names publicly.

---

## 5. Catalog operations — request / response

### `POST /catalog/search`
Request body (all optional):
```json
{
  "query": "hoodie",
  "filters": { "categories": ["apparel"], "price": { "min": 1000, "max": 5000 } },
  "pagination": { "limit": 20, "cursor": "<opaque>" },
  "context": {}, "signals": {}, "attribution": {}
}
```
`context` / `signals` / `attribution` are accepted and ignored by the SDK.
Response (always HTTP 200):
```json
{
  "ucp": { "version": "2026-04-08", "status": "success", "capabilities": { "dev.ucp.shopping.catalog.search": [...], "dev.ucp.shopping.catalog.lookup": [...] } },
  "products": [ { "id": "...", "title": "...", "description": {"plain": "..."},
                 "price_range": {"min": {...}, "max": {...}},
                 "variants": [ { "id": "<SKU used as line_items[].item.id>", "title": "...", "price": {"amount": 2500, "currency": "USD"}, "availability": {"available": true} } ],
                 "url": "https://...", "media": [{"type":"image","url":"..."}] } ],
  "pagination": { "has_next_page": true, "total_count": 42, "cursor": "<opaque>" }
}
```
- Each product carries a **single variant**; `variants[0].id` is the SKU to use
  as `checkout.line_items[].item.id`.
- `cursor` is opaque — pass it back verbatim in `pagination.cursor` for the next
  page. It's only present when `has_next_page` is true.

### `POST /catalog/lookup`
```json
{ "ids": ["sku-a", "sku-b"], "filters": {}, "context": {} }
```
Response: `{ "ucp": {...}, "products": [ ...same product shape, variant has "inputs": [{"id": "sku-a", "match": "exact"}]... ], "messages": [ { "type": "info", "code": "not_found", "content": "sku-b" } ] }`
Unknown ids come back as **`info` / `not_found` messages**, not as errors. HTTP 200.

### `POST /catalog/product`
```json
{ "id": "sku-a", "selected": {}, "preferences": {}, "context": {} }
```
Success: `{ "ucp": {...}, "product": { ...single product... }, "messages": [] }`.
Not found: **HTTP 200** with `ucp.status: "error"` and an unrecoverable
`not_found` message (this is a business outcome, not a transport error).

> Catalog data is informative. **Price/availability are authoritative only at
> checkout** (PRD §5.3). Don't cache catalog prices into a checkout.

---

## 6. Checkout operations — request / response + lifecycle

### Lifecycle
```
incomplete ──(all required info present)──▶ ready_for_complete ──(complete)──▶ completed
   │                                             │
   │ missing buyer fields / line items           │ still missing at complete →
   │ surface as `messages`                        └─▶ requires_escalation (+ continue_url)
canceled ◀── cancel / TTL expiry (from any non-terminal state)
```
Statuses the gateway will observe: `incomplete`, `ready_for_complete`,
`requires_escalation`, `completed`, `canceled`. Sessions expire after a TTL
(Lithe: 6h) — reads of an expired session return it as `canceled`.

### `POST /checkout-sessions` (create)
```json
{
  "line_items": [ { "item": { "id": "<SKU from catalog>" }, "quantity": 2 } ],
  "buyer": { "email": "a@b.com", "phone_number": "+1..." }
}
```
Returns the full checkout envelope (see below). `buyer` is optional at create.

### `GET /checkout-sessions/{id}` (get) — full snapshot.

### `PUT /checkout-sessions/{id}` (update)
```json
{ "line_items": [...], "buyer": { "phone_number": "+1..." } }
```
Semantics: `line_items` is a **full replacement** when present; `buyer` fields
are **merged** (non-null fields overwrite). Omit a field to leave it unchanged.
Per PRD §5.6, send the checkout body **without** an `id` (the id is the path
param).

### `POST /checkout-sessions/{id}/complete`
Body: `{ "payment": { ... } }` — see §7 for the exact instrument.
- If required info is still missing → status flips to `requires_escalation`,
  and `continue_url` points at the storefront for human handoff. **No order is
  created.** The gateway should treat this as "not ready" and surface messages.
- On success → status `completed` with an `order` block.

### `POST /checkout-sessions/{id}/cancel` → status `canceled`.

### Checkout envelope (shape returned by all checkout ops)
```json
{
  "ucp": { "version": "2026-04-08", "status": "success", "capabilities": {...}, "payment_handlers": {...} },
  "id": "chk_...",
  "status": "ready_for_complete",
  "currency": "USD",
  "line_items": [ { "id": "li_1", "item": {...}, "quantity": 2, "totals": [ {"type":"subtotal","display_text":"Subtotal","amount":5000} ] } ],
  "totals": [ { "type": "subtotal", "amount": 5000 }, { "type": "total", "amount": 5000 } ],
  "buyer": { "email": "...", "phone_number": "..." },
  "messages": [],
  "links": [ {"type":"terms_of_service","url":"..."} ],
  "expires_at": "2026-07-04T20:00:00Z",
  "continue_url": "https://store.example.com/products/checkout/chk_...",
  "payment": null,
  "order": null
}
```
On `completed`, `order` is populated: `{ "id": "...", "permalink_url": "https://..." }`.

### Validation messages (what "missing info" looks like)
`messages[]` items use `{ type, code, path, content, severity }`. Codes the SDK
emits:

| code             | severity                | meaning                                   |
| ---------------- | ----------------------- | ----------------------------------------- |
| `field_required` | `requires_buyer_input`  | a required buyer field / line item missing → keeps checkout `incomplete` |
| `item_unavailable` | `unrecoverable`       | unknown SKU in `line_items`               |
| `out_of_stock`   | `recoverable`           | known SKU, not purchasable right now      |

The gateway MUST pass `messages` through unchanged (PRD transparency rule).

---

## 7. Payment instrument contract (simulated balance ↔ offline handler)

This is the most important alignment point between the PRD's simulated wallet and
what merchants actually accept.

**How it fits together:**
- The merchant advertises a payment handler in its profile (Lithe/default:
  handler group `com.genko.offline_payment`, instrument **`id: "offline"`**,
  instrument `type: "offline"`).
- The PRD gateway runs its **own** simulated wallet (`dev.platform.simulated_balance`)
  entirely on the gateway side. The merchant neither sees nor needs the wallet.
- At `complete`, the gateway reserves balance, creates its internal authorization,
  and then **injects a UCP payment instrument that references that authorization**
  into the merchant's `complete` call.

**What to send at `complete_checkout`** (the merchant reads it):
```json
{
  "payment": {
    "instruments": [
      {
        "id": "offline",
        "handler_id": "offline",
        "type": "offline",
        "selected": true,
        "credential": { "reference": "<gateway authorization_id>" }
      }
    ]
  }
}
```
- The merchant extracts `credential.reference` (falls back to `credential.token`)
  and stores it as the order's `payment_reference`. That's the only field it
  needs from you. **Do not** send card numbers/CVV/wallet balance (matches PRD §8
  "no contiene").
- The merchant creates the order in a **pending-payment** state and returns the
  `order.id` + `permalink_url`. Settlement/credit is 100% simulated on the gateway
  side; from the merchant's perspective the reference is opaque.

**Result → your wallet state machine (PRD §8):** treat a `completed` checkout
with a populated `order` as success (move `reserved → merchant.pending_balance`).
If `complete` returns non-`completed` / error, release the reserved balance.

### 7b. Merchant → platform accreditation callback (API key)

Beyond trusting the injected instrument, the SDK can **actively verify and
accredit** the payment against the platform using a **merchant API key** the
infra issues to each store. This realizes the PRD's "SDK del comercio … verifica
autorizaciones simuladas" design point. It is **opt-in**: if the merchant is not
configured with a platform URL + API key, the SDK skips these calls and behaves
as a pure offline handler.

**Auth:** every call sends `Authorization: Bearer <merchant_api_key>`. The key is
merchant-scoped, stored only by the store (from env), and never appears in tool
params or logs.

**Flow inside `complete_checkout` (when configured):**
```
extract credential.reference  (= platform authorization_id)
      ↓
GET  {platform}/v1/payment-authorizations/{id}     ← verify status + amount + currency
      ↓ (must be created|reserved|submitted, amount == authoritative order total, currency match)
adapter.create_order(...)                          ← place the real order
      ↓ success                         ↓ failure
POST {platform}/.../{id}/accredit       POST {platform}/.../{id}/release {reason}
  { order_id, amount_minor, currency }
      ↓
checkout.status = completed (+ order)
```

**Endpoints the infra MUST expose** (paths configurable on the SDK side):
```
GET  {platform}/v1/payment-authorizations/{id}
  200 → { "id", "status", "amount_minor", "currency", "checkout_id"?, "merchant_id"? }

POST {platform}/v1/payment-authorizations/{id}/accredit
  Headers: Authorization: Bearer <api_key>, Idempotency-Key: <checkout id>
  body → { "order_id", "amount_minor", "currency" }
  200  → { "status": "completed", "transaction_id"? }        ← platform credits merchant

POST {platform}/v1/payment-authorizations/{id}/release        (best-effort on failure)
  body → { "reason"? }
  200
```

**Validation the SDK performs before placing the order** (each maps to a UCP
message `code` returned to the agent, `severity: recoverable`):
- authorization `status` ∉ `{created, reserved, submitted}` → `payment_authorization_invalid`
- `amount_minor` != authoritative order total → `payment_amount_mismatch`
- `currency` mismatch → `payment_currency_mismatch`
- missing `credential.reference` → `payment_declined` (`requires_buyer_input`)
- platform unreachable / non-JSON / timeout → `upstream_unreachable` / `upstream_response_invalid` / `upstream_timeout`

**Failure semantics:**
- Verify fails → **no order placed**, error envelope returned.
- `create_order` fails after a good verify → SDK calls `release`, returns `payment_failed`.
- `accredit` fails **after** the order exists → checkout still completes, but with a
  `reconciliation_required` warning message (order is not lost; platform should
  reconcile via `get_checkout`/`get_order`).

**Idempotency:** the SDK sends the checkout id as `Idempotency-Key` on `accredit`,
so a retried complete accredits at most once.

---

## 8. Order capability status

- The SDK fully implements `get_order` / `GET /orders/{id}`. It returns
  `{ "ucp": {status,...}, "order": {...} }`, or HTTP 200 + `ucp.status:"error"`
  with a `not_found` message for unknown ids.
- **It is opt-in.** A merchant enables it with `enable_order_capability=True`
  (SDK) which both advertises `dev.ucp.shopping.order` in the profile and mounts
  the route.
- **Lithe and the Genko example stores enable it**, so they expose the full **9**
  UCP REST operations and the platform's `get_order` tool works for them. The
  order snapshot (returned both here and in the completed checkout's `order`
  block) is a **rich success record**:
  `{ id, permalink_url, label, status, payment_status, currency, created_at, totals[], line_items[] }`.
  Unknown ids return HTTP 200 + `ucp.status:"error"` with a `not_found` message.

**Still gate per-merchant.** Not every merchant will enable Order, so the gateway
must gate its `get_order` tool on the presence of `dev.ucp.shopping.order` in
that merchant's profile.

---

## 9. Headers, idempotency, auth — what is / isn't enforced (be honest)

The PRD (§7) has the gateway attach `UCP-Agent`, `Request-Id`, `Idempotency-Key`,
and the merchant's own credentials. Current merchant-side reality:

| Header / behavior       | Merchant SDK today                                             |
| ----------------------- | -------------------------------------------------------------- |
| `UCP-Agent`             | **Accepted, not required, not validated.** Safe to always send.|
| `Request-Id`            | Accepted, ignored (not echoed). Safe to send.                  |
| `Idempotency-Key`       | **Not yet enforced.** The SDK does not de-dupe by key. See note.|
| Merchant auth (API key) | **Optional inbound gate available.** When a store configures `UCPMerchant(api_keys=...)` (Lithe: `UCP_GATEWAY_API_KEY`), the REST operation router requires `Authorization: Bearer <vendor_key>` and returns **401** otherwise; `/.well-known/ucp` discovery stays public. Production Genko vendors are **REST-only** (`enable_mcp=False`); the platform MCP gateway exposes tools and calls vendor REST. If unset, the surface is open (rate-limited). Separately, the SDK uses an **outbound** merchant API key to call the platform back for verify/accredit — see §7b. |
| TLS / HTTPS             | Deployment concern; the SDK speaks plain HTTP behind whatever host serves it. |

**Idempotency note (important for `complete`/`cancel`):** the SDK's checkout
engine is **naturally idempotent for `complete`** — completing an already
`completed` session returns the same checkout (with the same order) without
re-creating an order, and completing a `canceled` session errors. `cancel` on an
already-canceled/completed session errors. So repeating `complete_checkout` will
**not** double-place an order (satisfies PRD acceptance #12) even though the SDK
doesn't inspect the `Idempotency-Key` header. The gateway should still generate
and store its own idempotency key for its wallet bookkeeping.

**Vendor key gate (when enabled):** the gateway must send `Authorization: Bearer
<vendor_key>` (the key Genko issues to that store on registration) on **every**
REST call to that store. Discovery is exempt. Multiple keys can be configured for
rotation. Buyer identity (name/email/phone) still flows through the normal UCP
buyer fields — the gateway resolves the user from their *user* key and forwards
those fields; the vendor key only authorizes the store↔gateway channel.

**Agents must not use the vendor key.** End-user agents authenticate with
`gk_mcp_*` keys against `POST /mcp` on the platform only. Direct calls to a
merchant's `/ucp/v1/*` with a vendor key are a platform implementation detail,
not an supported agent integration path.

> Gaps we may close later if needed: header-level idempotency store, `UCP-Agent`
> validation. Coordinate before relying on any of these.

---

## 10. Error model

- **Transport / protocol errors** (bad path, malformed body) → normal HTTP error
  codes. The REST layer maps business error envelopes as: `not_found` → 404,
  `not_allowed` → 409, other error envelopes → 422; success → 200.
- **Business outcomes** are **not** transport failures — they come back HTTP 200
  with a UCP envelope whose `ucp.status` is `success`/`error` plus `messages[]`
  (e.g. catalog product not found, out-of-stock, missing buyer fields). The
  gateway must surface these as normal tool results, not `isError` (PRD §10).
- Gateway-specific codes (`merchant_not_registered`, `insufficient_platform_balance`,
  `idempotency_conflict`, `reconciliation_required`, …) are **your** layer — the
  merchant never emits them.

---

## 11. Production merchants (hackathon)

| Store | URL | Category |
| --- | --- | --- |
| Lithe | `https://lithe-production.up.railway.app` | apparel |
| Genko Gear | `https://genko-gear-production.up.railway.app` | home-goods |
| Genko Basics | `https://genko-basics-production.up.railway.app` | apparel |
| Genko Pantry | `https://genko-pantry-production.up.railway.app` | food |

Platform: `https://genko-platform-production.up.railway.app`.

Seed all four + one MCP key: `python scripts/seed_multi_merchant.py` in
genko-backend (writes `temp/multi_merchant_credentials.json`).

---

## 12. Lithe specifics (the reference merchant)

- Base URL: `settings.public_base_url`; REST endpoint therefore `{base}/ucp/v1`,
  discovery `{base}/.well-known/ucp`. Production Lithe does **not** mount
  shop-side MCP (`enable_mcp=False`).
- Currency: `USD` (minor units / cents).
- Required buyer fields: **`email` and `phone_number`** → a checkout stays
  `incomplete` with `field_required` messages until both are present. The gateway
  should collect these into `buyer` before `complete`.
- Catalog: predesign products; SKU/variant id format is
  `"{slug}__{size}__{color}"` (from `backend/app/catalog.py`). Use the id exactly
  as returned by search/lookup.
- On `complete`, Lithe creates a real order via its normal pipeline in a
  **pending-payment** state and returns `order.permalink_url`.
- Order capability: **advertised** — `GET /ucp/v1/orders/{order#}` resolves the
  rich order snapshot (id, label, status, payment_status, currency, created_at,
  totals, line_items, permalink). See §8.
- Platform accreditation: configured via env `UCP_PLATFORM_URL` +
  `UCP_PLATFORM_API_KEY` (§7b). When unset, Lithe completes checkouts as a pure
  offline handler (still records the payment reference, order `pending_payment`).
- Rate limiting: UCP write routes reuse Lithe's public write limit.

---

## 13. Merchant self-registration (proposed — not built yet)

The PRD resolves merchants from Supabase (`merchants` / `merchant_domains` /
`merchant_connections`). To populate that, the plan is an SDK helper
(`python -m genko.register`) that POSTs the merchant's public base URL to
a gateway/infra **register endpoint** so the infra can then fetch
`/.well-known/ucp` and cache the connection.

**This helper is not implemented yet.** For the gateway builder, please define
the register endpoint contract you want (URL + payload). A minimal proposal:

```
POST {infra}/merchants/register
{ "base_url": "https://store.example.com", "name": "Lithe", "category": "apparel" }
→ infra fetches {base_url}/.well-known/ucp, validates version+capabilities,
  stores ucp_base_url + capabilities + domain in merchant_connections.
```

Confirm the shape and we'll ship the SDK-side helper to match.

---

## 14. Quick checklist for the gateway builder

- [ ] Resolve `merchant_url` → registered `ucp_base_url` (never hit arbitrary hosts).
- [ ] `GET {base}/.well-known/ucp`, cache `capabilities` + `payment_handlers`.
- [ ] Expose `get_user_profile`, `discover_commerces`, `get_purchase_history` (platform data).
- [ ] Gate each of the 9 UCP proxy tools on the advertised capability (esp. `get_order`).
- [ ] Call the REST paths in §4, relative to the profile REST `endpoint`.
- [ ] Send vendor inbound key on outbound REST when the store requires it.
- [ ] Pass UCP envelopes/`messages` through **unchanged**; don't convert amounts.
- [ ] At `complete`, inject the offline instrument from §7 with your
      `authorization_id` in `credential.reference`.
- [ ] Issue each merchant an **API key** and expose the three
      `/v1/payment-authorizations/{id}` endpoints from §7b (get / accredit /
      release), Bearer-authenticated. The SDK verifies the authorization and
      accredits it on success.
- [ ] Treat `completed` + populated `order` as the settlement trigger; the SDK's
      `accredit` call is the merchant confirming placement so you credit its balance.
- [ ] Send `UCP-Agent` / `Request-Id` / `Idempotency-Key` (merchant tolerates them);
      keep your own idempotency + wallet bookkeeping gateway-side.
- [ ] `get_order` is live on Lithe and example stores; still gate it per-merchant on the
      profile advertising `dev.ucp.shopping.order`.
