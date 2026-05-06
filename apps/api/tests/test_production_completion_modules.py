import tempfile
import unittest

from lodia.store import LodiaStore


class ProductionCompletionModulesTests(unittest.TestCase):
    def test_review_sampling_source_trust_and_reviewer_metrics(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = LodiaStore(data_dir=tmp)
            ready_case = store.submit_text(
                owner_id="trusted_owner",
                text="请复盘一个企业 AI 客服任务，输出目标、过程、结果、验收标准和可复用规则。",
                allowed_uses=["commercial_dataset", "training"],
            )["case"]
            rejected_case = store.submit_text(
                owner_id="risky_owner",
                text="短问题。",
                allowed_uses=["commercial_dataset"],
            )["case"]
            store.approve_case(ready_case["case_id"], "reviewer_alpha")
            store.reject_case(rejected_case["case_id"], "reviewer_alpha", "too thin")

            trusted = store.refresh_source_trust_profile("trusted_owner", actor_id="admin")
            risky = store.refresh_source_trust_profile("risky_owner", actor_id="admin")
            scheduled = store.schedule_review_samples(limit=5, min_drl="DRL3", actor_id="admin")
            completed = store.complete_review_sample(
                scheduled["items"][0]["id"],
                reviewer_id="reviewer_beta",
                decision="passed",
                notes="sample accepted",
                score=0.95,
            )
            performance = store.reviewer_performance("reviewer_beta")

            self.assertGreaterEqual(trusted["score"], risky["score"])
            self.assertEqual(scheduled["created_count"], 1)
            self.assertTrue(scheduled["items"][0]["blind"])
            self.assertNotIn("owner_id", scheduled["items"][0]["case_snapshot"])
            self.assertEqual(completed["decision"], "passed")
            self.assertEqual(performance["samples"]["completed"], 1)

    def test_eval_holdout_reconciliation_invoice_and_sso_flow(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = LodiaStore(data_dir=tmp)
            dataset = _ready_dataset(store)
            store.register_holdout_case(dataset["case_ids"][0], purpose="gold_eval", actor_id="admin")
            evaluation = store.run_dataset_evaluation(dataset["id"], actor_id="admin")

            customer = store.create_enterprise_customer("Acme AI", "buyer@example.com", tenant_id="enterprise_a", actor_id="admin")
            contract = store.create_enterprise_contract(customer["id"], actor_id="admin")
            order = store.create_enterprise_order(customer["id"], dataset["id"], contract["id"], 100_000, 20_000, actor_id="admin")
            store.recognize_enterprise_order_usage(order["id"], actor_id="admin")
            reconciliation = store.run_reconciliation("enterprise_order", order["id"], actor_id="admin")
            invoice = store.create_invoice(order["id"], "INV-2026-0001", 100_000, tax_cents=6_000, actor_id="admin")
            paid = store.mark_invoice_paid(invoice["id"], actor_id="admin")
            sso = store.upsert_sso_provider_config(
                tenant_id="enterprise_a",
                provider_type="oidc",
                issuer="https://sso.example.com",
                domain="example.com",
                metadata={"client_id_ref": "hash-only"},
                actor_id="admin",
            )

            self.assertEqual(evaluation["status"], "failed")
            self.assertIn("holdout_overlap", {item["code"] for item in evaluation["findings"]})
            self.assertEqual(reconciliation["status"], "passed")
            self.assertEqual(paid["status"], "paid")
            self.assertNotIn("invoice_no_hash", paid)
            self.assertEqual(sso["tenant_id"], "enterprise_a")
            self.assertEqual(sso["metadata"]["client_id_ref"], "hash-only")

    def test_dsr_delete_blocks_future_dataset_use_and_writes_proof(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = LodiaStore(data_dir=tmp)
            case = store.submit_text(
                owner_id="delete_owner",
                text="请复盘一个可复用运营任务，包含目标、执行过程、验收结果和下一步规则。",
                allowed_uses=["commercial_dataset", "training"],
            )["case"]
            store.approve_case(case["case_id"], "reviewer")
            request = store.create_dsr_request("delete_owner", "delete", "user requested deletion", actor_id="admin")
            fulfilled = store.fulfill_dsr_request(request["id"], actor_id="admin")
            proof = store.read_dsr_proof(request["id"], actor_id="admin")

            self.assertEqual(fulfilled["status"], "completed")
            self.assertEqual(store.get_case(case["case_id"])["status"], "withdrawn")
            self.assertIn('"request_type": "delete"', proof["content"])
            with self.assertRaisesRegex(ValueError, "no_eligible_cases"):
                store.create_dataset("Deleted Dataset", "commercial_dataset", "DRL3", 100_000, 20_000)

    def test_dsr_delete_recalls_existing_dataset_artifacts_and_delivery(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = LodiaStore(data_dir=tmp)
            dataset = _ready_dataset(store)
            customer = store.create_enterprise_customer("Recall Buyer", "recall@example.com", actor_id="admin")
            grant = store.create_dataset_delivery_grant(
                dataset["id"],
                customer["id"],
                purpose="commercial_dataset",
                terms_version="enterprise-delivery-2026-05",
                actor_id="admin",
            )

            request = store.create_dsr_request("eval_owner", "delete", "withdraw from historical exports", actor_id="admin")
            fulfilled = store.fulfill_dsr_request(request["id"], actor_id="admin")
            proof = store.read_dsr_proof(request["id"], actor_id="admin")

            self.assertEqual(fulfilled["deleted_cases"], 1)
            self.assertEqual(store.get_dataset(dataset["id"])["status"], "privacy_recalled")
            self.assertEqual(store.get_dataset_delivery_grant(grant["id"])["status"], "revoked")
            self.assertIn('"dataset_recalls"', proof["content"])
            with self.assertRaisesRegex(ValueError, "dataset_not_ready"):
                store.read_dataset_artifact(dataset["id"], "data")
            with self.assertRaisesRegex(ValueError, "delivery_grant_not_active"):
                store.read_delivery_grant_artifact(grant["id"], grant["delivery_token"], "data")

    def test_dsr_export_returns_portability_snapshot_without_raw_object_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = LodiaStore(data_dir=tmp)
            case = store.submit_text(
                owner_id="export_owner",
                text="请复盘一个可复用的数据处理任务，包含目标、过程、结果、验收标准。",
                allowed_uses=["commercial_dataset"],
            )["case"]
            store.approve_case(case["case_id"], "reviewer")

            request = store.create_dsr_request("export_owner", "export", "user requested export", actor_id="admin")
            store.fulfill_dsr_request(request["id"], actor_id="admin")
            proof = store.read_dsr_proof(request["id"], actor_id="admin")

            self.assertIn('"export"', proof["content"])
            self.assertIn('"case_id"', proof["content"])
            self.assertNotIn("raw_path", proof["content"])


def _ready_dataset(store: LodiaStore):
    case = store.submit_text(
        owner_id="eval_owner",
        text="请复盘一个高质量企业 Agent 任务，输出目标、约束、工具调用、验收标准和可复用案例。",
        allowed_uses=["commercial_dataset", "training", "gold_eval"],
    )["case"]
    store.approve_case(case["case_id"], "reviewer_alpha")
    store.expert_verify_case(case["case_id"], "reviewer_alpha")
    store.gold_review_case(case["case_id"], "reviewer_beta")
    store.gold_review_case(case["case_id"], "reviewer_gamma")
    return store.create_dataset("Eval Dataset", "commercial_dataset", "DRL3", 0, 0)


if __name__ == "__main__":
    unittest.main()
