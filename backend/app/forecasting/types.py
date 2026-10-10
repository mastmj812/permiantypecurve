"""Moved to ``boxfit.types`` (shared fitting package, BOX step 5a).

This module is an alias: importing ``app.forecasting.types`` returns the ``boxfit.types``
module object itself, so attribute access and monkeypatching hit the one
implementation. The star import is for static type checkers only."""

import sys

import boxfit.types as _impl
from boxfit.types import *  # noqa: F403

sys.modules[__name__] = _impl
