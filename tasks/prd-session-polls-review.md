# PRD: Session polls review follow-ups

## Introduction

`/code-review` (high) of the `ralph/session-polls` branch (see `tasks/prd-session-polls.md`) found nine correctness problems and one efficiency issue in the session poll feature. `./scripts/check.sh` passes (2125 tests); these are gaps the tests don't cover. Each finding was checked against the code before writing this PRD:

- `scoring.all_voted` is true when only the GM has joined, so the GM's own vote closes the round before anyone else could join (`sessions/scoring.py`, `should_auto_close` in `sessions/service.py`).
- `join`, `leave` and `submit_vote` are not serialised with `close_round` / `confirm`, so a vote can land after a round was evaluated, and a double click on Join is told the poll is full (`sessions/service.py`).
- `close_round` with reason `all_voted` / `deadline` trusts a decision made from an earlier, unlocked read.
- The 24 h reminder is posted immediately for a session confirmed less than 24 h before its start (`sessions/reminders.py`).
- `Save` with nothing selected records an all-No vote, the same as "None of these work" (`sessions/views.py`).
- `refresh_poll_message` can put the live buttons back on a message that a close just stripped (`cogs/sessions.py`).
- If the result or next-round message cannot be sent after a round was closed, the poll is stuck: no Confirm buttons, or no live round message (`cogs/sessions.py`, `post_outcome`).
- Cancelling a poll that awaits confirmation leaves the Confirm buttons on the result message (`handle_session_cancel`).
- The 60 s notify loop reads the same round state several times per poll, one SQLite connection per read.

This PRD fixes them on the same branch (`ralph/session-polls`), so the feature ships fixed. `sessions.sqlite3` has not been released yet, so schema changes here need no migration. Each story is one finding (or two closely tied ones). Where a finding changes what a user sees, the same story updates the docs.

## Goals

- A round never closes before there is someone to wait for, and never on a decision made from stale data.
- Votes, joins and leaves can't slip in around a round close or a confirm; the offered result and the roster always match.
- A double click never produces a wrong error.
- A session confirmed late doesn't get an instant "24 hours before" ping.
- An empty form can't count as a vote.
- A poll can't be left stuck, with no usable message, because one Discord send failed; and a cancelled poll leaves no live buttons behind.
- The notify loop reads each poll's state once per cycle.

## User Stories

### US-001: A lone GM doesn't close the round
**Description:** As a GM, I want the poll to wait for other players after I vote, so that my own vote doesn't close or burn a round.

**Acceptance Criteria:**
- [ ] `scoring.all_voted` returns False unless at least `MIN_PLAYERS_TO_AUTO_CLOSE` (new constant, 2) players have joined and none is pending; the docstring says why
- [ ] `should_auto_close` therefore returns `None` (or `"deadline"` when due) for a poll with only the GM joined; a manual close by the GM and a deadline close still work and evaluate normally
- [ ] Tests: GM alone has voted gives no `all_voted`; GM plus one player both voted gives `all_voted`; GM alone with an elapsed deadline gives `"deadline"`; a manual close with only the GM still evaluates; all existing scoring tests still pass
- [ ] Docs: `README.md` Session polls section says a round closes by itself only once at least two players (the GM and one more) have all voted
- [ ] Typecheck passes
- [ ] `./scripts/check.sh` passes

### US-002: Serialise joins, leaves and votes with close and confirm
**Description:** As a player, I want my vote or join to be either counted in the round or refused, never recorded after the round was evaluated.

