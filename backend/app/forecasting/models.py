"""Moved to ``boxfit.models`` (shared fitting package, BOX step 5a).

This module is an alias: importing ``app.forecasting.models`` returns the ``boxfit.models``
module object itself, so attribute access and monkeypatching hit the one
implementation. The star import is for static type checkers only."""

import sys

import boxfit.models as _impl
from boxfit.models import *  # noqa: F403

sys.modules[__name__] = _impl
