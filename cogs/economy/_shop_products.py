"""Product registry for Trap Coin shop listings."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

import discord
from discord.ext import commands


@runtime_checkable
class ShopProduct(Protocol):
    """One sellable shop item type handled by a dedicated cog or helper."""

    item_type: str

    def buy_denial(
        self,
        guild: discord.Guild,
        member: discord.Member,
        item: dict[str, Any],
    ) -> str | None:
        """Return a Vietnamese error if this member cannot buy ``item``."""

    async def use_item(
        self,
        *,
        guild: discord.Guild,
        member: discord.Member,
        item: dict[str, Any],
        source: discord.Interaction | commands.Context,
    ) -> str | None:
        """Apply an owned item.

        Return a user-facing message, or ``None`` when the product already
        responded (for example by replacing the shop view with an editor).
        """


_PRODUCTS: dict[str, ShopProduct] = {}


def register_shop_product(product: ShopProduct) -> None:
    _PRODUCTS[product.item_type] = product


def unregister_shop_product(item_type: str) -> None:
    _PRODUCTS.pop(item_type, None)


def get_shop_product(item_type: str) -> ShopProduct | None:
    return _PRODUCTS.get(item_type)


def registered_item_types() -> frozenset[str]:
    return frozenset(_PRODUCTS)
