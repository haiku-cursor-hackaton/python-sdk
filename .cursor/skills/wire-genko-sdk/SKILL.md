---
name: wire-genko-sdk
description: >-
  Integrate the Genko SDK (genko-sdk) into a Python/FastAPI ecommerce store:
  pip install, MerchantAdapter, UCP REST routers, platform env vars, registration,
  and verification. Use when wiring UCP, adding agent checkout, installing genko-sdk,
  merchant adapter, UCP_GATEWAY_API_KEY, or onboarding a store to the Genko platform.
---

# Wire Genko SDK into a Store

You are integrating [Genko SDK](https://github.com/haiku-cursor-hackaton/python-sdk)
into the **merchant's ecommerce repo** (FastAPI backend). Work in that repo, not
in python-sdk unless installing or reading reference files.

## Canonical guide

Read and follow **[docs/AGENT_INTEGRATION.md](../../docs/AGENT_INTEGRATION.md)**
end-to-end. Execute every checklist item in order. Do not skip verification (§6).

Supporting docs (read when needed):

- Platform contract: [docs/PLATFORM_INTEGRATION.md](../../docs/PLATFORM_INTEGRATION.md)
- Reference merchant: [Lithe ucp_adapter.py](https://github.com/haiku-cursor-hackaton/Lithe-Hackathon/blob/main/backend/app/ucp_adapter.py), [main.py UCP block](https://github.com/haiku-cursor-hackaton/Lithe-Hackathon/blob/main/backend/app/main.py)

## Execution rules

1. **Reuse existing order logic** — `MerchantAdapter.create_order` must call the
   store's normal pipeline; never add a second order-creation path.
2. **REST only in production** — `enable_mcp=False`; do not mount `mcp_router`.
3. **Integer minor-unit prices** — USD cents, never floats.
4. **Mount UCP routers before SPA catch-alls** — `/.well-known/ucp` and `/ucp/v1/*`
   must resolve on the public base URL.
5. **Agents shop via the platform** — `POST /mcp` on Genko backend; stores do not
   expose agent-facing MCP in production.

## Checklist (track in your response)

```
- [ ] 1. Install genko-sdk into the merchant environment
- [ ] 2. Implement MerchantAdapter (products + create_order)
- [ ] 3. Wire UCPMerchant in FastAPI (REST + discovery only)
- [ ] 4. Set PUBLIC_BASE_URL and document env vars in .env.example
- [ ] 5. enable_order_capability=True (platform get_order)
- [ ] 6. Register store on Genko platform (document steps for operator)
- [ ] 7. Tests: /.well-known/ucp + checkout lifecycle
```

## Step 1 — Install SDK

Pick one:

```bash
pip install -e /path/to/python-sdk
```

```text
# requirements.txt / pyproject (pin SHA in production Docker)
genko-sdk @ git+https://github.com/haiku-cursor-hackaton/python-sdk.git@main
```

If python-sdk is not cloned yet:

```bash
git clone https://github.com/haiku-cursor-hackaton/python-sdk.git
pip install -e python-sdk
```

## Deliverables

When done, the merchant repo should have:

- `MerchantAdapter` implementation wired to real catalog + orders
- `UCPMerchant` mounted in FastAPI with `enable_mcp=False`
- Updated `.env.example` (`PUBLIC_BASE_URL`, optional `UCP_PLATFORM_*`, `UCP_GATEWAY_API_KEY`)
- At least one test hitting `/.well-known/ucp` and completing a checkout path
