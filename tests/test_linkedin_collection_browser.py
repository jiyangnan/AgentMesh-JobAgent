"""Isolated DOM checks; the fixture browser has no recruiting account."""
from types import SimpleNamespace

import pytest

from tests.test_playwright_runtime import chrome as chrome  # noqa: PLC0414
from jobagent.drivers.playwright_runtime import PlaywrightRuntime
from jobagent.platforms.discovery import CollectionError
from jobagent.platforms.linkedin.collect import LinkedInCollectionReader


@pytest.mark.parametrize("lang,location,label,unit,result_text,km", [
    ("en", "Location Tokyo", "Within 80 km", "Kilometers", "Show results", 25),
    ("zh", "地点 东京", "80 公里内", "公里", "显示结果", 25),
    ("ja", "場所: 東京", "80km以内", "キロメートル", "結果を表示", 25),
    ("ko", "지역 도쿄", "80km 이내", "킬로미터", "결과 표시", 25),
    ("en", "Location San Francisco", "Within 80 miles", "Miles", "Show results", 24.14016),
])
def test_explicit_native_distance_is_committed_and_read_back(chrome, lang, location, label, unit, result_text, km):
    from jobagent.platforms.linkedin.collect import _UI
    with PlaywrightRuntime(chrome) as runtime:
        runtime.context.route("**/*", lambda route: route.abort())
        page = runtime.context.pages[0]
        page.set_content(f'''<button onclick="panel.hidden=!panel.hidden">{location}</button>
          <div id="panel" hidden><input componentkey="LocationTypeahead_TextEditor">
          <button>{label}</button><label id="unit">{unit}</label>
          <input type="range" aria-labelledby="unit" min="0" max="100" step="5" value="80">
          <button onclick="panel.hidden=true">{result_text}</button></div>
          <main componentkey="SearchResultsMainContent"><div role="button" componentkey="job-card-component-ref-123">Job</div></main>''')
        reader = LinkedInCollectionReader(page=page, manager=object())
        result = reader._radius({"radius_km": km}, _UI[lang])
        assert result["readback_verified"] is True and result["radius_km"] == km
        assert result["value"] == (15 if unit == "Miles" else 25)
        assert page.get_by_role("slider").is_hidden()


def test_old_cards_must_change_and_settle_before_collection(chrome):
    with PlaywrightRuntime(chrome) as runtime:
        runtime.context.route("https://www.linkedin.com/**", lambda route: route.fulfill(body="<html></html>"))
        page = runtime.context.pages[0]
        page.goto("https://www.linkedin.com/jobs/search-results/?currentJobId=456")
        page.set_content('''<main componentkey="SearchResultsMainContent">
          <div role="button" componentkey="job-card-component-ref-123">Old job</div></main>''')
        page.evaluate('''() => setTimeout(() => {
          document.querySelector('[role="button"]').setAttribute('componentkey','job-card-component-ref-456');
          document.querySelector('[role="button"]').textContent = 'Current job';
        }, 300)''')
        reader = LinkedInCollectionReader(page=page, manager=object())
        reader._settle(["123"])
        assert reader._card_ids() == ["456"]


def test_same_cards_after_scope_change_require_one_fresh_document(chrome):
    with PlaywrightRuntime(chrome) as runtime:
        loads = []

        def document(route):
            loads.append(route.request.url)
            route.fulfill(body='''<main componentkey="SearchResultsMainContent">
              <div role="button" componentkey="job-card-component-ref-123">Fresh scope</div></main>''',
                          content_type="text/html")

        runtime.context.route("https://www.linkedin.com/**", document)
        page = runtime.context.pages[0]
        scope = "https://www.linkedin.com/jobs/search-results/?geoId=102277331"
        page.goto(scope)
        page.locator('[role="button"]').evaluate("e => e.textContent = 'Stale scope'")
        reader = LinkedInCollectionReader(page=page, manager=object())
        reader._settle(["123"])
        assert loads == [scope, scope]
        assert reader._card_ids() == ["123"]
        assert page.get_by_text("Fresh scope", exact=True).is_visible()


@pytest.mark.parametrize("lang,about,more,less", [("en", "About the job", "more", "less"),
    ("zh-CN", "关于职位", "更多", "收起"), ("ja", "この求人について", "その他", "表示を減らす"),
    ("ko", "채용공고 정보", "더보기", "간략히 보기")])
def test_only_description_expands_and_auxiliary_tab_is_closed(chrome, lang, about, more, less):
    with PlaywrightRuntime(chrome) as runtime:
        runtime.context.route("https://www.linkedin.com/**", lambda route: route.fulfill(body=f'''<html lang="{lang}">
          <main><div>Example Ltd</div><div>Engineer</div><div>Tokyo · 1 day ago</div><div>Remote</div>
          <button onclick="window.applicationClicks=(window.applicationClicks||0)+1">Easy Apply</button>
          <div><h2>{about}</h2></div><div id="jd">Short description
          <button data-testid="expandable-text-button" aria-hidden="true" onclick="document.getElementById('jd').textContent='Full original description. SQL required. {less}'">… {more}</button></div>
          <aside>Different company Easy Apply</aside></main></html>''', content_type="text/html; charset=utf-8"))
        original = runtime.context.pages[0]
        before = len(runtime.context.pages)
        reader = LinkedInCollectionReader(page=original, manager=SimpleNamespace())
        job = reader.read_job("123")
        assert job.raw_data["jobDesc"] == "Full original description. SQL required."
        assert job.raw_data["linkedin"]["jd_complete"] is True
        assert job.name == "Engineer" and job.company == "Example Ltd"
        assert len(runtime.context.pages) == before + 1
        assert reader._detail_page.evaluate("window.applicationClicks || 0") == 0
        reader.disconnect()
        assert len(runtime.context.pages) == before and not original.is_closed()
        with pytest.raises(CollectionError):
            reader.read_job("123/other")
