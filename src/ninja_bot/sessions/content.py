"""User-facing session poll strings (no discord or ninja_bot imports)."""

from __future__ import annotations

# /session_poll validation.
BLANK_NAME_MESSAGE = "Give the poll a name."
NAME_TOO_LONG_MESSAGE = "The poll name can be at most {max} characters."
INVALID_WEEKS_MESSAGE = "`weeks` must be between 1 and {max}."
INVALID_DURATION_MESSAGE = "`duration_hours` must be between 1 and {max}."
INVALID_MIN_PLAYERS_MESSAGE = "`min_players` must be between 2 and {max}."
INVALID_ROUND_HOURS_MESSAGE = "`round_hours` must be between 1 and {max}."
TOO_MANY_OPEN_POLLS_MESSAGE = (
    "This server already has {max} open session polls; finish or cancel one first."
)

# Join / Leave.
JOINED_MESSAGE = "You joined the poll. Vote on the slots that work for you."
ALREADY_JOINED_MESSAGE = "You already joined this poll."
POLL_FULL_MESSAGE = "This poll is full ({max} players)."
LEFT_MESSAGE = "You left the poll; your votes were removed."
NOT_JOINED_MESSAGE = "You haven't joined this poll. Press **Join** first."
POLL_CLOSED_MESSAGE = "This poll isn't taking votes any more."
GM_CANNOT_LEAVE_MESSAGE = "You run this poll, so you can't leave it. Cancel it instead."

# Votes.
VOTE_RECORDED_MESSAGE = "Your votes are saved."
UNKNOWN_SLOT_MESSAGE = "Those slots aren't part of the current round; open the vote again."

# Close round / confirm / cancel.
NOT_ALLOWED_MESSAGE = "Only the GM or someone with **Manage Events** can do that."
ROUND_ALREADY_CLOSED_MESSAGE = "This round is already closed."
NOT_AWAITING_CONFIRM_MESSAGE = "This poll isn't waiting for a date to be confirmed."
SLOT_NOT_OFFERED_MESSAGE = "That slot isn't one of the offered dates."
UNKNOWN_POLL_MESSAGE = "There's no open poll with that id; it may be finished or cancelled."
UNKNOWN_SESSION_MESSAGE = (
    "There's no upcoming session with that id; it may have started or been cancelled."
)

# Poll message.
ROUND_LABEL = "Round {round} of {max}"
GM_LABEL = "GM {gm}"
DURATION_LABEL = "{hours} h sessions"
MIN_PLAYERS_LABEL = "at least {count} players"
DESCRIPTION_SEPARATOR = " · "
SLOT_LINE = "`{index}` {start} · ✅ {yes} · 🤔 {if_needed}"
DEADLINE_LINE = "Round closes {deadline}"
JOINED_FIELD = "Joined"
JOINED_CONTINUED_FIELD = "Joined (cont.)"
WAITING_FIELD = "Waiting for"
MORE_LINE = "+{count} more"
POLL_FOOTER = (
    "Press Join, then Vote. ✅ works · 🤔 only if needed. Times show in your own time zone."
)

# Round results.
RESULT_DESCRIPTION = "A date fits! {gm}, pick one with **Confirm**."
RESULT_SLOT_LINE = "**{rank}.** {start} · ✅ {yes} · 🤔 {if_needed}"
CANT_MAKE_IT_LINE = "Can't make it: {players}"
EVERYONE_CAN_LINE = "Everyone can make it."
NEXT_ROUND_DESCRIPTION = (
    "No date fit everyone, so the poll moves on to **round {round} of {max}** with"
    " {slots} new slots from {first} to {last}."
)
NEXT_ROUND_PENDING_FIELD = "Still need a vote from"
NO_FIT_DESCRIPTION = "No date fit after {rounds} rounds."
NO_SLOTS_DESCRIPTION = "No date fit, and the next window has no slots left to try."
NO_FIT_ADVICE = "Try a new poll with fewer days, more weeks, or a lower `min_players`."

# Confirmed session.
SESSION_TITLE = "📅 {name}"
SESSION_WHEN = "{start} ({relative})"
SESSION_GM_FIELD = "GM"
SESSION_LENGTH_FIELD = "Length"
LENGTH_LABEL = "{hours} h"
SESSION_ROSTER_FIELD = "Players"
SESSION_ROSTER_CONTINUED_FIELD = "Players (cont.)"

