# CodexQuotaLogger

A local, read-only **account-level** Codex quota recorder. Python 3.10+, standard-library runtime, no model SDK, no browser automation, no prompts.

[中文说明](README.zh-CN.md) · [Local verification handoff](LOCAL_HANDOFF.md) · [Protocol notes](docs/PROTOCOL.md) · [QA evidence](docs/QA.md)

**Before installing:** there are mature quota viewers already. If you mainly want a menu-bar/dashboard view, consider [CodexMeter](https://github.com/raycalrui/CodexMeter), [CodexBar](https://github.com/steipete/CodexBar), or [caut](https://github.com/Dicklesworthstone/coding_agent_usage_tracker). This project deliberately targets a narrower niche: a headless, read-only, reset-safe local audit trail with bounded storage and conservative attribution semantics. See [competitive landscape](docs/COMPETITORS.md).

## Policy

- Reconcile a full account snapshot when a distinct quota notification arrives (2-second minimum spacing).
- **Also poll every 5 minutes**, because a separate idle app-server is not a guaranteed cross-client event feed. Unchanged polls do not append CSV/raw evidence.
- Append an hourly heartbeat even when unchanged; changes do not postpone that heartbeat.
- Keep normalized CSV history; raw **redacted** JSONL retains the latest 30 UTC calendar dates. Gzip closed days when temporary-file space permits.
- Hard admission cap: **250,000,000 logical bytes in the managed data directory**, including CSV, raw files, health, salt, temporary files, and operational logs. Evict oldest raw evidence first. Never automatically delete CSV. If protected data fills the budget, stop appending and report `storage_full`.

“Immediate” means after receiving/detecting an update, not a guarantee of real-time remote billing. A remote client's use may only be observed at the next 5-minute poll, and backend data can lag. No samples can be recovered from before installation or while the computer is asleep/offline. Reconnects start new conservative intervals.

## Safety boundary

The program requests only `initialize`, `account/read` with `refreshToken: false`, `account/rateLimits/read`, and (opt-in) `account/usage/read`. It sends only the `initialized` client notification. A runtime allowlist rejects every other method. Unexpected server requests close the connection; the logger never approves them.

It never starts/resumes conversations, calls `codex exec`, sends a model turn, changes plans, redeems resets, logs in/out, or directly opens authentication files. Existing OAuth state is handled **by Codex itself**; Codex may refresh its own credentials or write its own caches. The logger does not independently certify server billing. Its design and tests verify the absence of model-turn calls, not a billing promise from OpenAI.

This records **account quota, not per-project/person/model usage**. No automatic allocation is made when several people or agents share the account. A model-looking quota bucket is a backend meter, not evidence of the executor of each task.

## Quick start — stop after the dry-run

Unpack the source in a stable directory, then:

```sh
cd CodexQuotaLogger
python3 -B -m unittest discover -s tests -v
python3 quota_logger.py doctor
```

`doctor` detects the installed Codex version, attempts to inspect its generated local JSON schemas, and makes **exactly one real quota-read RPC**, plus the initialization/auth-metadata reads. It prints a redacted snapshot, closes its child process, writes no quota history and installs nothing. An unavailable schema is explicitly reported; a successful live read does not pretend the schema was inspected.

`codex` must already be installed and signed in to the intended ChatGPT account. No dependency installation or authentication is performed for you. An API-key-only provider may not support account quota reads. To select a particular binary:

```sh
python3 quota_logger.py doctor --codex /absolute/path/to/codex
```

Do **not** install/load the LaunchAgent until you have inspected that result and approved it. All tests in this source package use synthetic data and need no Codex login.

## After approval

```sh
# Writes a user-level plist only; does not load it.
python3 quota_logger.py install --approve-install
# Explicitly load/start the service.
python3 quota_logger.py start
python3 quota_logger.py status
```

Keep the checkout at the same path: the plist references its launcher and the exact Python/Codex paths, with no shell interpolation. A pip-installed console entry is optional; direct checkout execution avoids installation/build dependencies.

Other commands:

```sh
python3 quota_logger.py snapshot     # one live quota read; no history writes
python3 quota_logger.py status       # saved status; never starts Codex
python3 quota_logger.py stop         # unload only this LaunchAgent
python3 quota_logger.py restart
python3 quota_logger.py uninstall    # unload/remove plist; preserve ALL history
python3 quota_logger.py plist        # preview plist, no installation
python3 quota_logger.py run          # foreground logger; Ctrl-C stops it
python3 quota_logger.py housekeeping # local raw retention/compression only
```

`--data-dir`, `--codex`, `--timezone`, `--poll-seconds`, `--heartbeat-seconds`, `--cap-bytes` and `--retention-days` are supported. Use the same data directory when inspecting a customized installation. Polling cannot be configured below 30 seconds. Defaults are 300/3600 seconds, Asia/Shanghai, 250 MB and 30 days.

`--usage` opts into a separate, once-daily `account/usage/read`. Unsupported/erroring optional endpoints are disabled for that connection; quota recording continues. These backend daily buckets retain their dates without inventing a local-timezone interpretation or converting tokens into quota percentages.

## Data files

Default: `~/Library/Application Support/CodexQuotaLogger/`

| File | Purpose / retention |
|---|---|
| `quota_history.csv` | Long-term normalized rows; never automatically evicted |
| `raw/YYYY-MM-DD.jsonl[.gz]` | Redacted quota snapshots and optional daily usage; at most 30 UTC dates; may be evicted earlier for the cap |
| `health.json` | Last success, last persisted rows, process state, policy |
| `operations*.jsonl` | Small rotating event-code logs, no arbitrary upstream error text |
| `identity.salt` | Random local fingerprint salt, **not** an account credential |
| `.lock` | OS-held single-writer lock; no PID guessing or global process killing |

Directories are owner-private and generated files use mode 0600. Symlinks and hard-linked managed files are refused. Auth/account IDs, email, banner/free-text fields, cookies, headers and token fields are not copied into evidence. A salted local account fingerprint separates account switches without saving the identity; treat even these activity logs as private.

Raw evidence is a **known-field privacy projection**, not an unredacted packet capture. Unknown fields are dropped. Quota bucket IDs, supported fields, numeric precision, nulls, and optional earned-reset counts survive. The count comes from `availableCount`, never from a possibly truncated detail-list length.

One snapshot produces a row per meter bucket; rows share `sample_id`. Do not sum percentages across meters. `primary` and `secondary` keep the backend's names and window durations rather than assuming one always means weekly.

## Reset and missing-data rules

Primary and secondary windows have separate interval IDs. A primary reset alone does not break a still-continuous weekly window. Window metadata, account, plan or relevant limit changes start new intervals. An unexplained percentage decrease is `possible_reset_or_correction`, not negative consumption. Missing/reappearing measurements and telemetry gaps also cut intervals conservatively. Restarting the daemon starts fresh segments; no unsupported continuity is reconstructed.

`ordinaryUsageAllowed` is recorded when supplied. Percentages/reset timestamps alone do not establish permission to use Codex again. The utility computes `100 - usedPercent` without clamping and records out-of-range values as anomalies. No token-price conversion or task-cost estimates are generated.

## Storage and recovery limits

The cap applies to regular-file **lengths** managed by this logger. Filesystem allocation, directory blocks, APFS snapshots, backups, exported copies, Python/Codex installations, temporary diagnostic schema output, and Codex's own files outside this directory are outside that cap. `status` also shows allocated bytes where available. It does not scan or clean your disk outside its own data directory.

Compression counts its temporary working copy against the cap and can be deferred. Raw retention uses dates in file names, not gzip modification times. Protected CSV can eventually fill any finite budget: indefinite retention + indefinite capture + a fixed cap cannot all hold forever. This implementation preserves history and the cap, suspending new capture when necessary.

Ordinary interrupted appends roll back only that incomplete write. A pre-existing incomplete CSV tail is left untouched and blocks further appends for manual recovery; it is never silently discarded. Network failures and child crashes use bounded exponential backoff. Sleep/wake gaps trigger fresh reads, not rapid catch-up polls. Child stderr and LaunchAgent stdout/stderr go to `/dev/null` to prevent unbounded or credential-bearing upstream logs; app-defined diagnostics live in the bounded operational files.

Future work: optional, separately scoped local task markers; account-level history will remain independent of model-cost attribution.
