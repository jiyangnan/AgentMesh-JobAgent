from __future__ import annotations

import pytest

from jobagent.infra.delivery_preview import (
    DeliveryPreviewError,
    build_delivery_preview,
    validate_delivery_preview,
)
from jobagent.infra.delivery_authorization import (
    DeliveryAuthorizationError,
    build_delivery_authorization,
    validate_delivery_authorization,
)


@pytest.mark.parametrize(
    ("platform", "actions"),
    [
        ("boss", ["personalized_greeting"]),
        ("liepin", ["resume", "personalized_greeting"]),
        ("zhilian", ["resume"]),
        ("51job", ["resume"]),
    ],
)
def test_delivery_preview_lists_every_send_candidate_and_binds_continuation(
    platform,
    actions,
):
    jobs = [
        {
            "id": "job-1",
            "title": "数据运营经理",
            "company": "示例科技",
            "area": "上海·浦东新区",
            "salary": "25-35K",
            "url": "https://example.test/jobs/1",
            "score": 91,
            "reason": "经历与岗位职责高度匹配",
        },
        {
            "id": "job-2",
            "title": "高级数据分析师",
            "company": "另一家公司",
            "area": "北京·海淀区",
            "salary": "30-40K",
            "url": "https://example.test/jobs/2",
        },
    ]
    preview = build_delivery_preview(
        platform=platform,
        discover_id="dis-preview",
        send_candidates=jobs,
        send_command=f"jobagent {platform} send --input reviewed.json",
        selected_count=2,
        promoted_count=0,
        review_count=1,
        rejected_count=3,
        skipped_delivered_count=0,
    )

    assert preview["display_required"] is True
    assert preview["requires_user_action"] is True
    assert preview["requires_user_confirmation"] is True
    assert preview["automatic_continuation"] is False
    assert preview["summary"]["send_count"] == 2
    assert [item["title"] for item in preview["items"]] == [
        "数据运营经理",
        "高级数据分析师",
    ]
    assert all(item["delivery_actions"] == actions for item in preview["items"])
    assert "1. 数据运营经理｜示例科技｜上海·浦东新区｜25-35K" in preview["fallback_text"]
    assert "2. 高级数据分析师｜另一家公司｜北京·海淀区｜30-40K" in preview["fallback_text"]
    assert preview["continuation"] == {
        "action": "jobagent.interaction.respond",
        "automatic": False,
        "requires_user_confirmation": True,
    }
    interaction = preview["interaction"]
    assert interaction["kind"] == "delivery_confirmation"
    assert [
        option["option_id"] for option in interaction["fields"][0]["options"]
    ] == ["confirm_all", "exclude_jobs", "cancel_delivery"]
    assert "确认全部投递" in interaction["fallback_text"]
    assert "排除部分岗位" in interaction["fallback_text"]
    assert preview["host_presentations"]["adapters"]["codex"]["supported"] is True
    codex_other = preview["host_presentations"]["adapters"]["codex"]["free_text_other"]
    assert codex_other["default_option_id"] == "exclude_jobs"
    assert codex_other["exclude_indices_source"] == "other_text"
    assert (
        validate_delivery_preview(
            preview,
            send_candidates=jobs,
            expected_platform=platform,
            expected_discover_id="dis-preview",
            expected_preview_id=preview["preview_id"],
        )
        == preview
    )


def test_delivery_preview_rejects_changed_handoff_or_candidate_list():
    jobs = [{"id": "job-1", "title": "产品经理", "url": "https://example.test/jobs/1"}]
    preview = build_delivery_preview(
        platform="boss",
        discover_id="dis-preview",
        send_candidates=jobs,
        send_command="jobagent boss greet send --input reviewed.json",
        selected_count=1,
        promoted_count=0,
        review_count=0,
        rejected_count=0,
        skipped_delivered_count=0,
    )

    with pytest.raises(ValueError, match="handoff id mismatch"):
        validate_delivery_preview(
            preview,
            send_candidates=jobs,
            expected_platform="boss",
            expected_discover_id="dis-preview",
            expected_preview_id="dpv_wrong",
        )
    with pytest.raises(ValueError, match="candidate binding mismatch"):
        validate_delivery_preview(
            preview,
            send_candidates=[{**jobs[0], "url": "https://example.test/jobs/changed"}],
            expected_platform="boss",
            expected_discover_id="dis-preview",
        )

    with pytest.raises(ValueError, match="platform or Discover binding mismatch"):
        validate_delivery_preview(
            preview,
            send_candidates=jobs,
            expected_platform="liepin",
            expected_discover_id="dis-preview",
        )


