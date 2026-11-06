"""Application configuration — 12-factor, env-driven, validated once at import."""
from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

_INSECURE_SECRETS = {
    "change-me-dev-secret-do-not-use-in-prod",
    "change-me-compose-secret-not-for-real-prod",
    "",
}

# The seed/demo admin password. A real production deploy (demo_mode off) must
# never boot with this still in place — it's public knowledge.
_DEMO_ADMIN_PASSWORD = "ChangeMe!2026"

Environment = Literal["development", "staging", "production", "test"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── App ────────────────────────────────────────────────────────────────
    app_name: str = "ClaimGuard 360°"
    environment: Environment = "development"
    debug: bool = True
    api_v1_prefix: str = "/api/v1"
    version: str = "1.0.0"

    # ── Logging ────────────────────────────────────────────────────────────
    log_level: str = "INFO"
    log_json: bool = False

    # ── Server ─────────────────────────────────────────────────────────────
    host: str = "0.0.0.0"
    port: int = 8000

    # ── Security ───────────────────────────────────────────────────────────
    jwt_secret_key: str = "change-me-dev-secret-do-not-use-in-prod"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 7

    # NoDecode: keep pydantic-settings from JSON-parsing the env value; the
    # validator below splits a comma-separated string instead.
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:5173"]
    )
    rate_limit_per_minute: int = 120

    # Which request header holds the real client IP. Forwarding headers are set by
    # whoever sent the request, so only trust what *your* proxy writes:
    # - CLIENT_IP_HEADER: a header the platform overwrites on every request
    #   (Render/Cloudflare: "true-client-ip"). Takes precedence when set.
    # - FORWARDED_IP_HOPS: otherwise, count this many entries from the right of
    #   X-Forwarded-For (1 = the bundled nginx, which appends the peer address).
    #   0 = no proxy: use the socket peer.
    client_ip_header: str = ""
    forwarded_ip_hops: int = Field(1, ge=0, le=5)

    # Auth brute-force protection (Redis-backed, per email+IP).
    auth_max_failures: int = 8  # per account + client IP
    # Per account, whatever the IP: stops guessing spread across many addresses.
    # Higher than the per-IP limit so one attacker can't trivially lock a user out.
    auth_max_failures_per_account: int = 25
    auth_lockout_seconds: int = 900  # 15 minutes

    # Refresh-token cookie (httpOnly). Secure must be true behind TLS in prod.
    cookie_secure: bool = False
    cookie_samesite: Literal["strict", "lax", "none"] = "strict"
    refresh_cookie_name: str = "cg_refresh"

    # ── Database ───────────────────────────────────────────────────────────
    database_url: str = "postgresql+asyncpg://claimguard:claimguard@localhost:5432/claimguard"
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_echo: bool = False

    # ── Redis / Celery ─────────────────────────────────────────────────────
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"

    # ── FraudShield ──────────────────────────────────────────────────────────
    # ML backend: "auto" (trained ensemble if artifacts present, else heuristic),
    # "trained" (force XGBoost+IsolationForest), or "heuristic" (dependency-free).
    ml_engine: str = "auto"
    # Override the trained-model artifacts directory; blank uses the packaged dir
    # (app/modules/fraudshield/artifacts/).
    ml_artifacts_dir: str = ""

    # ── Demo / seed ────────────────────────────────────────────────────────
    # Off unless asked for: demo mode loads sample data, mounts the scripted
    # scenario router and relaxes the production checks (secure cookies, a
    # changed admin password). A deploy that forgets to set it gets the safe
    # side. docker-compose.yml and .env.example turn it on for the local demo.
    demo_mode: bool = False
    seed_on_startup: bool = False
    first_admin_email: str = "admin@claimguard.co.zw"
    first_admin_password: str = "ChangeMe!2026"

    # ── Notification delivery ────────────────────────────────────────────────
    # Real delivery is opt-in: when the credentials for a channel are absent the
    # adapter logs only (keeps the demo working with no external services).
    # Email (SMTP)
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = "ClaimGuard 360 <no-reply@claimguard.co.zw>"
    smtp_starttls: bool = True
    # WhatsApp (Twilio)
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_whatsapp_from: str = ""  # e.g. +14155238886
    # SMS / USSD gateway (vendor-neutral). Inbound webhooks must send this value
    # in the X-Gateway-Token header; empty disables them.
    gateway_webhook_secret: str = ""
    # The USSD code members dial to answer an alert. The networks assign the real
    # one; until then this demo value is shown everywhere (API and UI read it here).
    ussd_code: str = "*483*360#"

    # ── Claim lifecycle sweep (runs inside the API: Render has no Celery) ─────────
    # Every N minutes: escalate breached SLAs, lapse unanswered member alerts.
    # 0 turns it off. Each step is idempotent, so several API workers are safe.
    claim_sweep_minutes: int = Field(15, ge=0, le=1440)
    # Days a member has to answer a verification alert before it lapses.
    member_alert_ttl_days: int = Field(7, ge=1, le=90)
    # Hours a model-approved claim waits before it is paid. Until then a member's
    # dispute, an investigation or a reviewer can still stop it. 0 = pay at once.
    payment_hold_hours: int = Field(72, ge=0, le=720)

    @property
    def email_configured(self) -> bool:
        return bool(self.smtp_host)

    @property
    def whatsapp_configured(self) -> bool:
        return bool(
            self.twilio_account_sid
            and self.twilio_auth_token
            and self.twilio_whatsapp_from
        )

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, v: object) -> object:
        if isinstance(v, str):
            return [o.strip() for o in v.split(",") if o.strip()]
        return v

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @model_validator(mode="after")
    def _enforce_production_security(self) -> Settings:
        """Fail fast on insecure configuration in production.

        Baseline checks apply to every production boot. The strictest checks
        (secure cookies, non-default admin password) apply only when demo_mode
        is off — i.e. a *real* deployment, not the packaged demo which
        intentionally runs `ENVIRONMENT=production` over plain http.
        """
        if self.is_production:
            problems = []
            if self.jwt_secret_key in _INSECURE_SECRETS:
                problems.append(
                    "JWT_SECRET_KEY must be set to a strong secret "
                    "(e.g. `openssl rand -hex 32`)"
                )
            if self.debug:
                problems.append("DEBUG must be false in production")
            if any(o == "*" for o in self.cors_origins):
                problems.append("CORS_ORIGINS must not be '*' in production")
            if self.client_ip_header and self.client_ip_header.lower() in {
                "x-forwarded-for", "x-real-ip"
            }:
                problems.append(
                    "CLIENT_IP_HEADER must be a header your platform overwrites "
                    "(e.g. true-client-ip); use FORWARDED_IP_HOPS for X-Forwarded-For"
                )

            if not self.demo_mode:
                if not self.cookie_secure:
                    problems.append(
                        "COOKIE_SECURE must be true in production (serve over HTTPS)"
                    )
                if self.first_admin_password == _DEMO_ADMIN_PASSWORD:
                    problems.append(
                        "FIRST_ADMIN_PASSWORD must be changed from the demo default"
                    )

            if problems:
                raise ValueError(
                    "Insecure production configuration:\n  - " + "\n  - ".join(problems)
                )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
