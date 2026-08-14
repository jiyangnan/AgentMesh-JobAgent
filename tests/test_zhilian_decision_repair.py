from __future__ import annotations

import pytest

from jobagent.application import decision_repair
from jobagent.domain.models import Job


def _manifest(*, title: str = "查看更多信息") -> dict:
    return {
        "manifest_type": "decision_manifest",
        "manifest_id": "dm_original",
        "discover_id": "dis_repairable",
        "candidate_digest": "sha256:" + "1" * 64,
        "platform": "zhilian",
        "deduplicated_count": 1,
        "expires_at": "2026-08-14T00:00:00Z",
        "selected": [
            {
                "id": "job-1",
                "title": title,
                "company": None,
                "salary": None,
                "area": "深圳",
                "url": "https://www.zhaopin.com/jobdetail/job-1.htm",
            }
        ],
        "review": [],
        "rejected": [],
    }


def test_zhilian_review_repair_hydrates_same_job_and_requests_zero_charge_resign(
    monkeypatch, tmp_path
):
    original = _manifest()
    envelope = {"manifest": original, "platform": "zhilian"}
    observed: dict = {}

    monkeypatch.setattr(
        decision_repair,
        "verify_stored_decision",
        lambda manifest, **_kwargs: manifest,
    )
    monkeypatch.setattr(
        decision_repair,
        "load_pending_interaction",
        lambda: None,
    )

    class FakeCollector:
        def __init__(self, driver=None):
            observed["driver"] = driver

        def _hydrate_from_details(self, jobs, detail_limit, wait_seconds):
            assert detail_limit == 1
            assert wait_seconds == 8
            assert jobs[0].name == "查看更多信息"
            return {
                "jobs": [
                    Job(
                        name="数据运营经理",
                        company="深圳示例科技有限公司",
                        salary="25-35K",
                        area="深圳",
                        experience="3-5年",
                        degree="本科",
                        skills="",
                        boss="",
                        city="深圳",
                        url=jobs[0].url,
                        platform="zhilian",
                        raw_data=jobs[0].raw_data,
                    )
                ],
                "snapshots": [{"ok": True}],
            }

    monkeypatch.setattr(decision_repair, "ZhilianReadOnlyCollector", FakeCollector)

    repaired_candidates = [
        {
            "id": "job-1",
            "title": "数据运营经理",
            "company": "深圳示例科技有限公司",
            "salary": "25-35K",
            "area": "深圳",
            "url": "https://www.zhaopin.com/jobdetail/job-1.htm",
        }
    ]
    repaired_manifest = {
        **original,
        "manifest_id": "dm_repaired",
        "candidate_digest": "sha256:" + "2" * 64,
        "selected": [{**repaired_candidates[0], "classification": "selected"}],
        "repair": {
            "additional_credits": 0,
            "same_discover_id": True,
            "request_preserved": True,
        },
    }

    def fake_cloud_repair(**kwargs):
        observed["cloud"] = kwargs
        return {
            "manifest": repaired_manifest,
            "candidates": repaired_candidates,
            "repair": repaired_manifest["repair"],
        }

    monkeypatch.setattr(decision_repair.cloud_client, "discovery_repair", fake_cloud_repair)
    monkeypatch.setattr(
        decision_repair,
        "verify_decision_manifest",
        lambda manifest, **kwargs: observed.update(verification=kwargs) or manifest,
    )
    saved = tmp_path / "dis_repairable.json"
    monkeypatch.setattr(decision_repair, "save_manifest", lambda _manifest: saved)

    result = decision_repair.repair_zhilian_decision_if_needed(envelope)

    assert result["repaired"] is True
    assert result["repair"]["additional_credits"] == 0
    assert result["decision_file"] == str(saved)
    assert observed["cloud"]["discover_id"] == "dis_repairable"
    assert observed["cloud"]["expected_manifest_id"] == "dm_original"
    assert observed["cloud"]["patches"] == [
        {
            "id": "job-1",
            "url": "https://www.zhaopin.com/jobdetail/job-1.htm",
            "title": "数据运营经理",
            "company": "深圳示例科技有限公司",
            "salary": "25-35K",
        }
    ]
    assert observed["verification"]["jobs"] == repaired_candidates


def test_zhilian_review_repair_stops_when_detail_is_still_unreviewable(monkeypatch):
    original = _manifest()
    monkeypatch.setattr(
        decision_repair,
        "verify_stored_decision",
        lambda manifest, **_kwargs: manifest,
    )
    monkeypatch.setattr(decision_repair, "load_pending_interaction", lambda: None)

    class IncompleteCollector:
        def __init__(self, driver=None):
            del driver

        def _hydrate_from_details(self, jobs, detail_limit, wait_seconds):
            del detail_limit, wait_seconds
            return {"jobs": jobs, "snapshots": [{"ok": True}]}

    monkeypatch.setattr(
        decision_repair,
        "ZhilianReadOnlyCollector",
        IncompleteCollector,
    )
    monkeypatch.setattr(
        decision_repair.cloud_client,
        "discovery_repair",
        lambda **_kwargs: pytest.fail("cloud repair must not run"),
    )

    with pytest.raises(decision_repair.DecisionRepairError) as error:
        decision_repair.repair_zhilian_decision_if_needed(
            {"manifest": original, "platform": "zhilian"}
        )

    assert error.value.payload["error"] == "decision_repair_incomplete"
    assert error.value.payload["no_charge"] is True
    assert error.value.payload["request_preserved"] is True
