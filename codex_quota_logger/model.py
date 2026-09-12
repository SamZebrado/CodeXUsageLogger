"""Privacy-preserving protocol projection and conservative interval tracking.

Account RPC responses are untrusted input. Only known quota fields survive the
projection. 'Raw' evidence elsewhere in the program means this redacted wire
projection, never arbitrary RPC payloads or credentials.
"""
from __future__ import annotations

import copy
import hashlib
import hmac
import json
import math
import re
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo


class ShapeError(ValueError):
    """Unsupported or malformed account response (no payload in message)."""


def number(value, *, integer=False, nonnegative=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value) or abs(value) > 10**20:
        return None
    if nonnegative and value < 0:
        return None
    if integer and int(value) != value:
        return None
    return int(value) if integer else value


def boolean(value):
    return value if type(value) is bool else None


def label(value):
    # No free-form strings, URLs, emails, headers, JWTs, or credential prefixes.
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./ -]{0,95}", value):
        return None
    if any(s in value.lower() for s in ("bearer", "sk-", "ghp_", "github_pat_", "eyj")):
        return None
    return value


def decimal_string(value):
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        return None
    text = str(value)
    if len(text) > 48 or not re.fullmatch(r"-?\d+(?:\.\d+)?", text):
        return None
    try:
        return text if Decimal(text).is_finite() else None
    except InvalidOperation:
        return None


def utc_string(epoch):
    if epoch is None:
        return None
    try:
        return datetime.fromtimestamp(epoch, timezone.utc).isoformat(timespec="seconds")
    except (ValueError, OverflowError, OSError):
        return None


def window(value):
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ShapeError("invalid_window")
    used = number(value.get("usedPercent"))
    if value.get("usedPercent") is not None and used is None:
        raise ShapeError("invalid_used_percent")
    return {
        "usedPercent": used,
        "windowDurationMins": number(value.get("windowDurationMins"), integer=True, nonnegative=True),
        "resetsAt": number(value.get("resetsAt"), integer=True, nonnegative=True),
    }


def bucket(value):
    if not isinstance(value, dict):
        raise ShapeError("invalid_bucket")
    credits = value.get("credits")
    if credits is not None and not isinstance(credits, dict):
        raise ShapeError("invalid_credits")
    limit = value.get("individualLimit")
    if limit is not None and not isinstance(limit, dict):
        raise ShapeError("invalid_spend_limit")
    return {
        "limitId": label(value.get("limitId")),
        "normalModelSlug": label(value.get("normalModelSlug")),
        "primary": window(value.get("primary")),
        "secondary": window(value.get("secondary")),
        "credits": None if credits is None else {
            "hasCredits": boolean(credits.get("hasCredits")),
            "unlimited": boolean(credits.get("unlimited")),
            "balance": decimal_string(credits.get("balance")),
        },
        "individualLimit": None if limit is None else {
            "limit": decimal_string(limit.get("limit")),
            "used": decimal_string(limit.get("used")),
            "remainingPercent": number(limit.get("remainingPercent")),
            "resetsAt": number(limit.get("resetsAt"), integer=True, nonnegative=True),
        },
        "spendControlReached": boolean(value.get("spendControlReached")),
        "planType": label(value.get("planType")),
        "rateLimitReachedType": label(value.get("rateLimitReachedType")),
    }


def project_quota(result):
    """Retain quota structure, decimals, nulls and all identifiable meter buckets."""
    if not isinstance(result, dict):
        raise ShapeError("invalid_quota_response")
    single = result.get("rateLimits")
    many = result.get("rateLimitsByLimitId")
    if single is None and many is None:
        raise ShapeError("missing_quota_buckets")
    if many is not None and not isinstance(many, dict):
        raise ShapeError("invalid_bucket_map")
    if many is not None and len(many) > 128:
        raise ShapeError("too_many_buckets")
    projected_many = None
    if many is not None:
        projected_many = {}
        for key, value in many.items():
            if label(key) is None or value is None:
                raise ShapeError("invalid_bucket_key_or_value")
            projected_many[key] = bucket(value)
    resets = result.get("rateLimitResetCredits")
    if resets is not None and not isinstance(resets, dict):
        raise ShapeError("invalid_reset_summary")
    reset_summary = None
    if resets is not None:
        # availableCount is authoritative; never substitute len(credits).
        reset_summary = {"availableCount": number(resets.get("availableCount"), integer=True, nonnegative=True)}
        details = resets.get("credits")
        if details is None:
            reset_summary["credits"] = None
        elif isinstance(details, list) and len(details) <= 1024:
            reset_summary["credits"] = [{
                "status": label(c.get("status")),
                "resetType": label(c.get("resetType")),
                "grantedAt": number(c.get("grantedAt"), integer=True, nonnegative=True),
                "expiresAt": number(c.get("expiresAt"), integer=True, nonnegative=True),
            } for c in details if isinstance(c, dict)]
        else:
            raise ShapeError("invalid_reset_details")
    return {
        "ordinaryUsageAllowed": boolean(result.get("ordinaryUsageAllowed")),
        "rateLimits": bucket(single) if single is not None else None,
        "rateLimitsByLimitId": projected_many,
        "rateLimitResetCredits": reset_summary,
    }