@pytest.mark.parametrize("title", ["查看更多信息", "查看详情", "更多"])
def test_zhilian_delivery_preview_rejects_unreviewable_core_fields(title):
    with pytest.raises(DeliveryPreviewError) as error:
        build_delivery_preview(
            platform="zhilian",
            discover_id="dis-bad-preview",
            send_candidates=[
                {
                    "id": "job-bad",
                    "title": title,
                    "company": None,
                    "salary": None,
                    "url": "https://www.zhaopin.com/jobdetail/job-bad.htm",
                }
            ],
            send_command="jobagent zhilian apply send --input reviewed.json",
            selected_count=1,
            promoted_count=0,
            review_count=0,
            rejected_count=0,
            skipped_delivered_count=0,
        )

    assert error.value.payload["error"] == "delivery_candidate_unreviewable"
    assert error.value.payload["platform"] == "zhilian"
    assert error.value.payload["requires_user_action"] is False
    assert error.value.payload["no_charge"] is True
    assert error.value.payload["next_suggested"] == "jobagent zhilian apply review"


def test_send_requires_preview_handoff_before_delivery(monkeypatch):
    from jobagent.application import delivery

    monkeypatch.setattr(
        delivery,
        "load_envelope",
        lambda *_args, **_kwargs: {
            "platform": "zhilian",
            "discover_id": "dis-preview",
            "manifest": {},
            "send_candidates": [],
            "source_path": "/tmp/dis-preview.review.json",
        },
    )
    monkeypatch.setattr(delivery, "verify_stored_decision", lambda *_args, **_kwargs: {})

    with pytest.raises(DeliveryPreviewError) as error:
        delivery._load_reviewed("zhilian", None, preview_id=None)

    assert error.value.payload["error"] == "delivery_preview_required"
    assert error.value.payload["requires_user_action"] is False
    assert error.value.payload["request_preserved"] is True
    assert error.value.payload["next_suggested"].startswith("jobagent zhilian apply review")


def test_send_requires_bound_user_authorization_after_preview(monkeypatch):
    from jobagent.application import delivery

    jobs = [
        {
            "id": "job-1",
            "title": "数据分析师",
            "company": "示例科技",
            "salary": "20-30K",
            "url": "https://example.test/1",
        }
    ]
    preview = build_delivery_preview(
        platform="zhilian",
        discover_id="dis-confirm",
        send_candidates=jobs,
        send_command="jobagent zhilian apply send --input reviewed.json",
        selected_count=1,
        promoted_count=0,
        review_count=0,
        rejected_count=0,
        skipped_delivered_count=0,
    )
    envelope = {
        "platform": "zhilian",
        "discover_id": "dis-confirm",
        "manifest": {},
        "send_candidates": jobs,
        "delivery_preview": preview,
        "source_path": "/tmp/dis-confirm.review.json",
    }
    monkeypatch.setattr(delivery, "load_envelope", lambda *_args, **_kwargs: envelope)
    monkeypatch.setattr(delivery, "verify_stored_decision", lambda *_args, **_kwargs: {})

    with pytest.raises(DeliveryAuthorizationError) as error:
        delivery._load_reviewed(
            "zhilian",
            None,
            preview_id=preview["preview_id"],
            authorization_id=None,
        )

    assert error.value.payload["error"] == "delivery_confirmation_required"
    assert error.value.payload["requires_user_action"] is True
    assert error.value.payload["request_preserved"] is True


