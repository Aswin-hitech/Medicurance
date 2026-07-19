from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    CITIZEN = "user"
    OFFICER = "officer"
    ADMIN = "admin"

