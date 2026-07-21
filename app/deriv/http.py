from __future__ import annotations


def mask_app_id(app_id: str) -> str:
    value = str(app_id or "").strip()
    if len(value) <= 7:
        return "***"
    return f"{value[:4]}...{value[-3:]}"


def deriv_headers(app_id: str, *, bearer_token: str = "") -> dict[str, str]:
    value = str(app_id or "").strip()
    if not value:
        raise RuntimeError("DERIV_APP_ID is required")
    headers = {
        "Deriv-App-ID": value,
        "Content-Type": "application/json",
    }
    token = str(bearer_token or "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers
