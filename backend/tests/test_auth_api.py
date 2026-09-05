"""Integration tests for /api/auth/* - signup/login/duplicate-email/
wrong-password, plus the JWT/SMTP 503 "not configured" gates.

Uses the `client` fixture from conftest.py (TestClient + in-memory
SQLite) - see conftest.py's module docstring for why SQLite is the right
choice here.
"""

from app.core.config import get_settings

from .conftest import VALID_TEST_PASSWORD, auth_headers, signup, unique_email


# --- signup ------------------------------------------------------------


def test_signup_creates_a_user_and_returns_tokens(client):
    email = unique_email()
    response = client.post(
        "/api/auth/signup", json={"email": email, "password": VALID_TEST_PASSWORD}
    )

    assert response.status_code == 201
    body = response.json()
    assert body["access_token"]
    assert body["refresh_token"]
    assert body["token_type"] == "bearer"


def test_signup_rejects_a_password_missing_an_uppercase_letter(client):
    response = client.post(
        "/api/auth/signup",
        json={"email": unique_email(), "password": "lowercase-only-123!"},
    )
    assert response.status_code == 422
    assert "uppercase letter" in response.text


def test_signup_rejects_a_password_missing_a_special_character(client):
    response = client.post(
        "/api/auth/signup",
        json={"email": unique_email(), "password": "NoSpecialChar123"},
    )
    assert response.status_code == 422
    assert "special character" in response.text


def test_signup_rejects_a_too_short_password(client):
    response = client.post(
        "/api/auth/signup", json={"email": unique_email(), "password": "Ab1!"}
    )
    assert response.status_code == 422
    assert "8 characters" in response.text


def test_signup_duplicate_email_is_rejected(client):
    email = unique_email()
    first = client.post(
        "/api/auth/signup", json={"email": email, "password": VALID_TEST_PASSWORD}
    )
    assert first.status_code == 201

    second = client.post(
        "/api/auth/signup", json={"email": email, "password": "A-different-Password-456!"}
    )

    assert second.status_code == 400
    assert "already registered" in second.json()["detail"].lower()


def test_me_requires_a_valid_token(client):
    signed_up = signup(client)

    authed = client.get("/api/auth/me", headers=auth_headers(signed_up))
    assert authed.status_code == 200
    assert authed.json()["email"] == signed_up["email"]

    unauthed = client.get("/api/auth/me")
    assert unauthed.status_code == 401


# --- login ---------------------------------------------------------------


def test_login_with_correct_credentials_succeeds(client):
    signed_up = signup(client)

    response = client.post(
        "/api/auth/login",
        json={"email": signed_up["email"], "password": signed_up["password"]},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["access_token"]
    assert body["refresh_token"]


def test_login_with_wrong_password_is_rejected(client):
    signed_up = signup(client)

    response = client.post(
        "/api/auth/login",
        json={"email": signed_up["email"], "password": "definitely-the-wrong-password"},
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Incorrect email or password"


def test_login_with_unregistered_email_is_rejected(client):
    response = client.post(
        "/api/auth/login",
        json={"email": unique_email(), "password": "whatever-password-123"},
    )

    assert response.status_code == 401


def test_refresh_token_issues_a_new_access_token(client):
    signed_up = signup(client)

    response = client.post(
        "/api/auth/refresh", json={"refresh_token": signed_up["refresh_token"]}
    )

    assert response.status_code == 200
    assert response.json()["access_token"]


def test_refresh_rejects_an_access_token_used_as_a_refresh_token(client):
    """access and refresh tokens carry a `type` claim precisely so one
    can't be replayed as the other - see app/core/security.py."""
    signed_up = signup(client)

    response = client.post(
        "/api/auth/refresh", json={"refresh_token": signed_up["access_token"]}
    )

    assert response.status_code == 401


# --- config-gated 503s: JWT / SMTP ----------------------------------------


def test_signup_returns_503_when_jwt_not_configured(client, monkeypatch):
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
    get_settings.cache_clear()

    response = client.post(
        "/api/auth/signup",
        json={"email": unique_email(), "password": VALID_TEST_PASSWORD},
    )

    assert response.status_code == 503
    assert response.json()["detail"]["error"] == "jwt_not_configured"


def test_login_returns_503_when_jwt_not_configured(client, monkeypatch):
    # Sign up successfully first (JWT configured at this point), then
    # remove the secret before attempting to log in.
    signed_up = signup(client)

    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
    get_settings.cache_clear()

    response = client.post(
        "/api/auth/login",
        json={"email": signed_up["email"], "password": signed_up["password"]},
    )

    assert response.status_code == 503
    assert response.json()["detail"]["error"] == "jwt_not_configured"


def test_forgot_password_returns_503_when_smtp_not_configured(client, monkeypatch):
    for var in ("SMTP_HOST", "SMTP_USERNAME", "SMTP_PASSWORD", "SMTP_FROM_EMAIL"):
        monkeypatch.delenv(var, raising=False)
    get_settings.cache_clear()

    response = client.post("/api/auth/forgot-password", json={"email": unique_email()})

    assert response.status_code == 503
    assert response.json()["detail"]["error"] == "smtp_not_configured"


def test_config_status_reflects_unset_groups(client, monkeypatch):
    monkeypatch.delenv("SMTP_HOST", raising=False)
    get_settings.cache_clear()

    response = client.get("/api/config/status")

    assert response.status_code == 200
    body = response.json()
    assert body["smtp"] is False