# /sessions list.
LIST_TITLE = "Session polls and sessions"
LIST_POLLS_FIELD = "Open polls"
LIST_SESSIONS_FIELD = "Upcoming sessions"
LIST_EMPTY_MESSAGE = "No open polls or upcoming sessions. Start one with `/session_poll`."
LIST_POLL_LINE = "{name} · {round}"
LIST_POLL_WAITING = "waiting for {players}"
LIST_POLL_ALL_VOTED = "everyone voted"
LIST_POLL_AWAITING_CONFIRM = "waiting for the GM to confirm"
LIST_SESSION_LINE = "**{name}** · {start} ({relative}) · GM {gm}"

# /session_cancel. Choice labels: `{date}` is the poll's first day, `{start}` the session's
# start, both in SESSIONS_TIMEZONE. `{name}` in the confirmations is already escaped.
CANCEL_POLL_CHOICE = "Poll: {name} (from {date})"
CANCEL_SESSION_CHOICE = "Session: {name} ({start})"
UNKNOWN_CANCEL_ITEM_MESSAGE = (
    "Pick an open poll or upcoming session from the list; it may be finished or cancelled."
)
POLL_CANCELLED_MESSAGE = "🚫 The session poll **{name}** was cancelled by {user}."
SESSION_CANCELLED_MESSAGE = "🚫 The session **{name}** on {start} was cancelled by {user}."
CANCELLED_POLL_EDIT = "🚫 **Cancelled.** This poll no longer takes votes."
CANCELLED_SESSION_EDIT = "🚫 **Cancelled.** This session won't take place."

# /sessions. `{seconds}` / `{unit}` as in POLL_COOLDOWN_MESSAGE.
LIST_COOLDOWN_MESSAGE = "Slow down! You can list sessions again in {seconds} {unit}."

# Poll buttons.
JOIN_BUTTON = "Join"
LEAVE_BUTTON = "Leave"
VOTE_BUTTON = "Vote"
CLOSE_ROUND_BUTTON = "Close round"
# `{rank}` is the slot's place in the result, `{label}` its `format_slot_label`.
CONFIRM_BUTTON = "Confirm {rank}: {label}"

# Round close and confirm (cog). Mentions in these only ping who `allowed_mentions` names.
ROUND_CLOSED_MESSAGE = "**Round {round} is closed.** The buttons below no longer work."
RESULT_PING = "{gm}, pick the date with a **Confirm** button."
NEXT_ROUND_PING = "{players}: a new round is open, please vote."
CONFIRMED_RESULT_MESSAGE = "✅ **Confirmed:** {start}."
SESSION_ANNOUNCEMENT = "{players}: the session is on!"

# Private vote view. `{tz}` is the SESSIONS_TIMEZONE name the option labels are in.
VOTE_PROMPT = (
    "Pick the slots that work for you, then press **Save**. Times are in {tz}."
    " A slot in neither list counts as a no."
)
VOTE_YES_PLACEHOLDER = "✅ Works for me"
VOTE_MAYBE_PLACEHOLDER = "🤔 Only if needed"
VOTE_SAVE_BUTTON = "Save"
VOTE_NONE_BUTTON = "None of these work"

# /session_poll and button replies (cog).
GUILD_ONLY_MESSAGE = "Session polls only work in a server channel."
CHANNEL_PROBLEM_MESSAGE = (
    "I can't post a poll here: I need View Channel, Send Messages and Embed Links in this channel."
)
# `{seconds}` is the wait rounded up to whole seconds; `{unit}` is "second" or "seconds".
POLL_COOLDOWN_MESSAGE = "Slow down! You can start another session poll in {seconds} {unit}."
STALE_BUTTON_MESSAGE = "This button doesn't work any more."
GENERIC_ERROR_MESSAGE = "Something went wrong with the session poll. Please try again later."

# Background reminders and nudges (cog). `{players}` are mentions of who gets pinged.
REMINDER_MESSAGE = "⏰ {players} **{name}** starts {start}!"
NUDGE_MESSAGE = "👋 {players} please vote in **{name}** (round {round})."
