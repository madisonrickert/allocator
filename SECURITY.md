# Security posture

`allocator` handles read-only brokerage and exchange credentials. This document describes how they are stored and the threat model assumptions.

## Credential storage

Credentials are stored exclusively in the operating system keyring via the [`keyring`](https://pypi.org/project/keyring/) Python library.

- **macOS**: Apple Keychain (the only officially supported platform for now)
- **Linux**: Secret Service (GNOME Keyring, KWallet) — best-effort, not primary target
- **Windows**: Windows Credential Locker — best-effort

Keyring entries use the service namespace `allocator/` followed by the data source (e.g. `allocator/monarch`). Values are the raw credential blobs — typically JSON for multi-field credentials.

**Credentials are never:**
- written to disk in plaintext
- logged, even at debug level
- included in error messages (all API errors are sanitized first)
- committed to the repository

The `allocator setup` wizard is the only supported way to save credentials.

## API scopes

- **Monarch**: username/password or session token. The `monarchmoneycommunity` library requires read access to accounts and holdings; no write operations are used.

A direct Coinbase API integration is tracked for a later release. When added, it will use read-only scopes (`wallet:accounts:read`, `wallet:user:read`) with no trade or transfer permissions. In 0.1, Monarch's aggregated Coinbase feed is the only crypto path and is not guaranteed to produce per-coin ticker rows.

## Threat model

`allocator` is designed for a single trusted user running the tool locally on their own machine. It is **not** designed to defend against:

- a malicious user with a shell on the same machine (they can read keyring entries for the same user account)
- network-level attacks on the macOS keyring daemon
- supply-chain compromise of upstream dependencies (mitigation: lockfile + pinned versions)

It **is** designed to avoid:

- accidental credential commit to git (pre-commit secret scanning + `.gitignore`)
- credential leakage via error messages or logs
- read-only scope meaning that a leaked credential cannot move funds

## Reporting a vulnerability

Please use GitHub's private Security Advisory flow:
**[github.com/madisonrickert/allocator/security/advisories/new](https://github.com/madisonrickert/allocator/security/advisories/new)**

Do not open a public issue for security bugs. I'll acknowledge within 72 hours.
