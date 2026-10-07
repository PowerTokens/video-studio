# Security Policy

## Reporting a vulnerability

Please report security issues **privately** by email to [service@powertokens.ai](mailto:service@powertokens.ai). Do not open a public issue, pull request or Discord post for them.

Helpful details:

- The affected version (window title, e.g. `v1.11`) and whether you use the EXE, the source, the CLI or the MCP server.
- What an attacker could do and the steps to reproduce it.
- Any proof of concept, with real keys removed.

We will acknowledge your report, keep you updated while we work on a fix and credit you in the release notes if you wish.

## Protect your API key

- **Never post an API key in issues, discussions, pull requests, screenshots or logs.** Blur or remove keys and Task IDs before sharing anything.
- If a key may have been exposed, delete it in your [PowerTokens dashboard](https://powertokens.ai/api-keys?utm_source=github&utm_medium=oss&utm_campaign=video-studio) and create a new one right away.
- The CLI config (`cli-config.json`) stores keys in plain text. Prefer the `POWERTOKENS_API_KEY` / `POWERTOKENS_API_KEYS` environment variables and never commit that file.

## Supported versions

Security fixes go into the latest release. Please update to the newest version from the [Releases](../../releases/latest) page before reporting.

For how the app handles keys and download links, see [Security notes](README.md#security-notes) in the README.
