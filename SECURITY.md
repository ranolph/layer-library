# Security

## Intended deployment

Layer Library is a self-hosted application for a trusted local network. It has no
user-account authentication or separation between users. Anyone who can reach
the application can use its features and access its stored content.

Keep it on a trusted network or behind an authenticated private access service.
A public GitHub repository shares the source code; it does not make an
installation suitable for unrestricted public access.

## Keep installation data out of the repository

Store application data in the configured data directory or Docker volume.
Do not commit live databases, backups, uploads, printer access codes or your
local `.env` file. The included ignore files cover common locations, but review
new files before committing them.

## Report a vulnerability

If the repository's **Security** tab offers **Report a vulnerability**, use that
private reporting channel. Otherwise, open an issue asking the maintainer for a
private contact method without including exploit details or credentials.

Include the application version, installation method, affected behaviour and a
minimal reproduction with synthetic data. The project does not promise a
response time or a formal security support period.

## Updates

Review releases and dependency updates before upgrading. Back up the data
directory before changes that affect persisted data, and test an upgrade using
a copy of the data when practical.

