from __future__ import annotations

import asyncio
import logging
import math
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands, tasks

from ninja_bot.cs2.awards import compute_awards
from ninja_bot.cs2.cache import CachedStatsProvider, TtlCache
from ninja_bot.cs2.client import LeetifyClient, StatsProvider, full_match
from ninja_bot.cs2.embed import (
    build_announcement_embed,
    build_leaderboard_embed,
    build_match_embed,
    build_profile_embed,
)
from ninja_bot.cs2.formatting import escape_name
from ninja_bot.cs2.identities import Identity, MemberLookup, resolve_identities
from ninja_bot.cs2.models import (
    Cs2Error,
    Match,
    NoMatchesError,
    NotLinkedError,
    Profile,
    RateLimitedError,
)
from ninja_bot.cs2.poller import MatchPoller, NewMatch
from ninja_bot.cs2.resolver import SteamCommunityResolver, SteamResolver
from ninja_bot.cs2.squad import (
    Metric,
    fetch_squad,
    leaderboard_notes,
    metric_label,
    rank_squad,
)
from ninja_bot.cs2.steam import (
    MAX_STEAM_INPUT_LENGTH,
    SteamId,
    VanityName,
    parse_steam_target,
)
from ninja_bot.cs2.store import LinkStore, SqliteLinkStore
from ninja_bot.media.cooldown import UserCooldown

if TYPE_CHECKING:
    from ninja_bot.bot import NinjaBot

log = logging.getLogger(__name__)

GENERIC_ERROR_MESSAGE = "Something went wrong while fetching CS2 stats. Please try again later."
COOLDOWN_SECONDS = 10.0
LINK_COOLDOWN_SECONDS = 15.0
ALREADY_LINKED_MESSAGE = "You're already linked to that Steam account."
UNLINKED_MESSAGE = "Your Steam account is no longer linked."
NOTHING_TO_UNLINK_MESSAGE = "You don't have a linked Steam account."
MAX_MATCH_ID_LENGTH = 64
INVALID_MATCH_ID_MESSAGE = "That doesn't look like a Leetify match id."
LEADERBOARD_COOLDOWN_SECONDS = 30.0
ALL_PROFILES_FAILED_MESSAGE = (
    "Couldn't load any linked player's Leetify profile. Please try again later."
)
# Channel types auto-posts can go to (all have guild permissions and `send`).
POST_CHANNEL_TYPES = (discord.TextChannel, discord.Thread, discord.VoiceChannel)
type PostChannel = discord.TextChannel | discord.Thread | discord.VoiceChannel
# Causes for a skipped auto-post cycle; each is logged once until the channel works again.
CHANNEL_MISSING = "missing"
CHANNEL_NOT_TEXT = "not a text channel"
CHANNEL_NO_PERMISSIONS = "missing View Channel, Send Messages or Embed Links"
# Discord display names are cached briefly to save REST calls; a 404 for a shorter time.
MEMBER_NAME_TTL_SECONDS = 300.0
MEMBER_MISSING_TTL_SECONDS = 60.0
MEMBER_CACHE_SIZE = 512


@dataclass(frozen=True)
class AutoPost:
    """What the background auto-post loop needs; it only runs when `channel_id` is set."""

    bot: commands.Bot
    poller: MatchPoller
    channel_id: int | None
    poll_seconds: float


def linked_message(name: str, steam64_id: str | None = None) -> str:
    """Success reply; `steam64_id` is shown when it came from a custom URL lookup."""
    if steam64_id is None:
        return f"Linked to **{escape_name(name)}** on Leetify."
    return f"Linked to **{escape_name(name)}** on Leetify (Steam ID {steam64_id})."


def not_linked_message(name: str) -> str:
    """NotLinkedError text for someone other than the caller."""
    return (
        f"**{escape_name(name)}** hasn't linked a Steam account yet. They can run `/link` to do it."
    )


def cooldown_message(retry_after: float) -> str:
    """User-facing reply for a CS2 stats cooldown, rounded up to whole seconds."""
    seconds = max(1, math.ceil(retry_after))
    unit = "second" if seconds == 1 else "seconds"
    return f"Slow down! You can use CS2 stats commands again in {seconds} {unit}."


