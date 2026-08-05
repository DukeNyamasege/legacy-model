from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_final_alert_route_is_installed_by_last_api_hardening_layer() -> None:
    final_source = (ROOT / "app" / "final_execution_alert_api.py").read_text(
        encoding="utf-8"
    )
    hardening_source = (ROOT / "app" / "database_runtime_hardening.py").read_text(
        encoding="utf-8"
    )

    assert 'ROUTE_PATH = "/me/execution-alert"' in final_source
    assert "app.add_api_route(" in final_source
    assert 'methods=["GET"]' in final_source
    assert 'name="final_personal_execution_alert"' in final_source
    assert "FINAL_EXECUTION_ALERT_ROUTE_INSTALLED" in final_source
    assert "FINAL_EXECUTION_ALERT_ROUTE_INVALID" in final_source
    assert "count != 1" in final_source
    assert "install_final_execution_alert_api(app)" in hardening_source
    assert hardening_source.index("install_final_execution_alert_api(app)") < hardening_source.index(
        "_INSTALLED = True", hardening_source.index("install_final_execution_alert_api(app)")
    )


def test_final_alert_route_remains_account_scoped() -> None:
    source = (ROOT / "app" / "final_execution_alert_api.py").read_text(
        encoding="utf-8"
    )

    assert "base_api.get_current_account(request)" in source
    assert "Trade.managed_account_id == managed_id" in source
    assert "read_strategy(base_api.DATABASE, managed_id)" in source
    assert "_matches_strategy(signal, selection)" in source
    assert "_candidate_alert(" in source
    assert 'raise HTTPException(status_code=401, detail="Not authenticated")' in source