def test_delivery_authorization_is_bound_to_account_round_platform_preview_and_list():
    jobs = [{"id": "job-1", "title": "数据分析师", "url": "https://example.test/1"}]
    preview = build_delivery_preview(
        platform="boss",
        discover_id="dis-bound",
        send_candidates=jobs,
        send_command="jobagent boss greet send --input reviewed.json",
        selected_count=1,
        promoted_count=0,
        review_count=0,
        rejected_count=0,
        skipped_delivered_count=0,
    )
    authorization = build_delivery_authorization(
        account_ref="acct_delivery_test",
        round_id="round-1",
        platform="boss",
        discover_id="dis-bound",
        preview=preview,
        send_candidates=jobs,
        interaction_id=preview["interaction"]["interaction_id"],
    )

    assert authorization["authorization_id"].startswith("dau_")
    assert validate_delivery_authorization(
        authorization,
        expected_authorization_id=authorization["authorization_id"],
        account_ref="acct_delivery_test",
        round_id="round-1",
        platform="boss",
        discover_id="dis-bound",
        preview=preview,
        send_candidates=jobs,
    ) == authorization

    for changed in (
        {"account_ref": "acct_other_test"},
        {"round_id": "round-2"},
        {"platform": "liepin"},
        {"preview": {**preview, "preview_id": "dpv_other"}},
        {"send_candidates": [{**jobs[0], "url": "https://example.test/changed"}]},
    ):
        arguments = {
            "expected_authorization_id": authorization["authorization_id"],
            "account_ref": "acct_delivery_test",
            "round_id": "round-1",
            "platform": "boss",
            "discover_id": "dis-bound",
            "preview": preview,
            "send_candidates": jobs,
            **changed,
        }
        with pytest.raises(DeliveryAuthorizationError):
            validate_delivery_authorization(authorization, **arguments)


def test_send_rejects_wrong_authorization_id_with_safe_review_recovery(monkeypatch):
    from jobagent.application import delivery

    jobs = [{"id": "job-1", "title": "数据分析师", "url": "https://example.test/1"}]
    preview = build_delivery_preview(
        platform="boss",
        discover_id="dis-recovery",
        send_candidates=jobs,
        send_command="jobagent boss greet send --input reviewed.json",
        selected_count=1,
        promoted_count=0,
        review_count=0,
        rejected_count=0,
        skipped_delivered_count=0,
    )
    authorization = build_delivery_authorization(
        account_ref="acct_delivery_test",
        round_id="round-1",
        platform="boss",
        discover_id="dis-recovery",
        preview=preview,
        send_candidates=jobs,
        interaction_id=preview["interaction"]["interaction_id"],
    )
    envelope = {
        "platform": "boss",
        "discover_id": "dis-recovery",
        "manifest": {},
        "send_candidates": jobs,
        "delivery_preview": preview,
        "delivery_authorization": authorization,
        "source_path": "/tmp/dis-recovery.review.json",
    }
    monkeypatch.setattr(delivery, "load_envelope", lambda *_args, **_kwargs: envelope)
    monkeypatch.setattr(delivery, "verify_stored_decision", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        "jobagent.infra.account_state.current_account_ref",
        lambda: "acct_delivery_test",
    )
    monkeypatch.setattr(delivery.rounds, "round_status", lambda: {"round_id": "round-1"})

    with pytest.raises(DeliveryAuthorizationError) as error:
        delivery._load_reviewed(
            "boss",
            None,
            preview_id=preview["preview_id"],
            authorization_id="dau_wrong",
        )

    assert error.value.payload["error"] == "delivery_confirmation_required"
    assert error.value.payload["next_suggested"].startswith("jobagent boss greet preview")


def test_empty_delivery_preview_is_visible_and_can_advance_without_platform_action(
    monkeypatch,
):
    from jobagent.application import delivery

    preview = build_delivery_preview(
        platform="51job",
        discover_id="dis-empty",
        send_candidates=[],
        send_command="jobagent 51job apply send --input empty.review.json",
        selected_count=0,
        promoted_count=0,
        review_count=4,
        rejected_count=6,
        skipped_delivered_count=0,
    )
    reviewed = {
        "discover_id": "dis-empty",
        "send_candidates": [],
        "delivery_preview": preview,
        "source_path": "/tmp/empty.review.json",
    }
    statuses = []
    monkeypatch.setattr(delivery, "_load_reviewed", lambda *_args, **_kwargs: reviewed)
    monkeypatch.setattr(
        delivery.rounds,
        "set_platform_status",
        lambda platform, status, **kwargs: statuses.append((platform, status, kwargs)),
    )
    monkeypatch.setattr(
        delivery.rounds,
        "round_status",
        lambda: {"round_id": "round-1", "next_suggested": "jobagent 51job audit"},
    )

    result = delivery.send_reviewed(
        "51job",
        preview_id=preview["preview_id"],
        authorization_id=None,
    )

    assert "本平台没有待投递岗位" in preview["fallback_text"]
    assert result["attempted"] == 0
    assert result["next_suggested"] == "jobagent 51job audit"
    assert statuses[0][1] == "sent"


