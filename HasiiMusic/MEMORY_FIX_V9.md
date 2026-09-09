# HasiiMusic V9 — Native Engine + Call-State Diagnostics

## Purpose
V8 reduced application-side accumulation but Render logs still showed RSS growth after playback stopped. V9 targets the suspected native voice stack while making stale in-memory call state visible and conservatively cleaning idle state.

## Native versions
- `py-tgcalls==3.0.0rc3`
- `ntgcalls==3.0.0rc2`

These are pre-release versions. PyPI lists stable `py-tgcalls 2.3.3` and `ntgcalls 2.2.5`; V9 intentionally uses the RC releases as a controlled diagnostic/fix candidate for the native-memory problem.

## Code changes
1. Logs the actual py-tgcalls/ntgcalls versions at startup.
2. Adds conservative stale call-state reconciliation. It only schedules cleanup when MongoDB has no active call, the local queue has no current track, and no transition is running. It does not force native PyTgCalls teardown.
3. `/stats` diagnostics now inspect PyTgCalls/native-binding cache sizes when those attributes exist, plus the optional native `binding.calls()` accessor when safely available.
4. Existing V6/V8 queue, preload, download-lock and allocator changes remain intact.

## Render deployment
Replace the two corresponding lines in the project's `requirements.txt` with:

```text
py-tgcalls==3.0.0rc3
ntgcalls==3.0.0rc2
```

Keep the existing `MALLOC_ARENA_MAX=2` and `MALLOC_TRIM_THRESHOLD_=131072` environment variables. Do not upload `cookies.txt`.

## Expected startup evidence
Look for:
`py-tgcalls=3.0.0rc3 ntgcalls=3.0.0rc2`

## What V9 is testing
The important signal is not only the peak RSS. Compare idle baseline, post-stop RSS, and native cache counts. If RSS drops materially and native call/cache counts return to zero after calls stop, the previous native stack was a strong contributor. If RSS still rises while native counts are zero, the next step should target a native-process/container-level allocator profile rather than more Python cleanup.
