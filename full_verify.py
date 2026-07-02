import asyncio
import json
import sys

import enhanced_bot
import websockets


async def verify_configuration() -> int:
    cfg = enhanced_bot.load_config("config.yaml")
    tokens = enhanced_bot.decrypt_tokens(
        enhanced_bot.load_tokens(cfg["files"]["tokens"]),
        cfg["deriv"].get("token_encryption_key", ""),
    )

    if not tokens:
        print("No PAT found. Add a token to tokens.txt or configure DERIV_TOKEN.")
        return 1

    token = tokens[0]
    app_id = str(cfg["deriv"]["app_id"])
    base_url = str(cfg["deriv"]["rest_base_url"])
    public_ws_url = str(cfg["deriv"]["public_ws_url"])

    print("=== Deriv New API Verification ===\n")
    print(f"App ID: {app_id}")
    print(f"REST Base URL: {base_url}")
    print(f"Public WS URL: {public_ws_url}\n")

    accounts_response = await enhanced_bot._rest_request(
        "GET",
        "/trading/v1/options/accounts",
        app_id,
        base_url,
        token=token,
    )
    if "error" in accounts_response:
        print(f"[FAIL] Account verification failed: {accounts_response['error'].get('message')}")
        return 1

    accounts = accounts_response.get("data", [])
    print(f"[OK] PAT authenticated. Found {len(accounts)} account(s).")
    if not accounts:
        print("[FAIL] No accounts returned for the PAT.")
        return 1

    account_id = accounts[0]["account_id"]
    otp_response = await enhanced_bot._rest_request(
        "POST",
        f"/trading/v1/options/accounts/{account_id}/otp",
        app_id,
        base_url,
        token=token,
        json_data={},
    )
    if "error" in otp_response:
        print(f"[FAIL] OTP request failed: {otp_response['error'].get('message')}")
        return 1

    otp_url = otp_response.get("data", {}).get("url", "")
    print(
        "[OK] OTP endpoint returned a private WebSocket URL for "
        f"{enhanced_bot.mask_account_id(account_id)}."
    )

    try:
        async with websockets.connect(public_ws_url) as ws:
            await ws.send(json.dumps({"active_symbols": "brief"}))
            raw_response = await ws.recv()
            data = json.loads(raw_response)
    except Exception as exc:
        print(f"[FAIL] Public WebSocket test failed: {exc}")
        return 1

    if "active_symbols" not in data:
        print(f"[FAIL] Unexpected public WebSocket response: {data}")
        return 1

    print(f"[OK] Public WebSocket connected. {len(data['active_symbols'])} active symbols returned.")
    print("[OK] Private WebSocket URL was not printed because it contains a one-time credential.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(verify_configuration()))
