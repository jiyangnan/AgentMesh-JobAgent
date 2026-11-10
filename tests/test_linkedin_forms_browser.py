"""Native form preparation is exact-page, sourced, and cannot submit."""
import pytest

from jobagent.drivers.playwright_runtime import PlaywrightRuntime
from jobagent.platforms.discovery import CollectionError
from jobagent.platforms.linkedin.forms import ApplicationForm
from tests.test_playwright_runtime import (
    chrome as chrome,  # noqa: PLC0414 - Register the shared pytest fixture.
)


def form_page(runtime, body, *, lang="en", job="123"):
    runtime.context.route("https://www.linkedin.com/**", lambda route: route.fulfill(
        body=f'<html lang="{lang}"><main><button onclick="window.submits++">Submit application</button>'
             f'<dialog open id="form">{body}</dialog><dialog hidden><label>Hidden<input required></label></dialog></main></html>',
        content_type="text/html; charset=utf-8"))
    p = runtime.context.pages[0]
    p.goto(f"https://www.linkedin.com/jobs/view/{job}/")
    p.evaluate("window.submits=0")
    return p, ApplicationForm(p, job)


def sourced(value, kind="user_confirmed"):
    return {"value": value, "source": {"kind": kind, "reference": "synthetic-user-answer-1"}}


def test_duplicate_yes_no_options_are_bound_to_their_question_and_snapshot(chrome):
    with PlaywrightRuntime(chrome) as runtime:
        p, form = form_page(runtime, '''<p>Right to work in Japan?*</p><fieldset>
          <label><input type="radio" name="permit">Yes</label><label><input type="radio" name="permit">No</label></fieldset>
          <p>Require sponsorship?*</p><fieldset><label><input type="radio" name="sponsor">Yes</label>
          <label><input type="radio" name="sponsor">No</label></fieldset><button>Next</button>''')
        snapshot = form.observe()
        assert not snapshot["unsupported"]
        assert [f["question"] for f in snapshot["fields"]] == ["Right to work in Japan?*", "Require sponsorship?*"]
        with pytest.raises(CollectionError, match="Resolve missing"):
            form.advance(snapshot)
        answers = {snapshot["fields"][0]["field_id"]: sourced("No"), snapshot["fields"][1]["field_id"]: sourced("Yes")}
        result = form.fill(snapshot, answers)
        assert [f["value"] for f in result["snapshot"]["fields"]] == [["No"], ["Yes"]]
        assert p.evaluate("window.submits") == 0


def test_changed_page_or_unknown_answer_is_rejected_before_any_draft_write(chrome):
    with PlaywrightRuntime(chrome) as runtime:
        p, form = form_page(runtime, '<label>Email<input type="email" value="synthetic@example.test"></label>'
            '<label>Country<select><option value=""></option><option value="JP" selected>Japan</option></select></label><button>Next</button>')
        snapshot = form.observe()
        assert not snapshot["unsupported"]
        key = snapshot["fields"][0]["field_id"]
        with pytest.raises(CollectionError) as exc:
            form.fill(snapshot, {key: sourced("new@example.test"), "other-field": sourced("invented")})
        assert exc.value.code == "linkedin_answer_scope_invalid"
        assert p.get_by_label("Email").input_value() == "synthetic@example.test"
        p.get_by_label("Email").fill("edited@example.test")
        with pytest.raises(CollectionError) as exc:
            form.fill(snapshot, {key: sourced("new@example.test")})
        assert exc.value.code == "linkedin_application_changed"
        assert p.get_by_label("Email").input_value() == "edited@example.test"


