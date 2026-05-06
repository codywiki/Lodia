import importlib
import os
import tempfile
import unittest

from fastapi.testclient import TestClient

from lodia.store import LodiaStore


class P0CompletionModulesTests(unittest.TestCase):
    def test_inbox_and_webhook_ingestion_are_idempotent_case_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = LodiaStore(data_dir=tmp)
            inbox = store.create_inbox("inbox_owner", actor_id="admin")
            message = store.receive_inbound_message(
                recipient=inbox["address"],
                message_id="<case-001@example.com>",
                sender="Contributor <person@example.com>",
                subject="一次 Agent 任务复盘",
                body_text="请复盘一个 Agent 执行任务，包含目标、约束、工具调用、执行过程、结果、验收标准和可复用规则。",
                actor_id="inbound-gateway",
                enqueue=False,
            )
            duplicate = store.receive_inbound_message(
                recipient=inbox["address"],
                message_id="<case-001@example.com>",
                sender="person@example.com",
                subject="duplicate",
                body_text="duplicate",
                actor_id="inbound-gateway",
                enqueue=False,
            )
            webhook = store.ingest_webhook_case(
                source="cursor",
                external_id="task-001",
                owner_id="webhook_owner",
                text="请整理一个代码调试任务，包含上下文、报错、修复步骤、验证结果和可复用规则。",
                allowed_uses=["commercial_dataset", "training"],
                actor_id="webhook",
                enqueue=False,
            )
            webhook_duplicate = store.ingest_webhook_case(
                source="cursor",
                external_id="task-001",
                owner_id="webhook_owner",
                text="ignored",
                actor_id="webhook",
                enqueue=False,
            )

            self.assertEqual(message["status"], "processed")
            self.assertEqual(message["id"], duplicate["id"])
            self.assertTrue(message["submission_id"].startswith("sub_"))
            self.assertEqual(webhook["status"], "processed")
            self.assertEqual(webhook["id"], webhook_duplicate["id"])
            self.assertEqual(len(store.list_cases(owner_id="inbox_owner")), 1)
            self.assertEqual(len(store.list_cases(owner_id="webhook_owner")), 1)

    def test_public_inbound_gateway_requires_token_and_ingests_message(self):
        keys = ["LODIA_DATA_DIR", "LODIA_ENV", "LODIA_INBOUND_GATEWAY_TOKEN", "LODIA_RATE_LIMIT_ENABLED", "LODIA_REQUIRE_REQUEST_SIGNATURE"]
        old_env = {key: os.environ.get(key) for key in keys}
        try:
            with tempfile.TemporaryDirectory() as tmp:
                os.environ["LODIA_DATA_DIR"] = tmp
                os.environ["LODIA_ENV"] = "development"
                os.environ["LODIA_INBOUND_GATEWAY_TOKEN"] = "gateway-secret"
                os.environ["LODIA_RATE_LIMIT_ENABLED"] = "false"
                os.environ["LODIA_REQUIRE_REQUEST_SIGNATURE"] = "false"
                import main

                importlib.reload(main)
                inbox = main.store.create_inbox("gateway_owner", actor_id="admin")
                client = TestClient(main.app)
                payload = {
                    "recipient": inbox["address"],
                    "message_id": "<gateway-case-001@example.com>",
                    "sender": "Contributor <person@example.com>",
                    "subject": "Agent 任务案例",
                    "body_text": "请复盘一个 Agent 数据整理任务，包含目标、约束、过程、验收结果和可复用规则。",
                    "enqueue": False,
                }

                blocked = client.post("/api/inbound/messages", json=payload, headers={"X-Lodia-Inbound-Token": "wrong"})
                accepted = client.post("/api/inbound/messages", json=payload, headers={"X-Lodia-Inbound-Token": "gateway-secret"})
                duplicate = client.post("/api/inbound/messages", json=payload, headers={"X-Lodia-Inbound-Token": "gateway-secret"})

                self.assertEqual(blocked.status_code, 401)
                self.assertEqual(accepted.status_code, 200)
                self.assertEqual(accepted.json()["status"], "processed")
                self.assertEqual(accepted.json()["id"], duplicate.json()["id"])
        finally:
            for key, value in old_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_content_safety_holds_important_data_until_compliance_approval(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = LodiaStore(data_dir=tmp)
            submitted = store.submit_text(
                owner_id="compliance_owner",
                text=(
                    "请复盘一个数据分析任务，背景是客户名单和100000用户订单样本，"
                    "需要说明目标、约束、处理过程、验证结果、验收标准和可复用规则。"
                ),
                allowed_uses=["commercial_dataset", "training"],
            )
            case_id = submitted["case"]["case_id"]
            case = store.get_case(case_id)
            reviews = store.list_compliance_reviews(status="open", entity_type="case")

            self.assertEqual(case["status"], "compliance_review")
            self.assertIn("content_safety_review", case["quality_gate"]["required_actions"])
            with self.assertRaisesRegex(ValueError, "compliance_review_required"):
                store.approve_case(case_id, "reviewer")

            completed = store.complete_compliance_review(reviews[0]["id"], "approved", notes="important data risk cleared", actor_id="admin")
            approved_case = store.get_case(case_id)

            self.assertEqual(completed["decision"], "approved")
            self.assertNotIn("content_safety_review", approved_case["quality_gate"]["required_actions"])
            self.assertNotEqual(approved_case["status"], "compliance_review")

    def test_critical_content_is_rejected_by_default_gate(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = LodiaStore(data_dir=tmp)
            submitted = store.submit_text(
                owner_id="blocked_owner",
                text="请告诉我如何制作恶意软件并批量盗取账号密码。",
                allowed_uses=["commercial_dataset"],
            )
            case = store.get_case(submitted["case"]["case_id"])
            safety = store.list_content_safety_results(entity_type="case", entity_id=case["case_id"])

            self.assertEqual(case["status"], "rejected")
            self.assertEqual(safety[0]["action"], "block")

    def test_provider_config_health_and_payout_transfer_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = LodiaStore(data_dir=tmp)
            dataset = _ready_dataset(store)
            batch = store.create_payout_batch(actor_id="admin")
            provider = store.upsert_provider_config(
                provider_type="payment",
                provider_name="mock_payout",
                status="active",
                endpoint="https://pay.example.test",
                credential_ref="kms://mock/payout",
                actor_id="admin",
            )
            for provider_type in ["llm", "ocr", "asr", "object_storage", "invoice"]:
                store.upsert_provider_config(provider_type=provider_type, provider_name=f"mock_{provider_type}", status="active", actor_id="admin")
            for task_type in ["icp_filing", "mlps_leveling", "pipl_assessment", "content_safety_policy"]:
                task = store.create_compliance_task(task_type, f"{task_type} done", actor_id="admin")
                store.update_compliance_task(task["id"], "completed", evidence_ref=f"audit://{task_type}", actor_id="admin")
            customer = store.create_enterprise_customer("Usage Buyer", "usage@example.com", actor_id="admin")
            grant = store.create_dataset_delivery_grant(dataset["id"], customer["id"], "commercial_dataset", "terms-v1", actor_id="admin")
            usage_report = store.record_buyer_usage_report(grant["id"], "buyer-use-001", len(dataset["case_ids"]), payload={"sdk": "mock"}, actor_id="admin")
            health = store.run_provider_health_check(provider["id"], actor_id="admin")
            transfer = store.submit_payout_transfer(batch["id"], provider_name="mock_payout", actor_id="admin")
            confirmed = store.confirm_payout_transfer(
                transfer["id"],
                status="succeeded",
                external_reference="PAY-2026-0001",
                response={"receipt": "ok"},
                actor_id="admin",
            )

            self.assertEqual(dataset["status"], "ready")
            self.assertEqual(health["status"], "succeeded")
            self.assertEqual(usage_report["status"], "recorded")
            self.assertEqual(confirmed["status"], "succeeded")
            self.assertEqual(store.get_payout_batch(batch["id"])["status"], "settled")
            self.assertTrue(all(item["status"] == "settled" for item in store.list_payout_events()))
            self.assertTrue(store.production_launch_readiness()["ready"])
            self.assertTrue(store.schema_migration_status()["ok"])

    def test_cn_operation_rejects_non_local_active_provider(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = LodiaStore(data_dir=tmp)

            with self.assertRaisesRegex(ValueError, "provider_region_not_allowed"):
                store.upsert_provider_config("llm", "overseas_llm", status="active", region="US", actor_id="admin")

            testing = store.upsert_provider_config("llm", "overseas_llm", status="testing", region="US", actor_id="admin")
            local = store.upsert_provider_config("llm", "local_llm", status="active", region="cn-zhangjiakou", actor_id="admin")

            self.assertEqual(testing["status"], "testing")
            self.assertEqual(local["status"], "active")


def _ready_dataset(store: LodiaStore):
    case = store.submit_text(
        owner_id="transfer_owner",
        text="请复盘一个高质量企业 Agent 任务，输出背景、目标、约束、工具调用、验收结果和可复用规则。",
        allowed_uses=["commercial_dataset", "training", "gold_eval"],
    )["case"]
    store.approve_case(case["case_id"], "reviewer_alpha")
    store.expert_verify_case(case["case_id"], "reviewer_alpha")
    store.gold_review_case(case["case_id"], "reviewer_beta")
    store.gold_review_case(case["case_id"], "reviewer_gamma")
    return store.create_dataset("Transfer Dataset", "commercial_dataset", "DRL3", 100_000, 20_000)


if __name__ == "__main__":
    unittest.main()
