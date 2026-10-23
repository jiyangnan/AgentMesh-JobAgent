"""Read the data bound to current Zhilian split-view cards, never hidden APIs."""
from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from urllib.parse import urlsplit


def description_from_document(html: str, url: str) -> str:
    """Read an identity-bound full description from inert page JSON."""
    identifier = urlsplit(url).path.rsplit("/", 1)[-1].removesuffix(".htm")
    class Scripts(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=False)
            self.active = False
            self.parts = []
        def handle_starttag(self, tag, attrs):
            self.active = tag == "script"
        def handle_endtag(self, tag):
            if tag == "script":
                self.active = False
        def handle_data(self, data):
            if self.active:
                self.parts.append(data)
    parser = Scripts()
    parser.feed(html)
    for script in parser.parts:
        match = re.search(r"(?:window\.)?__INITIAL_STATE__\s*=\s*(?=\{)", script)
        if not match:
            continue
        try:
            value, _ = json.JSONDecoder().raw_decode(script[match.end():])
        except (ValueError, TypeError):
            continue
        if not isinstance(value, dict):
            continue
        detail = (value.get("jobDetail") or {}).get("detailedPosition") or {}
        if str(detail.get("positionNumber") or "") == identifier:
            description = detail.get("description") or detail.get("jobDesc") or ""
            if isinstance(description, str) and 80 <= len(description) <= 20000:
                # JSON is data only; use an inert HTML parser for any markup.
                class Text(HTMLParser):
                    def __init__(self):
                        super().__init__()
                        self.parts = []
                    def handle_starttag(self, tag, attrs):
                        if tag in {"br", "p", "div"}:
                            self.parts.append("\n")
                    def handle_data(self, data):
                        self.parts.append(data)
                text = Text()
                text.feed(description)
                return "".join(text.parts).strip()
    return ""


MODERN_LIST_JS = r"""
function zhilianModernList() {
  const clean = v => String(v || '').replace(/\s+/g, ' ').trim();
  const panel = document.querySelector('.job-list-panel');
  const nodes = panel ? [...panel.querySelectorAll('.job-card')] : [];
  if (!nodes.length) return null;
  const props = panel.__vue__?.$props;
  const cards = [];
  let invalid = 0;
  for (const node of nodes) {
    const job = node.__vue__?.$props?.job;
    const titleNode = node.querySelector('.job-card__title-clamp .vue-clamp__text');
    const title = clean(titleNode?.getAttribute('aria-label') || titleNode?.textContent);
    const companyNode = node.querySelector('.job-card__company-name');
    const company = clean(companyNode?.textContent);
    const salary = clean(node.querySelector('.job-card__salary')?.textContent);
    const location = clean(node.querySelector('.job-card__location')?.textContent);
    const id = clean(job?.number);
    let url;
    try { url = new URL(job?.positionUrl || job?.positionURL); } catch (_) {}
    // Both URL identity and rendered metadata must agree with the bound row.
    // Never substitute a right-hand detail or stale initial-state entry.
    if (!job || !id || !title || title !== clean(job.name)
        || !company || !clean(job.companyName).includes(company)
        || !salary || salary !== clean(job.salary60)
        || !job.workCity || !location.includes(clean(job.workCity))
        || !url || url.protocol !== 'https:' || url.hostname !== 'www.zhaopin.com'
        || url.username || url.password || url.port
        || url.pathname !== '/jobdetail/' + id + '.htm') {
      invalid++; continue;
    }
    const position = job.jobDetailData?.position;
    const base = position?.base;
    const description = position?.desc?.description;
    let jd = '';
    if (base?.positionNumber === id && clean(base.positionName) === title
        && typeof description === 'string') {
      // This field is the full description; jobDescription is a list teaser.
      const inert = new DOMParser().parseFromString(description, 'text/html');
      inert.querySelectorAll('br').forEach(e => e.replaceWith('\n'));
      jd = (inert.body.textContent || '').trim();
      if (jd.length < 80 || jd.length > 20000) jd = '';
    }
    cards.push({
      positionId:id, jobTitle:title, companyName:clean(job.companyName),
      jobUrl:url.href, salary, cityName:clean(job.workCity),
      district:clean(job.cityDistrict), education:clean(job.education),
      workingExp:clean(job.workingExp), companySize:clean(job.companySize),
      industryName:clean(job.industryName), financingStage:clean(job.financingStage),
      skills:Array.isArray(job.jobSkillTags) ? job.jobSkillTags.filter(x=>typeof x==='string') : [],
      ...(jd ? {jobDesc:jd} : {}),
      cardSource:'bound_split_card', titleSource:'rendered_bound_title',
      companySource:'rendered_bound_company'
    });
  }
  return {
    cards, renderedCount:nodes.length, invalid,
    hasMore:typeof props?.hasMore === 'boolean' ? props.hasMore : null,
    loading:props?.loading === true || props?.loadingMore === true,
    currentPage:Math.ceil(nodes.length / 20)
  };
}
"""


def build_load_more_script() -> str:
    return MODERN_LIST_JS + r"""
(() => {
  const list = zhilianModernList();
  if (!list || list.invalid || list.hasMore !== true || list.loading)
    return JSON.stringify({ok:false,error:'zhilian_load_more_unavailable'});
  const last = document.querySelector('.job-list-panel .job-card:last-child');
  if (!last) return JSON.stringify({ok:false,error:'zhilian_load_more_unavailable'});
  last.scrollIntoView({block:'end',behavior:'instant'});
  window.scrollTo({top:document.documentElement.scrollHeight,behavior:'instant'});
  return JSON.stringify({ok:true,action:'scroll_list',beforeCount:list.renderedCount});
})()
"""
