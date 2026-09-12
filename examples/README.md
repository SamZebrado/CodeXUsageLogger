# Synthetic examples only

These files contain **invented test data**, not anyone's actual quota history.

- `SYNTHETIC-SNAPSHOT.json`: CLI doctor output from the bundled fake server. Its `0.0.0-synthetic` version and PASS are a protocol demonstration, not a real-account dry-run.
- `SYNTHETIC-HISTORY.csv`: startup, detected usage change, unchanged heartbeat, then a primary-window rollover. Notice that the secondary interval remains the same when only the primary window rolls over.

Do not overwrite these files with private readings before committing them. Real quota data stays outside the repository.
