# Delivery QA — v0.1.0

## Scope and evidence level

Development and offline verification were performed on **Linux, Python 3.13.5**. This is a source-package verification report, not a claim of a real macOS account test or an independent external review.

- **Offline automated suite: PASS — 103 tests.**
- Tests use synthetic account responses and a local Python fake app-server. No real account, network request, model turn, reset redemption, or browser automation is involved.
- Runtime communication is restricted by a tested allowlist to initialization and account metadata reads. A rejected server request is never approved.
- **Isolated local Git clone: PASS — the same 103 tests, 15.811 seconds; clean working tree after tests, CLI help and synthetic doctor.**
- First implementation checkpoint used for that clone: `c6337d19b9b715c8785763a9735ac689e8c03fd8`. Subsequent release-only changes add this QA record and checksum manifest; runtime/test source remains unchanged.

## Test coverage

The suite covers quota normalization, precision/null handling, privacy projection, semantic deduplication, multi-meter payloads, sparse notifications, earned-reset count semantics, hourly heartbeats, 5-minute polling, independent primary/secondary intervals, account/plan changes, missing measurements, unexplained percentage decreases, and out-of-range anomalies.

Storage tests cover private permissions, writer locks, CSV headers/quoting, incomplete-tail preservation, partial-write rollback, retention by UTC date, gzip and temporary-space admission, raw-first eviction, protected CSV, log rotation, symlink/hardlink rejection, and cap exhaustion.

Transport tests run a real child-process pipe against the fake server, including fragmented lines, interleaved/wrong-ID replies, malformed and oversized JSON, timeouts, EOF, sanitized error codes, notification coalescing, forbidden methods, and unexpected server requests. Daemon tests include reconnect behavior and real fake-server-to-CSV operation, including an unsupported optional usage endpoint.

LaunchAgent tests inspect the generated plist, path handling, explicit installation approval, and data preservation. They **do not execute macOS launchctl** in this environment.

## Reproduce offline

```sh
python3 -B -m unittest discover -s tests -v
python3 quota_logger.py --help
```

A fake-protocol demonstration (not a real quota reading):

```sh
python3 quota_logger.py doctor --codex "$PWD/tests/fake_codex.py"
```

The version `0.0.0-synthetic` identifies that demonstration. Do not submit it as local-account acceptance.

## Release checks

The release ZIP is source-only. It excludes `.git`, bytecode caches, real quota history, real-account dry-run output, authentication files, and fingerprint salts. Test fixtures deliberately contain invalid sentinel credentials to verify non-persistence; these are not account credentials.

`MANIFEST.sha256` covers the shipped files except itself. The sibling ZIP checksum covers the entire archive. The ZIP's CRC is checked before delivery.

## Not performed here

- User's installed Codex schema and real authenticated account read: **NOT RUN**.
- Real macOS launchd installation/start/stop and sleep/wake behavior: **NOT RUN**.
- Backend billing or whether upstream Codex internally refreshes authentication/caches: **NOT VERIFIED**.
- GitHub hosted Actions: **NOT YET VERIFIED in this source QA report**; local offline tests are authoritative until a hosted run is checked.
- Repository publication target: **existing public `SamZebrado/CodeXUsageLogger`**. Publication must exclude runtime/account data and real dry-run output.

For local acceptance, run `doctor` once against the actual installed Codex, review its supported shape/schema status and redacted response, then stop for approval before installing anything.
