from discord.ext import commands  # pyright: ignore[reportMissingImports]   
import discord  # pyright: ignore[reportMissingImports]
from PIL import Image, ImageDraw, ImageFont  # pyright: ignore[reportMissingImports]
import aiohttp  # pyright: ignore[reportMissingImports]
import io

RULE_CHANNEL = 890635313590992916
ROLE_CHANNEL = 889515119829200926
INTRO_CHANNEL = 889523103909167114


class MemberPresenceCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        guild = member.guild
        channel = discord.utils.get(guild.text_channels, name="chung")
        if not channel:
            return

        avatar_url = member.display_avatar.replace(size=256).url
        async with aiohttp.ClientSession() as session:
            async with session.get(avatar_url) as resp:
                avatar_bytes = await resp.read()

        avatar = Image.open(io.BytesIO(avatar_bytes)).convert("RGBA")

        img = Image.new("RGBA", (800, 400), (15, 15, 15, 255))
        draw = ImageDraw.Draw(img)

        avatar = avatar.resize((180, 180))
        mask = Image.new("L", avatar.size, 0)
        ImageDraw.Draw(mask).ellipse((0, 0, 180, 180), fill=255)
        avatar.putalpha(mask)

        img.paste(avatar, (310, 60), avatar)

        font_big = ImageFont.load_default()
        font_small = ImageFont.load_default()

        draw.text(
            (400, 260),
            f"{member.name} just joined",
            fill="white",
            anchor="mm",
            font=font_big,
        )
        draw.text(
            (400, 300),
            f"Member #{guild.member_count}",
            fill=(180, 180, 180),
            anchor="mm",
            font=font_small,
        )

        # --- xuất ảnh ---
        buffer = io.BytesIO()
        img.save(buffer, format="PNG")
        buffer.seek(0)

        await channel.send(file=discord.File(buffer, filename="welcome.png"))


async def setup(bot):
    await bot.add_cog(MemberPresenceCog(bot))
