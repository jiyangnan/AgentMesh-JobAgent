"""Read signed search scopes through the existing dedicated browser runtime.

Only search, pagination and description expansion are exposed. This reader
has no application, consent, follow, upload or submission operation.
"""
from __future__ import annotations

import json
import math
import re
import time
from datetime import UTC, datetime
from hashlib import sha256
from urllib.parse import parse_qs, urlsplit

from jobagent.domain.models import Job
from jobagent.infra.protocol import digest_payload
from jobagent.platforms.discovery import CollectedPage, CollectionError

SEARCH = '[componentkey="SearchResultsMainContent"]'
CARDS = SEARCH + ' [role="button"][componentkey^="job-card-component-ref-"]'
LOCATION_INPUT = '[componentkey="LocationTypeahead_TextEditor"]'
_UI = {
    "zh": {"location": r"^地点 ", "easy": r"快速申请", "about": "关于职位", "more": "更多", "less": "收起",
           "radius": r"^\d+ (?:公里|英里)内$", "show_results": "显示结果"},
    "en": {"location": r"^Location(?: |$)", "easy": r"Easy Apply", "about": "About the job", "more": "more", "less": "less",
           "radius": r"^Within \d+ (?:km|mi|miles)$", "show_results": "Show results"},
    "ja": {"location": r"^場所: ", "easy": r"Easy応募", "about": "この求人について", "more": "その他", "less": "表示を減らす",
           "radius": r"^\d+(?:km|マイル)以内$", "show_results": "結果を表示"},
    "ko": {"location": r"^지역 ", "easy": r"원클릭 지원", "about": "채용공고 정보", "more": "더보기", "less": "간략히 보기",
           "radius": r"^\d+(?:km|마일) 이내$", "show_results": "결과 표시"},
}
_LOCATION_OPTIONS = """() => {
  const field = document.querySelector('[componentkey="LocationTypeahead_TextEditor"]');
  for (let root = field?.parentElement; root && root !== document.body; root = root.parentElement) {
    if (root.querySelector('[componentkey="SearchResultsMainContent"]')) return [];
    const links = [...root.querySelectorAll('a')].filter(e => e.getClientRects().length &&
      e.href.includes('JOB_SEARCH_PAGE_LOCATION_'));
    if (links.length) return links.map(e => ({label:e.innerText.trim(), url:e.href}));
  }
  return [];
}"""


def fail(code, message, **details):
    raise CollectionError(code, message, details={"request_preserved": True, "retryable": False, **details})


def detail_id(url):
    try:
        p = urlsplit(url)
        match = re.fullmatch(r"/jobs/view/([0-9]+)/?", p.path)
        if p.scheme == "https" and p.netloc == "www.linkedin.com" and match and not p.fragment:
            return match[1]
    except (TypeError, ValueError):
        pass
    return None


def location_options(rows, keyword):
    result = []
    for row in rows:
        p = urlsplit(row["url"])
        params = parse_qs(p.query)
        geo = params.get("geoId", [])
        if (p.scheme == "https" and p.netloc == "www.linkedin.com" and p.path == "/jobs/search-results/"
                and params.get("origin") == ["JOB_SEARCH_PAGE_LOCATION_AUTOCOMPLETE"]
                and params.get("keywords") == [keyword] and len(geo) == 1 and geo[0].isdigit()
                and not params.get("currentJobId") and row["label"].strip()):
            item = {"label": row["label"].strip(), "geo_id": geo[0], "url": row["url"]}
            if item not in result:
                result.append(item)
    return result


def choose_location(options, target, country_label=None, resolution=None):
    if resolution is not None:
        matches = [item for item in options if item["geo_id"] == resolution.get("geo_id")
                   and item["label"] == resolution.get("label")]
        if resolution.get("target_digest") != digest_payload(target):
            fail("linkedin_location_context_changed", "The confirmed native location belongs to another target")
        if len(matches) == 1:
            return matches[0]
        fail("linkedin_location_selection_required", "The previously selected location is no longer a current native option",
             requires_user_action=True, target=target, location_options=options)
    names = {target["label"].strip().casefold()}
    if target["kind"] == "country" and country_label:
        names.add(country_label.casefold())
    matches = [item for item in options if item["label"].casefold() in names]
    if len(matches) != 1:
        fail("linkedin_location_selection_required", "The native location choices do not identify one exact requested scope",
             requires_user_action=True, target=target, location_options=options)
    return matches[0]


