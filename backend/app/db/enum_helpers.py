"""SQLAlchemy ↔ Postgres enum bridge.

Postgres enum types in our migrations use lowercase string values
(e.g. ``'well_headers'``, ``'oil'``). Python enum members use
ALL_CAPS names but lowercase ``.value`` strings:

    class Stream(str, enum.Enum):
        OIL = "oil"
        GAS = "gas"
        ...

Without ``values_callable``, SQLAlchemy's default ``Enum`` column type sends
the member NAME on writes (``"OIL"``) — Postgres rejects it with
``invalid input value for enum``. ``pg_enum`` round-trips by ``.value`` so
the wire format matches what the migration created.
"""

from __future__ import annotations

import enum
from typing import Any, TypeVar

from sqlalchemy import Enum as SAEnum

E = TypeVar("E", bound=enum.Enum)


def pg_enum(py_enum: type[E], *, name: str, **kwargs: Any) -> SAEnum:
    return SAEnum(
        py_enum,
        name=name,
        values_callable=lambda x: [e.value for e in x],
        **kwargs,
    )
