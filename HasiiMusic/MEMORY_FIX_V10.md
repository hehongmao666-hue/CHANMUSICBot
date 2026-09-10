# HasiiMusic V10 — Idle Playback State Cleanup

V10 is a narrow follow-up to V9. It does not change YouTube, FFmpeg, queue download, or PyTgCalls playback behavior.

## Changes
- Reconciles stale state across `_chat_locks`, `_session_gen`, `_track_index`, `_transition_tasks`, `_stopping`, and `_pending_transitions`.
- Runs reconciliation automatically every 5 minutes; `/stats` can still trigger the same safe reconciliation.
- Cleanup is conservative: MongoDB must show no active call, the local queue must have no current track, no transition task may be running, and the native binding must report zero active calls. If the native binding cannot provide a safe count, automatic cleanup is skipped.
- Calls `gc.collect()` and Linux `malloc_trim(0)` only after stale state entries were actually removed.
- Stops the reconciler cleanly during application shutdown.

## V9 dependencies remain unchanged
Keep:

    py-tgcalls==3.0.0rc3
    ntgcalls==3.0.0rc2

## Validation
`python -m compileall HasiiMusic` passes.
