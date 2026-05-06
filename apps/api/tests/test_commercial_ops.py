import tempfile
import unittest

from lodia.store import LodiaStore


class CommercialOpsTests(unittest.TestCase):
    def test_enterprise_order_contract_delivery_and_revenue_recognition_flow(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = LodiaStore(data_dir=tmp)
            dataset = _ready_dataset(store, gross_revenue_cents=0, direct_cost_cents=0)
            customer = store.create_enterprise_customer("Acme AI", "buyer@example.com", tenant_id="enterprise_a", actor_id="admin")
            contract = store.create_enterprise_contract(
                customer_id=customer["id"],
                terms_version="pilot-terms",
                terms={"allowed_use": "training", "resale": False},
                actor_id="admin",
            )
            order = store.create_enterprise_order(
                customer_id=customer["id"],
                dataset_id=dataset["id"],
                contract_id=contract["id"],
                gross_revenue_cents=100_000,
                direct_cost_cents=20_000,
                max_reads=1,
                actor_id="admin",
            )
            grant = store.create_dataset_delivery_grant(
                dataset_id=dataset["id"],
                customer_id=customer["id"],
                order_id=order["id"],
                purpose="commercial_dataset",
                terms_version="pilot-terms",
                max_reads=1,
                actor_id="admin",
            )
            delivered = store.read_delivery_grant_artifact(grant["id"], grant["delivery_token"], "manifest")
            recognized = store.recognize_enterprise_order_usage(order["id"], actor_id="admin")

            self.assertEqual(delivered["artifact"], "manifest")
            self.assertEqual(store.get_dataset_delivery_grant(grant["id"])["read_count"], 1)
            self.assertEqual(recognized["status"], "recognized")
            self.assertEqual(recognized["delivery_grant_id"], grant["id"])
            self.assertEqual(recognized["payout"]["contributor_pool_cents"], 64_000)
            self.assertEqual(store.list_payout_events(status="pending")[0]["amount_cents"], 64_000)

            with self.assertRaisesRegex(ValueError, "delivery_grant_read_limit_exceeded"):
                store.read_delivery_grant_artifact(grant["id"], grant["delivery_token"], "manifest")

    def test_tenant_order_and_delivery_read_quotas_are_enforced(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = LodiaStore(data_dir=tmp)
            dataset = _ready_dataset(store, gross_revenue_cents=0, direct_cost_cents=0)
            customer = store.create_enterprise_customer("Buyer", "buyer@example.com", tenant_id="quota_tenant", actor_id="admin")
            contract = store.create_enterprise_contract(customer["id"], actor_id="admin")
            store.upsert_tenant_quota(
                "quota_tenant",
                monthly_order_limit=1,
                monthly_delivery_read_limit=1,
                actor_id="admin",
            )
            order = store.create_enterprise_order(customer["id"], dataset["id"], contract["id"], 100_000, 20_000, max_reads=2, actor_id="admin")
            with self.assertRaisesRegex(ValueError, "tenant_order_quota_exceeded"):
                store.create_enterprise_order(customer["id"], dataset["id"], contract["id"], 100_000, 20_000, actor_id="admin")

            grant = store.create_dataset_delivery_grant(
                dataset_id=dataset["id"],
                customer_id=customer["id"],
                order_id=order["id"],
                purpose="commercial_dataset",
                terms_version="enterprise-test",
                max_reads=2,
                actor_id="admin",
            )
            store.read_delivery_grant_artifact(grant["id"], grant["delivery_token"], "manifest")
            with self.assertRaisesRegex(ValueError, "tenant_delivery_read_quota_exceeded"):
                store.read_delivery_grant_artifact(grant["id"], grant["delivery_token"], "quality_report")

    def test_dispute_holds_and_releases_order_payouts(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = LodiaStore(data_dir=tmp)
            dataset = _ready_dataset(store, gross_revenue_cents=0, direct_cost_cents=0)
            customer = store.create_enterprise_customer("Dispute Buyer", "buyer@example.com", actor_id="admin")
            contract = store.create_enterprise_contract(customer["id"], actor_id="admin")
            order = store.create_enterprise_order(customer["id"], dataset["id"], contract["id"], 100_000, 20_000, actor_id="admin")
            store.recognize_enterprise_order_usage(order["id"], actor_id="admin")

            dispute = store.create_dispute("enterprise_order", order["id"], "buyer quality challenge", actor_id="admin")
            held = store.list_payout_events(status="held")
            with self.assertRaisesRegex(ValueError, "no_payouts_eligible"):
                store.create_payout_batch(actor_id="admin")

            resolved = store.resolve_dispute(dispute["id"], "release", "quality accepted", actor_id="admin")
            batch = store.create_payout_batch(actor_id="admin")

            self.assertEqual(dispute["held_payout_count"], 1)
            self.assertEqual(len(held), 1)
            self.assertEqual(resolved["status"], "resolved_release")
            self.assertEqual(batch["payout_count"], 1)


def _ready_dataset(store: LodiaStore, gross_revenue_cents: int, direct_cost_cents: int):
    case = store.submit_text(
        owner_id="commercial_owner",
        text="请复盘一个企业 AI 任务案例，输出目标、过程、结果、验收标准和可复用规则。",
        allowed_uses=["commercial_dataset", "training"],
    )["case"]
    store.approve_case(case["case_id"], "reviewer")
    return store.create_dataset(
        "Commercial Ops Dataset",
        "commercial_dataset",
        "DRL3",
        gross_revenue_cents,
        direct_cost_cents,
    )


if __name__ == "__main__":
    unittest.main()
