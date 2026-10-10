"""Moved to ``boxfit.metrics`` (shared fitting package, BOX step 5a).

This module is an alias: importing ``app.forecasting.metrics`` returns the ``boxfit.metrics``
module object itself, so attribute access and monkeypatching hit the one
implementation. The star import is for static type checkers only."""

import sys

import boxfit.metrics as _impl
from boxfit.metrics import *  # noqa: F403

sys.modules[__name__] = _impl
