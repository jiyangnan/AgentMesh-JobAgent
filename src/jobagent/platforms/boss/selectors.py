"""Boss DOM selectors, visible action labels and search-card extraction."""

from __future__ import annotations

BOSS_SELECTOR_VERSION = "2026-07-11.0"

CHAT_ENTRY_TEXTS = ["立即沟通", "继续沟通"]
CHAT_EDITOR_SELECTOR = "[contenteditable='true'].chat-input, [contenteditable='true']"
SEND_BUTTON_SELECTOR = "button.btn-send, .btn-send"


def build_boss_snapshot_script(limit: int = 15) -> str:
    """Extract visible Boss search cards without calling the search XHR directly."""
    safe_limit = max(1, min(100, int(limit)))
    return f"""
    (function(){{
      const selectorVersion = "{BOSS_SELECTOR_VERSION}";
      const limit = {safe_limit};
      const glyphDigits = {{
        '\\ue031':'0', '\\ue032':'1', '\\ue033':'2', '\\ue034':'3', '\\ue035':'4',
        '\\ue036':'5', '\\ue037':'6', '\\ue038':'7', '\\ue039':'8', '\\ue03a':'9'
      }};
      const decode = (value) => String(value || '').replace(/[\\ue031-\\ue03a]/g, (char) => glyphDigits[char] || char);
      const clean = (value) => decode(value).replace(/\\s+/g, ' ').trim();
      const href = location.href || '';
      const title = document.title || '';
      const nodes = Array.from(document.querySelectorAll('.job-card-box'));
      const bodyText = clean(document.body && (document.body.innerText || document.body.textContent));
      const loginRequired = nodes.length === 0
        && (/login|passport/.test(href) || /登录|扫码登录|验证码登录/.test(title + '\\n' + bodyText.slice(0, 800)));
      const verificationRequired = nodes.length === 0
        && (/verify|code=36/.test(href) || /安全验证|环境存在异常|拖动滑块/.test(bodyText.slice(0, 1000)));
      const cards = [];
      const seen = new Set();
      for (const card of nodes) {{
        if (cards.length >= limit) break;
        const link = card.querySelector('.job-name[href*="job_detail"]');
        const jobUrl = link ? link.href : '';
        const match = jobUrl.match(/job_detail\\/([^/?#]+)\\.html/);
        const jobId = match ? match[1] : '';
        if (!jobId || seen.has(jobId)) continue;
        seen.add(jobId);
        const tags = Array.from(card.querySelectorAll('.tag-list li'))
          .map((node) => clean(node.innerText || node.textContent))
          .filter(Boolean);
        const locationText = clean(card.querySelector('.company-location')?.innerText || '');
        const locationParts = locationText.split('·').map(clean).filter(Boolean);
        cards.push({{
          encryptJobId: jobId,
          jobId,
          jobName: clean(link && (link.innerText || link.textContent)),
          salaryDesc: clean(card.querySelector('.job-salary')?.innerText || ''),
          brandName: clean(card.querySelector('.boss-name')?.innerText || ''),
          cityName: locationParts[0] || '',
          areaDistrict: locationParts[1] || '',
          businessDistrict: locationParts.slice(2).join('·'),
          jobExperience: tags[0] || '',
          jobDegree: tags[1] || '',
          skills: tags.slice(2),
          jobUrl,
          source: 'boss_search_dom',
          selectorVersion,
          rawText: clean(card.innerText || card.textContent).slice(0, 1200)
        }});
      }}
      return JSON.stringify({{
        ok: true,
        platform: 'boss',
        selectorVersion,
        url: href,
        title,
        loginRequired,
        verificationRequired,
        candidateCount: nodes.length,
        cardCount: cards.length,
        noResults: /暂无职位|没有找到|换个关键词/.test(bodyText.slice(0, 1400)),
        cards,
        bodySnippet: bodyText.slice(0, 1000)
      }});
    }})()
    """
