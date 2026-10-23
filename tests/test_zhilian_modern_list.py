"""Synthetic split-view fixtures; no live jobs, accounts or network."""
import json

import pytest
from tests.test_playwright_runtime import chrome  # noqa: F401

from jobagent.drivers.boss.cdp_client import CDPClient
from jobagent.infra.platform_tabs import list_targets
from jobagent.platforms.zhilian.modern import MODERN_LIST_JS
from jobagent.platforms.zhilian.selectors import build_zhilian_snapshot_script
from jobagent.platforms.zhilian.collect import ZhilianReadOnlyCollector, _snapshot_failure


@pytest.fixture
def page(chrome):
    client = CDPClient()
    target = list_targets(chrome["port"])[0]
    client.connect(target["webSocketDebuggerUrl"])
    try:
        yield client
    finally:
        client.disconnect()


def evaluate(page, source):
    value = page.evaluate(source)["result"]["value"]
    return json.loads(value) if isinstance(value, str) else value


def install(page, count=20):
    evaluate(page, r"""(() => {
      document.body.innerHTML='<div class="job-list-panel"></div>';
      const panel=document.querySelector('.job-list-panel');
      panel.__vue__={$props:{hasMore:true,loading:false,loadingMore:false}};
      window.addFixtureCard = i => {
        const div=document.createElement('div');
        div.className='job-card'; div.style='height:100px;width:600px';
        div.innerHTML='<div class="job-card__title-clamp"><span class="vue-clamp__text" aria-label="数据产品经理'+i+'">数据产品经理'+i+'</span></div>'
          +'<a class="job-card__company-name">示例公司</a>'
          +'<span class="job-card__salary">20-30K</span><span class="job-card__location">深圳 南山</span>';
        div.__vue__={$props:{job:{number:'CC123J'+i,name:'数据产品经理'+i,
          companyName:'示例公司有限公司',workCity:'深圳',salary60:'20-30K',
          positionUrl:'https://www.zhaopin.com/jobdetail/CC123J'+i+'.htm',
          jobDescription:'这个字段只有摘要，不可用作完整JD',
          jobDetailData:{position:{base:{positionNumber:'CC123J'+i,positionName:'数据产品经理'+i},
            desc:{description:'岗位职责：负责数据产品规划及需求分析。'.repeat(12)+'末尾硬性要求：具备数据治理经验。'}}}}}};
        panel.appendChild(div);
      };
      for(let i=0;i<COUNT;i++) addFixtureCard(i);
      return true;
    })()""".replace("COUNT", str(count)))


def test_all_rendered_cards_and_full_jd_not_teaser(page):
    install(page)
    data = evaluate(page, build_zhilian_snapshot_script(30))
    assert len(data["cards"]) == data["renderedCardCount"] == 20
    assert data["hasNextPage"] is True and data["paginationMode"] == "infinite_list"
    assert len({j["positionId"] for j in data["cards"]}) == 20
    assert all(j["jobDesc"].endswith("末尾硬性要求：具备数据治理经验。") for j in data["cards"])
    assert all("摘要" not in j["jobDesc"] for j in data["cards"])


@pytest.mark.parametrize("change", [
    "document.querySelector('.job-card').__vue__=null",
    "document.querySelector('.vue-clamp__text').setAttribute('aria-label','其他岗位')",
    "document.querySelector('.job-card').__vue__.$props.job.positionUrl='https://example.test/jobdetail/CC123J0.htm'",
])
def test_partial_recognition_fails_before_cloud_matching(page, change):
    install(page)
    evaluate(page, "(() => {" + change + "; return true})()")
    snapshot = evaluate(page, build_zhilian_snapshot_script(30))
    assert snapshot["invalidCardCount"] == 1
    assert _snapshot_failure(snapshot) == "zhilian_card_coverage_incomplete"


def test_different_job_detail_never_attaches_to_candidate(page):
    install(page, 1)
    evaluate(page, "(() => {document.querySelector('.job-card').__vue__.$props.job.jobDetailData.position.base.positionNumber='other';return true})()")
    data = evaluate(page, MODERN_LIST_JS + "JSON.stringify(zhilianModernList())")
    assert "jobDesc" not in data["cards"][0]


def test_infinite_list_scroll_advances_once_without_navigation(page):
    install(page)
    evaluate(page, """(() => {
      window.scrollCount=0;
      window.addEventListener('scroll', () => {
        if(window.scrollCount++) return;
        for(let i=20;i<40;i++) addFixtureCard(i);
      });
      return true;
    })()""")
    class Driver:
        def _exec_js(self, source):
            return evaluate(page, source)
    collector = ZhilianReadOnlyCollector(driver=Driver())
    result = collector._select_page(2, wait_seconds=3)
    assert result["alreadySelected"] and result["loadedCount"] == 40
    snapshot = evaluate(page, build_zhilian_snapshot_script(30))
    assert len(snapshot["cards"]) == 30 and snapshot["renderedCardCount"] == 40