def test_native_empty_radio_labels_use_only_their_own_visible_option(chrome):
    with PlaywrightRuntime(chrome) as runtime:
        bodies = []
        for question, name in [("Right to work?", "permit"), ("Require sponsorship?", "sponsor")]:
            options = "".join(
                f'<div><div><input id="{name}-{value}" aria-label="{question}" '
                f'type="radio" name="{name}" required><label for="{name}-{value}"></label></div>'
                f'<div><p>{value}</p></div></div>' for value in ("Yes", "No"))
            bodies.append(f'<p>{question}*</p><fieldset role="radiogroup">{options}</fieldset>')
        p, form = form_page(runtime, "".join(bodies) + "<button>Review</button>")
        snapshot = form.observe()
        assert snapshot["unsupported"] == []
        assert all([o["value"] for o in f["options"]] == ["Yes", "No"] for f in snapshot["fields"])
        assert all(f["invalid"] and f["required"] for f in snapshot["fields"])
        after = form.fill(snapshot, {f["field_id"]: sourced(v)
            for f, v in zip(snapshot["fields"], ["No", "Yes"], strict=True)})["snapshot"]
        assert [f["value"] for f in after["fields"]] == [["No"], ["Yes"]]
        assert p.evaluate("window.submits") == 0
        # Shared text around multiple radios never supplies an option identity.
        p, form = form_page(runtime, '<p>Another question?*</p><fieldset>'
            '<input type="radio" aria-label="Same" name="x"><input type="radio" aria-label="Same" name="x">'
            '<p>Yes No</p></fieldset><button>Review</button>')
        assert "ambiguous_options" in form.observe()["unsupported"]


def test_partial_invalid_sources_do_not_modify_earlier_fields_or_accept_consent(chrome):
    with PlaywrightRuntime(chrome) as runtime:
        p, form = form_page(runtime, '<label>Name<input value="Synthetic"></label>'
            '<label>I agree to the policy<input type="checkbox" required></label><button>Next</button>')
        snapshot = form.observe()
        with pytest.raises(CollectionError) as exc:
            form.fill(snapshot, {snapshot["fields"][0]["field_id"]: sourced("Changed"),
                                 snapshot["fields"][1]["field_id"]: sourced(True, "resume_fact")})
        assert exc.value.code == "linkedin_explicit_choice_required"
        assert p.get_by_label("Name", exact=True).input_value() == "Synthetic"
        assert not p.get_by_role("checkbox").is_checked()


def test_conditional_form_change_stops_before_touching_later_stale_fields(chrome):
    with PlaywrightRuntime(chrome) as runtime:
        p, form = form_page(runtime, '''<label>Country<select onchange="document.getElementById('form').insertAdjacentHTML('beforeend','<label>State<input required></label>')">
          <option value="JP">Japan</option><option value="US">United States</option></select></label>
          <label>Summary<input value="Original"></label><button>Next</button>''')
        snapshot = form.observe()
        result = form.fill(snapshot, {snapshot["fields"][0]["field_id"]: sourced("US"),
                                      snapshot["fields"][1]["field_id"]: sourced("Do not write this yet")})
        assert result["requires_replan"]
        assert p.get_by_label("Summary").input_value() == "Original"
        assert result["snapshot"]["fields"][-1]["question"] == "State"


def test_resume_names_are_choices_not_hashes_and_require_explicit_selection(chrome):
    with PlaywrightRuntime(chrome) as runtime:
        p, form = form_page(runtime, '''<fieldset><label><input type="radio" name="resume" aria-label="Resume_EN.pdf" checked>Resume_EN.pdf</label>
          <label><input type="radio" name="resume" aria-label="Resume_JA.pdf">Resume_JA.pdf</label></fieldset>
          <input type="file" hidden><button>Next</button>''')
        snapshot = form.observe()
        field = snapshot["fields"][0]
        assert field["type"] == "attachment" and field["value"] == ["Resume_EN.pdf"]
        assert not snapshot["unsupported"]
        with pytest.raises(CollectionError) as exc:
            form.fill(snapshot, {field["field_id"]: sourced("Resume_JA.pdf", "resume_fact")})
        assert exc.value.code == "linkedin_explicit_choice_required"
        after = form.fill(snapshot, {field["field_id"]: sourced("Resume_JA.pdf")})["snapshot"]
        assert after["fields"][0]["value"] == ["Resume_JA.pdf"]
        assert p.locator('input[type=file]').input_value() == ""


