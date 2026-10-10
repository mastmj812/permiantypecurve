"""Moved to ``boxfit.tc.aggregate`` (shared fitting package, BOX step 5a).

This module is an alias: importing ``app.type_curves.aggregate`` returns the ``boxfit.tc.aggregate``
module object itself, so attribute access and monkeypatching hit the one
implementation. The star import is for static type checkers only."""

import sys

import boxfit.tc.aggregate as _impl
from boxfit.tc.aggregate import *  # noqa: F403

sys.modules[__name__] = _impl
