import importlib
import hashlib
import hmac
import os
import tempfile
import time
import unittest

from fastapi.testclient import TestClient
from starlette.requests import Request

from lodia.limits import rate_limit_key
from lodia.store import LodiaStore


ENV_KEYS = [
    "LODIA_DATA_DIR",
    "LODIA_ENV",
    "LODIA_RATE_LIMIT_ENABLED",
    "LODIA_RATE_LIMIT_REQUESTS",
    "LODIA_RATE_LIMIT_WINDOW_SECONDS",
    "LODIA_MAX_REQUEST_BODY_BYTES",
    "LODIA_REQUIRE_REQUEST_SIGNATURE",
    "LODIA_REQUEST_SIGNATURE_SECRET",
    "POSTGRES_DSN",
    "DATABASE_URL",
]


class ProductionGuardrailTests(unittest.TestCase):
    def test_ready_endpoint_checks_database_and_object_storage(self):
        with isolated_env() as tmp:
            os.environ["LODIA_DATA_DIR"] = tmp
            import main

            importlib.reload(main)
            client = TestClient(main.app)

            response = client.get("/api/ready")

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertTrue(payload["ok"])
            self.assertTrue(payload["database"]["ok"])
            self.assertTrue(payload["object_storage"]["ok"])
            self.assertIn("X-Request-ID", response.headers)

    def test_rate_limit_blocks_excess_requests(self):
        with isolated_env() as tmp:
            os.environ["LODIA_DATA_DIR"] = tmp
            os.environ["LODIA_RATE_LIMIT_ENABLED"] = "true"
            os.environ["LODIA_RATE_LIMIT_REQUESTS"] = "1"
            os.environ["LODIA_RATE_LIMIT_WINDOW_SECONDS"] = "60"
            import main

            importlib.reload(main)
            client = TestClient(main.app)
            payload = {"owner_id": "demo", "text": "请分析一个高质量任务案例。", "allowed_uses": ["candidate_pool"]}

            first = client.post("/api/pipeline/preview", json=payload)
            second = client.post("/api/pipeline/preview", json=payload)

            self.assertEqual(first.status_code, 200)
            self.assertEqual(second.status_code, 429)
            self.assertEqual(second.json()["detail"], "rate_limit_exceeded")
            self.assertEqual(second.headers["X-RateLimit-Limit"], "1")

    def test_request_body_limit_rejects_oversized_payload(self):
        with isolated_env() as tmp:
            os.environ["LODIA_DATA_DIR"] = tmp
            os.environ["LODIA_MAX_REQUEST_BODY_BYTES"] = "64"
            import main

            importlib.reload(main)
            client = TestClient(main.app)

            response = client.post(
                "/api/pipeline/preview",
                json={"owner_id": "demo", "text": "x" * 200, "allowed_uses": ["candidate_pool"]},
            )

            self.assertEqual(response.status_code, 413)
            self.assertEqual(response.json()["detail"], "request_body_too_large")

    def test_store_lists_are_paginated_and_filterable(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = LodiaStore(data_dir=tmp)
            first = store.submit_text("alice", "整理一个客服场景任务。", ["candidate_pool"])["case"]
            second = store.submit_text("bob", "整理一个运营分析任务。", ["candidate_pool"])["case"]
            third = store.submit_text("alice", "整理一个销售跟进任务。", ["candidate_pool"])["case"]

            page = store.list_cases(limit=1, offset=1)
            alice_cases = store.list_cases(limit=10, owner_id="alice")

            self.assertEqual(len(page), 1)
            self.assertIn(page[0]["case_id"], {first["case_id"], second["case_id"], third["case_id"]})
            self.assertEqual({item["owner_id"] for item in alice_cases}, {"alice"})
            self.assertEqual(len(alice_cases), 2)

    def test_rate_limit_key_ignores_forwarded_for_by_default(self):
        with isolated_env() as tmp:
            os.environ["LODIA_DATA_DIR"] = tmp
            import main

            importlib.reload(main)

            captured = {}

            @main.app.get("/api/_test/rate-key")
            async def test_rate_key(request: Request):
                captured["default"] = rate_limit_key(request)
                captured["trusted"] = rate_limit_key(request, trust_proxy_headers=True)
                return captured

            client = TestClient(main.app)
            response = client.get("/api/_test/rate-key", headers={"X-Forwarded-For": "203.0.113.9"})

            self.assertEqual(response.status_code, 200)
            self.assertNotEqual(captured["default"], captured["trusted"])
            self.assertTrue(captured["default"].startswith("ip:"))

    def test_request_signature_can_protect_mutation_endpoints(self):
        with isolated_env() as tmp:
            os.environ["LODIA_DATA_DIR"] = tmp
            os.environ["LODIA_REQUIRE_REQUEST_SIGNATURE"] = "true"
            os.environ["LODIA_REQUEST_SIGNATURE_SECRET"] = "secret"
            import main

            importlib.reload(main)
            client = TestClient(main.app)
            body = b'{"owner_id":"demo","text":"signed case","allowed_uses":["candidate_pool"]}'

            blocked = client.post("/api/pipeline/preview", content=body, headers={"Content-Type": "application/json"})
            self.assertEqual(blocked.status_code, 401)

            timestamp = str(int(time.time()))
            body_hash = hashlib.sha256(body).hexdigest()
            signed = f"{timestamp}.POST./api/pipeline/preview.{body_hash}".encode("utf-8")
            signature = hmac.new(b"secret", signed, hashlib.sha256).hexdigest()
            allowed = client.post(
                "/api/pipeline/preview",
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "X-Lodia-Timestamp": timestamp,
                    "X-Lodia-Signature": signature,
                },
            )
            self.assertEqual(allowed.status_code, 200)

    def test_signature_headers_are_allowed_by_cors(self):
        with isolated_env() as tmp:
            os.environ["LODIA_DATA_DIR"] = tmp
            import main

            importlib.reload(main)
            client = TestClient(main.app)

            response = client.options(
                "/api/pipeline/preview",
                headers={
                    "Origin": "http://localhost:5173",
                    "Access-Control-Request-Method": "POST",
                    "Access-Control-Request-Headers": "X-Lodia-Signature,X-Lodia-Timestamp,X-Request-ID",
                },
            )

            self.assertEqual(response.status_code, 200)
            allowed_headers = response.headers["access-control-allow-headers"].lower()
            self.assertIn("x-lodia-signature", allowed_headers)
            self.assertIn("x-lodia-timestamp", allowed_headers)
            self.assertIn("x-request-id", allowed_headers)


class isolated_env:
    def __enter__(self):
        self.old_env = {key: os.environ.get(key) for key in ENV_KEYS}
        for key in ENV_KEYS:
            os.environ.pop(key, None)
        self.tmp = tempfile.TemporaryDirectory()
        return self.tmp.name

    def __exit__(self, exc_type, exc, traceback):
        self.tmp.cleanup()
        for key, value in self.old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        return False


if __name__ == "__main__":
    unittest.main()
