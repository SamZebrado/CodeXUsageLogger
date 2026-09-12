# Competitive landscape

This project was not the first Codex quota monitor. Several strong open-source tools already cover quota visibility. The goal here is therefore **not** to build another menu-bar widget.

## Direct and adjacent alternatives

| Project | Main strength | Overlap with this project | Key difference |
| --- | --- | --- | --- |
| [CodexMeter](https://github.com/raycalrui/CodexMeter) | Native macOS menu-bar app with quota history, token activity, banked resets, notifications, CSV export | Very high: uses `codex app-server`, `account/rateLimits/read`, update notifications, local history | Better interactive UI. This project is intentionally headless and emphasizes conservative interval boundaries, redacted raw evidence, fixed storage admission rules, and no UI/update framework. |
| [CodexBar](https://github.com/steipete/CodexBar) | Mature multi-provider menu-bar usage tracker | High at current-quota visibility | Much broader provider support and UI. This project uses only the local Codex app-server and avoids browser-cookie/provider-account aggregation. |
| [caut / coding_agent_usage_tracker](https://github.com/Dicklesworthstone/coding_agent_usage_tracker) | Cross-platform CLI for many coding-agent providers; JSON/Markdown robot output | Medium: quota/credit reads and automation-friendly output | Designed for on-demand multi-provider inspection. This project is a persistent longitudinal recorder with change detection, hourly anchors, reset-safe segmentation and retention controls. |
| [CodexUsageBar](https://codexusagebar.com/) | Minimal macOS menu-bar quota display and notifications | Medium | Focuses on glanceable display, not a durable forensic history. |
| [codex-monitor](https://github.com/manuelsh/codex-monitor) | Local dashboard combining quota, task tokens and estimated usage | Medium | Focuses on task-level dashboarding/estimation. This logger intentionally refuses to infer which task/person/model caused account-level quota changes. |
| [codex-hud](https://github.com/haenara-shin/codex-hud) | Statusline quota display across multiple Codex storage/protocol eras | Medium | Optimized for interactive session visibility; this project is an independent background recorder. |

## Why keep this project?

The useful niche is a **small, auditable measurement appliance**:

- no model turns and a fixed read-only RPC allowlist;
- persistent account-level history even when work happens in other Codex clients;
- change-triggered samples plus hourly anchors;
- conservative reset/window/account boundary handling instead of filling gaps with estimates;
- redacted raw evidence retained briefly for debugging;
- long-term normalized CSV;
- hard 250 MB managed-storage ceiling and raw-first eviction;
- no automatic per-project, per-person or per-model attribution.

If one of the alternatives already satisfies these requirements, using it is preferable to maintaining another tool. Future development here should stay focused on measurement integrity and low-maintenance background operation rather than duplicating dashboards.

## Project-start rule

Before adding a substantial new feature or spinning up a related tool, first search current open-source and commercial alternatives, document what already exists, and state the smallest unmet requirement that justifies new code.
