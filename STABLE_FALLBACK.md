# Known-good WOWKIDS fallback

This repository has a confirmed working automation snapshot from **2026-09-22**.

## Confirmed behavior

The snapshot was reported working end-to-end for two selected students:

- Feedback Assistant job received by the Windows agent.
- WOWKIDS Home/calendar navigation.
- Correct date and class opened.
- View comments opened the roster.
- Only explicitly queued students were processed.
- Student assessment page opened.
- Existing visual ability discovery and score selection ran.
- Student-level Submit ran automatically.
- **Post All remained untouched.**

## Protected refs

- Immutable archive branch: `stable-working-2026-09-22`
- Exact tested runtime commit: `b6aaa74f9fc3ad5bde03e4e978b1abda0079379f`
- Convenience fallback branch: `stable-current`

Do not move or delete `stable-working-2026-09-22`.

Only update `stable-current` after a newer version has passed a real end-to-end class test.

## One-click fallback

Run:

`USE_STABLE_VERSION.bat`

It preserves local uncommitted work in a git stash, switches the working folder to `stable-current`, compile-checks the core modules, reuses the saved Windows pairing, and restarts the background agent.

To return to development/main later, run:

`RETURN_TO_LATEST.bat`

Pairing credentials remain under the Windows user profile and are not stored in Git.
