import importlib
import os
import tempfile
import unittest

from fastapi.testclient import TestClient

from lodia.store import LodiaStore


ENV_KEYS = [
    "LODIA_DATA_DIR",
    "LODIA_ENV",
    "LODIA_ADMIN_TOKEN",
    "LODIA_REVIEWER_TOKEN",
    "LODIA_CONTRIBUTOR_TOKEN",
    "POSTGRES_DSN",
    "DATABASE_URL",
]


class UploadSessionsAndMetricsTests(unittest.TestCase):
    def test_direct_upload_session_completes_into_asset_pipeline(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = LodiaStore(data_dir=tmp)
            content = b"agent trace task with goals steps tool results acceptance criteria reusable rules"
            created = store.create_asset_upload_session(
                owner_id="upload_owner",
                filename="trace.txt",
                media_type="text/plain",
                byte_size=len(content),
                allowed_uses=["commercial_dataset", "training"],
            )
            session = created["session"]
            self.assertFalse(created["upload"]["direct_upload_supported"])

            with self.assertRaisesRegex(ValueError, "upload_object_not_readable"):
                store.complete_asset_upload_session(session["id"])
            self.assertEqual(store.get_asset_upload_session(session["id"])["status"], "pending")

            store.objects.put_bytes(session["object_key"], content, "text/plain")
            completed = store.complete_asset_upload_session(session["id"])

            self.assertEqual(completed["asset"]["status"], "evidence_ready")
            self.assertEqual(len(store.list_cases(owner_id="upload_owner")), 1)
            self.assertEqual(store.get_asset_upload_session(session["id"])["status"], "completed")
            with self.assertRaisesRegex(ValueError, "upload_session_not_pending"):
                store.complete_asset_upload_session(session["id"])

    def test_upload_session_api_scopes_owner_and_hides_internal_uri(self):
        with isolated_env() as tmp:
            os.environ["LODIA_ENV"] = "production"
            os.environ["LODIA_DATA_DIR"] = tmp
            os.environ["LODIA_CONTRIBUTOR_TOKEN"] = "contributor-token"
            import main

            importlib.reload(main)
            client = TestClient(main.app)
            response = client.post(
                "/api/assets/upload-sessions",
                headers={"Authorization": "Bearer contributor-token"},
                json={
                    "owner_id": "spoofed",
                    "filename": "trace.txt",
                    "media_type": "text/plain",
                    "byte_size": 32,
                    "allowed_uses": ["candidate_pool"],
                },
            )

            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["session"]["owner_id"], "contributor")
            self.assertNotIn("object_uri", payload["session"])
            self.assertNotIn("object_key", payload["session"])
            self.assertNotIn("object_uri", payload["upload"])
            self.assertNotIn("object_key", payload["upload"])

    def test_prometheus_and_vendor_processing_records_are_emitted(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = LodiaStore(data_dir=tmp)
            submitted = store.submit_text(
                owner_id="metrics_owner",
                text="请分析这个任务案例，输出目标、过程、结果、验收标准和复用规则。",
                allowed_uses=["commercial_dataset", "training"],
            )

            records = store.list_vendor_processing_records(entity_id=submitted["case"]["case_id"])
            metrics_text = store.prometheus_metrics()

            self.assertEqual(records[0]["provider"], "local_rules")
            self.assertEqual(records[0]["data_category"], "redacted_case_content")
            self.assertIn("lodia_service_ready 1", metrics_text)
            self.assertIn("lodia_model_invocations_total", metrics_text)

    def test_tenant_metadata_flows_into_users_and_auth_context(self):
        with isolated_env() as tmp:
            os.environ["LODIA_ENV"] = "production"
            os.environ["LODIA_DATA_DIR"] = tmp
            os.environ["LODIA_ADMIN_TOKEN"] = "admin-token"
            import main

            importlib.reload(main)
            client = TestClient(main.app)
            tenant = client.post(
                "/api/admin/tenants",
                headers={"Authorization": "Bearer admin-token"},
                json={"id": "Enterprise-A", "name": "Enterprise A"},
            )
            self.assertEqual(tenant.status_code, 200)
            self.assertEqual(tenant.json()["id"], "enterprise_a")

            created = client.post(
                "/api/admin/users",
                headers={"Authorization": "Bearer admin-token"},
                json={
                    "tenant_id": "enterprise_a",
                    "email": "tenant-user@lodia.local",
                    "password": "very-strong-password",
                    "display_name": "Tenant User",
                    "roles": ["contributor"],
                },
            )
            self.assertEqual(created.status_code, 200)
            self.assertEqual(created.json()["tenant_id"], "enterprise_a")
            login = client.post("/api/auth/login", json={"email": "tenant-user@lodia.local", "password": "very-strong-password"})
            token = login.json()["token"]
            me = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
            self.assertEqual(me.json()["tenant_id"], "enterprise_a")


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
