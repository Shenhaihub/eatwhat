"""Create e2e user + dump session JSON for AuthContext localStorage injection."""
from __future__ import annotations

import json
import os

os.environ.pop("APP_ENV", None)
from supabase import create_client

from app.core.config import Settings

s = Settings(_env_file=".env")
service_role = (
    s.supabase_service_role_key.get_secret_value()
    if hasattr(s.supabase_service_role_key, "get_secret_value")
    else s.supabase_service_role_key
)
sb = create_client(s.supabase_url, service_role)

EMAIL = "e2e-user@example.com"
PWD = "E2E-Pass-1234!"

sess = None
try:
    r = sb.auth.sign_up({"email": EMAIL, "password": PWD})
    print("SIGN_UP uid:", r.user.id if r.user else None)
    sess = r.session
except Exception as e:  # noqa: BLE001
    print("sign_up skip:", type(e).__name__, e)
    sess = None

if not sess:
    r = sb.auth.sign_in_with_password({"email": EMAIL, "password": PWD})
    sess = r.session

user = sess.user
obj = {
    "provider_token": None,
    "provider_refresh_token": None,
    "access_token": sess.access_token,
    "refresh_token": sess.refresh_token,
    "expires_in": sess.expires_in,
    "expires_at": int(sess.expires_at) if sess.expires_at else None,
    "token_type": "bearer",
    "user": {
        "id": str(user.id),
        "aud": user.aud,
        "role": user.role,
        "email": user.email,
        "email_confirmed_at": (
            user.email_confirmed_at.isoformat()
            if getattr(user, "email_confirmed_at", None)
            else None
        ),
        "phone": getattr(user, "phone", None),
        "confirmed_at": (
            user.confirmed_at.isoformat()
            if getattr(user, "confirmed_at", None)
            else None
        ),
        "last_sign_in_at": (
            user.last_sign_in_at.isoformat()
            if getattr(user, "last_sign_in_at", None)
            else None
        ),
        "app_metadata": dict(user.app_metadata or {}),
        "user_metadata": dict(user.user_metadata or {}),
        "identities": [],
        "created_at": user.created_at.isoformat() if user.created_at else None,
        "updated_at": (
            user.updated_at.isoformat() if getattr(user, "updated_at", None) else None
        ),
    },
}
out = os.path.join(os.path.dirname(__file__), "_e2e_session.json")
with open(out, "w", encoding="utf-8") as f:
    json.dump(obj, f, ensure_ascii=False, indent=2)
print("DONE ->", out)
print("uid:", obj["user"]["id"])
print("email:", obj["user"]["email"])