def clean_match_id(match: str | None) -> str | None:
    """Strip an optional match id; blank means "latest". Raises Cs2Error if too long."""
    if match is None:
        return None
    if len(match) > MAX_MATCH_ID_LENGTH:
        raise Cs2Error(INVALID_MATCH_ID_MESSAGE)
    return match.strip() or None


class MemberCache:
    """Display names per (guild ID, user ID); only definitive answers are cached."""

    def __init__(
        self,
        clock: Callable[[], float] = time.monotonic,
        max_size: int = MEMBER_CACHE_SIZE,
    ) -> None:
        self.names: TtlCache[tuple[int, int], str] = TtlCache(
            MEMBER_NAME_TTL_SECONDS, clock, max_size
        )
        # Confirmed "not a member" (Discord answered 404); the value is always True.
        self.not_members: TtlCache[tuple[int, int], bool] = TtlCache(
            MEMBER_MISSING_TTL_SECONDS, clock, max_size
        )
        # Lookups in progress, so concurrent callers share one REST call.
        self.pending: dict[tuple[int, int], asyncio.Task[str | None]] = {}


def member_lookup(guild: discord.Guild, cache: MemberCache) -> MemberLookup:
    """Display name of a guild member: our cache, then the member cache, then REST.

    No Server Members intent is needed. Transient HTTP errors are not cached, so the next
    lookup tries again.
    """

    async def fetch(key: tuple[int, int]) -> str | None:
        try:
            member = await guild.fetch_member(key[1])
        except discord.NotFound:
            cache.not_members.set(key, True)
            return None
        except discord.HTTPException as exc:
            log.warning("Could not fetch a Discord member: HTTP %s", exc.status)
            return None
        cache.names.set(key, member.display_name)
        return member.display_name

    async def lookup(discord_id: int) -> str | None:
        key = (guild.id, discord_id)
        name = cache.names.get(key)
        if name is not None:
            return name
        if cache.not_members.get(key):
            return None
        member = guild.get_member(discord_id)
        if member is not None:
            cache.names.set(key, member.display_name)
            return member.display_name
        task = cache.pending.get(key)
        if task is None:
            task = asyncio.create_task(fetch(key))
            cache.pending[key] = task
            task.add_done_callback(lambda _: cache.pending.pop(key, None))
        # Shielded so one cancelled caller doesn't cancel the lookup for the others.
        return await asyncio.shield(task)

    return lookup


async def _send_safely(send: Awaitable[object]) -> bool:
    """Await a Discord send; log and swallow HTTP errors (e.g. an expired token)."""
    try:
        await send
    except discord.HTTPException:
        log.warning("Could not send CS2 response", exc_info=True)
        return False
    return True


