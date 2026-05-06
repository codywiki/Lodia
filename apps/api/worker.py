from __future__ import annotations

import os
import time
import traceback
from typing import Optional

from lodia.store import LodiaStore


def process_one(store: Optional[LodiaStore] = None, queue_name: str = "ingestion", worker_id: str = "worker") -> bool:
    active_store = store or LodiaStore()
    job = active_store.claim_next_job(queue_name=queue_name, worker_id=worker_id)
    if not job:
        return False

    try:
        if job["job_type"] == "process_submission":
            active_store.process_submission(job["payload"]["submission_id"], actor_id=worker_id)
        elif job["job_type"] == "process_asset":
            active_store.process_asset(job["payload"]["asset_id"], actor_id=worker_id)
        elif job["job_type"] == "extract_asset":
            active_store.process_asset_extraction(job["payload"]["asset_id"], actor_id=worker_id)
        elif job["job_type"] == "run_maintenance":
            active_store.run_maintenance(limit=int(job["payload"].get("limit", 100)), actor_id=worker_id)
        else:
            raise ValueError(f"unsupported_job_type:{job['job_type']}")
        active_store.complete_job(job["id"], worker_id=worker_id)
        return True
    except Exception as exc:  # pragma: no cover - defensive logging path
        active_store.fail_job(job["id"], traceback.format_exc(), worker_id=worker_id)
        raise exc


def main() -> None:
    queue_name = os.environ.get("LODIA_WORKER_QUEUE", "ingestion")
    worker_id = os.environ.get("LODIA_WORKER_ID", f"worker-{os.getpid()}")
    interval = float(os.environ.get("LODIA_WORKER_POLL_INTERVAL_SECONDS", "2"))
    run_once = os.environ.get("LODIA_WORKER_RUN_ONCE", "false").lower() == "true"
    store = LodiaStore()

    while True:
        did_work = process_one(store=store, queue_name=queue_name, worker_id=worker_id)
        if run_once:
            return
        if not did_work:
            time.sleep(interval)


if __name__ == "__main__":
    main()
