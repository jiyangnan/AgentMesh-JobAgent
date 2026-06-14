"""Greeter Engine — batch greeting orchestration."""

from __future__ import annotations

import random
import time
from typing import Any

from jobagent.domain.models import Job, RankedJob, SendAttempt
from jobagent.drivers.boss.base import BossActionDriver
from jobagent.drivers.boss import create_driver
from jobagent.infra.config import GreeterConfig
from jobagent.infra.state import audit_log_path, save_json
from jobagent.platforms.boss.send_flow import execute_boss_greeting_flow
from jobagent.platforms import normalize_platform_key


class GreeterEngine:
    """Orchestrates batch job greeting: preview, send, and audit."""

    def __init__(
        self,
        config: GreeterConfig,
        driver: BossActionDriver | None = None,
    ):
        self.config = config
        self.driver = driver or create_driver()

    # ── Preview ───────────────────────────────────────────────

    def preview(self, jobs: list[RankedJob], limit: int = 10) -> list[dict[str, Any]]:
        """Return preview of greeting messages without sending."""
        results = []
        for rj in jobs[:limit]:
            message = self.config.get_template(rj.job)
            results.append({
                "job_name": rj.job.name,
                "company": rj.job.company,
                "boss": rj.job.boss,
                "salary": rj.job.salary,
                "url": rj.job.url,
                "message": message,
                "score": rj.score,
                "match_level": rj.match_level,
            })
        return results

    # ── Single send (internal) ────────────────────────────────

    def _send_one(self, job: Job, message: str) -> SendAttempt:
        """Execute the greeting flow for a single job.

        The current implementation routes to the Boss platform send flow.
        Multi-platform support should move selection above this method instead
        of mixing selector/state-machine details here.
        """
        return execute_boss_greeting_flow(
            self.driver,
            job.url,
            message,
            verify=self.config.verify,
            retry_on_unverified=True,
        )

    # ── Batch send ────────────────────────────────────────────

    def send_batch(
        self,
        jobs: list[RankedJob],
        limit: int = 10,
        delay_range: tuple[float, float] = (10.0, 20.0),
        message_overrides: dict[str, str] | None = None,
    ) -> list[SendAttempt]:
        """Send greetings to multiple jobs with rate limiting.

        Args:
            jobs: Ranked job list (typically from RankingEngine).
            limit: Max jobs to greet.
            delay_range: Random delay between jobs (min, max) in seconds.

        Returns:
            List of SendAttempt results (one per job processed).
        """
        results: list[SendAttempt] = []
        targets = jobs[:limit]
        total = len(targets)

        print(f"\n💬 Batch greeting starting: {total} jobs")
        print(f"   Dry-run: {self.config.dry_run}")
        print(f"   Verify:  {self.config.verify}")
        print(f"   Delay:   {delay_range[0]}-{delay_range[1]}s between jobs\n")

        overrides = message_overrides or {}
        for i, rj in enumerate(targets, 1):
            job = rj.job
            override = overrides.get(job.url)
            message = override or self.config.get_template(job)
            source = "preview" if override else "template"
            print(f"[{i}/{total}] {job.name} @ {job.company}  (msg source: {source})")
            print(f"        URL: {job.url}")
            print(f"        Msg: {message[:60]}{'...' if len(message) > 60 else ''}")

            if self.config.dry_run:
                attempt = SendAttempt(
                    job_url=job.url,
                    message=message,
                    delivered=False,
                    error="dry_run",
                )
                print(f"        ⏭️  Dry-run skipped")
            else:
                attempt = self._send_one(job, message)
                status = "✅ Delivered" if attempt.delivered else f"❌ Failed: {attempt.error}"
                print(f"        {status}")
                if attempt.error == "risk_control":
                    print(
                        "        ⚠️  上游返回了验证挑战：今天发送过快。"
                        "建议停止当日发送，明天恢复（或加大延迟）。"
                    )

            results.append(attempt)
            self._save_audit(attempt, job)

            # Rate limiting: skip delay after the last job
            if i < total:
                delay = random.uniform(*delay_range)
                print(f"        ⏳ Waiting {delay:.1f}s...")
                time.sleep(delay)

        # Print summary
        success = sum(1 for a in results if a.delivered)
        print(f"\n{'=' * 50}")
        print(f"Batch complete: {success}/{total} delivered")
        print(f"{'=' * 50}\n")

        return results

    # ── Audit persistence ─────────────────────────────────────

    def _save_audit(self, attempt: SendAttempt, job: Job) -> None:
        """Append send attempt to audit log with job context."""
        path = audit_log_path()
        records: list[dict[str, Any]] = []
        if path.exists():
            import json
            try:
                records = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, Exception):
                records = []
        record = attempt.to_dict()
        record["job_name"] = job.name
        record["company"] = job.company
        record["boss"] = job.boss
        record["platform"] = normalize_platform_key(job.platform)
        records.append(record)
        save_json(path, records)
