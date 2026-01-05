import discord
from discord.ext import commands, tasks

class VariableSetting(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.variable = None  # Initialize the variable to None
        self.db = bot.db  # Assuming bot has a db attribute for database operations

        self.collection = self.db['global_variables']  
        self.bot.global_vars = self.load_variables()  # Load variables into bot's global_vars dict

    def save_variable(self, name: str, value: str):
        """Saves or updates a variable in the database."""
        self.collection.update_one(
            {'name': name},
            {'$set': {'name': name, 'value': value}},
            upsert=True
        )
        # Update the in-memory dict
        self.bot.global_vars[name] = value

    def load_variables(self) -> dict:
        """Loads all global variables from the database into a dict."""
        variables = {}
        for doc in self.collection.find():
            variables[doc['name']] = doc['value']
        return variables

    @commands.group(name='setting', invoke_without_command=True)
    async def setting(self, ctx):
        """Group command for settings."""
        await ctx.send("Use subcommands to manage settings.")

    @setting.command(name='set_variable')
    async def set_variable(self, ctx, name: str, value: str):
        """Sets a variable with the given name and value."""
        setattr(self, name, value)
        self.save_variable(name, value)
        await ctx.send(f"Variable '{name}' set to '{value}'.")

    @setting.command(name='get_variable')
    async def get_variable(self, ctx, requested_name: str):
        """Gets the value of a variable by name."""
        name = ctx.invoked_with
        for var_name, var_value in self.bot.global_vars.items():
            if var_name == requested_name:
                await ctx.send(f"Variable '{var_name}' has value: '{var_value}'")
                return
            
        await ctx.send(f"Variable '{requested_name}' not found.")
    

async def setup(bot):
    await bot.add_cog(VariableSetting(bot))