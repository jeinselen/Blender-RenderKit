# AGENTS.md — Render Remote Development Checklist

Review of the Render Remote feature on branch `feature/RenderRemote` after 9 refactor commits. Deliverables and fixes are grouped into six development stages; work top-to-bottom. Severity tags: `[CRIT]`, `[HIGH]`, `[MED]`, `[LOW]`.

Primary files: [Launch_RenderKit/render_remote.py](Launch_RenderKit/render_remote.py) (~4411 lines), [Launch_RenderKit/__init__.py](Launch_RenderKit/__init__.py), [Launch_RenderKit/blender_manifest.toml](Launch_RenderKit/blender_manifest.toml), [tests/test_render_remote.py](tests/test_render_remote.py).

Line numbers are approximate — they reflect the state of the branch at the time of review and may drift as fixes land.

## Scope & assumptions

- Target Blender: `blender_version_min = 4.5.0`, `blender_version_max = 5.9.9` per the manifest. Blender 5 is the near-term target.
- Feature is intended for **trusted LAN** use. LAN-only peer filtering (`LAN_ALLOWED_NETWORKS` at [render_remote.py:39-48](Launch_RenderKit/render_remote.py#L39)) is kept as defense-in-depth — it is not a substitute for auth or transport security.
- **Real TLS** is in scope (Stage 3). Passcode + HMAC challenge-response stays as the authentication layer on top of TLS.
- The **subpackage split** (Stage 5) is in scope in this pass, as a dedicated mechanical commit.

---

## Stage 1 — Blender 5 / Extension-platform compliance

Intent: the addon installs and registers cleanly under Blender 5's extension rules.

- [ ] [HIGH] Add the `network` permission to [blender_manifest.toml](Launch_RenderKit/blender_manifest.toml). The `[permissions]` block is currently commented out at lines 46-48 but the feature opens UDP and TCP sockets:

  ```toml
  [permissions]
  network = "Discovery and render coordination with other Blender instances on the LAN"
  files = "Saving videos, incremental images, and render time logs"
  clipboard = "Copy and paste output variables"
  ```

- [ ] [HIGH] Gate all outbound networking behind `bpy.app.online_access`. Required by Blender's extension rules whenever `network` permission is declared. Check at `NetworkManager.start_discovery_server`, `start_communication_server`, `discover_nodes`, and every `_create_connection` call site. When online access is off, log a clear notice and bail — do not open sockets.
- [ ] [MED] Replace Blender version comparisons with capability checks. At [render_remote.py:1292](Launch_RenderKit/render_remote.py#L1292) the current branch is `node.base_path if bpy.app.version < tuple([5, 0, 0]) else node.directory`. Rewrite as `getattr(node, 'directory', None) or getattr(node, 'base_path', None)`. Grep `bpy.app.version` throughout the module and apply the same pattern to any other hits.
- [ ] [MED] Audit every `Panel`, `Operator`, `PropertyGroup`, and `AddonPreferences` class registered from `render_remote.py` and `__init__.py` for an explicit `bl_idname`, and confirm operator `bl_idname`s use the `category.name` dot form. Main panel (`REMOTERENDER_PT_main_panel` at ~3855) and operators (e.g. `render_remote.start_discovery` at ~3150) look correct. **Verify `RENDER_PT_render_region` in [Launch_RenderKit/render_region.py](Launch_RenderKit/render_region.py)** — reviewer flagged a missing `bl_idname` on that class; add if absent.
- [ ] [LOW] Remove the stray `bl_info = {'version': (1, 0, 2)}` at [render_remote.py:22-24](Launch_RenderKit/render_remote.py#L22). `bl_info` is pre-extension (Blender 2.80-era) metadata; extensions derive version from `blender_manifest.toml`. It is dead weight and misleads readers.

---

## Stage 2 — Thread safety & Blender main-thread discipline

Intent: `bpy` is not thread-safe. All Blender state access must be funneled to the main thread; shared mutable state across threads needs locking.

- [ ] [CRIT] Audit every `bpy.context`, `bpy.data`, and `bpy.ops` access reachable from a daemon thread and move it to the main thread via `bpy.app.timers.register(..., first_interval=0)`, or cache the value on the main thread before the worker starts. Starting points:
  - discovery loop (~[render_remote.py:1617](Launch_RenderKit/render_remote.py#L1617))
  - communication accept loop (~[render_remote.py:1665](Launch_RenderKit/render_remote.py#L1665))
  - per-client handler thread (~[render_remote.py:1702](Launch_RenderKit/render_remote.py#L1702))
  - monitor loop (~[render_remote.py:1247](Launch_RenderKit/render_remote.py#L1247))
  - preferences read at ~[render_remote.py:1418](Launch_RenderKit/render_remote.py#L1418)

  The existing `TimerManager` is the right vehicle — extend it, don't reinvent.

- [ ] [HIGH] `SecureConnection` ([render_remote.py:866-973](Launch_RenderKit/render_remote.py#L866)) mutates `auth_tokens` and `auth_challenges` from multiple threads without synchronization. Add a `threading.Lock` (or `RLock`) and wrap `create_challenge`, `consume_challenge`, `issue_auth_token`, `verify_auth_token`, `cleanup_expired_auth`, and any revoke helpers.
- [ ] [HIGH] Replace the plain-bool `NetworkManager.is_rendering` with a proper state primitive. It is used as a coordination gate (blocks discovery shutdown, guards cancellation) but is mutated from handlers, timers, and worker threads. Options: a `threading.Event`, or a small state machine `'idle' | 'preparing' | 'rendering' | 'stopping'` guarded by a `Lock`. Rewrite the call sites at `stop_discovery_server`, `render_cancel_handler`, `_execute_render_request`, and the `is_rendering` read sites.
- [ ] [MED] `OutputFileMonitor.output_manifest` has partial locking via `manifest_lock`. Audit every read/write path — `_final_sync_scan` and a couple of getters copy the dict without holding the lock. Wrap every access.
- [ ] [MED] Replace blocking `time.sleep(1.0)` in daemon loops with `threading.Event().wait(timeout=1.0)` so shutdown interrupts sleep immediately. Applies to `_monitor_loop` (~[render_remote.py:1247](Launch_RenderKit/render_remote.py#L1247)) and any other polling loop in the file.
- [ ] [MED] Audit every `thread.join()` for a `timeout` argument. Most already have one; make sure no shutdown path can block `unregister()` indefinitely.

---

## Stage 3 — Security hardening

Intent: authentication, transport, path handling, and payload validation must hold up against an untrusted peer on the same LAN.

- [ ] [HIGH] **Implement real TLS** on top of the existing challenge-response auth. `SecureConnection.create_ssl_context` at [render_remote.py:878-883](Launch_RenderKit/render_remote.py#L878) currently disables hostname checking and sets `verify_mode = CERT_NONE`, and is never actually used — the TCP server and clients run in the clear. Replace with:
  - Generate a long-lived self-signed cert+key on first launch per host. Store under `bpy.utils.user_resource('CONFIG', 'render_remote/')` with `0600` permissions on POSIX. Expose "regenerate certificate" and "show fingerprint" buttons in the Render Remote preferences.
  - Split `create_ssl_context` into `server_ssl_context()` (loads host cert+key) and `client_ssl_context(expected_fingerprint)` (`ssl.create_default_context()` with `check_hostname=False` because peers are IPs, **and** custom verification of the peer certificate's SHA-256 fingerprint against a TOFU-pinned value). **Never** set `verify_mode = CERT_NONE`.
  - Wrap every accepted TCP socket: `ssl_sock = ctx.wrap_socket(client_sock, server_side=True)`. Wrap every outbound client socket in `_create_connection` with the pinned-fingerprint client context.
  - TOFU flow: first connect to a new node → prompt user to confirm fingerprint → store. Subsequent mismatches refuse the connection and surface a "fingerprint changed" error in the UI.
  - UDP discovery stays plaintext but carries only IP/port/node-name/fingerprint advertisement. All auth and file transfer moves over the TLS-wrapped TCP channel.
  - `recv_message` / `send_message` / `send_file` / `recv_file` APIs are unchanged — they just receive the wrapped socket.
  - Stage 6 tests cover cert generation, fingerprint accept/reject, and refusal of plaintext peers.

- [ ] [HIGH] Harden `resolve_under_root` at [render_remote.py:319-330](Launch_RenderKit/render_remote.py#L319). This is the single gate protecting file sync and file-request endpoints. Replace the `os.path.commonpath` string compare with `candidate.is_relative_to(root)` (Python 3.9+; Blender 4.5 ships 3.11). After `.resolve()`, also walk the resolved path's parents to confirm no component resolves to a symlink that escapes `root`. Add the same scrutiny to `relative_path_under_root` at ~[render_remote.py:332](Launch_RenderKit/render_remote.py#L332).
- [ ] [HIGH] Validate inbound render settings. `_handle_render_request` (~[render_remote.py:2060](Launch_RenderKit/render_remote.py#L2060)) and `_apply_render_settings` (~[render_remote.py:2557](Launch_RenderKit/render_remote.py#L2557)) apply remote-supplied values directly to `scene.render`. Introduce a whitelist + bounds check:
  - `resolution_x` / `resolution_y` in `[1, 16384]`
  - `engine` must be in the set of registered render engines
  - output path must resolve under an allowed project root
  - `frame_start`, `frame_end`, `frame_step` sane and bounded
  - sample counts / time limits bounded

  Reject the whole request on any violation with a structured error.

- [ ] [HIGH] Add message-schema validation. `recv_message` returns raw JSON; every `_handle_*` handler must validate expected keys, types, and bounds before using them. Define a small `validate_message(msg, schema)` helper in `protocol.py` (Stage 5 split) and apply at the top of each handler.
- [ ] [HIGH] Verify downloaded file hashes. `NetworkManager.request_file_from_target` (~[render_remote.py:2400](Launch_RenderKit/render_remote.py#L2400)) receives an `expected_hash` in the response but never compares it to `file_sync_manager.calculate_file_hash(download_path)` after write. Add the comparison; on mismatch, delete the file and return failure.
- [ ] [HIGH] Replace bare `except:` clauses with specific exception types. Known sites: [render_remote.py:700](Launch_RenderKit/render_remote.py#L700), 1658, 1721, 1766, 2116, 2147, 2185, 3867. Bare `except` masks `KeyboardInterrupt`, `SystemExit`, and genuine bugs. Use `rg '^\s*except:\s*$' Launch_RenderKit/` to find any remaining.
- [ ] [MED] Credential storage threat-model note. `remote_passcode` ([__init__.py:211-217](Launch_RenderKit/__init__.py#L211)), `email_password` (~[__init__.py:380-384](Launch_RenderKit/__init__.py#L380)), and Pushover keys (~[__init__.py:406-415](Launch_RenderKit/__init__.py#L406)) use `StringProperty(subtype='PASSWORD')`. Blender stores preferences in plaintext on disk; `PASSWORD` only masks the UI widget. Keep the existing on-screen warning **and** add a one-paragraph threat-model note in the preferences panel and [README.md](README.md). Add `BLENDER_REMOTE_PASSCODE` (and equivalents for email / Pushover) as environment-variable overrides so users with stricter needs can avoid on-disk storage.
- [ ] [MED] Replace `print(...)` network logging with the `logging` module routed through [Launch_RenderKit/utility_log.py](Launch_RenderKit/utility_log.py). No log line may include a raw passcode, auth token, client/server nonce, or absolute filesystem path outside the project root. Audit sites around ~1737, 1747, 1754, 2202, 2210, 2216.
- [ ] [MED] Use `hmac.compare_digest` wherever proofs, tokens, or hashes are compared. Confirm in `_handle_authenticate` (~[render_remote.py:1827](Launch_RenderKit/render_remote.py#L1827)) and `verify_auth_token` (~[render_remote.py:945](Launch_RenderKit/render_remote.py#L945)).
- [ ] [MED] Discovery broadcast list at ~[render_remote.py:2137-2142](Launch_RenderKit/render_remote.py#L2137) is hardcoded (`255.255.255.255` + three common subnets). Enumerate the host's own interfaces and compute their broadcast addresses; fall back to `255.255.255.255` only.
- [ ] [MED] Rate-limit failed authentication attempts per peer IP. `SecureConnection` currently caps concurrent challenges but not failed proofs. Add a sliding-window counter (e.g. 5 failures / 60s → temporary ban).

---

## Stage 4 — Edge cases & robustness

Intent: graceful behavior when the network, filesystem, or user misbehaves.

- [ ] [HIGH] Guard `_execute_render_request` (~[render_remote.py:2547](Launch_RenderKit/render_remote.py#L2547)) before calling `bpy.ops.wm.open_mainfile(...)`, which discards unsaved changes. Check `bpy.data.is_dirty` — if the user has unsaved work, refuse the request and report back to the requester. Wrap the open in `try/except RuntimeError` for load failures.
- [ ] [HIGH] Dynamically-registered render handlers leak on unregister. `RenderManager._setup_render_monitoring` (~[render_remote.py:2635-2640](Launch_RenderKit/render_remote.py#L2635)) appends handlers at render start and uses the `_handlers_registered` flag to avoid duplicates, but those handlers are **instance state** — `render_remote.unregister()` at [render_remote.py:4378-4401](Launch_RenderKit/render_remote.py#L4378) never removes them. Call `render_manager._clear_render_handlers()` (or equivalent) in `unregister()` before class deregistration.
- [ ] [HIGH] Add an in-flight-render guard to `reset_connection_status_on_load` (~[render_remote.py:4255](Launch_RenderKit/render_remote.py#L4255)). Mirror the guard in `cleanup_on_load_pre` (~4236-4238): `if network_manager.is_rendering: return`. Otherwise a file load fired during a render will wipe connection state mid-operation.
- [ ] [MED] File sync TOCTOU. `_handle_sync_file` (~[render_remote.py:1982-1992](Launch_RenderKit/render_remote.py#L1982)) does `recv_file` → `os.stat` → manifest write. If the file is deleted between receive and stat, the handler crashes. Wrap in `try/except FileNotFoundError`, roll back the manifest entry, and return an error response.
- [ ] [MED] Normalize socket timeouts. Current values are inconsistent: `0.5s` at ~[render_remote.py:2127](Launch_RenderKit/render_remote.py#L2127), `1.0s` at ~1620/1687, `30s` elsewhere. Define named constants (`DISCOVERY_REPLY_TIMEOUT`, `CONNECT_TIMEOUT`, `READ_TIMEOUT`) in `constants.py` (Stage 5 split) and use them everywhere.
- [ ] [MED] Add retry-with-backoff for discovery and file requests. Wrap client calls in a 3-attempt retry with exponential backoff (1s/2s/4s) before reporting failure. Do **not** retry authentication (amplifies brute-force pressure).
- [ ] [LOW] Register a minimal `atexit.register()` shutdown hook as a safety net in case Blender exits without firing the addon's unregister path. Keep the existing `cleanup_on_exit` handler — add `atexit` on top.

---

## Stage 5 — Code quality, structure, maintainability

Intent: `render_remote.py` is ~4400 lines in one file. The subpackage split is mandatory in this pass; keep each commit mechanical and low-risk.

- [ ] [HIGH] **Split `render_remote.py` into a subpackage** at `Launch_RenderKit/render_remote/`. Ship this as a dedicated commit with **zero logic changes** — pure move + import rewiring — so the diff is reviewable. Proposed layout:

  | Module | Contents |
  | --- | --- |
  | `__init__.py` | `register()` / `unregister()`, module-level singletons, re-exports used by `Launch_RenderKit/__init__.py` |
  | `constants.py` | `PROTOCOL_MAX_*`, `AUTH_*`, `LAN_ALLOWED_NETWORKS`, timeout constants |
  | `paths.py` | `normalize_relative_path`, `resolve_under_root`, `relative_path_under_root`, `FileFilter` |
  | `protocol.py` | `send_message`, `recv_message`, `send_file`, `recv_file`, `recv_exact`, `validate_file_size`, `error_response`, message-schema validator |
  | `auth.py` | `SecureConnection`, TLS context helpers, fingerprint pin store |
  | `file_sync.py` | `FileSyncManager`, `OutputFileMonitor` |
  | `network.py` | `NetworkManager` and its `_handle_*` methods |
  | `render.py` | `RenderManager`, render-handler wiring |
  | `timers.py` | `TimerManager` |
  | `ui.py` | Render Remote `AddonPreferences` section, panels, operators, `PropertyGroup`s |
  | `handlers.py` | `cleanup_on_exit`, `cleanup_on_load_pre`, `reset_connection_status_on_load`, `atexit` hook |

  `Launch_RenderKit/__init__.py` keeps `from . import render_remote`; the public API surface is unchanged.

- [ ] [MED] Encapsulate module-level globals (`render_manager`, `network_manager`, `timer_manager`) into a single `AddonState` singleton that is created on `register()` and torn down on `unregister()`. Makes unit testing and state cleanup tractable.
- [ ] [MED] Standardize the error response envelope. `error_response(code, message)` exists at ~[render_remote.py:220](Launch_RenderKit/render_remote.py#L220) but several sites return `{'status': 'error', 'message': ...}` without a `code`. Audit every `return {'status': 'error', ...}` and route through the helper.
- [ ] [LOW] Add type hints to the public-ish surfaces: protocol helpers (`send_message`, `recv_message`, `send_file`, `recv_file`), path utilities, `SecureConnection` methods, `NetworkManager` public methods. Interior helpers can stay unannotated.
- [ ] [LOW] Replace broad `except Exception` in worker loops with typed exceptions, plus a final `except Exception` that re-raises when a module-level `DEBUG` flag is set. Keeps production resilient, makes development diagnosable.
- [ ] [LOW] Move hardcoded constants (`PROTOCOL_MAX_MESSAGE_SIZE`, `PROTOCOL_MAX_FILE_SIZE`, `AUTH_PBKDF2_ITERATIONS`, timeouts) to addon preferences with sensible clamps, so users on slow networks or strict security profiles can tune without code edits.

---

## Stage 6 — Test coverage

Intent: existing tests cover roughly 9 of ~200 functions. Close the gap, security paths first. Run with `python3 -m unittest discover -s tests`.

- [ ] [CRIT] Path-security primitives. Cover `resolve_under_root` and `relative_path_under_root`: traversal attempts (`..`, encoded variants), absolute paths, Windows drive paths on POSIX, symlinks escaping root, non-existent leaf, non-existent intermediate.
- [ ] [CRIT] Full `SecureConnection` suite:
  - `hash_password` — deterministic given the same salt
  - `create_challenge` — nonce uniqueness, cap eviction at `AUTH_MAX_CHALLENGES`
  - `consume_challenge` — wrong IP, wrong server nonce, expired, replay (second consume fails)
  - `issue_auth_token` / `verify_auth_token` — wrong IP, expired, malformed
  - `cleanup_expired_auth` — removes expired, keeps valid
- [ ] [HIGH] TLS tests (new, from Stage 3): self-signed cert generation succeeds and is idempotent; fingerprint pinning accepts the known fingerprint; a mismatching fingerprint is rejected; a plaintext peer is rejected.
- [ ] [HIGH] Message-handler tests. For each `_handle_*` in `NetworkManager` assert:
  - unauthenticated calls are rejected where they should be
  - malformed payloads return a structured error (never crash)
  - path traversal attempts via `relative_path` are refused
  - oversized files/messages are refused
- [ ] [HIGH] Render-request validation. Assert `_handle_render_request` rejects: invalid resolutions, unknown engines, out-of-root output paths, a second render while one is already running.
- [ ] [MED] `send_file` / `recv_file` — partial reads, truncated streams, mid-transfer socket errors, file smaller than declared size.
- [ ] [MED] `OutputFileMonitor` lifecycle — `start_monitoring` → create file → `_scan_for_new_files` picks it up → manifest reflects it; quiet-period debounce behavior.
- [ ] [MED] Add `tests/conftest.py` and either a `pytest.ini` or `[tool.pytest.ini_options]` in a `pyproject.toml` so `pytest` discovers the suite. Keep compatibility with `python3 -m unittest discover`.
- [ ] [LOW] Add a GitHub Actions workflow (`.github/workflows/tests.yml`) running `python -m unittest discover -s tests` on push and pull_request. No Blender install required — the suite uses a fake `bpy` module.

---

## Verification plan (run after each stage)

1. **Static**: `python3 -m py_compile Launch_RenderKit/*.py` and, on a Blender 5 install, `blender --command extension validate Launch_RenderKit`.
2. **Unit**: `python3 -m unittest discover -s tests`. All existing tests plus new ones from Stage 6 must pass.
3. **Manual LAN**: two Blender 5 instances on the same subnet. Verify discovery → authenticate (correct and incorrect passcode) → sync project → remote render of a frame → output sync back. Run once with `bpy.app.online_access=True` and once with it off — the latter must refuse to open sockets cleanly.
4. **Cancellation**: start a remote render, cancel via UI and via closing Blender. Confirm no daemon threads or sockets leak (`lsof -p <pid>` before/after).
5. **Security smoke tests**: craft a peer that sends (a) path-traversal `relative_path`, (b) oversized `file_size`, (c) malformed JSON, (d) render settings with `resolution_x = 999999`. Each must be rejected with a structured error and without crashing the host.

---

## Reuse existing utilities

- `TimerManager` in `render_remote.py` — the right vehicle for main-thread marshalling. Extend; do not reinvent.
- `FileFilter` at [render_remote.py:54-77](Launch_RenderKit/render_remote.py#L54) — reuse for any new path filtering rather than adding ad-hoc lists.
- [Launch_RenderKit/utility_log.py](Launch_RenderKit/utility_log.py) — target for the `print(...)` → `logging` migration.
- `sanitize_ui_message` at ~[render_remote.py:3120](Launch_RenderKit/render_remote.py#L3120) — apply to all user- or log-visible strings containing remote-supplied data.
- `error_response` at ~[render_remote.py:220](Launch_RenderKit/render_remote.py#L220) — use everywhere instead of ad-hoc dicts.
