# Security Policy

## Supported use

Ledger Agent is designed for a local, single-user setup. The Web service binds to `127.0.0.1` by default.

Do not expose it through `0.0.0.0`, a reverse proxy, port forwarding, or a public server unless you add authentication, HTTPS, access control, backups, and an independent security review.

## Sensitive data

Never commit or attach any of the following to an issue, pull request, release, or screenshot:

- `.env`, API keys, bearer tokens, or provider credentials.
- `.financial_agent/ledger.db` and its backups.
- Chat attachments, exports, application logs, or Windows `%LOCALAPPDATA%\LedgerAgent` files.

Before publishing logs, redact names, merchants, account identifiers, balances, request headers, and dates that could identify a person.

## Reporting a vulnerability

Please use GitHub's private security advisory feature for this repository. If private reporting is unavailable, open a minimal issue that contains no exploit details or personal financial data and ask for a private contact channel.

Do not include API keys, real ledger databases, or personal screenshots in a report.
