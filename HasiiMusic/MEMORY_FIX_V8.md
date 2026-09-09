# HasiiMusic V8 — multi-chat memory lifecycle fix

## What changed

1. **YouTube per-video download-lock cleanup**
   - The downloader previously kept one `asyncio.Lock` forever for every unique video ID ever requested.
   - V8 uses reference-counted lock ownership and removes the lock when no coroutine is using/waiting on it.
   - This prevents long-running multi-chat playback from accumulating thousands of permanent lock objects.

2. **Spotify embed cache is now bounded**
   - The embed fallback previously retained the full raw track list for every unique Spotify collection indefinitely.
   - V8 limits the cache to 20 recent collections and at most 2,000 cached raw tracks in total.

3. **Background playlist expansion is now lifecycle-aware**
   - Playlist batch tasks were created with `asyncio.create_task()` but were not tracked or cancelled when playback stopped.
   - A late task could repopulate a chat's queue after `/stop`.
   - V8 tracks one playlist-expansion task per chat, cancels it during stop, and validates session generation / active-call state before adding tracks.

4. **Existing V7 allocator settings remain compatible**
   - Keep the Render environment variables already added:
     - `MALLOC_ARENA_MAX=2`
     - `MALLOC_TRIM_THRESHOLD_=131072`

## Intentionally unchanged

- YouTube cookies/search logic
- PyTgCalls / ntgcalls versions
- Core playback stream-switch strategy from V6
- User-facing playback commands

## Validation

All HasiiMusic Python files pass `python -m compileall -q` after the V8 changes.
