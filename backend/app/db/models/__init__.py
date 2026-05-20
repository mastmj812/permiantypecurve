"""Importing this package ensures every mapped class is registered with
``Base.metadata`` — Alembic's env.py imports from here for autogenerate.
"""

from app.db.models.deals import Deal
from app.db.models.forecasts import FitMethod, Forecast, ModelType, Stream
from app.db.models.production_monthly import ProductionMonthly
from app.db.models.sync_state import SyncEntity, SyncJob, SyncJobStatus, SyncWatermark
from app.db.models.type_curves import AlignmentMethod, NormalizationBasis, TypeCurve
from app.db.models.users import User
from app.db.models.wells import Well, WellstickSource, WellStatus

__all__ = [
    "AlignmentMethod",
    "Deal",
    "FitMethod",
    "Forecast",
    "ModelType",
    "NormalizationBasis",
    "ProductionMonthly",
    "Stream",
    "SyncEntity",
    "SyncJob",
    "SyncJobStatus",
    "SyncWatermark",
    "TypeCurve",
    "User",
    "Well",
    "WellStatus",
    "WellstickSource",
]
