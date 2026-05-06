import importlib
import os
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from lodia.object_storage import LocalObjectStorage
from lodia.store import LodiaStore


ENV_KEYS = [
    "LODIA_DATA_DIR",
    "LODIA_ENV",
    "LODIA_ADMIN_TOKEN",
    "LODIA_REVIEWER_TOKEN",
    "LODIA_CONTRIBUTOR_TOKEN",
    "POSTGRES_DSN",
    "DATABASE_URL",
]


class AssetAndAuthorizationTests(unittest.TestCase):
    def test_text_asset_creates_case_and_authorization_blocks_future_export_after_withdrawal(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = LodiaStore(data_dir=tmp)
            uploaded = store.submit_asset(
                owner_id="asset_owner",
                filename="agent-trace.txt",
                media_type="text/plain",
                content=(
                    "请复盘这个 Agent 任务，用户手机号 13800138000，"
                    "要求输出目标、执行过程、工具结果、验收标准和复用规则。"
                ).encode("utf-8"),
                allowed_uses=["commercial_dataset", "training"],
            )
            asset = uploaded["asset"]

            self.assertEqual(asset["status"], "evidence_ready")
            self.assertIsNotNone(asset["submission_id"])
            self.assertIsNotNone(asset["authorization_snapshot_id"])
            self.assertTrue(Path(asset["raw_path"]).exists())
            self.assertTrue(Path(asset["extracted_text_path"]).exists())

            cases = store.list_cases(owner_id="asset_owner")
            self.assertEqual(len(cases), 1)
            self.assertNotIn("13800138000", cases[0]["redacted_text"])
            approved = store.approve_case(cases[0]["case_id"], reviewer_id="reviewer_asset")
            dataset = store.create_dataset(
                name="Asset Dataset",
                purpose="commercial_dataset",
                min_drl="DRL3",
                gross_revenue_cents=50_000,
                direct_cost_cents=10_000,
            )
            contract = store.get_data_contract(dataset["id"])["contract"]
            self.assertEqual(contract["authorization_snapshot_ids"], [approved["authorization_snapshot_id"]])

            withdrawn = store.withdraw_authorization_snapshot(
                approved["authorization_snapshot_id"],
                reason="user withdrawal",
                actor_id="asset_owner",
            )
            self.assertEqual(withdrawn["status"], "withdrawn")
            self.assertEqual(store.get_case(approved["case_id"])["status"], "withdrawn")
            self.assertEqual(store.get_asset(asset["id"])["status"], "withdrawn")
            with self.assertRaisesRegex(ValueError, "no_eligible_cases"):
                store.create_dataset(
                    name="Blocked Dataset",
                    purpose="commercial_dataset",
                    min_drl="DRL3",
                    gross_revenue_cents=50_000,
                    direct_cost_cents=10_000,
                )

    def test_executable_asset_is_rejected_before_case_creation(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = LodiaStore(data_dir=tmp)
            uploaded = store.submit_asset(
                owner_id="asset_owner",
                filename="bad.exe",
                media_type="application/octet-stream",
                content=b"MZ" + b"\x00" * 64,
                allowed_uses=["candidate_pool"],
            )

            asset = uploaded["asset"]
            self.assertEqual(asset["status"], "rejected")
            self.assertIsNone(asset["submission_id"])
            self.assertTrue(asset["risk"]["blocked"])
            self.assertEqual(store.list_cases(owner_id="asset_owner"), [])

    def test_invalid_asset_authorization_does_not_write_raw_object(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = LodiaStore(data_dir=tmp)
            authorization = store.create_authorization_snapshot(
                owner_id="asset_owner",
                allowed_uses=["private_library"],
            )

            with self.assertRaisesRegex(ValueError, "authorization_scope_exceeded"):
                store.submit_asset(
                    owner_id="asset_owner",
                    filename="trace.txt",
                    media_type="text/plain",
                    content=b"usable trace",
                    allowed_uses=["commercial_dataset"],
                    authorization_snapshot_id=authorization["id"],
                )

            self.assertFalse(Path(tmp, "raw", "assets").exists())

    def test_invalid_text_authorization_does_not_write_raw_object(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = LodiaStore(data_dir=tmp)
            authorization = store.create_authorization_snapshot(
                owner_id="text_owner",
                allowed_uses=["private_library"],
            )

            with self.assertRaisesRegex(ValueError, "authorization_scope_exceeded"):
                store.submit_text(
                    owner_id="text_owner",
                    text="usable task case",
                    allowed_uses=["commercial_dataset"],
                    authorization_snapshot_id=authorization["id"],
                )

            self.assertFalse(Path(tmp, "raw").exists())

    def test_local_object_storage_rejects_paths_outside_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            storage = LocalObjectStorage(Path(tmp, "objects"))
            outside = Path(tmp, "outside.txt")
            outside.write_text("outside", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "local_object_outside_root"):
                storage.read_bytes(str(outside))
            with self.assertRaisesRegex(ValueError, "local_object_outside_root"):
                storage.delete(str(outside))

            self.assertTrue(outside.exists())

    def test_asset_upload_api_scopes_owner_to_authenticated_contributor(self):
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
                    "email": "asset@lodia.local",
                    "password": "very-strong-password",
                    "display_name": "Asset Contributor",
                    "roles": ["contributor"],
                },
            )
            self.assertEqual(created.status_code, 200)
            login = client.post("/api/auth/login", json={"email": "asset@lodia.local", "password": "very-strong-password"})
            token_payload = login.json()

            response = client.post(
                "/api/assets",
                headers={"Authorization": f"Bearer {token_payload['token']}"},
                files={"file": ("trace.txt", b"agent task with acceptance criteria", "text/plain")},
                data={"owner_id": "spoofed", "allowed_uses": '["candidate_pool"]'},
            )

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["asset"]["owner_id"], token_payload["user"]["id"])

    def test_reviewer_cannot_withdraw_another_contributors_authorization(self):
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
                    "text": "请复盘一个可复用的 Agent 任务，输出目标、过程、结果和验收标准。",
                    "allowed_uses": ["commercial_dataset"],
                },
            )
            self.assertEqual(submitted.status_code, 200)
            authorization_id = submitted.json()["case"]["authorization_snapshot_id"]

            blocked = client.post(
                f"/api/authorizations/{authorization_id}/withdraw",
                headers={"Authorization": "Bearer reviewer-token"},
                json={"reason": "reviewer should not mutate owner consent"},
            )
            allowed = client.post(
                f"/api/authorizations/{authorization_id}/withdraw",
                headers={"Authorization": "Bearer contributor-token"},
                json={"reason": "owner withdrawal"},
            )

            self.assertEqual(blocked.status_code, 403)
            self.assertEqual(allowed.status_code, 200)
            self.assertEqual(allowed.json()["status"], "withdrawn")


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
