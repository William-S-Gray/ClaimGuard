"""Security-hardening unit tests: prod config guards + real-client-IP extraction."""
from __future__ import annotations

import pytest
from pydantic import ValidationError
from starlette.requests import Request

from app.core.config import Settings, settings
from app.core.dependencies import client_ip

STRONG_SECRET = "a" * 64


def _make_request(headers: dict[str, str], client_host: str = "10.0.0.1") -> Request:
    scope = {
        "type": "http",
        "headers": [(k.lower().encode(), v.encode()) for k, v in headers.items()],
        "client": (client_host, 12345),
    }
    return Request(scope)


# ─── Production config guards ────────────────────────────────────────────────────
def test_real_prod_rejects_insecure_cookie_and_demo_password():
    with pytest.raises(ValidationError) as exc:
        Settings(
            environment="production",
            demo_mode=False,
            jwt_secret_key=STRONG_SECRET,
            cookie_secure=False,
            first_admin_password="ChangeMe!2026",
        )
    msg = str(exc.value)
    assert "COOKIE_SECURE must be true" in msg
    assert "FIRST_ADMIN_PASSWORD" in msg


def test_real_prod_accepts_hardened_config():
    s = Settings(
        environment="production",
        debug=False,
        demo_mode=False,
        jwt_secret_key=STRONG_SECRET,
        cookie_secure=True,
        first_admin_password="a-strong-unique-admin-password",
    )
    assert s.is_production and not s.demo_mode


def test_demo_prod_allows_relaxed_config():
    # The packaged demo runs ENVIRONMENT=production with demo_mode on; it must boot
    # with http cookies and the demo admin password.
    s = Settings(
        environment="production",
        debug=False,
        demo_mode=True,
        jwt_secret_key=STRONG_SECRET,
        cookie_secure=False,
        first_admin_password="ChangeMe!2026",
    )
    assert s.demo_mode


def test_prod_still_rejects_insecure_jwt_secret():
    with pytest.raises(ValidationError):
        Settings(environment="production", demo_mode=True, jwt_secret_key="")


# ─── Real client IP behind a proxy ───────────────────────────────────────────────
def test_client_ip_ignores_x_real_ip(monkeypatch):
    # Render doesn't overwrite X-Real-IP, so a client could pick its own value.
    monkeypatch.setattr(settings, "client_ip_header", "")
    req = _make_request({"x-real-ip": "203.0.113.7"}, client_host="172.18.0.5")
    assert client_ip(req) == "172.18.0.5"


def test_client_ip_ignores_client_supplied_forwarded_entries(monkeypatch):
    # nginx appends the real peer; whatever the client put before it is untrusted.
    monkeypatch.setattr(settings, "client_ip_header", "")
    monkeypatch.setattr(settings, "forwarded_ip_hops", 1)
    req = _make_request(
        {"x-forwarded-for": "6.6.6.6, 203.0.113.9"}, client_host="172.18.0.5"
    )
    assert client_ip(req) == "203.0.113.9"


def test_client_ip_counts_hops_from_the_right(monkeypatch):
    monkeypatch.setattr(settings, "client_ip_header", "")
    monkeypatch.setattr(settings, "forwarded_ip_hops", 2)
    req = _make_request({"x-forwarded-for": "6.6.6.6, 203.0.113.9, 10.1.1.1"})
    assert client_ip(req) == "203.0.113.9"
    # Fewer entries than hops: don't guess, use the socket peer.
    short = _make_request({"x-forwarded-for": "6.6.6.6"}, client_host="172.18.0.5")
    assert client_ip(short) == "172.18.0.5"


def test_client_ip_prefers_the_platform_header(monkeypatch):
    monkeypatch.setattr(settings, "client_ip_header", "true-client-ip")
    req = _make_request(
        {"true-client-ip": "198.51.100.4", "x-forwarded-for": "6.6.6.6, 10.1.1.1"}
    )
    assert client_ip(req) == "198.51.100.4"


def test_client_ip_hops_zero_means_no_proxy(monkeypatch):
    monkeypatch.setattr(settings, "client_ip_header", "")
    monkeypatch.setattr(settings, "forwarded_ip_hops", 0)
    req = _make_request({"x-forwarded-for": "6.6.6.6"}, client_host="172.18.0.5")
    assert client_ip(req) == "172.18.0.5"


def test_client_ip_falls_back_to_socket_peer():
    req = _make_request({}, client_host="172.18.0.5")
    assert client_ip(req) == "172.18.0.5"


def test_prod_rejects_a_forgeable_client_ip_header():
    with pytest.raises(ValidationError, match="CLIENT_IP_HEADER"):
        Settings(
            environment="production", debug=False, demo_mode=True,
            jwt_secret_key=STRONG_SECRET, client_ip_header="X-Forwarded-For",
        )


def test_cors_never_falls_back_to_any_origin(monkeypatch):
    """An empty CORS_ORIGINS used to become "*", which (with credentials) let any
    site read a visitor's tokens. Empty must now mean same-origin only."""
    from starlette.testclient import TestClient

    from app.main import create_app

    monkeypatch.setattr(settings, "cors_origins", [])
    resp = TestClient(create_app()).post(
        "/api/v1/auth/refresh",
        headers={"Origin": "https://evil.example", "Cookie": "cg_refresh=x"},
    )
    assert "access-control-allow-origin" not in resp.headers
