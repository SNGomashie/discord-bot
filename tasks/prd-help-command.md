# PRD: `/help` command

## Introduction

The bot has seven slash commands across three features (media, CS2 stats, utility), but nothing inside Discord explains them. The only in-bot documentation is the one-line Discord description of each command, and the README's "Known limitations" even lists "No help command". Friends have to ask the owner how `/link` works, what `start` and `end` accept, or what a leaderboard metric means.

This PRD adds `/help`:

- `/help` shows an overview: a "getting started" block and every command grouped by feature, with its parameters and a one-line summary.
- `/help command:<name>` shows the full detail of one command: usage line, every parameter (required or optional, choices), examples and notes. The `command` option autocompletes from the commands the bot actually has registered.
- The reply is private by default; `public:true` posts it in the channel.
- All user-facing text lives in **one content module** (`src/ninja_bot/help/content.py`), so the wording and voice can be changed by editing that one file. The text starts in a plain, neutral voice.
- A test fails when a registered command, a parameter, or the README's command table drifts away from the help content.

## Goals

- A user can learn every command, its parameters and its accepted formats without leaving Discord or opening the README.
- Help can't silently go stale: adding, renaming or removing a slash command (or one of its parameters) without updating the help content, or the README commands table, breaks `./scripts/check.sh`.
- Changing the wording or voice of `/help` means editing `help/content.py` only. No logic changes, and no test changes beyond content that is genuinely new.
- `/help` is cheap and safe: no network calls, no database access, no pings, no cooldown for private replies.

## User Stories

### US-001: Help content model and text module
**Description:** As the owner, I want all help text in one module with a small typed structure so that I can restyle or reword it without touching logic.

