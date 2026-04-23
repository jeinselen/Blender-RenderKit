# AGENTS.md

## Scope

These instructions apply to the whole repository. Render Remote work lives primarily in `Launch_RenderKit/render_remote.py`, with feature preferences and registration hooks in `Launch_RenderKit/__init__.py`.

## Render Remote Overhaul Goal

Render Remote should become a one-click LAN-only rendering workflow:

1. Discover or manually connect to a trusted target computer on the same local network.
2. Sync the current `.blend` file plus only Blender-referenced project files that live under the project root.
3. Preserve the same relative folder structure on the target.
4. Re-copy only new or changed inputs.
5. Delete previously synced inputs that are no longer referenced, without deleting unknown target files or render outputs.
6. Start rendering from the synced target snapshot.
7. Pull completed render outputs back to the source using the same project-relative structure.
8. Shut down all network services when the feature or target service is disabled.

The intended project layout is a project root containing sibling folders such as `blender/`, `images/`, `textures/`, `renders/`, etc. For example, `/project/images/environment.exr` may be referenced by `/project/blender/project.blend` and must sync.

## Current Critical Problems

- `remote_enable` only gates registration at add-on load. Turning the setting off later hides UI but does not reliably stop sockets, timers, handlers, or monitors.
- Source mode starts a listening communication server even though output sync is currently source-pull. Source machines should not listen unless there is a clear protocol need.
- Authentication is unsafe: the passcode is sent over raw sockets, the `SecureConnection` SSL helper is unused, and some routes skip auth entirely.
- `request_file` trusts a client-provided absolute path, allowing arbitrary file reads from the target.
- The server binds broadly and has no LAN-only peer validation.
- Dependency filtering treats folders named `images` as likely render output, which excludes valid referenced assets.
- Remote deletion is detected in the UI but never applied.
- Output sync is race-prone and path mapping depends on mutable local Blender state instead of fixed job metadata.

## Non-Negotiable Design Rules

- Treat the existing Render Remote implementation as a prototype. Prefer replacing protocol and state flow over layering small patches onto unsafe assumptions.
- Never trust client-provided absolute paths. Network messages should identify files by `project_id`, `job_id`, and normalized relative paths only.
- Require authentication for every non-discovery route when target service is enabled. Apply auth consistently to status, cancel, manifest, upload, download, render, and delete routes.
- Do not support unauthenticated rendering or file transfer. If discovery is allowed without auth, it may only disclose minimal node metadata.
- Enforce LAN-only access with Python `ipaddress` checks against the target machine's local network interfaces.
- Resolve and validate every filesystem path with `Path.resolve()` plus `os.path.commonpath()` before reading, writing, deleting, or opening a `.blend`.
- Keep target cache operations confined to the configured remote cache directory.
- Delete only files owned by the previous input manifest for the same project/job lineage. Never delete unknown files, user files, or render outputs as cleanup.
- Use atomic file writes for transfers: write to a temporary sibling file, verify size and hash, then replace.
- Use bounded message sizes, bounded file sizes, socket timeouts, and clear error responses.
- Do not use folder-name heuristics like `images`, `renders`, or `output` to decide whether a referenced input is valid. The manifest should be based on actual Blender references and explicit output roots.

## Target Architecture

Separate Render Remote into clear components:

- `RemoteServiceLifecycle`: starts/stops target listening sockets, discovery, timers, render handlers, and monitors.
- `RemoteProtocol`: length-prefixed JSON messages plus binary file payloads, with message limits and route-level auth.
- `RemoteAuth`: pairing/passcode flow, challenge-response or encrypted channel, expiring job-scoped tokens, and token revocation on stop.
- `ProjectManifestBuilder`: scans the active Blender project for referenced files under the project root and records relative path, size, mtime, hash, and role.
- `TargetCache`: owns `cache/<project_id>/workspace`, input manifests, output manifests, and safe path resolution.
- `InputSync`: diffs source and target manifests, uploads new/changed inputs, and asks target to delete obsolete manifest-owned inputs.
- `RemoteRenderJob`: opens only the synced `.blend` snapshot, maps output paths into the target workspace, starts render, tracks progress, and records output files.
- `OutputSync`: source polls target output manifest, downloads stable completed outputs, verifies hashes, and writes them back under the source project root.

Keeping these as classes in `render_remote.py` is acceptable for the first overhaul if that reduces integration risk. Extract into modules later if the file becomes hard to test.

## Concrete Development Steps

1. Add lifecycle control.
   - Add an update callback for `remote_enable` in `Launch_RenderKit/__init__.py`.
   - When disabled, call a single Render Remote shutdown function that stops discovery, communication, timers, render handlers, output monitors, and clears auth tokens.
   - Make unregister idempotent so repeated shutdowns are safe.
   - Remove source-side communication server startup from connect and start-render flows unless a new protocol explicitly needs it.