**Acceptance Criteria:**
- [ ] In `SessionService`, `join` (including its prefill), `leave` and `submit_vote` run under the same `self._lock` as `close_round`, `confirm` and `cancel_poll`, and read the poll status inside the lock; the lock is never taken twice in one call path (no deadlock: helpers called under the lock don't acquire it)
- [ ] A vote or join arriving after the round was evaluated returns `POLL_CLOSED` and writes nothing
- [ ] `join` no longer mistakes a lost race for a full poll: because the call now runs under the lock, a `False` from `store.join` can only mean the poll is full; add a short comment saying so and a test with two concurrent `asyncio.gather` joins by the same user (one `JOINED`, one `ALREADY_JOINED`, never `FULL` on a poll with space)
- [ ] Test: a vote started concurrently with `close_round` (use a store whose `record_votes` awaits an `asyncio.Event`) is either counted in the evaluated round or returns `POLL_CLOSED`, and `confirm` afterwards offers exactly the slots the result showed
- [ ] Typecheck passes
- [ ] `./scripts/check.sh` passes

### US-003: Re-check the auto-close reason under the lock
**Description:** As a GM, I want a round to close on "everyone voted" or "deadline" only if that is still true when it closes.

**Acceptance Criteria:**
- [ ] `close_round` with reason `"all_voted"` or `"deadline"` recomputes `should_auto_close(poll, players, slot_ids, votes, now)` inside the lock; when it returns `None` the round stays open and `close_round` returns a new frozen `NotReady(poll)` outcome (added to the `RoundOutcome` union)
- [ ] `"manual"` is unchanged (permission check, no readiness check)
- [ ] The cog (`close_and_post`, `advance_if_ready`, `_close_due_rounds`) treats `NotReady` like `NotVoting`: nothing is posted, nothing raises
- [ ] Tests: a player joins between the unlocked read and the locked close (simulate by joining inside a fake store hook) so `all_voted` is no longer true and the round stays open; a `"deadline"` close attempted before the deadline (fake clock) stays open; `NotReady` posts nothing in the cog; a still-true reason closes as before
- [ ] Typecheck passes
- [ ] `./scripts/check.sh` passes

### US-004: No instant reminder for a late-confirmed session
**Description:** As a player, I want reminders only for the moments that are still ahead when the session is confirmed.

**Acceptance Criteria:**
- [ ] `SessionStore.confirm` takes `now` and, in the same transaction that inserts the session, marks every `REMINDER_OFFSETS` offset with `start - offset <= now` as already handled (rows in `session_reminders`), without posting; `SessionService.confirm` passes `self.clock()`
- [ ] A session confirmed 5 h before its start: the 24 h reminder is never posted, the 1 h reminder is; confirmed 30 min before: neither is posted; confirmed 3 days ahead: both are
- [ ] The `reminders.py` logic for downtime catch-up is unchanged (the closest missed reminder is still sent late)
- [ ] Tests on the real `SqliteSessionStore` (`tmp_path`) and through `SessionNotifier.due_reminders` with a fake clock for the three cases above; the store Protocol, fakes in the tests and docstrings updated
- [ ] Docs: `README.md` reminders bullet says reminders whose time has already passed when the session is confirmed are skipped (the announcement covers them); `docs/manual-checks.md` reminder check adjusted
- [ ] Typecheck passes
- [ ] `./scripts/check.sh` passes

### US-005: Refuse an empty Save
**Description:** As a player, I don't want to be counted as having voted "no to everything" because I pressed Save too early.

**Acceptance Criteria:**
- [ ] In `sessions/views.py`, **Save** with both selections empty does not stop the view and does not call `on_save`; it replies ephemerally with a new content constant (e.g. `VOTE_EMPTY_MESSAGE`: pick at least one slot, or press **None of these work**) and the form stays usable
- [ ] **None of these work** still records an all-No vote and **Save** with at least one selection still works; a user whose stored votes pre-filled the selects can Save unchanged
- [ ] Tests: empty Save refused and view still open, non-empty Save, none-of-these, pre-filled Save, constant lives in `sessions/content.py`
- [ ] Docs: `README.md` Vote button bullet mentions the prompt for an empty Save; `docs/manual-checks.md` vote-view check added
- [ ] Typecheck passes
- [ ] `./scripts/check.sh` passes

### US-006: A refresh never restores buttons or touches closed polls
**Description:** As a player, I don't want a closed round's message to show live buttons again.

**Acceptance Criteria:**
- [ ] `refresh_poll_message` returns without editing when the poll's status is not `voting`
- [ ] The refresh edit sends only the new embed (and `allowed_mentions`), never `view=`: the poll buttons are static, so omitting the view leaves them as Discord has them, and a late refresh can't bring back buttons that a close removed
- [ ] The vote-save path and any other caller keep working unchanged
- [ ] Tests: refresh on a voting poll edits with an embed and no `view` kwarg; on `awaiting_confirm`, `confirmed`, `failed` and `cancelled` polls it edits nothing; a refresh racing a close (close edit then refresh) leaves `view=None` as the last view state seen by the fake message
- [ ] Typecheck passes
- [ ] `./scripts/check.sh` passes

### US-007: Store the result message and detect unposted messages
**Description:** As a developer, I need the store to know which poll messages were really posted so that a failed send can be repaired and a cancel can find every message.

**Acceptance Criteria:**
- [ ] `polls` gets `result_message_id INTEGER NULL` (the result message with the Confirm buttons); `Poll` gets `result_message_id: int | None`; `_POLL_COLUMNS`, the row mapper and the schema are updated (no migration: the table has never shipped); `set_result_message(poll_id, message_id)` is added to the Protocol and `SqliteSessionStore`
- [ ] `advance_round` now **replaces** `message_id` with its argument (default `None`) instead of keeping the old one, so `message_id IS NULL` on a voting poll in round 2 or later means "the round message was never posted"; `result_message_id` is reset to NULL when a poll enters `awaiting_confirm`
- [ ] `polls_missing_messages() -> list[Poll]` returns voting polls with `round_number > 1` and `message_id IS NULL`, and `awaiting_confirm` polls with `result_message_id IS NULL`, oldest first (round 1 is excluded: a failed first post is already discarded by `_discard`)
- [ ] `confirm`'s session keeps `message_id` as the poll's voting message as before
- [ ] Tests on the real store: set/get of the result message, `advance_round` clears `message_id` and a later `set_poll_message` sets it, the new query returns exactly the two cases and not round-1 polls or polls whose messages are set, restart persistence; fakes and the Protocol docs updated
- [ ] Docs: `README.md` Data and storage `polls` row lists `result_message_id`
- [ ] Typecheck passes
- [ ] `./scripts/check.sh` passes

### US-008: Repair a failed outcome post and cancel result messages
**Description:** As a GM, I want a poll to recover when Discord rejected the result or next-round message, and I want a cancel to remove every live button.

**Acceptance Criteria:**
- [ ] `SessionService.rebuild_outcome(poll_id) -> RoundFits | RoundAdvanced | None` rebuilds, without changing any state, the outcome for a poll in `awaiting_confirm` (the fitting top slots, as `confirm` evaluates them) or a voting round-2+ poll (current slots and pending players); `None` for anything else
- [ ] The cog holds one `asyncio.Lock` (`_outcome_lock`) around `close_and_post`, `handle_close`'s close-and-post, and the repair, so a repair can't run between a close and its post
- [ ] `_post_result` stores the sent message's id with `set_result_message`; `_post_next_round` already stores the new `message_id`
- [ ] `repair_missing_messages()` runs at the start of each notify cycle: for each poll from `polls_missing_messages()`, under the lock, re-read the poll, skip it if the message now exists or the channel is unusable (`_channel`), otherwise rebuild the outcome and post it exactly like the first time, also marking the previous poll message closed when it has an id; errors are logged per poll and never raise
- [ ] Nudges are skipped for a voting poll without `message_id`
- [ ] `handle_session_cancel` for a poll also edits `result_message_id` (when set) with the cancelled text and `view=None`, in addition to `message_id`
- [ ] Tests: a failing channel on the first `_post_result` leaves the poll `awaiting_confirm` without a result id, and the next cycle (channel working) posts the result once and stores its id; same for a failed next-round post; no double post when the first send succeeded; repair skipped when the channel is unusable; the lock serialises a close and a repair; cancel of an `awaiting_confirm` poll edits both messages; no nudge without a poll message
- [ ] Docs: `README.md` Session polls section ("Channel gone" / failure handling) says a result or next-round message that could not be sent is retried every 60 s; `docs/manual-checks.md` gets a check (revoke Send Messages, close a round, restore it, see the message appear)
- [ ] Typecheck passes
- [ ] `./scripts/check.sh` passes

### US-009: Read each poll's state once per notify cycle
**Description:** As the owner, I want the 60-second loop to stay cheap as polls accumulate.

**Acceptance Criteria:**
- [ ] `SessionStore` gets `round_state(poll_id, round_number) -> tuple[list[int], list[PollSlot], dict[tuple[int, int], Vote]]` reading players, slots and votes over one connection (one `asyncio.to_thread` hop); `SessionService.round_state` uses it
- [ ] `run_notify_cycle` reads the state of every voting poll once and passes it to both the auto-close decision and the nudge decision (`SessionNotifier.due_nudges` accepts an optional mapping of precomputed states and falls back to reading them when absent); a poll closed during the cycle is not nudged in that cycle
- [ ] Behaviour is otherwise unchanged: all existing notify, nudge and deadline tests still pass
- [ ] Tests: with N voting polls the cycle makes at most one `round_state` store call per poll (counting fake store); a poll closed by the deadline is not nudged in the same cycle; `due_nudges` without the mapping still works
- [ ] Typecheck passes
- [ ] `./scripts/check.sh` passes

## Functional Requirements

- FR-1: A round closes on its own for "everyone voted" only when at least `MIN_PLAYERS_TO_AUTO_CLOSE` (2) players have joined and none is pending.
- FR-2: Join, leave, vote, close, confirm and cancel are serialised by one service lock; a request that finds the poll no longer `voting` returns `POLL_CLOSED` and writes nothing.
- FR-3: A non-manual close re-checks its reason under the lock and returns `NotReady` (posting nothing) when it no longer holds.
- FR-4: Reminder offsets already past at confirm time are marked handled in the same transaction as the session insert.
- FR-5: **Save** with no selected slot is refused with a prompt; **None of these work** is the only way to record an all-No vote.
- FR-6: A poll refresh never changes or restores components and does nothing for a poll that is not `voting`.
- FR-7: Every poll message the bot sends is recorded (`message_id`, `result_message_id`); a voting round-2+ poll without `message_id`, or an `awaiting_confirm` poll without `result_message_id`, is repaired by the notify cycle.
- FR-8: Cancelling a poll removes the buttons from both its poll message and its result message.
- FR-9: The notify cycle reads each voting poll's state once.

## Non-Goals (Out of Scope)

- Anything not in the review findings: no new commands, parameters or settings.
- Per-poll locks (one service-wide lock is enough at this scale).
- Reposting a round-1 poll message that failed on `/session_poll` (the poll is already discarded and the GM sees an error).
- Removing departed members from rosters (already listed in Known limitations).

## Technical Considerations

- The lock is an `asyncio.Lock` on `SessionService` (already used by `close_round`, `confirm` and `cancel_poll`); the single bot process is the only writer, so no SQLite-level locking is added.
- The cog's `_outcome_lock` is separate from the service lock and is never held while the service lock is awaited from inside the service (the cog calls the service, never the other way round), so there is no lock-order cycle.
- Because `sessions.sqlite3` is unreleased, the `result_message_id` column and the changed `advance_round` semantics are plain schema/code changes; if the branch's data is already in a dev database, deleting `data/sessions.sqlite3` in dev is enough.
- Discord keeps an edited message's components when `view` is omitted from `Message.edit`; this is what makes US-006 work.

## Success Metrics

- `./scripts/check.sh` passes after every story; the new tests fail on the pre-fix code.
- Manual checks (added to `docs/manual-checks.md`): lone-GM vote leaves the round open; a failed outcome post is repaired within a minute; cancelling an `awaiting_confirm` poll removes the Confirm buttons.

## Open Questions

- None. The review's severity order is followed (US-001 to US-009).
