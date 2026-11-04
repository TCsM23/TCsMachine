# Changelog

The section for each version becomes the release notes the in-app updater shows.

## 0.3.0 · 2026-11-04

- **Plan mode.** The unit investigates read-only, then files a plan. You approve it, or send it back with a note, before anything changes. After approval it switches to the mode you choose (`plan_then`).
- **Specialist units.** Define sub-units in `units/*.md` with their own instructions, model and tools, and the unit dispatches them by name. Any write tools they have still go through your approvals. Edit them from the Skills screen.
- **Review pass.** Optional (`review = true`): after a directive that changed files, a read-only unit reviews the diff. Its findings appear in the log, and the unit sees them with your next directive.
- **Background jobs.** `run_command(background=true)` for dev servers, watchers and long test runs, plus `job_status` and `job_stop`. Jobs show in a side panel with their output. They are approval-gated like any command, time-limited, and stopped when PALE exits.
- **File watchers.** Schedule a directive with `watch src/**/*.py` to run it when matching files change. The run's own edits don't retrigger it, and each watcher has a cooldown and an hourly cap.
- **Rewind.** Fork any session from just before an earlier directive (the REWIND button on your messages, or `/rewind` in the terminal). The original session is kept.
- **Loop guard.** A unit that repeats the identical call is warned on the 3rd try and stopped on the 5th.
- **Archive search** across every session's text and tool output, a **context meter**, and **OS notifications** for approvals, questions and reports while the window is in the background.
- **Fixes.** `web_search` in a new `pale.toml` was written under `[hooks]` and silently ignored. The desktop store now creates its folder before its first save.

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
