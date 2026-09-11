from __future__ import annotations

import random
from collections import deque
from dataclasses import dataclass
from typing import Sequence

import discord  # pyright: ignore[reportMissingImports]
from discord.ext import commands  # pyright: ignore[reportMissingImports]

from assets.gifs import (
    BITE_GIFS,
    BLUSH_GIFS,
    BONK_GIFS,
    BOOP_GIFS,
    CUDDLE_GIFS,
    FEED_GIFS,
    HANDHOLD_GIFS,
    HIGHFIVE_GIFS,
    HUG_GIFS,
    KIDNAP_GIFS,
    KISS_GIFS,
    LICK_GIFS,
    PAT_GIFS,
    PINCH_GIFS,
    POKE_GIFS,
    PUNCH_GIFS,
    SLAP_GIFS,
    SMACK_GIFS,
    SNIFF_GIFS,
    SNUGGLE_GIFS,
    STARE_GIFS,
    TICKLE_GIFS,
    WAVE_GIFS,
    WINK_GIFS,
)

HIT_GIFS = SLAP_GIFS + PUNCH_GIFS

# Shared cooldown for all SFW interaction commands (rate uses / per seconds).
SFW_INTERACTION_COOLDOWN_RATE = 1
SFW_INTERACTION_COOLDOWN_PER = 3.0


@dataclass(frozen=True)
class InteractionSpec:
    """One SFW member interaction (command + rank copy + GIF pool)."""

    name: str
    title: str
    verb: str
    suffix: str
    gifs: Sequence[str]
    given_text: str
    received_text: str
    aliases: tuple[str, ...] = ()
    # Soft / meme self-targets only; social-with-other stays blocked.
    allow_self: bool = False

    @property
    def help_text(self) -> str:
        return f"{self.verb.capitalize()} member khác."