def test_visible_attachment_labels_control_hidden_native_radios(chrome):
    with PlaywrightRuntime(chrome) as runtime:
        p, form = form_page(runtime, '''<fieldset><input style="display:none" id="en" type="radio" name="resume" checked>
          <label for="en">Resume_EN.pdf</label><input style="display:none" id="ja" type="radio" name="resume">
          <label for="ja">Resume_JA.pdf</label></fieldset><button>Next</button>''')
        snapshot = form.observe()
        assert not snapshot["unsupported"]
        field = snapshot["fields"][0]
        assert field["type"] == "attachment"
        result = form.fill(snapshot, {field["field_id"]: sourced("Resume_JA.pdf")})
        assert result["snapshot"]["fields"][0]["value"] == ["Resume_JA.pdf"]
        assert p.evaluate("window.submits") == 0


def test_conditional_value_change_replans_even_when_fields_are_unchanged(chrome):
    with PlaywrightRuntime(chrome) as runtime:
        p, form = form_page(runtime, '''<label>Country<select onchange="document.getElementById('city').value=''">
          <option value="JP">Japan</option><option value="US">United States</option></select></label>
          <label>City<input id="city" value="Tokyo"></label><button>Next</button>''')
        snapshot = form.observe()
        result = form.fill(snapshot, {snapshot["fields"][0]["field_id"]: sourced("US"),
                                      snapshot["fields"][1]["field_id"]: sourced("Stale answer")})
        assert result["requires_replan"]
        assert p.get_by_label("City", exact=True).input_value() == ""


def test_native_values_and_unknown_custom_fields_are_not_guessed(chrome):
    with PlaywrightRuntime(chrome) as runtime:
        p, form = form_page(runtime, '<label>Start date<input type="date"></label>'
            '<div role="listbox">Custom skill choices</div><button>Next</button>')
        snapshot = form.observe()
        assert set(snapshot["unsupported"]) >= {"field_type:date", "custom_input"}
        with pytest.raises(CollectionError):
            form.advance(snapshot)
        p.goto("https://www.linkedin.com/jobs/view/456/")
        with pytest.raises(CollectionError) as exc:
            form.observe()
        assert exc.value.code == "linkedin_application_identity_changed"


@pytest.mark.parametrize("lang,next_text,review,submit", [
    ("en", "Next", "Review", "Submit application"), ("zh", "下一步", "查看", "提交申请"),
    ("ja", "次へ", "確認", "応募を送信"), ("ko", "다음", "검토", "신청서 전송")])
def test_preparation_reaches_review_without_ever_clicking_submit(chrome, lang, next_text, review, submit):
    with PlaywrightRuntime(chrome) as runtime:
        p, form = form_page(runtime, f'''<label>First name<input value="Synthetic" required></label>
          <button onclick="nextPage()">{next_text}</button><script>
          function nextPage() {{
            const f=document.getElementById('form');
            f.innerHTML='<label>Optional question<input></label><button>{review}</button>';
            f.querySelector('button').onclick=()=>{{
              f.innerHTML='<p>Complete application preview</p><button>{submit}</button>';
              f.querySelector('button').onclick=()=>window.submits++;
            }};
          }}</script>''', lang=lang)
        assert form.advance(form.observe())["advanced"]
        assert form.observe()["action"] == "review"
        assert form.advance(form.observe())["advanced"]
        snapshot = form.observe()
        assert snapshot["action"] == "submit"
        with pytest.raises(CollectionError) as exc:
            form.advance(snapshot)
        assert exc.value.code == "linkedin_application_submit_forbidden"
        assert p.evaluate("window.submits") == 0


def test_review_follow_choice_is_explicit_and_invalidates_old_snapshot(chrome):
    with PlaywrightRuntime(chrome) as runtime:
        p, form = form_page(runtime, '<label>Follow company<input type="checkbox" checked></label>'
            '<button onclick="window.submits++">Submit application</button>')
        old = form.observe()
        after = form.fill(old, {old["fields"][0]["field_id"]: sourced(False)})["snapshot"]
        assert after["snapshot_digest"] != old["snapshot_digest"]
        assert after["fields"][0]["value"] is False
        assert p.evaluate("window.submits") == 0
