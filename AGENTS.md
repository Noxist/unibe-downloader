# ILIAS downloader agent instructions

Follow the server security policy, project standard, parent `AGENTS.md`, and this project's `README.md`.

- Never print session state, rclone credentials, cookies, TOTP/password data, JWTs, or signed media URLs.
- Preserve the source archive and indexes; never replace `rclone copy` with destructive sync.
- Operate user systemd units as `leandro` and touch only the affected unit.
- Do not copy `.auth`, sessions, indexes, or logs into Drive/review bundles.

The README is the operational source of truth.
