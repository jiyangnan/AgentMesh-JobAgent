"""Liepin DOM selectors and snapshot extraction script."""

from __future__ import annotations

LIEPIN_SELECTOR_VERSION = "2026-06-12.2"

LIEPIN_CARD_SELECTORS: tuple[str, ...] = (
    "[data-job-id]",
    "[data-nick]",
    ".job-card-pc-container",
    ".job-card",
    ".sojob-item-main",
    "li",
)


def build_liepin_snapshot_script(limit: int = 20) -> str:
    """Return JavaScript that extracts visible Liepin job-card candidates."""
    selectors = ", ".join(LIEPIN_CARD_SELECTORS)
    return f"""
    (function(){{
      const limit = {int(limit)};
      const selectorVersion = '{LIEPIN_SELECTOR_VERSION}';
      const candidates = Array.from(document.querySelectorAll({selectors!r}));
      const title = document.title || '';
      const href = location.href || '';
      const bodyText = (document.body && (document.body.innerText || document.body.textContent) || '').trim();
      const loginPromptPresent = /登录|验证码|扫码/.test(title + '\\n' + bodyText.slice(0, 500));
      const loginRequired = /\\/login|passport|account/.test(href) || /登录|账号|passport/i.test(title);
      const seen = new Set();
      const cards = [];
      const rejected = {{
        emptyText: 0,
        weakSignal: 0,
        duplicate: 0,
        missingIdentity: 0
      }};
      function textOf(el) {{
        return (el && (el.innerText || el.textContent) || '').trim();
      }}
      function absUrl(href) {{
        try {{ return new URL(href, location.origin).href; }}
        catch (e) {{ return href || ''; }}
      }}
      function cleanLine(line) {{
        return String(line || '').replace(/[【】]/g, '').trim();
      }}
      function isNoiseLine(line) {{
        return !line
          || /急聘|广告|在线|分钟前|小时前|天前|招聘者活跃/.test(line)
          || /^(\\d+人以上|\\d+-\\d+人|\\d+人以下)$/.test(line);
      }}
      for (const el of candidates) {{
        if (cards.length >= limit) break;
        const text = textOf(el);
        if (!text || text.length < 8) {{
          rejected.emptyText += 1;
          continue;
        }}
        if (!/k|薪|年|经验|本科|大专|招聘|猎头|顾问|公司|AI|产品/i.test(text)) {{
          rejected.weakSignal += 1;
          continue;
        }}
        // Identity: prefer a child <a> with /job/ href; also accept the
        // candidate itself when it IS the <a> (Liepin wraps job titles in
        // <a href="/job/XXX.shtml"> directly, with no inner <a>).
        let link = el.querySelector('a[href*="/job/"], a[href*="liepin.com/job"]');
        if (!link && el.tagName === 'A') {{
          const selfHref = el.getAttribute('href') || '';
          if (selfHref.indexOf('/job/') >= 0 || selfHref.indexOf('liepin.com/job') >= 0) {{
            link = el;
          }}
        }}
        const url = link ? absUrl(link.getAttribute('href') || '') : '';
        const id = el.getAttribute('data-job-id') || el.getAttribute('data-id') || '';
        if (!url && !id) {{
          rejected.missingIdentity += 1;
          continue;
        }}
        const dedupe = url || id || text.slice(0, 80);
        if (seen.has(dedupe)) {{
          rejected.duplicate += 1;
          continue;
        }}
        seen.add(dedupe);

        const lines = text.split('\\n').map(cleanLine).filter(Boolean);
        if (lines.length < 4) {{
          rejected.weakSignal += 1;
          continue;
        }}
        const salaryIndex = lines.findIndex(x => /k|薪|万|元/i.test(x));
        // City extraction (two strategies):
        //   1. Prefer the 【城市-区域】 bracketed pattern Liepin uses on job
        //      cards ("【沈阳-浑南区】", "【上海-黄浦区】", etc.) — this catches
        //      every Chinese city, not just a whitelist.
        //   2. Fall back to a major-city whitelist for cards without brackets.
        let cityIndex = -1;
        for (let i = 0; i < lines.length; i++) {{
          if (/^[【\\[]?[\\u4e00-\\u9fa5]{2,8}-[\\u4e00-\\u9fa5]{2,8}[】\\]]?$/.test(lines[i])) {{
            cityIndex = i; break;
          }}
        }}
        if (cityIndex < 0) {{
          cityIndex = lines.findIndex(x => /北京|上海|深圳|广州|杭州|成都|南京|苏州|武汉|西安|天津|重庆|大连|厦门|青岛|长沙|郑州|合肥|佛山|东莞|宁波|沈阳|乌鲁木齐|济南|哈尔滨|长春|昆明|南宁|福州|石家庄|太原|贵阳|兰州|海口|南昌|无锡|温州|珠海|中山|惠州/.test(x));
        }}
        const experienceIndex = lines.findIndex(x => /经验|年/.test(x));
        const degreeIndex = lines.findIndex(x => /本科|硕士|博士|大专|学历|统招/.test(x));
        const salary = salaryIndex >= 0 ? lines[salaryIndex] : '';
        const city = cityIndex >= 0 ? lines[cityIndex] : '';
        const experience = experienceIndex >= 0 ? lines[experienceIndex] : '';
        const degree = degreeIndex >= 0 ? lines[degreeIndex] : '';
        const companyStart = Math.max(salaryIndex, experienceIndex, degreeIndex, cityIndex) + 1;
        const company = lines.find((x, i) =>
          i >= companyStart
          && x !== lines[0]
          && x !== salary
          && x !== city
          && x !== experience
          && x !== degree
          && !isNoiseLine(x)
        ) || '';
        cards.push({{
          jobId: id,
          jobTitle: lines[0] || '',
          salary,
          companyName: company,
          cityName: city,
          workYear: experience,
          education: degree,
          jobUrl: url,
          rawText: text
        }});
      }}
      return JSON.stringify({{
        ok: true,
        url: href,
        title,
        selectorVersion,
        loginRequired,
        loginPromptPresent,
        bodySnippet: bodyText.slice(0, 500),
        candidateCount: candidates.length,
        cardCount: cards.length,
        rejected,
        cards
      }});
    }})()
    """
