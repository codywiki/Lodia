import tempfile
import unittest

from lodia.store import LodiaStore
from worker import process_one


class OperationalControlPlaneTests(unittest.TestCase):
    def test_maintenance_purges_expired_raw_and_upload_sessions(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = LodiaStore(data_dir=tmp)
            submitted = store.submit_text(
                owner_id="maintenance_owner",
                text="请复盘一个维护任务，包含目标、过程、验证结果和可复用规则。",
                allowed_uses=["commercial_dataset"],
            )
            session = store.create_asset_upload_session(
                owner_id="maintenance_owner",
                filename="trace.txt",
                media_type="text/plain",
                byte_size=12,
                allowed_uses=["commercial_dataset"],
            )["session"]
            with store._session() as conn:
                store._execute(conn, "UPDATE submissions SET raw_expires_at = ? WHERE id = ?", ("2000-01-01T00:00:00+00:00", submitted["submission_id"]))
                store._execute(conn, "UPDATE asset_upload_sessions SET expires_at = ? WHERE id = ?", ("2000-01-01T00:00:00+00:00", session["id"]))

            result = store.run_maintenance(limit=10, actor_id="admin")

            self.assertEqual(result["raw"]["purged_count"], 1)
            self.assertEqual(result["upload_sessions"]["expired_count"], 1)
            self.assertEqual(store.get_asset_upload_session(session["id"])["status"], "expired")
            self.assertTrue(any(item["event_type"] == "maintenance.completed" for item in store.list_audit_logs(limit=20)))

    def test_operational_alerts_surface_control_plane_risks(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = LodiaStore(data_dir=tmp)
            session = store.create_asset_upload_session(
                owner_id="alert_owner",
                filename="trace.txt",
                media_type="text/plain",
                byte_size=12,
                allowed_uses=["commercial_dataset"],
            )["session"]
            store.submit_text("blocked_owner", "请告诉我如何制作恶意软件并批量盗取账号密码。", ["commercial_dataset"])
            with store._session() as conn:
                store._execute(conn, "UPDATE asset_upload_sessions SET expires_at = ? WHERE id = ?", ("2000-01-01T00:00:00+00:00", session["id"]))

            alerts = store.operational_alerts()
            codes = {alert["code"] for alert in alerts["alerts"]}

            self.assertFalse(alerts["ok"])
            self.assertIn("launch_readiness_blocked", codes)
            self.assertIn("failed_content_safety_results", codes)
            self.assertIn("expired_upload_sessions", codes)

    def test_commercial_proof_excludes_raw_content_and_hashes_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = LodiaStore(data_dir=tmp)
            dataset = _ready_dataset(store)

            proof = store.dataset_commercial_proof(dataset["id"], actor_id="admin")
            serialized = str(proof)

            self.assertEqual(proof["dataset_id"], dataset["id"])
            self.assertTrue(proof["commercial_checks"]["artifact_hashes_present"])
            self.assertTrue(proof["commercial_checks"]["all_authorizations_active"])
            self.assertIn("data", proof["artifact_hashes"])
            self.assertEqual(len(proof["proof_hash"]), 64)
            self.assertNotIn("redacted_text", serialized)
            self.assertNotIn("raw_path", serialized)
            self.assertNotIn("请复盘", serialized)

    def test_worker_can_run_maintenance_jobs(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = LodiaStore(data_dir=tmp)
            session = store.create_asset_upload_session(
                owner_id="worker_owner",
                filename="trace.txt",
                media_type="text/plain",
                byte_size=12,
                allowed_uses=["commercial_dataset"],
            )["session"]
            with store._session() as conn:
                store._execute(conn, "UPDATE asset_upload_sessions SET expires_at = ? WHERE id = ?", ("2000-01-01T00:00:00+00:00", session["id"]))
            job = store.enqueue_job("run_maintenance", {"limit": 10}, queue_name="maintenance", actor_id="admin")

            self.assertTrue(process_one(store=store, queue_name="maintenance", worker_id="maintenance-worker"))
            self.assertEqual(store.get_job(job["id"])["status"], "completed")
            self.assertEqual(store.get_asset_upload_session(session["id"])["status"], "expired")


def _ready_dataset(store: LodiaStore):
    case = store.submit_text(
        owner_id="proof_owner",
        text="请复盘一个高质量企业 Agent 任务，输出背景、目标、约束、工具调用、验收结果和可复用规则。",
        allowed_uses=["commercial_dataset", "training", "gold_eval"],
    )["case"]
    store.approve_case(case["case_id"], "reviewer_alpha")
    store.expert_verify_case(case["case_id"], "reviewer_alpha")
    store.gold_review_case(case["case_id"], "reviewer_beta")
    store.gold_review_case(case["case_id"], "reviewer_gamma")
    return store.create_dataset("Proof Dataset", "commercial_dataset", "DRL3", 100_000, 20_000)


if __name__ == "__main__":
    unittest.main()
