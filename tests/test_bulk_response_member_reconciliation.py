from __future__ import annotations

import unittest
from pathlib import Path

from app.bulk_response_member_reconciliation import (
    _canonicalize_response,
    _positionally_correlate_transaction_members,
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

    def test_data_successes_list_is_promoted_to_transactions(self) -> None:
        response = {
            "data": {
                "successes": [
                    {"account_id": "DOT111", "contractId": "1001", "transactionId": "2001"},
                    {"account_id": "DOT222", "contractId": "1002", "transactionId": "2002"},
                ]
            }
        }
        normalized = _reconciled_normalize_bulk_response(response, self.request)
        transactions = normalized["data"]["transactions"]
        self.assertEqual(
            {(item["account_id"], str(item["contract_id"])) for item in transactions},
            {("DOT111", "1001"), ("DOT222", "1002")},
        )
        self.assertEqual(normalized["errors"], [])

    def test_data_members_mapping_is_promoted_without_losing_account_identity(self) -> None:
        response = {
            "data": {
                "members": {
                    "DOT111": {"contractId": "1001", "transactionId": "2001"},
                    "DOT222": {"contractId": "1002", "transactionId": "2002"},
                }
            }
        }
        canonical, shapes = _canonicalize_response(response)
        self.assertIn("data.members:mapping", shapes)
        self.assertIn("data.members:promoted_to_transactions", shapes)
        normalized = _reconciled_normalize_bulk_response(response, self.request)
        transactions = normalized["data"]["transactions"]
        self.assertEqual(
            {(item["account_id"], str(item["contract_id"])) for item in transactions},
            {("DOT111", "1001"), ("DOT222", "1002")},
        )

    def test_canonical_transactions_win_over_success_aliases(self) -> None:
        response = {
            "data": {
                "transactions": [
                    {"account_id": "DOT111", "contract_id": "1001"},
                    {"account_id": "DOT222", "contract_id": "1002"},
                ],
                "successes": [
                    {"account_id": "DOT111", "contract_id": "duplicate"},
                ],
            }
        }
        canonical, shapes = _canonicalize_response(response)
        self.assertNotIn("data.successes:promoted_to_transactions", shapes)
        self.assertEqual(
            [str(item["contract_id"]) for item in canonical["data"]["transactions"]],
            ["1001", "1002"],
        )

    def test_mixed_transaction_success_and_member_error_are_correlated_by_request_position(self) -> None:
        response = {
            "data": {
                "transactions": [
                    {"contractId": "1001", "transactionId": "2001"},
                    {
                        "error": {
                            "code": "BadInputRequest",
                            "message": "Token or account validation failed",
                        }
                    },
                ]
            }
        }
        normalized = _reconciled_normalize_bulk_response(response, self.request)
        self.assertEqual(len(normalized["data"]["transactions"]), 1)
        self.assertEqual(normalized["data"]["transactions"][0]["account_id"], "DOT111")
        self.assertEqual(str(normalized["data"]["transactions"][0]["contract_id"]), "1001")
        self.assertEqual(len(normalized["errors"]), 1)
        self.assertEqual(normalized["errors"][0]["account_id"], "DOT222")
        self.assertEqual(normalized["errors"][0]["code"], "BadInputRequest")

    def test_flat_member_error_in_transactions_is_correlated_by_request_position(self) -> None:
        response = {
            "data": {
                "transactions": [
                    {"contract_id": "1001", "transaction_id": "2001"},
                    {
                        "code": "BadInputRequest",
                        "message": "Account type mismatch",
                    },
                ]
            }
        }
        normalized = _reconciled_normalize_bulk_response(response, self.request)
        self.assertEqual(normalized["data"]["transactions"][0]["account_id"], "DOT111")
        self.assertEqual(normalized["errors"][0]["account_id"], "DOT222")
        self.assertEqual(normalized["errors"][0]["message"], "Account type mismatch")

    def test_partial_explicit_identity_is_filled_only_when_position_agrees(self) -> None:
        response = {
            "data": {
                "transactions": [
                    {"account_id": "DOT111", "contract_id": "1001"},
                    {"error": {"code": "InvalidToken", "message": "expired"}},
                ]
            }
        }
        correlated, shapes = _positionally_correlate_transaction_members(
            response,
            self.request,
        )
        self.assertIn("data.transactions:request_position_account_id", shapes)
        self.assertEqual(correlated["data"]["transactions"][0]["account_id"], "DOT111")
        self.assertEqual(correlated["data"]["transactions"][1]["account_id"], "DOT222")

    def test_explicit_position_conflict_disables_positional_inference(self) -> None:
        response = {
            "data": {
                "transactions": [
                    {"account_id": "DOT222", "contract_id": "1001"},
                    {"error": {"code": "InvalidToken", "message": "expired"}},
                ]
            }
        }
        correlated, shapes = _positionally_correlate_transaction_members(
            response,
            self.request,
        )
        self.assertEqual(shapes, [])
        self.assertEqual(correlated["data"]["transactions"][0]["account_id"], "DOT222")
        self.assertNotIn("account_id", correlated["data"]["transactions"][1])

    def test_partial_transaction_array_is_never_positionally_guessed(self) -> None:
        response = {
            "data": {
                "transactions": [
                    {"contract_id": "1001"},
                ]
            }
        }
        correlated, shapes = _positionally_correlate_transaction_members(
            response,
            self.request,
        )
        self.assertEqual(shapes, [])
        self.assertNotIn("account_id", correlated["data"]["transactions"][0])

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
        self.assertIn("success_aliases=true", module_source)
        self.assertIn("positional_transaction_members=safe_exact_cardinality", module_source)
        self.assertIn("BULK_RESPONSE_POSITIONAL_CORRELATION", module_source)


if __name__ == "__main__":
    unittest.main()
