"""City headings must not stand in for the active Liepin search filter."""
import json
import shutil
import subprocess

import pytest

from jobagent.platforms.liepin.city_resolver import LiepinCityResolver
from jobagent.platforms.liepin.collect import LiepinReadOnlyCollector
from jobagent.platforms.liepin.selectors import build_liepin_snapshot_script


QUERY = "数据产品经理"


def extract(script, *, city="深圳", selected="深圳", code="050090", query=QUERY):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js unavailable for offline DOM-script test")
    body = f"城市\n全国\n深圳\n已选条件：\n{selected}\n清空筛选条件\n订阅\n「{query}·{city}」\n暂无相关职位"
    url = f"https://www.liepin.com/city-sz/zhaopin/?key={QUERY}"
    if code:
        url += f"&city={code}&dq={code}"
    harness = r"""
      const key = {value: QUERY, getAttribute: () => null,
        getBoundingClientRect: () => ({width: 100, height: 30})};
      global.location = {href: URL_VALUE, origin: 'https://www.liepin.com'};
      global.document = {title: '【深圳招聘信息_深圳人才网招聘信息】-猎聘',
        body: {innerText: BODY},
        querySelector: () => null,
        querySelectorAll: selector => selector.includes('input[name="key"]') ? [key] : []};
      global.window = {getComputedStyle: () => ({display: 'block', visibility: 'visible', opacity: 1})};
    """
    for marker, value in {"QUERY": QUERY, "URL_VALUE": url, "BODY": body}.items():
        harness = harness.replace(marker, json.dumps(value, ensure_ascii=False))
    output = subprocess.run([node, "-e", harness + "console.log(" + script + ");"],
                            text=True, capture_output=True, check=True)
    result = json.loads(output.stdout)
    return result.get("cityEvidence", result)


def evidence_script():
    class Recorder:
        def _exec_js(self, script):
            self.script = script
            return {}
    driver = Recorder()
    LiepinReadOnlyCollector(driver=driver)._extract_city_search_evidence()
    return driver.script


@pytest.mark.parametrize("builder", [build_liepin_snapshot_script, evidence_script])
def test_modern_city_filter_is_verified_without_legacy_hidden_input(tmp_path, builder):
    evidence = extract(builder())
    resolver = LiepinCityResolver(tmp_path / "cities.json")
    result = resolver.verify_evidence(evidence, city="深圳", query=QUERY, expected_code="050090")
    assert result["verified"] is True
    assert result["code"] == "050090"
    assert resolver.remember("深圳", "050090", result)


@pytest.mark.parametrize("builder", [build_liepin_snapshot_script, evidence_script])
@pytest.mark.parametrize("city,selected,code,query", [
    ("全国", "", "410", QUERY),
    ("全国", "", "", QUERY),
    ("深圳", "北京", "050090", QUERY),
    ("深圳", "深圳", "050090", "其他岗位"),
])
def test_city_title_cannot_override_actual_filter_or_query(tmp_path, builder, city, selected, code, query):
    evidence = extract(builder(), city=city, selected=selected, code=code, query=query)
    resolver = LiepinCityResolver(tmp_path / "cities.json")
    assert not resolver.verify_evidence(evidence, city="深圳", query=QUERY, expected_code="050090")["verified"]
    assert not resolver.verify_route_evidence(evidence, city="深圳", query=QUERY,
        expected_route="https://www.liepin.com/city-sz/",
        previous_url="https://www.liepin.com/zhaopin/")["verified"]
    assert not resolver.cache_path.exists()


def test_city_route_and_metadata_alone_cannot_verify_search_scope(tmp_path):
    evidence = {"url": "https://www.liepin.com/city-sz/zhaopin/?key=数据产品经理",
        "metaCity": "深圳", "titleCity": "深圳", "inputQuery": QUERY, "urlQuery": QUERY,
        "resultSurface": True, "jobCardCount": 30}
    result = LiepinCityResolver(tmp_path / "cities.json").verify_route_evidence(
        evidence, city="深圳", query=QUERY, expected_route="https://www.liepin.com/city-sz/",
        previous_url="https://www.liepin.com/zhaopin/")
    assert not result["verified"]
