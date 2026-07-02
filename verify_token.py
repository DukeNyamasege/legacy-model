import asyncio
import sys

import enhanced_bot


async def verify_token() -> int:
    cfg = enhanced_bot.load_config("config.yaml")
    tokens_path = cfg["files"]["tokens"]
    tokens = enhanced_bot.decrypt_tokens(
        enhanced_bot.load_tokens(tokens_path),
        cfg["deriv"].get("token_encryption_key", ""),
    )

    if not tokens:
        print("No PAT found. Add a token to tokens.txt or configure DERIV_TOKEN.")
        return 1

    token = tokens[0]
    app_id = str(cfg["deriv"]["app_id"])
    base_url = str(cfg["deriv"]["rest_base_url"])

    print("=== Deriv PAT Verification ===\n")
    response = await enhanced_bot._rest_request(
        "GET",
        "/trading/v1/options/accounts",
        app_id,
        base_url,
        token=token,
    )

    if "error" in response:
        print(f"[FAIL] Token verification failed: {response['error'].get('message')}")
        return 1

    accounts = response.get("data", [])
    print(f"[OK] Token authenticated. Found {len(accounts)} account(s).")
    for account in accounts:
        account_id = account.get("account_id", "unknown")
        account_type = account.get("account_type", "unknown")
        balance = float(account.get("balance", 0.0))
        print(
            f" - {enhanced_bot.mask_account_id(account_id)} "
            f"({account_type}): ${balance:.2f}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(verify_token()))
