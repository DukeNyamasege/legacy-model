from __future__ import annotations

# Install canonical fixed-base accounting before app.api creates the repository.
# The API and worker therefore read the same account-independent model ledger and
# cannot reintroduce debt-sized Martingale replay in dashboard calculations.
from app.hybrid_safety import install_hybrid_accounting_integrity

install_hybrid_accounting_integrity()

from app.api_account_lifecycle import app  # noqa: E402
from app.database_runtime_hardening import (  # noqa: E402
    install_database_runtime_hardening,
)
from app.production_integration_hardening import (  # noqa: E402
    install_production_integration_hardening,
)

# Install the final OAuth/dashboard integration first, then add the final
# database-aware health and exception boundary. No later wrapper may replace
# these production routes or return raw SQLAlchemy connection tracebacks.
install_production_integration_hardening(app)
install_database_runtime_hardening(app)
