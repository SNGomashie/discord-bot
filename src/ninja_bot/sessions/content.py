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
