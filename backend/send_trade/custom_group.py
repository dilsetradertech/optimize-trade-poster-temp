import httpx
from datetime import datetime
from pytz import timezone
import re

IST = timezone("Asia/Kolkata")

BOT_TOKEN = "bot_c85e487415114b759bca723761e9ec50e10f53daa39fbc430487206f2965ce7b"
API_URL = "https://staging-api.dilsetrader.in"


def normalize_tradingview_image(url: str | None) -> str | None:
    if not url:
        return None

    if "s3.tradingview.com/snapshots" in url:
        return url

    match = re.search(r"tradingview\.com/x/([A-Za-z0-9]+)/?", url)
    if not match:
        return url

    code = match.group(1)
    first_char = code[0].lower()

    return f"https://s3.tradingview.com/snapshots/{first_char}/{code}.png"

async def send_to_custom_group(data, trade_id=None):
    print("=========call custom group file ================")

    headers = {
        "Authorization": BOT_TOKEN,
        "Content-Type": "application/json"
    }
    entry_price = data.get("entryPrice", 0)
    security_id = data.get("security_id")
    print(f"========={security_id}=================")
    trade_id = trade_id or data.get("id")

    entry_range = f"{entry_price * 0.98:.1f} - {entry_price * 1.02:.1f}"

    text = f"""
New F&O Trade Alert 🔥🔥📢📢
📌 Trade Details:
• Enter: {data.get("scrip")}
• Action: {"BUY" if data.get("position_type") == "LONG" else "SELL"}
• Trade Type: {data.get("tradeType")}
• Entry Price Range: {entry_range}
• Stop Loss: {data.get("stoploss")}
• Target 1: {data.get("target1")}
• Target 2: {data.get("target2")}
• Target 3: {data.get("target3")}

⏳ Trade Given at: {datetime.now(IST).strftime("%I:%M %p || %Y-%m-%d")}
📝 Rationale: {data.get("reason")}
📊 Chart: {data.get("chart_url")}
✅ Disclaimer: https://dilsetrader.in/disclaimer/
"""
    payload = {
        "groupIds": ["176e74ea-5d87-4f23-9622-3bd9d065de00"],
        "textContent": text,
        "mediaUrl": normalize_tradingview_image(data.get("chart_url")),
        "msgType": "IMAGE",
        "extraInfo": {
            "securityId": str(security_id) if security_id else None,
            
        },
        # "buttons": [
        #     {
        #         "text": "✅Order",
        #         "order": 0,
        #         "action": "place_order",
        #         "actionData": {
        #             "targetBotId": "65532658-9452-44b4-8657-198929a9e752",
        #             "botUsername": "@bot"
        #         },
        #         "style": "SUCCESS"
        #     }
        # ]
    }
    async with httpx.AsyncClient() as client:
        try:
            res = await client.post(
                f"{API_URL}/api/bot/send-poster",
                json=payload,
                headers=headers
            )
            res.raise_for_status()
            print("================++++++++++Custom group response:", res.json())
            print("✅ Custom group message sent")

        except Exception as e:
            print("❌ Custom group send failed:", e)