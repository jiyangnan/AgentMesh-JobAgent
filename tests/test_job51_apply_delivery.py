from __future__ import annotations

from jobagent.platforms.job51.apply import (
    Job51ApplySender,
    _job51_apply_inspect_script,
    _job51_history_inspect_script,
)
from jobagent.platforms.job51.audit import Job51AuditLog
from jobagent.domain.models import SendAttempt


class RecoveryDriver:
    def __init__(self):
        self.opened: list[str] = []
        self.snapshots = [
            {"ok": True, "loginRequired": False, "delivered": False, "pageReady": True, "cardFound": True},
            {"ok": False, "error": "job51_card_not_found", "jobId": "J172480001"},
            {"ok": True, "loginRequired": False, "delivered": False, "pageReady": True, "cardFound": True},
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


def test_job51_delivery_detection_is_scoped_to_target_card_or_visible_notice():
    script = _job51_apply_inspect_script("J172480001")

    assert "targetText" in script
    assert "applyControls" in script
    assert "applyText" in script
    assert "applyAvailable" in script
    assert "/已投递|已申请/.test(targetText + ' ' + applyText)" in script
    assert "/已投递/.test(bodyText)" not in script
    assert "visibleNotices" in script


def test_job51_unavailable_job_is_audited_as_skip_and_does_not_stop_batch(monkeypatch, tmp_path):
    audit_log = Job51AuditLog(path=tmp_path / "audit.json")
    sender = Job51ApplySender(driver=object(), audit_log=audit_log)
    results = iter(
        [
            SendAttempt(
                job_url="https://example/one",
                message="",
                delivered=False,
                error="job_unavailable",
            ),
            SendAttempt(job_url="https://example/two", message="", delivered=True),
        ]
    )
    monkeypatch.setattr(sender, "_send_one", lambda *_args, **_kwargs: next(results))

    attempts = sender.send_batch(
        [
            {"job_id": "one", "url": "https://example/one"},
            {"job_id": "two", "url": "https://example/two"},
        ],
        limit=2,
        stop_on_failure=True,
    )

    assert [attempt.error for attempt in attempts] == ["job_unavailable", ""]
    assert [event["status"] for event in audit_log.list_recent(10)] == ["skipped", "delivered"]


class HistoryVerificationDriver:
    def __init__(self):
        self.opened: list[str] = []
        self.inspect_calls = 0

    def open_url_in_new_tab(self, url: str, wait_seconds: int = 0):
        self.opened.append(url)
        return {"ok": True, "url": url, "wait_seconds": wait_seconds}

    def _exec_js(self, script: str):
        if "document.querySelectorAll('.apox .e, .exmsg .e')" in script:
            return {
                "ok": True,
                "historyReady": True,
                "loginRequired": False,
                "delivered": True,
                "matchedJobId": "172706410",
            }
        if "const candidates" in script:
            return {"ok": True, "clicked": "投递", "jobId": "172706410"}
        self.inspect_calls += 1
        if self.inspect_calls == 1:
            return {
                "ok": True,
                "pageReady": True,
                "cardFound": True,
                "applyAvailable": True,
                "delivered": False,
                "loginRequired": False,
            }
        return {
            "ok": True,
            "pageReady": True,
            "cardFound": True,
            "applyAvailable": False,
            "delivered": False,
            "loginRequired": False,
        }


def test_job51_ambiguous_button_removal_is_verified_in_application_history(monkeypatch, tmp_path):
    monkeypatch.setattr("jobagent.platforms.job51.apply.time.sleep", lambda _: None)
    driver = HistoryVerificationDriver()

    attempts = Job51ApplySender(
        driver=driver,
        audit_log=Job51AuditLog(path=tmp_path / "audit.json"),
    ).send_batch(
        [{
            "job_id": "172706410",
            "name": "高级数据平台工程师（151563）",
            "company": "任仕达企业管理（上海）有限公司",
            "url": "https://we.51job.com/pc/search?keyword=data&jobArea=010000#jobId=172706410",
        }]
    )

    assert attempts[0].delivered is True
    assert driver.opened[-1] == "https://i.51job.com/userset/my_apply.php"
    assert any(step["step"] == "verify_51job_application_history" for step in attempts[0].steps)


def test_job51_history_verification_is_scoped_to_job_id_or_exact_row_identity():
    script = _job51_history_inspect_script(
        "172706410",
        "高级数据平台工程师（151563）",
        "任仕达企业管理（上海）有限公司",
    )

    assert "a.zhn" in script
    assert "a.gs" in script
    assert "idMatches || textMatches" in script
    assert "document.body" in script
    assert "delivered: Boolean(row)" in script
