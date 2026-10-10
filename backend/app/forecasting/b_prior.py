"""Moved to ``boxfit.b_prior`` (shared fitting package, BOX step 5a).

This module is an alias: importing ``app.forecasting.b_prior`` returns the ``boxfit.b_prior``
module object itself, so attribute access and monkeypatching hit the one
implementation. The star import is for static type checkers only."""

import sys

import boxfit.b_prior as _impl
from boxfit.b_prior import *  # noqa: F403
from boxfit.b_prior import _load as _load

sys.modules[__name__] = _impl
