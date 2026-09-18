"""Interactive Trap Coin shop hub: catalog, inventory, and purchases."""

from __future__ import annotations

import asyncio
import logging

import discord
from discord.ext import commands

from cogs.economy._shop_catalog import CatalogProduct
from cogs.economy._shop_helpers import (
    CUSTOM_ROLE_ITEM_ID,
    ITEM_TYPE_BADGE,
    ITEM_TYPE_CUSTOM_ROLE,
    ITEM_TYPE_ROLE,
    MAX_CATALOG_ITEMS,
    format_price,
    is_reserved_item_id,
    normalize_item_id,
    validate_price,
)
from cogs.economy._shop_products import (
    get_shop_product,
    register_shop_product,
    unregister_shop_product,
)
from cogs.economy._shop_store import ShopStore
from cogs.economy._shop_ui import (
    NO_MENTIONS,
    PANEL_INVENTORY,
    PANEL_STORE,
    ShopView,
    send_private,
)
from cogs.roles._role_safety import dangerous_permission_names


logger = logging.getLogger(__name__)


class ShopCog(commands.Cog):
    """Guild-specific Trap Coin catalog, inventory, and interactive shop."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.db = bot.db
        self.store = ShopStore(bot.db)
        self._views: set[ShopView] = set()
        self._unloading = False
        self._purchase_locks: dict[tuple[int, int], asyncio.Lock] = {}
        self._catalog_products = (
            CatalogProduct(ITEM_TYPE_ROLE, self.store),
            CatalogProduct(ITEM_TYPE_BADGE, self.store),
        )
        for product in self._catalog_products:
            register_shop_product(product)

    def cog_unload(self) -> None:
        self._unloading = True
        for view in tuple(self._views):
            view.stop()
        self._views.clear()
        for product in self._catalog_products:
            unregister_shop_product(product.item_type)

    def _purchase_lock(self, guild_id: int, user_id: int) -> asyncio.Lock:
        return self._purchase_locks.setdefault((guild_id, user_id), asyncio.Lock())

    def shop_snapshot(self, guild_id: int, user_id: int) -> dict:
        return {
            "catalog": self.store.list_enabled(guild_id),
            "inventory": self.store.list_inventory(guild_id, user_id),
            "balance": self.store.get_balance(user_id),
            "active_badge": self.store.get_active_badge(user_id, guild_id),
        }

    @staticmethod
    def _member_can_manage_role(
        member: discord.Member, role: discord.Role
    ) -> bool:
        return member == member.guild.owner or member.top_role > role

    async def _open_shop(self, ctx: commands.Context) -> None:
        assert ctx.guild is not None
        view = ShopView(
            self,
            guild_id=ctx.guild.id,
            author_id=ctx.author.id,
            prefix=ctx.clean_prefix,
        )
        self._views.add(view)
        view.message = await ctx.reply(
            embed=view.build_embed(),
            view=view,
            mention_author=False,
            allowed_mentions=NO_MENTIONS,
        )

    def _unavailable_product_message(self) -> str:
        return "Vật phẩm này tạm không khả dụng."

    async def _purchase_item(
        self,
        *,
        guild: discord.Guild,
        member: discord.Member,
        item_id: str,
    ) -> tuple[bool, str]:
        try:
            normalized_id = normalize_item_id(item_id)
        except ValueError:
            return False, "Item ID không hợp lệ."

        item = self.store.find_enabled(guild.id, normalized_id)
        if item is None:
            return False, "Không tìm thấy vật phẩm đang bán với ID đó."

        product = get_shop_product(str(item.get("item_type", "")))
        if product is None:
            return False, self._unavailable_product_message()
        denial = product.buy_denial(guild, member, item)
        if denial:
            return False, denial

        lock = self._purchase_lock(guild.id, member.id)
        if lock.locked():
            return False, "Đang xử lý giao dịch. Vui lòng chờ một chút."
        async with lock:
            result = self.store.purchase(
                guild_id=guild.id,
                user_id=member.id,
                item=item,
            )
        return result.success, result.message

    async def _use_item(
        self,
        *,
        guild: discord.Guild,
        member: discord.Member,
        item_id: str,
        source: discord.Interaction | commands.Context,
    ) -> str | None:
        try:
            normalized_id = normalize_item_id(item_id)
        except ValueError:
            return "Item ID không hợp lệ."

        owned = self.store.owned_record(guild.id, member.id, normalized_id)
        if owned is None:
            return "Bạn chưa sở hữu vật phẩm này."

        item = self.store.find_item(guild.id, normalized_id)
        if item is None:
            return "Vật phẩm này không còn tồn tại trong catalog."

        product = get_shop_product(str(item.get("item_type", "")))
        if product is None:
            return self._unavailable_product_message()
        return await product.use_item(
            guild=guild,
            member=member,
            item=item,
            source=source,
        )

    async def handle_shop_action(
        self,
        interaction: discord.Interaction,
        view: ShopView,
        action: str,
    ) -> None:
        if interaction.guild is None:
            await send_private(interaction, "Cửa hàng chỉ dùng trong server.")
            return
        member = interaction.guild.get_member(interaction.user.id)
        if member is None:
            member = interaction.user
        if getattr(member, "id", None) is None:
            await send_private(interaction, "Cửa hàng chỉ dùng trong server.")
            return

        if action == "close":
            await view.close_panel(interaction)
            return
        if action == "store":
            view.panel = PANEL_STORE
            view.confirming = None
            await view.refresh(interaction)
            return
        if action == "inventory":
            view.panel = PANEL_INVENTORY
            view.confirming = None
            await view.refresh(interaction)
            return
        if action == "unequip":
            self.store.clear_active_badge(member.id)
            await view.refresh(interaction)
            await send_private(interaction, "Đã gỡ badge đang trang bị.")
            return

        item = view.selected_item
        if item is None:
            await send_private(interaction, "Hãy chọn một vật phẩm trước.")
            return
        item_id = str(item["item_id"])

        if action == "buy":
            if view.owns_selected():
                await send_private(interaction, "Bạn đã sở hữu vật phẩm này.")
                return
            token = ("buy", item_id)
            if view.confirming != token:
                view.confirming = token
                await view.refresh(interaction)
                return
            view.confirming = None
            success, message = await self._purchase_item(
                guild=interaction.guild,
                member=member,
                item_id=item_id,
            )
            await view.refresh(interaction)
            await send_private(interaction, message)
            return

        if action == "use":
            notice = await self._use_item(
                guild=interaction.guild,
                member=member,
                item_id=item_id,
                source=interaction,
            )
            if notice is None:
                view.stop()
                return
            await view.refresh(interaction)
            await send_private(interaction, notice)

    @commands.group(
        name="shop",
        aliases=["store"],
        invoke_without_command=True,
        help="Mở cửa hàng Trap Coin tương tác.",
    )
    @commands.guild_only()
    async def shop(self, ctx: commands.Context) -> None:
        await self._open_shop(ctx)

    @shop.command(name="buy", help="Mua một vật phẩm trong shop.")
    @commands.guild_only()
    @commands.cooldown(2, 5, commands.BucketType.user)
    async def shop_buy(self, ctx: commands.Context, item_id: str) -> None:
        assert ctx.guild is not None
        _, message = await self._purchase_item(
            guild=ctx.guild,
            member=ctx.author,
            item_id=item_id,
        )
        await ctx.send(message, allowed_mentions=NO_MENTIONS)

    @shop.command(name="inventory", aliases=["inv"], help="Xem kho vật phẩm.")
    @commands.guild_only()
    async def shop_inventory(
        self,
        ctx: commands.Context,
        member: discord.Member | None = None,
    ) -> None:
        assert ctx.guild is not None
        target = member or ctx.author
        owned = self.store.list_inventory(ctx.guild.id, target.id)
        if not owned:
            await ctx.send(
                f"{target.mention} chưa sở hữu vật phẩm nào trong shop.",
                allowed_mentions=NO_MENTIONS,
            )
            return

        active_badge = self.store.get_active_badge(target.id, ctx.guild.id)
        lines = []
        for record in owned[:MAX_CATALOG_ITEMS]:
            marker = (
                " · đang dùng"
                if active_badge and active_badge.get("item_id") == record["item_id"]
                else ""
            )
            lines.append(f"• {record['item_id']} — {record['name']}{marker}")

        embed = discord.Embed(
            title=f"🎒 Kho đồ của {target.display_name}",
            description="\n".join(lines),
            color=discord.Color.blurple(),
        )
        await ctx.send(embed=embed, allowed_mentions=NO_MENTIONS)

    @shop.command(name="use", help="Dùng role, badge, hoặc custom role đã mua.")
    @commands.guild_only()
    async def shop_use(self, ctx: commands.Context, item_id: str) -> None:
        assert ctx.guild is not None
        notice = await self._use_item(
            guild=ctx.guild,
            member=ctx.author,
            item_id=item_id,
            source=ctx,
        )
        if notice:
            await ctx.send(notice, allowed_mentions=NO_MENTIONS)

    @shop.command(name="unequip", help="Gỡ badge đang trang bị.")
    @commands.guild_only()
    async def shop_unequip(self, ctx: commands.Context) -> None:
        self.store.clear_active_badge(ctx.author.id)
        await ctx.send("Đã gỡ badge đang trang bị.")

    @shop.command(name="add_role", help="Thêm role vào shop.")
    @commands.guild_only()
    @commands.has_guild_permissions(manage_guild=True)
    async def shop_add_role(
        self,
        ctx: commands.Context,
        item_id: str,
        price: int,
        role: discord.Role,
        *,
        description: str = "",
    ) -> None:
        assert ctx.guild is not None
        try:
            normalized_id = normalize_item_id(item_id)
            valid_price = validate_price(price)
        except ValueError as exc:
            await ctx.send(str(exc))
            return
        if is_reserved_item_id(normalized_id):
            await ctx.send(
                f"ID `{CUSTOM_ROLE_ITEM_ID}` dành riêng cho custom role. "
                f"Dùng `{ctx.clean_prefix}shop add_custom_role`."
            )
            return

        if role.is_default() or role.managed:
            await ctx.send(
                "Không thể bán role mặc định hoặc role được integration quản lý."
            )
            return
        dangerous = dangerous_permission_names(role.permissions)
        if dangerous:
            await ctx.send(
                "Không thể bán role có quyền quản trị: " + ", ".join(dangerous)
            )
            return
        if not self._member_can_manage_role(ctx.author, role):
            await ctx.send(
                "Bạn chỉ có thể thêm role thấp hơn role cao nhất của mình."
            )
            return
        if role >= ctx.guild.me.top_role:
            await ctx.send("Role này phải thấp hơn role cao nhất của bot.")
            return

        document = self.store.upsert_item(
            guild_id=ctx.guild.id,
            item_id=normalized_id,
            name=role.name,
            description=description or f"Role {role.name}",
            price=valid_price,
            item_type=ITEM_TYPE_ROLE,
            updated_by=ctx.author.id,
            role_id=role.id,
        )
        await ctx.send(
            f"Đã lưu **{document['name']}** ({normalized_id}) với giá "
            f"{format_price(valid_price)}.",
            allowed_mentions=NO_MENTIONS,
        )

    @shop.command(name="add_badge", help="Thêm badge vào shop.")
    @commands.guild_only()
    @commands.has_guild_permissions(manage_guild=True)
    async def shop_add_badge(
        self,
        ctx: commands.Context,
        item_id: str,
        price: int,
        *,
        display_name: str,
    ) -> None:
        assert ctx.guild is not None
        try:
            normalized_id = normalize_item_id(item_id)
            valid_price = validate_price(price)
        except ValueError as exc:
            await ctx.send(str(exc))
            return
        if is_reserved_item_id(normalized_id):
            await ctx.send(
                f"ID `{CUSTOM_ROLE_ITEM_ID}` dành riêng cho custom role. "
                f"Dùng `{ctx.clean_prefix}shop add_custom_role`."
            )
            return

        document = self.store.upsert_item(
            guild_id=ctx.guild.id,
            item_id=normalized_id,
            name=display_name,
            description=f"Badge {display_name}",
            price=valid_price,
            item_type=ITEM_TYPE_BADGE,
            updated_by=ctx.author.id,
        )
        await ctx.send(
            f"Đã lưu **{document['name']}** ({normalized_id}) với giá "
            f"{format_price(valid_price)}.",
            allowed_mentions=NO_MENTIONS,
        )

    @shop.command(name="add_custom_role", help="Thêm custom role vào shop.")
    @commands.guild_only()
    @commands.has_guild_permissions(manage_guild=True)
    async def shop_add_custom_role(
        self,
        ctx: commands.Context,
        price: int,
        *,
        description: str = "",
    ) -> None:
        assert ctx.guild is not None
        try:
            valid_price = validate_price(price)
        except ValueError as exc:
            await ctx.send(str(exc))
            return

        document = self.store.upsert_item(
            guild_id=ctx.guild.id,
            item_id=CUSTOM_ROLE_ITEM_ID,
            name="Custom role",
            description=(
                description
                or "Tạo một custom role riêng với tên và màu của bạn."
            ),
            price=valid_price,
            item_type=ITEM_TYPE_CUSTOM_ROLE,
            updated_by=ctx.author.id,
        )
        await ctx.send(
            f"Đã lưu **{document['name']}** ({CUSTOM_ROLE_ITEM_ID}) với giá "
            f"{format_price(valid_price)}.",
            allowed_mentions=NO_MENTIONS,
        )

    @shop.command(name="remove", aliases=["disable"], help="Ẩn vật phẩm khỏi shop.")
    @commands.guild_only()
    @commands.has_guild_permissions(manage_guild=True)
    async def shop_remove(self, ctx: commands.Context, item_id: str) -> None:
        assert ctx.guild is not None
        try:
            normalized_id = normalize_item_id(item_id)
        except ValueError:
            await ctx.send("Item ID không hợp lệ.")
            return
        if not self.store.disable_item(ctx.guild.id, normalized_id):
            await ctx.send("Không tìm thấy vật phẩm đó.")
            return
        await ctx.send(f"Đã ẩn {normalized_id} khỏi shop.")

    @shop.error
    async def shop_error(
        self, ctx: commands.Context, error: commands.CommandError
    ) -> None:
        if isinstance(error, commands.CommandOnCooldown):
            await ctx.send(f"Vui lòng thử lại sau {error.retry_after:.1f} giây.")
            return
        if isinstance(error, commands.MissingPermissions):
            await ctx.send("Bạn cần quyền Manage Server để quản lý shop.")
            return
        if isinstance(error, commands.BadArgument):
            await ctx.send("Tham số không hợp lệ. Dùng lệnh help để xem cú pháp.")
            return
        raise error


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ShopCog(bot))
