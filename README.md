# Wowkids rating tool

Deterministic helper for entering the five student ratings in the WeChat mini
program **Wowkids管理工具**.

Design rules this tool follows (they are in the code, not just the docs):

* It only ever writes the five rating categories.
* It never touches names, photos, galleries, notes or `Post All`.
* It stops before Submit; Submit needs its own second key press.
* `ESC` is an immediate abort.
* It refuses to run when the expected page is not clearly visible.
* It never sends anything anywhere; everything stays in local files.

## Folder map

| Path | What it is |
| --- | --- |
| `inspect_uia.py` | Stage 1 inspector. Read-only dump of the window's UI Automation tree. |
| `probe_sections.py` | Stage 2b probe: maps how each category expands/collapses. Expands headers only. |
| `wkcommon.py` | Shared plumbing: library bootstrap, window discovery, logging. |
| `selftest_logic.py` | Offline self-test of the mapping maths (no mini program needed). |
| `_fetch_libs.py` | Downloads and unpacks the vendored libraries into `libs/`. Only needed once. |
| `libs/` | Vendored `pywinauto` + `comtypes` + `pywin32`. No `pip` needed. |
| `ratings.csv` | The data file the tool reads ratings from. |
| `reports/` | Inspector output: `uia_dump_<stamp>.txt` and `.json`. |
| `logs/` | Run logs once the fill/submit stages exist. |
| `run_inspect.bat` | Double-click launcher for Stage 1. |

## Status

* Stage 1 (inspect the UI Automation tree) - **built and exercised**. The first
  real run captured the Home page while the window was minimised, so the rating
  page itself still needs one confirmed run.
* Stage 2 (read-only rating page detector) - **built**. Both real runs so far
  captured the class page (`Post All` + student cards), never the five-category
  rating page, so the option geometry is still unknown.
* Stage 3 (fill one student, never submit) - **built**, blocked on the same
  missing geometry. Run it with `dry` first.
* Stages 4-7 - not built yet, on purpose.

## Coordinate space must be confirmed first (fail-closed)

Mouse clicks are refused until `calibration.json` exists, which only happens
after the calibration overlay has been checked by eye:

```
run_calibrate.bat                 measure + draw overlays, click nothing
run_calibrate.bat confirm H1      activate the hypothesis that matched the image
```

The overlay draws three interpretations of every landmark rectangle onto a real
screenshot: red = H1 (raw rects are screen pixels), blue = H2 (relative to the
window rectangle), magenta = H3 (relative to the client area). Green outlines
the client area, yellow the window, orange marks the bottom 140px no-click band.

## Rating sections are accordions

Each of the five categories is an expandable section: while collapsed only the
heading and description exist in the tree, and expanding it reveals five score
rows (`circle + stars`). The tool therefore measures expansion state from the
tree (row count and geometry), never from the arrow glyph:

* `collapsed` - no option-sized rows inside the section band
* `expanded-vertical` - five or more stacked score rows (the real layout)
* `invalid-layout` - a single line holding five or more nodes. That shape is
  rejected: the probe aborts instead of clicking, because it means the geometry
  is not understood.

`expanded-vertical` requires five rows with strictly increasing Y positions and
aligned left edges, each row carrying a marker node (image/radio/small text).

`run_probe.bat` performs the expansion experiment: for each category it records
the tree, expands the header, records the tree again, verifies that five score
rows appeared, and reports whether the form collapses the previously open
section (accordion behaviour).

```
run_probe_first.bat          always probes Making Skills only
run_probe.bat first          same thing
run_probe.bat all            probe all five categories
run_probe.bat 1,2            probe categories 1 and 2
```

Expansion uses `ExpandCollapse`/`Invoke`/`Toggle` when the tree exposes those
patterns (no coordinates involved), otherwise a single click that must pass all
of these: inside the category card horizontally, on the heading line, above the
first score row, outside the bottom 140px of the client area, at least 12px away
from Home/Course/Student/学员/Post All/Submit/提交/Gallery, and mapped through the
confirmed coordinate space. Option rows are never clicked. Before/after
screenshots go to `reports/shots/` via PrintWindow (Windows 10 compatible) with
a BitBlt fallback.

## Window diagnostic

`run_diagnose.bat` lists every top-level window and summarises what each
WeChat-related window contains, including which one holds the five rating
headings. Use it when a detector run lands on the wrong page.

## Stage 1: how to run it

1. Open the mini program and navigate to a student's rating page, so the five
   categories and the stars are on screen. Do not change anything.
2. Double-click `run_inspect.bat`.
3. It prints a short summary and writes the full tree into `reports/`.

The inspector never clicks, types or scrolls. It only reads.

What the report answers:

* whether the five rating headings appear in the UI Automation tree,
* whether the star choices are real controls (`RadioButton` / `ListItem` / ...)
  or just pictures,
* whether the Submit button is exposed,
* which containers support `ScrollPattern`, so scrolling can be automated
  without guessing coordinates.

If the report says "no WeChat mini program window found", the mini program is
closed, minimised, or running under a debugger that is hiding it. Start the
mini program, bring its window up, and run it again.

Both batch files restore a minimised mini program window automatically, because
a minimised Chromium window reports a 0x0 window rectangle.

## Stage 2: how to run it

1. Open a student's rating page (the five categories plus stars).
2. Double-click `run_detect.bat`.
3. It prints whether the page was recognised, how many sections produced a
   5-option row, and writes `reports/rating_page_<stamp>.txt` and `.json`.

Exit codes: `0` = rating page mapped, `2` = no mini program window found,
`3` = not a rating page (it then lists the visible text so you can navigate).

On failure it also lists every page frame it can see, with a text sample of
each, which is how we identify which page is actually open.

## Stage 3: fill one student, never submit

```
run_fill_test.bat dry     build and print the plan, click nothing
run_fill_test.bat         click the five options (scores 4,4,4,3,5)
```

The script refuses to click unless the window title matches, all five headings
are present in order, and every section yields a clean five-option row. Each
click point is recomputed from the live UIA rectangle and must fall inside the
live window rectangle and inside that section's band. After every click it
re-reads the tree, re-maps the page, and records whether the section changed.
ESC aborts at any point. There is no Submit code path in the script.

Outputs: `reports/fill_<stamp>.txt|json`, `logs/fill_<stamp>.log` and
`logs/processed.csv` (duplicate protection).

## Planned stages

3. Fill one test student with `4,4,4,3,5`, stopping before Submit.
4. `F8` = fill the current student from `ratings.csv`.
5. `F9` = press Submit (only after `F8` succeeded).
6. CSV + logging polish, duplicate-student protection.
7. Friendly Windows launcher.

## Data format

`ratings.csv`:

```
Student,Making,ProblemSolving,Theory,Creative,Interpersonal
DemoStudent,4,4,4,3,5
```

Column order is fixed: Making, Problem Solving, Theory, Creative,
Interpersonal.