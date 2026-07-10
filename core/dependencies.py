from __future__ import annotations

from functools import lru_cache

from config.settings import Config
from database.mongo_client import db
from database.claim_repository import ClaimRepository
from database.user_repository import UserRepository


@lru_cache(maxsize=1)
def get_claim_repository() -> ClaimRepository:
    return ClaimRepository(db)


@lru_cache(maxsize=1)
def get_user_repository() -> UserRepository:
    return UserRepository(db)


def get_app_settings() -> Config:
    return Config

