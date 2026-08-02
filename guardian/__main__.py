from __future__ import annotations

import logging
import os
import sys

from .config import GuardianConfig
from .service import GuardianService


def main() -> int:
    logging.basicConfig(
        level=getattr(logging, os.getenv("GUARDIAN_LOG_LEVEL", "INFO").upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    try:
        service = GuardianService(GuardianConfig.from_env())
        service.run()
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception:
        logging.getLogger("legacy_model.guardian").exception("GUARDIAN_FATAL")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
