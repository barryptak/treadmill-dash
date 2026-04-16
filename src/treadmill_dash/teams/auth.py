"""Microsoft 365 authentication via MSAL.

Uses WAM (Windows Account Manager) broker on Windows for seamless SSO
with the account you're already signed into Windows with, satisfying
Conditional Access device compliance policies.

Falls back to device code flow on non-Windows platforms.

Reuses the Clawpilot app registration (99fa64eb) which already has the
required Graph delegated permissions.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional

import msal

log = logging.getLogger(__name__)

CLIENT_ID = "99fa64eb-feda-4f94-aecd-30637ca7bf2d"
TENANT_ID = "72f988bf-86f1-41af-91ab-2d7cd011db47"
AUTHORITY = f"https://login.microsoftonline.com/{TENANT_ID}"

# Scopes — must match what's already admin-consented on the app registration.
# Presence.Read requires separate admin consent, so we use the /me/presence
# endpoint with just User.Read for now.
SCOPES = [
    "User.Read",
    "Chat.ReadWrite",
]

CACHE_DIR = Path.home() / ".treadmill-dash"
CACHE_FILE = CACHE_DIR / "msal-cache.json"

IS_WINDOWS = sys.platform == "win32"


class M365Auth:
    """Handles M365 token acquisition with WAM broker + device code fallback."""

    def __init__(self) -> None:
        self._cache = msal.SerializableTokenCache()
        self._load_cache()

        kwargs: dict = {
            "client_id": CLIENT_ID,
            "authority": AUTHORITY,
            "token_cache": self._cache,
        }

        # Enable WAM broker on Windows for SSO + device compliance
        if IS_WINDOWS:
            kwargs["enable_broker_on_windows"] = True
            kwargs["allow_broker"] = True

        self._app = msal.PublicClientApplication(**kwargs)
        self._account: Optional[dict] = None
        self._restore_account()

    # -- Cache persistence --

    def _load_cache(self) -> None:
        if CACHE_FILE.exists():
            try:
                self._cache.deserialize(CACHE_FILE.read_text())
            except Exception:
                log.warning("Failed to load MSAL cache, starting fresh")

    def _save_cache(self) -> None:
        if self._cache.has_state_changed:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            CACHE_FILE.write_text(self._cache.serialize())

    def _restore_account(self) -> None:
        accounts = self._app.get_accounts()
        if accounts:
            self._account = accounts[0]
            log.info(f"Restored account: {self._account['username']}")

    # -- Public API --

    @property
    def signed_in(self) -> bool:
        return self._account is not None

    @property
    def username(self) -> Optional[str]:
        return self._account["username"] if self._account else None

    @property
    def display_name(self) -> Optional[str]:
        if self._account:
            return self._account.get("name") or self._account.get("username")
        return None

    def acquire_token(self, scopes: Optional[list[str]] = None) -> str:
        """Acquire an access token. Tries silent first, then interactive.

        On Windows: uses WAM broker (satisfies device compliance policies).
        On other platforms: falls back to device code flow.

        Returns the raw access token string.
        Raises RuntimeError on failure.
        """
        scopes = scopes or SCOPES

        # Try silent acquisition first (cached / refresh token)
        if self._account:
            result = self._app.acquire_token_silent(scopes, account=self._account)
            if result and "access_token" in result:
                self._save_cache()
                return result["access_token"]
            log.info("Silent token acquisition failed, trying interactive...")

        # Interactive auth
        if IS_WINDOWS:
            result = self._acquire_via_broker(scopes)
        else:
            result = self._acquire_via_device_code(scopes)

        if "access_token" not in result:
            error = result.get("error_description") or result.get("error", "Unknown error")
            raise RuntimeError(f"M365 sign-in failed: {error}")

        accounts = self._app.get_accounts()
        if accounts:
            self._account = accounts[0]

        self._save_cache()
        log.info(f"Signed in as: {self.username}")
        return result["access_token"]

    def _acquire_via_broker(self, scopes: list[str]) -> dict:
        """Interactive auth via WAM broker (Windows only)."""
        log.info("Authenticating via WAM broker...")
        return self._app.acquire_token_interactive(
            scopes,
            prompt="select_account",
            parent_window_handle=self._app.CONSOLE_WINDOW_HANDLE,
        )

    def _acquire_via_device_code(self, scopes: list[str]) -> dict:
        """Interactive auth via device code flow (non-Windows fallback)."""
        flow = self._app.initiate_device_flow(scopes)
        if "user_code" not in flow:
            return {"error": "device_flow_failed",
                    "error_description": flow.get("error_description", "Unknown")}

        print()
        print(f"  To sign in, visit: {flow['verification_uri']}")
        print(f"  Enter code: {flow['user_code']}")
        print()

        return self._app.acquire_token_by_device_flow(flow)

    def sign_out(self) -> None:
        """Clear cached account and tokens."""
        if self._account:
            username = self._account.get("username", "unknown")
            self._app.remove_account(self._account)
            self._account = None
            self._save_cache()
            log.info(f"Signed out: {username}")

    def get_graph_token(self) -> str:
        """Convenience: acquire a Graph API token."""
        return self.acquire_token(SCOPES)