# Single source of truth: adding an action only requires a new entry here.
SFW_ACTION_SPECS: tuple[InteractionSpec, ...] = (
    InteractionSpec(
        name="kiss",
        title="💋 Moah moahhh~",
        verb="hôn",
        suffix="💖",
        gifs=KISS_GIFS,
        given_text="hôn người khác",
        received_text="được hôn",
    ),
    InteractionSpec(
        name="hug",
        title="🤗 Ỏoooo, ôm cái nào!",
        verb="ôm",
        suffix="🫂",
        gifs=HUG_GIFS,
        given_text="ôm người khác",
        received_text="được ôm",
    ),
    InteractionSpec(
        name="pat",
        title="😉 Xoa đầu cái nha~",
        verb="xoa đầu",
        suffix="🌸",
        gifs=PAT_GIFS,
        given_text="xoa đầu người khác",
        received_text="được xoa đầu",
        allow_self=True,
    ),
    InteractionSpec(
        name="slap",
        title="🤬 Ăn tát đi!",
        verb="tát",
        suffix="🤚🏻",
        gifs=SLAP_GIFS,
        given_text="tát người khác",
        received_text="bị tát",
        allow_self=True,  # facepalm energy
    ),
    InteractionSpec(
        name="punch",
        title="👊 Một đấm là nằm!",
        verb="đấm",
        suffix="👊🏻",
        gifs=PUNCH_GIFS,
        given_text="đấm người khác",
        received_text="bị đấm",
        allow_self=True,
    ),
    InteractionSpec(
        name="hit",
        title="💥 Bốp bốp!",
        verb="đánh",
        suffix="🔨",
        gifs=HIT_GIFS,
        given_text="đánh người khác",
        received_text="bị đánh",
        allow_self=True,
    ),
    InteractionSpec(
        name="poke",
        title="👉 Chọc chọc!",
        verb="chọc",
        suffix="👉🏻",
        gifs=POKE_GIFS,
        given_text="chọc người khác",
        received_text="bị chọc",
        allow_self=True,
    ),
    InteractionSpec(
        name="cuddle",
        title="🥰 Cuddle nè~",
        verb="cuddle",
        suffix="💕",
        gifs=CUDDLE_GIFS,
        given_text="cuddle người khác",
        received_text="được cuddle",
    ),
    InteractionSpec(
        name="snuggle",
        title="🐻 Snuggle chút nha~",
        verb="snuggle",
        suffix="💗",
        gifs=SNUGGLE_GIFS,
        given_text="snuggle người khác",
        received_text="được snuggle",
    ),
    InteractionSpec(
        name="boop",
        title="👆 Boop!",
        verb="boop mũi",
        suffix="🐽",
        gifs=BOOP_GIFS,
        given_text="boop mũi người khác",
        received_text="bị boop mũi",
    ),
    InteractionSpec(
        name="handhold",
        title="🤝 Nắm tay nào~",
        verb="nắm tay",
        suffix="💞",
        gifs=HANDHOLD_GIFS,
        given_text="nắm tay người khác",
        received_text="được nắm tay",
        aliases=("holdhand",),
    ),
    InteractionSpec(
        name="bonk",
        title="🔨 Bonk!",
        verb="bonk",
        suffix="💢",
        gifs=BONK_GIFS,
        given_text="bonk người khác",
        received_text="bị bonk",
        allow_self=True,
    ),
    InteractionSpec(
        name="bite",
        title="🦷 Nham nham~",
        verb="cắn",
        suffix="🤭",
        gifs=BITE_GIFS,
        given_text="cắn người khác",
        received_text="bị cắn",
        aliases=("nom",),
    ),
    InteractionSpec(
        name="stare",
        title="👀 ...",
        verb="nhìn chằm chằm",
        suffix="😳",
        gifs=STARE_GIFS,
        given_text="nhìn chằm chằm người khác",
        received_text="bị nhìn chằm chằm",
    ),
    InteractionSpec(
        name="lick",
        title="👅 Liếm cái nè~",
        verb="liếm",
        suffix="😛",
        gifs=LICK_GIFS,
        given_text="liếm người khác",
        received_text="bị liếm",
    ),
    InteractionSpec(
        name="smack",
        title="💢 Đấm yêu nè~",
        verb="đấm yêu",
        suffix="💕",
        gifs=SMACK_GIFS,
        given_text="đấm yêu người khác",
        received_text="bị đấm yêu",
        allow_self=True,
    ),
    InteractionSpec(
        name="sniff",
        title="👃 Hít hà~",
        verb="ngửi",
        suffix="✨",
        gifs=SNIFF_GIFS,
        given_text="ngửi người khác",
        received_text="bị ngửi",
    ),
    InteractionSpec(
        name="kidnap",
        title="🏃 Bắt cóc cái nào!",
        verb="bắt cóc",
        suffix="💨",
        gifs=KIDNAP_GIFS,
        given_text="bắt cóc người khác",
        received_text="bị bắt cóc",
    ),
    InteractionSpec(
        name="tickle",
        title="😆 Cù lét cái!",
        verb="cù lét",
        suffix="🤭",
        gifs=TICKLE_GIFS,
        given_text="cù lét người khác",
        received_text="bị cù lét",
        allow_self=True,
    ),
    InteractionSpec(
        name="pinch",
        title="🤏 Véo má nè~",
        verb="véo má",
        suffix="✨",
        gifs=PINCH_GIFS,
        given_text="véo má người khác",
        received_text="bị véo má",
    ),
    InteractionSpec(
        name="wave",
        title="👋 Xin chào~",
        verb="vẫy tay với",
        suffix="🌸",
        gifs=WAVE_GIFS,
        given_text="vẫy tay với người khác",
        received_text="được vẫy tay",
    ),
    InteractionSpec(
        name="blush",
        title="😳 Ửng hồng~",
        verb="làm đỏ mặt",
        suffix="💗",
        gifs=BLUSH_GIFS,
        given_text="làm đỏ mặt người khác",
        received_text="bị làm đỏ mặt",
        allow_self=True,
    ),
    InteractionSpec(
        name="highfive",
        title="🙌 High five!",
        verb="high five với",
        suffix="✨",
        gifs=HIGHFIVE_GIFS,
        given_text="high five với người khác",
        received_text="được high five",
        aliases=("brofist",),
    ),
    InteractionSpec(
        name="feed",
        title="🍽️ Ăn đi nè~",
        verb="đút ăn",
        suffix="😋",
        gifs=FEED_GIFS,
        given_text="đút ăn người khác",
        received_text="được đút ăn",
    ),
    InteractionSpec(
        name="wink",
        title="😉 Nháy mắt nè~",
        verb="nháy mắt với",
        suffix="💞",
        gifs=WINK_GIFS,
        given_text="nháy mắt với người khác",
        received_text="được nháy mắt",
    ),
)

