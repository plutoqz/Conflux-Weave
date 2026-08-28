# UX-0 browser verification

This directory pins the browser automation library used by the UX-0 acceptance
scripts. It is not a Workbench runtime dependency or a frontend build system.

Install without downloading a browser when a local Chromium-compatible browser
is already available:

```powershell
$env:PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD = "1"
npm ci --prefix tools/ux0-browser --ignore-scripts
$env:CONFLUX_WEAVE_BROWSER_EXECUTABLE = "C:\path\to\chrome.exe"
```

The persisted-history verifier expects a Workbench at `127.0.0.1:8765`. The
state verifier expects the deterministic fixture server at `127.0.0.1:8766`.
Rebuild `scripts/make_ux0_state_fixture.py` before every state-verifier run
because the cancellation check intentionally mutates its isolated fixture.
