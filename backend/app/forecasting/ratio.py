"""Moved to ``boxfit.ratio`` (shared fitting package, BOX step 5a).

This module is an alias: importing ``app.forecasting.ratio`` returns the ``boxfit.ratio``
module object itself, so attribute access and monkeypatching hit the one
implementation. The star import is for static type checkers only."""

import sys

import boxfit.ratio as _impl
from boxfit.ratio import *  # noqa: F403

sys.modules[__name__] = _impl
