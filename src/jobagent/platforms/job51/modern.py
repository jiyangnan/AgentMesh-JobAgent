"""Read full descriptions bound to rendered 51Job cards and verify pagination."""
from __future__ import annotations

import json
import time
from urllib.parse import parse_qs, urlsplit


BOUND_LIST_JS = r"""
function job51BoundRows(nodes) {
  let parent = nodes[0], rows = null;
  for (let depth=0; parent && depth<5; depth++, parent=parent.parentElement) {
    const value = parent.__vue__?.$props?.joblist;
    if (Array.isArray(value)) { rows=value; break; }
  }
  if (!rows) return null;
  const clean=v=>String(v||'').replace(/\s+/g,' ').trim();
  const byId=new Map(), duplicates=new Set();
  for (const row of rows) {
    const id=String(row.jobId||'');
    if (byId.has(id)) duplicates.add(id);
    byId.set(id,row);
  }
  const result=new Map(); let invalid=0;
  for (const node of nodes) {
    let visible={};
    try { visible=JSON.parse(node.querySelector('[sensorsdata]')?.getAttribute('sensorsdata')||'{}'); } catch (_) {}
    const id=String(visible.jobId||''), row=byId.get(id);
    const title=clean(node.querySelector('.jname')?.getAttribute('title') || node.querySelector('.jname')?.textContent);
    const company=clean(node.querySelector('.cname')?.getAttribute('title') || node.querySelector('.cname')?.textContent);
    let url; try { url=new URL(row?.jobHref); } catch (_) {}
    if (!row || duplicates.has(id) || !/^\d+$/.test(id) || !title || !company
        || title!==clean(row.jobName) || title!==clean(visible.jobTitle)
        || company!==clean(row.fullCompanyName)
        || clean(node.querySelector('.sal')?.textContent)!==clean(row.provideSalaryString)
        || clean(node.querySelector('.area')?.textContent)!==clean(row.jobAreaString)
        || !url || url.protocol!=='https:' || url.hostname!=='jobs.51job.com'
        || url.port || url.username || url.password
        || !new RegExp('^/(?:[^/]+/)?'+id+'\\.html$').test(url.pathname)) {
      invalid++; continue;
    }
    let jd='';
    if (typeof row.jobDescribe==='string') {
      const inert=new DOMParser().parseFromString(row.jobDescribe,'text/html');
      inert.querySelectorAll('br').forEach(e=>e.replaceWith('\n'));
      jd=(inert.body.textContent||'').trim();
      if (jd.length<80 || jd.length>20000) jd='';
    }
    url.search=''; url.hash='';
    result.set(id,{jobUrl:url.href, ...(jd?{jobDesc:jd}:{}),
      companySize:row.companySizeString, industryName:row.coIndustryText,
      cardSource:'bound_search_card', fullDescriptionSource:jd?'jobDescribe':null});
  }
  return {rows:result,invalid};
}
"""


def _unwrap(value):
    if isinstance(value, dict) and 'raw' in value:
        try:
            value = json.loads(value['raw'])
        except (ValueError, TypeError):
            return {}
    return value if isinstance(value, dict) else {}


def collect_page(reader, query, city, page, wait_seconds):
    """Reuse this query's page and operate its bounded visible pager once per step."""
    from .collect import build_job51_search_url, _job51_city_code
    from .selectors import build_job51_snapshot_script

    desired = max(1, int(page))
    if desired > 50:
        return {'ok':False,'error':'job51_page_limit_exceeded'}
    observed = _unwrap(reader._exec_js("JSON.stringify({url:location.href,query:document.querySelector('#keywordInput')?.value})"))
    try:
        current = urlsplit(observed.get('url',''))
        params = parse_qs(current.query)
        same = (current.scheme=='https' and current.netloc=='we.51job.com' and current.path=='/pc/search'
                and params.get('keyword')==[query] and observed.get('query')==query
                and params.get('jobArea', [''])==[_job51_city_code(city)])
    except ValueError:
        same = False
    if not same:
        opened = reader.open_url_in_new_tab(build_job51_search_url(query, city), wait_seconds=wait_seconds)
        if not opened.get('ok'):
            return opened
    script = build_job51_snapshot_script(40)

    def ready(expected=None):
        snapshot={}
        for _ in range(max(2, int(max(8, wait_seconds)*2))):
            snapshot=_unwrap(reader._exec_js(script))
            if snapshot.get('loginRequired') or snapshot.get('ok') is False:
                return snapshot
            if snapshot.get('pageReady') and (snapshot.get('noResults') or
                    (snapshot.get('currentPage')==snapshot.get('loadedPage')
                     and (expected is None or snapshot.get('currentPage')==expected))):
                return snapshot
            time.sleep(.5)
        return {**snapshot,'ok':False,'error':'job51_page_transition_unconfirmed'}

    snapshot=ready()
    for _ in range(50):
        if snapshot.get('ok') is False or snapshot.get('loginRequired') or snapshot.get('noResults'):
            return snapshot
        current_page=snapshot.get('currentPage')
        if current_page==desired:
            if snapshot.get('query')!=query:
                return {**snapshot,'ok':False,'error':'job51_query_changed'}
            return snapshot
        if not isinstance(current_page,int):
            return {**snapshot,'ok':False,'error':'job51_pagination_unknown'}
        action=_unwrap(reader._exec_js(r"""(() => {
          const desired=DESIRED, current=Number(document.querySelector('.el-pager .active')?.textContent);
          const pager=document.querySelector('.el-pagination');
          if (!pager || current!==CURRENT) return JSON.stringify({ok:false});
          const direct=[...pager.querySelectorAll('li.number')].find(e=>Number(e.textContent)===desired);
          const next=direct || pager.querySelector(desired>current?'button.btn-next':'button.btn-prev');
          if (!next || !next.getClientRects().length || next.disabled || next.classList.contains('disabled'))
            return JSON.stringify({ok:false,error:'job51_pagination_exhausted'});
          next.click();
          return JSON.stringify({ok:true,expected:direct?desired:current+(desired>current?1:-1)});
        })()""".replace('DESIRED',str(desired)).replace('CURRENT',str(current_page))))
        if not action.get('ok'):
            return {**snapshot,'ok':False,'error':action.get('error','job51_pagination_unknown')}
        snapshot=ready(action['expected'])
    return {**snapshot,'ok':False,'error':'job51_page_limit_exceeded'}
