import base64
import os
from functools import lru_cache
from pathlib import Path


class Settings:
    def __init__(self):
        e = os.environ.get
        self.env = e("GENRANQ_ENV", "development")
        # Two connections: the app role is subject to row-level security; the system role
        # is used only by auth lookup, signup, the vault backend and the job claimer.
        self.database_url_app = e("DATABASE_URL_APP", "postgresql+psycopg://genranq_app:genranq_app@localhost:5432/genranq")
        self.database_url_system = e("DATABASE_URL_SYSTEM", "postgresql+psycopg://genranq_owner:genranq_owner@localhost:5432/genranq")
        self.jwt_secret = e("JWT_SECRET", "dev-only-change-me-" + "x" * 32)
        self.access_ttl_min = int(e("ACCESS_TTL_MIN", "15"))
        self.refresh_ttl_days = int(e("REFRESH_TTL_DAYS", "30"))
        self.step_up_ttl_min = int(e("STEP_UP_TTL_MIN", "10"))
        # 32-byte key-encryption key (KEK). In production this is held in a KMS; here it is
        # read from env and used to wrap per-secret data keys (envelope encryption).
        kek = e("VAULT_MASTER_KEY")
        self.vault_kek = base64.b64decode(kek) if kek else b"\x01" * 32
        self.storage_dir = Path(e("STORAGE_DIR", str(Path(__file__).resolve().parents[1] / "storage")))
        # SSRF guard: the crawler refuses private/loopback targets unless explicitly allowed (tests/dev).
        self.allow_private_crawl = e("ALLOW_PRIVATE_CRAWL", "0") == "1"
        self.crawl_delay_sec = float(e("CRAWL_DELAY_SEC", "0.2"))
        self.crawl_user_agent = e("CRAWL_USER_AGENT", "GenranqBot/1.0 (+https://genranq.com/bot)")
        self.cors_origins = [x for x in e("CORS_ORIGINS", "http://localhost:5173").split(",") if x]
        self.google_drive_spec_file_id = e("SPEC_DRIVE_FILE_ID")  # configured spec source on Drive
        if self.env == "production":
            assert "dev-only" not in self.jwt_secret, "JWT_SECRET must be set in production"
            assert kek, "VAULT_MASTER_KEY must be set in production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
