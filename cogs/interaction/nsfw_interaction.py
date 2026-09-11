import asyncio
import random
from collections import deque
from datetime import datetime, timedelta

import discord  # pyright: ignore[reportMissingImports]
from discord.ext import commands  # pyright: ignore[reportMissingImports]


# Tránh lặp gif đcmmmmmmm
class GifPicker:
    def __init__(self, gifs: list[str], history_size: int = 5):
        self.gifs = gifs
        self.recent = deque(maxlen=history_size)

    def pick(self) -> str | None:
        if not self.gifs:
            return None
        candidates = [g for g in self.gifs if g not in self.recent]
        gif = random.choice(candidates or self.gifs)
        self.recent.append(gif)
        return gif

class NSFWInteractionCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.bj_picker = self._load_gif_picker("BLOWJOB_GIFS")
        self.hj_picker = self._load_gif_picker("HANDJOB_GIFS")
        self.fj_picker = self._load_gif_picker("FOOTJOB_GIFS")
        self.aj_picker = self._load_gif_picker("ASSJOB_GIFS")
        self.tj_picker = self._load_gif_picker("THIGHJOB_GIFS")
        self.spank_picker = self._load_gif_picker("SPANK_GIFS")
        self.rj_picker = self._load_gif_picker("RIMJOB_GIFS")
        self.frot_picker = self._load_gif_picker("FROTTING_GIFS")
        self.fuck_picker = self._load_gif_picker("FUCKING_GIFS")
        self.cream_picker = self._load_gif_picker("CREAMPIE_GIFS")
        self.threesome_picker = self._load_gif_picker("THREESOME_GIFS")
        self.orgy_picker = self._load_gif_picker("ORGY_GIFS")
        self.gangbang_picker = self._load_gif_picker("GANGBANG_GIFS")
        self.ride_picker = self._load_gif_picker("RIDE_GIFS")
        self.fingering_picker = self._load_gif_picker("FINGERING_GIFS")
        self.facesit_picker = self._load_gif_picker("FACESIT_GIFS")
        self.db = bot.db
        self.KING_ROLE_ID = int(self.bot.global_vars["KING_ROLE_ID"])
        self.QUEEN_ROLE_ID = int(self.bot.global_vars["QUEEN_ROLE_ID"])
        self.NSFW_INTERACTIONS = [
            "bj",
            "rj",
            "hj",
            "fj",
            "aj",
            "tj",
            "spank",
            "frot",
            "fuck",
            "cream",
            "3some",
            "orgy",
            "gangbang",
            "ride",
            "fingering",
            "facesit",
        ]

    def _load_gif_picker(self, variable_name: str) -> GifPicker:
        """Load GIF list from Mongo global_vars. Blank entries skipped; empty list OK."""
        raw = self.bot.global_vars.get(variable_name)
        if not isinstance(raw, list):
            gifs: list[str] = []
        else:
            gifs = [
                gif.strip()
                for gif in raw
                if isinstance(gif, str) and gif.strip()
            ]
        history = max(len(gifs), 1)
        return GifPicker(gifs, history_size=history)

    def format_relative_time_vn(self, dt: datetime) -> str:
        now = discord.utils.utcnow()
        diff = dt - now
        if diff.total_seconds() <= 0:
            return "đã hết"
        seconds = int(diff.total_seconds())
        if seconds < 60:
            return f"trong {seconds} giây"
        minutes = seconds // 60
        if minutes < 60:
            return f"trong {minutes} phút"
        hours = minutes // 60
        if hours < 24:
            return f"trong {hours} giờ"
        days = hours // 24
        return f"trong {days} ngày"

    def check_if_user_is_locked(self, member_id: int) -> bool:
        return self.db["nsfw_settings"].find_one({
            "user_locked": member_id,
            "lock_until": {"$gte": discord.utils.utcnow()}
        }) is not None

    def get_remaining_lock_time(self, member_id: int) -> dict | None:
        return self.db["nsfw_settings"].find_one({
            "user_locked": member_id,
            "lock_until": {"$gte": discord.utils.utcnow()}
        })

    async def _nsfw_guard(self, ctx: commands.Context) -> bool:
        if ctx.channel.is_nsfw():
            return True
        await ctx.message.add_reaction("⚠️")
        warn_msg = await ctx.reply("🔞 Dùng lệnh này trong channel NSFW nhé.")
        await asyncio.sleep(5)
        await warn_msg.delete()
        await ctx.message.delete()
        return False

    async def _handle_interaction(
            self,
            ctx,
            member,
            log_action,
            action,
            title,
            description,
            gif_picker,
            self_allowed=False
        ):
        if not await self._nsfw_guard(ctx):
            return
        if not self_allowed and member == ctx.author:
            await ctx.send("Bạn không thể tự tương tác với mình được đâu 😳")
            return
        if self.check_if_user_is_locked(ctx.author.id):
            lock_time = self.get_remaining_lock_time(ctx.author.id)['lock_until']
            await ctx.send(f"{member.mention} hiện đang bị khoá lệnh NSFW, không thể thực hiện tương tác này {self.format_relative_time_vn(lock_time)}.")
            return
        coefficient = 3 if self.KING_ROLE_ID in [r.id for r in ctx.author.roles] else 1
        self.db["interactions"].insert_one({
            "message_id": ctx.message.id,
            "initMember": ctx.author.id,
            "targetMember": member.id,
            "action": log_action,
            "coefficient": coefficient,
            "created_at": discord.utils.utcnow(),
        })
        embed = discord.Embed(title=title, description=description)
        gif_url = gif_picker.pick()
        if gif_url:
            embed.set_image(url=gif_url)
        if coefficient > 1:
            embed.set_footer(text=f"Bị {ctx.author.name} {action} x{coefficient} lần")
        await ctx.send(embed=embed)

    async def _handle_interaction_multi(
            self,
            ctx,
            members,
            log_action,
            action,
            title,
            description,
            gif_picker,
            self_allowed=False
        ):
        if not await self._nsfw_guard(ctx):
            return
        if not self_allowed and ctx.author in members:
            await ctx.send("Bạn không thể tự tương tác với mình được đâu 😳")
            return
        if self.check_if_user_is_locked(ctx.author.id):
            lock_time = self.get_remaining_lock_time(ctx.author.id)["lock_until"]
            await ctx.send(
                f"{ctx.author.mention} hiện đang bị khoá lệnh NSFW, không thể thực hiện tương tác này {self.format_relative_time_vn(lock_time)}."
            )
            return
        coefficient = 3 if self.KING_ROLE_ID in [r.id for r in ctx.author.roles] else 1
        target_ids = [member.id for member in members]
        self.db["interactions"].insert_one({
            "message_id": ctx.message.id,
            "initMember": ctx.author.id,
            "targetMember": target_ids[0],
            "targetMembers": target_ids,
            "action": log_action,
            "coefficient": coefficient,
            "created_at": discord.utils.utcnow(),
        })
        embed = discord.Embed(title=title, description=description)
        gif_url = gif_picker.pick()
        if gif_url:
            embed.set_image(url=gif_url)
        if coefficient > 1:
            embed.set_footer(text=f"Bị {ctx.author.name} {action} x{coefficient} lần")
        await ctx.send(embed=embed)

    # Commands
    @commands.command(name="nsfwrule")
    async def nsfw_rule(self, ctx):
        if not await self._nsfw_guard(ctx):
            return

        rule_text = (
            "🔞 **Quy Định Sử Dụng Lệnh NSFW** 🔞\n"
            "1. **Kênh NSFW**: Chỉ sử dụng các lệnh NSFW trong các kênh được đánh dấu là NSFW.\n"
            "2. **Tôn Trọng Thành Viên**: Không ép buộc ai tham gia vào các tương tác NSFW nếu họ không muốn.\n"
            "3. **Hạn Chế Tuổi**: Đảm bảo rằng tất cả thành viên tham gia đều trên 18 tuổi.\n"
            "4. **Hành Vi Phù Hợp**: Tránh các hành vi quấy rối, xúc phạm hoặc không phù hợp trong các tương tác NSFW.\n"
            "5. **Tuân Thủ Quy Định Discord**: Mọi hoạt động phải tuân thủ Điều Khoản Dịch Vụ và Nguyên Tắc Cộng Đồng của Discord.\n"
            "Việc vi phạm các quy định này có thể dẫn đến việc bị cảnh cáo hoặc cấm sử dụng lệnh NSFW."
        )

        gameplay_text = (
            "🎮 **Cách Chơi Các Lệnh NSFW** 🎮\n"
            "1. Sử dụng lệnh với cú pháp: "
            f"`{ctx.clean_prefix}<lệnh> @tên_thành_viên` "
            "(riêng `3some` cần tag 2 người, `orgy` tag 2-10 người, "
            "`gangbang` tag 1-10 người).\n"
            "2. Các lệnh bao gồm: `bj` (bú cu), `rj` (liếm lồn), "
            "`hj` (sục cho), `fj` (footjob), `aj` (assjob), `tj` (thighjob), "
            "`spank` (vỗ mông), `frot` (đấu kiếm), `fuck` (chịch), "
            "`cream` (xuất trong), `3some` (chơi 3some), `orgy` (chơi orgy), "
            "`gangbang`, `ride` (cưỡi), `fingering` (mó), `facesit` (ngồi mặt).\n"
            "3. Mỗi lệnh có thời gian hồi (cooldown) là 3 giây để tránh spam.\n"
            "4. Femboy Queen có thể khoá lệnh NSFW của người chơi bất kỳ "
            "trong vòng 24 giờ.\n"
            "5. Femboy King sẽ nhận được hệ số x3 điểm khi sử dụng lệnh NSFW.\n"
        )

        embed = discord.Embed(
            title="📜 Quy Định và Hướng Dẫn Sử Dụng Lệnh NSFW",
            description="",
            color=discord.Color.red(),
        )
        embed.add_field(name="Quy Định NSFW", value=rule_text, inline=False)
        embed.add_field(name="Cách Chơi Lệnh NSFW", value=gameplay_text, inline=False)
        await ctx.send(embed=embed)

    @commands.command(name="bj")
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def blowjob(self, ctx, member: discord.Member):
        await self._handle_interaction(ctx, member, "bj", "bú cu", "👅 Bú bú", f"{ctx.author.mention} bú cu {member.mention} 💖", self.bj_picker)

    @commands.command(name="rj")
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def rimjob(self, ctx, member: discord.Member):
        await self._handle_interaction(ctx, member, "rj", "liếm lồn", "🍑 Liếm cái ik~", f"{ctx.author.mention} liếm lồn {member.mention} 👅💦", self.rj_picker)

    @commands.command(name="hj")
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def handjob(self, ctx, member: discord.Member):
        await self._handle_interaction(ctx, member, "hj", "sục cho", "🥰 Sục cho nè~", f"{ctx.author.mention} sục cho {member.mention} 💦", self.hj_picker, self_allowed=True)

    @commands.command(name="fj")
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def footjob(self, ctx, member: discord.Member):
        await self._handle_interaction(
            ctx,
            member,
            "fj",
            "footjob",
            "🦶 Footjob nè~",
            f"{ctx.author.mention} footjob cho {member.mention} 💦",
            self.fj_picker,
        )

    @commands.command(name="aj", aliases=["assjob"])
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def assjob(self, ctx, member: discord.Member):
        await self._handle_interaction(
            ctx,
            member,
            "aj",
            "assjob",
            "🍑 Assjob nè~",
            f"{ctx.author.mention} assjob cho {member.mention} 💦",
            self.aj_picker,
        )

    @commands.command(name="tj", aliases=["thighjob"])
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def thighjob(self, ctx, member: discord.Member):
        await self._handle_interaction(
            ctx,
            member,
            "tj",
            "thighjob",
            "🦵 Thighjob nè~",
            f"{ctx.author.mention} thighjob cho {member.mention} 💦",
            self.tj_picker,
        )

    @commands.command(name="spank")
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def spank(self, ctx, member: discord.Member):
        await self._handle_interaction(
            ctx,
            member,
            "spank",
            "vỗ mông",
            "🍑 Vỗ mông cái!",
            f"{ctx.author.mention} vỗ mông {member.mention} 👋💦",
            self.spank_picker,
            self_allowed=True,
        )

    @commands.command(name="frot")
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def frotting(self, ctx, member: discord.Member):
        await self._handle_interaction(ctx, member, "frot", "đấu kiếm", "🤺 Đấu kiếm nhẹ nhàng nha~", f"{ctx.author.mention} frot với {member.mention} 🌸", self.frot_picker)

    @commands.command(name="fuck")
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def fucking(self, ctx, member: discord.Member):
        await self._handle_interaction(ctx, member, "fuck", "chịch", "Lên giường thôi 👉🏻👌🏻💦", f"{ctx.author.mention} chịch {member.mention} 💦", self.fuck_picker)

    @commands.command(name="cream")
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def creampie(self, ctx, member: discord.Member):
        await self._handle_interaction(
            ctx,
            member,
            "cream",
            "xuất trong",
            "💦 Aaaahhh~! Em chịu không nổi nữa rồi...",
            f"{ctx.author.mention} ra bên trong {member.mention} 💦!",
            self.cream_picker
        )

    @commands.command(name="3some", aliases=["threesome"])
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def threesome(self, ctx, member1: discord.Member, member2: discord.Member):
        if member1 == member2:
            await ctx.send("Hai người nhận phải khác nhau.")
            return
        await self._handle_interaction_multi(
            ctx,
            [member1, member2],
            "3some",
            "chơi 3some",
            "😈 3some nào~",
            f"{ctx.author.mention} chơi 3some với {member1.mention} và {member2.mention} 💦",
            self.threesome_picker,
        )

    @commands.command(name="orgy")
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def orgy(self, ctx, *members: discord.Member):
        if len(members) < 2:
            await ctx.send("Tag từ 2 đến 10 người để chơi orgy nha.")
            return
        if len(members) > 10:
            await ctx.send("Orgy tối đa 10 người được tag thôi.")
            return

        unique_members = []
        seen_member_ids = set()
        for member in members:
            if member.id in seen_member_ids:
                await ctx.send("Mỗi người chỉ cần tag một lần thôi.")
                return
            seen_member_ids.add(member.id)
            unique_members.append(member)

        mentions = ", ".join(member.mention for member in unique_members)
        await self._handle_interaction_multi(
            ctx,
            unique_members,
            "orgy",
            "chơi orgy",
            "😈 Orgy tới bến~",
            f"{ctx.author.mention} mở thác loạn với {mentions} 💦",
            self.orgy_picker,
        )

    @commands.command(name="gangbang", aliases=["gb"])
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def gangbang(self, ctx, *members: discord.Member):
        if len(members) < 1:
            await ctx.send("Tag từ 1 đến 10 người để gangbang nha.")
            return
        if len(members) > 10:
            await ctx.send("Gangbang tối đa 10 người được tag thôi.")
            return

        unique_members = []
        seen_member_ids = set()
        for member in members:
            if member.id in seen_member_ids:
                await ctx.send("Mỗi người chỉ cần tag một lần thôi.")
                return
            seen_member_ids.add(member.id)
            unique_members.append(member)

        mentions = ", ".join(member.mention for member in unique_members)
        await self._handle_interaction_multi(
            ctx,
            unique_members,
            "gangbang",
            "gangbang",
            "😈 Gangbang tới bến~",
            f"{ctx.author.mention} gangbang {mentions} 💦",
            self.gangbang_picker,
        )

    @commands.command(name="ride")
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def ride(self, ctx, member: discord.Member):
        await self._handle_interaction(
            ctx,
            member,
            "ride",
            "cưỡi",
            "🥵 Cưỡi cái nào~",
            f"{ctx.author.mention} cưỡi {member.mention} 💦",
            self.ride_picker,
        )

    @commands.command(name="fingering", aliases=["finger"])
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def fingering(self, ctx, member: discord.Member):
        await self._handle_interaction(
            ctx,
            member,
            "fingering",
            "mó",
            "👉 Mó cái nè~",
            f"{ctx.author.mention} mó {member.mention} 💦",
            self.fingering_picker,
            self_allowed=True,
        )

    @commands.command(name="facesit", aliases=["sitface"])
    @commands.cooldown(1, 3, commands.BucketType.user)
    async def facesit(self, ctx, member: discord.Member):
        await self._handle_interaction(
            ctx,
            member,
            "facesit",
            "ngồi lên mặt",
            "🍑 Ngồi mặt nè~",
            f"{ctx.author.mention} ngồi lên mặt {member.mention} 💦",
            self.facesit_picker,
        )

    # Generic error handler for all commands
    async def _cooldown_error(self, ctx, error):
        if isinstance(error, commands.CommandOnCooldown):
            await ctx.reply(f"⏳ Lệnh đang trong thời gian hồi, vui lòng chờ {error.retry_after:.1f} giây nữa.")

    # Assign error handlers
    blowjob.error = _cooldown_error
    rimjob.error = _cooldown_error
    handjob.error = _cooldown_error
    footjob.error = _cooldown_error
    assjob.error = _cooldown_error
    thighjob.error = _cooldown_error
    spank.error = _cooldown_error
    frotting.error = _cooldown_error
    fucking.error = _cooldown_error
    creampie.error = _cooldown_error
    threesome.error = _cooldown_error
    orgy.error = _cooldown_error
    gangbang.error = _cooldown_error
    ride.error = _cooldown_error
    fingering.error = _cooldown_error
    facesit.error = _cooldown_error

    @commands.command(name="ranknsfw", aliases=["nsfwrank"])
    async def ranknsfw(
        self,
        ctx: commands.Context,
        mode_or_action: str | None = None,
        interaction_type: str | None = None,
    ):
        if not await self._nsfw_guard(ctx):
            return

        nsfw_interactions = self.NSFW_INTERACTIONS

        # text cho NGƯỜI CHỦ ĐỘNG
        action_text_given = {
            "bj": "bú cu",
            "rj": "liếm lồn",
            "hj": "sục cho member khác",
            "fj": "footjob cho member khác",
            "aj": "assjob cho member khác",
            "tj": "thighjob cho member khác",
            "spank": "vỗ mông người khác",
            "frot": "đấu kiếm",
            "fuck": "địt member khác",
            "cream": "xuất trong",
            "3some": "chơi 3some",
            "orgy": "chơi orgy",
            "gangbang": "gangbang người khác",
            "ride": "cưỡi member khác",
            "fingering": "mó người khác",
            "facesit": "ngồi lên mặt người khác",
        }

        # text cho NGƯỜI BỊ
        action_text_received = {
            "bj": "được bú cu",
            "rj": "được liếm lồn",
            "hj": "được sục cặc",
            "fj": "được footjob",
            "aj": "được assjob",
            "tj": "được thighjob",
            "spank": "bị vỗ mông",
            "frot": "được đấu kiếm",
            "fuck": "bị địt",
            "cream": "bị xuất trong",
            "3some": "tham gia 3some",
            "orgy": "tham gia orgy",
            "gangbang": "bị gangbang",
            "ride": "bị cưỡi",
            "fingering": "bị mó",
            "facesit": "bị ngồi lên mặt",
        }

        # mặc định: người CHỦ ĐỘNG
        mode = "given"

        if mode_or_action == "r":
            mode = "received"
            action = interaction_type
        else:
            action = mode_or_action

        if action not in (nsfw_interactions + [None]):
            await ctx.send(
                "Loại tương tác không hợp lệ.\nVui lòng sử dụng: `bj`, `rj`, `hj`, `fj`, `aj`, `tj`, `spank`, `frot`, `fuck`, `cream`, `3some`, `orgy`, `gangbang`, `ride`, `fingering`, `facesit`."
            )
            return

        start_of_month = discord.utils.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        end_of_month = (start_of_month + timedelta(days=32)).replace(day=1)

        if mode == "given":
            pipeline = [
                {"$match": {"created_at": {"$gte": start_of_month, "$lt": end_of_month}}},
                {"$addFields": {"coefficient": {"$ifNull": ["$coefficient", 1]}}},
                {"$group": {"_id": "$initMember", "count": {"$sum": "$coefficient"}}},
                {"$sort": {"count": -1}},
                {"$limit": 10},
            ]
        else:
            pipeline = [
                {"$match": {"created_at": {"$gte": start_of_month, "$lt": end_of_month}}},
                {"$addFields": {"coefficient": {"$ifNull": ["$coefficient", 1]}}},
                {"$addFields": {"targets": {"$ifNull": ["$targetMembers", ["$targetMember"]]}}},
                {"$unwind": "$targets"},
                {"$group": {"_id": "$targets", "count": {"$sum": "$coefficient"}}},
                {"$sort": {"count": -1}},
                {"$limit": 10},
            ]

        if action:
            pipeline.insert(0, {"$match": {"action": action}})
        else:
            pipeline.insert(0, {"$match": {"action": {"$in": nsfw_interactions}}})

        top_users = list(self.db["interactions"].aggregate(pipeline))

        lines = []
        for rank, record in enumerate(top_users, start=1):
            user_id = record["_id"]
            count = record["count"]

            user = self.bot.get_user(user_id)
            name = user.mention if user else f"ID {user_id}"

            if mode == "given":
                if action:
                    text = f"{count} lần {action_text_given[action]}."
                else:
                    text = f"{count} lần chơi người khác."
            else:
                if action:
                    text = f"{count} lần {action_text_received[action]}."
                else:
                    text = f"{count} lần bị chơi."

            lines.append(f"**{rank}**. {name} – {text}")

        description = "\n".join(lines) if lines else "Chưa có dữ liệu."

        current_month = discord.utils.utcnow().month
        current_year = discord.utils.utcnow().year

        if mode == "given":
            title = f"Top 10 con quỷ sex của server tháng {current_month}/{current_year} 😈"
            if action:
                title = f"🏆 Top 10 người {action_text_given[action]} nhiều nhất 💦"
        else:
            title = f"Top 10 noletinhduc tháng {current_month}/{current_year} 👉🏻👌🏻💦"
            if action:
                title = f"🏆 Top 10 người {action_text_received[action]} nhiều nhất 💦"

        embed = discord.Embed(title=title, description=description)
        embed.set_author(name="BXH độ răm", icon_url=ctx.author.display_avatar.url)
        embed.set_thumbnail(url=self.bot.user.display_avatar.url)
        embed.set_image(
            url="https://api-cdn.rule34.xxx//images/1500/85f729598f01b951f528e47b49078414.gif?1585014"
        )
        await ctx.send(embed=embed)

    @commands.command(name="mrank")
    @commands.has_permissions(administrator=True)
    async def monthlyranknsfw(
        self,
        ctx: commands.Context,
        month: int,
        year: int
    ):
        if not await self._nsfw_guard(ctx):
            return
        
        # the logic is similar to ranknsfw but for a specified month and year
        # that need only rank 5 user over all interactions
        # and later, make the embed and send 
        start_of_month = datetime(year, month, 1)
        end_of_month = (start_of_month + timedelta(days=32)).replace(day=1)
        # Pipeline for "given" (người chủ động)
        pipeline_given = [
            {"$match": {"created_at": {"$gte": start_of_month, "$lt": end_of_month}}},
            {"$match": {"action": {"$in": self.NSFW_INTERACTIONS}}},
            {"$addFields": {"coefficient": {"$ifNull": ["$coefficient", 1]}}},
            {"$group": {"_id": "$initMember", "count": {"$sum": "$coefficient"}}},
            {"$sort": {"count": -1}},
            {"$limit": 5},
        ]

        # Pipeline for "received" (người bị động)
        pipeline_received = [
            {"$match": {"created_at": {"$gte": start_of_month, "$lt": end_of_month}}},
            {"$match": {"action": {"$in": self.NSFW_INTERACTIONS}}},
            {"$addFields": {"coefficient": {"$ifNull": ["$coefficient", 1]}}},
            {"$addFields": {"targets": {"$ifNull": ["$targetMembers", ["$targetMember"]]}}},
            {"$unwind": "$targets"},
            {"$group": {"_id": "$targets", "count": {"$sum": "$coefficient"}}},
            {"$sort": {"count": -1}},
            {"$limit": 5},
        ]

        top_given = list(self.db["interactions"].aggregate(pipeline_given))
        top_received = list(self.db["interactions"].aggregate(pipeline_received))

        # Build "given" table
        lines_given = []
        for rank, record in enumerate(top_given, start=1):
            user_id = record["_id"]
            count = record["count"]
            user = self.bot.get_user(user_id)
            name = user.mention if user else f"ID {user_id}"
            lines_given.append(f"**{rank}**. {name} – {count} lần")
        
        # Fill remaining positions with blanks
        for rank in range(len(top_given) + 1, 6):
            lines_given.append(f"**{rank}**. —")
        
        # Build "received" table
        lines_received = []
        for rank, record in enumerate(top_received, start=1):
            user_id = record["_id"]
            count = record["count"]
            user = self.bot.get_user(user_id)
            name = user.mention if user else f"ID {user_id}"
            lines_received.append(f"**{rank}**. {name} – {count} lần")

        # Fill remaining positions with blanks
        for rank in range(len(top_received) + 1, 6):
            lines_received.append(f"**{rank}**. —")

        title = f"📊 Tổng kết tháng {month}/{year}"
        embed = discord.Embed(title=title, color=discord.Color.purple())
        embed.set_author(name="BXH độ răm", icon_url=ctx.author.display_avatar.url)
        embed.set_thumbnail(url=self.bot.user.display_avatar.url)
        
        embed.add_field(
            name="😈 Top 5 Con Quỷ Sex",
            value="\n".join(lines_given) if lines_given else "Chưa có dữ liệu.",
            inline=False
        )
        
        embed.add_field(
            name="💦 Top 5 Nô Lệ Tình Dục",
            value="\n".join(lines_received) if lines_received else "Chưa có dữ liệu.",
            inline=False
        )

        next_month = month % 12 + 1
        next_year = year if month < 12 else year + 1

        # Add congratulations for new King and Queen
        if top_given:
            king_user_id = top_given[0]["_id"]
            king_user = self.bot.get_user(king_user_id)
            embed.add_field(
            name="👑 Femboy King mới",
            value=f"Chúc mừng {king_user.mention if king_user else f'<@{king_user_id}>'} đã trở thành **Femboy King** tháng {next_month}/{next_year}! 🎉",
            inline=False
            )

        if top_received:
            queen_user_id = top_received[0]["_id"]
            queen_user = self.bot.get_user(queen_user_id)
            embed.add_field(
            name="👑 Femboy Queen mới",
            value=f"Chúc mừng {queen_user.mention if queen_user else f'<@{queen_user_id}>'} đã trở thành **Femboy Queen** tháng {next_month}/{next_year}! 🎉",
            inline=False
            )

        embed.set_image(
            url="https://api-cdn.rule34.xxx//images/1500/85f729598f01b951f528e47b49078414.gif?1585014"
        )
        await ctx.send(embed=embed)

    @monthlyranknsfw.error
    async def monthlyranknsfw_error(self, ctx: commands.Context, error):
        if isinstance(error, commands.MissingPermissions):
            await ctx.reply("❌ Bạn cần quyền Quản trị viên để sử dụng lệnh này.")
        elif isinstance(error, commands.BadArgument):
            await ctx.reply("❌ Vui lòng cung cấp tháng và năm hợp lệ. Ví dụ: `!tf mrank 3 2024`")





async def setup(bot: commands.Bot):
    await bot.add_cog(NSFWInteractionCog(bot))
