from jobagent.platforms.message_contract import validate_personalized_message


def test_conversational_platform_messages_are_required_and_bounded():
    assert validate_personalized_message("boss", "")["error"] == "missing_signed_greeting"
    assert validate_personalized_message("liepin", "x" * 101)["error"] == "signed_greeting_too_long"
    valid = validate_personalized_message("boss", "您好，我对这个岗位很感兴趣。")
    assert valid["ok"] is True
    assert valid["length"] == len("您好，我对这个岗位很感兴趣。")
    assert len(valid["sha256"]) == 64


def test_resume_only_platforms_reject_personalized_message_expectations():
    for platform in ("zhilian", "51job"):
        result = validate_personalized_message(platform, "您好")
        assert result["ok"] is False
        assert result["error"] == "personalized_message_unsupported"
