# Changelog

The section for each version becomes the release notes the in-app updater shows.

## 0.2.0 · 2026-10-30

- **Desktop app.** Native PALE window with its own icon, a Windows Setup wizard with a desktop shortcut, and a first-run wizard (API key, sector folder, unit, model, budget).
- **Built-in updater.** Checks GitHub Releases, shows these notes, verifies the download's SHA-256, installs silently and relaunches. You can skip a version or turn automatic checks off.
- **Sectors.** Several workspaces, switchable from the top bar or the command palette.
- **MCP servers.** Configure stdio servers in `pale.toml`; their tools go through the same approvals and black box as built-ins.
- **Git tools.** `git_status`, `git_diff`, `git_log`, and `git_commit` with approval.
- **Attachments.** Drop or paste files and screenshots into a directive. Images are shown to the model.
- **Web search.** Optional server-side search, off by default.
- **New screens.** File explorer with viewer, settings editor, 30-day spend chart, field reports, session rename/export/delete, editable memory, quick directives, Ctrl+K command palette, night theme, UI sounds.

## 0.1.0 · 2026-10-28

- First release: agent loop, sandboxed tools, approval modes, black box, budget caps, secret redaction, undo, memory, quarantined skills, scheduler, Telegram gateway, web terminal, themed CLI.
