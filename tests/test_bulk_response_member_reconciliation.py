from __future__ import annotations

import unittest
from pathlib import Path

from app.bulk_response_member_reconciliation import (
    _canonicalize_response,
    _reconciled_normalize_bulk_response,
)


ROOT = Path(__file__).resolve().parents[1]
PRODUCTION = ROOT / "app" / "production_worker_integration.py"


class BulkResponseMemberReconciliationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.request = {
            "accounts": [
                {"account_id": "DOT111", "token": "not-logged-1"},
                {"account_id": "DOT222", "token": "not-logged-2"},
            ]
        }

    def test_account_keyed_transactions_become_member_list(self) -> None:
        response = {
            "data": {
                "transactions": {
                    "DOT111": {"contractId": "1001", "transactionId": "2001"},
                    "DOT222": {"contractId": "1002", "transactionId": "2002"},
                }
            }
        }
        normalized = _reconciled_normalize_bulk_response(response, self.request)
        transactions = normalized["data"]["transactions"]
        self.assertEqual(
            {(item["account_id"], str(item["contract_id"])) for item in transactions},
            {("DOT111", "1001"), ("DOT222", "1002")},
        )
        self.assertEqual(normalized["errors"], [])

    def test_account_keyed_success_and_error_are_both_preserved(self) -> None:
        response = {
            "data": {
                "transactions": {
                    "DOT111": {"contract_id": "1001", "transaction_id": "2001"}
                },
                "errors": {
                    "DOT222": {
                        "code": "BadInputRequest",
                        "message": "Token or account validation failed",
                    }
                },
            }
        }
        normalized = _reconciled_normalize_bulk_response(response, self.request)
        self.assertEqual(
            normalized["data"]["transactions"][0]["account_id"],
            "DOT111",
        )
        self.assertEqual(normalized["errors"][0]["account_id"], "DOT222")
        self.assertEqual(normalized["errors"][0]["code"], "BadInputRequest")

    def test_top_level_account_keyed_errors_are_supported(self) -> None:
        canonical, shapes = _canonicalize_response(
            {
                "errors": {
                    "DOT111": {"code": "InvalidToken", "message": "expired"},
                    "DOT222": {"code": "BadInputRequest", "message": "wrong account"},
                }
            }
        )
        self.assertEqual(len(canonical["errors"]), 2)
        self.assertIn("errors:mapping", shapes)
        self.assertEqual(
            {item["account_id"] for item in canonical["errors"]},
            {"DOT111", "DOT222"},
        )

    def test_installer_is_after_seamless_normalizer(self) -> None:
        source = PRODUCTION.read_text(encoding="utf-8")
        self.assertIn("install_bulk_response_member_reconciliation", source)
        self.assertLess(
            source.index("install_seamless_execution_runtime()"),
            source.index("install_bulk_response_member_reconciliation()"),
        )
        module_source = (
            ROOT / "app" / "bulk_response_member_reconciliation.py"
        ).read_text(encoding="utf-8")
        self.assertIn("credentials_logged=false", module_source)
        self.assertIn("duplicate_retry=false", module_source)


if __name__ == "__main__":
    unittest.main()
