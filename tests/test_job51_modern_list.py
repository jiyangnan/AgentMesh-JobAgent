"""Synthetic current 51Job cards and pager; no real account or recruiting actions."""
import json
import time
import pytest
from tests.test_zhilian_modern_list import page  # noqa: F401
from tests.test_playwright_runtime import chrome  # noqa: F401
from jobagent.platforms.job51.selectors import build_job51_snapshot_script
from jobagent.platforms.job51.collect import Job51ReadOnlyCollector, _snapshot_failure


def evaluate(page, source):
    result=page.evaluate(source)
    assert "exceptionDetails" not in result, result.get("result", {}).get("description", result)
    value=result["result"]["value"]
    return json.loads(value) if isinstance(value,str) else value


def install(page):
    page.send("Page.navigate", {"url": "data:text/html,<meta charset=utf-8><body></body>"})
    for _ in range(20):
        if page.evaluate("location.href")["result"].get("value", "").startswith("data:text/html"):
            break
        time.sleep(.05)
    evaluate(page, r"""(() => {
      document.body.innerHTML='<div id="app"><input id="keywordInput" value="数据产品经理"><section id="bound"><div class="joblist"></div></section><div class="el-pagination"><button class="btn-prev">prev</button><ul class="el-pager"><li class="number active">1</li><li class="number">2</li></ul><button class="btn-next">next</button></div></div>';
      window.moves=0;
      window.renderFixture=n=>{
        document.querySelector('.joblist').innerHTML='';
        const rows=[];
        for(let i=0;i<20;i++) {
          const id=String(1000+(n-1)*20+i),title='数据产品经理'+id;
          const node=document.createElement('div');node.className='joblist-item';
          node.innerHTML='<span class="jname">'+title+'</span><span class="sal">3-5万</span><span class="area">深圳·南山区</span><a class="cname">示例公司有限公司</a>';
          node.querySelector('.jname').setAttribute('sensorsdata',JSON.stringify({jobId:id,jobTitle:title,pageNum:n,jobSalary:'3-5万',jobArea:'深圳·南山区'}));
          rows.push({jobId:id,jobName:title,fullCompanyName:'示例公司有限公司',provideSalaryString:'3-5万',jobAreaString:'深圳·南山区',jobHref:'https://jobs.51job.com/shenzhen-nanshan/'+id+'.html?trace=abc',jobDescribe:'职责：负责数据产品需求分析及设计。'.repeat(15)+'末尾要求：有数据治理落地经验。'});
          document.querySelector('.joblist').appendChild(node);
        }
        document.querySelector('#bound').__vue__={$props:{joblist:rows}};
        for(const e of document.querySelectorAll('.el-pager li'))e.classList.toggle('active',Number(e.textContent)===n);
        document.querySelector('.btn-prev').disabled=n===1;
        document.querySelector('.btn-next').disabled=n===2;
      };
      for(const e of document.querySelectorAll('.el-pager li'))e.onclick=()=>{moves++;const n=Number(e.textContent);e.classList.add('active');setTimeout(()=>renderFixture(n),50);};
      renderFixture(1);return true;
    })()""")


def test_full_bound_jd_and_actual_detail_urls(page):
    install(page)
    data=evaluate(page,build_job51_snapshot_script(40))
    assert data['invalidCardCount']==0
    assert data['currentPage']==data['loadedPage']==1
    assert len(data['cards'])==20
    assert all(j['jobDesc'].endswith('末尾要求：有数据治理落地经验。') for j in data['cards'])
    assert data['cards'][0]['jobUrl']=='https://jobs.51job.com/shenzhen-nanshan/1000.html'


@pytest.mark.parametrize('fault',[
    "r.jobId='different'", "r.fullCompanyName='其他公司'",
    "r.jobHref='https://example.test/1000.html'", "r.jobHref='https://jobs.51job.com/a/9999.html'",
])
def test_wrong_bound_identity_stops_collection(page,fault):
    install(page)
    evaluate(page,"(()=>{const r=document.querySelector('#bound').__vue__.$props.joblist[0];"+fault+";return true})()")
    data=evaluate(page,build_job51_snapshot_script(40))
    assert data['invalidCardCount']==1
    assert _snapshot_failure(data)=='job51_card_coverage_incomplete'


def test_second_page_requires_pager_not_url_and_old_rows_are_not_returned(page):
    install(page)
    class Driver:
        bulk_readonly=True
        def open_url_in_new_tab(self,*a,**k):
            pytest.fail('Same query must reuse its page, not navigate for pageNum')
        def _exec_js(self,script):
            if script.startswith('JSON.stringify({url:location.href,query:'):
                return {'url':'https://we.51job.com/pc/search?keyword=数据产品经理&jobArea=040000&pageNum=2','query':'数据产品经理'}
            return evaluate(page,script)
    result=Job51ReadOnlyCollector(Driver()).collect('数据产品经理','深圳',page=2,limit=30,wait_seconds=1)
    assert result.ok
    assert len(result.jobs)==20
    assert result.jobs[0].raw_data['jobId']=='1020'
    assert evaluate(page,'window.moves')==1
    again=Job51ReadOnlyCollector(Driver()).collect('数据产品经理','深圳',page=2,limit=30,wait_seconds=1)
    assert again.ok and evaluate(page,'window.moves')==1
