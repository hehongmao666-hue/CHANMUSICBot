# HasiiMusic Memory Fix V11 — Native Per-Chat Lifecycle

V11 builds on V10 and targets the remaining signal from Render diagnostics: one active native call can coexist with many stale `call_locks` / `track_index` entries and PyTgCalls internal caches.

## Changes

1. **Per-chat native ownership detection**
   - Reads `ntgcalls.binding.calls()` and preserves the active chat IDs.
   - Does not require the entire native client to become idle before cleanup.

2. **Per-chat native cleanup**
   - For a chat with no MongoDB call, no current queue item, no running transition, and no native call, V11 invokes PyTgCalls' own `_clear_call(chat_id)` cleanup path when available.
   - This clears the native call state and PyTgCalls caches for that chat without recreating or interrupting the whole client.

3. **Internal cache reconciliation**
   - Reconciliation now also discovers stale keys in `_call_sources`, `_wait_connect`, `_p2p_configs`, `_pending_connections`, `_need_unmute`, `_presentations`, and `_cache_user_peer`.
   - This catches native-side stale state even when the bot's Python queue/state maps are already empty.

4. **Active calls are protected**
   - A chat reported by `ntgcalls.calls()` is never touched by idle reconciliation.
   - Other groups can continue playing while stale groups are cleaned.

5. **Fallback remains conservative**
   - If the installed native binding cannot provide chat IDs, V11 falls back to V10's whole-client count logic and does not force unsafe cleanup while calls are active.

6. **Allocator trim remains after actual cleanup**
   - `gc.collect()` + Linux `malloc_trim(0)` still run after reconciliation.

## Important

This does not claim the native WebRTC stack is proven to leak. It is a targeted lifecycle fix based on the observed mismatch between active native calls and stale application/native cache state.

YouTube/yt-dlp configuration is intentionally unchanged.
