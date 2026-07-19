"""
Compatibility layer.

The active claim submission flow now lives in `blueprints/user.py`.
This module keeps legacy imports working without maintaining a second claim flow.
"""

from blueprints.user import submit_claim

__all__ = ["submit_claim"]
