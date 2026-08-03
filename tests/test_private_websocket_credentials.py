from __future__ import annotations

import unittest

from enhanced_bot import private_websocket_credential_from_payload


class PrivateWebSocketCredentialTests(unittest.TestCase):
    def test_trade_scoped_oauth_access_token_is_valid_for_private_websocket(self) -> None:
        self.assertEqual(
            private_websocket_credential_from_payload(
                {
                    "auth_type": "oauth",
                    "access_token": "oauth-access-token",
                    "scope": "read trade",
                }
            ),
            "oauth-access-token",
        )

    def test_oauth_without_trade_scope_is_not_accepted_for_purchase(self) -> None:
        self.assertEqual(
            private_websocket_credential_from_payload(
                {
                    "auth_type": "oauth",
                    "access_token": "oauth-access-token",
                    "scope": "read",
                }
            ),
            "",
        )

    def test_pat_remains_valid_for_private_websocket(self) -> None:
        self.assertEqual(
            private_websocket_credential_from_payload(
                {"auth_type": "pat", "pat_token": "personal-access-token"}
            ),
            "personal-access-token",
        )


if __name__ == "__main__":
    unittest.main()
