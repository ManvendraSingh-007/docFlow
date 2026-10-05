import hmac
import hashlib
import time
import uuid
import httpx
from typing import Dict, Any, Tuple
from backend.config import settings

class WebhookDispatcher:
    """
    Outbound Webhook Dispatcher with HMAC-SHA256 signatures,
    latency tracking, and payload formatting.
    """

    @classmethod
    def generate_signature(cls, payload_bytes: bytes, secret: str) -> str:
        return hmac.new(
            secret.encode("utf-8"),
            payload_bytes,
            hashlib.sha256
        ).hexdigest()

    @classmethod
    async def dispatch(cls, url: str, payload: Dict[str, Any], secret: str = None) -> Tuple[bool, int, str, float]:
        """
        Sends payload to the designated webhook URL.
        Returns: (success: bool, status_code: int, response_text: str, duration_ms: float)
        """
        if not url:
            return False, 0, "No webhook URL configured", 0.0

        secret = secret or settings.webhook_secret
        import json
        payload_json = json.dumps(payload, ensure_ascii=False)
        payload_bytes = payload_json.encode("utf-8")

        signature = cls.generate_signature(payload_bytes, secret)
        delivery_id = str(uuid.uuid4())
        timestamp = str(int(time.time()))

        headers = {
            "Content-Type": "application/json",
            "User-Agent": "DocFlow-Webhook-Dispatcher/1.0",
            "X-DocFlow-Delivery-Id": delivery_id,
            "X-DocFlow-Timestamp": timestamp,
            "X-DocFlow-Signature": f"sha256={signature}"
        }

        start_time = time.perf_counter()
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(url, content=payload_bytes, headers=headers)
                duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
                success = 200 <= response.status_code < 300
                return success, response.status_code, response.text[:500], duration_ms
        except Exception as e:
            duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
            return False, 500, f"Dispatch Error: {str(e)}", duration_ms