class Cs2(commands.Cog):
    def __init__(
        self,
        stats: StatsProvider,
        links: LinkStore,
        cooldown: UserCooldown | None = None,
        link_cooldown: UserCooldown | None = None,
        resolver: SteamResolver | None = None,
        leaderboard_cooldown: UserCooldown | None = None,
        auto_post: AutoPost | None = None,
        members: MemberCache | None = None,
    ) -> None:
        self.stats = stats
        self.links = links
        # Turns custom URL names into SteamID64s; creates no session until first use.
        self.resolver = resolver if resolver is not None else SteamCommunityResolver()
        # Used by the stats commands; taken after input is valid, refunded unless posted.
        self.cooldown = cooldown if cooldown is not None else UserCooldown(COOLDOWN_SECONDS)
        # Separate from the stats cooldown; never refunded once Leetify was called.
        self.link_cooldown = (
            link_cooldown if link_cooldown is not None else UserCooldown(LINK_COOLDOWN_SECONDS)
        )
        # One /leaderboard costs a profile request per link, so it gets a longer cooldown.
        self.leaderboard_cooldown = (
            leaderboard_cooldown
            if leaderboard_cooldown is not None
            else UserCooldown(LEADERBOARD_COOLDOWN_SECONDS)
        )
        self.auto_post = auto_post
        # Set after a Leetify rate limit so the next poll cycle is skipped once.
        self._skip_next_poll = False
        # Channel problems already logged, so a broken setup warns once instead of every cycle.
        self._channel_warnings: set[str] = set()
        # Discord member names for scoreboards and auto-posts; in memory only.
        self.members = members if members is not None else MemberCache()

    async def cog_load(self) -> None:
        if self.auto_post is None or self.auto_post.channel_id is None:
            log.debug("CS2_CHANNEL_ID is not set; CS2 auto-posts are off")
            return
        self.poll_loop.change_interval(seconds=self.auto_post.poll_seconds)
        self.poll_loop.start()

    async def cog_unload(self) -> None:
        self.poll_loop.cancel()
        try:
            await self.stats.aclose()
        finally:
            await self.resolver.aclose()

    @tasks.loop(seconds=300)
    async def poll_loop(self) -> None:
        await self.run_poll_cycle()

    @poll_loop.before_loop
    async def _before_poll_loop(self) -> None:
        if self.auto_post is not None:
            await self.auto_post.bot.wait_until_ready()

    async def run_poll_cycle(self) -> None:
        """Post new matches of linked players; never raises, so the loop keeps running."""
        if self.auto_post is None or self.auto_post.channel_id is None:
            return
        if self._skip_next_poll:
            self._skip_next_poll = False
            log.info("Skipping a CS2 poll cycle after a Leetify rate limit")
            return
        try:
            channel = await self._post_channel(self.auto_post.bot, self.auto_post.channel_id)
            if channel is None:
                return
            result = await self.auto_post.poller.poll()
            for new_match in result.matches:
                await self._announce(channel, self.auto_post.poller, new_match)
            if result.rate_limited:
                log.info("Leetify rate limited the CS2 poll; skipping the next cycle")
                self._skip_next_poll = True
        except Exception:
            log.exception("Unexpected error in a CS2 poll cycle")

    async def _announce(
        self, channel: PostChannel, poller: MatchPoller, new_match: NewMatch
    ) -> None:
        """Send one announcement; the match is confirmed even if the send failed."""
        try:
            match = new_match.match
            identities = await self._match_identities_or_none(channel.guild, match)
            embed = build_announcement_embed(
                match, compute_awards(match), new_match.squad, identities=identities
            )
            # Squad members are shown as mentions; render them, but never ping anyone.
            await _send_safely(
                channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
            )
        finally:
            await poller.confirm(new_match)

    async def _post_channel(self, bot: commands.Bot, channel_id: int) -> PostChannel | None:
        """The auto-post channel, or None (warned once per cause) if it can't be used."""
        channel = bot.get_channel(channel_id)
        if channel is None:
            try:
                channel = await bot.fetch_channel(channel_id)
            except (discord.NotFound, discord.Forbidden):
                self._warn_channel(CHANNEL_MISSING)
                return None
        if not isinstance(channel, POST_CHANNEL_TYPES):
            self._warn_channel(CHANNEL_NOT_TEXT)
            return None
        permissions = channel.permissions_for(channel.guild.me)
        can_send = (
            permissions.send_messages_in_threads
            if isinstance(channel, discord.Thread)
            else permissions.send_messages
        )
        if not (permissions.view_channel and can_send and permissions.embed_links):
            self._warn_channel(CHANNEL_NO_PERMISSIONS)
            return None
        self._channel_warnings.clear()
        return channel

    def _warn_channel(self, cause: str) -> None:
        if cause in self._channel_warnings:
            return
        self._channel_warnings.add(cause)
        log.warning("Not posting CS2 matches: the CS2_CHANNEL_ID channel is %s", cause)

    @app_commands.command(name="link", description="Link your Steam account for CS2 stats")
    @app_commands.describe(
        steam="Your SteamID64, custom URL name, or steamcommunity.com profile link",
    )
    async def link(
        self,
        interaction: discord.Interaction,
        steam: app_commands.Range[str, 1, MAX_STEAM_INPUT_LENGTH],
    ) -> None:
        await self.handle_link(interaction, steam)

    @app_commands.command(name="scoreboard", description="Show a CS2 match scoreboard")
    @app_commands.describe(
        player="Whose match to show (defaults to you)",
        match="A Leetify match id (defaults to the latest match)",
    )
    async def scoreboard(
        self,
        interaction: discord.Interaction,
        player: discord.Member | None = None,
        match: app_commands.Range[str, 1, MAX_MATCH_ID_LENGTH] | None = None,
    ) -> None:
        await self.handle_scoreboard(interaction, player, match)

    @app_commands.command(name="profile", description="Show a CS2 player's Leetify profile")
    @app_commands.describe(player="Whose profile to show (defaults to you)")
    async def profile(
        self, interaction: discord.Interaction, player: discord.Member | None = None
    ) -> None:
        await self.handle_profile(interaction, player)

    @app_commands.command(name="leaderboard", description="Rank everyone who linked CS2 stats")
    @app_commands.describe(metric="What to rank by (defaults to Leetify rating)")
    @app_commands.choices(
        metric=[app_commands.Choice(name=metric_label(m), value=m.value) for m in Metric]
    )
    async def leaderboard(
        self,
        interaction: discord.Interaction,
        metric: app_commands.Choice[str] | None = None,
    ) -> None:
        chosen = Metric(metric.value) if metric is not None else Metric.RATING
        await self.handle_leaderboard(interaction, chosen)

    @app_commands.command(name="unlink", description="Remove your linked Steam account")
    async def unlink(self, interaction: discord.Interaction) -> None:
        await self.handle_unlink(interaction)

    async def handle_link(self, interaction: discord.Interaction, steam: str) -> None:
        try:
            target = parse_steam_target(steam)
        except Cs2Error as exc:
            await interaction.response.send_message(exc.message, ephemeral=True)
            return

        # A known ID can be checked before any network call; a name has to be resolved first.
        if isinstance(target, SteamId):
            try:
                current = await self.links.get(interaction.user.id)
            except Exception as exc:
                await self._reply_lookup_error(interaction, exc)
                return
            if current == target.value:
                await interaction.response.send_message(ALREADY_LINKED_MESSAGE, ephemeral=True)
                return

        retry_after = self.link_cooldown.try_acquire(interaction.user.id)
        if retry_after > 0:
            await interaction.response.send_message(cooldown_message(retry_after), ephemeral=True)
            return

        # Steam and Leetify can be slow; defer so Discord doesn't time out the interaction.
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            message = await self._link_deferred(interaction.user.id, target)
        except Cs2Error as exc:
            message = exc.message
        except Exception:
            log.exception("Unexpected error linking a Steam account")
            message = GENERIC_ERROR_MESSAGE
        await _send_safely(interaction.followup.send(message, ephemeral=True))

    async def _link_deferred(self, user_id: int, target: SteamId | VanityName) -> str:
        """Resolve, verify and store the link; returns the reply text."""
        if isinstance(target, VanityName):
            steam64_id = await self.resolver.resolve_vanity(target.value)
            if await self.links.get(user_id) == steam64_id:
                return ALREADY_LINKED_MESSAGE
            shown_id: str | None = steam64_id
        else:
            steam64_id = target.value
            shown_id = None
        profile = await self.stats.get_profile(steam64_id)
        await self.links.set(user_id, steam64_id)
        return linked_message(profile.name or steam64_id, shown_id)

    async def handle_unlink(self, interaction: discord.Interaction) -> None:
        try:
            removed = await self.links.remove(interaction.user.id)
        except Exception:
            log.exception("Unexpected error unlinking a Steam account")
            await interaction.response.send_message(GENERIC_ERROR_MESSAGE, ephemeral=True)
            return
        message = UNLINKED_MESSAGE if removed else NOTHING_TO_UNLINK_MESSAGE
        await interaction.response.send_message(message, ephemeral=True)

    async def handle_scoreboard(
        self,
        interaction: discord.Interaction,
        player: discord.Member | None = None,
        match: str | None = None,
    ) -> None:
        try:
            game_id = clean_match_id(match)
            target = player if player is not None else interaction.user
            steam64_id = await self._linked_steam_id(interaction, target)
        except Exception as exc:
            await self._reply_lookup_error(interaction, exc)
            return
        await self._run_with_cooldown(
            interaction, lambda: self._scoreboard_deferred(interaction, steam64_id, game_id)
        )

    async def handle_profile(
        self, interaction: discord.Interaction, player: discord.Member | None = None
    ) -> None:
        try:
            target = player if player is not None else interaction.user
            steam64_id = await self._linked_steam_id(interaction, target)
        except Exception as exc:
            await self._reply_lookup_error(interaction, exc)
            return
        await self._run_with_cooldown(
            interaction, lambda: self._profile_deferred(interaction, steam64_id)
        )

    async def handle_leaderboard(
        self, interaction: discord.Interaction, metric: Metric = Metric.RATING
    ) -> None:
        await self._run_with_cooldown(
            interaction,
            lambda: self._leaderboard_deferred(interaction, metric),
            self.leaderboard_cooldown,
        )

    async def _reply_lookup_error(self, interaction: discord.Interaction, exc: Exception) -> None:
        """Ephemeral reply for a failed input check or link lookup (before the defer)."""
        if isinstance(exc, Cs2Error):
            await interaction.response.send_message(exc.message, ephemeral=True)
            return
        log.error("Unexpected error looking up a CS2 link", exc_info=exc)
        await interaction.response.send_message(GENERIC_ERROR_MESSAGE, ephemeral=True)

    async def _run_with_cooldown(
        self,
        interaction: discord.Interaction,
        work: Callable[[], Awaitable[bool]],
        cooldown: UserCooldown | None = None,
    ) -> None:
        """Take the caller's cooldown, defer and run `work`; refund unless it posted.

        `cooldown` defaults to the shared stats cooldown.
        """
        cooldown = cooldown if cooldown is not None else self.cooldown
        user_id = interaction.user.id
        retry_after = cooldown.try_acquire(user_id)
        if retry_after > 0:
            await interaction.response.send_message(cooldown_message(retry_after), ephemeral=True)
            return

        posted = False
        try:
            await interaction.response.defer(thinking=True)
            posted = await work()
        finally:
            if not posted:
                cooldown.refund(user_id)

    async def _linked_steam_id(
        self, interaction: discord.Interaction, target: discord.User | discord.Member
    ) -> str:
        steam64_id = await self.links.get(target.id)
        if steam64_id is None:
            if target.id == interaction.user.id:
                raise NotLinkedError()
            raise NotLinkedError(not_linked_message(target.display_name))
        return steam64_id

    async def _scoreboard_deferred(
        self, interaction: discord.Interaction, steam64_id: str, game_id: str | None
    ) -> bool:
        """Fetch and post the scoreboard; True only if the embed was posted."""
        try:
            match, profile = await self._fetch_match_and_profile(steam64_id, game_id)
            identities = await self._match_identities_or_none(interaction.guild, match)
            embed = build_match_embed(
                match, compute_awards(match), steam64_id, profile, identities=identities
            )
        except Cs2Error as exc:
            await _send_safely(interaction.followup.send(exc.message))
            return False
        except Exception:
            log.exception("Unexpected error building a CS2 scoreboard")
            await _send_safely(interaction.followup.send(GENERIC_ERROR_MESSAGE))
            return False
        # Friends are shown as mentions; render them, but never ping anyone.
        return await _send_safely(
            interaction.followup.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        )

    async def _profile_deferred(self, interaction: discord.Interaction, steam64_id: str) -> bool:
        """Fetch and post the profile; True only if the embed was posted."""
        try:
            profile = await self.stats.get_profile(steam64_id)
            embed = build_profile_embed(profile)
        except Cs2Error as exc:
            await _send_safely(interaction.followup.send(exc.message))
            return False
        except Exception:
            log.exception("Unexpected error building a CS2 profile")
            await _send_safely(interaction.followup.send(GENERIC_ERROR_MESSAGE))
            return False
        return await _send_safely(interaction.followup.send(embed=embed))

    async def _leaderboard_deferred(self, interaction: discord.Interaction, metric: Metric) -> bool:
        """Fetch every linked profile and post the ranking; True only if players were shown."""
        try:
            links = await self.links.all_links()
            if not links:
                # The empty state is a valid reply, but it shouldn't cost the cooldown.
                await _send_safely(
                    interaction.followup.send(embed=build_leaderboard_embed([], metric))
                )
                return False
            fetch = await fetch_squad(self.stats, links)
            if not fetch.entries:
                message = (
                    RateLimitedError.default_message
                    if fetch.rate_limited and not fetch.skipped
                    else ALL_PROFILES_FAILED_MESSAGE
                )
                await _send_safely(interaction.followup.send(message))
                return False
            embed = build_leaderboard_embed(rank_squad(fetch.entries, metric), metric)
            notes = leaderboard_notes(fetch, len(links))
            if notes:
                footer = [embed.footer.text] if embed.footer.text else []
                embed.set_footer(text=" · ".join([*footer, *notes]))
        except Exception:
            log.exception("Unexpected error building a CS2 leaderboard")
            await _send_safely(interaction.followup.send(GENERIC_ERROR_MESSAGE))
            return False
        return await _send_safely(
            interaction.followup.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        )

    async def _fetch_match_and_profile(
        self, steam64_id: str, game_id: str | None
    ) -> tuple[Match, Profile | None]:
        """Fetch both at once; a match error wins and the other request is cancelled."""
        match_task = asyncio.create_task(self._fetch_match(steam64_id, game_id))
        profile_task = asyncio.create_task(self._fetch_profile_or_none(steam64_id))
        try:
            return await match_task, await profile_task
        finally:
            # No-op for finished tasks; gather retrieves every outcome so nothing is logged.
            match_task.cancel()
            profile_task.cancel()
            await asyncio.gather(match_task, profile_task, return_exceptions=True)

    async def _fetch_match(self, steam64_id: str, game_id: str | None) -> Match:
        if game_id is not None:
            return await self.stats.get_match(game_id)
        matches = await self.stats.get_recent_matches(steam64_id)
        if not matches:
            raise NoMatchesError()
        return await full_match(self.stats, matches[0])

    async def _match_identities(
        self, guild: discord.Guild | None, match: Match
    ) -> dict[str, Identity]:
        """Linked server members among the match players; empty outside a server."""
        if guild is None:
            return {}
        return await resolve_identities(
            match.players, self.links, member_lookup(guild, self.members)
        )

    async def _match_identities_or_none(
        self, guild: discord.Guild | None, match: Match
    ) -> dict[str, Identity] | None:
        """The linked members in the match, or None if the lookup failed.

        Names and mentions are extras, so a failure here never stops the scoreboard; None
        (lookup broke) is kept apart from `{}` (nobody in the match is a member).
        """
        try:
            return await self._match_identities(guild, match)
        except Exception:
            log.exception("Posting scoreboard without Discord names")
            return None

    async def _fetch_profile_or_none(self, steam64_id: str) -> Profile | None:
        """The profile only adds the breakdown, so its CS2 errors don't stop the scoreboard."""
        try:
            return await self.stats.get_profile(steam64_id)
        except Cs2Error as exc:
            log.info("Posting scoreboard without profile: %s", type(exc).__name__)
            return None

    async def cog_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        log.error("App command error in Cs2 cog", exc_info=error)
        message = GENERIC_ERROR_MESSAGE
        if interaction.response.is_done():
            await _send_safely(interaction.followup.send(message, ephemeral=True))
        else:
            await _send_safely(interaction.response.send_message(message, ephemeral=True))


async def setup(bot: NinjaBot) -> None:
    client = CachedStatsProvider(LeetifyClient(bot.config.leetify_api_key))
    store = SqliteLinkStore(Path(bot.config.data_dir) / "cs2.sqlite3")
    auto_post = AutoPost(
        bot,
        MatchPoller(client, store),
        bot.config.cs2_channel_id,
        bot.config.cs2_poll_seconds,
    )
    await bot.add_cog(
        Cs2(
            client,
            store,
            UserCooldown(COOLDOWN_SECONDS),
            UserCooldown(LINK_COOLDOWN_SECONDS),
            SteamCommunityResolver(),
            UserCooldown(LEADERBOARD_COOLDOWN_SECONDS),
            auto_post,
        )
    )
