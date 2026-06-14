"""Pipeline — orchestrates crawl → filter → greet workflow."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jobagent.domain.models import Job, GreetResult, CandidateProfile, RankedJob
from jobagent.domain.filter import FilterEngine
from jobagent.domain.ranking import RankingEngine
from jobagent.domain.greeter import GreeterEngine
from jobagent.infra.config import Config
from jobagent.infra.exceptions import LoginRequiredError
from jobagent.platforms.boss import BossDataDriver


class Pipeline:
    """Orchestrates the full job agent pipeline: crawl → filter → greet."""

    def __init__(self, config: Config):
        self.config = config
        self.data_driver = BossDataDriver()
        self.filter_engine = FilterEngine()
        self.ranking_engine = RankingEngine(CandidateProfile.from_config(config))
        self.greeter_engine = GreeterEngine(config.greeter)

        self.data_dir = Path("data")
        self.raw_dir = self.data_dir / "raw"
        self.filtered_dir = self.data_dir / "filtered"
        self.ranked_dir = self.data_dir / "ranked"
        self.results_dir = self.data_dir / "results"

        for d in (self.raw_dir, self.filtered_dir, self.ranked_dir, self.results_dir):
            d.mkdir(parents=True, exist_ok=True)

    def _run_id(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    def _save_json(self, path: Path, data: list[dict]) -> None:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def crawl(self) -> tuple[list[Job], Path]:
        """Phase 1: Fetch raw jobs from Boss直聘."""
        run_id = self._run_id()
        raw_jobs = self.data_driver.fetch_all(
            queries=self.config.crawler.queries,
            cities=self.config.crawler.cities,
            max_pages=self.config.crawler.pages_per_query,
        )
        raw_file = self.raw_dir / f"raw_{run_id}.json"
        self._save_json(raw_file, [j.to_dict() for j in raw_jobs])
        print(f"   Saved raw → {raw_file}")
        return raw_jobs, raw_file

    def filter(self, jobs: list[Job]) -> tuple[list[Job], Path]:
        """Phase 2: Apply filters to raw jobs."""
        run_id = self._run_id()
        filtered = self.filter_engine.apply(jobs, self.config.filter)
        filtered_file = self.filtered_dir / f"filtered_{run_id}.json"
        self._save_json(filtered_file, [j.to_dict() for j in filtered])
        print(f"   Saved filtered → {filtered_file}")
        return filtered, filtered_file

    def rank(self, jobs: list[Job], top_n: int = 20) -> tuple[list[RankedJob], Path]:
        """Phase 3: Score and rank filtered jobs."""
        run_id = self._run_id()
        ranked = self.ranking_engine.rank(jobs, top_n=top_n)
        ranked_file = self.ranked_dir / f"ranked_{run_id}.json"
        self._save_json(ranked_file, [rj.to_dict() for rj in ranked])
        print(f"   Saved ranked → {ranked_file}")
        return ranked, ranked_file

    def greet(self, jobs: list[RankedJob]) -> tuple[list, Path]:
        """Phase 4: Send greetings to ranked jobs."""
        run_id = self._run_id()
        results = self.greeter_engine.send_batch(jobs, limit=10)
        results_file = self.results_dir / f"greet_{run_id}.json"
        self._save_json(results_file, [r.to_dict() for r in results])
        print(f"   Saved results → {results_file}")
        return results, results_file

    def run(self) -> dict[str, Any]:
        """Execute the complete pipeline and return summary."""
        run_id = self._run_id()
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+08:00")

        # Profile summary
        profile = CandidateProfile.from_config(self.config)
        print(f"\n🚀 Job Agent Pipeline starting at {timestamp}")
        print(f"   Platform: {self.config.platform}")
        if profile.target_roles:
            print(f"   Target roles: {', '.join(profile.target_roles)}")
        if profile.skills:
            print(f"   Skills: {', '.join(profile.skills[:5])}")
        if profile.preferred_cities:
            print(f"   Cities: {', '.join(profile.preferred_cities)}")
        print(f"   Queries: {len(self.config.crawler.queries)}")
        print(f"   Cities: {[c['name'] for c in self.config.crawler.cities]}")
        print()

        try:
            # Phase 1: Crawl
            print("📡 Phase 1: Crawling...")
            raw_jobs, raw_file = self.crawl()
            print(f"   Total raw jobs: {len(raw_jobs)}")

            # Phase 2: Filter
            print("\n🔍 Phase 2: Filtering...")
            filtered_jobs, filtered_file = self.filter(raw_jobs)
            print(f"   Total filtered jobs: {len(filtered_jobs)}")

            # Phase 3: Rank
            print("\n🏆 Phase 3: Ranking...")
            ranked_jobs, ranked_file = self.rank(filtered_jobs, top_n=20)
            print(f"   Top ranked jobs: {len(ranked_jobs)}")

            # Phase 4: Greet (optional, controlled by config)
            summary: dict[str, Any] = {
                "run_id": run_id,
                "timestamp": timestamp,
                "platform": self.config.platform,
                "crawled": len(raw_jobs),
                "filtered": len(filtered_jobs),
                "ranked": len(ranked_jobs),
                "raw_file": str(raw_file),
                "filtered_file": str(filtered_file),
                "ranked_file": str(ranked_file),
            }

            if self.config.greeter.enabled:
                print("\n💬 Phase 4: Greeting...")
                greet_results, greet_file = self.greet(ranked_jobs)
                summary["greeted"] = len(greet_results)
                summary["delivered"] = sum(1 for r in greet_results if r.delivered)
                summary["greet_file"] = str(greet_file)
                print(f"   Delivered: {summary['delivered']}/{summary['greeted']}")
            else:
                summary["greeted"] = 0
                summary["delivered"] = 0

            print("\n" + "=" * 60)
            print(f"✅ Pipeline complete!")
            print(f"   Crawled:   {summary['crawled']}")
            print(f"   Filtered:  {summary['filtered']}")
            print(f"   Ranked:    {summary['ranked']}")
            if self.config.greeter.enabled:
                print(f"   Greeted:   {summary['greeted']}")
                print(f"   Delivered: {summary['delivered']}")
            print("=" * 60)

            return summary

        except LoginRequiredError as e:
            print(e)
            return {
                "run_id": run_id,
                "timestamp": timestamp,
                "status": "LOGIN_REQUIRED",
                "error": str(e),
            }
