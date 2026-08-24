"""Backward-compatible alias. Prefer ``baseball_analyze.models.predict_core``."""

import sys

from baseball_analyze.models import predict_core as _predict_core

sys.modules[__name__] = _predict_core
