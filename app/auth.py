import hashlib
import hmac
import secrets
from datetime import datetime, timedelta
from typing import Optional
from fastapi import Cookie, Header, HTTPException, Request, status
from app.config import IST, SECRET_KEY, SESSION_TTL_DAYS
from app.database import get_db


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=16384, r=8, p=1, dklen=32)
    return f"scrypt${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        algo, salt_hex, dk_hex = stored_hash.split("$", 2)
        if algo != "scrypt":
            return False
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(dk_hex)
        actual = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=16384, r=8, p=1, dklen=32)
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


def hash_token(token: str) -> str:
    return hmac.new(SECRET_KEY.encode("utf-8"), token.encode("utf-8"), hashlib.sha256).hexdigest()


def generate_recovery_code() -> str:
    # Format: SV-XXXX-XXXX-XXXX
    raw = secrets.token_hex(6).upper()
    return f"SV-{raw[0:4]}-{raw[4:8]}-{raw[8:12]}"


def create_session(user_id: int, device_info: str = "Web Browser") -> str:
    raw_token = secrets.token_urlsafe(32)
    t_hash = hash_token(raw_token)
    now = datetime.now(IST)
    expires = now + timedelta(days=SESSION_TTL_DAYS)
    with get_db() as db:
        db.execute(
            """INSERT INTO sessions (token_hash, user_id, device_info, created_at, expires_at, revoked)
               VALUES (?, ?, ?, ?, ?, 0)""",
            (t_hash, user_id, device_info[:120], now.isoformat(), expires.isoformat()),
        )
    return raw_token


def revoke_session(raw_token: str) -> None:
    if not raw_token:
        return
    t_hash = hash_token(raw_token)
    with get_db() as db:
        db.execute("UPDATE sessions SET revoked = 1 WHERE token_hash = ?", (t_hash,))


def _extract_token(request: Request, sv_session: Optional[str], authorization: Optional[str]) -> Optional[str]:
    if authorization and authorization.lower().startswith("bearer "):
        return authorization.split(" ", 1)[1].strip()
    if sv_session:
        return sv_session
    return request.cookies.get("sv_session")


def get_user_from_token(raw_token: Optional[str]) -> Optional[dict]:
    if not raw_token:
        return None
    t_hash = hash_token(raw_token)
    now_iso = datetime.now(IST).isoformat()
    with get_db() as db:
        row = db.execute(
            """SELECT u.*, s.expires_at
               FROM sessions s
               JOIN users u ON u.id = s.user_id
               WHERE s.token_hash = ? AND s.revoked = 0 AND s.expires_at > ?""",
            (t_hash, now_iso),
        ).fetchone()
    if not row:
        return None
    user = dict(row)
    user.pop("password_hash", None)
    user.pop("recovery_hash", None)
    return user


async def get_current_user_optional(
    request: Request,
    sv_session: Optional[str] = Cookie(default=None),
    authorization: Optional[str] = Header(default=None),
) -> Optional[dict]:
    token = _extract_token(request, sv_session, authorization)
    return get_user_from_token(token)


async def require_user(
    request: Request,
    sv_session: Optional[str] = Cookie(default=None),
    authorization: Optional[str] = Header(default=None),
) -> dict:
    token = _extract_token(request, sv_session, authorization)
    user = get_user_from_token(token)
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    if user["ban_type"] == "perm":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"Account permanently banned: {user.get('ban_reason') or 'Policy violation'}")
    if user["ban_type"] == "temp" and user.get("ban_until"):
        if user["ban_until"] > datetime.now(IST).isoformat():
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"Account temporarily banned until {user['ban_until']}: {user.get('ban_reason') or ''}")
        else:
            with get_db() as db:
                db.execute("UPDATE users SET ban_type='none', ban_reason='', ban_until=NULL WHERE id=?", (user["id"],))
            user["ban_type"] = "none"
    if user["is_suspended"]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Account is suspended. Contact administrator.")
    return user


async def require_admin(
    request: Request,
    sv_session: Optional[str] = Cookie(default=None),
    authorization: Optional[str] = Header(default=None),
) -> dict:
    user = await require_user(request, sv_session, authorization)
    if user["role"] != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Super administrator access required")
    return user


async def require_mod_or_admin(
    request: Request,
    sv_session: Optional[str] = Cookie(default=None),
    authorization: Optional[str] = Header(default=None),
) -> dict:
    user = await require_user(request, sv_session, authorization)
    if user["role"] not in ("moderator", "admin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Moderator or administrator access required")
    return user
