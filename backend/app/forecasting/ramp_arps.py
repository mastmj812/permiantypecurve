"""Moved to ``boxfit.ramp_arps`` (shared fitting package, BOX step 5a).

This module is an alias: importing ``app.forecasting.ramp_arps`` returns the ``boxfit.ramp_arps``
module object itself, so attribute access and monkeypatching hit the one
implementation. The star import is for static type checkers only."""

import sys

import boxfit.ramp_arps as _impl
from boxfit.ramp_arps import *  # noqa: F403

sys.modules[__name__] = _impl
