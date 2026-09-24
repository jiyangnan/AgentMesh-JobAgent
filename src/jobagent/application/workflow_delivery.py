"""Bind local complete previews to the service's authorization and revocation."""
from jobagent.infra import state, cloud_client, protocol, rounds
from jobagent.infra.account_state import current_account_ref
from jobagent.infra.delivery_preview import DeliveryPreviewError, preview_required_payload


def enabled():
    workflow = state.load_json(state.STATE_DIR / "workflow.json") or {}
    active = state.load_json(state.current_round_path()) or {}
    return bool(workflow.get("intent") or active.get("round_criteria"))


def _verify(payload, kind, review):
    proof = protocol.verify_signed_payload(payload, public_key=protocol.DECISION_SIGNING_PUBLIC_KEY, expected_type=kind)
    active = rounds.ensure_current_round()
    if (proof.get("protocol_version") != 2 or proof.get("account_ref") != current_account_ref()
            or proof.get("round_id") != active["round_id"] or proof.get("discover_id") != review["discover_id"]
            or proof.get("client_preview_id") != review["delivery_preview"]["preview_id"]
            or proof.get("candidate_digest") != protocol.digest_payload(review["send_candidates"])
            or proof.get("criteria_revision") != (active.get("round_criteria") or {}).get("criteria_revision", 0)):
        raise ValueError("Workflow delivery receipt context mismatch")
    return payload


def register(review):
    if not enabled():
        return
    active = rounds.ensure_current_round()
    response = cloud_client.workflow_delivery_preview({"round_id": active["round_id"],
        "discover_id": review["discover_id"], "manifest_id": review["manifest"]["manifest_id"],
        "client_preview_id": review["delivery_preview"]["preview_id"],
        "criteria_revision": (active.get("round_criteria") or {}).get("criteria_revision", 0),
        "candidates": review["send_candidates"],
        "promoted_ids": [item["job_id"] for item in review.get("user_overrides", [])],
        "confirm_promote": bool(review.get("user_overrides")),
        "excluded_ids": [str(item.get("id") or item.get("job_id")) for item in review.get("user_delivery_exclusions", [])]})
    review["server_preview"] = _verify(response["preview"], "workflow_delivery_preview", review)


def answer(review, choice):
    if not enabled() and not review.get("server_preview"):
        return None
    if not review.get("server_preview"):
        raise DeliveryPreviewError(preview_required_payload(review["platform"], review.get("source_path")))
    response = cloud_client.workflow_delivery_answer(review["server_preview"]["preview_id"], choice)
    if choice == "confirm_all":
        return _verify(response["authorization"], "workflow_delivery_authorization", review)
    return None


def assert_authorized(review):
    if not enabled() and not review.get("server_preview"):
        return
    preview = review.get("server_preview") or {}
    authorization = (review.get("delivery_authorization") or {}).get("server_authorization")
    if not preview or not authorization:
        raise DeliveryPreviewError(preview_required_payload(review["platform"], review.get("source_path")))
    _verify(authorization, "workflow_delivery_authorization", review)
    try:
        response = cloud_client.workflow_delivery_status(preview["preview_id"])
    except cloud_client.CloudError as exc:
        if exc.code in {"delivery_preview_superseded", "delivery_confirmation_required", "delivery_list_cancelled", "criteria_revision_conflict", "delivery_manifest_stale", "delivery_decision_expired"}:
            payload = preview_required_payload(review["platform"], review.get("source_path"))
            payload["cause"] = exc.code
            raise DeliveryPreviewError(payload) from exc
        raise
    if response.get("authorization") != authorization:
        raise ValueError("The server authorization changed after confirmation")
