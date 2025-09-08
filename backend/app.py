# app.py
import os
import json
from decimal import Decimal, ROUND_HALF_UP
import boto3
from typing import Optional, Dict, Any

dynamodb = boto3.resource("dynamodb")
RATES_TABLE = os.environ.get("RATES_TABLE", "Rates")
CANONICAL_BASE = os.environ.get("CANONICAL_BASE", "USD")  # used for cross-rate fallback
rates_table = dynamodb.Table(RATES_TABLE)

def _json(status: int, body: Dict[str, Any]):
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": "*"
        },
        "body": json.dumps(body)
    }

def _round4(x: Decimal) -> float:
    return float(x.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))

def _to_dec(x) -> Decimal:
    # Normalize incoming numeric types/strings to Decimal safely
    return Decimal(str(x))

def get_rates_for_base(base: str) -> Optional[Dict[str, Any]]:
    """
    Reads a single item from DynamoDB Rates table:
      Key: { base: "<BASE>" }
    Expected item shape:
      { "base": "USD", "rates": {"EUR": 0.9204, ...}, "asOf": "...", "cachedAt": 1693838400 }
    """
    res = rates_table.get_item(Key={"base": base.upper()})
    return res.get("Item")

def compute_rate(from_cur: str, to_cur: str) -> Optional[Dict[str, Any]]:
    """
    Computes rate(from->to) using DynamoDB-stored rates only.
    Tries in order:
      1) Direct: item where base == from_cur and to_cur in rates
      2) Cross-rate using CANONICAL_BASE (default USD): rate(base->to)/rate(base->from)
      3) Inverse using item where base == to_cur: rate(to->from) then invert
    Returns:
      { "rate": Decimal, "asOf": "...", "provenance": "direct|cross|inverse", "baseUsed": "<BASE>" }
      or None if cannot compute.
    """
    from_cur = from_cur.upper()
    to_cur = to_cur.upper()

    # 1) Direct (base == from)
    item_from = get_rates_for_base(from_cur)
    if item_from and "rates" in item_from and to_cur in item_from["rates"]:
        rate = _to_dec(item_from["rates"][to_cur])
        return {
            "rate": rate,
            "asOf": item_from.get("asOf"),
            "provenance": "direct",
            "baseUsed": from_cur
        }

    # 2) Cross-rate using canonical base (e.g., USD)
    item_base = get_rates_for_base(CANONICAL_BASE)
    if item_base and "rates" in item_base:
        rates_map = item_base["rates"]
        if from_cur in rates_map and to_cur in rates_map:
            # rate(from->to) = rate(base->to) / rate(base->from)
            rate_to = _to_dec(rates_map[to_cur])
            rate_from = _to_dec(rates_map[from_cur])
            if rate_from != 0:
                rate = rate_to / rate_from
                return {
                    "rate": rate,
                    "asOf": item_base.get("asOf"),
                    "provenance": "cross",
                    "baseUsed": CANONICAL_BASE
                }

    # 3) Inverse using item where base == to (if it contains from)
    item_to = get_rates_for_base(to_cur)
    if item_to and "rates" in item_to and from_cur in item_to["rates"]:
        # item_to["rates"][from] means: 1 to_cur = r from_cur ⇒ 1 from_cur = 1/r to_cur
        r = _to_dec(item_to["rates"][from_cur])
        if r != 0:
            rate = Decimal(1) / r
            return {
                "rate": rate,
                "asOf": item_to.get("asOf"),
                "provenance": "inverse",
                "baseUsed": to_cur
            }

    return None

def lambda_handler(event, context):
    """
    HTTP API (API Gateway v2) Lambda handler.
    Expects query params: from, to, amount
    Uses only DynamoDB (boto3) for rates; no external HTTP fetch here.
    """
    qs = event.get("queryStringParameters") or {}
    from_cur = (qs.get("from") or "").upper()
    to_cur = (qs.get("to") or "").upper()
    amount_raw = qs.get("amount")

    # Basic validation
    if not from_cur or not to_cur or amount_raw is None:
        return _json(400, {"error": "Required query params: from, to, amount"})
    if len(from_cur) != 3 or len(to_cur) != 3:
        return _json(400, {"error": "Currency codes must be 3 letters (ISO 4217)"})

    try:
        amount = _to_dec(amount_raw)
        if amount < 0:
            return _json(400, {"error": "Amount must be non-negative"})
    except Exception:
        return _json(400, {"error": "Amount must be a valid number"})

    # Trivial identity conversion
    if from_cur == to_cur:
        return _json(200, {
            "from": from_cur,
            "to": to_cur,
            "amount": float(amount),
            "rate": 1.0,
            "result": _round4(amount),
            "asOf": None,
            "provenance": "identity"
        })

    rate_info = compute_rate(from_cur, to_cur)
    if not rate_info:
        # Could not compute from DynamoDB-only data
        return _json(404, {
            "error": f"Cannot compute {from_cur}->{to_cur} from cached rates only",
            "hint": f"Ensure Rates table has base='{from_cur}' with '{to_cur}' in rates "
                    f"OR base='{CANONICAL_BASE}' with both '{from_cur}' and '{to_cur}' present."
        })

    rate = rate_info["rate"]
    result = amount * rate

    return _json(200, {
        "from": from_cur,
        "to": to_cur,
        "amount": float(amount),
        "rate": float(rate.quantize(Decimal('0.0001'), rounding=ROUND_HALF_UP)),
        "result": _round4(result),
        "asOf": rate_info.get("asOf"),
        "provenance": rate_info.get("provenance"),
        "baseUsed": rate_info.get("baseUsed")
    })
