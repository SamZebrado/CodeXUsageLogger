#!/usr/bin/env python3
"""Run from a checkout without pip or third-party dependencies."""
import sys
if sys.version_info < (3, 10):
    raise SystemExit("Python 3.10 or later is required; no automatic installation performed.")
sys.dont_write_bytecode = True
from codex_quota_logger.cli import main
if __name__ == "__main__":
    raise SystemExit(main())
