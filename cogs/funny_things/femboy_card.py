import discord
from discord.ext import commands
import datetime

class FemboyCardCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.FEMBOY_ROLE = bot.FEMBOY_ROLE

    @commands.command(name="femboycard", help="Tạo thẻ femboy cho một thành viên được đề cập.")
    async def femboy_card(self, ctx):
        member = ctx.author
        
        # find the user highest role that matches femboy roles
        femboy_roles = [role for role in member.roles if role.name in self.FEMBOY_ROLE]

        if not femboy_roles:
            await ctx.send(f"{member.mention}, bạn không có vai trò femboy để tạo thẻ femboy!")
            return
        highest_femboy_role = max(femboy_roles, key=lambda r: r.position)

        femboy_role = discord.utils.get(ctx.guild.roles, name=highest_femboy_role.name)
        if femboy_role is None:
            await ctx.send(f"{member.mention}, vai trò femboy của bạn không hợp lệ!")
            return

        embed = discord.Embed(
            title="🌸 Femboy Card 🌸",
            description=f"**Tên:** {member.mention}\n **Cấp hiệu:** {femboy_role.name}\n **ID thành viên:** {member.id}",
            color=femboy_role.color,
        )

        embed.set_author(name=member.name, icon_url=member.display_avatar.url)
        embed.set_thumbnail(url=member.display_avatar.url)

        # Add more fields or customization as needed
        # embed.add_field(name="Tính cách", value="Dễ thương và quyến rũ!", inline=False)
        # embed.add_field(name="Sở thích", value="Ngắm femboy và chơi game!", inline=False)

        # embed.set_image(url=femboy_role.icon.url if femboy_role.icon else member.display_avatar.url) 

        embed.add_field(name="", value=f"**Được công nhận là Femboy**", inline=False)
        embed.add_field(name="", value="Dễ thương - Tự tin - Tỏa sáng ✨", inline=False)

        embed.add_field(name="", value=f"**Ngày tạo thẻ: ** {datetime.datetime.utcnow().strftime('%Y-%m-%d')}", inline=False)
        embed.add_field(name="", value="**Hiệu lực đến: ** Mãi mãi dễ thương", inline=True)

        embed.set_footer(text="Ký bởi: Cộng đồng TFVN.")
        embed.timestamp = datetime.datetime.utcnow()

        await ctx.send(embed=embed)

async def setup(bot):
    await bot.add_cog(FemboyCardCog(bot))