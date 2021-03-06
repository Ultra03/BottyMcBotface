import traceback

import discord
import datetime
import typing
from discord.ext import commands


class Misc(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.spam_cooldown = commands.CooldownMapping.from_cooldown(3, 15.0, commands.BucketType.channel)

    @commands.command(name="jumbo")
    @commands.guild_only()
    async def jumbo(self, ctx, emoji: typing.Union[discord.Emoji, discord.PartialEmoji]):
        """Post large version of a given emoji

        Example usage
        -------------
        !jumbo :ntwerk:

        Parameters
        ----------
        emoji : typing.Union[discord.Emoji, discord.PartialEmoji]
            Emoji to post
        """


        bot_chan = self.bot.settings.guild().channel_botspam
        if not self.bot.settings.permissions.hasAtLeast(ctx.guild, ctx.author, 5) and ctx.channel.id != bot_chan:
            if await self.ratelimit(ctx.message):
                raise commands.BadArgument("This command is on cooldown.")

        await ctx.message.delete()
        await ctx.send(emoji.url)

    async def ratelimit(self, message):
        bucket = self.spam_cooldown.get_bucket(message)
        return bucket.update_rate_limit()

    @commands.command(name="avatar")
    @commands.guild_only()
    async def avatar(self, ctx, member: discord.Member = None):
        """Post large version of a given user's avatar

        Parameters
        ----------
        member : discord.Member, optional
            Member to get avatar of, default to command invoker
        """

        if member is None:
            member = ctx.author

        bot_chan = self.bot.settings.guild().channel_botspam

        if not self.bot.settings.permissions.hasAtLeast(ctx.guild, ctx.author, 5) and ctx.channel.id != bot_chan:
            raise commands.BadArgument(
                f"Command only allowed in <#{bot_chan}>")

        await ctx.message.delete()
        await ctx.send(member.avatar_url)

    @jumbo.error
    @avatar.error
    async def info_error(self, ctx, error):
        await ctx.message.delete(delay=5)
        if (isinstance(error, commands.MissingRequiredArgument)
            or isinstance(error, commands.BadArgument)
            or isinstance(error, commands.BadUnionArgument)
            or isinstance(error, commands.MissingPermissions)
            or isinstance(error, commands.BotMissingPermissions)
            or isinstance(error, commands.MaxConcurrencyReached)
                or isinstance(error, commands.NoPrivateMessage)):
            await self.bot.send_error(ctx, error)
        else:
            await self.bot.send_error(ctx, "A fatal error occured. Tell <@109705860275539968> about this.")
            traceback.print_exc()


def setup(bot):
    bot.add_cog(Misc(bot))
