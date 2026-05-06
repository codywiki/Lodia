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
    "LODIA_REQUIRE_PAYOUT_PROFILE_FOR_SETTLEMENT",
    "POSTGRES_DSN",
    "DATABASE_URL",
]


class EnterpriseDeliveryAndPayoutProfileTests(unittest.TestCase):
    def test_delivery_grant_controls_dataset_artifact_reads(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = LodiaStore(data_dir=tmp)
            case = store.submit_text(
                owner_id="delivery_owner",
                text="请复盘一个客服任务，手机号 13800138000，输出目标、过程、结果、验收标准和可复用规则。",
                allowed_uses=["commercial_dataset", "training"],
            )["case"]
            store.approve_case(case["case_id"], "reviewer")
            dataset = store.create_dataset("Enterprise Dataset", "commercial_dataset", "DRL3", 100_000, 20_000)
            customer = store.create_enterprise_customer("Acme AI", "buyer@example.com", actor_id="admin")
            self.assertNotIn("contact_email_hash", customer)
            grant = store.create_dataset_delivery_grant(
                dataset_id=dataset["id"],
                customer_id=customer["id"],
                purpose="commercial_dataset",
                terms_version="enterprise-test",
                max_reads=1,
                actor_id="admin",
            )

            self.assertIn("delivery_token", grant)
            self.assertNotIn("token_hash", grant)
            with self.assertRaisesRegex(ValueError, "invalid_delivery_token"):
                store.read_delivery_grant_artifact(grant["id"], "wrong-token", "data")

            with self.assertRaisesRegex(ValueError, "unsupported_dataset_artifact"):
                store.read_delivery_grant_artifact(grant["id"], grant["delivery_token"], "unknown")
            self.assertEqual(store.get_dataset_delivery_grant(grant["id"])["read_count"], 0)

            artifact = store.read_delivery_grant_artifact(grant["id"], grant["delivery_token"], "data")
            record = json.loads(artifact["content"].splitlines()[0])

            self.assertNotIn("owner_id", record)
            self.assertNotIn("13800138000", json.dumps(record, ensure_ascii=False))
            self.assertEqual(store.get_dataset_delivery_grant(grant["id"])["read_count"], 1)
            with self.assertRaisesRegex(ValueError, "delivery_grant_read_limit_exceeded"):
                store.read_delivery_grant_artifact(grant["id"], grant["delivery_token"], "data")

            revoked = store.revoke_dataset_delivery_grant(grant["id"], actor_id="admin")
            self.assertEqual(revoked["status"], "revoked")

    def test_enterprise_customer_rejects_invalid_contact_email(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = LodiaStore(data_dir=tmp)
            with self.assertRaisesRegex(ValueError, "invalid_contact_email"):
                store.create_enterprise_customer("Bad Buyer", "not-an-email", actor_id="admin")

    def test_enterprise_delivery_api_uses_token_header_without_admin_auth(self):
        with isolated_env() as tmp:
            os.environ["LODIA_ENV"] = "production"
            os.environ["LODIA_DATA_DIR"] = tmp
            os.environ["LODIA_ADMIN_TOKEN"] = "admin-token"
            os.environ["LODIA_REVIEWER_TOKEN"] = "reviewer-token"
            os.environ["LODIA_CONTRIBUTOR_TOKEN"] = "contributor-token"
            import main

            importlib.reload(main)
            client = TestClient(main.app)
            submitted = client.post(
                "/api/submissions/text",
                headers={"Authorization": "Bearer contributor-token"},
                json={
                    "owner_id": "spoofed",
                    "text": "请复盘一个可复用客服任务，输出目标、过程、结果和验收标准。",
                    "allowed_uses": ["commercial_dataset", "training"],
                },
            )
            self.assertEqual(submitted.status_code, 200)
            approved = client.post(
                f"/api/review/{submitted.json()['case']['case_id']}/approve",
                headers={"Authorization": "Bearer reviewer-token"},
                json={},
            )
            self.assertEqual(approved.status_code, 200)
            dataset = client.post(
                "/api/datasets",
                headers={"Authorization": "Bearer admin-token"},
                json={"name": "API Delivery Dataset", "purpose": "commercial_dataset", "min_drl": "DRL3"},
            )
            self.assertEqual(dataset.status_code, 200)
            customer = client.post(
                "/api/admin/enterprise/customers",
                headers={"Authorization": "Bearer admin-token"},
                json={"name": "Buyer", "contact_email": "buyer@example.com"},
            )
            self.assertEqual(customer.status_code, 200)
            self.assertNotIn("contact_email_hash", customer.json())
            grant = client.post(
                f"/api/admin/datasets/{dataset.json()['id']}/delivery-grants",
                headers={"Authorization": "Bearer admin-token"},
                json={"customer_id": customer.json()["id"], "max_reads": 2},
            )
            self.assertEqual(grant.status_code, 200)
            self.assertNotIn("token_hash", grant.json())

            blocked = client.get(f"/api/delivery-grants/{grant.json()['id']}/artifacts/data")
            not_found = client.get(
                "/api/delivery-grants/missing/artifacts/data",
                headers={"X-Lodia-Delivery-Token": grant.json()["delivery_token"]},
            )
            wrong_token = client.get(
                f"/api/delivery-grants/{grant.json()['id']}/artifacts/data",
                headers={"X-Lodia-Delivery-Token": "wrong-token"},
            )
            allowed = client.get(
                f"/api/delivery-grants/{grant.json()['id']}/artifacts/data",
                headers={"X-Lodia-Delivery-Token": grant.json()["delivery_token"]},
            )
            read_limit = client.get(
                f"/api/delivery-grants/{grant.json()['id']}/artifacts/data",
                headers={"X-Lodia-Delivery-Token": grant.json()["delivery_token"]},
            )
            over_limit = client.get(
                f"/api/delivery-grants/{grant.json()['id']}/artifacts/data",
                headers={"X-Lodia-Delivery-Token": grant.json()["delivery_token"]},
            )

            self.assertEqual(blocked.status_code, 401)
            self.assertEqual(not_found.status_code, 401)
            self.assertEqual(wrong_token.status_code, 401)
            self.assertEqual(allowed.status_code, 200)
            self.assertEqual(read_limit.status_code, 200)
            self.assertEqual(over_limit.status_code, 429)
            self.assertIn("contributor_ref", allowed.text)

    def test_payout_profile_gate_can_block_settlement_until_verified(self):
        with isolated_env() as tmp:
            os.environ["LODIA_DATA_DIR"] = tmp
            os.environ["LODIA_REQUIRE_PAYOUT_PROFILE_FOR_SETTLEMENT"] = "true"
            store = LodiaStore(data_dir=tmp)
            case = store.submit_text(
                owner_id="payout_ready_owner",
                text="请分析一个客服任务案例，输出处理步骤、结果、验收标准和可复用规则。",
                allowed_uses=["commercial_dataset", "training"],
            )["case"]
            store.approve_case(case["case_id"], reviewer_id="reviewer")
            store.create_dataset("Payout Gated Dataset", "commercial_dataset", "DRL3", 100_000, 20_000)
            batch = store.create_payout_batch(actor_id="admin")

            with self.assertRaisesRegex(ValueError, "payout_profile_not_ready"):
                store.settle_payout_batch(batch["id"], actor_id="admin")

            pending = store.upsert_payout_profile(
                contributor_id="payout_ready_owner",
                country_region="CN",
                account_type="bank",
                account_reference="6222000000000000",
                actor_id="payout_ready_owner",
            )
            self.assertEqual(pending["status"], "pending_review")
            self.assertNotIn("account_ref_hash", pending)

            ready = store.upsert_payout_profile(
                contributor_id="payout_ready_owner",
                country_region="cn",
                account_type="BANK",
                account_reference="6222000000000000",
                kyc_status="Verified",
                tax_status="Verified",
                risk_status="Clear",
                actor_id="admin",
            )
            self.assertEqual(ready["status"], "active")
            self.assertEqual(ready["country_region"], "CN")
            self.assertEqual(ready["account_type"], "bank")
            settled = store.settle_payout_batch(batch["id"], actor_id="admin")
            self.assertEqual(settled["status"], "settled")

            with self.assertRaisesRegex(ValueError, "invalid_kyc_status"):
                store.upsert_payout_profile(
                    contributor_id="payout_ready_owner",
                    country_region="CN",
                    account_type="bank",
                    account_reference="6222000000000000",
                    kyc_status="typo",
                    actor_id="admin",
                )


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
