# Protocol and scope notes

Checked on 2026-09-12 against the public OpenAI app-server documentation and selected generated types at upstream commit **aee8a55ab6010f1d53e741edec74dbcffa07bcfe**. Upstream `main` and the user's installed CLI need not match.

Primary references:

- https://developers.openai.com/codex/app-server/ (redirects to the official ChatGPT Learn app-server docs)
- https://github.com/openai/codex/blob/aee8a55ab6010f1d53e741edec74dbcffa07bcfe/codex-rs/app-server-protocol/schema/typescript/v2/GetAccountRateLimitsResponse.ts
- https://github.com/openai/codex/blob/aee8a55ab6010f1d53e741edec74dbcffa07bcfe/codex-rs/app-server-protocol/schema/typescript/v2/RateLimitSnapshot.ts
- https://github.com/openai/codex/blob/aee8a55ab6010f1d53e741edec74dbcffa07bcfe/codex-rs/app-server-protocol/schema/typescript/v2/RateLimitWindow.ts
- https://github.com/openai/codex/blob/aee8a55ab6010f1d53e741edec74dbcffa07bcfe/codex-rs/app-server-protocol/schema/typescript/v2/RateLimitResetCreditsSummary.ts
- https://github.com/openai/codex/blob/aee8a55ab6010f1d53e741edec74dbcffa07bcfe/codex-rs/app-server-protocol/schema/typescript/v2/SpendControlLimitSnapshot.ts

## Implemented transport

`codex app-server`, using its default stdio transport. Messages are newline-delimited JSON. The wire format omits the JSON-RPC `jsonrpc` header. The client waits for `initialize`'s matching response, sends `initialized`, then performs metadata reads. It does not follow the documentation's subsequent conversational thread/turn example.

A single owned child is kept alive during background capture. Request IDs are matched despite interleaved notifications; payload length, wait time and read-loop work are bounded. No network listener is opened by this client. Its unexpected-server-request policy is to disconnect, not approve or supply credentials.

## Quota compatibility

`rateLimits` is the compatibility view; `rateLimitsByLimitId` is the multi-meter view. The latter is preferred without losing a distinct legacy bucket. Percentages retain backend precision. `resetsAt` is seconds since the Unix epoch; absent values stay unknown. Optional plan, credit, spend-control, meter model label and available reset count fields are projected when present.

`ordinaryUsageAllowed` is an optional backend permission flag in the checked generated type. No permission/recovery is inferred from percentages alone.

`account/rateLimits/updated` is treated as a sparse **hint**. The client refetches instead of clearing previously known fields with sparse nulls. Identical hints are coalesced to avoid a read-notify-read feedback loop. The only durable raw record is the redacted complete read and a notification counter; this is not a complete event packet archive.

The endpoint is not a contractual realtime account stream across every other client/device. Independent 5-minute polling remains enabled even in an idle session. All persisted timestamps are local receipt times, not backend transaction times.

## Installed-version check

`doctor` runs:

```text
codex --version
codex app-server generate-json-schema --out <temporary-directory>
```

It inspects generated method constants/enums before the live read. It uses explicit shape validators for the supported payload subset, not a full third-party JSON Schema interpreter. Schema generation unavailability is reported separately from live API compatibility. Stable initialization is used; experimental capabilities are not enabled just to obtain extra fields.

## Optional activity data

The official documentation describes `account/usage/read`, with a `summary` and nullable `dailyUsageBuckets` containing `startDate` and `tokens`. The utility projects only documented numeric fields and valid dates. No assumption about daily bucket timezone, monetary conversion or equality with local rollout token sums is made. It is opt-in and outside the single-read doctor procedure.

## What is not verified here

The user's exact installed Codex build, entitlement, real rate-limit payload, operating-system sleep behavior, user LaunchAgent/TCC behavior, and backend billing are not testable in the development container. The bundled fake server and tests have no network, real account, model invocation, or valid credentials. Their PASS cannot stand in for the Mac dry-run.
