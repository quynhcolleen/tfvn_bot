import discord
from discord.ext import commands


class MonitorAfkMessageCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.db = bot.db
        self.on_afk_dynamic_status = set()
        self._load_dynamic_afk_users()  # Load active AFK users on startup

    def _load_dynamic_afk_users(self):
        """Load dynamic AFK user IDs into the set to avoid DB queries."""
        afk_users = self.db["afk_reminders"].find(
            {
                "$or": [
                    {"end_at": None}
                ]
            },
            {"user_id": 1}  # Only fetch user_id for efficiency
        )
        self.on_afk_dynamic_status = {reminder["user_id"] for reminder in afk_users}

    def refresh_afk_status(self):
        """Call this method to refresh the AFK user set (e.g., after setting/unsetting AFK)."""
        self._load_dynamic_afk_users()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return

        if message.author.id in self.on_afk_dynamic_status:
            # User is AFK dynamically, clear their AFK status
            self.db["afk_reminders"].delete_one({"user_id": message.author.id})
            self.on_afk_dynamic_status.remove(message.author.id)
            embed = discord.Embed(
                description=f"Chào mừng trở lại {message.author.mention}! Lời nhắc AFK của bạn đã được xóa.",
                color=discord.Color.green(),
            )

            # add time of being AFK to the embed
            afk_record = self.db["afk_reminders"].find_one(
                {"user_id": message.author.id},
                sort=[("start_at", -1)]
            )

            if afk_record and afk_record.get("start_at"):
                start_at = afk_record["start_at"]
                end_at = discord.utils.utcnow()
                delta = end_at - start_at
                hours, remainder = divmod(int(delta.total_seconds()), 3600)
                minutes, seconds = divmod(remainder, 60)
                time_afk = f"{hours} giờ, {minutes} phút, {seconds} giây"
                embed.add_field(name="⏱ Bạn đã offline trong: ", value=time_afk, inline=False)


            # add list of mention while you offline to the embed
            afk_pings = self.db["afk_pings"].find({"user_id": message.author.id, "is_read": False})

            ping_list = list(afk_pings)
            if ping_list:
                mention_count = len(ping_list)
                embed.add_field(
                    name="📌 Bạn đã bị ping:",
                    value=f"Bạn đã bị ping {mention_count} lần trong khi bạn AFK.",
                    inline=False,
                )

                cnt = 0
                for ping in ping_list:
                    if cnt >= 10:
                        embed.add_field(
                            name="...",
                            value="Và nhiều hơn nữa...",
                            inline=False,
                        )
                        break
                    
                    cnt += 1
                    pinged_by = message.guild.get_member(ping["pinged_by"])
                    channel = message.guild.get_channel(ping["channel_id"])
                    timestamp = ping["timestamp"].strftime("%Y-%m-%d %H:%M:%S UTC")
                    embed.add_field(
                        name=f"- Bởi {pinged_by.display_name if pinged_by else 'Unknown User'}",
                        value=f"Vào {timestamp} trong kênh {channel.mention if channel else 'Unknown Channel'} (nhảy đến tin nhắn: {ping.get('jump_url') or 'N/A'})",
                        inline=False,
                    )

                # mark all as read
                self.db["afk_pings"].update_many(
                    {"user_id": message.author.id, "is_read": False},
                    {"$set": {"is_read": True}}
                )
            
            await message.reply(embed=embed)

        if not message.mentions:
            return
        
        mentioned_user_ids = [user.id for user in message.mentions]
        current_time = discord.utils.utcnow()

        afk_reminders = self.db["afk_reminders"].find(
            {
                "user_id": {"$in": mentioned_user_ids},
                "$or": [
                    {"end_at": {"$gt": current_time}},
                    {"end_at": None}
                ]
            }
        )

        for reminder in afk_reminders:
            user = message.guild.get_member(reminder["user_id"])
            if not user:
                continue

            remind_message = reminder["message"]

            # record to afk_pings collection
            self.db["afk_pings"].insert_one(
                {
                    "user_id": reminder["user_id"],
                    "pinged_by": message.author.id,
                    "jump_url": message.jump_url,
                    "is_read": False,
                    "timestamp": current_time,
                    "channel_id": message.channel.id,
                    "message_id": message.id,
                }
            )

            await message.channel.send(
                f"{message.author.mention}, "
                f"{user.display_name} đang AFK:\n> {remind_message}"
            )


async def setup(bot: commands.Bot):
    await bot.add_cog(MonitorAfkMessageCog(bot))
