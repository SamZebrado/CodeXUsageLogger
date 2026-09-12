# Competitive landscape

This project is **not** the first Codex quota monitor. A competitor scan performed before publication found several mature alternatives, including one (`CodexMeter`) with very high functional overlap. The justification for keeping this repository is therefore deliberately narrow: a headless, read-only, reset-safe audit trail with bounded storage and conservative attribution semantics.

## Direct and adjacent alternatives

| Project | Main strength | Overlap with this project | Key difference |
| --- | --- | --- | --- |
| [CodexMeter](https://github.com/raycalrui/CodexMeter) | Native macOS menu-bar app; Codex App Server; 5-hour/weekly quota, banked resets, token activity, local history, CSV export, launch at login | **Very high**. It already records quota history as changes plus periodic anchors and reads `account/rateLimits/read` / optional `account/usage/read`. | Prefer CodexMeter if an interactive macOS UI is desired. This project stays headless and emphasizes strict RPC allowlisting, explicit reset/window segmentation, redacted short-lived raw evidence, and a hard managed-storage admission policy. |
| [CodexBar](https://github.com/steipete/CodexBar) | Mature multi-provider usage tracker with macOS/Linux UI and CLI, many providers, usage/spend views | High for current-quota visibility; broader than this project | Better choice for multi-provider monitoring. This project intentionally avoids browser-cookie/provider aggregation and only records Codex account-level history through the local App Server. |
| [caut / coding_agent_usage_tracker](https://github.com/Dicklesworthstone/coding_agent_usage_tracker) | Cross-platform Rust CLI for many coding-agent providers with human/JSON/Markdown output | Medium: quota/credit reads and automation-friendly output | Designed mainly for on-demand multi-provider inspection. This project is a persistent longitudinal recorder with change detection, hourly anchors, reset-safe segmentation and retention controls. |
| [codex-hud](https://github.com/fwyc0573/codex-hud) | Interactive Codex/tmux HUD for model, context, tokens, project/session and agent activity | Adjacent | Optimized for active-session observability. This project runs independently in the background and records account-level quota history even when work happens elsewhere. |
| `CodexUsageBar` family | Several small macOS menu-bar projects exist under this name on GitHub | Medium for glanceable quota display | These generally target a visible status bar rather than a conservative evidence/audit log. Because there are multiple unrelated repositories with the same name, this document does not treat one fork as canonical. |

## What the scan changed

The competitor scan materially narrowed the product scope. In particular, **we should not spend effort cloning CodexMeter/CodexBar UI features**. If the user's goal changes to a menu-bar chart, notifications, or broad provider aggregation, adopting one of those projects is preferable to extending this repository.

The remaining niche is a **small measurement appliance**:

- no model turns and a fixed read-only RPC allowlist;
- persistent account-level history even when work happens in other Codex clients;
- change-triggered samples plus hourly anchors, with a low-rate poll as cross-client fallback;
- conservative reset/window/account boundary handling instead of filling gaps with estimates;
- redacted raw evidence retained briefly for debugging;
- long-term normalized CSV;
- hard 250 MB managed-storage ceiling and raw-first eviction;
- no automatic per-project, per-person or per-model attribution;
- CLI/LaunchAgent operation with no always-visible UI requirement.

If an alternative already satisfies these requirements on the target machine, using it is preferable to maintaining another tool.

## Project-start rule

For this repository, and as a general development habit for future greenfield work:

1. Search current open-source and commercial alternatives first.
2. Identify which existing project is closest to the actual need.
3. State the smallest unmet requirement (the `gap`) that would justify new code.
4. Prefer adopting/extending an existing tool when the gap is small.
5. Only start a new implementation when the gap is material, and keep the new scope limited to that gap.

This check should happen **before architecture/design work**, so implementation effort is not spent rediscovering an existing product.
