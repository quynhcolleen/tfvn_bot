from discord.ext import commands  # pyright: ignore[reportMissingImports]
import discord  # pyright: ignore[reportMissingImports]


class HelpCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="help")
    async def custom_help(self, ctx):
        embed = discord.Embed(
            title="📖 Hướng dẫn sử dụng TFVN bot",
            description="Danh sách lệnh hiện có:",
            color=0xFFC0CB,
        )
        embed.add_field(
            name="General", value="`!tf hello` – Chào con bot.", inline=False
        )
        embed.add_field(
            name="NSFW",
            value=(
                "`!tf nsfw` – Hướng dẫn lệnh nsfw.\n"
                "`!tf verify` – Hướng dẫn chứng thực độ tuổi.\n"
                "`!tf nsfw` – Hướng dẫn lệnh nsfw.\n"
            ),
            inline=False,
        )

        embed.set_footer(text="Prefix: !tf")

        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(HelpCog(bot))
