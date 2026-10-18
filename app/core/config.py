import os
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional


class Settings(BaseSettings):
    # Brevo API configuration
    BREVO_API_KEY: str = ""
    BREVO_SENDER_EMAIL: str = "noreply@autoinvoice.com"
    BREVO_SENDER_NAME: str = "AutoInvoice Ledger"

    # JWT & Auth configuration
    JWT_SECRET: str = "autoinvoice_jwt_super_secret_key_change_me_in_production_32bytes"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # Cookie security flag (False for local development over HTTP, True for production HTTPS)
    COOKIE_SECURE: bool = False

    # Database configuration
    # Async database URL for SQLAlchemy async engine
    DATABASE_URL: str = "sqlite+aiosqlite:///./invoices.db"
    # Sync database URL for Alembic migrations
    SYNC_DATABASE_URL: str = "sqlite:///./invoices.db"

    # Rate limiting
    RATELIMIT_STORAGE_URL: str = "memory://"

    # Testing flag
    TESTING: bool = False

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
