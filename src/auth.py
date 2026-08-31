"""
auth.py — Authentication & Authorization Layer
================================================
Provides:
  - Secure user registration with bcrypt password hashing
  - Login validation against the SQLite User table
  - Streamlit session state injection on successful auth
  - Strict AuthZ: all downstream queries must be filtered by user_id

Design:
  - Passwords are NEVER stored in plaintext. Only bcrypt digests are persisted.
  - Session state key 'user_id' is the single source of truth for identity.
  - 'authenticated' flag is set alongside 'user_id' for quick guard checks.
"""

import logging
from typing import Optional

import bcrypt
import streamlit as st

from src.database import (
    User,
    create_user,
    get_user_by_username,
    init_db,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Password utilities
# ---------------------------------------------------------------------------

def hash_password(plain_password: str) -> str:
    """
    Hash a plain-text password using bcrypt (cost factor 12).

    Returns:
        A utf-8 bcrypt digest string suitable for DB storage.
    """
    salt = bcrypt.gensalt(rounds=12)
    digest = bcrypt.hashpw(plain_password.encode("utf-8"), salt)
    return digest.decode("utf-8")


def verify_password(plain_password: str, password_hash: str) -> bool:
    """
    Verify a plain-text password against a stored bcrypt hash.

    Returns:
        True if the password is correct, False otherwise.
    """
    return bcrypt.checkpw(
        plain_password.encode("utf-8"),
        password_hash.encode("utf-8"),
    )


# ---------------------------------------------------------------------------
# Registration & Authentication
# ---------------------------------------------------------------------------

def register_user(username: str, email: str, password: str) -> tuple[bool, str]:
    """
    Register a new user.

    Args:
        username: Desired username (3–32 chars, alphanumeric + underscores).
        email:    Email address (must be unique).
        password: Plain-text password (min 8 chars).

    Returns:
        (success: bool, message: str)
    """
    # --- Validation ---
    username = username.strip()
    email    = email.strip().lower()
    password = password.strip()

    if len(username) < 3:
        return False, "Username must be at least 3 characters."
    if len(username) > 32:
        return False, "Username must be at most 32 characters."
    if not username.replace("_", "").isalnum():
        return False, "Username may only contain letters, numbers, and underscores."
    if "@" not in email or "." not in email.split("@")[-1]:
        return False, "Please enter a valid email address."
    if len(password) < 8:
        return False, "Password must be at least 8 characters."

    # --- Persist ---
    try:
        password_hash = hash_password(password)
        create_user(username=username, email=email, password_hash=password_hash)
        logger.info("Registered new user: %s", username)
        return True, f"Account created for **{username}**. You can now log in."
    except ValueError as e:
        return False, str(e)
    except Exception as e:
        logger.error("Registration failed for %s: %s", username, e)
        return False, "Registration failed due to a server error. Please try again."


def authenticate_user(username: str, password: str) -> Optional[User]:
    """
    Validate credentials and return the User object if correct.

    Args:
        username: Username string.
        password: Plain-text password.

    Returns:
        User object on success, None on failure.
    """
    user = get_user_by_username(username.strip())
    if user is None:
        logger.debug("Login failed: username '%s' not found.", username)
        return None
    if not user.is_active:
        logger.warning("Login attempt for deactivated account: %s", username)
        return None
    if not verify_password(password, user.password_hash):
        logger.debug("Login failed: incorrect password for '%s'.", username)
        return None
    logger.info("User authenticated: %s (id=%d)", username, user.id)
    return user


# ---------------------------------------------------------------------------
# Session State Management
# ---------------------------------------------------------------------------

def login_session(user: User) -> None:
    """
    Inject user identity into Streamlit session state.

    Sets:
        st.session_state["authenticated"]  = True
        st.session_state["user_id"]        = user.id
        st.session_state["username"]       = user.username
        st.session_state["email"]          = user.email
    """
    st.session_state["authenticated"] = True
    st.session_state["user_id"]       = user.id
    st.session_state["username"]      = user.username
    st.session_state["email"]         = user.email
    logger.info("Session started for user_id=%d (%s)", user.id, user.username)


def logout_session() -> None:
    """
    Clear all authentication and analysis state from the session.
    """
    keys_to_clear = [
        "authenticated", "user_id", "username", "email",
        "current_project_id", "current_project_name",
        "requirements", "endpoints_v1", "endpoints_v2",
        "req_embeddings", "ep_embeddings_v1", "ep_embeddings_v2",
        "matrix_v1", "matrix_v2", "drift_report", "drift_kpis",
        "compliance_summary_v1", "embedder", "explainer", "pr_reviewer",
        "analysis_done", "ground_truth", "eval_report", "per_req_metrics",
    ]
    for key in keys_to_clear:
        st.session_state.pop(key, None)
    logger.info("Session cleared.")


def is_authenticated() -> bool:
    """Return True if the current session has a valid authenticated user."""
    return bool(st.session_state.get("authenticated", False)) and \
           st.session_state.get("user_id") is not None


def get_current_user_id() -> int:
    """
    Return the authenticated user's ID.
    Raises RuntimeError if called without an active authenticated session.
    """
    if not is_authenticated():
        raise RuntimeError(
            "get_current_user_id() called without an authenticated session. "
            "Ensure is_authenticated() is checked before calling this function."
        )
    return st.session_state["user_id"]


def assert_project_owner(project_id: int, user_id: int) -> None:
    """
    Authorization guard: raise PermissionError if the project doesn't belong to the user.

    Usage:
        assert_project_owner(project_id=42, user_id=get_current_user_id())
    """
    from src.database import get_project
    project = get_project(project_id=project_id, user_id=user_id)
    if project is None:
        raise PermissionError(
            f"Access denied: project {project_id} does not belong to user {user_id}."
        )


# ---------------------------------------------------------------------------
# Streamlit Auth UI
# ---------------------------------------------------------------------------

def render_auth_page() -> None:
    """
    Render the full authentication page with Login and Register tabs.
    On successful login, updates session state and triggers st.rerun().
    """
    # Page styling
    st.markdown("""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');
    html, body, [class*="css"] { font-family: 'Inter', sans-serif; }
    .auth-container {
        max-width: 440px;
        margin: 3rem auto;
        padding: 2.5rem;
        background: linear-gradient(145deg, #ffffff, #f8f9ff);
        border: 1px solid #e8eaf6;
        border-radius: 16px;
        box-shadow: 0 8px 40px rgba(26,35,126,0.10);
    }
    .auth-logo {
        text-align: center;
        font-size: 2.5rem;
        margin-bottom: 0.5rem;
    }
    .auth-title {
        text-align: center;
        font-size: 1.4rem;
        font-weight: 700;
        color: #1a237e;
        margin-bottom: 1.5rem;
    }
    </style>
    """, unsafe_allow_html=True)

    col_l, col_m, col_r = st.columns([1, 2, 1])
    with col_m:
        st.markdown('<div class="auth-logo">🔍</div>', unsafe_allow_html=True)
        st.markdown(
            '<div class="auth-title">AI Traceability Engine</div>',
            unsafe_allow_html=True,
        )

        tab_login, tab_register = st.tabs(["🔑 Sign In", "📝 Create Account"])

        # ── Login Tab ──────────────────────────────────────────────────────
        with tab_login:
            with st.form("login_form", clear_on_submit=False):
                username = st.text_input(
                    "Username", placeholder="your_username", key="login_username"
                )
                password = st.text_input(
                    "Password", type="password", placeholder="••••••••", key="login_password"
                )
                submitted = st.form_submit_button(
                    "Sign In", type="primary", use_container_width=True
                )

            if submitted:
                if not username or not password:
                    st.error("Please enter both username and password.")
                else:
                    user = authenticate_user(username, password)
                    if user:
                        login_session(user)
                        st.success(f"Welcome back, **{user.username}**! 🎉")
                        st.rerun()
                    else:
                        st.error("❌ Incorrect username or password.")

        # ── Register Tab ───────────────────────────────────────────────────
        with tab_register:
            with st.form("register_form", clear_on_submit=True):
                new_username = st.text_input(
                    "Choose a Username", placeholder="alice_dev", key="reg_username"
                )
                new_email = st.text_input(
                    "Email Address", placeholder="alice@company.com", key="reg_email"
                )
                new_password = st.text_input(
                    "Password (min 8 chars)", type="password",
                    placeholder="••••••••", key="reg_password"
                )
                confirm_password = st.text_input(
                    "Confirm Password", type="password",
                    placeholder="••••••••", key="reg_confirm"
                )
                submitted_reg = st.form_submit_button(
                    "Create Account", type="primary", use_container_width=True
                )

            if submitted_reg:
                if new_password != confirm_password:
                    st.error("❌ Passwords do not match.")
                else:
                    success, message = register_user(new_username, new_email, new_password)
                    if success:
                        st.success(f"✅ {message}")
                    else:
                        st.error(f"❌ {message}")
