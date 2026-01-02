import asyncio
from discord.ext import commands  # pyright: ignore[reportMissingImports]
import discord  # pyright: ignore[reportMissingImports]


class HelpCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="help")
    async def custom_help(self, ctx, *args):
        if args:
            return  # Ignore arguments for now
        embed = discord.Embed(
            title="📖 Hướng dẫn sử dụng TFVN bot",
            description="Danh sách lệnh hiện có:",
            color=0xFFC0CB,
        )
        embed.add_field(
            name="Tương tác chung:",
            value=("`!tf hello` – Chào con bot.\n"
                   "`!tf cat` – Mèo.\n"
                   "`!tf dog` – Chó.\n"
            ),
            inline=False,
        )
        embed.add_field(
            name="Tương tác với member khác:",
            value=(
                "`!tf kiss @user` – Hôn member khác.\n"
                "`!tf hug @user` – Ôm member khác.\n"
                "`!tf pat @user` – Xoa đầu member khác.\n"
                "`!tf slap @user` – Tát member khác.\n"
                "`!tf punch @user` – Đấm member khác.\n"
                "`!tf hit @user` – Đánh member khác.\n"
                "`!tf poke @user` – Chọc member khác.\n"
                "`!tf avatar @user` – Xem avatar của member khác.\n"
            ),
            inline=False,
        )
        embed.add_field(
            name="BXH tương tác:",
            value=(
                "`!tf rank` – Xem bảng xếp hạng tương tác chung.\n"
                "`!tf rank r` – Xem bảng xếp hạng người bị/được tương tác chung.\n"
                "`!tf rank <action>` – Xem bảng xếp hạng member theo tương tác riêng.\n"
                "`!tf rank r <action>` – Xem bảng xếp hạng member bị/được tương tác riêng.\n"
            ),
            inline=False,
        )
        embed.add_field(
            name="NSFW",
            value=(
                "`!tf nsfw` – Hướng dẫn lệnh nsfw.\n"
                "`!tf verify` – Hướng dẫn chứng thực độ tuổi.\n"
            ),
            inline=False,
        )

        embed.set_footer(text="Prefix: !tf")

        await ctx.send(embed=embed)

    @commands.command(name="nsfw")
    async def nsfw_help(self, ctx, *args):
        if args:
            return  # Ignore arguments for now
        embed = discord.Embed(
            title="Lệnh NSFW",
            color=0xFFC0CB,
        )
        embed.add_field(
            name="Tìm kiếm nội dung NSFW:",
            value=(
                "`!tf r34 <tags>` – Tìm kiếm ảnh/video trên Rule34.\n"
                "`!tf gbr <tags>` – Tìm kiếm ảnh/video trên Gelbooru.\n"
            ),
            inline=False,
        )
        embed.add_field(
            name="Chịch member khác:",
            value=(
                "`!tf bj @user` - Blowjob cho member khác.\n"
                "`!tf rj @user` - Rimjob (liếm lồn) cho member khác.\n"
                "`!tf hj @user` - Handjob cho member khác.\n"
                # "`!tf fj @user - Footjob cho member khác.\n"
                # "`!tf finger @user - Móc member khác.\n"
                "`!tf frot @user` - Frotting với member khác.\n"
                "`!tf fuck @user` - Làm tình với member khác.\n"
                "`!tf cream @user` - Creampie member khác.\n"
            ),
            inline=False,
        )
        embed.add_field(
            name="Bảng xếp hạng - vinh danh kẻ dâm:",
            value=(
                "`!tf ranknsfw` - Xem bảng xếp hạng tổng thể quỷ sếch.\n"
                "`!tf ranknsfw r` - Xem bảng xếp hạng tổng người bị sếch.\n"
                "`!tf ranknsfw <action>` - Xem bảng xếp hạng quỷ sếch theo tương tác.\n"
                "`!tf ranknsfw r <action>` - Xem bảng xếp hạng người bị sếch theo tương tác.\n"
            ),
            inline=False,
        )
        if not ctx.channel.is_nsfw():
            await ctx.message.add_reaction("⚠️")
            warn_msg = await ctx.reply("🔞 Dùng lệnh này trong channel NSFW nhé.")
            await asyncio.sleep(5)
            await warn_msg.delete()
            await ctx.message.delete()
            return
        else:
            await ctx.reply(embed=embed)


async def setup(bot):
    await bot.add_cog(HelpCog(bot))
