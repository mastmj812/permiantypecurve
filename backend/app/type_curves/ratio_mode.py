"""Moved to ``boxfit.tc.ratio_mode`` (shared fitting package, BOX step 5a).

This module is an alias: importing ``app.type_curves.ratio_mode`` returns the ``boxfit.tc.ratio_mode``
module object itself, so attribute access and monkeypatching hit the one
implementation. The star import is for static type checkers only."""

import sys

import boxfit.tc.ratio_mode as _impl
from boxfit.tc.ratio_mode import *  # noqa: F403

sys.modules[__name__] = _impl
