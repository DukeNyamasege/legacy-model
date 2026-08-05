from __future__ import annotations

import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "app" / "websocket_hot_path_hardening.py"
WORKER = ROOT / "app" / "worker.py"


class WebSocketHotPathSourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.source = SOURCE.read_text(encoding="utf-8")
        cls.worker = WORKER.read_text(encoding="utf-8")
        ast.parse(cls.source)

    def test_public_reader_is_isolated_from_tick_processing(self) -> None:
        self.assertIn("PUBLIC_TICK_QUEUE_SIZE", self.source)
        self.assertIn("_public_tick_reader", self.source)
        self.assertIn("_public_tick_consumer", self.source)
        reader = self.source.split("async def _public_tick_reader", 1)[1].split(
            "async def _public_tick_consumer", 1
        )[0]
        self.assertNotIn("await client.bot._on_tick", reader)
        self.assertIn("queue.put_nowait", reader)

    def test_protocol_ping_is_replaced_by_staggered_application_ping(self) -> None:
        self.assertGreaterEqual(self.source.count("ping_interval=None"), 2)
        self.assertGreaterEqual(self.source.count('{"ping": 1}'), 2)
        self.assertIn("APP_HEARTBEAT_MIN_SECONDS", self.source)
        self.assertIn("APP_HEARTBEAT_MAX_SECONDS", self.source)
        self.assertIn("APP_HEARTBEAT_MISSES", self.source)

    def test_proposals_have_nonfinancial_private_websocket_fallback(self) -> None:
        self.assertIn("PROPOSAL_ROUTE_FALLBACK", self.source)
        self.assertIn("PROPOSAL_ROUTE_RECOVERED", self.source)
        self.assertIn("financial_requests=0", self.source)
        self.assertIn("No financial buy request was sent", self.source)

    def test_purchases_are_bounded_but_remain_per_account_websocket(self) -> None:
        self.assertIn("PRIVATE_WS_BUY_CONCURRENCY", self.source)
        self.assertIn("asyncio.Semaphore(PRIVATE_WS_BUY_CONCURRENCY)", self.source)
        self.assertIn("grouped._buy_one_serialized = paced_buy_one", self.source)
        self.assertIn("private_websocket_only=true", self.source)
        self.assertIn("bulk_purchase=false", self.source)
        self.assertIn("copy_trading=false", self.source)

    def test_repetitive_database_work_moves_off_event_loop(self) -> None:
        self.assertIn("ThreadPoolExecutor", self.source)
        self.assertIn("_install_background_tick_flush", self.source)
        self.assertIn("_install_coalesced_settlements", self.source)
        self.assertIn("asyncio.to_thread(bot.repository.record_candidate", self.source)
        self.assertIn(
            "asyncio.to_thread(\n            bot.repository.record_proposal",
            self.source,
        )

    def test_contract_metadata_no_longer_competes_with_proposals(self) -> None:
        self.assertIn("RF_CONTRACT_CAPABILITY_CACHE", self.source)
        self.assertIn("metadata_requests=0", self.source)
        self.assertIn("provider_proposal_and_buy_authoritative=true", self.source)
        cache_function = self.source.split(
            "async def proposal_authoritative_contract_cache", 1
        )[1].split("def _schedule_group_cache_refresh", 1)[0]
        self.assertNotIn("contracts_for", cache_function)

    def test_worker_installs_hardening_after_final_execution_layers(self) -> None:
        self.assertIn(
            "from app.websocket_hot_path_hardening import (",
            self.worker,
        )
        self.assertIn("install_websocket_hot_path_hardening()", self.worker)
        self.assertGreater(
            self.worker.index("install_websocket_hot_path_hardening()"),
            self.worker.index("install_scalable_group_execution_hardening()"),
        )


if __name__ == "__main__":
    unittest.main()
