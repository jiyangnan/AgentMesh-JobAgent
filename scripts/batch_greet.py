#!/usr/bin/env python3
"""
Batch Greet — iterate through ranked/filtered jobs and send greeting messages.
Usage:
    python scripts/batch_greet.py --input data/filtered/filtered_20260501_165010.json --config config/config.yaml
    python scripts/batch_greet.py --input data/filtered/filtered_20260501_165010.json --config config/config.yaml --dry-run
"""
import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from jobagent.application.probe_send import run_probe_send
from jobagent.infra.config import GreeterConfig


def load_jobs(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def already_greeted(url: str, results_dir: Path) -> tuple[bool, str]:
    """Check if a URL has already been greeted. Returns (greeted, status)."""
    if not results_dir.exists():
        return False, "no_results_dir"
    for f in sorted(results_dir.glob("results_*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if not isinstance(data, list):
                continue
            for item in data:
                if item.get("url", "").rstrip("/") == url.rstrip("/"):
                    return True, item.get("status", "unknown")
        except Exception:
            continue
    return False, "not_greeted"


def main():
    parser = argparse.ArgumentParser(description="Batch greet jobs on Boss直聘")
    parser.add_argument("--input", "-i", required=True, help="Input JSON file with job list")
    parser.add_argument("--config", "-c", default="config/config.yaml", help="Config YAML file")
    parser.add_argument("--dry-run", action="store_true", help="Print what would be done without sending")
    parser.add_argument("--limit", type=int, default=0, help="Limit number of jobs (0=all)")
    parser.add_argument("--skip-greeted", action="store_true", default=True, help="Skip already-greeted jobs (default: True)")
    parser.add_argument("--delay", type=float, default=8.0, help="Delay between jobs in seconds (default: 8)")
    parser.add_argument("--output", "-o", help="Output results JSON file")
    args = parser.parse_args()

    # Load config for greeting template
    import yaml
    with open(args.config, encoding='utf-8') as f:
        raw = yaml.safe_load(f)
    greeter_cfg = GreeterConfig(**raw.get("greeter", {}))
    template = greeter_cfg.template.strip()
    print(f"📋 Greeting template:\n{template}\n")

    # Load jobs
    jobs = load_jobs(args.input)
    if args.limit > 0:
        jobs = jobs[: args.limit]
    print(f"📥 Loaded {len(jobs)} jobs from {args.input}")

    # Results directory
    results_dir = Path("data/results")
    results_dir.mkdir(parents=True, exist_ok=True)

    # Determine output file
    if args.output:
        output_path = Path(args.output)
    else:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        output_path = results_dir / f"greet_results_{ts}.json"

    results = []
    already_ok = 0
    skipped_already = 0

    for i, job in enumerate(jobs, 1):
        url = job.get("url", "")
        name = job.get("name") or job.get("jobName", "unknown")
        company = job.get("company", "unknown")
        print(f"\n[{i}/{len(jobs)}] {name} | {company}")
        print(f"   URL: {url}")

        # Check if already greeted
        if args.skip_greeted:
            was_greeted, prev_status = already_greeted(url, results_dir)
            if was_greeted:
                print(f"   ⏭ Already greeted (status: {prev_status}), skipping")
                skipped_already += 1
                continue

        if args.dry_run:
            print(f"   🟡 [DRY RUN] Would send greeting to: {url}")
            continue

        # Send greeting
        print(f"   🟢 Sending greeting...")
        try:
            attempt = run_probe_send(job_url=url, message=template)
            status = "ok" if attempt.delivered else "failed"
            result_entry = {
                "name": name,
                "company": company,
                "url": url,
                "status": status,
                "greeting": template,
                "error_msg": attempt.error or None,
                "greeted_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+08:00"),
            }
            results.append(result_entry)
            if attempt.delivered:
                already_ok += 1
                print(f"   ✅ Delivered! (step count: {len(attempt.steps)})")
            else:
                print(f"   ❌ Failed: {attempt.error}")
        except Exception as e:
            print(f"   ❌ Exception: {e}")
            results.append({
                "name": name,
                "company": company,
                "url": url,
                "status": "error",
                "greeting": template,
                "error_msg": str(e),
                "greeted_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+08:00"),
            })

        # Delay between jobs
        if i < len(jobs):
            print(f"   ⏳ Waiting {args.delay}s...")
            time.sleep(args.delay)

    # Save results
    if not args.dry_run:
        output_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n💾 Saved {len(results)} results to {output_path}")

    # Summary
    print(f"\n{'='*60}")
    print(f"📊 Summary")
    print(f"   Total jobs processed: {len(results)}")
    print(f"   ✅ Delivered: {already_ok}")
    print(f"   ❌ Failed: {len(results) - already_ok}")
    print(f"   ⏭ Skipped (already greeted): {skipped_already}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
