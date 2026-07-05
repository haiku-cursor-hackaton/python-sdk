"""A scripted agent that shops a UCP store over shop-side MCP (JSON-RPC).

For **production**, agents connect to the Genko platform (`POST /mcp`), not the
store. This script is for local SDK demos only.

Start the demo store first::

    uvicorn examples.demo_store:app --port 8100

Then run::

    python examples/agent_client.py
"""

from __future__ import annotations

import sys

import httpx

AGENT_PROFILE = "https://agent.example/profiles/shopping-agent.json"


def rpc(client: httpx.Client, mcp_url: str, method: str, params: dict | None = None, request_id: int = 1) -> dict:
    payload = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        payload["params"] = params
    resp = client.post(
        mcp_url,
        json=payload,
        headers={"UCP-Agent": f'profile="{AGENT_PROFILE}"'},
    )
    resp.raise_for_status()
    return resp.json()


def call_tool(client: httpx.Client, mcp_url: str, name: str, arguments: dict, request_id: int = 1) -> dict:
    result = rpc(client, mcp_url, "tools/call", {"name": name, "arguments": arguments}, request_id)
    if "error" in result:
        raise RuntimeError(result["error"])
    return result["result"]["structuredContent"]


def main() -> None:
    base_url = sys.argv[1].rstrip("/") if len(sys.argv) > 1 else "http://127.0.0.1:8100"
    mcp_url = f"{base_url}/ucp/mcp"

    with httpx.Client(timeout=30) as client:
        print(f"1. Discovering {base_url}/.well-known/ucp")
        profile = client.get(f"{base_url}/.well-known/ucp").json()
        version = profile["ucp"]["version"]
        print(f"   UCP version: {version}")

        print("2. Listing tools")
        tools = rpc(client, mcp_url, "tools/list")["result"]["tools"]
        print("   tools:", ", ".join(t["name"] for t in tools))

        print("3. Searching products")
        products = call_tool(client, mcp_url, "search_products", {})["products"]
        available = [p for p in products if p.get("available", True)]
        for p in products:
            flag = "" if p.get("available", True) else " (unavailable)"
            print(f"   - {p['id']}: {p['title']} = {p['price']}{flag}")
        chosen = available[0]

        print(f"4. Creating checkout for {chosen['id']}")
        checkout = call_tool(
            client,
            mcp_url,
            "create_checkout",
            {"line_items": [{"item": {"id": chosen["id"]}, "quantity": 2}]},
        )
        checkout_id = checkout["id"]
        print(f"   checkout {checkout_id} status={checkout['status']}")

        print("5. Adding buyer details")
        checkout = call_tool(
            client,
            mcp_url,
            "update_checkout",
            {
                "id": checkout_id,
                "buyer": {"email": "agent-buyer@example.com", "phone_number": "+15551234567"},
            },
        )
        print(f"   status={checkout['status']}")
        for total in checkout.get("totals", []):
            print(f"   {total.get('display_text', total['type'])}: {total['amount']}")

        print("6. Completing checkout")
        checkout = call_tool(
            client,
            mcp_url,
            "complete_checkout",
            {
                "id": checkout_id,
                "payment": {
                    "instruments": [
                        {
                            "handler_id": "offline",
                            "type": "offline",
                            "credential": {"reference": "AGENT-REF-001"},
                        }
                    ]
                },
            },
        )
        print(f"   status={checkout['status']}")
        order = checkout.get("order")
        if order:
            print(f"   order placed: {order['id']} -> {order['permalink_url']}")
        else:
            print("   no order created; messages:", checkout.get("messages"))


if __name__ == "__main__":
    main()
