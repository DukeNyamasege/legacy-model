from __future__ import annotations

import unittest

from app.bulk_response_member_reconciliation import (
    _normalize_nested_transaction_member,
    _reconciled_normalize_bulk_response,
)


class BulkResponseDeepMemberFixTests(unittest.TestCase):
    def setUp(self) -> None:
        self.request = {
            "accounts": [
                {"account_id": "DOT111", "token": "not-logged"},
            ]
        }

    def test_nested_response_buy_contract_is_promoted(self) -> None:
        response = {
            "data": {
                "transactions": [
                    {
                        "response": {
                            "buy": {
                                "contract_id": 1001,
                                "transaction_id": 2001,
                                "purchase_time": 1234567890,
                            }
                        }
                    }
                ]
            }
        }
        normalized = _reconciled_normalize_bulk_response(response, self.request)
        self.assertEqual(normalized["errors"], [])
        self.assertEqual(len(normalized["data"]["transactions"]), 1)
        item = normalized["data"]["transactions"][0]
        self.assertEqual(item["account_id"], "DOT111")
        self.assertEqual(int(item["contract_id"]), 1001)
        self.assertEqual(int(item["transaction_id"]), 2001)

    def test_nested_transaction_data_buy_contract_is_promoted(self) -> None:
        response = {
            "data": {
                "transactions": [
                    {
                        "transaction": {
                            "data": {
                                "buy": {
                                    "contractId": "1002",
                                    "transactionId": "2002",
                                }
                            }
                        }
                    }
                ]
            }
        }
        normalized = _reconciled_normalize_bulk_response(response, self.request)
        self.assertEqual(normalized["errors"], [])
        item = normalized["data"]["transactions"][0]
        self.assertEqual(item["account_id"], "DOT111")
        self.assertEqual(str(item["contract_id"]), "1002")

    def test_nested_member_error_is_preserved_as_real_provider_error(self) -> None:
        response = {
            "data": {
                "transactions": [
                    {
                        "response": {
                            "error": {
                                "status": 400,
                                "code": "BadInputRequest",
                                "message": "Token or account validation failed",
                            }
                        }
                    }
                ]
            }
        }
        normalized = _reconciled_normalize_bulk_response(response, self.request)
        self.assertEqual(normalized["data"]["transactions"], [])
        self.assertEqual(len(normalized["errors"]), 1)
        self.assertEqual(normalized["errors"][0]["account_id"], "DOT111")
        self.assertEqual(normalized["errors"][0]["code"], "BadInputRequest")

    def test_structurally_present_unknown_member_is_explicit_no_retry_error(self) -> None:
        response = {
            "data": {
                "transactions": [
                    {
                        "status": "processed",
                        "provider_reference": "opaque-reference",
                    }
                ]
            }
        }
        normalized = _reconciled_normalize_bulk_response(response, self.request)
        self.assertEqual(normalized["data"]["transactions"], [])
        self.assertEqual(len(normalized["errors"]), 1)
        self.assertEqual(normalized["errors"][0]["account_id"], "DOT111")
        self.assertEqual(normalized["errors"][0]["code"], "BULK_MEMBER_UNRESOLVED")
        self.assertIn("not retried", normalized["errors"][0]["message"])

    def test_generic_nested_id_is_never_promoted_to_contract_id(self) -> None:
        member, _shapes = _normalize_nested_transaction_member(
            {"response": {"buy": {"id": 999999, "transaction_id": 2003}}}
        )
        self.assertNotIn("contract_id", member)
        self.assertEqual(int(member["transaction_id"]), 2003)

    def test_nested_account_identity_is_preserved(self) -> None:
        member, _shapes = _normalize_nested_transaction_member(
            {
                "response": {
                    "account_id": "DOT111",
                    "buy": {"contract_id": 1004},
                }
            }
        )
        self.assertEqual(member["account_id"], "DOT111")
        self.assertEqual(int(member["contract_id"]), 1004)


if __name__ == "__main__":
    unittest.main()
