"""Moved to ``boxfit.tc.fit_p50`` (shared fitting package, BOX step 5a).

This module is an alias: importing ``app.type_curves.fit_p50`` returns the ``boxfit.tc.fit_p50``
module object itself, so attribute access and monkeypatching hit the one
implementation. The star import is for static type checkers only."""

import sys

import boxfit.tc.fit_p50 as _impl
from boxfit.tc.fit_p50 import *  # noqa: F403

sys.modules[__name__] = _impl
