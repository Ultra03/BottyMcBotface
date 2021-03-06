import datetime
import re
import string
import traceback

import cogs.utils.logs as logging
import discord
import humanize
import pytimeparse
from cogs.monitors.report import report
from data.case import Case
from discord.ext import commands
from fold_to_ascii import fold


class FilterMonitor(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.spoiler_filter = r'\|\|(.*?)\|\|'
        self.invite_filter = r'(?:https?://)?discord(?:(?:app)?\.com/invite|\.gg)\/{1,}[a-zA-Z0-9]+/?'
        self.spam_cooldown = commands.CooldownMapping.from_cooldown(2, 10.0, commands.BucketType.user)

    @commands.Cog.listener()
    async def on_message(self, msg):
        if not msg.guild:
            return
        if msg.author.bot:
            return
        guild = self.bot.settings.guild()
        if msg.guild.id != self.bot.settings.guild_id:
            return
        if msg.channel.id in guild.filter_excluded_channels:
            return
        """
        BAD WORD FILTER
        """
        symbols = (u"абвгдеёжзийклмнопрстуфхцчшщъыьэюяАБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ",
                   u"abBrdeex3nnKnmHonpcTyoxu4wwbbbeoRABBrDEEX3NNKNMHONPCTyOXU4WWbbbEOR")

        tr = {ord(a): ord(b) for a, b in zip(*symbols)}

        folded_message = fold(msg.content.translate(tr).lower()).lower()
        folded_without_spaces = "".join(folded_message.split())
        folded_without_spaces_and_punctuation = folded_without_spaces.translate(str.maketrans('', '', string.punctuation))

        if folded_message:
            reported = False
            for word in guild.filter_words:
                if not self.bot.settings.permissions.hasAtLeast(msg.guild, msg.author, word.bypass):
                    if (word.word.lower() in folded_message) or \
                        (word.word != "fag" and word.word.lower() in folded_without_spaces_and_punctuation):
                        # remove all whitespace, punctuation in message and run filter again
                        # prevent a potential false positive, sorry for langauge :(
                        await self.delete(msg)
                        if not reported:
                            await self.ratelimit(msg)
                            reported = True
                        if word.notify:
                            await report(self.bot, msg, msg.author)
                            return
        """
        INVITE FILTER
        """
        if msg.content:
            if not self.bot.settings.permissions.hasAtLeast(msg.guild, msg.author, 5):
                invites = re.findall(self.invite_filter, msg.content, flags=re.S)
                if invites:
                    whitelist = self.bot.settings.guild().filter_excluded_guilds
                    for invite in invites:
                        try:
                            invite = await self.bot.fetch_invite(invite)

                            id = None
                            if isinstance(invite, discord.Invite):
                                id = invite.guild.id
                            elif isinstance(invite, discord.PartialInviteGuild) or isinstance(invite, discord.PartialInviteChannel):
                                id = invite.id

                            if id not in whitelist:
                                await self.delete(msg)
                                await self.ratelimit(msg)
                                await report(self.bot, msg, msg.author, invite)
                                return

                        except discord.errors.NotFound:
                            await self.delete(msg)
                            await self.ratelimit(msg)
                            await report(self.bot, msg, msg.author, invite)
                            return
        """
        SPOILER FILTER
        """
        if not self.bot.settings.permissions.hasAtLeast(msg.guild, msg.author, 5):
            if re.search(self.spoiler_filter, msg.content, flags=re.S):
                await self.delete(msg)
                return

            for a in msg.attachments:
                if a.is_spoiler():
                    await self.delete(msg)
                    return

        """
        NEWLINE FILTER
        """
        if not self.bot.settings.permissions.hasAtLeast(msg.guild, msg.author, 5):
            if len(msg.content.splitlines()) > 100:
                dev_role = msg.guild.get_role(guild.role_dev)
                if not dev_role or dev_role not in msg.author.roles:
                    await self.delete(msg)
                    await self.ratelimit(msg)
                    return

    async def ratelimit(self, message):
        current = message.created_at.replace(tzinfo=datetime.timezone.utc).timestamp()

        bucket = self.spam_cooldown.get_bucket(message)
        if bucket.update_rate_limit(current):
            ctx = await self.bot.get_context(message, cls=commands.Context)
            await self.mute(ctx, message.author)

    async def delete(self, msg):
        try:
            await msg.delete()
        except Exception:
            pass

    @commands.Cog.listener()
    async def on_message_edit(self, before, after):
        await self.on_message(after)

    async def mute(self, ctx: commands.Context, user: discord.Member) -> None:
        dur = "15m"
        reason = "Filter spam"

        now = datetime.datetime.now()
        delta = pytimeparse.parse(dur)

        u = await self.bot.settings.user(id=user.id)
        mute_role = self.bot.settings.guild().role_mute
        mute_role = ctx.guild.get_role(mute_role)

        if mute_role in user.roles or u.is_muted:
            return

        case = Case(
            _id=self.bot.settings.guild().case_id,
            _type="MUTE",
            date=now,
            mod_id=ctx.me.id,
            mod_tag=str(ctx.me),
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

        await self.bot.settings.inc_caseid()
        await self.bot.settings.add_case(user.id, case)
        u = await self.bot.settings.user(id=user.id)
        u.is_muted = True
        u.save()

        await user.add_roles(mute_role)

        log = await logging.prepare_mute_log(ctx.me, user, case)

        public_chan = ctx.guild.get_channel(self.bot.settings.guild().channel_public)
        if public_chan:
            log.remove_author()
            log.set_thumbnail(url=user.avatar_url)
            await public_chan.send(embed=log)

        try:
            await user.send("You have been muted in r/Jailbreak", embed=log)
        except Exception:
            pass

    async def info_error(self, ctx, error):
        if (isinstance(error, commands.MissingRequiredArgument)
            or isinstance(error, commands.BadArgument)
            or isinstance(error, commands.BadUnionArgument)
            or isinstance(error, commands.MissingPermissions)
                or isinstance(error, commands.NoPrivateMessage)):
            await self.bot.send_error(ctx, error)
        else:
            traceback.print_exc()


def setup(bot):
    bot.add_cog(FilterMonitor(bot))
