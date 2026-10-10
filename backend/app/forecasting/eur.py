"""Moved to ``boxfit.eur`` (shared fitting package, BOX step 5a).

This module is an alias: importing ``app.forecasting.eur`` returns the ``boxfit.eur``
module object itself, so attribute access and monkeypatching hit the one
implementation. The star import is for static type checkers only."""

import sys

import boxfit.eur as _impl
from boxfit.eur import *  # noqa: F403
from boxfit.eur import _solve_t_at_rate as _solve_t_at_rate

sys.modules[__name__] = _impl
