"""Compatibility launcher for the current guarded development stage.

RUN_NEXT_STAGE.bat still calls this filename so existing local copies can
self-update with git pull and immediately run the newer remaining-category test.
There is intentionally no Submit/Post All code here.
"""

from select_remaining_scores_test import main


if __name__ == "__main__":
    raise SystemExit(main())
