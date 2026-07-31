from __future__ import annotations

# Install canonical fixed-base accounting before app.api creates the repository.
# The API and worker therefore read the same account-independent model ledger and
# cannot reintroduce debt-sized Martingale replay in dashboard calculations.
from app.hybrid_safety import install_hybrid_accounting_integrity

install_hybrid_accounting_integrity()

from app.api_account_lifecycle import app  # noqa: E402
from app.production_integration_hardening import (  # noqa: E402
    install_production_integration_hardening,
)

# This must be the last API installer. It replaces the final OAuth, dashboard,
# WebSocket and deployment-health boundaries after all legacy wrappers are loaded.
install_production_integration_hardening(app)