SFW_INTERACTIONS: list[str] = [spec.name for spec in SFW_ACTION_SPECS]
ACTION_TEXT_GIVEN: dict[str, str] = {
    spec.name: spec.given_text for spec in SFW_ACTION_SPECS
}
ACTION_TEXT_RECEIVED: dict[str, str] = {
    spec.name: spec.received_text for spec in SFW_ACTION_SPECS
}
_SFW_ACTION_BY_NAME: dict[str, InteractionSpec] = {
    spec.name: spec for spec in SFW_ACTION_SPECS
}


class GifPicker:
    """Avoid repeating the same GIF within a short recent window."""

    def __init__(self, gifs: Sequence[str], history_size: int = 5) -> None:
        self.gifs = list(gifs)
        self.recent: deque[str] = deque(maxlen=history_size)

    def pick(self) -> str:
        if not self.gifs:
            raise ValueError("GifPicker requires a non-empty GIF list.")
        candidates = [gif for gif in self.gifs if gif not in self.recent]
        chosen = random.choice(candidates or self.gifs)
        self.recent.append(chosen)
        return chosen


class UserInteractionCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.db = bot.db
        self._pickers = {
            spec.name: GifPicker(spec.gifs, history_size=5)
            for spec in SFW_ACTION_SPECS
        }
        self._register_interaction_commands()

    def _register_interaction_commands(self) -> None:
        """Register one command per InteractionSpec (no per-action method bodies)."""
        for spec in SFW_ACTION_SPECS:
            callback = commands.cooldown(
                SFW_INTERACTION_COOLDOWN_RATE,
                SFW_INTERACTION_COOLDOWN_PER,
                commands.BucketType.user,
            )(self._make_interaction_callback(spec))
            command = commands.Command(
                callback,
                name=spec.name,
                aliases=list(spec.aliases),
                help=spec.help_text,
            )
            command.cog = self
            self.__cog_commands__ = self.__cog_commands__ + (command,)

    def _make_interaction_callback(self, spec: InteractionSpec):
        # First arg is the cog instance injected by discord.py when command.cog is set.
        async def callback(
            cog: UserInteractionCog,
            ctx: commands.Context,
            member: discord.Member,
        ) -> None:
            await cog._do_interaction(ctx, member, spec)

        callback.__name__ = f"interaction_{spec.name}"
        callback.__qualname__ = f"UserInteractionCog.interaction_{spec.name}"
        return callback

    def record_action(
        self, action: str, ctx: commands.Context, member: discord.Member
    ) -> None:
        self.db["interactions"].insert_one(
            {
                "message_id": ctx.message.id,
                "initMember": ctx.author.id,
                "targetMember": member.id,
                "action": action,
                "created_at": discord.utils.utcnow(),
            }
        )

    async def _send_embed(
        self,
        ctx: commands.Context,
        *,
        title: str,
        description: str,
        gif_url: str | None = None,
    ) -> None:
        embed = discord.Embed(title=title, description=description)
        if gif_url:
            embed.set_image(url=gif_url)
        await ctx.send(embed=embed)

    def _target_guard(
        self, ctx: commands.Context, member: discord.Member, spec: InteractionSpec
    ) -> str | None:
        """Return a user-facing error message, or None if the target is allowed."""
        if member.bot:
            if member.id == getattr(self.bot.user, "id", None):
                return "Đừng tương tác với bot nha, mình không phải thịt đâu 🤖"
            return "Không thể tương tác với bot khác được đâu 🤖"

        if member.id == ctx.author.id and not spec.allow_self:
            return (
                f"Bạn không thể tự **{spec.verb}** chính mình được đâu 😳\n"
                f"Hãy tag một member khác nhé."
            )
        return None

    async def _do_interaction(
        self,
        ctx: commands.Context,
        member: discord.Member,
        spec: InteractionSpec,
    ) -> None:
        deny_reason = self._target_guard(ctx, member, spec)
        if deny_reason is not None:
            await ctx.reply(deny_reason, mention_author=False)
            return

        picker = self._pickers[spec.name]
        self.record_action(spec.name, ctx, member)

        if member.id == ctx.author.id:
            description = (
                f"{ctx.author.mention} tự {spec.verb} mình {spec.suffix}"
            )
        else:
            description = (
                f"{ctx.author.mention} {spec.verb} {member.mention} {spec.suffix}"
            )

        await self._send_embed(
            ctx,
            title=spec.title,
            description=description,
            gif_url=picker.pick(),
        )

        if (
            member.id != ctx.author.id
            and ctx.guild is not None
        ):
            marriage_cog = self.bot.get_cog("MarriageCog")
            if marriage_cog is not None and hasattr(
                marriage_cog, "try_grant_couple_xp"
            ):
                await marriage_cog.try_grant_couple_xp(
                    ctx.guild.id,
                    ctx.author.id,
                    member.id,
                    action=spec.name,
                    channel=ctx.channel,
                )

    async def cog_command_error(
        self, ctx: commands.Context, error: commands.CommandError
    ) -> None:
        if isinstance(error, commands.CommandOnCooldown):
            await ctx.reply(
                f"Chậm thôi~ đợi **{error.retry_after:.1f}s** rồi tương tác tiếp nhé ⏳",
                mention_author=False,
            )
            return
        if isinstance(error, commands.MissingRequiredArgument):
            if ctx.command and ctx.command.name in _SFW_ACTION_BY_NAME:
                await ctx.reply(
                    f"Cú pháp: `{ctx.prefix}{ctx.command.name} @user`",
                    mention_author=False,
                )
                return
        if isinstance(error, commands.MemberNotFound):
            await ctx.reply(
                "Không tìm thấy member đó. Hãy tag đúng người nhé.",
                mention_author=False,
            )
            return
        # Let the global handler deal with unexpected errors.
        raise error

    def _global_avatar_url(self, member: discord.Member | discord.User) -> str:
        """Discord global avatar (account-level), not the server override."""
        avatar = member.avatar or member.default_avatar
        return avatar.url

    def _server_avatar_url(self, member: discord.Member) -> str | None:
        """Guild-specific avatar, or None if the member has none in this server."""
        if member.guild_avatar is None:
            return None
        return member.guild_avatar.url

    async def _send_avatar_embed(
        self,
        ctx: commands.Context,
        member: discord.Member,
        *,
        title: str,
        image_url: str,
        kind_label: str,
    ) -> None:
        embed = discord.Embed(
            title=title,
            description=(
                f"{kind_label} của {member.mention}\n"
                f"[Mở ảnh gốc]({image_url})"
            ),
            color=discord.Color.blurple(),
        )
        embed.set_image(url=image_url)
        embed.set_footer(text=f"ID: {member.id}")
        await ctx.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())

    @commands.command(
        name="avatar",
        aliases=["av", "global_avatar", "globalav"],
        help="Xem avatar global (tài khoản Discord) của member.",
    )
    async def avatar(
        self, ctx: commands.Context, member: discord.Member | None = None
    ) -> None:
        member = member or ctx.author
        await self._send_avatar_embed(
            ctx,
            member,
            title=f"🌐 Avatar global — {member.display_name}",
            image_url=self._global_avatar_url(member),
            kind_label="Avatar global",
        )

    @commands.command(
        name="server_avatar",
        aliases=["sav", "guild_avatar", "serverav"],
        help="Xem avatar server của member (fallback sang global nếu chưa đặt).",
    )
    @commands.guild_only()
    async def server_avatar(
        self, ctx: commands.Context, member: discord.Member | None = None
    ) -> None:
        member = member or ctx.author
        server_url = self._server_avatar_url(member)
        if server_url is not None:
            await self._send_avatar_embed(
                ctx,
                member,
                title=f"🏠 Avatar server — {member.display_name}",
                image_url=server_url,
                kind_label="Avatar server",
            )
            return

        # No server avatar (common for non-Nitro / unset): show global instead.
        await self._send_avatar_embed(
            ctx,
            member,
            title=f"🌐 Avatar global — {member.display_name}",
            image_url=self._global_avatar_url(member),
            kind_label="Avatar global (không có avatar server)",
        )

    @commands.command(name="rank", aliases=["ranking"])
    async def rank(
        self,
        ctx: commands.Context,
        mode_or_action: str | None = None,
        interaction_type: str | None = None,
    ) -> None:
        mode = "given"
        if mode_or_action == "r":
            mode = "received"
            action = interaction_type
        else:
            action = mode_or_action

        if action is not None and action not in _SFW_ACTION_BY_NAME:
            actions_hint = ", ".join(f"`{name}`" for name in SFW_INTERACTIONS)
            await ctx.send(f"Loại tương tác không hợp lệ.\nDùng: {actions_hint}.")
            return

        user_field = "$initMember" if mode == "given" else "$targetMember"
        pipeline: list[dict] = [
            {"$group": {"_id": user_field, "count": {"$sum": 1}}},
            {"$sort": {"count": -1}},
            {"$limit": 10},
        ]
        if action:
            pipeline.insert(0, {"$match": {"action": action}})
        else:
            pipeline.insert(0, {"$match": {"action": {"$in": SFW_INTERACTIONS}}})

        top_users = list(self.db["interactions"].aggregate(pipeline))

        lines: list[str] = []
        for rank_index, record in enumerate(top_users, start=1):
            user_id = record["_id"]
            count = record["count"]
            user = self.bot.get_user(user_id)
            name = user.mention if user else f"ID {user_id}"

            if mode == "given":
                text = (
                    f"{count} lần {ACTION_TEXT_GIVEN[action]}."
                    if action
                    else f"{count} lần tương tác."
                )
            else:
                text = (
                    f"{count} lần {ACTION_TEXT_RECEIVED[action]}."
                    if action
                    else f"{count} lần bị tương tác."
                )
            lines.append(f"**{rank_index}. {name}** – {text}")

        description = "\n".join(lines) if lines else "Chưa có dữ liệu."

        if mode == "given":
            title = (
                f"🏆 Top 10 người {ACTION_TEXT_GIVEN[action]} nhiều nhất"
                if action
                else "🏆 Top 10 người tương tác nhiều nhất"
            )
        else:
            title = (
                f"🏆 Top 10 người {ACTION_TEXT_RECEIVED[action]} nhiều nhất"
                if action
                else "🏆 Top 10 người bị tương tác nhiều nhất"
            )

        embed = discord.Embed(title=title, description=description)
        embed.set_author(name="BXH tương tác", icon_url=ctx.author.display_avatar.url)
        if self.bot.user is not None:
            embed.set_thumbnail(url=self.bot.user.display_avatar.url)
        embed.set_image(
            url=(
                "https://cdn.discordapp.com/attachments/"
                "1382770560743903246/1456661155236806832/Untitled_design_37.png"
            )
        )
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(UserInteractionCog(bot))
