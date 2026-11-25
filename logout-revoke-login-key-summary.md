# Desktop logout key fix — 2026-11-25

Changed app.py (login-only SHA-256 fingerprint metadata, persistence beside protected key pool, manual duplicate override, logout removal and pool refresh) and tests/test_logout_key.py (offline ownership, restart and Generate gate regressions). Metadata contains fingerprints only.

Passed: python3 -m unittest tests.test_oauth_desktop tests.test_generate_login tests.test_logout_key tests.test_ai_plan_bridge tests.test_platform tests.test_inputs — 81 tests run, 1 platform-dependent skip. git diff --check passed. Initial sandbox run could not bind loopback test servers; final run with loopback access passed.

Behavior: login-only keys are removed locally at logout; manual keys, including the same value pasted before or after login, remain. OAuth session/profile clear. Logged-in keys remain usable. No server key-revocation API was added.

Remaining limitation: legacy untagged keys are preserved because their source cannot safely be inferred. No live API calls or native GUI end-to-end testing were performed. Versions unchanged; no push, PR, deployment, or unrelated checkout changes.
