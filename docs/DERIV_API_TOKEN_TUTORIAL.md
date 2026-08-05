# Deriv API token setup for bulk purchase trading

Users must connect their own Deriv API token before the platform can include their account in REST bulk purchase execution.

## What the user should do

1. Log in to Deriv.
2. Open **Security & limits**.
3. Open **API token**.
4. Create a token with **trade** permission enabled.
5. Copy the token.
6. Return to the platform and open **Settings > Credentials**.
7. Paste the token and save it.
8. Start or resume auto trading.

## Why this is required

The official Deriv REST bulk purchase endpoint buys the same contract for many accounts in one request, but authentication is still per account. Each account entry must include the end user's own API token with trade permission and the account ID it owns. Accounts without a valid token are skipped individually and do not stop the remaining accounts.

## Bulk execution architecture

Execution is grouped in two stages:

1. **Logical partition first.** Accounts are separated by account type, strategy group, side, role, market, contract type, barrier, duration and stake. Examples: System Over 1 normal, System Over 3 recovery, System Over 4 post-virtual, manual Even, manual Odd, manual Over, Rise, and Fall all become separate partitions when their contract parameters differ.
2. **100-account REST request shards second.** Inside each logical partition, accounts are split into REST bulk-purchase requests of at most 100 accounts. For example, 300 accounts in the same Over 1 partition become 3 REST requests; 600 accounts become 6 REST requests. The partition identity remains the same, but every 100-account shard has its own batch ID and purchase log.

If a partition shard contains only one account, that account is treated as its own master context for that request. This avoids forcing a lonely account to wait for another account and avoids seed/connection issues. In larger shards, the first active account in the ordered shard is stored as the batch master context only for logging and batch identity; every account still trades with its own API token, own account ID and own stake.

## Dashboard wording

Use this wording for users:

> Please link your Deriv API token with trade scope in Settings > Credentials. How to get it: open Deriv, go to Security & limits, open API token, create a token with trade permission, then paste it here.

A hyperlink to the Deriv API token page can be added later where this message is displayed.
