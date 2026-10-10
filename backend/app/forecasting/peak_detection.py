"""Moved to ``boxfit.peak_detection`` (shared fitting package, BOX step 5a).

This module is an alias: importing ``app.forecasting.peak_detection`` returns the ``boxfit.peak_detection``
module object itself, so attribute access and monkeypatching hit the one
implementation. The star import is for static type checkers only."""

import sys

import boxfit.peak_detection as _impl
from boxfit.peak_detection import *  # noqa: F403

sys.modules[__name__] = _impl
