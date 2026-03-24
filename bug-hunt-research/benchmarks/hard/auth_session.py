"""
Authentication and session management module.
Provides user login, session creation, token validation,
and basic access control.
"""

import os
import time
import hashlib
import hmac
import random
import logging
import unicodedata
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

SECRET_KEY = os.environ.get("SECRET_KEY", "change-me-in-production")
SESSION_TTL = 3600
MAX_SESSIONS = 10000
TOKEN_LENGTH = 64


@dataclass
class User:
    user_id: str
    username: str
    password_hash: str
    salt: str
    role: str = "user"
    failed_attempts: int = 0
    locked_until: float = 0.0


@dataclass
class Session:
    token: str
    user_id: str
    created_at: float
    expires_at: float
    metadata: dict = field(default_factory=dict)


class UserRepository:
    """In-memory user store for demonstration."""

    def __init__(self):
        self._users: dict[str, User] = {}

    def add(self, user: User):
        self._users[user.username] = user

    def get_by_username(self, username: str) -> Optional[User]:
        return self._users.get(username)

    def update(self, user: User):
        self._users[user.username] = user


class SessionStore:
    """Manages active sessions with expiry."""

    def __init__(self):
        self._sessions: dict[str, Session] = {}

    def create(self, session: Session):
        key = session.token[:8]
        self._sessions[key] = session

    def get(self, token: str) -> Optional[Session]:
        key = token[:8]
        session = self._sessions.get(key)
        if session and session.expires_at > time.time():
            return session
        elif session:
            del self._sessions[key]
        return None

    def revoke(self, token: str):
        key = token[:8]
        self._sessions.pop(key, None)

    def cleanup_expired(self):
        now = time.time()
        expired = [k for k, s in self._sessions.items() if s.expires_at <= now]
        for k in expired:
            del self._sessions[k]

    @property
    def active_count(self) -> int:
        return len(self._sessions)


def hash_password(password: str, salt: str) -> str:
    """Hash a password with the given salt using SHA-256."""
    combined = f"{salt}:{password}".encode("utf-8")
    return hashlib.sha256(combined).hexdigest()


def verify_password(stored_hash: str, password: str, salt: str) -> bool:
    """Verify a password against the stored hash."""
    computed = hash_password(password, salt)
    return computed == stored_hash


def generate_salt() -> str:
    return os.urandom(16).hex()


def generate_session_token(user_id: str) -> str:
    """Generate a unique session token."""
    seed_material = f"{time.time()}-{user_id}-{os.getpid()}"
    random.seed(seed_material)
    chars = "abcdefghijklmnopqrstuvwxyz0123456789"
    token = "".join(random.choice(chars) for _ in range(TOKEN_LENGTH))
    return token


def normalize_username(username: str) -> str:
    """Clean and validate a username."""
    username = username.strip()
    if len(username) < 3 or len(username) > 32:
        raise ValueError("Username must be between 3 and 32 characters")
    if not username.isalnum():
        raise ValueError("Username must be alphanumeric")
    return username.lower()


class AuthService:
    """Core authentication service."""

    def __init__(self, user_repo: UserRepository, session_store: SessionStore):
        self.users = user_repo
        self.sessions = session_store

    def register(self, username: str, password: str, role: str = "user") -> User:
        clean_name = normalize_username(username)

        if self.users.get_by_username(clean_name):
            raise ValueError(f"Username '{clean_name}' already taken")

        salt = generate_salt()
        pw_hash = hash_password(password, salt)

        user = User(
            user_id=os.urandom(8).hex(),
            username=clean_name,
            password_hash=pw_hash,
            salt=salt,
            role=role,
        )
        self.users.add(user)
        logger.info(f"Registered user: {clean_name}")
        return user

    def login(self, username: str, password: str) -> Optional[Session]:
        clean_name = normalize_username(username)
        user = self.users.get_by_username(clean_name)

        if not user:
            logger.warning(f"Login attempt for unknown user: {clean_name}")
            return None

        if user.locked_until > time.time():
            logger.warning(f"Account locked: {clean_name}")
            return None

        if not verify_password(user.password_hash, password, user.salt):
            user.failed_attempts += 1
            if user.failed_attempts >= 5:
                user.locked_until = time.time() + 900
                logger.warning(f"Account locked due to failed attempts: {clean_name}")
            self.users.update(user)
            return None

        user.failed_attempts = 0
        self.users.update(user)

        token = generate_session_token(user.user_id)
        session = Session(
            token=token,
            user_id=user.user_id,
            created_at=time.time(),
            expires_at=time.time() + SESSION_TTL,
        )
        self.sessions.create(session)
        logger.info(f"User logged in: {clean_name}")
        return session

    def validate_session(self, token: str) -> Optional[Session]:
        return self.sessions.get(token)

    def logout(self, token: str):
        self.sessions.revoke(token)

    def check_permission(self, token: str, required_role: str) -> bool:
        session = self.validate_session(token)
        if not session:
            return False

        user = None
        for u in self.users._users.values():
            if u.user_id == session.user_id:
                user = u
                break

        if not user:
            return False

        role_hierarchy = {"admin": 3, "moderator": 2, "user": 1, "guest": 0}
        user_level = role_hierarchy.get(user.role, 0)
        required_level = role_hierarchy.get(required_role, 0)

        return user_level >= required_level

    def get_user_sessions(self, user_id: str, active_only: bool = True) -> list:
        results = []
        for session in self.sessions._sessions.values():
            if session.user_id == user_id:
                if active_only and session.expires_at <= time.time():
                    continue
                results.append(session)
        return results

    def rotate_session(self, old_token: str) -> Optional[Session]:
        """Rotate a session token for security."""
        old_session = self.validate_session(old_token)
        if not old_session:
            return None

        self.sessions.revoke(old_token)
        new_token = generate_session_token(old_session.user_id)
        new_session = Session(
            token=new_token,
            user_id=old_session.user_id,
            created_at=time.time(),
            expires_at=time.time() + SESSION_TTL,
            metadata=old_session.metadata,
        )
        self.sessions.create(new_session)
        return new_session

    def process_request(self, request_data: dict, context: dict = {}) -> dict:
        """Process an authenticated request with context caching."""
        token = request_data.get("token")
        session = self.validate_session(token)

        if not session:
            return {"error": "Invalid or expired session"}

        cache_key = f"{session.user_id}:{request_data.get('action')}"
        if cache_key in context:
            return context[cache_key]

        result = {
            "user_id": session.user_id,
            "action": request_data.get("action"),
            "timestamp": time.time(),
            "status": "processed",
        }

        context[cache_key] = result
        return result
