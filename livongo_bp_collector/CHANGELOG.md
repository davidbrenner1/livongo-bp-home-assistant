# Changelog

## 0.2.9
- Added support for automated direct Teladoc credentials login (`username` / `password` options or `!secret`).
- Added automatic re-authentication fallback when session expires.
- Resolved Playwright child process reaping issues by allowing native signal tracking.

## 0.2.8
- Captured and persisted full React SPA `sessionStorage` alongside cookies and `localStorage`.

## 0.2.7
- Bypassed premature `member.teladoc.com` idle session redirect and navigated directly to blood pressure logs.

## 0.2.6
- Added proactive keep-alive token renewal (`/v1/users/me/web/refresh/v2` and `/utils/chronic_care/sso`).

## 0.2.5
- Added persistent Mac login helper with automated SSH sync fallback.
- Migrated default member portal URLs and Referers from deprecated Livongo endpoints to Teladoc Health (`my.teladoc.com` / `member.teladoc.com/signin`).
- Added multi-domain cookie and local storage state persistence across `teladoc.com` and `livongo.com`.

## 0.2.3
- Enabled `init: true` in `config.yaml` to ensure Docker `tini` process reaper prevents `<defunct>` zombie process accumulation.
- Added a 90-second watchdog execution timeout around `fetch_livongo_readings()` to guarantee `sync_lock` is never held permanently if browser IPC hangs.
- Added automatic cleanup of orphan/defunct Chromium processes and kernel-level `SIGCHLD` reaping.
- Gracefully handled Home Assistant API connection errors during Supervisor reboots.

## 0.2.2
- Block third-party tracking scripts (Mixpanel, New Relic, Apptentive, etc.) to prevent socket exhaustion and `ERR_SOCKET_NOT_CONNECTED` network errors in Docker.
- Added navigation retry loop (up to 3 attempts with exponential backoff) with `wait_until="commit"`.

## 0.2.1
- Automatically persist refreshed Playwright browser session state & cookies back to disk on each sync cycle to maintain active authentication.
- Added JSON payload support to the `/upload` endpoint for automated session uploads.

## 0.2.0
- Switched distribution to a prebuilt GHCR multi-architecture image.
- Added automated GitHub Actions publishing for amd64 and aarch64.
- Retained Livongo JSON API collector, Home Assistant sensors, ingress UI, SQLite storage, and event replay.

## 0.1.0
- Initial local-build version.