def same_native_location(left, right):
    # Native menus and selected controls reverse country/locality order in
    # Chinese. Compare their complete labels, never just a city substring.
    return sorted(re.split(r"[,，\s]+", left.strip().casefold())) == sorted(re.split(r"[,，\s]+", right.strip().casefold()))


def radius_steps(radius_km, native):
    """Use the rendered unit and supported increments, never the URL's miles."""
    factors = {"公里": 1, "Kilometers": 1, "km": 1, "キロメートル": 1, "킬로미터": 1,
               "Miles": 1.609344, "英里": 1.609344, "マイル": 1.609344, "마일": 1.609344}
    try:
        factor = factors[native["unit"]]
        low, high, step = (float(native[key]) for key in ("min", "max", "step"))
        desired = radius_km / factor
        increments = (desired - low) / step
        if (not all(math.isfinite(v) for v in (low, high, step, increments)) or step <= 0
                or not low <= desired <= high or not 0 <= increments <= 100
                or abs(increments - round(increments)) > 1e-6):
            raise ValueError("unsupported increment")
        return round(increments), desired
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        fail("linkedin_radius_selection_required", "The requested radius is not an exact supported native distance",
             requires_user_action=True, requested_radius_km=radius_km, native_range=native,
             next_suggested="jobagent round status")


def _salary(header, jd):
    lines = [line.strip() for line in (header + "\n" + jd).splitlines() if line.strip()]
    money = re.compile(r"[$€£¥￥₩]\s*\d|\b(?:USD|CAD|AUD|HKD|SGD|CNY|JPY|KRW|EUR|GBP)\b|\d[\d,.]*\s*(?:万|萬|億|억)")
    pay = re.compile(r"salary|compensation|base pay|pay range|/(?:yr|year|mo|month|hr|hour)\b|薪资|薪酬|年薪|月薪|年収|給与|연봉|급여", re.IGNORECASE)
    monetary = [i for i, line in enumerate(lines) if money.search(line)]
    disclosed = [lines[i] for i in monetary if any(pay.search(line) for line in lines[max(0, i - 2):i + 1])]
    if disclosed:
        return "\n".join(disclosed), "disclosed"
    return "\n".join(lines[i] for i in monetary), "ambiguous" if monetary else "not_disclosed"


def parse_detail(observation):
    identifier = detail_id(observation["url"])
    if identifier != observation["requested_id"]:
        fail("linkedin_detail_identity_mismatch", "The opened detail does not identify the requested job")
    jd = observation["jd"].strip()
    if not observation.get("expanded") or not jd or len(jd) > 20000:
        fail("linkedin_full_description_required", "A complete supported-size job description is required")
    header = observation["header"]
    for boundary in ("人脉推荐", "了解招聘团队", "People you can reach out to", "Meet the hiring team",
                     "リーチ可能なメンバー", "채용 담당자 만나보기", "연락할 수 있는 사람"):
        header = header.split(boundary, 1)[0]
    lines = [line.strip() for line in header.splitlines() if line.strip()]
    if len(lines) < 3:
        fail("linkedin_job_identity_incomplete", "The current job header is incomplete")
    closed = any(term in header for term in ("已停止接受", "不再接受", "No longer accepting applications"))
    applied = any(line in {"申请已提交", "已申请", "Applied", "Application submitted", "Your application was sent",
                          "応募が送信されました", "지원서를 전송함"} for line in lines)
    easy = any(line in {"快速申请", "Easy Apply", "Easy応募", "원클릭 지원"} for line in lines)
    external = any(line in {"申请", "Apply"} for line in lines)
    modes = {"现场办公": "onsite", "混合办公": "hybrid", "远程办公": "remote",
             "On-site": "onsite", "Hybrid": "hybrid", "Remote": "remote",
             "オフィス勤務": "onsite", "ハイブリッド": "hybrid", "リモート": "remote",
             "대면근무": "onsite", "재택대면혼합근무": "hybrid", "재택근무": "remote"}
    observed_modes = {modes[line] for line in lines if line in modes}
    salary, disclosure = _salary(header, jd)
    snapshot = {"version": 1, "observed_at": observation["observed_at"], "evidence_scope": "current_job_detail",
        "application_type": "easy_apply" if easy else "external" if external else "unknown",
        "availability": "closed" if closed else "already_applied" if applied else "open" if easy or external else "unknown",
        "jd_complete": True, "jd_sha256": sha256(jd.encode()).hexdigest(),
        "workplace_type": next(iter(observed_modes)) if len(observed_modes) == 1 else "unknown",
        "salary_disclosure": disclosure}
    return Job(name=lines[1], company=lines[0], city=lines[2].split("·", 1)[0].strip(), salary=salary,
        url=f"https://www.linkedin.com/jobs/view/{identifier}/", platform="linkedin",
        raw_data={"id": identifier, "jobDesc": jd, "linkedin": snapshot})


