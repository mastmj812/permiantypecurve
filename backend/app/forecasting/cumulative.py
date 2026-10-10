"""Moved to ``boxfit.cumulative`` (shared fitting package, BOX step 5a).

This module is an alias: importing ``app.forecasting.cumulative`` returns the ``boxfit.cumulative``
module object itself, so attribute access and monkeypatching hit the one
implementation. The star import is for static type checkers only."""

import sys

import boxfit.cumulative as _impl
from boxfit.cumulative import *  # noqa: F403

sys.modules[__name__] = _impl
