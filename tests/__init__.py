"""Optional local settings for the tests.

Put machine-specific values, such as the paths to your own reference projects,
in `.env.local` at the repository root (it is git-ignored):

    SLICER_OPT_V7=~/projects/my-part.3mf
    SLICER_OPT_V6_DIR=~/projects/older-version

Values already set in the environment win.
"""

import os
from pathlib import Path

_LOCAL = Path(__file__).resolve().parent.parent / ".env.local"
if _LOCAL.is_file():
    for _line in _LOCAL.read_text().splitlines():
        _key, _sep, _value = _line.strip().partition("=")
        if _sep and _key and not _key.startswith("#"):
            os.environ.setdefault(_key.strip(), os.path.expanduser(_value.strip().strip('"').strip("'")))


# Exact times in the acceptance tests were measured with this Bambu Studio; another version
# slices the same file to other times (the v7 lid: 8670.84 s here, 8663.40 s with 02.08.02.61).
MEASURED_WITH = "02.08.04.57"


def measured_with_this_bambu() -> bool:
    from slicer_optimizer import paths
    return paths.studio_version() == MEASURED_WITH
