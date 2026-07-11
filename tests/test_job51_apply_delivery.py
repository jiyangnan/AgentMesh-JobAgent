from __future__ import annotations

from jobagent.platforms.job51.apply import (
    Job51ApplySender,
    _job51_apply_inspect_script,
)
from jobagent.platforms.job51.audit import Job51AuditLog


class RecoveryDriver:
    def __init__(self):
        self.opened: list[str] = []
        self.snapshots = [
            {"ok": True, "loginRequired": False, "delivered": False},
            {"ok": False, "error": "job51_card_not_found", "jobId": "J172480001"},
            {"ok": True, "loginRequired": False, "delivered": False},
            {"ok": True, "clicked": "投递", "jobId": "J172480001"},
            {"ok": True, "delivered": True, "bodySnippet": "投递成功"},
        ]

    def open_url_in_new_tab(self, url: str, wait_seconds: int = 0):
        self.opened.append(url)
        return {"ok": True, "url": url, "wait_seconds": wait_seconds}

    def _exec_js(self, script: str):
        return self.snapshots.pop(0)


def test_job51_apply_recovers_when_search_ranking_moves_card(monkeypatch, tmp_path):
    monkeypatch.setattr("jobagent.platforms.job51.apply.time.sleep", lambda _: None)
    driver = RecoveryDriver()
    attempts = Job51ApplySender(
        driver=driver,
        audit_log=Job51AuditLog(path=tmp_path / "audit.json"),
    ).send_batch(
        [{
            "name": "AI产品经理(J10389)",
            "company": "Example",
            "url": "https://we.51job.com/pc/search?keyword=AI%E4%BA%A7%E5%93%81%E7%BB%8F%E7%90%86&jobArea=010000#jobId=J172480001",
        }]
    )

    assert attempts[0].delivered is True
    assert len(driver.opened) == 2
    assert "keyword=AI%E4%BA%A7%E5%93%81%E7%BB%8F%E7%90%86%28J10389%29" in driver.opened[1]
    assert "jobArea=010000" in driver.opened[1]
    assert any(step["step"] == "click_51job_apply_recovery" for step in attempts[0].steps)


def test_job51_resume_intervention_only_uses_visible_dialogs():
    script = _job51_apply_inspect_script("J172480001")

    assert "visibleDialogs" in script
    assert "resumeDialogText" in script
    assert ".test(resumeDialogText)" in script
