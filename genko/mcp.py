"""MCP (Model Context Protocol) transport binding.

Exposes a single JSON-RPC endpoint so AI agents can drive the checkout as
tools. Implements ``initialize``, ``tools/list`` and ``tools/call``. Tool
responses use the UCP dual-output pattern: the payload in ``structuredContent``
plus a serialized copy in ``content[]`` for backwards compatibility.
"""

from __future__ import annotations

import json
from collections.abc import Sequence

from fastapi import APIRouter, Request
from fastapi.params import Depends

from .engine import CheckoutEngine
from .models import Buyer, LineItemRequest, Payment

JSONRPC_VERSION = "2.0"

_LINE_ITEMS_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "properties": {
            "item": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]},
            "quantity": {"type": "integer", "minimum": 1},
        },
        "required": ["item", "quantity"],
    },
}
_BUYER_SCHEMA = {
    "type": "object",
    "properties": {
        "first_name": {"type": "string"},
        "last_name": {"type": "string"},
        "email": {"type": "string"},
        "phone_number": {"type": "string"},
    },
}


_CATALOG_FILTERS_SCHEMA = {
    "type": "object",
    "properties": {
        "categories": {"type": "array", "items": {"type": "string"}},
        "price": {
            "type": "object",
            "properties": {
                "min": {"type": "integer"},
                "max": {"type": "integer"},
            },
        },
    },
}
_CATALOG_PAGINATION_SCHEMA = {
    "type": "object",
    "properties": {
        "limit": {"type": "integer", "minimum": 1},
        "cursor": {"type": "string"},
    },
}


def _tool_defs(enable_order: bool = False) -> list[dict]:
    tools = [
        {
            "name": "search_products",
            "description": "Search the merchant catalog (UCP catalog search).",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "filters": _CATALOG_FILTERS_SCHEMA,
                    "pagination": _CATALOG_PAGINATION_SCHEMA,
                },
            },
        },
        {
            "name": "lookup_products",
            "description": "Resolve specific products by id (UCP catalog lookup).",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "ids": {"type": "array", "items": {"type": "string"}},
                    "filters": _CATALOG_FILTERS_SCHEMA,
                },
                "required": ["ids"],
            },
        },
        {
            "name": "get_product",
            "description": "Get the full detail of a single product by id (UCP catalog product).",
            "inputSchema": {
                "type": "object",
                "properties": {"id": {"type": "string"}},
                "required": ["id"],
            },
        },
        {
            "name": "create_checkout",
            "description": "Create a checkout session for one or more line items.",
            "inputSchema": {
                "type": "object",
                "properties": {"line_items": _LINE_ITEMS_SCHEMA, "buyer": _BUYER_SCHEMA},
                "required": ["line_items"],
            },
        },
        {
            "name": "get_checkout",
            "description": "Get the current state of a checkout session.",
            "inputSchema": {
                "type": "object",
                "properties": {"id": {"type": "string"}},
                "required": ["id"],
            },
        },
        {
            "name": "update_checkout",
            "description": "Update line items and/or buyer details on a checkout session.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "line_items": _LINE_ITEMS_SCHEMA,
                    "buyer": _BUYER_SCHEMA,
                },
                "required": ["id"],
            },
        },
        {
            "name": "complete_checkout",
            "description": "Finalize a checkout session and place the order.",
            "inputSchema": {
                "type": "object",
                "properties": {"id": {"type": "string"}, "payment": {"type": "object"}},
                "required": ["id"],
            },
        },
        {
            "name": "cancel_checkout",
            "description": "Cancel a checkout session.",
            "inputSchema": {
                "type": "object",
                "properties": {"id": {"type": "string"}},
                "required": ["id"],
            },
        },
    ]
    if enable_order:
        tools.append(
            {
                "name": "get_order",
                "description": "Get the current snapshot of a previously placed order by id.",
                "inputSchema": {
                    "type": "object",
                    "properties": {"id": {"type": "string"}},
                    "required": ["id"],
                },
            }
        )
    return tools


def _rpc_result(request_id, result: dict) -> dict:
    return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "result": result}


def _rpc_error(request_id, code: int, message: str, data: dict | None = None) -> dict:
    error: dict = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "error": error}


def _tool_output(payload: dict) -> dict:
    """UCP MCP dual-output: structuredContent + serialized content[]."""
    return {
        "structuredContent": payload,
        "content": [{"type": "text", "text": json.dumps(payload)}],
    }


def build_mcp_router(
    engine: CheckoutEngine,
    *,
    path: str,
    server_name: str,
    enable_order: bool = False,
    dependencies: Sequence[Depends] | None = None,
) -> APIRouter:
    router = APIRouter(tags=["ucp-mcp"], dependencies=list(dependencies or []))

    def _dispatch_tool(name: str, args: dict) -> dict:
        if name == "get_order" and enable_order:
            return engine.get_order_detail(args["id"])

        if name == "search_products":
            return engine.search_catalog(
                query=args.get("query"),
                filters=args.get("filters"),
                pagination=args.get("pagination"),
            )

        if name == "lookup_products":
            return engine.lookup_catalog(args["ids"], filters=args.get("filters"))

        if name == "get_product":
            return engine.get_product_detail(
                args["id"], selected=args.get("selected"), preferences=args.get("preferences")
            )

        if name == "create_checkout":
            line_items = [LineItemRequest.model_validate(li) for li in args.get("line_items", [])]
            buyer = Buyer.model_validate(args["buyer"]) if args.get("buyer") else None
            return engine.create_checkout(line_items=line_items, buyer=buyer).model_dump(exclude_none=True)

        if name == "get_checkout":
            return engine.get_checkout(args["id"]).model_dump(exclude_none=True)

        if name == "update_checkout":
            line_items = (
                [LineItemRequest.model_validate(li) for li in args["line_items"]]
                if args.get("line_items") is not None
                else None
            )
            buyer = Buyer.model_validate(args["buyer"]) if args.get("buyer") else None
            return engine.update_checkout(
                args["id"], line_items=line_items, buyer=buyer
            ).model_dump(exclude_none=True)

        if name == "complete_checkout":
            payment = Payment.model_validate(args["payment"]) if args.get("payment") else None
            return engine.complete_checkout(args["id"], payment=payment).model_dump(exclude_none=True)

        if name == "cancel_checkout":
            return engine.cancel_checkout(args["id"]).model_dump(exclude_none=True)

        raise KeyError(name)

    @router.post(path)
    async def mcp_endpoint(request: Request) -> dict:
        try:
            body = await request.json()
        except Exception:
            return _rpc_error(None, -32700, "Parse error")

        if not isinstance(body, dict):
            return _rpc_error(None, -32600, "Invalid Request")

        request_id = body.get("id")
        method = body.get("method")
        params = body.get("params") or {}

        if method == "initialize":
            return _rpc_result(
                request_id,
                {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": server_name, "version": engine.version},
                },
            )

        if method == "tools/list":
            return _rpc_result(request_id, {"tools": _tool_defs(enable_order)})

        if method == "tools/call":
            tool_name = params.get("name")
            arguments = params.get("arguments") or {}
            # Per spec, agent metadata is carried in arguments.meta; ignored here.
            arguments.pop("meta", None)
            try:
                payload = _dispatch_tool(tool_name, arguments)
            except KeyError:
                return _rpc_error(request_id, -32601, f"Unknown tool: {tool_name}")
            except Exception as error:  # noqa: BLE001
                return _rpc_error(request_id, -32602, f"Invalid arguments: {error}")
            return _rpc_result(request_id, _tool_output(payload))

        return _rpc_error(request_id, -32601, f"Method not found: {method}")

    return router
