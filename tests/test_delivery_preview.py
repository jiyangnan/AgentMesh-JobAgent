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
