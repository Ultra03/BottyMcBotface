import datetime
import traceback
import typing

import cogs.utils.logs as logging
import discord
import humanize
import pytimeparse
from data.case import Case
from discord.ext import commands


class ModActions(commands.Cog):
    """This cog handles all the possible moderator actions.
    - Kick
    - Ban
    - Unban
    - Warn
    - Liftwarn
    - Mute
    - Unmute
    - Purge
    """

    def __init__(self, bot):
        self.bot = bot

    async def check_permissions(self, ctx, user: typing.Union[discord.Member, int] = None):
        if isinstance(user, discord.Member):
            if user.id == ctx.author.id:
                await ctx.message.add_reaction("🤔")
                raise commands.BadArgument("You can't call that on yourself.")
            if user.id == self.bot.user.id:
                await ctx.message.add_reaction("🤔")
                raise commands.BadArgument("You can't call that on me :(")

        # must be at least a mod
        if not self.bot.settings.permissions.hasAtLeast(ctx.guild, ctx.author, 5):
            raise commands.BadArgument(
                "You do not have permission to use this command.")
        if user:
            if isinstance(user, discord.Member):
                if user.top_role >= ctx.author.top_role:
                    raise commands.BadArgument(
                        message=f"{user.mention}'s top role is the same or higher than yours!")

    @commands.guild_only()
    @commands.bot_has_guild_permissions(kick_members=True, ban_members=True)
    @commands.command(name="warn")
    async def warn(self, ctx: commands.Context, user: typing.Union[discord.Member, int], points: int, *, reason: str = "No reason.") -> None:
        """Warn a user (mod only)

        Example usage:
        --------------
        `!warn <@user/ID> <points> <reason (optional)>
`
        Parameters
        ----------
        user : discord.Member
            The member to warn
        points : int
            Number of points to warn far
        reason : str, optional
            Reason for warning, by default "No reason."

        """

        await self.check_permissions(ctx, user)

        if points < 1:  # can't warn for negative/0 points
            raise commands.BadArgument(message="Points can't be lower than 1.")

        # if the ID given is of a user who isn't in the guild, try to fetch the profile
        if isinstance(user, int):
            try:
                user = await self.bot.fetch_user(user)
            except discord.NotFound:
                raise commands.BadArgument(
                    f"Couldn't find user with ID {user}")

        guild = self.bot.settings.guild()

        reason = discord.utils.escape_markdown(reason)
        reason = discord.utils.escape_mentions(reason)

        # prepare the case object for database
        case = Case(
            _id=guild.case_id,
            _type="WARN",
            mod_id=ctx.author.id,
            mod_tag=str(ctx.author),
            reason=reason,
            punishment=str(points)
        )

        # increment case ID in database for next available case ID
        await self.bot.settings.inc_caseid()
        # add new case to DB
        await self.bot.settings.add_case(user.id, case)
        # add warnpoints to the user in DB
        await self.bot.settings.inc_points(user.id, points)

        # fetch latest document about user from DB
        results = await self.bot.settings.user(user.id)
        cur_points = results.warn_points

        # prepare log embed, send to #public-mod-logs, user, channel where invoked
        log = await logging.prepare_warn_log(ctx.author, user, case)
        log.add_field(name="Current points", value=cur_points, inline=True)

        log_kickban = None

        if cur_points >= 600:
            # automatically ban user if more than 600 points
            try:
                await user.send("You were banned from r/Jailbreak for reaching 600 or more points.", embed=log)
            except Exception:
                pass

            log_kickban = await self.add_ban_case(ctx, user, "600 or more warn points reached.")
            await user.ban(reason="600 or more warn points reached.")

        elif cur_points >= 400 and not results.was_warn_kicked and isinstance(user, discord.Member):
            # kick user if >= 400 points and wasn't previously kicked
            await self.bot.settings.set_warn_kicked(user.id)

            try:
                await user.send("You were kicked from r/Jailbreak for reaching 400 or more points. Please note that you will be banned at 600 points.", embed=log)
            except Exception:
                pass

            log_kickban = await self.add_kick_case(ctx, user, "400 or more warn points reached.")
            await user.kick(reason="400 or more warn points reached.")

        else:
            if isinstance(user, discord.Member):
                try:
                    await user.send("You were warned in r/Jailbreak. Please note that you will be kicked at 400 points and banned at 600 points.", embed=log)
                except Exception:
                    pass

        # also send response in channel where command was called
        await ctx.message.reply(embed=log, delete_after=10)
        await ctx.message.delete(delay=10)

        public_chan = ctx.guild.get_channel(
            self.bot.settings.guild().channel_public)
        if public_chan:
            log.remove_author()
            log.set_thumbnail(url=user.avatar_url)
            await public_chan.send(embed=log)

            if log_kickban:
                log_kickban.remove_author()
                log_kickban.set_thumbnail(url=user.avatar_url)
                await public_chan.send(embed=log_kickban)

    @commands.guild_only()
    @commands.command(name="liftwarn")
    async def liftwarn(self, ctx: commands.Context, user: discord.Member, case_id: int, *, reason: str = "No reason.") -> None:
        """Mark a warn as lifted and remove points. (mod only)

        Example usage:
        --------------
        `!liftwarn <@user/ID> <case ID> <reason (optional)>`

        Parameters
        ----------
        user : discord.Member
            User to remove warn from
        case_id : int
            The ID of the case for which we want to remove points
        reason : str, optional
            Reason for lifting warn, by default "No reason."

        """

        await self.check_permissions(ctx, user)

        # retrieve user's case with given ID
        cases = await self.bot.settings.get_case(user.id, case_id)
        case = cases.cases.filter(_id=case_id).first()

        reason = discord.utils.escape_markdown(reason)
        reason = discord.utils.escape_mentions(reason)

        # sanity checks
        if case is None:
            raise commands.BadArgument(
                message=f"{user} has no case with ID {case_id}")
        elif case._type != "WARN":
            raise commands.BadArgument(
                message=f"{user}'s case with ID {case_id} is not a warn case.")
        elif case.lifted:
            raise commands.BadArgument(
                message=f"Case with ID {case_id} already lifted.")

        u = await self.bot.settings.user(id=user.id)
        if u.warn_points - int(case.punishment) < 0:
            raise commands.BadArgument(
                message=f"Can't lift Case #{case_id} because it would make {user.mention}'s points negative.")

        # passed sanity checks, so update the case in DB
        case.lifted = True
        case.lifted_reason = reason
        case.lifted_by_tag = str(ctx.author)
        case.lifted_by_id = ctx.author.id
        case.lifted_date = datetime.datetime.now()
        cases.save()

        # remove the warn points from the user in DB
        await self.bot.settings.inc_points(user.id, -1 * int(case.punishment))

        # prepare log embed, send to #public-mod-logs, user, channel where invoked
        log = await logging.prepare_liftwarn_log(ctx.author, user, case)
        try:
            await user.send("Your warn was lifted in r/Jailbreak.", embed=log)
        except Exception:
            pass

        await ctx.message.reply(embed=log, delete_after=10)
        await ctx.message.delete(delay=10)

        public_chan = ctx.guild.get_channel(
            self.bot.settings.guild().channel_public)
        if public_chan:
            log.remove_author()
            log.set_thumbnail(url=user.avatar_url)
            await public_chan.send(embed=log)

    @commands.guild_only()
    @commands.command(name="removepoints")
    async def removepoints(self, ctx: commands.Context, user: discord.Member, points: int, *, reason: str = "No reason.") -> None:
        """Remove warnpoints from a user. (mod only)

        Example usage:
        --------------
        `!removepoints <@user/ID> <points> <reason (optional)>`

        Parameters
        ----------
        user : discord.Member
            User to remove warn from
        points : int
            Amount of points to remove
        reason : str, optional
            Reason for lifting warn, by default "No reason."

        """

        await self.check_permissions(ctx, user)

        reason = discord.utils.escape_markdown(reason)
        reason = discord.utils.escape_mentions(reason)

        if points < 1:
            raise commands.BadArgument("Points can't be lower than 1.")

        u = await self.bot.settings.user(id=user.id)
        if u.warn_points - points < 0:
            raise commands.BadArgument(
                message=f"Can't remove {points} points because it would make {user.mention}'s points negative.")

        # passed sanity checks, so update the case in DB
        # remove the warn points from the user in DB
        await self.bot.settings.inc_points(user.id, -1 * points)

        case = Case(
            _id=self.bot.settings.guild().case_id,
            _type="REMOVEPOINTS",
            mod_id=ctx.author.id,
            mod_tag=str(ctx.author),
            punishment=str(points),
            reason=reason,
        )

        # increment DB's max case ID for next case
        await self.bot.settings.inc_caseid()
        # add case to db
        await self.bot.settings.add_case(user.id, case)

        # prepare log embed, send to #public-mod-logs, user, channel where invoked
        log = await logging.prepare_removepoints_log(ctx.author, user, case)
        try:
            await user.send("Your points were removed in r/Jailbreak.", embed=log)
        except Exception:
            pass

        await ctx.message.reply(embed=log, delete_after=10)
        await ctx.message.delete(delay=10)

        public_chan = ctx.guild.get_channel(
            self.bot.settings.guild().channel_public)
        if public_chan:
            log.remove_author()
            log.set_thumbnail(url=user.avatar_url)
            await public_chan.send(embed=log)

    @commands.guild_only()
    @commands.bot_has_guild_permissions(kick_members=True)
    @commands.command(name="roblox")
    async def roblox(self, ctx: commands.Context, user: discord.Member) -> None:
        """Kick a Roblox user and tell them where to go (mod only)

        Example usage:
        --------------
        `!roblox <@user/ID>`

        Parameters
        ----------
        user : discord.Member
            User to kick
        """
        
        await self.check_permissions(ctx, user)
        reason = "This Discord server is for iOS jailbreaking, not Roblox. Please join https://discord.gg/jailbreak instead, thank you!"
        log = await self.add_kick_case(ctx, user, reason)

        try:
            await user.send("You were kicked from r/Jailbreak", embed=log)
        except Exception:
            pass

        await user.kick(reason=reason)

        await ctx.message.reply(embed=log, delete_after=10)
        await ctx.message.delete(delay=10)

        public_chan = ctx.guild.get_channel(
            self.bot.settings.guild().channel_public)
        if public_chan:
            log.remove_author()
            log.set_thumbnail(url=user.avatar_url)
            await public_chan.send(embed=log)
            
    @commands.guild_only()
    @commands.bot_has_guild_permissions(kick_members=True)
    @commands.command(name="kick")
    async def kick(self, ctx: commands.Context, user: discord.Member, *, reason: str = "No reason.") -> None:
        """Kick a user (mod only)

        Example usage:
        --------------
        `!kick <@user/ID> <reason (optional)>`

        Parameters
        ----------
        user : discord.Member
            User to kick
        reason : str, optional
            Reason for kick, by default "No reason."

        """

        await self.check_permissions(ctx, user)

        reason = discord.utils.escape_markdown(reason)
        reason = discord.utils.escape_mentions(reason)

        log = await self.add_kick_case(ctx, user, reason)

        try:
            await user.send("You were kicked from r/Jailbreak", embed=log)
        except Exception:
            pass

        await user.kick(reason=reason)

        await ctx.message.reply(embed=log, delete_after=10)
        await ctx.message.delete(delay=10)

        public_chan = ctx.guild.get_channel(
            self.bot.settings.guild().channel_public)
        if public_chan:
            log.remove_author()
            log.set_thumbnail(url=user.avatar_url)
            await public_chan.send(embed=log)

    async def add_kick_case(self, ctx, user, reason):
        # prepare case for DB
        case = Case(
            _id=self.bot.settings.guild().case_id,
            _type="KICK",
            mod_id=ctx.author.id,
            mod_tag=str(ctx.author),
            reason=reason,
        )

        # increment max case ID for next case
        await self.bot.settings.inc_caseid()
        # add new case to DB
        await self.bot.settings.add_case(user.id, case)

        return await logging.prepare_kick_log(ctx.author, user, case)

    @commands.guild_only()
    @commands.bot_has_guild_permissions(ban_members=True)
    @commands.command(name="ban")
    async def ban(self, ctx: commands.Context, user: typing.Union[discord.Member, int], *, reason: str = "No reason."):
        """Ban a user (mod only)

        Example usage:
        --------------
        `!ban <@user/ID> <reason (optional)>`

        Parameters
        ----------
        user : typing.Union[discord.Member, int]
            The user to be banned, doesn't have to be part of the guild
        reason : str, optional
            Reason for ban, by default "No reason."

        """

        await self.check_permissions(ctx, user)

        reason = discord.utils.escape_markdown(reason)
        reason = discord.utils.escape_mentions(reason)

        # if the ID given is of a user who isn't in the guild, try to fetch the profile
        if isinstance(user, int):
            try:
                user = await self.bot.fetch_user(user)
            except discord.NotFound:
                raise commands.BadArgument(
                    f"Couldn't find user with ID {user}")

        log = await self.add_ban_case(ctx, user, reason)

        try:
            await user.send("You were banned from r/Jailbreak", embed=log)
        except Exception:
            pass

        if isinstance(user, discord.Member):
            await user.ban(reason=reason)
        else:
            # hackban for user not currently in guild
            await ctx.guild.ban(discord.Object(id=user.id))

        await ctx.message.reply(embed=log, delete_after=10)
        await ctx.message.delete(delay=10)

        public_chan = ctx.guild.get_channel(
            self.bot.settings.guild().channel_public)
        if public_chan:
            log.remove_author()
            log.set_thumbnail(url=user.avatar_url)
            await public_chan.send(embed=log)

    async def add_ban_case(self, ctx, user, reason):
        # prepare the case to store in DB
        case = Case(
            _id=self.bot.settings.guild().case_id,
            _type="BAN",
            mod_id=ctx.author.id,
            mod_tag=str(ctx.author),
            punishment="PERMANENT",
            reason=reason,
        )

        # increment DB's max case ID for next case
        await self.bot.settings.inc_caseid()
        # add case to db
        await self.bot.settings.add_case(user.id, case)
        # prepare log embed to send to #public-mod-logs, user and context
        return await logging.prepare_ban_log(ctx.author, user, case)

    @commands.guild_only()
    @commands.bot_has_guild_permissions(ban_members=True)
    @commands.command(name="unban")
    async def unban(self, ctx: commands.Context, user: int, *, reason: str = "No reason.") -> None:
        """Unban a user (must use ID) (mod only)

        Example usage:
        --------------
        `!unban <user ID> <reason (optional)> `

        Parameters
        ----------
        user : int
            ID of the user to unban
        reason : str, optional
            Reason for unban, by default "No reason."

        """

        await self.check_permissions(ctx)

        reason = discord.utils.escape_markdown(reason)
        reason = discord.utils.escape_mentions(reason)

        try:
            user = await self.bot.fetch_user(user)
        except discord.NotFound:
            raise commands.BadArgument(f"Couldn't find user with ID {user}")

        try:
            await ctx.guild.unban(discord.Object(id=user.id), reason=reason)
        except discord.NotFound:
            raise commands.BadArgument(f"{user} is not banned.")

        case = Case(
            _id=self.bot.settings.guild().case_id,
            _type="UNBAN",
            mod_id=ctx.author.id,
            mod_tag=str(ctx.author),
            reason=reason,
        )
        await self.bot.settings.inc_caseid()
        await self.bot.settings.add_case(user.id, case)

        log = await logging.prepare_unban_log(ctx.author, user, case)
        await ctx.message.reply(embed=log, delete_after=10)
        await ctx.message.delete(delay=10)

        public_chan = ctx.guild.get_channel(
            self.bot.settings.guild().channel_public)
        if public_chan:
            log.remove_author()
            log.set_thumbnail(url=user.avatar_url)
            await public_chan.send(embed=log)

    @commands.guild_only()
    @commands.bot_has_guild_permissions(manage_messages=True)
    @commands.command(name="purge")
    async def purge(self, ctx: commands.Context, limit: int = 0) -> None:
        """Purge messages from current channel (mod only)

        Example usage:
        --------------
        `!purge <number of messages>`

        Parameters
        ----------
        limit : int, optional
            Number of messages to purge, must be > 0, by default 0 for error handling

        """

        await self.check_permissions(ctx)

        if limit <= 0:
            raise commands.BadArgument(
                "Number of messages to purge must be greater than 0")

        msgs = await ctx.channel.history(limit=limit+1).flatten()

        await ctx.channel.purge(limit=limit+1)
        await ctx.send(f'Purged {len(msgs)} messages.', delete_after=10)

    @commands.guild_only()
    @commands.bot_has_guild_permissions(manage_roles=True)
    @commands.command(name="mute")
    async def mute(self, ctx: commands.Context, user: discord.Member, dur: str = "", *, reason: str = "No reason.") -> None:
        """Mute a user (mod only)

        Example usage:
        --------------
        `!mute <@user/ID> <duration> <reason (optional)>`

        Parameters
        ----------
        user : discord.Member
            Member to mute
        dur : str
            Duration of mute (i.e 1h, 10m, 1d)
        reason : str, optional
            Reason for mute, by default "No reason."

        """
        await self.check_permissions(ctx, user)

        reason = discord.utils.escape_markdown(reason)
        reason = discord.utils.escape_mentions(reason)

        now = datetime.datetime.now()
        delta = pytimeparse.parse(dur)

        if delta is None:
            if reason == "No reason." and dur == "":
                reason = "No reason."
            elif reason == "No reason.":
                reason = dur
            else:
                reason = f"{dur} {reason}"

        mute_role = self.bot.settings.guild().role_mute
        mute_role = ctx.guild.get_role(mute_role)

        if mute_role in user.roles:
            raise commands.BadArgument("This user is already muted.")

        case = Case(
            _id=self.bot.settings.guild().case_id,
            _type="MUTE",
            date=now,
            mod_id=ctx.author.id,
            mod_tag=str(ctx.author),
            reason=reason,
        )

        if delta:
            try:
                time = now + datetime.timedelta(seconds=delta)
                case.until = time
                case.punishment = humanize.naturaldelta(
                    time - now, minimum_unit="seconds")
                self.bot.settings.tasks.schedule_unmute(user.id, time)
            except Exception:
                raise commands.BadArgument(
                    "An error occured, this user is probably already muted")
        else:
            case.punishment = "PERMANENT"

        await self.bot.settings.inc_caseid()
        await self.bot.settings.add_case(user.id, case)
        u = await self.bot.settings.user(id=user.id)
        u.is_muted = True
        u.save()

        await user.add_roles(mute_role)

        log = await logging.prepare_mute_log(ctx.author, user, case)
        await ctx.message.reply(embed=log, delete_after=10)
        await ctx.message.delete(delay=10)

        public_chan = ctx.guild.get_channel(
            self.bot.settings.guild().channel_public)
        if public_chan:
            log.remove_author()
            log.set_thumbnail(url=user.avatar_url)
            await public_chan.send(embed=log)

        try:
            await user.send("You have been muted in r/Jailbreak", embed=log)
        except Exception:
            pass

    @commands.guild_only()
    @commands.bot_has_guild_permissions(manage_roles=True)
    @commands.command(name="unmute")
    async def unmute(self, ctx: commands.Context, user: discord.Member, *, reason: str = "No reason.") -> None:
        """Unmute a user (mod only)

        Example usage:
        --------------
       ` !unmute <@user/ID> <reason (optional)>`

        Parameters
        ----------
        user : discord.Member
            Member to unmute
        reason : str, optional
            Reason for unmute, by default "No reason."

        """

        await self.check_permissions(ctx, user)

        mute_role = self.bot.settings.guild().role_mute
        mute_role = ctx.guild.get_role(mute_role)
        await user.remove_roles(mute_role)

        u = await self.bot.settings.user(id=user.id)
        u.is_muted = False
        u.save()

        try:
            self.bot.settings.tasks.cancel_unmute(user.id)
        except Exception:
            pass

        case = Case(
            _id=self.bot.settings.guild().case_id,
            _type="UNMUTE",
            mod_id=ctx.author.id,
            mod_tag=str(ctx.author),
            reason=reason,
        )
        await self.bot.settings.inc_caseid()
        await self.bot.settings.add_case(user.id, case)

        log = await logging.prepare_unmute_log(ctx.author, user, case)

        await ctx.message.reply(embed=log, delete_after=10)
        await ctx.message.delete(delay=10)

        try:
            await user.send("You have been unmuted in r/Jailbreak", embed=log)
        except Exception:
            pass

        public_chan = ctx.guild.get_channel(
            self.bot.settings.guild().channel_public)
        if public_chan:
            log.remove_author()
            log.set_thumbnail(url=user.avatar_url)
            await public_chan.send(embed=log)

    @unmute.error
    @mute.error
    @liftwarn.error
    @unban.error
    @ban.error
    @warn.error
    @purge.error
    @kick.error
    @roblox.error
    @removepoints.error
    async def info_error(self, ctx, error):
        await ctx.message.delete(delay=5)
        if (isinstance(error, commands.MissingRequiredArgument)
            or isinstance(error, commands.BadArgument)
            or isinstance(error, commands.BadUnionArgument)
            or isinstance(error, commands.BotMissingPermissions)
            or isinstance(error, commands.MissingPermissions)
                or isinstance(error, commands.NoPrivateMessage)):
            await self.bot.send_error(ctx, error)
        else:
            await self.bot.send_error(ctx, error)
            traceback.print_exc()


def setup(bot):
    bot.add_cog(ModActions(bot))
