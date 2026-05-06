from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from lodia.store import LodiaStore


def main() -> int:
    parser = argparse.ArgumentParser(description="Lodia operational management CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    migrations = subparsers.add_parser("migrations", help="Inspect schema migrations")
    migrations_sub = migrations.add_subparsers(dest="action", required=True)
    migrations_status = migrations_sub.add_parser("status", help="Print schema migration status")
    migrations_status.add_argument("--check", action="store_true", help="Exit non-zero when migrations are missing")

    readiness = subparsers.add_parser("readiness", help="Run service readiness checks")
    readiness.add_argument("--check", action="store_true", help="Exit non-zero when readiness is failing")

    launch = subparsers.add_parser("launch-readiness", help="Run production launch readiness checks")
    launch.add_argument("--check", action="store_true", help="Exit non-zero when launch blockers exist")

    alerts = subparsers.add_parser("alerts", help="Print operational alerts")
    alerts.add_argument("--check", action="store_true", help="Exit non-zero when critical alerts exist")

    maintenance = subparsers.add_parser("maintenance", help="Run or enqueue maintenance")
    maintenance_sub = maintenance.add_subparsers(dest="action", required=True)
    maintenance_run = maintenance_sub.add_parser("run", help="Run maintenance synchronously")
    maintenance_run.add_argument("--limit", type=int, default=100)
    maintenance_enqueue = maintenance_sub.add_parser("enqueue", help="Enqueue maintenance for workers")
    maintenance_enqueue.add_argument("--limit", type=int, default=100)
    maintenance_enqueue.add_argument("--queue", default="maintenance")

    proof = subparsers.add_parser("commercial-proof", help="Print dataset commercial proof")
    proof.add_argument("dataset_id")

    args = parser.parse_args()
    store = LodiaStore()

    if args.command == "migrations":
        result = store.schema_migration_status()
        _print_json(result)
        return 0 if result["ok"] or not args.check else 2
    if args.command == "readiness":
        result = store.readiness_check()
        _print_json(result)
        return 0 if result["ok"] or not args.check else 2
    if args.command == "launch-readiness":
        result = store.production_launch_readiness()
        _print_json(result)
        return 0 if result["ready"] or not args.check else 2
    if args.command == "alerts":
        result = store.operational_alerts()
        _print_json(result)
        return 0 if result["critical_count"] == 0 or not args.check else 2
    if args.command == "maintenance" and args.action == "run":
        _print_json(store.run_maintenance(limit=args.limit, actor_id="manage_cli"))
        return 0
    if args.command == "maintenance" and args.action == "enqueue":
        _print_json(store.enqueue_job("run_maintenance", {"limit": args.limit}, queue_name=args.queue, actor_id="manage_cli"))
        return 0
    if args.command == "commercial-proof":
        _print_json(store.dataset_commercial_proof(args.dataset_id, actor_id="manage_cli"))
        return 0

    parser.error("unsupported command")
    return 2


def _print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    sys.exit(main())
