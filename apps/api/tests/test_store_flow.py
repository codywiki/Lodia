import tempfile
import unittest
from pathlib import Path

from lodia.store import LodiaStore


class StoreFlowTests(unittest.TestCase):
    def test_submission_review_dataset_and_payout_flow(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = LodiaStore(data_dir=tmp)
            secret = "sk-" + "abcdefghijklmnopqrstuvwxyz"
            submitted = store.submit_text(
                owner_id="contributor_1",
                text=(
                    "请分析这个客服投诉案例，客户手机号 13800138000，"
                    f"API key {secret}。要求输出处理步骤、"
                    "验收结果和可复用规则。"
                ),
                allowed_uses=["private_library", "candidate_pool", "commercial_dataset", "training"],
            )
            case = submitted["case"]

            self.assertEqual(case["quality_gate"]["drl"], "DRL2")
            self.assertNotIn("13800138000", case["redacted_text"])
            self.assertNotIn(secret, case["redacted_text"])

            raw_files = list(Path(tmp, "raw").glob("*.txt"))
            self.assertEqual(len(raw_files), 1)
            self.assertIn("13800138000", raw_files[0].read_text(encoding="utf-8"))

            approved = store.approve_case(case["case_id"], reviewer_id="reviewer_1")
            self.assertEqual(approved["quality_gate"]["drl"], "DRL3")
            self.assertTrue(approved["quality_gate"]["commercial_ready"])

            dataset = store.create_dataset(
                name="Demo Agent Dataset",
                purpose="commercial_dataset",
                min_drl="DRL3",
                gross_revenue_cents=100_000,
                direct_cost_cents=20_000,
            )

            self.assertEqual(dataset["status"], "ready")
            self.assertEqual(dataset["case_ids"], [case["case_id"]])
            self.assertTrue(Path(dataset["manifest_path"]).exists())
            self.assertTrue(Path(dataset["quality_report_path"]).exists())
            self.assertTrue(Path(dataset["data_path"]).exists())
            self.assertEqual(len(store.list_usage_events()), 1)
            payouts = store.list_payout_events()
            self.assertEqual(len(payouts), 1)
            self.assertEqual(payouts[0]["amount_cents"], 64_000)


if __name__ == "__main__":
    unittest.main()
