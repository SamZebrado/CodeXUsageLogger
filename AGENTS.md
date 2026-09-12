# CodexQuotaLogger development contract

- Runtime Python standard library only. Preserve the fixed read-only RPC allowlist.
- Never introduce model calls, thread/turn creation, conversation resume, login/logout, credential extraction, reset redemption or billing actions.
- Run `python3 -B -m unittest discover -s tests -v`. Tests use a fake server, not a real account.
- Never call a fake-server test a successful real Mac/account dry-run.
- Notifications are sparse hints. Preserve full-read semantics and 5-minute fallback polling.
- Keep account quota separate from task/model/person cost attribution.
- Protect normalized history. Under storage pressure evict known raw files first; stop capture rather than silently deleting history or exceeding the cap.
- Keep runtime data, identity salt and real dry-run output out of Git and attachments.
- No LaunchAgent installation/loading until explicit post-dry-run approval.
- No unrelated project, browser, Bridge, Android, credential or system cleanup work.
