import os
import boto3
from decimal import Decimal
import json
import time

# DynamoDB setup
dynamodb = boto3.resource("dynamodb")
RATES_TABLE = os.environ.get("RATES_TABLE", "Rates")
table = dynamodb.Table(RATES_TABLE)

def lambda_handler(event, context):
    """
    Lambda handler that inserts one rate entry into the Rates table.
    Expects an event like:
      {
        "base": "USD",
        "currency": "EUR",
        "rate": 0.9204
      }
    """

    base = event.get("base", "USD").upper()
    currency = event.get("currency", "EUR").upper()
    rate = Decimal(str(event.get("rate", 0.9204)))

    # Put or update the item
    table.update_item(
        Key={"base": base},
        UpdateExpression="SET rates.#cur = :r, asOf = :asOf, cachedAt = :now",
        ExpressionAttributeNames={"#cur": currency},
        ExpressionAttributeValues={
            ":r": rate,
            ":asOf": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            ":now": int(time.time())
        }
    )

    return {
        "statusCode": 200,
        "body": json.dumps({
            "message": f"Added rate {base}->{currency} = {rate}",
            "base": base,
            "currency": currency,
            "rate": float(rate)
        })
    }