**Acceptance Criteria:**
- [ ] New package `src/ninja_bot/help/` (with `__init__.py`) containing `content.py`; it imports nothing from `discord` and nothing from other `ninja_bot` modules
- [ ] `content.py` defines frozen dataclasses: `CommandHelp(name: str, group: str, summary: str, details: str, examples: tuple[str, ...], notes: tuple[str, ...], parameter_notes: Mapping[str, str])` (`name` has no leading slash; `parameter_notes` maps a parameter name to extra help such as accepted formats) and `HelpGroup(key: str, title: str)`
- [ ] `GROUPS` lists three groups in display order: media, CS2 stats, utility. `COMMANDS` is a tuple of `CommandHelp` with one entry for each of `ping`, `download`, `link`, `unlink`, `scoreboard`, `profile`, `leaderboard` and `help` itself
- [ ] Every entry's content is accurate to the code (read the constants, do not copy from the README or this PRD): for example `download` documents `format`, the clip syntax for `start`/`end` (`90`, `1:30`, `1h2m3s`), and that `end` needs `start`; `link` documents SteamID64, `/profiles/` link, `/id/` link and bare custom URL name; `leaderboard` lists the seven metrics; `scoreboard` documents `player` and `match`
- [ ] `content.py` also holds every other user-facing string used by the help feature, as named module constants: overview title and intro, the getting-started text (see US-003), field names (`Usage`, `Parameters`, `Examples`, `Notes`), the footer, the unknown-command reply (with a `{suggestions}` placeholder), the public-cooldown reply, and the generic error reply
- [ ] The wording is plain and neutral: short sentences, no persona, no emojis other than those the bot already uses in other embeds (none needed)
- [ ] Each summary is at most 100 characters; each `details` is at most 600 characters; each example is at most 150 characters (the tests in US-004/US-005 rely on this to stay within Discord's limits)
- [ ] Unit tests (`tests/test_help_content.py`) check structure only, never exact prose: names are unique, lower-case, without `/`; every `group` exists in `GROUPS`; the length limits above hold; every example starts with `/<name>` of its own command; `parameter_notes` keys are non-empty
- [ ] Typecheck passes
- [ ] `./scripts/check.sh` passes (ruff, ruff format, mypy strict, pytest)

### US-002: Command metadata and usage line
**Description:** As a user, I want help to show each command's real parameters so that what I read matches what Discord shows when I type the command.

**Acceptance Criteria:**
- [ ] New module `help/commands.py` (no `discord` import) defines frozen dataclasses `ParamInfo(name: str, description: str, required: bool, choices: tuple[str, ...])` and `CommandInfo(name: str, description: str, params: tuple[ParamInfo, ...])`
- [ ] `usage_line(info) -> str` returns `/name` followed by the parameters in order: required ones as `name:<name>`, optional ones as `[name:<name>]`; a parameter with choices uses the choice values joined by `|` instead (`[format:mp3|mp4]`); a command with no parameters is just `/name`
- [ ] `parameter_lines(info, notes) -> list[str]` returns one line per parameter: `name (required|optional): description`, then ` Choices: a, b, c` when it has choices, then the matching entry of `notes` (a `Mapping[str, str]`, normally `CommandHelp.parameter_notes`) when present. Every line is at most 300 characters (long notes are cut with `…`)
- [ ] Both functions are pure and deterministic
- [ ] Unit tests: no parameters, one required, mixed required and optional, a choice parameter, a note present and absent, a long note being cut, parameter order preserved
- [ ] Typecheck passes
- [ ] `./scripts/check.sh` passes

### US-003: Overview embed
**Description:** As a user, I want one screen that tells me what the bot can do and where to start.

**Acceptance Criteria:**
- [ ] New module `help/embed.py` defines `build_overview_embed(commands: Sequence[CommandInfo]) -> discord.Embed` using the content constants from US-001 for all text
- [ ] The embed has the overview title and intro, a **Getting started** field (text from `content.py`, for example: link your Steam account with `/link`, then use `/scoreboard`, `/profile` and `/leaderboard`; use `/download` for videos), and one field per group in `GROUPS` order
- [ ] Each group field lists its commands, in `COMMANDS` order, as one line each: `` `usage_line` `` followed by the command's `summary`, joined with newlines; a group with no registered command is omitted
- [ ] A command that is registered but has no `CommandHelp` entry still appears (under a trailing "Other" group, using its Discord description) rather than being hidden
- [ ] A `CommandHelp` whose command is not in the given `commands` is skipped (no stale entries)
- [ ] The footer is the content constant (a hint that `/help command:<name>` shows details)
- [ ] The embed respects Discord limits: every field value at most 1024 characters (excess lines are dropped and a `+N more` line is appended), total at most 6000 characters, at most 25 fields
- [ ] Mentions and markdown in command text can't cause trouble: no user data is included
- [ ] Unit tests with hand-built `CommandInfo` lists: all groups present and ordered, empty group omitted, unregistered command skipped, registered-but-undocumented command shown under "Other", the 1024 truncation path, total size under 6000 for the full real content with all eight commands
- [ ] Typecheck passes
- [ ] `./scripts/check.sh` passes

### US-004: Command detail embed
**Description:** As a user, I want the full explanation of one command, including accepted formats and examples.

**Acceptance Criteria:**
- [ ] `help/embed.py` defines `build_command_embed(info: CommandInfo, help_entry: CommandHelp | None) -> discord.Embed`
- [ ] Title is `/<name>`. The description is the entry's `summary` and `details` (or just the Discord description when `help_entry` is `None`)
- [ ] Fields, in order and each omitted when empty: **Usage** (the `usage_line` in a code block), **Parameters** (the `parameter_lines`, one per line), **Examples** (each example in its own line, in a code block), **Notes** (one bullet per note). Field names come from `content.py`
- [ ] Limits: every field value at most 1024 characters (parameters and notes are trimmed from the end with `+N more`), title at most 256, total at most 6000
- [ ] Unit tests: a command with parameters, choices and notes; a command with no parameters (no Parameters field); `help_entry=None`; oversized notes being trimmed; the largest real command (`download`) staying under all limits
- [ ] Typecheck passes
- [ ] `./scripts/check.sh` passes

### US-005: Name lookup and autocomplete matching
**Description:** As a user, I want `/help command:` to forgive small typing differences and suggest what I meant.

**Acceptance Criteria:**
- [ ] New module `help/lookup.py` (no `discord` import) with `normalize_name(text: str) -> str` (strip whitespace, strip one leading `/`, lower-case)
- [ ] `find_command(text, names: Sequence[str]) -> str | None` returns the matching name from `names` after normalising both sides, else `None`
- [ ] `suggest(text, names, limit=3) -> list[str]` returns up to `limit` names that start with or contain the normalised text, prefix matches first, then alphabetical; an empty text returns the first `limit` names alphabetically
- [ ] `autocomplete_names(current, names, limit=25) -> list[str]` returns names that contain the normalised `current` (prefix matches first, then alphabetical), at most `limit` (Discord's maximum is 25); empty `current` returns all names alphabetically up to the limit
- [ ] Unit tests (parametrized): `/Download`, ` download `, `DOWNLOAD`, unknown names, empty and whitespace-only input, a lone `/`, prefix before substring ordering, the limit being respected, no duplicates
- [ ] Typecheck passes
- [ ] `./scripts/check.sh` passes

### US-006: The `/help` cog
**Description:** As a user, I want to type `/help` and get my answer privately, or post it for everyone when I choose.

**Acceptance Criteria:**
- [ ] New cog `src/ninja_bot/cogs/help.py` with `async def setup(bot)`, registered in `EXTENSIONS` in `bot.py`; the cog is a thin wrapper that only extracts command metadata from the live tree and calls the pure functions from US-002 to US-005
- [ ] `/help` has description "Show what the bot can do", an optional string `command` ("A command to explain, e.g. download") with an autocomplete callback, and an optional bool `public` ("Post the help in the channel for everyone (default: only you see it)", default `False`)
- [ ] The cog builds `CommandInfo` objects from `bot.tree.get_commands()` (top-level `app_commands.Command` objects only; their `name`, `description` and `parameters` with name, description, `required`, choice values). The autocomplete returns `app_commands.Choice` objects built from `autocomplete_names` over the registered names
- [ ] `/help` with no `command` replies with the overview embed. With a `command` it replies with the detail embed, matching through `find_command`. An unknown name gets the unknown-command text (with up to 3 `suggest` names) as a private reply and does not use the cooldown
- [ ] The reply is ephemeral unless `public` is true. Replies use `allowed_mentions=discord.AllowedMentions.none()`
- [ ] A cooldown applies **only** to `public:true` requests: reuse `UserCooldown` from `media/cooldown.py` with a module constant `PUBLIC_COOLDOWN_SECONDS = 15.0`; it is taken after the command is resolved, and refunded if the send fails. When throttled the reply is private and uses the content constant (with the seconds left, rounded up like the other cogs)
- [ ] No network, database or file access happens in the command; it works in DMs and in guilds
- [ ] `cog_app_command_error` replies privately with the generic error constant and logs the exception, like the other cogs
- [ ] Unit tests (call `Help.help.callback(cog, interaction)` with `AsyncMock`/`MagicMock`, as in `tests/test_cs2_cog.py`): overview reply is ephemeral; detail reply for a known command; `/Download` style input matches; unknown command gives the suggestion reply; `public:true` is not ephemeral; the public cooldown throttles a second public call but not a private one; a failing send refunds the cooldown; autocomplete returns choices for a partial string and for empty input; the embed passed has no mentions allowed
- [ ] Typecheck passes
- [ ] `./scripts/check.sh` passes

### US-007: Drift tests
**Description:** As the owner, I want the build to fail when help or the README no longer matches the real commands.

**Acceptance Criteria:**
- [ ] `tests/test_help_drift.py` builds the real command tree by loading the real extensions from `bot.EXTENSIONS` onto a `NinjaBot` with a test `Config` (no `CS2_CHANNEL_ID`, `DATA_DIR` under `tmp_path`, no network); if loading all of them in a test is impractical, instantiate the cogs directly instead, but the set of commands must come from the real cog classes, never from a hand-written list
- [ ] Test: every registered top-level slash command has a `CommandHelp` entry in `COMMANDS`, and every `CommandHelp` names a registered command (failure messages name the offending command)
- [ ] Test: every key of each entry's `parameter_notes` is the name of a real parameter of that command
- [ ] Test: every registered command's Discord `description` and every one of its parameters' descriptions is non-empty, so nothing is undocumented in Discord's own UI either
- [ ] Test: `README.md` has a row in its "Commands at a glance" table (first column `` `/name` ``) for every registered command, and no row for a command that doesn't exist (parse the table rows with a regex; the test reads the file from the repo root)
- [ ] Test: both embeds built from the real commands and content are within Discord's limits (fields at most 1024 characters, total at most 6000, at most 25 fields)
- [ ] Tests assert structure, not prose, so rewording `content.py` never breaks them
- [ ] Typecheck passes
- [ ] `./scripts/check.sh` passes

### US-008: Documentation
**Description:** As a user and as a contributor, I want the README, manual checks and architecture notes to describe `/help`.

**Acceptance Criteria:**
- [ ] `README.md`: `/help` added to the "Commands at a glance" table (parameters `command`, `public`; reply private by default; cooldown only for public replies, 15 s); a short `/help` section (what the overview and detail views show, autocomplete, the `public` flag, that help text lives in `src/ninja_bot/help/content.py`); the contents list and the repo layout block updated; the "No help command" bullet under Known limitations removed
- [ ] `README.md` development section: a note that adding a command requires a `CommandHelp` entry in `help/content.py` and a README table row, and that `tests/test_help_drift.py` enforces both
- [ ] `docs/manual-checks.md`: a new `/help` section (see Manual Checks below)
- [ ] `CLAUDE.md`: `help/` (content, commands, embed, lookup) and `cogs/help.py` added to the Architecture list; the Documentation table gets a row "A new/changed/removed slash command -> also `help/content.py`"; the line "all `/help` text lives in `help/content.py`" is recorded as a convention
- [ ] No persona or voice rules are added to `CLAUDE.md`
- [ ] Every relative link and anchor in the edited docs still resolves
- [ ] `./scripts/check.sh` passes

## Functional Requirements

- FR-1: The bot registers a global slash command `/help` with optional parameters `command` (string, autocomplete) and `public` (bool, default false).
- FR-2: `/help` without `command` replies with an overview embed: intro, getting started, and each registered command under its group with its usage line and one-line summary.
- FR-3: `/help command:<name>` replies with a detail embed: usage line, per-parameter lines (required or optional, choices, accepted-format notes), examples and notes.
- FR-4: Parameters, required flags, choices and descriptions shown by `/help` come from the live registered commands, not from hand-written copies.
- FR-5: Explanations, examples, notes, getting-started text, group titles and all other help wording come from `help/content.py` only.
- FR-6: Command names are matched case-insensitively and with an optional leading `/`; an unknown name gets a private reply with up to three suggestions.
- FR-7: The reply is ephemeral unless `public:true`. No reply mentions or pings anyone.
- FR-8: Public replies are rate limited per user (15 s, refunded if the send fails); private replies are not.
- FR-9: A command that is registered but missing from the help content still appears in the overview (using its Discord description) and the detail view; a help entry for a command that no longer exists is not shown.
- FR-10: Embeds stay within Discord limits (field value 1024, total 6000, 25 fields) for the real content.
- FR-11: `./scripts/check.sh` fails if a registered command lacks a help entry, a help entry or parameter note names something that doesn't exist, or the README commands table is out of step with the registered commands.
- FR-12: The help feature makes no network, database or file access at command time.

## Non-Goals

- No persona, joke voice or restyle of existing bot messages. Existing replies (errors, cooldowns, embeds) stay as they are; a different voice for `/help` is a later edit of `help/content.py` only.
- No topic pages (for example "clips" or "auto-posts") beyond the getting-started block and per-command notes.
- No cooldown, limit, upload-size or setting values in the help text (they live in the README); nothing links to the README from the embeds.
- No explanation of the auto-posted match feature in `/help`.
- No sub-command groups, buttons, select menus or pagination. One embed per reply.
- No per-guild or per-user customisation, no localisation.
- No `/help` for admin or deployment topics (that stays in `docs/deployment.md`).
- No change to `bot.py` other than adding the extension to `EXTENSIONS`.

## Design Considerations

- Keep it short enough to read on a phone: an overview line per command is `` `/usage` summary ``.
- Overview layout: title, one-line intro, **Getting started**, then one field per group (Media, CS2 stats, Utility). Detail layout: title `/name`, description, **Usage**, **Parameters**, **Examples**, **Notes**.
- Putting every user-facing string in `content.py` (including field names and error replies) is what keeps a future voice change a one-file edit. Tests must therefore import those constants rather than hard-code the text.
- Follow the existing embed code in `media/embed.py` and `cs2/embed.py` for limits handling and truncation style.

## Technical Considerations

- Follow the repo convention: thin cog, pure logic in the `help/` package, `from __future__ import annotations`, mypy strict. The new modules (`content`, `commands`, `lookup`) must not import `discord`; only `embed.py` and the cog do.
- `help/` is a new top-level package; naming it `help` only shadows the builtin inside the package namespace and is fine, but never write `from ninja_bot.help import *`.
- `commands.Bot` already has a prefix `help` command. The bot uses `commands.when_mentioned` and doesn't read messages, so it's unreachable; leave it alone.
- Read live metadata from `bot.tree.get_commands()`. Because `bot.py` uses `copy_global_to(guild)` in dev, the global tree still holds every command; nothing in `bot.py` changes.
- Autocomplete must respond within Discord's 3-second limit; it only filters an in-memory list, so no `defer` is needed. Return at most 25 choices.
- The command is guild-independent (works in DMs), unlike the CS2 member lookups.
- Reuse: `UserCooldown` (`media/cooldown.py`), the cooldown-message pattern from `cogs/media.py`, and the cog test style from `tests/test_cs2_cog.py`.
- Run `./scripts/check.sh` after every story; keep each story to one commit (`feat:` / `test:` / `docs:`), story ID in the body.

## Success Metrics

- Every one of the eight registered commands is explainable with `/help` alone: usage, parameters, formats and an example.
- Adding a ninth command without a help entry or README row fails `./scripts/check.sh`.
- Rewording `help/content.py` (including a full voice change) needs no change outside that file and passes `./scripts/check.sh` unchanged.
- Both embeds stay under Discord's limits with the real content (asserted by tests).

## Manual Checks (Discord only, not gates)

These go into `docs/manual-checks.md` in US-008:

- [ ] `/help` shows the overview privately: getting started plus Media, CS2 stats and Utility groups, and a footer hint
- [ ] Typing `/help command:` offers all commands, narrows as you type (`do` -> `download`)
- [ ] `/help command:download` shows usage `/download url:<url> [format:mp3|mp4] [start:<start>] [end:<end>]`, parameter notes for clips and examples
- [ ] `/help command:/Link` (capital, leading slash) works the same as `link`
- [ ] `/help command:nonsense` gives a private reply with suggestions
- [ ] `/help public:true` posts in the channel; a second public call within 15 s is throttled privately, a private call is not
- [ ] The embeds render cleanly on the phone app (no cut-off code blocks)
- [ ] `/help` works in a DM with the bot (global sync) and in the test server

## Open Questions

- Should the overview also show the usage line for every command, or only the command name and summary, if it feels crowded on mobile? (This PRD shows usage lines; trim in `content.py`/`embed.py` if too long.)
- Should the cooldown numbers and limits (30 s `/download`, 25 players in `/leaderboard`, and so on) appear in the detail view later? They were left out because they would duplicate constants; if added, derive them from the real constants.
- If more commands are added later, does the overview need pagination (25 fields, 6000 characters)? Not needed with eight commands.
- Do you want to restyle the help voice afterwards? Only `help/content.py` needs to change; a tone preference can be recorded in `CLAUDE.md` once you've settled on it.
