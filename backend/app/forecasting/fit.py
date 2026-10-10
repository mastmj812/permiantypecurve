"""Moved to ``boxfit.fit`` (shared fitting package, BOX step 5a).

This module is an alias: importing ``app.forecasting.fit`` returns the ``boxfit.fit``
module object itself, so attribute access and monkeypatching hit the one
implementation. The star import is for static type checkers only."""

import sys

import boxfit.fit as _impl
from boxfit.fit import *  # noqa: F403
from boxfit.fit import _bounds_and_p0 as _bounds_and_p0
from boxfit.fit import _di_at_bound as _di_at_bound
from boxfit.fit import _fit_with_b_prior as _fit_with_b_prior
from boxfit.fit import _flag_downtime as _flag_downtime
from boxfit.fit import _post_peak_slice as _post_peak_slice
from boxfit.fit import _rate_callable as _rate_callable
from boxfit.fit import _stream_di_hi as _stream_di_hi

sys.modules[__name__] = _impl
