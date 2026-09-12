# GitHub publication

Canonical repository: `SamZebrado/CodeXUsageLogger`.

The repository is public, so publication QA must remain fail-closed for personal data and runtime evidence. Source files, synthetic fixtures and documentation may be published; live quota history, account identifiers, auth state, local salts, `.env` files and real dry-run output must remain untracked.

The connected GitHub integration has write access to this repository. The source tree is designed to be pushed as a normal `main` branch checkout. `.gitignore` excludes the known runtime artifacts, but staged-file inspection remains mandatory before any future publication.

Before pushing future changes:

```sh
python3 -B -m unittest discover -s tests -v
git status --short
git diff --check
git diff --cached --stat
```

For public changes, also search for accidental local paths, account names, emails, tokens and live quota files. Synthetic fixtures deliberately contain invalid sentinel values to test redaction and are safe to publish.

Public visibility, tags and releases are separate decisions. Do not publish generated runtime history by attaching it to an issue, release or Actions artifact.