class LinkedInCollectionReader:
    bulk_readonly = True
    platform = "linkedin"

    def __init__(self, *, page=None, manager=None):
        from jobagent.drivers.boss.chrome_manager import ChromeInstanceManager
        self.manager = manager or ChromeInstanceManager()
        self._runtime = None
        self._detail_page = None
        self.last_search_observation = None
        if page is None:
            from jobagent.drivers.playwright_runtime import PlaywrightRuntime
            from jobagent.infra.platform_tabs import ensure_platform_tab
            self.manager.ensure_running()
            self._runtime = PlaywrightRuntime({"id": "linkedin-collection", "port": self.manager.port,
                "profile_directory": str(self.manager.user_data_dir)})
            runtime = self._runtime.__enter__()
            try:
                target = ensure_platform_tab(platform="linkedin", port=self.manager.port)
                page = next(p for p in runtime.context.pages if runtime.target_id(p) == (target.get("id") or target.get("targetId")))
            except Exception:
                self.disconnect()
                raise
        self.page = page

    def disconnect(self):
        if self._detail_page is not None:
            self._detail_page.close()
            self._detail_page = None
        if self._runtime is not None:
            self._runtime.__exit__(None, None, None)
            self._runtime = None

    def _ui(self):
        lang = (self.page.locator("html").get_attribute("lang") or "").split("-")[0]
        if lang not in _UI:
            fail("linkedin_ui_language_unverified", "This LinkedIn interface language has not been verified", language=lang)
        return _UI[lang], lang

    def check_login_status(self):
        p = urlsplit(self.page.url)
        return (p.scheme == "https" and p.netloc == "www.linkedin.com" and p.path.startswith("/jobs/")
                and self.page.get_by_role("button", name=re.compile(r"本人|\(you\)|\bMe\b|^我$|さんのプロフィール$|님\(나\)$")).count() > 0)

    def _settle(self, previous_ids=None):
        # LinkedIn keeps background requests active after rendering. Require
        # native results, current-job alignment and a stable list instead.
        # A changed filter/location must replace the old result set or be read
        # from a fresh document. Different scopes can legitimately share cards.
        self.page.locator(SEARCH).wait_for(state="visible", timeout=15000)
        stable, since, deadline = None, time.monotonic(), time.monotonic() + 15
        while time.monotonic() < deadline:
            state = self.page.evaluate("""arg => {
          const root = document.querySelector(arg.search);
          const ids = [...new Set([...document.querySelectorAll(arg.cards)].map(e =>
            e.getAttribute('componentkey').replace('job-card-component-ref-', '')))];
          const selected = new URL(location.href).searchParams.get('currentJobId');
          const ready = ids.length && ids.every(id => /^\\d+$/.test(id)) &&
            (!selected || ids.includes(selected)) &&
            !root.querySelector('[aria-busy="true"], [role="progressbar"]') &&
            (!arg.previous || JSON.stringify(ids) !== JSON.stringify(arg.previous));
          return {ids, ready: Boolean(ready)};
        }""", {"search": SEARCH, "cards": CARDS, "previous": previous_ids})
            if not state["ready"] or state["ids"] != stable:
                stable, since = state["ids"], time.monotonic()
            elif time.monotonic() - since >= 0.75:
                return
            self.page.wait_for_timeout(250)
        if previous_ids and state["ids"] == previous_ids:
            url = urlsplit(self.page.url)
            if (url.scheme == "https" and url.netloc == "www.linkedin.com"
                    and url.path == "/jobs/search-results/"):
                # Do not accept stale cards merely because their URL changed.
                # One reload of the committed scope obtains a fresh native
                # result document; the caller still checks query/filter/scope.
                self.page.reload(wait_until="domcontentloaded", timeout=20000)
                self._settle()
                return
        fail("linkedin_results_not_ready", "The native result list did not settle for the requested search",
             requires_technical_recovery=True)

    def _card_ids(self):
        return self.page.locator(CARDS).evaluate_all("els => [...new Set(els.map(e => e.getAttribute('componentkey').replace('job-card-component-ref-', '')))]")

    def _search(self, query, *, location_resolution=None, radius_resolution=None):
        target = query["location"]
        if target.get("market") != "outside_mainland_china" or query.get("easy_apply_only") is not True:
            fail("linkedin_search_scope_invalid", "LinkedIn collection requires the signed outside-mainland Easy Apply scope")
        p = self.page
        p.bring_to_front()
        p.goto("https://www.linkedin.com/jobs/", wait_until="domcontentloaded", timeout=20000)
        ui, _ = self._ui()
        field = p.locator('input[componentkey="jobSearchBox"], input[componentkey="semanticSearchBox"]')
        field.wait_for(state="visible", timeout=12000)
        if not self.check_login_status():
            fail("linkedin_login_required", "Sign in using the original dedicated browser", requires_user_action=True)
        field.fill(query["keyword"])
        field.press("Enter")
        p.locator(SEARCH).wait_for(state="visible", timeout=20000)
        location_control = p.get_by_role("button", name=re.compile(ui["location"]))
        prior_location = location_control.inner_text()
        location_control.click(timeout=5000)
        location_field = p.locator(LOCATION_INPUT)
        location_field.wait_for(state="visible", timeout=8000)
        previous = p.evaluate(_LOCATION_OPTIONS)
        old_text = location_field.inner_text()
        location_field.fill(target["label"])
        p.wait_for_function("""arg => {
            const options = (OPTIONS)();
            return options.length && options.every(e => new URL(e.url).searchParams.get('origin') ===
              'JOB_SEARCH_PAGE_LOCATION_AUTOCOMPLETE') && (!arg.changed || JSON.stringify(options) !== arg.previous);
          }""".replace("OPTIONS", _LOCATION_OPTIONS),
            arg={"changed": old_text != target["label"], "previous": json.dumps(previous, ensure_ascii=False, separators=(",", ":"))}, timeout=12000)
        options = location_options(p.evaluate(_LOCATION_OPTIONS), query["keyword"])
        country = (p.evaluate("code => new Intl.DisplayNames([document.documentElement.lang], {type:'region'}).of(code)", target["country_code"])
                   if target.get("country_code") else None)
        choice = choose_location(options, target, country, location_resolution)
        old_geo = parse_qs(urlsplit(p.url).query).get("geoId")
        unchanged = old_geo == [choice["geo_id"]] or (not old_geo and same_native_location(prior_location, choice["label"]))
        previous_ids = None if unchanged else self._card_ids()
        p.get_by_role("link", name=choice["label"], exact=True).click(timeout=5000)
        location_field.wait_for(state="hidden", timeout=12000)
        control = p.get_by_role("button", name=re.compile(ui["location"]))
        from patchright.sync_api import expect
        p.wait_for_url(lambda url: parse_qs(urlsplit(url).query).get("geoId") == [choice["geo_id"]], timeout=15000)
        self._settle(previous_ids)
        radius = self._radius({**target, "radius_km": radius_resolution["radius_km"]} if radius_resolution else target, ui) if target.get("radius_km") is not None else None
        easy = p.get_by_role("checkbox", name=re.compile(ui["easy"], re.IGNORECASE))
        if easy.count() != 1:
            fail("linkedin_easy_apply_filter_ambiguous", "Exactly one native Easy Apply search filter is required")
        previous_ids = None
        if not easy.is_checked():
            previous_ids = self._card_ids()
            label = p.locator("label[for=" + json.dumps(easy.get_attribute("id")) + "]")
            if label.count() != 1:
                fail("linkedin_easy_apply_filter_ambiguous", "The native filter label is not unique")
            label.click(timeout=5000)
        expect(easy).to_be_checked(timeout=10000)
        self._settle(previous_ids)
        params = parse_qs(urlsplit(p.url).query)
        if (params.get("geoId") != [choice["geo_id"]] or params.get("keywords") != [query["keyword"]]
                or not control.inner_text().strip() or not easy.is_checked()):
            fail("linkedin_search_scope_changed", "The rendered location, query and filter no longer match the requested search")
        self.last_search_observation = {"target": target, "keyword": query["keyword"], "native_location": choice,
            "rendered_location": control.inner_text(), "easy_apply": True, "url": p.url,
            **({"native_radius": radius} if radius else {}),
            "observed_at": datetime.now(UTC).isoformat()}

    def _radius(self, target, ui):
        p = self.page
        control = p.get_by_role("button", name=re.compile(ui["location"]))
        control.click(timeout=5000)
        p.locator(LOCATION_INPUT).wait_for(state="visible", timeout=8000)
        radius = p.get_by_text(re.compile(ui["radius"]))
        if radius.count() != 1:
            fail("linkedin_radius_control_unverified", "The native distance control is not unique")
        radius.click(timeout=5000)
        slider = p.get_by_role("slider")
        slider.wait_for(state="visible", timeout=8000)
        native = slider.evaluate("""e => ({min:e.min, max:e.max, step:e.step, value:e.value,
            unit:(e.getAttribute('aria-labelledby') || '').split(/\\s+/).map(id =>
              e.getRootNode().querySelector('#'+CSS.escape(id))?.textContent.trim()).filter(Boolean).join(' ')})""")
        steps, desired = radius_steps(target["radius_km"], native)
        if float(native["value"]) != desired:
            slider.press("Home")
            for _ in range(steps):
                slider.press("ArrowRight")
            if abs(float(slider.input_value()) - desired) > 1e-6:
                fail("linkedin_radius_control_unverified", "The requested native distance was not selected")
            p.get_by_text(ui["show_results"], exact=True).click(timeout=5000)
            slider.wait_for(state="hidden", timeout=10000)
            # A radius change need not change any cards (e.g. all jobs are
            # inside both radii); verify native control readback instead.
            self._settle()
        else:
            control.click(timeout=5000)
            slider.wait_for(state="hidden", timeout=5000)
        # Reopen and read the committed native value; URL distance is rounded
        # in miles even when this member interface displays kilometres.
        control.click(timeout=5000)
        p.locator(LOCATION_INPUT).wait_for(state="visible", timeout=8000)
        p.get_by_text(re.compile(ui["radius"])).click(timeout=5000)
        slider.wait_for(state="visible", timeout=8000)
        if abs(float(slider.input_value()) - desired) > 1e-6:
            fail("linkedin_search_scope_changed", "The committed search radius changed")
        control.click(timeout=5000)
        slider.wait_for(state="hidden", timeout=5000)
        return {**native, "value": desired, "radius_km": target["radius_km"], "readback_verified": True}

    def collect_page(self, query, page, limit, *, excluded_ids=None, location_resolution=None, radius_resolution=None):
        try:
            return self._collect_page(query, page, limit, excluded_ids=excluded_ids, location_resolution=location_resolution, radius_resolution=radius_resolution)
        except CollectionError:
            raise
        except Exception as exc:  # noqa: BLE001 - Preserve any interrupted browser read without retrying it.
            fail("linkedin_browser_read_interrupted", "The browser read stopped before a verified result",
                 error_type=type(exc).__name__, requires_technical_recovery=True)

    def _collect_page(self, query, page, limit, *, excluded_ids=None, location_resolution=None, radius_resolution=None):
        self._search(query, **({"radius_resolution": radius_resolution} if radius_resolution is not None else {}), **({"location_resolution": location_resolution} if location_resolution is not None else {}))
        p = self.page
        for number in range(2, page + 1):
            next_page = p.locator(f'[data-testid="pagination-indicator-{number - 1}"]')
            if next_page.count() == 0:
                if p.locator('[data-testid="pagination-controls-next-button-hidden"]').count() == 1:
                    return CollectedPage([], exhausted=True, evidence=self.last_search_observation)
                fail("linkedin_pagination_unverified", "The requested next page is not available as an observed control")
            previous_ids = self._card_ids()
            next_page.click(timeout=5000)
            from patchright.sync_api import expect
            expect(next_page).to_have_attribute("aria-current", "true", timeout=15000)
            self._settle(previous_ids)
        ids = self._card_ids()
        if not ids or any(not identifier.isdigit() for identifier in ids):
            fail("linkedin_results_unverified", "No verified job IDs were observed; this is not proof of zero results")
        jobs = []
        for identifier in ids:
            if identifier in (excluded_ids or ()):
                continue
            job = self.read_job(identifier)
            mode = job.raw_data["linkedin"]["workplace_type"]
            if query.get("workplace_types") and mode != "unknown" and mode not in query["workplace_types"]:
                continue
            jobs.append(job)
            if len(jobs) >= limit:
                break
        return CollectedPage(jobs, exhausted=p.locator('[data-testid="pagination-controls-next-button-hidden"]').count() == 1,
                             evidence={**self.last_search_observation, "page": page, "rendered_job_ids": ids,
                                       "url": p.url, "observed_at": datetime.now(UTC).isoformat()})

    def read_job(self, identifier):
        if not isinstance(identifier, str) or not identifier.isascii() or not identifier.isdigit():
            fail("linkedin_job_id_invalid", "Only a native numeric LinkedIn job ID can be read")
        if self._detail_page is None:
            self._detail_page = self.page.context.new_page()
        p = self._detail_page
        url = f"https://www.linkedin.com/jobs/view/{identifier}/"
        p.goto(url, wait_until="domcontentloaded", timeout=20000)
        if detail_id(p.url) != identifier:
            fail("linkedin_detail_identity_mismatch", "Detail navigation redirected away from the requested job", requires_user_action=True)
        lang = (p.locator("html").get_attribute("lang") or "").split("-")[0]
        if lang not in _UI:
            fail("linkedin_ui_language_unverified", "The detail interface language is not verified", language=lang)
        ui = _UI[lang]
        heading = p.get_by_role("heading", name=ui["about"], exact=True)
        heading.wait_for(state="visible", timeout=15000)
        description = heading.locator("..").locator("xpath=following-sibling::*[1]")
        if description.count() != 1 or description.evaluate("e=>e.tagName") not in {"P", "DIV"}:
            fail("linkedin_description_structure_changed", "The current job description container is not unique")
        expansion = description.locator('[data-testid="expandable-text-button"]')
        more = expansion.filter(has_text=re.compile(r"^\s*(?:…\s*)?" + re.escape(ui["more"]) + r"\s*$"))
        if expansion.count() and more.count() != 1:
            fail("linkedin_description_structure_changed", "The visible expansion control is not verified for this language")
        if more.count() > 1:
            fail("linkedin_description_structure_changed", "The description expansion control is not unique")
        if more.count() and more.is_visible():
            more.click(timeout=5000)
            more.wait_for(state="hidden", timeout=5000)
        jd = description.inner_text().strip()
        for ending in ("… " + ui["less"], ui["less"]):
            if jd.endswith(ending):
                jd = jd[:-len(ending)].rstrip()
        return parse_detail({"requested_id": identifier, "url": p.url, "observed_at": datetime.now(UTC).isoformat(),
            "header": p.locator("main").inner_text().split(ui["about"], 1)[0], "jd": jd,
            "expanded": not jd.endswith(ui["more"])})
