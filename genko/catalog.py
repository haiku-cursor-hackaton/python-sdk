"""Mapping helpers for the UCP shopping/catalog capability.

The SDK models a purchasable SKU as a single :class:`~genko.models.Product`
(variants such as size/color are already encoded into ``Product.id``). The UCP
Catalog capability, however, expresses products as a richer document with a
``price_range``, ``media`` and one or more ``variants``. Because each SDK
``Product`` already *is* the purchasable variant, we map it to a Catalog Product
with a SINGLE variant whose ``id`` equals the checkout ``line_items[].item.id``.

Kept intentionally dict-based (the Catalog document is read-only output and the
spec is additively versioned) rather than over-modelled with Pydantic.
"""

from __future__ import annotations

from typing import Any

from .models import Product


def product_to_catalog(product: Product, *, inputs: list[dict] | None = None) -> dict:
    """Map an SDK :class:`Product` to a UCP Catalog Product document.

    ``inputs`` is only supplied for Lookup responses; when present it is attached
    to the single variant as ``[{"id": <requested id>, "match": "exact"}]``.
    """
    price = {"amount": product.price, "currency": product.currency}
    description = {"plain": product.description or product.title}

    variant: dict[str, Any] = {
        "id": product.id,
        "title": product.title,
        "description": dict(description),
        "price": dict(price),
        "availability": {"available": product.available},
    }
    if inputs is not None:
        variant["inputs"] = inputs

    catalog_product: dict[str, Any] = {
        "id": product.id,
        "title": product.title,
        "description": dict(description),
        "price_range": {"min": dict(price), "max": dict(price)},
        "variants": [variant],
    }

    url = product.attributes.get("url")
    if url:
        catalog_product["url"] = url
    if product.image_url:
        catalog_product["media"] = [{"type": "image", "url": product.image_url}]

    return catalog_product
