from typing import Optional, Any
import os
from pydantic_settings import BaseSettings, SettingsConfigDict


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

    def model_post_init(self, __context: Any) -> None:
        chat_db = os.getenv("CHAT_DB_PATH")
        if chat_db:
            if "sqlite+aiosqlite:///" in chat_db:
                self.DATABASE_URL = chat_db
                self.SYNC_DATABASE_URL = chat_db.replace("sqlite+aiosqlite:///", "sqlite:///")
            elif chat_db.startswith("sqlite:///"):
                self.SYNC_DATABASE_URL = chat_db
                self.DATABASE_URL = chat_db.replace("sqlite:///", "sqlite+aiosqlite:///")
            elif "://" in chat_db:
                self.DATABASE_URL = chat_db
                self.SYNC_DATABASE_URL = chat_db
            else:
                abs_path = os.path.abspath(chat_db).replace("\\", "/")
                self.DATABASE_URL = f"sqlite+aiosqlite:///{abs_path}"
                self.SYNC_DATABASE_URL = f"sqlite:///{abs_path}"


settings = Settings()

