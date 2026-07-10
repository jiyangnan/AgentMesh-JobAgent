"""51Job live read-only DOM selectors."""

from __future__ import annotations

JOB51_SELECTOR_VERSION = "2026-07-10.0"


def build_job51_snapshot_script(limit: int = 20) -> str:
    safe_limit = max(1, int(limit))
    return f"""
    (function(){{
      const selectorVersion = "{JOB51_SELECTOR_VERSION}";
      const limit = {safe_limit};
      const clean = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
      const href = location.href || '';
      const title = document.title || '';
      const bodyText = clean(document.body && (document.body.innerText || document.body.textContent));
      const loginRequired = /login|passport|登录[/]注册|请登录|扫码登录|验证码登录|手机验证码|安全验证|滑块/.test(href + '\\n' + title)
        && !document.querySelector('.joblist-item');
      function parseSensors(el) {{
        const raw = el && el.getAttribute && el.getAttribute('sensorsdata');
        if (!raw) return {{}};
        try {{ return JSON.parse(raw); }} catch (e) {{ return {{_parseError: String(e), _raw: raw.slice(0, 500)}}; }}
      }}
      function text(el, selector) {{
        const node = el && el.querySelector(selector);
        return clean(node && (node.innerText || node.textContent));
      }}
      function titleAttr(el, selector) {{
        const node = el && el.querySelector(selector);
        return clean(node && (node.getAttribute('title') || node.innerText || node.textContent));
      }}
      const cards = [];
      const seen = new Set();
      const nodes = Array.from(document.querySelectorAll('.joblist-item'));
      for (const card of nodes) {{
        if (cards.length >= limit) break;
        const sensorsNode = card.querySelector('[sensorsdata]');
        const sensors = parseSensors(sensorsNode);
        const jobId = clean(sensors.jobId || card.getAttribute('data-job-id') || '');
        const rawText = clean(card.innerText || card.textContent);
        if (!jobId && !rawText) continue;
        const key = jobId || rawText.slice(0, 120);
        if (seen.has(key)) continue;
        seen.add(key);
        const companyLink = card.querySelector('a.comp[href]');
        const tags = Array.from(card.querySelectorAll('.joblist-item-tags .tag'))
          .map((node) => clean(node.innerText || node.textContent))
          .filter(Boolean);
        cards.push({{
          jobId,
          companyId: clean(sensors.companyId || ''),
          jobTitle: clean(sensors.jobTitle || titleAttr(card, '.jname') || text(card, '.joblist-item-jobname')),
          salary: clean(sensors.jobSalary || text(card, '.sal')),
          cityName: clean(sensors.jobArea || text(card, '.area')),
          workYear: clean(sensors.jobYear || ''),
          education: clean(sensors.jobDegree || ''),
          companyName: titleAttr(card, '.cname') || clean((companyLink && companyLink.innerText) || '').split(' ')[0],
          companyUrl: companyLink ? companyLink.href : '',
          companyIndustry: text(card, '.bc'),
          tags,
          rawText: rawText.slice(0, 1600),
          sourceUrl: href,
          actions: {{
            hasChat: Boolean(card.querySelector('.chat')),
            chatText: text(card, '.chat'),
            hasApply: Boolean(card.querySelector('button.apply, .btn.apply')),
            applyText: text(card, 'button.apply, .btn.apply')
          }},
          sensors
        }});
      }}
      return JSON.stringify({{
        ok: true,
        platform: '51job',
        selectorVersion,
        url: href,
        title,
        loginRequired,
        loginPromptPresent: /登录[/]注册|请登录|扫码登录|验证码登录/.test(bodyText.slice(0, 1200)),
        candidateCount: nodes.length,
        cardCount: cards.length,
        cards,
        bodySnippet: bodyText.slice(0, 1200)
      }});
    }})()
    """
