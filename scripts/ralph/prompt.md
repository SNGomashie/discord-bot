# Ralph iteration instructions

You are an autonomous coding agent working on the ninja-discord-bot project. Each iteration is a fresh context; memory lives in git history, `scripts/ralph/progress.txt` and `scripts/ralph/prd.json`.

1. Read `CLAUDE.md`, `scripts/ralph/prd.json`, and `scripts/ralph/progress.txt` (check its "Codebase Patterns" section first).
2. Make sure you are on the branch named in `prd.json` (`branchName`).
3. Pick the highest-priority user story where `passes` is `false`.
4. Implement ONLY that one story. Keep the change small and consistent with existing code.
4b. If the story changes user-visible behaviour, a setting, a permission, a dependency or stored data, update the docs in the same story, following the "Documentation" table in `CLAUDE.md` (README first). Take values from the code, and remove text that is no longer true.
5. Run `./scripts/check.sh`. Fix everything until it passes. Never weaken tests or gates to make them pass.
6. If you learn a reusable pattern or gotcha, add it to the "Codebase Patterns" section of `progress.txt` (and to CLAUDE.md if it's important for future work).
7. Commit all changes using Conventional Commits (`feat:`, `fix:`, `test:`, `refactor:`, `docs:`, `chore:`), lowercase imperative subject, e.g. `feat: add dice roll command`. Put the story ID in the commit body (`Story: US-001`).
8. Set `passes: true` for that story in `prd.json` and append a short entry to `progress.txt` (what was done, files, learnings).
9. Never read or print `.env`. Never touch tokens or production config. You cannot verify behavior inside Discord; verify via unit tests with mocked Discord objects. Acceptance criteria that need a human in Discord should be noted in `progress.txt` as "needs manual check" but do not block `passes`.

If ALL stories have `passes: true`, reply with exactly `<promise>COMPLETE</promise>`. Otherwise end your turn normally after finishing one story.
