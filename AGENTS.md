Be Brief.

Ignore zip, image, and media files in the repository.

## Current Project Shape

Primary Render Remote package:

- [Launch_RenderKit/render_remote/__init__.py](Launch_RenderKit/render_remote/__init__.py) - registration, public re-exports, handlers, singleton wiring
- [Launch_RenderKit/render_remote/constants.py](Launch_RenderKit/render_remote/constants.py) - protocol, auth, LAN, retry, and timeout constants
- [Launch_RenderKit/render_remote/paths.py](Launch_RenderKit/render_remote/paths.py) - path normalization, root enforcement, `FileFilter`
- [Launch_RenderKit/render_remote/protocol.py](Launch_RenderKit/render_remote/protocol.py) - message/file framing, structured errors, schema validation
- [Launch_RenderKit/render_remote/auth.py](Launch_RenderKit/render_remote/auth.py) - `SecureConnection`, auth state, TLS/certificate helpers, fingerprint pinning
- [Launch_RenderKit/render_remote/file_sync.py](Launch_RenderKit/render_remote/file_sync.py) - file hash/cache/sync helpers
- [Launch_RenderKit/render_remote/output_monitor.py](Launch_RenderKit/render_remote/output_monitor.py) - output manifest and render-output monitoring
- [Launch_RenderKit/render_remote/network.py](Launch_RenderKit/render_remote/network.py) - discovery, connection management, message handlers, render request orchestration
- [Launch_RenderKit/render_remote/render.py](Launch_RenderKit/render_remote/render.py) - render monitor and render-handler lifecycle
- [Launch_RenderKit/render_remote/timers.py](Launch_RenderKit/render_remote/timers.py) - main-thread timer/marshalling helpers
- [Launch_RenderKit/render_remote/ui.py](Launch_RenderKit/render_remote/ui.py) - operators, panels, property groups, preferences UI helpers
- [Launch_RenderKit/render_remote/handlers.py](Launch_RenderKit/render_remote/handlers.py) - load/exit cleanup and shutdown safety hooks

Other relevant files:

- [Launch_RenderKit/__init__.py](Launch_RenderKit/__init__.py) - addon preferences and top-level addon registration
- [Launch_RenderKit/blender_manifest.toml](Launch_RenderKit/blender_manifest.toml) - Blender extension metadata and permissions
- [Launch_RenderKit/render_region.py](Launch_RenderKit/render_region.py) - render-region panel/operator definitions
- [Launch_RenderKit/utility_log.py](Launch_RenderKit/utility_log.py) - logging integration
- [README.md](README.md) - user-facing setup/security notes
- [tests/test_render_remote.py](tests/test_render_remote.py) - current unit coverage

## Local Test Coverage

Complete and verify the test suite locally. Do not add or rely on GitHub Actions for this phase. The required command is:

```bash
python3 -m unittest discover -s tests
```

Local test goals:

- [ ] [CRIT] Path-security primitives. Cover `resolve_under_root` and `relative_path_under_root`: traversal attempts (`..`, encoded variants), absolute paths, Windows drive paths on POSIX, symlinks escaping root, non-existent leaf, and non-existent intermediate paths.
- [ ] [CRIT] Full `SecureConnection` suite:
  - `hash_password` deterministic with the same salt
  - `create_challenge` nonce uniqueness and cap eviction at `AUTH_MAX_CHALLENGES`
  - `consume_challenge` rejects wrong IP, wrong server nonce, expired challenge, and replay
  - `issue_auth_token` / `verify_auth_token` reject wrong IP, expired token, and malformed token
  - `cleanup_expired_auth` removes expired auth state and keeps valid state
- [ ] [HIGH] TLS tests: self-signed cert generation succeeds and is idempotent; fingerprint pinning accepts the known fingerprint; mismatching fingerprints are rejected; plaintext peers are rejected.
- [ ] [HIGH] Message-handler tests. For each `_handle_*` in `NetworkManager`, assert:
  - unauthenticated calls are rejected where required
  - malformed payloads return structured errors without crashing
  - path traversal through `relative_path` is refused
  - oversized files/messages are refused
- [ ] [HIGH] Render-request validation. Assert `_handle_render_request` rejects invalid resolutions, unknown render engines, out-of-root output paths, and a second render while one is already running.
- [ ] [MED] `send_file` / `recv_file`: partial reads, truncated streams, mid-transfer socket errors, and file smaller than declared size.
- [ ] [MED] `OutputFileMonitor` lifecycle: `start_monitoring` -> create file -> `_scan_for_new_files` picks it up -> manifest reflects it; quiet-period debounce behavior.
- [ ] [LOW] Optional local ergonomics only: add `tests/conftest.py` and either `pytest.ini` or `[tool.pytest.ini_options]` in `pyproject.toml` if it helps local `pytest` discovery. Preserve `python3 -m unittest discover -s tests` as the required suite entry point.

## Verification Plan

Use this after meaningful changes, especially while expanding Stage 6:

1. **Static:** `python3 -m py_compile Launch_RenderKit/*.py Launch_RenderKit/render_remote/*.py`
2. **Unit:** `python3 -m unittest discover -s tests`
3. **Extension validation:** on a Blender 5 install, `blender --command extension validate Launch_RenderKit`
4. **Manual LAN:** two Blender 5 instances on the same subnet. Verify discovery -> authenticate with correct and incorrect passcodes -> sync project -> remote render a frame -> sync output back.
5. **Online-access denial:** repeat discovery/startup with `bpy.app.online_access=False`; sockets must not open and the UI/logs should report the refusal cleanly.
6. **Cancellation:** start a remote render, cancel via UI and via closing Blender. Confirm no daemon threads or sockets leak.
7. **Security smoke tests:** send path traversal, oversized file/message, malformed JSON, and invalid render settings. Each should return a structured error without crashing the host.

## Implementation Notes

- Prefer the split package modules above; do not recreate the old monolithic `render_remote.py`.
- Keep using `TimerManager` for Blender main-thread marshalling.
- Keep using `FileFilter` and the path helpers for path filtering and root enforcement.
- Route log output through [Launch_RenderKit/utility_log.py](Launch_RenderKit/utility_log.py).
- Sanitize remote-supplied text before showing it in UI or logs.
- Use `error_response(code, message)` instead of ad-hoc `{'status': 'error', ...}` dictionaries.
- Preserve `python3 -m unittest discover -s tests` as the required local verification command. Pytest compatibility is optional and must not replace the unittest path.