def account_context(account_result, quota_result, salt: bytes):
    """Use identity only in memory for a LOCAL salted fingerprint; omit originals."""
    account = account_result.get("account") if isinstance(account_result, dict) else None
    account = account if isinstance(account, dict) else {}
    ident = quota_result.get("accountId")
    if not isinstance(ident, str) or not ident:
        ident = account.get("accountId") or account.get("id") or account.get("email")
    fingerprint = None
    if isinstance(ident, str) and 0 < len(ident) <= 4096:
        fingerprint = hmac.new(salt, ident.encode(), hashlib.sha256).hexdigest()[:24]
    return {"account_fingerprint": fingerprint, "auth_mode": label(account.get("type")),
            "plan_type": label(account.get("planType"))}


def normalize(projected, context):
    many = projected["rateLimitsByLimitId"]
    result = dict(many or {})
    single = projected["rateLimits"]
    if single is not None:
        key = single.get("limitId")
        if not result:
            result[key or "legacy_default"] = single
        elif key and key not in result:
            result[key] = single
        elif not key and single not in result.values():
            result["legacy_default"] = single
    resets = projected["rateLimitResetCredits"]
    return {"context": copy.deepcopy(context), "buckets": result,
            "ordinary_usage_allowed": projected["ordinaryUsageAllowed"],
            "reset_summary": None if resets is None else {"availableCount": resets.get("availableCount")}}


def project_usage(result):
    if not isinstance(result, dict):
        raise ShapeError("invalid_usage_response")
    summary = result.get("summary")
    if summary is not None and not isinstance(summary, dict):
        raise ShapeError("invalid_usage_summary")
    out = {"summary": {}, "dailyUsageBuckets": None}
    for k in ("lifetimeTokens", "peakDailyTokens", "longestRunningTurnSec", "currentStreakDays", "longestStreakDays"):
        v = number((summary or {}).get(k), integer=True, nonnegative=True)
        if v is not None:
            out["summary"][k] = v
    buckets = result.get("dailyUsageBuckets")
    if buckets is None:
        return out
    if not isinstance(buckets, list) or len(buckets) > 5000:
        raise ShapeError("invalid_daily_usage")
    rows = []
    for row in buckets:
        if not isinstance(row, dict):
            raise ShapeError("invalid_daily_row")
        day = row.get("startDate")
        tokens = number(row.get("tokens"), integer=True, nonnegative=True)
        if not isinstance(day, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day) or tokens is None:
            raise ShapeError("invalid_daily_fields")
        try:
            datetime.strptime(day, "%Y-%m-%d")
        except ValueError:
            raise ShapeError("invalid_daily_date") from None
        rows.append({"startDate": day, "tokens": tokens})
    out["daily]sageBuckets"] = rows
    return out


