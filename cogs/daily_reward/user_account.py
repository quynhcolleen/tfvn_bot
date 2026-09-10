from datetime import timezone
import logging

import discord
from discord.ext import commands
from pymongo import DESCENDING, ReturnDocument
from pymongo.errors import PyMongoError


logger = logging.getLogger(__name__)

MAX_ADMIN_AMOUNT = 1_000_000_000

TRANSACTION_LABELS = {
    "shop_purchase": "Mua vật phẩm",
    "slot_machine_play": "Chơi slot",
    "slot_machine_win": "Thắng slot",
    "blackjack_play": "Cược Blackjack",
    "blackjack_win": "Thắng Blackjack",
    "blackjack_push": "Hòa Blackjack",
    "blackjack_refund": "Hoàn cược Blackjack",
    "poker_play": "Cược Poker",
    "poker_win": "Thắng Poker",
    "poker_push": "Hòa Poker",
    "poker_refund": "Hoàn cược Poker",
    "vietnamese_king_win": "Thắng Vua Tiếng Việt",
    "word_connect_win": "Thắng Nối Từ",
    "big_speaker": "Big speaker / Loa",
    "big_speaker_refund": "Hoàn big speaker",
    "admin_add_tc": "Admin cộng TC",
    "admin_remove_tc": "Admin trừ TC",
    "admin_set_tc": "Admin đặt TC",
    "cultivation_exchange_buy": "Tiên Lộ: mua Linh Thạch",
    "cultivation_exchange_sell": "Tiên Lộ: bán Linh Thạch",
}


class UserAccountCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.db = bot.db

    @staticmethod
    def _clean_reason(reason: str) -> str:
        return " ".join(reason.split())[:200] if reason else ""

    @staticmethod
    def _validate_positive_amount(amount: int) -> str | None:
        if amount <= 0 or amount > MAX_ADMIN_AMOUNT:
            return f"Số TC phải từ **1** đến **{MAX_ADMIN_AMOUNT:,}**."
        return None

    @staticmethod
    def _validate_set_amount(amount: int) -> str | None:
        if amount < 0 or amount > MAX_ADMIN_AMOUNT:
            return f"Số TC phải từ **0** đến **{MAX_ADMIN_AMOUNT:,}**."
        return None

    def _log_admin_transaction(
        self,
        *,
        ctx: commands.Context,
        member: discord.Member,
        tx_type: str,
        transaction_type: str,
        amount: int,
        balance_after: int,
        reason: str,
    ) -> None:
        try:
            self.db["transaction_logs"].insert_one(
                {
                    "guild_id": ctx.guild.id if ctx.guild else None,
                    "user_id": member.id,
                    "type": tx_type,
                    "transaction_type": transaction_type,
                    "amount": amount,
                    "balance_after": balance_after,
                    "admin_id": ctx.author.id,
                    "reason": reason or None,
                    "timestamp": discord.utils.utcnow(),
                }
            )
        except PyMongoError:
            logger.exception(
                "Failed to log %s for user %s", tx_type, member.id
            )

    async def _admin_error(
        self,
        ctx: commands.Context,
        error: commands.CommandError,
        *,
        usage: str,
        action: str,
    ) -> None:
        if isinstance(error, commands.MissingPermissions):
            await ctx.send(
                f"Bạn cần quyền Administrator để {action}.",
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return
        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(
                f"Cú pháp: `{ctx.prefix}{usage}`",
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return
        if isinstance(error, commands.BadArgument):
            await ctx.send(
                "Member hoặc số TC không hợp lệ.",
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return
        logger.exception("Unexpected error in admin TC command (%s)", action)
        await ctx.send(
            f"Đã xảy ra lỗi khi {action}.",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @commands.command(name="user_balance", aliases=["balance"])
    async def user_balance(self, ctx: commands.Context) -> None:
        user_id = ctx.author.id
        self.db["user_accounts"].update_one(
            {"user_id": user_id},
            {"$setOnInsert": {"balance": 0}},
            upsert=True,
        )
        account = self.db["user_accounts"].find_one({"user_id": user_id}) or {}
        balance = int(account.get("balance", 0))
        badge = account.get("active_badge") or {}
        badge_text = ""
        if not ctx.guild or badge.get("guild_id") == ctx.guild.id:
            if badge.get("name"):
                badge_text = f" · Badge: **{badge['name']}**"
        await ctx.send(
            f"{ctx.author.mention}, số dư của bạn: **{balance:,} TC**{badge_text}",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @commands.command(name="user_transactions", aliases=["transactions"])
    async def user_transactions(self, ctx: commands.Context) -> None:
        transactions = list(
            self.db["transaction_logs"]
            .find({"user_id": ctx.author.id})
            .sort("timestamp", DESCENDING)
            .limit(10)
        )
        if not transactions:
            await ctx.send("Bạn chưa có giao dịch nào.")
            return

        lines = []
        for transaction in transactions:
            transaction_type = transaction.get("transaction_type", "debit")
            sign = "+" if transaction_type == "credit" else "−"
            amount = int(transaction.get("amount", 0))
            label = TRANSACTION_LABELS.get(
                transaction.get("type"), transaction.get("type", "Giao dịch")
            )
            if transaction.get("item_id"):
                label += f" ({transaction['item_id']})"
            timestamp = transaction.get("timestamp")
            if timestamp:
                if timestamp.tzinfo is None:
                    timestamp = timestamp.replace(tzinfo=timezone.utc)
                when = discord.utils.format_dt(timestamp, style="R")
            else:
                when = "không rõ thời gian"
            lines.append(f"{sign}{amount:,} TC · {label} · {when}")

        embed = discord.Embed(
            title=f"💳 Giao dịch của {ctx.author.display_name}",
            description="\n".join(lines),
            color=discord.Color.blurple(),
        )
        await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())

    @commands.command(
        name="add_tc",
        aliases=["give_tc", "grant_tc"],
        help="Admin: cộng Trap Coin cho member.",
    )
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def add_tc(
        self,
        ctx: commands.Context,
        member: discord.Member,
        amount: int,
        *,
        reason: str = "",
    ) -> None:
        if member.bot:
            await ctx.send(
                "Không thể cộng TC cho bot.",
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return
        amount_error = self._validate_positive_amount(amount)
        if amount_error:
            await ctx.send(
                amount_error,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        cleaned_reason = self._clean_reason(reason)
        try:
            account = self.db["user_accounts"].find_one_and_update(
                {"user_id": member.id},
                {
                    "$inc": {"balance": amount},
                    "$setOnInsert": {"user_id": member.id},
                },
                upsert=True,
                return_document=ReturnDocument.AFTER,
            )
        except PyMongoError:
            logger.exception("Failed to add TC for user %s", member.id)
            await ctx.send(
                "Không thể cập nhật số dư. Vui lòng thử lại.",
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        new_balance = int((account or {}).get("balance", amount))
        self._log_admin_transaction(
            ctx=ctx,
            member=member,
            tx_type="admin_add_tc",
            transaction_type="credit",
            amount=amount,
            balance_after=new_balance,
            reason=cleaned_reason,
        )
        reason_text = f" · Lý do: {cleaned_reason}" if cleaned_reason else ""
        await ctx.send(
            f"Đã cộng **{amount:,} TC** cho {member.mention}. "
            f"Số dư mới: **{new_balance:,} TC**.{reason_text}",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @add_tc.error
    async def add_tc_error(
        self, ctx: commands.Context, error: commands.CommandError
    ) -> None:
        await self._admin_error(
            ctx,
            error,
            usage="add_tc @member <số_tc> [lý do]",
            action="cộng TC",
        )

    @commands.command(
        name="remove_tc",
        aliases=["sub_tc", "subtract_tc", "take_tc"],
        help="Admin: trừ Trap Coin của member.",
    )
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def remove_tc(
        self,
        ctx: commands.Context,
        member: discord.Member,
        amount: int,
        *,
        reason: str = "",
    ) -> None:
        if member.bot:
            await ctx.send(
                "Không thể trừ TC của bot.",
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return
        amount_error = self._validate_positive_amount(amount)
        if amount_error:
            await ctx.send(
                amount_error,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        cleaned_reason = self._clean_reason(reason)
        try:
            account = self.db["user_accounts"].find_one_and_update(
                {"user_id": member.id, "balance": {"$gte": amount}},
                {"$inc": {"balance": -amount}},
                return_document=ReturnDocument.AFTER,
            )
        except PyMongoError:
            logger.exception("Failed to remove TC for user %s", member.id)
            await ctx.send(
                "Không thể cập nhật số dư. Vui lòng thử lại.",
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        if account is None:
            current = self.db["user_accounts"].find_one({"user_id": member.id}) or {}
            balance = int(current.get("balance", 0))
            await ctx.send(
                f"{member.mention} không đủ TC để trừ **{amount:,}**. "
                f"Số dư hiện tại: **{balance:,} TC**.",
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        new_balance = int(account.get("balance", 0))
        self._log_admin_transaction(
            ctx=ctx,
            member=member,
            tx_type="admin_remove_tc",
            transaction_type="debit",
            amount=amount,
            balance_after=new_balance,
            reason=cleaned_reason,
        )
        reason_text = f" · Lý do: {cleaned_reason}" if cleaned_reason else ""
        await ctx.send(
            f"Đã trừ **{amount:,} TC** của {member.mention}. "
            f"Số dư mới: **{new_balance:,} TC**.{reason_text}",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @remove_tc.error
    async def remove_tc_error(
        self, ctx: commands.Context, error: commands.CommandError
    ) -> None:
        await self._admin_error(
            ctx,
            error,
            usage="remove_tc @member <số_tc> [lý do]",
            action="trừ TC",
        )

    @commands.command(
        name="set_tc",
        aliases=["set_balance"],
        help="Admin: đặt số dư Trap Coin của member.",
    )
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def set_tc(
        self,
        ctx: commands.Context,
        member: discord.Member,
        amount: int,
        *,
        reason: str = "",
    ) -> None:
        if member.bot:
            await ctx.send(
                "Không thể đặt TC cho bot.",
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return
        amount_error = self._validate_set_amount(amount)
        if amount_error:
            await ctx.send(
                amount_error,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        cleaned_reason = self._clean_reason(reason)
        try:
            before = self.db["user_accounts"].find_one({"user_id": member.id}) or {}
            previous = int(before.get("balance", 0))
            if previous == amount:
                await ctx.send(
                    f"{member.mention} đã có đúng **{amount:,} TC**. Không thay đổi.",
                    allowed_mentions=discord.AllowedMentions.none(),
                )
                return

            account = self.db["user_accounts"].find_one_and_update(
                {"user_id": member.id},
                {
                    "$set": {"balance": amount},
                    "$setOnInsert": {"user_id": member.id},
                },
                upsert=True,
                return_document=ReturnDocument.AFTER,
            )
        except PyMongoError:
            logger.exception("Failed to set TC for user %s", member.id)
            await ctx.send(
                "Không thể cập nhật số dư. Vui lòng thử lại.",
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        new_balance = int((account or {}).get("balance", amount))
        delta = new_balance - previous
        self._log_admin_transaction(
            ctx=ctx,
            member=member,
            tx_type="admin_set_tc",
            transaction_type="credit" if delta > 0 else "debit",
            amount=abs(delta),
            balance_after=new_balance,
            reason=cleaned_reason,
        )
        reason_text = f" · Lý do: {cleaned_reason}" if cleaned_reason else ""
        await ctx.send(
            f"Đã đặt số dư của {member.mention} thành **{new_balance:,} TC** "
            f"(trước đó **{previous:,} TC**).{reason_text}",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @set_tc.error
    async def set_tc_error(
        self, ctx: commands.Context, error: commands.CommandError
    ) -> None:
        await self._admin_error(
            ctx,
            error,
            usage="set_tc @member <số_tc> [lý do]",
            action="đặt TC",
        )

    @commands.command(
        name="check_tc",
        aliases=["tc_balance"],
        help="Admin: xem số dư Trap Coin của member.",
    )
    @commands.guild_only()
    @commands.has_permissions(administrator=True)
    async def check_tc(
        self,
        ctx: commands.Context,
        member: discord.Member | None = None,
    ) -> None:
        target = member or ctx.author
        if target.bot:
            await ctx.send(
                "Bot không có số dư Trap Coin.",
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        try:
            self.db["user_accounts"].update_one(
                {"user_id": target.id},
                {"$setOnInsert": {"balance": 0}},
                upsert=True,
            )
            account = self.db["user_accounts"].find_one({"user_id": target.id}) or {}
        except PyMongoError:
            logger.exception("Failed to check TC for user %s", target.id)
            await ctx.send(
                "Không thể đọc số dư. Vui lòng thử lại.",
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        balance = int(account.get("balance", 0))
        await ctx.send(
            f"Số dư của {target.mention}: **{balance:,} TC**.",
            allowed_mentions=discord.AllowedMentions.none(),
        )

    @check_tc.error
    async def check_tc_error(
        self, ctx: commands.Context, error: commands.CommandError
    ) -> None:
        await self._admin_error(
            ctx,
            error,
            usage="check_tc [@member]",
            action="xem số dư TC",
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(UserAccountCog(bot))
