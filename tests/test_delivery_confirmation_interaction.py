from __future__ import annotations

import json

import pytest

from jobagent.application.delivery_confirmation import (
    register_delivery_confirmation,
    respond_delivery_confirmation,
)
from jobagent.cli import _dispatch, build_parser
from jobagent.infra import interaction_state, state
from jobagent.infra.delivery_preview import build_delivery_preview


def _jobs() -> list[dict]:
    return [
        {
            "id": f"job-{index}",
            "job_id": f"job-{index}",
            "title": title,
            "company": f"示例公司{index}",
            "url": f"https://example.test/jobs/{index}",
        }
        for index, title in enumerate(
            ["数据分析师", "数据运营经理", "商业分析师"],
            start=1,
        )
    ]


@pytest.fixture
def confirmation_context(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "STATE_DIR", tmp_path)
    monkeypatch.setattr(state, "ROUNDS_DIR", tmp_path / "rounds")
    monkeypatch.setattr("jobagent.application.delivery_followup.current_account_ref", lambda: "acct_delivery_test")
    state.save_json(state.current_round_path(), {
        "schema_version": 4, "round_id": "round-1", "status": "active",
        "platform_order": ["boss", "liepin", "zhilian", "51job"],
        "platforms": {"boss": {"status": "completed"}, "liepin": {"status": "awaiting_delivery_confirmation"},
                      "zhilian": {"status": "pending"}, "51job": {"status": "pending"}},
    })
    pending_path = tmp_path / "pending-interaction.json"
    review_path = tmp_path / "reviewed.json"
    jobs = _jobs()
    preview = build_delivery_preview(
        platform="liepin",
        discover_id="dis-confirm",
        send_candidates=jobs,
        send_command=f"jobagent liepin apply send --input {review_path}",
        selected_count=3,
        promoted_count=0,
        review_count=0,
        rejected_count=2,
        skipped_delivered_count=0,
    )
    review = {
        "platform": "liepin",
        "discover_id": "dis-confirm",
        "manifest": {},
        "send_candidates": jobs,
        "delivery_preview": preview,
    }
    review_path.write_text(json.dumps(review, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(state, "pending_interaction_path", lambda: pending_path)
    monkeypatch.setattr(
        "jobagent.application.delivery_confirmation.verify_stored_decision",
        lambda *_args, **_kwargs: {},
    )
    monkeypatch.setattr(
        "jobagent.application.delivery_confirmation.current_account_ref",
        lambda: "acct_delivery_test",
    )
    statuses: list[tuple[str, str, dict]] = []
    monkeypatch.setattr(
        "jobagent.application.delivery_confirmation.rounds.set_platform_status",
        lambda platform, status, **kwargs: statuses.append((platform, status, kwargs)) or {},
    )
    register_delivery_confirmation(
        platform="liepin",
        review_path=str(review_path),
        review=review,
        preview=preview,
        round_id="round-1",
        account_ref="acct_delivery_test",
    )
    return {
        "pending_path": pending_path,
        "review_path": review_path,
        "statuses": statuses,
    }


def test_confirm_all_creates_bound_authorization_and_send_command(confirmation_context):
    pending = interaction_state.load_pending_interaction()

    result = respond_delivery_confirmation(
        pending,
        choice="confirm_all",
        exclude_indices=[],
    )

    saved = json.loads(
        confirmation_context["review_path"].read_text(encoding="utf-8")
    )
    authorization = saved["delivery_authorization"]
    assert result["ok"] is True
    assert result["event"] == "delivery_authorized"
    assert authorization["authorization_id"] in result["next_suggested"]
    assert saved["delivery_preview"]["preview_id"] in result["next_suggested"]
    assert confirmation_context["statuses"][-1][1] == "reviewed"


def test_cli_routes_delivery_confirmation_without_requiring_profile(confirmation_context):
    pending = interaction_state.load_pending_interaction()

    result = _dispatch(
        build_parser().parse_args(
            [
                "interaction",
                "respond",
                "--interaction-id",
                pending["interaction_id"],
                "--choice",
                "confirm_all",
            ]
        )
    )

    assert result["event"] == "delivery_authorized"
    assert result["authorization_id"] in result["next_suggested"]


def test_excluding_jobs_regenerates_preview_and_requires_final_confirmation(
    confirmation_context,
):
    first = interaction_state.load_pending_interaction()

    follow_up = respond_delivery_confirmation(
        first,
        choice="exclude_jobs",
        exclude_indices=[],
    )

    assert follow_up["error"] == "interaction_required"
    assert follow_up["interaction"]["kind"] == "delivery_exclusions"

    excluded = respond_delivery_confirmation(
        interaction_state.load_pending_interaction(),
        choice="exclude_jobs",
        exclude_indices=[2],
    )

    saved = json.loads(
        confirmation_context["review_path"].read_text(encoding="utf-8")
    )
    assert excluded["error"] == "interaction_required"
    assert excluded["event"] == "delivery_preview"
    assert [item["title"] for item in excluded["delivery_preview"]["items"]] == [
        "数据分析师",
        "商业分析师",
    ]
    assert [item["title"] for item in saved["user_delivery_exclusions"]] == [
        "数据运营经理"
    ]
    assert "delivery_authorization" not in saved

    confirmed = respond_delivery_confirmation(
        interaction_state.load_pending_interaction(),
        choice="confirm_all",
        exclude_indices=[],
    )
    assert confirmed["event"] == "delivery_authorized"
    assert "delivery_authorization" in json.loads(
        confirmation_context["review_path"].read_text(encoding="utf-8")
    )


def test_cancel_delivery_waits_for_destination_without_authorization(confirmation_context):
    result = respond_delivery_confirmation(
        interaction_state.load_pending_interaction(),
        choice="cancel_delivery",
        exclude_indices=[],
    )

    saved = json.loads(
        confirmation_context["review_path"].read_text(encoding="utf-8")
    )
    assert result["ok"] is False
    assert result["requires_user_action"] is True
    assert result["event"] == "delivery_cancelled"
    assert "delivery_authorization" not in saved
    assert state.load_json(state.current_round_path())["platforms"]["liepin"]["status"] == "awaiting_after_cancel_choice"
    assert confirmation_context["pending_path"].exists()


def test_exclusion_indices_are_validated_before_review_is_changed(confirmation_context):
    original = confirmation_context["review_path"].read_text(encoding="utf-8")

    result = respond_delivery_confirmation(
        interaction_state.load_pending_interaction(),
        choice="exclude_jobs",
        exclude_indices=[0, 9],
    )

    assert result["error"] == "invalid_interaction_response"
    assert confirmation_context["review_path"].read_text(encoding="utf-8") == original


def test_work_next_restores_full_preview_then_same_exclusion_prompt(confirmation_context):
    from jobagent.infra.workflow_protocol import with_contract
    pending = interaction_state.load_pending_interaction()
    resumed = _dispatch(build_parser().parse_args(['work', 'next']))
    assert resumed['interaction']['interaction_id'] == pending['interaction_id']
    assert len(resumed['delivery_preview']['items']) == 3
    assert with_contract(resumed)['agent_action']['type'] == 'wait_user'
    exclusions = respond_delivery_confirmation(pending, choice='exclude_jobs', exclude_indices=[])
    resumed = _dispatch(build_parser().parse_args(['work', 'next']))
    assert resumed['interaction'] == exclusions['interaction']
    action = with_contract(resumed)['agent_action']
    assert action['type'] == 'wait_user'
    assert action['response_arguments']['answer_flag'] == '--exclude-index'
    saved = json.loads(confirmation_context['review_path'].read_text())
    assert not saved.get('delivery_authorization')


@pytest.mark.parametrize('choice', ['cancel_delivery', 'exclude_jobs'])
def test_cancel_destination_is_explicit_and_idempotent(confirmation_context, choice):
    from jobagent.application import delivery_followup as followup
    result = respond_delivery_confirmation(interaction_state.load_pending_interaction(), choice=choice,
                                           exclude_indices=[1, 2, 3] if choice == 'exclude_jobs' else [])
    interaction_id = result['interaction']['interaction_id']
    assert [o['option_id'] for o in result['interaction']['fields'][0]['options']] == ['search_again', 'skip_platform']
    assert followup.pending()['interaction']['interaction_id'] == interaction_id
    with pytest.raises(Exception) as exc:
        followup.assert_list_active('liepin', 'dis-confirm')
    assert exc.value.payload['error'] == 'delivery_list_cancelled'
    answered = followup.respond(interaction_id, 'search_again')
    assert answered['event'] == 'search_again_requested'
    assert answered['workflow']['round_id'] == 'round-1'
    before = state.load_json(state.current_round_path())
    assert before['platforms']['liepin']['search_attempt'] == 2
    assert followup.respond(interaction_id, 'search_again')['idempotent_replay']
    assert state.load_json(state.current_round_path()) == before
    assert followup.respond(interaction_id, 'skip_platform')['error'] == 'interaction_response_conflict'
    assert followup.pending() is None


def test_cancel_then_skip_advances_without_search(confirmation_context):
    from jobagent.application import delivery_followup as followup
    result = respond_delivery_confirmation(interaction_state.load_pending_interaction(), choice='cancel_delivery', exclude_indices=[])
    answered = followup.respond(result['interaction']['interaction_id'], 'skip_platform')
    assert answered['workflow']['current_platform'] == 'zhilian'
    assert answered['workflow']['platforms']['liepin']['status'] == 'skipped_this_round'
    assert 'search_attempt' not in state.load_json(state.current_round_path())['platforms']['liepin']