2. Add protocol safety before new features.
   - Implement shared helpers for `send_message`, `recv_message`, `send_file`, and `recv_file`.
   - Read exactly the announced number of bytes for all responses.
   - Reject JSON messages above a small fixed limit.
   - Reject file payloads above a configurable fixed limit.
   - Return structured errors without leaking local absolute paths.

3. Replace authentication.
   - Remove plaintext passcode submission.
   - Implement a pairing/challenge flow or TLS-based channel before any secret is transmitted.
   - Require auth tokens on every route except minimal discovery.
   - Bind tokens to peer address, expiry time, and optionally `project_id`/`job_id`.
   - Clear tokens when the service stops, passcode changes, or Blender loads a different file.

4. Enforce LAN and path boundaries.
   - Reject non-private, non-link-local peers unless explicitly allowed by a future preference.
   - Normalize `project_name`/`project_id`; do not use user-facing project names directly as filesystem paths.
   - Add safe helpers for resolving project-relative paths under source project root and target cache workspace.
   - Replace all `startswith` path checks with `commonpath` checks.

5. Build a reliable input manifest.
   - Define the source project root as the parent of the `.blend` directory, matching current intended layout.
   - Include the saved `.blend` snapshot.
   - Include referenced images, image sequences, movie clips, sounds, fonts, linked libraries, cache files, and discoverable simulation/volume/geometry assets that live under the project root.
   - Treat referenced files outside the project root as unsupported and show them clearly to the user.
   - Remove render-output folder-name filtering from dependency scans.
   - Store manifest entries by POSIX-style relative path.

6. Implement target manifest ownership and deletion.
   - Store the latest input manifest in the target cache.
   - Diff local input manifest against target input manifest.
   - Upload new and modified files.
   - Send a delete-obsolete-inputs request containing only relative paths from the previous manifest that are absent from the new manifest.
   - On target, delete only paths present in the stored manifest and inside the cache workspace.

7. Make start render truly one-click.
   - On "Render Animation", scan dependencies, sync inputs, apply obsolete-input deletion, then send the render request.
   - Disable render start if required inputs are missing or unsupported external files exist.
   - Send render settings as project-relative output intent, not host-specific absolute paths.
   - Target opens only the synced `.blend` path from the cache workspace.

8. Rework output handling.
   - Determine expected output roots from main render filepath and compositor file output nodes after opening the synced file.
   - Keep outputs under the target workspace.
   - Track completed output files in an output manifest with relative path, size, hash, frame number when known, and stable timestamp.
   - Source polls output manifest while rendering and after completion until a quiet period passes.
   - Source downloads outputs by relative path, verifies hash, writes atomically under the source project root, and preserves folder structure.
   - Do not mark an output path as permanently synced if the target file later changes.

9. Update UI and status.
   - Make target mode explicit: "Allow Remote Rendering" starts a listening service; disabling it stops the service.
   - Make source mode connection non-listening.
   - Show sync phases: scanning, uploading, deleting stale inputs, rendering, downloading outputs, complete.
   - Show unsupported external references and missing files before render starts.
   - Avoid exposing auth tokens or absolute target paths in UI.

10. Add tests around pure-Python pieces.
    - Path traversal rejection.
    - LAN address validation.
    - Manifest diffing, including stale input deletion.
    - Dependency filtering does not drop `/images/...` references.
    - Message framing handles partial reads and rejects oversized messages.
    - File transfer verifies hash and does not leave partial destination files.
    - Auth-required routes reject missing/expired tokens.
    - Output sync overwrites changed frames and preserves relative paths.

## Manual Verification Checklist

- With Render Remote disabled, no discovery or communication socket remains open.
- Enabling target mode starts exactly the target service; disabling it stops all Render Remote activity.
- Source connect does not open a listening port.
- A project at `/project/blender/project.blend` referencing `/project/images/environment.exr` syncs both files to the target.
- Changing `environment.exr` re-syncs it; unchanged files are skipped.
- Removing a referenced file from the `.blend` causes the old target copy to be deleted after the next sync.
- A rendered file at `/project/renders/frame0001.png` on the target returns to `/project/renders/frame0001.png` on the source.
- Cancelling, querying status, requesting manifests, downloading files, and deleting files all fail without valid auth.
- Attempts to request `../` paths or absolute paths fail.
- Attempts from non-LAN addresses fail.

## Packaging Note

If Render Remote keeps network capabilities enabled for release builds, update `Launch_RenderKit/blender_manifest.toml` permissions to declare network use with an accurate reason. Do this only when the feature is secure enough to ship.
