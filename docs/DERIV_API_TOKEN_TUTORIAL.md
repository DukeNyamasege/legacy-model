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

## Dashboard wording

Use this wording for users:

> Please link your Deriv API token with trade scope in Settings > Credentials. How to get it: open Deriv, go to Security & limits, open API token, create a token with trade permission, then paste it here.

A hyperlink to the Deriv API token page can be added later where this message is displayed.