def semantic_digest(normalized):
    return hashlib.sha256(json.dumps(normalized, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


COLUMNS = ["schema_version", "sample_id", "timestamp_utc", "timestamp_local", "timezone",
           "event_type", "trigger", "codex_version", "account_fingerprint", "auth_mode", "plan_type",
           "limit_id", "normal_model_slug", "bucket_present", "ordinary_usage_allowed"]
for w in ("primary", "secondary"):
    COLUMNS += [w + s for s in ("_used_percent", "_remaining_percent", "_window_minutes", "_resets_at", "_interval_id")]
COLUMNS += ["measurement_interval_id", "rate_limit_reached_type", "credits_has_credits", "credits_unlimited",
            "credits_balance", "spend_control_reached", "individual_limit", "individual_used",
            "individual_remaining_percent", "individual_resets_at", "earned_reset_count", "notes"]


class Tracker:
    """Per-window segment IDs: a primary rollover does not erase a weekly segment.

    No inferred consumption/dollars, and no inferred permission to resume using
    Codex. 'reset_boundary' denotes a metadata boundary, not its business cause.
    """
    def __init__(self, previous=None):
        self.previous = previous if isinstance(previous, dict) else None
        self.ids = {}

    def observe(self, current, now, tz="Asia/Shanghai", trigger="poll", force=False, codex_version="unknown", gap=False):
        before = self.previous
        changed = before != current
        if not changed and not force and not gap:
            return []
        sample = uuid.uuid4().hex
        ts = now.astimezone(timezone.utc).isoformat(timespec="seconds")
        local = now.astimezone(ZoneInfo(tz)).isoformat(timespec="seconds")
        curr_b = current["buckets"]
        old_b = before["buckets"] if before else {}
        ctx = current["context"]
        context_change = bool(before and before["context"] != ctx)
        global_change = bool(before and before.get("reset_summary") != current.get("reset_summary"))
        rows = []
        keys = sorted(set(old_b) | set(curr_b)) or ["no_buckets"]
        for key in keys:
            b = curr_b.get(key)
            old = old_b.get(key)
            events, notes = [], []
            if before is None:
                events.append("startup")
            elif gap:
                events.append("telemetry_recovered")
            if context_change:
                if before["context"].get("account_fingerprint") != ctx.get("account_fingerprint"):
                    events.append("account_change")
                else:
                    events.append("plan_or_auth_change")
            if key == "no_buckets":
                events.append("no_buckets")
            elif b is None:
                events.append("bucket_disappeared")
            elif old is None and before is not None:
                events.append("bucket_appeared")
            row = {"schema_version": 1, "sample_id": sample, "timestamp_utc": ts,
                   "timestamp_local": local, "timezone": tz, "trigger": trigger,
                   "codex_version": codex_version, "account_fingerprint": ctx.get("account_fingerprint"),
                   "auth_mode": ctx.get("auth_mode"), "plan_type": (b or {}).get("planType") or ctx.get("plan_type"),
                   "limit_id": key, "normal_model_slug": (b or {}).get("normalModelSlug"),
                   "bucket_present": b is not None, "ordinary_usage_allowed": current.get("ordinary_usage_allowed")}
            metadata_change = bool(old and b and (old.get("planType"), old.get("normalModelSlug"),
                (old.get("individualLimit") or {}).get("limit")) != (b.get("planType"), b.get("normalModelSlug"),
                (b.get("individualLimit") or {}).get("limit")))
            if metadata_change:
                events.append("plan_or_limit_change")
            for w in ("primary", "secondary"):
                cur_w = (b or {}).get(w)
                old_w = (old or {}).get(w)
                wid = (key, w)
                boundary = before is None or gap or context_change or metadata_change or old is None or b is None
                if (cur_w is None) != (old_w is None):
                    boundary = True
                    if before is not None:
                        events.append("window_availability_change")
                if cur_w and old_w:
                    if cur_w["resetsAt"] != old_w["resetsAt"]:
                        events.append("reset_boundary")
                        boundary = True
                    if cur_w["windowDurationMins"] != old_w["windowDurationMins"]:
                        events.append("window_change")
                        boundary = True
                    a, z = old_w["usedPercent"], cur_w["usedPercent"]
                    if a is not None and z is not None and z < a and not boundary:
                        events.append("possible_reset_or_correction")
                        boundary = True
                        notes.append(w + "_decrease_without_reset_metadata")
                    if (a is None) != (z is None):
                        boundary = True
                        events.append("measurement_availability_change")
                if boundary or wid not in self.ids:
                    self.ids[wid] = uuid.uuid4().hex
                row[w + "_interval_id"] = self.ids[wid] if cur_w else None
                if cur_w:
                    used = cur_w["usedPercent"]
                    row[w + "_used_percent"] = used
                    row[w + "_remaining_percent"] = 100 - used if used is not None else None
                    row[w + "_window_minutes"] = cur_w["windowDurationMins"]
                    row[w + "_resets_at"] = utc_string(cur_w["resetsAt"])
                    if used is not None and not 0 <= used <= 100:
                        events.append("anomaly")
                        notes.append(w + "_percent_outside_0_100")
                if b is None:
                    self.ids.pop(wid, None)
            combined = "|".join(str(row.get(w + "_interval_id") or "") for w in ("primary", "secondary"))
            row["measurement_interval_id"] = hashlib.sha256(combined.encode()).hexdigest()[:24]
            b = b or {}
            c, spend = b.get("credits") or {}, b.get("individualLimit") or {}
            row.update(rate_limit_reached_type=b.get("rateLimitReachedType"),
                       credits_has_credits=c.get("hasCredits"), credits_unlimited=c.get("unlimited"),
                       credits_balance=c.get("balance"), spend_control_reached=b.get("spendControlReached"),
                       individual_limit=spend.get("limit"), individual_used=spend.get("used"),
                       individual_remaining_percent=spend.get("remainingPercent"), individual_resets_at=utc_string(spend.get("resetsAt")),
                       earned_reset_count=(current.get("reset_summary") or {}).get("availableCount"))
            if before and before.get("ordinary_usage_allowed") != current.get("ordinary_usage_allowed"):
                allowed = current.get("ordinary_usage_allowed")
                events.append("limit_reached" if allowed is False else "usage_allowed" if allowed is True else "permission_unavailable")
            if b.get("rateLimitReachedType") and (not old or old.get("rateLimitReachedType") != b.get("rateLimitReachedType")):
                events.append("limit_reached")
            if global_change:
                events.append("reset_credit_change")
            if changed and not events:
                events.append("change")
            if force and trigger == "heartbeat":
                events.append("heartbeat")
            row["event_type"] = "|".join(dict.fromkeys(events)) or trigger
            if not ctx.get("account_fingerprint"):
                notes.append("account_identity_unavailable")
            row["notes"] = "|".join(notes)
            rows.append(row)
        self.previous = copy.deepcopy(current)
        return rows