def test_51job_indeterminate_delivery_preserves_authorization_and_exact_resume(
    monkeypatch,
):
    from contextlib import nullcontext

    from jobagent.application import delivery
    from jobagent.domain.models import SendAttempt
    from jobagent.infra import interaction_state

    reviewed = {
        "discover_id": "dis-preserved",
        "send_candidates": [
            {"job_id": "job-pending", "url": "https://example.invalid/pending"},
            {"job_id": "job-complete", "url": "https://example.invalid/complete"},
        ],
        "source_path": "/tmp/preserved.review.json",
    }
    statuses = []
    cleared = []
    monkeypatch.setattr(delivery, "_load_reviewed", lambda *_args, **_kwargs: reviewed)
    monkeypatch.setattr(delivery, "_check_apply_login", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        delivery,
        "_apply_send",
        lambda *_args, **_kwargs: [
            SendAttempt(
                job_url="https://example.invalid/pending",
                message="",
                delivered=False,
                error="delivery_indeterminate",
            ),
            SendAttempt(
                job_url="https://example.invalid/complete",
                message="",
                delivered=True,
            ),
        ],
    )
    monkeypatch.setattr(
        delivery,
        "_delivery_outcome_summary",
        lambda _platform, _jobs: {
            "total": 2,
            "terminal": 1,
            "delivered": 1,
            "unavailable": 0,
            "unresolved": 0,
            "pending": 1,
            "failed": 0,
        },
    )
    monkeypatch.setattr(delivery, "progress_heartbeat", lambda *_args, **_kwargs: nullcontext())
    monkeypatch.setattr(delivery, "active_command", lambda *_args, **_kwargs: nullcontext())
    monkeypatch.setattr(delivery, "PlatformSessionLock", lambda *_args, **_kwargs: nullcontext())
    monkeypatch.setattr(delivery, "emit_stage", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(delivery, "print_first_delivery_star_prompt_once", lambda **_kwargs: None)
    monkeypatch.setattr(
        delivery.rounds,
        "set_platform_status",
        lambda platform, status, **kwargs: statuses.append((platform, status, kwargs)),
    )
    monkeypatch.setattr(
        delivery.rounds,
        "round_status",
        lambda: {"round_id": "round-preserved", "current_platform": "51job"},
    )
    monkeypatch.setattr(
        interaction_state,
        "load_pending_interaction",
        lambda: {
            "stage": "delivery_authorized",
            "context": {"platform": "51job", "authorization_id": "auth-preserved"},
        },
    )
    monkeypatch.setattr(interaction_state, "clear_pending_interaction", lambda: cleared.append(True))

    result = delivery.send_reviewed(
        "51job",
        input_path="/tmp/preserved.review.json",
        preview_id="preview-preserved",
        authorization_id="auth-preserved",
        limit=100,
    )

    assert result["ok"] is False
    assert result["error"] == "delivery_verification_indeterminate"
    assert result["retryable"] is True
    assert result["requires_user_action"] is False
    assert result["failed"] == 0
    assert result["indeterminate"] == 1
    assert result["delivered"] == 1
    assert result["authorization_preserved"] is True
    assert result["request_preserved"] is True
    assert result["no_charge"] is True
    assert result["billing"] == {"additional_credits": 0}
    assert result["additional_credits"] == 0
    assert "--preview-id preview-preserved" in result["next_suggested"]
    assert "--authorization-id auth-preserved" in result["next_suggested"]
    assert statuses[0][1] == "reviewed"
    assert cleared == []


def test_liepin_detail_verification_failure_preserves_signed_send_state(
    monkeypatch,
):
    from contextlib import nullcontext

    from jobagent.application import delivery
    from jobagent.domain.models import SendAttempt
    from jobagent.infra import interaction_state

    reviewed = {
        "discover_id": "dis-liepin-preserved",
        "send_candidates": [
            {
                "name": "高级产品经理",
                "area": "郑州·金水区",
                "url": "https://www.liepin.com/job/signed-detail.shtml",
                "cloud_greeting": "您好，我对这个岗位很感兴趣。",
            }
        ],
        "source_path": "/tmp/liepin-preserved.review.json",
    }
    statuses = []
    cleared = []
    monkeypatch.setattr(delivery, "_load_reviewed", lambda *_args, **_kwargs: reviewed)
    monkeypatch.setattr(
        delivery,
        "_apply_send",
        lambda *_args, **_kwargs: [
            SendAttempt(
                job_url=reviewed["send_candidates"][0]["url"],
                message=reviewed["send_candidates"][0]["cloud_greeting"],
                delivered=False,
                error="signed_job_detail_not_verified",
            )
        ],
    )
    monkeypatch.setattr(delivery, "progress_heartbeat", lambda *_args, **_kwargs: nullcontext())
    monkeypatch.setattr(delivery, "active_command", lambda *_args, **_kwargs: nullcontext())
    monkeypatch.setattr(delivery, "PlatformSessionLock", lambda *_args, **_kwargs: nullcontext())
    monkeypatch.setattr(delivery, "emit_stage", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(delivery, "print_first_delivery_star_prompt_once", lambda **_kwargs: None)
    monkeypatch.setattr(
        delivery.rounds,
        "set_platform_status",
        lambda platform, status, **kwargs: statuses.append((platform, status, kwargs)),
    )
    monkeypatch.setattr(
        delivery.rounds,
        "round_status",
        lambda: {"round_id": "round-preserved", "current_platform": "liepin"},
    )
    monkeypatch.setattr(
        interaction_state,
        "load_pending_interaction",
        lambda: {
            "stage": "delivery_authorized",
            "context": {"platform": "liepin", "authorization_id": "auth-preserved"},
        },
    )
    monkeypatch.setattr(interaction_state, "clear_pending_interaction", lambda: cleared.append(True))

    result = delivery.send_reviewed(
        "liepin",
        input_path=reviewed["source_path"],
        preview_id="preview-preserved",
        authorization_id="auth-preserved",
        limit=100,
    )

    assert result["ok"] is False
    assert result["error"] == "signed_job_detail_not_verified"
    assert result["requires_user_action"] is False
    assert result["request_preserved"] is True
    assert result["preview_preserved"] is True
    assert result["authorization_preserved"] is True
    assert result["no_charge"] is True
    assert result["billing"] == {"additional_credits": 0}
    assert "--preview-id preview-preserved" in result["next_suggested"]
    assert "--authorization-id auth-preserved" in result["next_suggested"]
    assert statuses[0][1] == "reviewed"
    assert cleared == []


def test_51job_terminal_unresolved_completes_send_with_cumulative_evidence(
    monkeypatch,
):
    from contextlib import nullcontext

    from jobagent.application import delivery
    from jobagent.domain.models import SendAttempt
    from jobagent.infra import interaction_state

    jobs = [
        {"job_id": f"delivered-{index}", "url": f"https://example.invalid/d/{index}"}
        for index in range(5)
    ] + [
        {"job_id": f"unavailable-{index}", "url": f"https://example.invalid/u/{index}"}
        for index in range(2)
    ] + [
        {"job_id": f"unresolved-{index}", "url": f"https://example.invalid/x/{index}"}
        for index in range(2)
    ]
    reviewed = {
        "discover_id": "dis-terminal",
        "send_candidates": jobs,
        "source_path": "/tmp/terminal.review.json",
    }
    attempts = [
        SendAttempt(job_url=job["url"], message="", delivered=False, error="already_delivered")
        for job in jobs[:5]
    ] + [
        SendAttempt(job_url=job["url"], message="", delivered=False, error="job_unavailable")
        for job in jobs[5:7]
    ] + [
        SendAttempt(job_url=job["url"], message="", delivered=False, error="delivery_unresolved")
        for job in jobs[7:]
    ]
    cumulative = {
        "total": 9,
        "terminal": 9,
        "delivered": 5,
        "unavailable": 2,
        "unresolved": 2,
        "pending": 0,
        "failed": 0,
    }
    statuses = []
    cleared = []
    monkeypatch.setattr(delivery, "_load_reviewed", lambda *_args, **_kwargs: reviewed)
    monkeypatch.setattr(delivery, "_check_apply_login", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(delivery, "_apply_send", lambda *_args, **_kwargs: attempts)
    monkeypatch.setattr(
        delivery,
        "_delivery_outcome_summary",
        lambda _platform, _jobs: cumulative,
        raising=False,
    )
    monkeypatch.setattr(delivery, "progress_heartbeat", lambda *_args, **_kwargs: nullcontext())
    monkeypatch.setattr(delivery, "active_command", lambda *_args, **_kwargs: nullcontext())
    monkeypatch.setattr(delivery, "PlatformSessionLock", lambda *_args, **_kwargs: nullcontext())
    monkeypatch.setattr(delivery, "emit_stage", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(delivery, "print_first_delivery_star_prompt_once", lambda **_kwargs: None)
    monkeypatch.setattr(
        delivery.rounds,
        "set_platform_status",
        lambda platform, status, **kwargs: statuses.append((platform, status, kwargs)),
    )
    monkeypatch.setattr(
        delivery.rounds,
        "round_status",
        lambda: {"round_id": "round-terminal", "current_platform": "51job"},
    )
    monkeypatch.setattr(
        interaction_state,
        "load_pending_interaction",
        lambda: {
            "stage": "delivery_authorized",
            "context": {"platform": "51job", "authorization_id": "auth-terminal"},
        },
    )
    monkeypatch.setattr(interaction_state, "clear_pending_interaction", lambda: cleared.append(True))

    result = delivery.send_reviewed(
        "51job",
        input_path="/tmp/terminal.review.json",
        preview_id="preview-terminal",
        authorization_id="auth-terminal",
        limit=100,
    )

    assert result["ok"] is True
    assert result["retryable"] is False
    assert result["next_suggested"] == "jobagent 51job audit"
    assert result["delivered"] == 5
    assert result["skipped"] == 2
    assert result["indeterminate"] == 0
    assert result["unresolved"] == 2
    assert result["completion_state"] == "completed_with_unresolved"
    assert result["no_charge"] is True
    assert result["request_preserved"] is True
    assert result["preview_preserved"] is True
    assert result["authorization_preserved"] is True
    assert statuses[0][1] == "sent"
    assert statuses[0][2]["evidence"] == {
        "discover_id": "dis-terminal",
        "attempted": 9,
        "delivered": 5,
        "failed": 0,
        "skipped": 2,
        "indeterminate": 0,
        "unresolved": 2,
        "reviewed_count": 9,
    }
    assert cleared == [True]


@pytest.mark.parametrize('platform', ['boss', 'liepin'])
def test_greeting_changes_have_new_confirmation_ids_and_show_exact_text(platform):
    from jobagent.infra.delivery_preview import _preview_id
    jobs = [{'id': 'j1', 'title': '数据产品经理', 'company': '示例', 'cloud_greeting': '您好，我有8年AI产品设计经验。'}]
    def build(values):
        return build_delivery_preview(platform=platform, discover_id='dis-one', send_candidates=values,
            send_command='unused', selected_count=1, promoted_count=0, review_count=0, rejected_count=0, skipped_delivered_count=0)
    first = build(jobs)
    assert first['items'][0]['greeting'] == jobs[0]['cloud_greeting']
    assert jobs[0]['cloud_greeting'] in first['fallback_text']
    changed = [{**jobs[0], 'cloud_greeting': '您好，我有6年数据治理经验。'}]
    second = build(changed)
    assert first['preview_id'] != second['preview_id']
    assert first['interaction']['interaction_id'] != second['interaction']['interaction_id']
    with pytest.raises(ValueError):
        validate_delivery_preview(first, send_candidates=changed, expected_platform=platform, expected_discover_id='dis-one')
    # Persisted legacy previews remain readable; existing authorization still
    # binds the complete send_candidates digest and never becomes a new permit.
    import copy
    legacy = copy.deepcopy(first); legacy.pop('content_bound')
    for item in legacy['items']: item.pop('greeting')
    legacy['preview_id'] = _preview_id(platform, 'dis-one', legacy['items'])
    legacy['interaction']['interaction_id'] = 'delivery:'+legacy['preview_id']
    assert validate_delivery_preview(legacy, send_candidates=jobs, expected_platform=platform, expected_discover_id='dis-one') == legacy
