from jobagent.infra.agent_host import AGENT_HOSTS, agent_host_header, detect_agent_host


def test_detects_known_agent_hosts_from_environment_markers():
    cases = {
        "claude_code": {"CLAUDECODE": "1"},
        "codex": {"CODEX_SANDBOX": "seatbelt"},
        "gemini_cli": {"GEMINI_CLI": "1"},
        "openclaw": {"OPENCLAW_SESSION": "x"},
        "opencode": {"OPENCODE": "1"},
        "cursor": {"CURSOR_TRACE_ID": "abc"},
    }
    for expected, env in cases.items():
        assert detect_agent_host(env, interactive=False) == expected


def test_config_only_variables_do_not_claim_a_host():
    assert detect_agent_host({"CODEX_HOME": "/x"}, interactive=True) == "terminal"
    assert detect_agent_host({}, interactive=False) == "other_agent"


def test_explicit_override_must_be_a_known_label():
    assert detect_agent_host({"JOBAGENT_AGENT_HOST": "Codex", "CLAUDECODE": "1"}) == "codex"
    assert detect_agent_host({"JOBAGENT_AGENT_HOST": "rm -rf", "CLAUDECODE": "1"}) == "claude_code"


def test_header_respects_analytics_opt_out_and_only_emits_known_labels():
    assert agent_host_header({"CLAUDECODE": "1", "DO_NOT_TRACK": "1"}) is None
    assert agent_host_header({"CLAUDECODE": "1", "JOBAGENT_ANALYTICS_DISABLED": "true"}) is None
    assert agent_host_header({"CLAUDECODE": "1"}) == "claude_code"
    assert agent_host_header({}) in AGENT_HOSTS


def test_cloud_requests_carry_the_agent_host_header(monkeypatch):
    import json
    from jobagent.infra import cloud_client

    seen = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return json.dumps({"ok": True}).encode()

    def fake_urlopen(request, timeout=None, context=None):
        seen.update({k.lower(): v for k, v in request.header_items()})
        return Response()

    monkeypatch.setattr(cloud_client.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setenv("CLAUDECODE", "1")
    for name in ("DO_NOT_TRACK", "JOBAGENT_ANALYTICS_DISABLED", "JOBAGENT_ANALYTICS_KILL_SWITCH",
                 "JOBAGENT_AGENT_HOST"):
        monkeypatch.delenv(name, raising=False)
    cloud_client._request("GET", "/v1/me", require_auth=True, api_key="ja_test_key")
    assert seen["x-jobagent-agent-host"] == "claude_code"
    assert seen["x-jobagent-client-version"]
    # Unauthenticated requests (health checks before setup) never carry the label.
    seen.clear()
    cloud_client._request("GET", "/v1/health", require_auth=False)
    assert "x-jobagent-agent-host" not in seen
    assert seen["x-jobagent-client-version"]
