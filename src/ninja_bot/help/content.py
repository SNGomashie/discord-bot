"""All user-facing /help text. Reword or restyle /help by editing this file only.

Commands, parameters and choices are read from the live command tree; this module
only adds explanations, examples and notes. It imports nothing from discord or ninja_bot.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field


@dataclass(frozen=True)
class HelpGroup:
    key: str
    title: str


@dataclass(frozen=True)
class CommandHelp:
    name: str  # Without the leading `/`.
    group: str  # A `HelpGroup.key`.
    summary: str  # One line, at most 100 characters.
    details: str  # At most 600 characters.
    examples: tuple[str, ...] = ()  # Each starts with `/<name>`, at most 150 characters.
    notes: tuple[str, ...] = ()
    # Parameter name -> extra help, e.g. accepted formats.
    parameter_notes: Mapping[str, str] = field(default_factory=dict)


MEDIA = "media"
CS2 = "cs2"
ACTIVITY = "activity"
SESSIONS = "sessions"
UTILITY = "utility"

GROUPS: tuple[HelpGroup, ...] = (
    HelpGroup(MEDIA, "Media"),
    HelpGroup(CS2, "CS2 stats"),
    HelpGroup(ACTIVITY, "Voice activity"),
    HelpGroup(SESSIONS, "Session polls"),
    HelpGroup(UTILITY, "Utility"),
)

# Title of the trailing group for registered commands without a CommandHelp entry.
OTHER_GROUP_TITLE = "Other"

COMMANDS: tuple[CommandHelp, ...] = (
    CommandHelp(
        name="download",
        group=MEDIA,
        summary="Download a video from a link as an mp4 video or mp3 audio file.",
        details=(
            "Give a link to a video and the bot posts the file in the channel. "
            "The default format is mp4. If the file would be too big to upload, "
            "the bot picks a lower quality or tells you it can't fit. "
            "Set start and end to download only part of the video. "
            "One download runs at a time; other requests wait in a queue."
        ),
        examples=(
            "/download url:https://youtu.be/dQw4w9WgXcQ",
            "/download url:https://youtu.be/dQw4w9WgXcQ format:mp3",
            "/download url:https://youtu.be/dQw4w9WgXcQ start:1:30 end:2:45",
        ),
        notes=(
            "Without end, the clip runs to the end of the video.",
            "A clip must be at least 1 second long.",
            "You only use up your cooldown when a file is actually posted.",
        ),
        parameter_notes={
            "url": "Must be an http or https link.",
            "start": (
                "Seconds (90 or 90.5), m:ss or h:mm:ss (1:30, 1:02:03), or units (1h2m3s, 1m30s)."
            ),
            "end": "Same formats as start. Needs start, and must be after it.",
        },
    ),
    CommandHelp(
        name="link",
        group=CS2,
        summary="Link your Steam account so the CS2 stats commands know who you are.",
        details=(
            "Links your Discord account to a Steam account on Leetify. "
            "Your stats come from Leetify, so the Steam account needs a public Leetify profile. "
            "Run it again with another account to change your link. "
            "The reply is only visible to you."
        ),
        examples=(
            "/link steam:76561198000000000",
            "/link steam:https://steamcommunity.com/id/yourname",
            "/link steam:yourname",
        ),
        notes=(
            "When you give a custom URL name, the reply also shows the Steam ID it found.",
            "Your link is also used to show your name in auto-posted matches.",
        ),
        parameter_notes={
            "steam": (
                "A 17-digit SteamID64, a steamcommunity.com/profiles/<id> link, "
                "a steamcommunity.com/id/<name> link, or just the custom URL name."
            ),
        },
    ),
    CommandHelp(
        name="unlink",
        group=CS2,
        summary="Remove your linked Steam account.",
        details=(
            "Removes the link between your Discord account and your Steam account. "
            "You can link again any time with /link. The reply is only visible to you."
        ),
        examples=("/unlink",),
    ),
    CommandHelp(
        name="scoreboard",
        group=CS2,
        summary="Show the scoreboard of a CS2 match, your latest one by default.",
        details=(
            "Shows both teams with each player's stats, a few fun awards and the "
            "player's own performance. Pick another member with player, or an older "
            "match with its Leetify match id. The player must have linked their account."
        ),
        examples=(
            "/scoreboard",
            "/scoreboard player:@friend",
        ),
        notes=("Linked members are shown with their server names in the tables.",),
        parameter_notes={
            "player": "A server member who used /link. Leave it empty for yourself.",
            "match": "A Leetify match id. Leave it empty for the latest match.",
        },
    ),
    CommandHelp(
        name="profile",
        group=CS2,
        summary="Show a player's Leetify profile: ranks, winrate, ratings and recent form.",
        details=(
            "Shows the Leetify profile of you or another member: ranks, winrate, "
            "number of matches, skill ratings and the results of recent matches. "
            "The player must have linked their account with /link."
        ),
        examples=(
            "/profile",
            "/profile player:@friend",
        ),
        parameter_notes={
            "player": "A server member who used /link. Leave it empty for yourself.",
        },
    ),
    CommandHelp(
        name="leaderboard",
        group=CS2,
        summary="Rank everyone who linked a Steam account by a CS2 stat.",
        details=(
            "Ranks every member who used /link by one metric: Leetify rating, aim, "
            "positioning, utility, clutch, opening duels or winrate. "
            "Each row also shows whether the player is trending up or down. "
            "Players whose stats can't be loaded are listed at the bottom."
        ),
        examples=(
            "/leaderboard",
            "/leaderboard metric:Aim",
        ),
        notes=("Players with the same value share a rank.",),
        parameter_notes={
            "metric": "Leave it empty to rank by Leetify rating.",
        },
    ),
    CommandHelp(
        name="activity",
        group=ACTIVITY,
        summary="Show who spent the most time in voice this month, plus awards and shame.",
        details=(
            "Shows the server's voice leaderboard for a month: total time and active days "
            "per member, the wall of shame (least time), fun awards and who hung out "
            "together most. Time in the AFK channel doesn't count. "
            "If Toasty tracking is on, it also shows the top Pokémon catchers, shinies, "
            "the most caught Pokémon and the Impatient award (most ;pokemon cooldown hits). "
            "Days and hours use the server's activity timezone."
        ),
        examples=(
            "/activity",
            "/activity month:2026-09",
        ),
        notes=("Members with the same time share a rank.",),
        parameter_notes={
            "month": "A month like 2026-09 (YYYY-MM). Leave it empty for this month.",
        },
    ),
    CommandHelp(
        name="myactivity",
        group=ACTIVITY,
        summary="Show your (or a member's) voice time, streaks and awards for a month.",
        details=(
            "Shows one member's month in voice: total time and rank, active days, "
            "current and longest streak, sessions, a day-by-day strip and any awards won. "
            "If Toasty tracking is on, it also shows their Pokémon catches, shinies and "
            ";pokemon cooldown hits. "
            "Pick another member with user."
        ),
        examples=(
            "/myactivity",
            "/myactivity user:@friend month:2026-09",
        ),
        notes=("If the member wasn't in voice that month, only you see the reply.",),
        parameter_notes={
            "user": "A server member. Leave it empty for yourself.",
            "month": "A month like 2026-09 (YYYY-MM). Leave it empty for this month.",
        },
    ),
    CommandHelp(
        name="session_poll",
        group=SESSIONS,
        summary="Start a poll to find a date for a game session.",
        details=(
            "Posts a poll in this channel with one slot per chosen day and start time "
            "in the coming weeks. You run it as the GM and are joined automatically. "
            "Players press Join to take part and Leave to drop out (their votes go with them). "
            "Vote opens a private form where you pick the slots that work for you "
            "and the ones that only work if needed, then Save. "
            "Once everyone voted (or the GM presses Close round) the round closes: "
            "if a date fits, the GM picks it with Confirm and the players are pinged; "
            "otherwise the next weeks get a new round, up to 6 rounds. "
            "Slot times show in everyone's own time zone."
        ),
        examples=(
            "/session_poll name:Curse of Strahd days:fri,sat times:19:00",
            "/session_poll name:One-shot days:weekend times:14:00,19:30 weeks:3 min_players:4",
            "/session_poll name:Campaign days:sat times:13:00 start:2026-11-07 round_hours:48",
        ),
        notes=(
            "A poll offers at most 25 slots; use fewer days, times or weeks if it says more.",
            "A server can have at most 5 open polls at a time.",
            "Only the GM or someone with Manage Events can close a round or confirm a date.",
            "Errors are only visible to you, and they don't use up your cooldown.",
        ),
        parameter_notes={
            "days": (
                "Comma-separated weekdays: short (fri) or full (friday) names, "
                "or weekend / weekdays."
            ),
            "times": "Comma-separated 24-hour start times like 19:00 or 9:30.",
            "start": "A date like 2026-11-06 (YYYY-MM-DD), not in the past. Default: tomorrow.",
            "min_players": "Without it, a date only fits when every joined player can make it.",
            "round_hours": "Hours each voting round stays open. Leave it empty for no deadline.",
        },
    ),
    CommandHelp(
        name="ping",
        group=UTILITY,
        summary="Check that the bot is online and see its latency.",
        details="Replies with the bot's current latency to Discord in milliseconds.",
        examples=("/ping",),
    ),
    CommandHelp(
        name="help",
        group=UTILITY,
        summary="Show what the bot can do, or explain one command.",
        details=(
            "Without command, shows every command with a short summary. "
            "With command, shows how to use that command: its parameters, examples and notes. "
            "The reply is only visible to you unless you set public to true."
        ),
        examples=(
            "/help",
            "/help command:download",
            "/help command:link public:true",
        ),
        parameter_notes={
            "command": "A command name, with or without the leading /.",
            "public": "Posting help in the channel has a short cooldown.",
        },
    ),
)

OVERVIEW_TITLE = "Bot commands"
OVERVIEW_INTRO = "Everything this bot can do. All commands are slash commands."
GETTING_STARTED_TITLE = "Getting started"
GETTING_STARTED = (
    "Link your Steam account with /link, then use /scoreboard, /profile and /leaderboard "
    "for CS2 stats. Use /activity and /myactivity to see who spends the most time in voice. "
    "Use /session_poll to find a date for a game session. "
    "Use /download to get a video or its audio as a file."
)

USAGE_FIELD = "Usage"
PARAMETERS_FIELD = "Parameters"
EXAMPLES_FIELD = "Examples"
NOTES_FIELD = "Notes"

FOOTER = "Use /help command:<name> for details on one command."

# `{suggestions}` is replaced with a list of command names (or NO_SUGGESTIONS).
UNKNOWN_COMMAND_MESSAGE = "There is no command with that name. {suggestions}"
SUGGESTIONS_TEXT = "Did you mean: {names}?"
NO_SUGGESTIONS = "Use /help to see every command."

# `{seconds}` is the wait rounded up to whole seconds; `{unit}` is "second" or "seconds".
PUBLIC_COOLDOWN_MESSAGE = (
    "You can post help in the channel again in {seconds} {unit}. "
    "Leave out public to see it privately right away."
)

GENERIC_ERROR_MESSAGE = "Something went wrong while showing help. Please try again later."
