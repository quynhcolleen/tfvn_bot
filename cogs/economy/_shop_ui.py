"""Owner-locked interactive catalog, inventory, and purchase panel."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import discord

from cogs.economy._shop_helpers import (
    RENTAL_ITEM_TYPES,
    catalog_option_description,
    catalog_option_label,
    format_price,
    item_icon,
    item_type_label,
)
from cogs.economy._shop_rentals import rental_active, rental_status


if TYPE_CHECKING:
    from cogs.economy.shop import ShopCog


logger = logging.getLogger(__name__)

SHOP_UI_TIMEOUT_SECONDS = 180
NO_MENTIONS = discord.AllowedMentions.none()
PANEL_STORE = "store"
PANEL_INVENTORY = "inventory"
SHOP_SELECT_CUSTOM_ID = "shop:item"
SHOP_BUY_CUSTOM_ID = "shop:buy"
SHOP_USE_CUSTOM_ID = "shop:use"
SHOP_UNEQUIP_CUSTOM_ID = "shop:unequip"
SHOP_STORE_CUSTOM_ID = "shop:panel-store"
SHOP_INVENTORY_CUSTOM_ID = "shop:panel-inventory"
SHOP_CLOSE_CUSTOM_ID = "shop:close"


async def send_private(interaction: discord.Interaction, content: str) -> None:
    """Send a mention-free private response before or after acknowledgement."""
    try:
        if interaction.response.is_done():
            await interaction.followup.send(
                content=content, ephemeral=True, allowed_mentions=NO_MENTIONS
            )
        else:
            await interaction.response.send_message(
                content=content, ephemeral=True, allowed_mentions=NO_MENTIONS
            )
    except discord.HTTPException:
        logger.debug("Could not send shop UI response", exc_info=True)


class ShopItemSelect(discord.ui.Select):
    def __init__(self, view: "ShopView") -> None:
        items = view.panel_items
        options = [
            discord.SelectOption(
                label=catalog_option_label(item),
                value=str(item["item_id"]),
                description=catalog_option_description(item),
                emoji=item_icon(str(item.get("item_type", ""))),
                default=str(item["item_id"]) == view.selected_id,
            )
            for item in items
        ]
        super().__init__(
            custom_id=SHOP_SELECT_CUSTOM_ID,
            placeholder=(
                "Chọn vật phẩm trong kho…"
                if view.panel == PANEL_INVENTORY
                else "Chọn vật phẩm để mua…"
            ),
            min_values=1,
            max_values=1,
            options=options,
            row=0,
        )
        self.shop_view = view

    async def callback(self, interaction: discord.Interaction) -> None:
        self.shop_view.selected_id = str(self.values[0])
        self.shop_view.confirming = None
        await self.shop_view.refresh(interaction)


class ShopButton(discord.ui.Button):
    def __init__(
        self,
        view: "ShopView",
        *,
        action: str,
        label: str,
        emoji: str,
        style: discord.ButtonStyle,
        custom_id: str,
        row: int,
        disabled: bool = False,
    ) -> None:
        super().__init__(
            label=label,
            emoji=emoji,
            style=style,
            custom_id=custom_id,
            row=row,
            disabled=disabled,
        )
        self.shop_view = view
        self.action = action

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.shop_view.cog.handle_shop_action(
            interaction, self.shop_view, self.action
        )


class ShopView(discord.ui.View):
    """Short-lived shop panel controlled only by the invoking member."""

    def __init__(
        self,
        cog: ShopCog,
        *,
        guild_id: int,
        author_id: int,
        prefix: str,
        panel: str = PANEL_STORE,
    ) -> None:
        super().__init__(timeout=SHOP_UI_TIMEOUT_SECONDS)
        self.cog = cog
        self.guild_id = guild_id
        self.author_id = author_id
        self.prefix = prefix
        self.panel = panel if panel in {PANEL_STORE, PANEL_INVENTORY} else PANEL_STORE
        self.message: discord.Message | None = None
        self.selected_id: str | None = None
        self.confirming: tuple[str, str, int] | None = None
        self.catalog: list[dict[str, Any]] = []
        self.inventory: list[dict[str, Any]] = []
        self.balance = 0
        self.active_badge: dict[str, Any] | None = None
        self.reload()

    def stop(self) -> None:
        super().stop()
        views = getattr(self.cog, "_views", None)
        if views is not None:
            views.discard(self)

    def reload(self) -> None:
        snapshot = self.cog.shop_snapshot(self.guild_id, self.author_id)
        self.catalog = snapshot["catalog"]
        self.inventory = snapshot["inventory"]
        self.balance = int(snapshot["balance"])
        self.active_badge = snapshot["active_badge"]
        if self.selected_id and self.selected_item is None:
            self.selected_id = None
            self.confirming = None
        self.rebuild()

    @property
    def panel_items(self) -> list[dict[str, Any]]:
        if self.panel != PANEL_INVENTORY:
            return self.catalog
        catalog_by_id = {str(item["item_id"]): item for item in self.catalog}
        listings: list[dict[str, Any]] = []
        for owned in self.inventory:
            item_id = str(owned["item_id"])
            catalog_item = catalog_by_id.get(item_id)
            listings.append(
                {
                    "item_id": item_id,
                    "name": owned.get("name")
                    or (catalog_item or {}).get("name")
                    or item_id,
                    "item_type": owned.get("item_type")
                    or (catalog_item or {}).get("item_type")
                    or "",
                    "price": int((catalog_item or owned).get("price") or 0),
                    "description": (catalog_item or {}).get("description", ""),
                    "expires_at": owned.get("expires_at"),
                }
            )
        return listings

    @property
    def selected_item(self) -> dict[str, Any] | None:
        if self.selected_id is None:
            return None
        for item in self.panel_items:
            if str(item["item_id"]) == self.selected_id:
                return item
        return None

    def owns_selected(self) -> bool:
        if self.selected_id is None:
            return False
        return any(
            str(item["item_id"]) == self.selected_id for item in self.inventory
        )

    def selected_rental(self) -> bool:
        return (self.selected_item or {}).get("item_type") in RENTAL_ITEM_TYPES

    def can_use_selected(self) -> bool:
        if not self.selected_rental():
            return self.owns_selected()
        return rental_active(next(
            (record for record in self.inventory if record["item_id"] == self.selected_id),
            None,
        ))

    def rebuild(self) -> None:
        self.clear_items()
        if self.panel_items:
            self.add_item(ShopItemSelect(self))
        on_store = self.panel == PANEL_STORE
        self.add_item(
            ShopButton(
                self,
                action="buy",
                label="Gia hạn" if self.selected_rental() and self.owns_selected() else "Mua",
                emoji="🛒",
                style=discord.ButtonStyle.success,
                custom_id=SHOP_BUY_CUSTOM_ID,
                row=1,
                disabled=(
                    not on_store
                    or self.selected_item is None
                    or (self.owns_selected() and not self.selected_rental())
                ),
            )
        )
        self.add_item(
            ShopButton(
                self,
                action="use",
                label="Dùng",
                emoji="✨",
                style=discord.ButtonStyle.primary,
                custom_id=SHOP_USE_CUSTOM_ID,
                row=1,
                disabled=self.selected_item is None or not self.can_use_selected(),
            )
        )
        self.add_item(
            ShopButton(
                self,
                action="unequip",
                label="Gỡ badge",
                emoji="📤",
                style=discord.ButtonStyle.secondary,
                custom_id=SHOP_UNEQUIP_CUSTOM_ID,
                row=1,
                disabled=self.active_badge is None,
            )
        )
        self.add_item(
            ShopButton(
                self,
                action="store",
                label="Cửa hàng",
                emoji="🛍️",
                style=discord.ButtonStyle.blurple if on_store else discord.ButtonStyle.secondary,
                custom_id=SHOP_STORE_CUSTOM_ID,
                row=2,
                disabled=on_store,
            )
        )
        self.add_item(
            ShopButton(
                self,
                action="inventory",
                label="Kho đồ",
                emoji="🎒",
                style=(
                    discord.ButtonStyle.blurple
                    if not on_store
                    else discord.ButtonStyle.secondary
                ),
                custom_id=SHOP_INVENTORY_CUSTOM_ID,
                row=2,
                disabled=not on_store,
            )
        )
        self.add_item(
            ShopButton(
                self,
                action="close",
                label="Đóng",
                emoji="✖️",
                style=discord.ButtonStyle.danger,
                custom_id=SHOP_CLOSE_CUSTOM_ID,
                row=2,
            )
        )

    def build_embed(self) -> discord.Embed:
        if self.panel == PANEL_INVENTORY:
            return self._inventory_embed()
        return self._store_embed()

    def _store_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="🛍️ Cửa hàng Trap Coin",
            description=(
                f"Số dư: **{self.balance:,} TC**\n"
                "Chọn vật phẩm rồi nhấn **Mua**. "
                "Mua xong có thể **Dùng** ngay trên bảng này."
            ),
            color=discord.Color.gold(),
        )
        if not self.catalog:
            embed.add_field(
                name="Danh mục",
                value="Cửa hàng chưa có vật phẩm nào.",
                inline=False,
            )
        else:
            lines = [
                (
                    f"{item_icon(str(item.get('item_type', '')))} "
                    f"**{item['name']}** — {format_price(int(item['price']))}\n"
                    f"`{item['item_id']}` · {item.get('description', 'Không có mô tả')}"
                )
                for item in self.catalog[:8]
            ]
            extra = len(self.catalog) - min(len(self.catalog), 8)
            if extra > 0:
                lines.append(f"… và {extra} vật phẩm nữa trong menu chọn.")
            embed.add_field(
                name="Đang bán",
                value="\n".join(lines)[:1024],
                inline=False,
            )
        embed.add_field(
            name="Đang chọn",
            value=self._selection_text(for_store=True),
            inline=False,
        )
        embed.set_footer(
            text=f"Chỉ người mở bảng được thao tác · {self.prefix}shop"
        )
        return embed

    def _inventory_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title="🎒 Kho đồ Trap Coin",
            description=f"Số dư: **{self.balance:,} TC**",
            color=discord.Color.blurple(),
        )
        if not self.inventory:
            embed.add_field(
                name="Kho",
                value="Bạn chưa sở hữu vật phẩm nào trong shop.",
                inline=False,
            )
        else:
            lines = []
            for record in self.inventory:
                marker = (
                    " · đang dùng"
                    if self.active_badge
                    and self.active_badge.get("item_id") == record["item_id"]
                    else ""
                )
                lines.append(
                    f"• `{record['item_id']}` — {record['name']}{marker}"
                )
                if record.get("item_type") in RENTAL_ITEM_TYPES:
                    lines.append(rental_status(record))
            embed.add_field(
                name="Đã mua",
                value="\n".join(lines)[:1024],
                inline=False,
            )
        embed.add_field(
            name="Đang chọn",
            value=self._selection_text(for_store=False),
            inline=False,
        )
        embed.set_footer(
            text=f"Chỉ người mở bảng được thao tác · {self.prefix}shop"
        )
        return embed

    def _selection_text(self, *, for_store: bool) -> str:
        item = self.selected_item
        if item is None:
            return "Chưa chọn vật phẩm."
        owned = self.owns_selected()
        parts = [
            f"{item_icon(str(item.get('item_type', '')))} **{item['name']}**",
            f"Loại: {item_type_label(str(item.get('item_type', '')))}",
            f"Giá: {format_price(int(item.get('price') or 0))}",
        ]
        if self.selected_rental():
            parts.append("Mỗi lần mua thêm 30 ngày, tính từ lúc thanh toán hoặc hạn còn lại.")
            record = next((row for row in self.inventory if row["item_id"] == self.selected_id), None)
            if record:
                parts.append(rental_status(record))
        elif owned:
            parts.append("Bạn đã sở hữu vật phẩm này.")
        if (
            for_store
            and self.confirming == ("buy", str(item["item_id"]), int(item["price"]))
            and (not owned or self.selected_rental())
        ):
            parts.append(
                f"Nhấn **{'Gia hạn' if owned else 'Mua'}** lần nữa để xác nhận với "
                f"{format_price(int(item.get('price') or 0))}."
            )
        return "\n".join(parts)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.guild is None or interaction.guild.id != self.guild_id:
            message = "Bảng này chỉ dùng được trong server đã mở nó."
        elif interaction.user.id != self.author_id:
            message = (
                "Chỉ người mở cửa hàng được thao tác. "
                f"Hãy mở bảng riêng bằng `{self.prefix}shop`."
            )
        elif self.is_finished() or getattr(self.cog, "_unloading", False):
            message = (
                f"Bảng đã đóng hoặc hết hạn. Hãy gọi lại `{self.prefix}shop`."
            )
        else:
            return True
        await send_private(interaction, message)
        return False

    def disable_all(self) -> None:
        for item in self.children:
            item.disabled = True

    async def refresh(self, interaction: discord.Interaction) -> None:
        self.reload()
        try:
            if interaction.response.is_done():
                if self.message is not None:
                    await self.message.edit(
                        embed=self.build_embed(),
                        view=self,
                        allowed_mentions=NO_MENTIONS,
                    )
                return
            await interaction.response.edit_message(
                embed=self.build_embed(),
                view=self,
                allowed_mentions=NO_MENTIONS,
            )
        except discord.HTTPException:
            logger.debug("Could not refresh shop panel", exc_info=True)

    async def close_panel(self, interaction: discord.Interaction) -> None:
        self.disable_all()
        self.stop()
        try:
            await interaction.response.edit_message(
                content="Đã đóng cửa hàng.",
                embed=None,
                view=self,
                allowed_mentions=NO_MENTIONS,
            )
        except discord.HTTPException:
            logger.debug("Could not close shop panel", exc_info=True)

    async def on_timeout(self) -> None:
        self.stop()
        self.disable_all()
        if self.message is None:
            return
        try:
            await self.message.edit(view=self, allowed_mentions=NO_MENTIONS)
        except discord.HTTPException:
            logger.debug(
                "Could not disable expired shop UI for user %s",
                self.author_id,
                exc_info=True,
            )
