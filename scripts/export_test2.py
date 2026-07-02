from __future__ import annotations

import os

from app.config import load_test2_config
from app.database import Database
from app.services.analytics_service import export_test2


def main() -> None:
    config = load_test2_config(os.getenv("DERIV_BOT_CONFIG", "config.yaml"))
    database = Database(config.database_url)
    database.create_schema()
    summary = export_test2(
        database, config.model.run_id, config.storage.export_directory
    )
    print(
        f"Exported {summary['purchased_trades']} Test 2 trades to "
        f"{config.storage.export_directory}"
    )


if __name__ == "__main__":
    main()
