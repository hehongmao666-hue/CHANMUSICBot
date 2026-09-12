# HasiiMusic Memory Fix V12 — Native Memory Attribution + Voice Lifecycle Diagnostics

V12 builds on V11. It is intentionally diagnostic-first: it does **not** recreate the PyTgCalls client or introduce an aggressive native reset.

## Changes

1. **Native call IDs are logged**
   - `/stats` now records the actual chat IDs returned by `ntgcalls.binding.calls()` when available.

2. **Large mmap attribution**
   - `/stats` summarizes `/proc/self/maps` into anonymous vs file-backed mappings and reports the largest anonymous mappings.
   - This helps distinguish glibc arena growth from mmap-heavy native allocations.

3. **Voice lifecycle snapshots**
   - Before/after `PyTgCalls.play()` are logged.
   - Before/after `leave_call()` are logged.
   - Join timeout paths record snapshots immediately before and after stop cleanup.
   - Snapshots include RSS, PSS, Anonymous, Private, child RSS, thread count, VmData, glibc `mallinfo2`, and native call IDs.

4. **No unsafe behavioral reset**
   - No whole-client PyTgCalls recreation.
   - No forced stop of unrelated active groups.
   - Existing V11 per-chat reconciliation remains in place.

5. **Bug cleanup**
   - Removed a duplicated `continue` left in the V11 native cleanup fallback.

## What to observe

The next Render run should be tested across repeated play/stop cycles and multiple simultaneous groups. The key comparison is:

`BEFORE_PLAY -> AFTER_PLAY -> BEFORE_LEAVE -> AFTER_LEAVE`

and, after all calls are stopped:

`/stats` Process RSS/PSS/Anonymous + Proc maps anonymous + glibc arena/mmap + native binding call IDs.

If RSS/Anonymous rises while native call IDs return to zero and large anonymous mappings remain, that is stronger evidence for retained native/library mappings. If child RSS remains after `AFTER_LEAVE`, investigate FFmpeg/process lifetime separately. If memory rises in lockstep with `binding.calls()` and falls after per-chat leave, the dominant cost may simply be active native call capacity rather than a leak.

This version does not claim a native memory leak is proven.
