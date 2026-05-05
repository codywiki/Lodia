import importlib
import os
import tempfile
import unittest

from fastapi.testclient import TestClient


class ApiFlowTests(unittest.TestCase):
    def test_http_submission_review_dataset_and_ledger_flow(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["LODIA_DATA_DIR"] = tmp
            import main

            importlib.reload(main)
            client = TestClient(main.app)
            submitted = client.post(
                "/api/submissions/text",
                json={
                    "owner_id": "contributor_api",
                    "text": (
                        "请分析这个客服投诉案例，客户手机号 13800138000，"
                        "邮箱 user@example.com。要求输出处理步骤、验收结果和可复用规则。"
                    ),
                    "allowed_uses": ["private_library", "candidate_pool", "commercial_dataset", "training"],
                },
            )
            self.assertEqual(submitted.status_code, 200)
            case = submitted.json()["case"]
            self.assertEqual(case["quality_gate"]["drl"], "DRL2")

            approved = client.post(f"/api/review/{case['case_id']}/approve", json={"reviewer_id": "reviewer_api"})
            self.assertEqual(approved.status_code, 200)
            self.assertEqual(approved.json()["quality_gate"]["drl"], "DRL3")

            dataset = client.post(
                "/api/datasets",
                json={
                    "name": "API Flow Dataset",
                    "purpose": "commercial_dataset",
                    "min_drl": "DRL3",
                    "gross_revenue_cents": 100000,
                    "direct_cost_cents": 20000,
                },
            )
            self.assertEqual(dataset.status_code, 200)
            self.assertEqual(dataset.json()["payout"]["contributor_pool_cents"], 64000)

            usage = client.get("/api/ledger/usage-events")
            payouts = client.get("/api/ledger/payout-events")
            self.assertEqual(len(usage.json()["items"]), 1)
            self.assertEqual(len(payouts.json()["items"]), 1)


if __name__ == "__main__":
    unittest.main()
