from __future__ import annotations

import json
import stat
import sys
import threading
import time

import pytest

from jobagent import __version__
from jobagent.infra import account_state, analytics, cloud_client, state


API_KEY = "jobagent_live_analytics_test_key"
ACCOUNT_REF = "acct_analytics_test"
OTHER_API_KEY = "jobagent_live_other_key"
OTHER_ACCOUNT_REF = "acct_other_account"
ORIGINAL_SCHEDULE_FLUSH = analytics.schedule_flush


@pytest.fixture
def bound_analytics(tmp_path, monkeypatch):
    monkeypatch.setattr(state, "STATE_DIR", tmp_path / "state")
    for name in (
        "JOBAGENT_ANALYTICS_DISABLED",
        "JOBAGENT_ANALYTICS_KILL_SWITCH",
        "DO_NOT_TRACK",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(analytics, "schedule_flush", lambda **_kwargs: False)
    account_state.ensure_account_state(
        {"account": {"account_ref": ACCOUNT_REF}},
        api_key=API_KEY,
        app_dir=tmp_path,
    )
    return tmp_path


def _spool() -> dict:
    return json.loads(analytics._spool_path().read_text(encoding="utf-8"))


def test_verified_init_persists_first_fact_with_0600_permissions(tmp_path, monkeypatch):
    from jobagent import cli

    monkeypatch.setattr(state, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(account_state, "APP_DIR", tmp_path)
    monkeypatch.setattr(analytics, "schedule_flush", lambda **_kwargs: False)
    monkeypatch.setattr(
        cloud_client,
        "me",
        lambda *, api_key=None: {
            "account": {"account_ref": ACCOUNT_REF},
            "credits": {"available": 0},
        },
    )
    monkeypatch.setattr(
        "jobagent.infra.credentials.save_api_key",
        lambda _key: tmp_path / "credentials",
    )
    monkeypatch.setattr(
        "jobagent.infra.product_announcements.mark_workbench_launch_announced",
        lambda: None,
    )

    result = cli._init(cli.build_parser().parse_args(["init", "--key", API_KEY]))

    assert result["ok"] is True
    spool = _spool()
    assert spool["facts"] == ["jobagent_initialized"]
    assert len(spool["events"]) == 1
    assert stat.S_IMODE(analytics._spool_path().stat().st_mode) == 0o600


def test_unverified_or_failed_init_does_not_record_fact(tmp_path, monkeypatch):
    from jobagent import cli

    monkeypatch.setattr(state, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(account_state, "APP_DIR", tmp_path)
    monkeypatch.setattr(analytics, "schedule_flush", lambda **_kwargs: False)
    monkeypatch.setattr(
        "jobagent.infra.credentials.save_api_key",
        lambda _key: tmp_path / "credentials",
    )

    no_verify = cli._init(
        cli.build_parser().parse_args(["init", "--key", API_KEY, "--no-verify"])
    )

    assert no_verify["ok"] is True
    assert not analytics._spool_path().exists()

    monkeypatch.setattr(
        cloud_client,
        "me",
        lambda *, api_key=None: {"account": {"account_ref": ACCOUNT_REF}},
    )

    def fail_binding(*_args, **_kwargs):
        raise account_state.AccountStateError({"ok": False, "error": "owner_mismatch"})

    monkeypatch.setattr(account_state, "ensure_account_state", fail_binding)
    failed = cli._init(cli.build_parser().parse_args(["init", "--key", API_KEY]))

    assert failed["ok"] is False
    assert not analytics._spool_path().exists()


def test_successful_legacy_account_bind_records_missing_initialized_fact(
    tmp_path,
    monkeypatch,
):
    from jobagent import cli
    from jobagent.infra import credentials

    monkeypatch.setattr(state, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(account_state, "APP_DIR", tmp_path)
    monkeypatch.setattr(analytics, "schedule_flush", lambda **_kwargs: False)
    (tmp_path / "state").mkdir(parents=True)
    (tmp_path / "state" / "profile.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        cloud_client,
        "me",
        lambda: {"account": {"account_ref": ACCOUNT_REF}},
    )
    monkeypatch.setattr(credentials, "load_api_key", lambda: API_KEY)

    result = cli._account(
        cli.build_parser().parse_args(["account", "bind", "--confirm-legacy"])
    )

    assert result["ok"] is True
    assert _spool()["facts"] == ["jobagent_initialized"]


def test_delivery_fact_requires_completed_real_batch_and_persisted_audit(monkeypatch):
    from jobagent.application import delivery

    recorded: list[str] = []
    monkeypatch.setattr(
        delivery,
        "_has_persisted_verified_delivery",
        lambda _platform: True,
    )
    monkeypatch.setattr(
        analytics,
        "record_delivery_verified",
        lambda platform: recorded.append(platform) or True,
    )

    delivery._record_completed_delivery_fact(
        "boss",
        complete_batch=True,
        delivered=1,
        dry_run=False,
    )
    delivery._record_completed_delivery_fact(
        "liepin",
        complete_batch=True,
        delivered=0,
        dry_run=False,
    )
    delivery._record_completed_delivery_fact(
        "zhilian",
        complete_batch=False,
        delivered=1,
        dry_run=False,
    )
    delivery._record_completed_delivery_fact(
        "51job",
        complete_batch=True,
        delivered=1,
        dry_run=True,
    )
    monkeypatch.setattr(
        delivery,
        "_has_persisted_verified_delivery",
        lambda _platform: False,
    )
    delivery._record_completed_delivery_fact(
        "liepin",
        complete_batch=True,
        delivered=1,
        dry_run=False,
    )

    assert recorded == ["boss"]


def test_committed_facts_are_deduplicated_per_account_and_platform(bound_analytics):
    assert analytics.record_jobagent_initialized(api_key=API_KEY) is True
    assert analytics.record_jobagent_initialized(api_key=API_KEY) is False
    assert analytics.record_delivery_verified("boss", api_key=API_KEY) is True
    assert analytics.record_delivery_verified("boss", api_key=API_KEY) is False
    assert analytics.record_delivery_verified("liepin", api_key=API_KEY) is True

    spool = _spool()
    assert spool["facts"] == [
        "jobagent_initialized",
        "delivery_verified:boss",
        "delivery_verified:liepin",
    ]
    assert [event["event_name"] for event in spool["events"]] == [
        "jobagent_initialized",
        "delivery_verified",
        "delivery_verified",
    ]


def test_pending_fact_from_previous_client_release_remains_readable(bound_analytics):
    analytics.record_jobagent_initialized(api_key=API_KEY)
    spool = _spool()
    spool["events"][0]["payload"]["client_release"] = "0.5.39"
    analytics._write_spool(spool, account_ref=ACCOUNT_REF)

    loaded = analytics._load_spool(account_ref=ACCOUNT_REF)

    assert loaded is not None
    assert loaded["events"][0]["payload"] == {"client_release": "0.5.39"}


def test_relay_failure_and_invalid_response_retain_identical_spool(
    bound_analytics,
    monkeypatch,
):
    analytics.record_jobagent_initialized(api_key=API_KEY)
    before = analytics._spool_path().read_bytes()

    def offline(*_args, **_kwargs):
        raise cloud_client.CloudError("offline", code="network_timeout", retryable=True)

    monkeypatch.setattr(cloud_client, "analytics_events", offline)
    assert analytics._flush_once(api_key=API_KEY) is False
    assert analytics._spool_path().read_bytes() == before

    monkeypatch.setattr(
        cloud_client,
        "analytics_events",
        lambda *_args, **_kwargs: {"ok": True},
    )
    assert analytics._flush_once(api_key=API_KEY) is False
    assert analytics._spool_path().read_bytes() == before


def test_acknowledged_and_duplicate_events_leave_dedupe_markers(
    bound_analytics,
    monkeypatch,
):
    analytics.record_jobagent_initialized(api_key=API_KEY)
    analytics.record_delivery_verified("boss", api_key=API_KEY)
    pending = list(_spool()["events"])
    monkeypatch.setattr(
        cloud_client,
        "analytics_events",
        lambda events, *, api_key=None: {
            "accepted_event_ids": [events[0]["event_id"]],
            "duplicate_event_ids": [events[1]["event_id"]],
            "rejected": [],
        },
    )

    assert analytics._flush_once(api_key=API_KEY) is True
    spool = _spool()
    assert spool["events"] == []
    assert spool["facts"] == ["jobagent_initialized", "delivery_verified:boss"]
    assert analytics.record_jobagent_initialized(api_key=API_KEY) is False
    assert analytics.record_delivery_verified("boss", api_key=API_KEY) is False
    assert {event["event_id"] for event in pending}


def test_key_switch_fails_closed_and_account_spools_stay_separate(
    bound_analytics,
    monkeypatch,
):
    analytics.record_jobagent_initialized(api_key=API_KEY)
    before = analytics._spool_path().read_bytes()
    monkeypatch.setattr(
        cloud_client,
        "analytics_events",
        lambda *_args, **_kwargs: pytest.fail("mismatched key reached relay"),
    )

    assert analytics._flush_once(api_key=OTHER_API_KEY) is False
    assert analytics.record_delivery_verified(
        "boss", api_key=OTHER_API_KEY
    ) is False
    assert analytics._spool_path().read_bytes() == before

    account_state.switch_account_state(
        {"account": {"account_ref": OTHER_ACCOUNT_REF}},
        new_state=True,
        api_key=OTHER_API_KEY,
        app_dir=bound_analytics,
    )

    assert not analytics._spool_path().exists()
    saved = (
        bound_analytics
        / "accounts"
        / ACCOUNT_REF
        / "state"
        / "analytics_spool.json"
    )
    assert saved.read_bytes() == before
    assert stat.S_IMODE(saved.stat().st_mode) == 0o600

    other_spool = analytics._spool_path()
    assert other_spool != saved
    assert analytics.record_jobagent_initialized(api_key=OTHER_API_KEY) is True

    account_state.switch_account_state(
        {"account": {"account_ref": ACCOUNT_REF}},
        new_state=True,
        api_key=API_KEY,
        app_dir=bound_analytics,
    )

    assert analytics._spool_path() == saved
    assert _spool()["facts"] == ["jobagent_initialized"]
    assert json.loads(other_spool.read_text(encoding="utf-8"))["facts"] == [
        "jobagent_initialized"
    ]


def test_record_pins_verified_owner_when_account_switches_before_spool_load(
    bound_analytics,
    monkeypatch,
):
    original_load = analytics._load_spool
    switched = False

    def switch_then_load(*, account_ref=None):
        nonlocal switched
        if not switched:
            switched = True
            account_state.switch_account_state(
                {"account": {"account_ref": OTHER_ACCOUNT_REF}},
                new_state=True,
                api_key=OTHER_API_KEY,
                app_dir=bound_analytics,
            )
        return original_load(account_ref=account_ref)

    monkeypatch.setattr(analytics, "_load_spool", switch_then_load)

    assert analytics.record_jobagent_initialized(api_key=API_KEY) is True
    assert account_state.current_account_ref(app_dir=bound_analytics) == OTHER_ACCOUNT_REF
    account_a_spool = analytics._spool_path(ACCOUNT_REF)
    account_b_spool = analytics._spool_path(OTHER_ACCOUNT_REF)
    assert json.loads(account_a_spool.read_text(encoding="utf-8"))["facts"] == [
        "jobagent_initialized"
    ]
    assert not account_b_spool.exists()


def test_flush_pins_verified_owner_when_account_switches_before_ack(
    bound_analytics,
    monkeypatch,
):
    assert analytics.record_jobagent_initialized(api_key=API_KEY) is True

    def switch_then_ack(events, *, api_key=None):
        assert api_key == API_KEY
        account_state.switch_account_state(
            {"account": {"account_ref": OTHER_ACCOUNT_REF}},
            new_state=True,
            api_key=OTHER_API_KEY,
            app_dir=bound_analytics,
        )
        return {
            "accepted_event_ids": [events[0]["event_id"]],
            "duplicate_event_ids": [],
            "rejected": [],
        }

    monkeypatch.setattr(cloud_client, "analytics_events", switch_then_ack)

    assert analytics._flush_once(api_key=API_KEY) is True
    assert account_state.current_account_ref(app_dir=bound_analytics) == OTHER_ACCOUNT_REF
    account_a_spool = json.loads(
        analytics._spool_path(ACCOUNT_REF).read_text(encoding="utf-8")
    )
    assert account_a_spool["facts"] == ["jobagent_initialized"]
    assert account_a_spool["events"] == []
    assert not analytics._spool_path(OTHER_ACCOUNT_REF).exists()


@pytest.mark.parametrize(
    "environment",
    ["JOBAGENT_ANALYTICS_DISABLED", "JOBAGENT_ANALYTICS_KILL_SWITCH", "DO_NOT_TRACK"],
)
def test_opt_out_and_kill_switch_disable_record_and_relay(
    bound_analytics,
    monkeypatch,
    environment,
):
    monkeypatch.setenv(environment, "1")
    monkeypatch.setattr(
        cloud_client,
        "analytics_events",
        lambda *_args, **_kwargs: pytest.fail("disabled analytics reached relay"),
    )

    assert analytics.record_jobagent_initialized(api_key=API_KEY) is False
    assert analytics._flush_once(api_key=API_KEY) is False
    assert not analytics._spool_path().exists()


def test_event_and_relay_payloads_exclude_sensitive_fields(bound_analytics, monkeypatch):
    analytics.record_jobagent_initialized(api_key=API_KEY)
    analytics.record_delivery_verified("51job", api_key=API_KEY)
    events = _spool()["events"]

    assert events[0].keys() == {
        "event_id",
        "event_name",
        "schema_version",
        "occurred_at",
        "payload",
    }
    assert events[0]["payload"] == {"client_release": __version__}
    assert events[1]["payload"] == {
        "platform": "51job",
        "client_release": __version__,
    }
    serialized = json.dumps(events, ensure_ascii=False).lower()
    for forbidden in (
        ACCOUNT_REF,
        API_KEY,
        "account_ref",
        "api_key",
        "round_id",
        "job_id",
        "resume",
        "url",
        "command",
        "source_system",
        "evidence_grade",
    ):
        assert forbidden.lower() not in serialized

    captured = {}

    def relay(method, path, body, **kwargs):
        captured.update({"method": method, "path": path, "body": body, **kwargs})
        return {"accepted_event_ids": [], "duplicate_event_ids": [], "rejected": []}

    monkeypatch.setattr(cloud_client, "_request", relay)
    cloud_client.analytics_events(events, api_key=API_KEY)

    assert captured["method"] == "POST"
    assert captured["path"] == "/v1/analytics/events"
    assert captured["body"] == {"events": events}
    assert captured["api_key"] == API_KEY
    assert captured["timeout"] == 2
    assert captured["max_attempts"] == 1


def test_relay_is_bounded_and_single_concurrency(bound_analytics, monkeypatch):
    analytics.record_jobagent_initialized(api_key=API_KEY)
    event = _spool()["events"][0]
    with pytest.raises(ValueError, match="between 1 and 25"):
        cloud_client.analytics_events([event] * 26, api_key=API_KEY)

    monkeypatch.setattr(
        cloud_client,
        "analytics_events",
        lambda *_args, **_kwargs: pytest.fail("second flush reached relay"),
    )

    with analytics._exclusive_lock(
        analytics._flush_lock_path(),
        analytics._FLUSH_THREAD_LOCK,
    ) as acquired:
        assert acquired is True
        assert analytics._flush_once(api_key=API_KEY) is False


def test_cli_dispatch_does_not_wait_for_blocked_or_failed_relay(
    bound_analytics,
    monkeypatch,
    capsys,
):
    from jobagent import cli
    from jobagent.infra import credentials

    analytics.record_jobagent_initialized(api_key=API_KEY)
    started = threading.Event()
    release = threading.Event()
    dispatched: list[bool] = []

    def blocked_then_failed(*, api_key=None, account_ref=None):
        assert account_ref == ACCOUNT_REF
        started.set()
        release.wait(timeout=1)
        raise cloud_client.CloudError("offline", code="network_timeout", retryable=True)

    def dispatch(_args):
        assert started.wait(timeout=0.25)
        assert release.is_set() is False
        dispatched.append(True)
        return {"ok": True, "command": "test"}

    monkeypatch.setattr(analytics, "_flush_once", blocked_then_failed)
    monkeypatch.setattr(analytics, "schedule_flush", ORIGINAL_SCHEDULE_FLUSH)
    monkeypatch.setattr(analytics, "_worker", None)
    monkeypatch.setattr(credentials, "load_api_key", lambda: API_KEY)
    monkeypatch.setattr(cli, "_maybe_update", lambda _args: None)
    monkeypatch.setattr(cli, "_prepare_client_upgrade", lambda _args: None)
    monkeypatch.setattr(cli, "_verify_state_owner_for_command", lambda _args: None)
    monkeypatch.setattr(cli, "_dispatch", dispatch)
    monkeypatch.setattr(
        cli,
        "_attach_pending_product_announcements",
        lambda result, **_kwargs: result,
    )
    monkeypatch.setattr(sys, "argv", ["jobagent", "platforms", "status"])

    before = time.monotonic()
    cli.main()
    elapsed = time.monotonic() - before

    assert dispatched == [True]
    assert elapsed < 0.5
    assert json.loads(capsys.readouterr().out)["ok"] is True
    release.set()
    worker = analytics._worker
    assert worker is not None
    worker.join(timeout=1)
    assert worker.is_alive() is False
