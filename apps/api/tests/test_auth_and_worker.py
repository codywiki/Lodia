import importlib
import os
import tempfile
import unittest

from fastapi.testclient import TestClient

from lodia.store import LodiaStore
from worker import process_one


class AuthAndWorkerTests(unittest.TestCase):
    def test_production_auth_blocks_anonymous_and_allows_admin_audit(self):
        keys = [
            "LODIA_ENV",
            "LODIA_DATA_DIR",
            "LODIA_ADMIN_TOKEN",
            "LODIA_CONTRIBUTOR_TOKEN",
            "LODIA_REVIEWER_TOKEN",
            "LODIA_AUTH_TOKENS",
            "POSTGRES_DSN",
            "DATABASE_URL",
        ]
        old_env = {key: os.environ.get(key) for key in keys}
        try:
            with tempfile.TemporaryDirectory() as tmp:
                os.environ["LODIA_ENV"] = "production"
                os.environ["LODIA_DATA_DIR"] = tmp
                os.environ["LODIA_ADMIN_TOKEN"] = "admin-token"
                os.environ["LODIA_CONTRIBUTOR_TOKEN"] = "contributor-token"
                os.environ.pop("POSTGRES_DSN", None)
                os.environ.pop("DATABASE_URL", None)

                import main

                importlib.reload(main)
                client = TestClient(main.app)

                blocked = client.post(
                    "/api/submissions/text",
                    json={"owner_id": "ignored", "text": "请分析一个可复用案例。", "allowed_uses": ["commercial_dataset"]},
                )
                self.assertEqual(blocked.status_code, 401)

                submitted = client.post(
                    "/api/submissions/text",
                    headers={"Authorization": "Bearer contributor-token"},
                    json={"owner_id": "ignored", "text": "请分析一个可复用案例。", "allowed_uses": ["commercial_dataset"]},
                )
                self.assertEqual(submitted.status_code, 200)
                self.assertEqual(submitted.json()["case"]["owner_id"], "contributor")

                audit = client.get("/api/audit/logs", headers={"Authorization": "Bearer admin-token"})
                self.assertEqual(audit.status_code, 200)
                self.assertGreaterEqual(len(audit.json()["items"]), 1)
        finally:
            for key, value in old_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_worker_processes_queued_submission(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = LodiaStore(data_dir=tmp)
            submitted = store.submit_text(
                owner_id="contributor_queue",
                text="请分析这个客服投诉案例，要求输出处理步骤、验收结果和可复用规则。",
                allowed_uses=["commercial_dataset", "training"],
                enqueue=True,
            )
            self.assertEqual(submitted["status"], "queued")
            self.assertEqual(len(store.list_jobs()), 1)

            self.assertTrue(process_one(store=store, queue_name="ingestion", worker_id="test-worker"))
            self.assertEqual(len(store.list_cases()), 1)
            self.assertEqual(store.list_jobs()[0]["status"], "completed")
            self.assertTrue(any(item["event_type"] == "job.completed" for item in store.list_audit_logs()))

    def test_processing_same_submission_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = LodiaStore(data_dir=tmp)
            submitted = store.submit_text(
                owner_id="contributor_idempotent",
                text="整理这个产品评测任务，输出目标、过程、反馈和验收标准。",
                allowed_uses=["commercial_dataset"],
                enqueue=True,
            )

            first = store.process_submission(submitted["submission_id"], actor_id="test-worker")
            second = store.process_submission(submitted["submission_id"], actor_id="test-worker")

            self.assertEqual(first["case_id"], second["case_id"])
            self.assertEqual(len(store.list_cases()), 1)


if __name__ == "__main__":
    unittest.main()
