# WOWKIDS speed optimization notes

## Safety baseline

The known-good end-to-end runtime remains frozen at:

- branch: `stable-working-2026-09-22`
- commit: `b6aaa74f9fc3ad5bde03e4e978b1abda0079379f`
- convenience fallback: `stable-current`

The speed work below is on `main`. Do not move the immutable stable branch.

## Already solved before this speed pass

- Background Windows agent starts automatically.
- Pairing survives normal Windows restarts and code updates.
- Feedback Assistant queues selected students and ratings.
- Opening WOWKIDS Home is enough; Home -> date -> class -> roster navigation is automated.
- Class ability layout is discovered once on the first student and reused.
- Student-level Submit is automatic and visually verified.
- Post All is structurally outside the automation and must remain manual.
- The active HumanLikeRatingSession does not animate/park the physical cursor for screenshots.
- Critical targeting uses screenshot-backed evidence because WeChat retains stale Chromium UIA PageFrames.

## Speed pass 1

### Measurement

`performance_log.py` records one row per processed student to:

- `reports/performance.jsonl`
- `reports/performance.csv`

Measured phases:

- roster ready
- student lookup
- open student
- assessment load
- category discovery
- rating actions
- Submit
- return to roster
- total

The code version is stored with each row. Run `SHOW_PERFORMANCE.bat` for the last 12 completed students.

### Safe execution changes

1. Removed mandatory roster-top scrolling when the requested student is already visible.
2. Replaced the fixed 550 ms accordion wait with a bounded visual wait that continues as soon as the verified 1..5 star stack is rendered.
3. Replaced the fixed 450 ms score wait with a bounded local radio-state visual wait.
4. Reduced roster/student/Submit polling intervals while preserving the same state checks and timeouts.
5. Cache assessment-ready proof inside one student's rating session so discovery/fill do not repeat the same load wait and top normalization.

## Intentionally NOT changed

### No AI/Jev loop

The deterministic engine is already accurate. Speed is an execution problem, not a reasoning problem.

### No broad UIA Invoke rewrite

WeChat's Chromium layer retains stale accessibility frames. UIA is useful as evidence, but replacing proven screenshot/coordinate clicks with direct Invoke would add risk without a demonstrated speed benefit.

### No speculative direct-next-student shortcut

WOWKIDS naturally returns to the roster after Submit. The current transition is verified and safe. We will optimize it only if performance logs show it is a major bottleneck.

### Pairing storage format unchanged in this pass

The token is persistent under the Windows user profile and normal restarts do not require re-pairing. Changing the credential format at the same time as speed work would add rollback risk. Do security-hardening separately after the optimized path is proven.

## Next decision rule

Do not blindly shorten more waits. Collect several successful student timings first, then optimize the largest measured phase. After a newer build completes a real end-to-end class reliably, move `stable-current` to that proven commit while keeping `stable-working-2026-09-22` immutable.
