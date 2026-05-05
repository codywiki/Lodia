import importlib
import os
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from lodia.store import LodiaStore


ENV_KEYS = [
    "LODIA_DATA_DIR",
    "LODIA_ENV",
    "LODIA_ADMIN_TOKEN",
    "LODIA_REVIEWER_TOKEN",
    "LODIA_CONTRIBUTOR_TOKEN",
    "LODIA_AUTH_TOKENS",
    "POSTGRES_DSN",
    "DATABASE_URL",
    "REDIS_URL",
    "LODIA_QUEUE_BACKEND",
]


class P0FoundationTests(unittest.TestCase):
    def test_db_backed_login_token_can_submit_and_be_revoked(self):
        with isolated_env() as tmp:
            os.environ["LODIA_ENV"] = "production"
            os.environ["LODIA_DATA_DIR"] = tmp
            os.environ["LODIA_ADMIN_TOKEN"] = "admin-token"
            import main

            importlib.reload(main)
            client = TestClient(main.app)

            created = client.post(
                "/api/admin/users",
                headers={"Authorization": "Bearer admin-token"},
                json={
                    "email": "Contributor@Lodia.local",
                    "password": "very-strong-password",
                    "display_name": "Contributor",
                    "roles": ["contributor"],
                },
            )
            self.assertEqual(created.status_code, 200)

            login = client.post(
                "/api/auth/login",
                json={"email": "contributor@lodia.local", "password": "very-strong-password"},
            )
            self.assertEqual(login.status_code, 200)
            login_payload = login.json()
            token = login_payload["token"]

            submitted = client.post(
                "/api/submissions/text",
                headers={"Authorization": f"Bearer {token}"},
                json={"owner_id": "spoofed", "text": "整理一个客服任务案例。", "allowed_uses": ["candidate_pool"]},
            )
            self.assertEqual(submitted.status_code, 200)
            self.assertEqual(submitted.json()["case"]["owner_id"], login_payload["user"]["id"])

            revoked = client.post(
                f"/api/admin/tokens/{login_payload['id']}/revoke",
                headers={"Authorization": "Bearer admin-token"},
            )
            self.assertEqual(revoked.status_code, 200)

            blocked = client.post(
                "/api/submissions/text",
                headers={"Authorization": f"Bearer {token}"},
                json={"owner_id": "ignored", "text": "再次提交。", "allowed_uses": ["candidate_pool"]},
            )
            self.assertEqual(blocked.status_code, 401)

    def test_review_queue_reject_contract_metrics_and_raw_purge(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = LodiaStore(data_dir=tmp)
            submitted = store.submit_text(
                owner_id="contributor_p0",
                text="请分析这个客服投诉案例，要求输出处理步骤、验收结果和可复用规则。",
                allowed_uses=["commercial_dataset", "training"],
            )
            case_id = submitted["case"]["case_id"]
            self.assertEqual(len(store.list_review_queue()), 1)

            rejected = store.reject_case(case_id, reviewer_id="reviewer_p0", reason="quality gap")
            self.assertEqual(rejected["status"], "rejected")

            submitted = store.submit_text(
                owner_id="contributor_p0",
                text="请分析这个销售跟进案例，要求输出任务目标、执行过程、验收结果和复用规则。",
                allowed_uses=["commercial_dataset", "training"],
            )
            approved = store.approve_case(submitted["case"]["case_id"], reviewer_id="reviewer_p0")
            dataset = store.create_dataset(
                name="P0 Dataset",
                purpose="commercial_dataset",
                min_drl="DRL3",
                gross_revenue_cents=100_000,
                direct_cost_cents=20_000,
            )
            self.assertEqual(dataset["contract_status"], "passed")
            contract = store.get_data_contract(dataset["id"])
            self.assertEqual(contract["contract"]["dataset_id"], dataset["id"])
            self.assertEqual(contract["contract"]["case_ids"], [approved["case_id"]])

            metrics = store.metrics_snapshot()
            self.assertGreaterEqual(metrics["cases"].get("commercial_ready", 0), 1)
            self.assertGreaterEqual(metrics["pending_payout_cents"], 1)

            approval = store.create_approval_request(
                operation_type="dataset_export",
                entity_type="dataset",
                entity_id=dataset["id"],
                reason="enterprise delivery",
                payload={"dataset_id": dataset["id"]},
                actor_id="admin_p0",
            )
            decided = store.decide_approval_request(approval["id"], "approved", "ok", actor_id="admin_p0")
            self.assertEqual(decided["status"], "approved")

            raw_file = next(Path(tmp, "raw").glob("*.txt"))
            with store._session() as conn:
                store._execute(conn, "UPDATE submissions SET raw_expires_at = ?", ("2000-01-01T00:00:00+00:00",))
            purge = store.purge_expired_raw_objects(limit=10)
            self.assertGreaterEqual(purge["purged_count"], 1)
            self.assertFalse(raw_file.exists())

    def test_tokens_validate_expiry_and_jobs_are_claimed_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = LodiaStore(data_dir=tmp)
            user = store.create_user(
                email="expiry@lodia.local",
                password="very-strong-password",
                roles=["contributor"],
            )

            with self.assertRaisesRegex(ValueError, "invalid_expires_at"):
                store.create_api_token(user["id"], "bad-expiry", expires_at="not-a-date")

            job = store.enqueue_job("test_job", {"ok": True}, queue_name="ingestion")
            first = store.claim_job_by_id(job["id"], worker_id="worker_one")
            second = store.claim_job_by_id(job["id"], worker_id="worker_two")

            self.assertIsNotNone(first)
            self.assertIsNone(second)


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
