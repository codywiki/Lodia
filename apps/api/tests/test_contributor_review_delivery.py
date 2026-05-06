import importlib
import json
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
    "LODIA_AUTH_TOKENS",
    "POSTGRES_DSN",
    "DATABASE_URL",
]


class ContributorReviewDeliveryTests(unittest.TestCase):
    def test_contributor_dashboard_is_self_scoped_and_dataset_paths_stay_internal(self):
        with isolated_env() as tmp:
            os.environ["LODIA_ENV"] = "production"
            os.environ["LODIA_DATA_DIR"] = tmp
            os.environ["LODIA_ADMIN_TOKEN"] = "admin-token"
            os.environ["LODIA_CONTRIBUTOR_TOKEN"] = "contributor-token"
            os.environ["LODIA_REVIEWER_TOKEN"] = "reviewer-token"
            import main

            importlib.reload(main)
            client = TestClient(main.app)
            submitted = client.post(
                "/api/submissions/text",
                headers={"Authorization": "Bearer contributor-token"},
                json={
                    "owner_id": "spoofed-owner",
                    "text": "请复盘一个客服任务案例，输出目标、过程、结果、验收标准和可复用规则。",
                    "allowed_uses": ["commercial_dataset", "training"],
                },
            )
            self.assertEqual(submitted.status_code, 200)
            self.assertEqual(submitted.json()["case"]["owner_id"], "contributor")

            dashboard = client.get("/api/contributor/dashboard", headers={"Authorization": "Bearer contributor-token"})
            cases = client.get("/api/contributor/cases", headers={"Authorization": "Bearer contributor-token"})
            self.assertEqual(dashboard.status_code, 200)
            self.assertEqual(dashboard.json()["cases"]["total"], 1)
            self.assertEqual({item["owner_id"] for item in cases.json()["items"]}, {"contributor"})

            approved = client.post(
                f"/api/review/{submitted.json()['case']['case_id']}/approve",
                headers={"Authorization": "Bearer reviewer-token"},
                json={},
            )
            self.assertEqual(approved.status_code, 200)
            dataset = client.post(
                "/api/datasets",
                headers={"Authorization": "Bearer admin-token"},
                json={
                    "name": "Scoped Dataset",
                    "purpose": "commercial_dataset",
                    "min_drl": "DRL3",
                    "gross_revenue_cents": 100000,
                    "direct_cost_cents": 20000,
                },
            )
            self.assertEqual(dataset.status_code, 200)
            self.assertNotIn("data_path", dataset.json())
            self.assertNotIn("manifest_path", dataset.json())

    def test_review_conflict_returns_client_error_instead_of_server_error(self):
        with isolated_env() as tmp:
            os.environ["LODIA_ENV"] = "production"
            os.environ["LODIA_DATA_DIR"] = tmp
            os.environ["LODIA_ADMIN_TOKEN"] = "admin-token"
            os.environ["LODIA_CONTRIBUTOR_TOKEN"] = "contributor-token"
            os.environ["LODIA_REVIEWER_TOKEN"] = "reviewer-token"
            os.environ["LODIA_AUTH_TOKENS"] = "reviewer-b-token:reviewer:reviewer_b:platform"
            import main

            importlib.reload(main)
            client = TestClient(main.app)
            submitted = client.post(
                "/api/submissions/text",
                headers={"Authorization": "Bearer contributor-token"},
                json={
                    "text": "请复盘一个可复用任务，输出目标、过程、结果、验收标准和可复用规则。",
                    "allowed_uses": ["commercial_dataset", "training"],
                },
            )
            case_id = submitted.json()["case"]["case_id"]
            claimed = client.post(
                "/api/review/claim",
                headers={"Authorization": "Bearer reviewer-token"},
                json={"case_id": case_id},
            )
            rejected = client.post(
                f"/api/review/{case_id}/reject",
                headers={"Authorization": "Bearer reviewer-b-token"},
                json={"reason": "conflict"},
            )

            self.assertEqual(claimed.status_code, 200)
            self.assertEqual(rejected.status_code, 400)
            self.assertEqual(rejected.json()["detail"], "review_case_claimed_by_other")

    def test_review_claim_blocks_conflicting_reviewer_actions(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = LodiaStore(data_dir=tmp)
            case = store.submit_text(
                owner_id="claim_owner",
                text="请分析一个 Agent 执行任务，输出目标、过程、工具结果、验收标准和可复用规则。",
                allowed_uses=["commercial_dataset", "training"],
            )["case"]

            claimed = store.claim_review_case("reviewer_a", case_id=case["case_id"])
            self.assertEqual(claimed["review_claimed_by"], "reviewer_a")
            with self.assertRaisesRegex(ValueError, "review_case_claimed_by_other"):
                store.claim_review_case("reviewer_b", case_id=case["case_id"])
            with self.assertRaisesRegex(ValueError, "review_case_claimed_by_other"):
                store.approve_case(case["case_id"], "reviewer_b")

            approved = store.approve_case(case["case_id"], "reviewer_a")
            self.assertEqual(approved["status"], "commercial_ready")
            self.assertIsNone(approved["review_claimed_by"])

    def test_rejected_commercial_case_cannot_be_exported(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = LodiaStore(data_dir=tmp)
            case = store.submit_text(
                owner_id="reject_owner",
                text="请分析一个销售任务案例，输出目标、过程、结果、验收标准和可复用规则。",
                allowed_uses=["commercial_dataset", "training"],
            )["case"]
            store.approve_case(case["case_id"], "reviewer_a")
            rejected = store.reject_case(case["case_id"], "reviewer_a", "quality failed")

            self.assertEqual(rejected["status"], "rejected")
            self.assertFalse(rejected["quality_gate"]["commercial_ready"])
            with self.assertRaisesRegex(ValueError, "case_rejected"):
                store.expert_verify_case(case["case_id"], "reviewer_a")
            with self.assertRaisesRegex(ValueError, "no_eligible_cases"):
                store.create_dataset("Rejected Dataset", "commercial_dataset", "DRL3", 100000, 20000)

    def test_dataset_artifact_exports_only_redacted_records_without_owner_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = LodiaStore(data_dir=tmp)
            case = store.submit_text(
                owner_id="private_contributor",
                text="请复盘一个客服任务，手机号 13800138000，输出目标、过程、结果、验收标准和可复用规则。",
                allowed_uses=["commercial_dataset", "training"],
            )["case"]
            store.approve_case(case["case_id"], "reviewer_a")
            dataset = store.create_dataset("Delivery Dataset", "commercial_dataset", "DRL3", 100000, 20000)

            artifact = store.read_dataset_artifact(dataset["id"], "data", actor_id="admin")
            record = json.loads(artifact["content"].splitlines()[0])

            self.assertEqual(artifact["media_type"], "application/x-ndjson")
            self.assertNotIn("owner_id", record)
            self.assertIn("contributor_ref", record)
            self.assertNotIn("13800138000", json.dumps(record, ensure_ascii=False))
            self.assertTrue(any(item["event_type"] == "dataset.artifact_read" for item in store.list_audit_logs()))


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
