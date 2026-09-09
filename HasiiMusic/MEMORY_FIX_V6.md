# HasiiMusic Memory Fix V6

## What this version changes

1. Stops tearing down and rejoining the Telegram voice call for every song.
   PyTgCalls is allowed to replace the active stream in-place; leave/rejoin is now only a recovery path.
2. Prevents stale StreamEnded events from recreating per-chat locks/state after a call has already stopped.
3. Tracks transition tasks and cancels them during stop.
4. Adds a stopping guard and session generations so delayed cleanup cannot delete a new session.
5. Cleans per-chat lock/track/session state after a real stop.
6. Removes wrapper tasks around preload scheduling and fully clears preload tracking maps.
7. Makes /stats lightweight: no full gc.get_objects() scan on every stats request.
8. Corrects the high-memory status wording.

## Dependency recommendation for Render

Use the stable PyTgCalls 2.3.3 line with NTgCalls 2.2.5:

py-tgcalls~=2.3.3
ntgcalls==2.2.5

Do not use the PyTgCalls 3.0.0 pre-release line for this production bot.
