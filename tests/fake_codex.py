#!/usr/bin/env python3
"""Synthetic app-server for tests ONLY. No network or real Codex authentication."""
import json
import os
import sys
import time
from pathlib import Path

if "--version" in sys.argv:
    print("codex-cli 0.0.0-synthetic")
    raise SystemExit(0)
if "generate-json-schema" in sys.argv:
    out = Path(sys.argv[sys.argv.index("--out") + 1])
    out.mkdir(parents=True, exist_ok=True)
    (out / "ClientRequest.json").write_text(json.dumps({"oneOf": [
        {"properties": {"method": {"const": m}}} for m in
        ("initialize", "account/read", "account/rateLimits/read", "account/usage/read")]}))
    raise SystemExit(0)
mode = os.environ.get("CQL_FAKE_MODE", "normal")
log = os.environ.get("CQL_FAKE_LOG")
init_done = False
reads = 0

def emit(m):
    print(json.dumps(m), flush=True)

for line in sys.stdin:
    m = json.loads(line)
    if log:
        with open(log, "a") as f:
            f.write(json.dumps(m) + "\n")
    method, rid = m.get("method"), m.get("id")
    if method == "initialize":
        init_done = True
        emit({"id": rid, "result": {"userAgent": "synthetic"}})
    elif method == "initialized":
        pass
    elif not init_done:
        emit({"id": rid, "error": {"code": -32000, "message": "not initialized"}})
    elif method == "account/read":
        emit({"id": rid, "result": {"account": {"type": "chatgpt", "email": "synthetic@example.invalid", "planType": "pro"}}})
    elif method == "account/usage/read":
        if mode == "unsupported_usage":
            emit({"id": rid, "error": {"code": -32601, "message": "unknown method SECRET_TOKEN_MUST_NOT_LEAK"}})
        else:
            emit({"id": rid, "result": {"summary": {"lifetimeTokens": 10000}, "dailyUsageBuckets": [{"startDate": "2026-09-12", "tokens": 500}]}})
    elif method == "account/rateLimits/read":
        reads += 1
        if mode == "bad_json":
            print("not json SECRET_TOKEN_MUST_NOT_LEAK", flush=True)
            continue
        if mode == "oversize":
            print("x" * 100000, flush=True)
            continue
        if mode == "exit":
            raise SystemExit(3)
        if mode == "request":
            emit({"id": 55, "method": "item/commandExecution/requestApproval", "params": {"command": "NEVER_EXECUTE"}})
            continue
        if mode == "timeout":
            time.sleep(3)
            continue
        if mode == "error":
            emit({"id": rid, "error": {"code": -32000, "message": "SECRET_TOKEN_MUST_NOT_LEAK"}})
            continue
        if mode in ("notices", "partial", "changes"):
            emit({"method": "remoteControl/status/changed", "params": {"private": "SECRET_TOKEN_MUST_NOT_LEAK"}})
            emit({"id": rid + 500, "result": {"wrong": True}})
            emit({"method": "account/rateLimits/updated", "params": {"rateLimits": {"limitId": "codex", "primary": {"usedPercent": 11}}}})
        result = {"accountId": "synthetic-account-id", "ordinaryUsageAllowed": True,
                  "rateLimits": {"limitId": "codex", "planType": "pro",
                      "primary": {"usedPercent": 10 + (reads if mode == "changes" else 0), "windowDurationMins": 300, "resetsAt": 1790000000},
                      "secondary": {"usedPercent": 25, "windowDurationMins": 10080, "resetsAt": 1790300000},
                      "credits": {"hasCredits": False, "unlimited": False, "balance": "0"},
                      "spendControlReached": None},
                  "rateLimitsByLimitId": None, "rateLimitResetCredits": {"availableCount": 2, "credits": None},
                  "accessToken": "SECRET_TOKEN_MUST_NOT_LEAK"}
        reply = {"id": rid, "result": result}
        if mode == "partial":
            text = json.dumps(reply) + "\n"
            for i in range(0, len(text), 37):
                sys.stdout.write(text[i:i+37]); sys.stdout.flush()
        else:
            emit(reply)
    else:
        emit({"id": rid, "error": {"code": -32601, "message": "unknown method"}})
