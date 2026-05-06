import tempfile
import unittest

from lodia.store import LodiaStore
from worker import process_one


class CommercialControlTests(unittest.TestCase):
    def test_drl5_gold_eval_requires_expert_and_two_distinct_gold_reviews(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = LodiaStore(data_dir=tmp)
            submitted = store.submit_text(
                owner_id="gold_owner",
                text="请复盘这个软件工程 Agent 任务，输出目标、执行步骤、工具结果、验收标准和可复用规则。",
                allowed_uses=["commercial_dataset", "training", "gold_eval"],
            )
            case = submitted["case"]
            approved = store.approve_case(case["case_id"], reviewer_id="human_reviewer")
            self.assertEqual(approved["quality_gate"]["drl"], "DRL3")

            with self.assertRaisesRegex(ValueError, "gold_eval_requires_drl5"):
                store.create_dataset("Gold Too Early", "gold_eval", "DRL4", 100_000, 20_000)

            expert = store.expert_verify_case(
                case["case_id"],
                reviewer_id="expert_reviewer",
                rubric={"answer_verifiability": "passed"},
                evidence={"trace": "checked"},
            )
            self.assertEqual(expert["quality_gate"]["drl"], "DRL4")

            first_gold = store.gold_review_case(case["case_id"], reviewer_id="gold_reviewer_1")
            self.assertEqual(first_gold["quality_gate"]["drl"], "DRL4")
            self.assertIn("gold_second_review", first_gold["quality_gate"]["required_actions"])
            with self.assertRaisesRegex(ValueError, "gold_reviewer_duplicate"):
                store.gold_review_case(case["case_id"], reviewer_id="gold_reviewer_1")

            second_gold = store.gold_review_case(case["case_id"], reviewer_id="gold_reviewer_2")
            self.assertEqual(second_gold["quality_gate"]["drl"], "DRL5")
            dataset = store.create_dataset("Gold Dataset", "gold_eval", "DRL5", 100_000, 20_000)
            self.assertEqual(dataset["case_ids"], [case["case_id"]])

    def test_payout_batch_settlement_is_idempotency_guarded(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = LodiaStore(data_dir=tmp)
            submitted = store.submit_text(
                owner_id="payout_owner",
                text="请分析这个客服任务案例，输出处理步骤、结果、验收标准和可复用规则。",
                allowed_uses=["commercial_dataset", "training"],
            )
            case = submitted["case"]
            store.approve_case(case["case_id"], reviewer_id="reviewer")
            store.create_dataset("Training Dataset", "commercial_dataset", "DRL3", 100_000, 20_000)
            pending = store.list_payout_events(status="pending")
            self.assertEqual(len(pending), 1)

            batch = store.create_payout_batch(actor_id="admin")
            self.assertEqual(batch["status"], "ready")
            self.assertEqual(store.list_payout_events(status="batched")[0]["settlement_batch_id"], batch["id"])
            settled = store.settle_payout_batch(batch["id"], actor_id="admin", external_reference="bank-file-001")
            self.assertEqual(settled["status"], "settled")
            self.assertEqual(store.list_payout_events(status="settled")[0]["settled_at"], settled["settled_at"])
            with self.assertRaisesRegex(ValueError, "payout_batch_not_ready"):
                store.settle_payout_batch(batch["id"], actor_id="admin")

    def test_pdf_extraction_worker_creates_case_and_records_model_invocation(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = LodiaStore(data_dir=tmp)
            uploaded = store.submit_asset(
                owner_id="pdf_owner",
                filename="trace.pdf",
                media_type="application/pdf",
                content=(
                    b"%PDF-1.4 Lodia printable task text: output goals steps tool results acceptance criteria reusable rules. %%EOF"
                ),
                allowed_uses=["commercial_dataset", "training"],
            )
            asset = uploaded["asset"]
            self.assertEqual(asset["status"], "extraction_pending")
            job = store.request_asset_extraction(asset["id"], actor_id="pdf_owner")
            self.assertEqual(job["job_type"], "extract_asset")

            self.assertTrue(process_one(store=store, queue_name="extraction", worker_id="extractor"))
            extracted = store.get_asset(asset["id"])
            self.assertEqual(extracted["status"], "evidence_ready")
            self.assertEqual(len(store.list_cases(owner_id="pdf_owner")), 1)
            invocations = store.list_model_invocations(entity_id=asset["id"])
            self.assertEqual(invocations[0]["status"], "succeeded")

    def test_observability_snapshot_contains_commercial_control_metrics(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = LodiaStore(data_dir=tmp)
            submitted = store.submit_text(
                owner_id="obs_owner",
                text="请分析这个任务案例，输出目标、过程、结果、验收标准和复用规则。",
                allowed_uses=["commercial_dataset", "training"],
            )
            store.approve_case(submitted["case"]["case_id"], reviewer_id="reviewer")
            snapshot = store.observability_snapshot()
            self.assertTrue(snapshot["ok"])
            self.assertGreaterEqual(snapshot["case_drl"].get("DRL3", 0), 1)
            self.assertGreaterEqual(snapshot["model_invocations"].get("succeeded", 0), 1)
            self.assertIn("readiness", snapshot)


if __name__ == "__main__":
    unittest.main()
