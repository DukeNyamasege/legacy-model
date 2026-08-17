from __future__ import annotations

"""One-time operator reset for a clean Deriv login/execution baseline.

This intentionally preserves managed-account rows, strategy preferences, balances,
and trade history. It removes only live browser/OAuth sessions and hard-stops every
account so the next explicit Start begins with fresh recovery state.
"""

from sqlalchemy import delete, func, or_, select

import app.api as base_api
from app.lifecycle_reset_authority import _hard_stop
from app.models import ClientSession, ManagedAccount, OAuthLoginState, RuntimePreference
from app.services.telegram_admin import _admin_chat_id, _bot_token
from app.telegram_silence import telegram_notifications_suspended


RESET_REASON = (
    "Administrator clean reset: all browser sessions were invalidated and Auto Trade "
    "was stopped. Log in again and press Start for a fresh base-stake session."
)


def _count(session, model) -> int:
    return int(session.scalar(select(func.count()).select_from(model)) or 0)


def main() -> None:
    with base_api.DATABASE.session() as session:
        managed_rows = session.scalars(
            select(ManagedAccount).order_by(ManagedAccount.id).with_for_update()
        ).all()
        browser_sessions_before = _count(session, ClientSession)
        oauth_states_before = _count(session, OAuthLoginState)

        stopped = 0
        cancelled_virtual = 0
        for row in managed_rows:
            cancelled_virtual += int(
                _hard_stop(
                    session,
                    row,
                    reason=RESET_REASON,
                    mark_history_reset=False,
                )
            )
            stopped += 1

        # Every existing HttpOnly client_session cookie becomes invalid on its next
        # request. In-progress/old PKCE callbacks are also invalidated, forcing a
        # completely fresh OAuth login instead of finishing a pre-reset flow.
        session.execute(delete(ClientSession))
        session.execute(delete(OAuthLoginState))

        # Allow the next Auto Trade start to generate a fresh Telegram lifecycle
        # message. Preserve admin chat discovery/update offsets and strategy prefs.
        session.execute(
            delete(RuntimePreference).where(
                or_(
                    RuntimePreference.preference_key.like("telegram_real_session:%"),
                    RuntimePreference.preference_key.like("telegram_account_session:%"),
                    RuntimePreference.preference_key.like("telegram_admin_last_alert:%"),
                    RuntimePreference.preference_key.like("telegram_autotrade_start:%"),
                )
            )
        )

    try:
        base_api.REPOSITORY.set_status(
            "STOPPED",
            "Administrator clean-session reset; waiting for fresh user logins",
        )
    except Exception:
        pass

    try:
        base_api.REPOSITORY.audit(
            "ADMIN_ALL_USER_SESSIONS_RESET",
            "vps-root-operator",
            "localhost",
            {
                "managed_accounts_stopped": stopped,
                "browser_sessions_deleted": browser_sessions_before,
                "oauth_states_deleted": oauth_states_before,
                "cancelled_open_virtual": cancelled_virtual,
                "trade_history_preserved": True,
                "strategy_settings_preserved": True,
            },
        )
    except Exception:
        pass

    telegram_token_ready = bool(_bot_token(base_api.CONFIG.telegram))
    telegram_chat_ready = bool(_admin_chat_id(base_api.REPOSITORY))
    telegram_suspended = bool(telegram_notifications_suspended())

    print("ADMIN_CLEAN_SESSION_RESET_COMPLETE")
    print(f"managed_accounts_stopped={stopped}")
    print(f"browser_sessions_deleted={browser_sessions_before}")
    print(f"oauth_login_states_deleted={oauth_states_before}")
    print(f"open_virtual_observations_cancelled={cancelled_virtual}")
    print("trade_history_preserved=true")
    print("strategy_settings_preserved=true")
    print(f"telegram_bot_token_configured={str(telegram_token_ready).lower()}")
    print(f"telegram_admin_chat_configured={str(telegram_chat_ready).lower()}")
    print(f"telegram_notifications_suspended={str(telegram_suspended).lower()}")


if __name__ == "__main__":
    main()
