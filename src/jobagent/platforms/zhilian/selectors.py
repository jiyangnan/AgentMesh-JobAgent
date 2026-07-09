"""Zhilian live read-only DOM selectors."""

from __future__ import annotations

import json

ZHILIAN_SELECTOR_VERSION = "2026-07-09.0"


def build_zhilian_city_filter_script(city: str) -> str:
    safe_city = json.dumps(city, ensure_ascii=False)
    return f"""
    (function(){{
      const mode = 'zhilian_city_filter';
      const targetCity = {safe_city};
      const href = location.href || '';
      const title = document.title || '';
      const bodyText = (document.body && (document.body.innerText || document.body.textContent) || '').trim();
      const loginRequired = /passport|login|登录[/]注册|请登录|扫码登录|验证码登录|手机验证码|安全验证|滑块/.test(href + '\\n' + title + '\\n' + bodyText.slice(0, 800));
      if (!targetCity) {{
        return JSON.stringify({{ok: true, mode, skipped: true}});
      }}
      if (loginRequired) {{
        return JSON.stringify({{ok: false, mode, error: 'zhilian_login_required', loginRequired: true, url: href, title}});
      }}
      function clean(value){{
        return String(value || '').replace(/\\s+/g, ' ').trim();
      }}
      function visible(el){{
        if (!el || !(el instanceof Element)) return false;
        const style = window.getComputedStyle(el);
        const rect = el.getBoundingClientRect();
        return style.display !== 'none'
          && style.visibility !== 'hidden'
          && Number(style.opacity || '1') !== 0
          && rect.width > 8
          && rect.height > 8;
      }}
      function directText(el){{
        const own = Array.from(el.childNodes || [])
          .filter((node) => node.nodeType === Node.TEXT_NODE)
          .map((node) => node.textContent || '')
          .join('');
        return clean(own) || clean(el.innerText || el.textContent || '');
      }}
      function clickPoint(el){{
        const target = el.closest('a,button,label,li,span,div') || el;
        const rect = target.getBoundingClientRect();
        return {{
          x: Math.round(rect.left + rect.width / 2),
          y: Math.round(rect.top + rect.height / 2),
          tag: target.tagName,
          className: String(target.className || '').slice(0, 120),
          text: directText(target)
        }};
      }}
      const cityNames = ['北京','上海','广州','深圳','天津','武汉','西安','成都','大连','长春','沈阳','南京','济南','青岛','杭州','苏州','无锡','宁波','重庆','郑州','长沙','福州','厦门','哈尔滨'];
      const visibleElements = Array.from(document.querySelectorAll('body *')).filter(visible);
      function cityCount(text){{
        return cityNames.reduce((count, name) => count + (text.includes(name) ? 1 : 0), 0);
      }}
      function findLocationHeader(){{
        const matches = visibleElements
          .filter((el) => /^地点\\s*[⌄∨▾⌃∧▲▼]?$/.test(directText(el)) || directText(el) === '地点')
          .map((el) => {{
            const rect = el.getBoundingClientRect();
            return {{el, rect, area: rect.width * rect.height}};
          }})
          .filter((item) => item.rect.top < 320)
          .sort((a, b) => a.area - b.area);
        return matches[0] && matches[0].el;
      }}
      function findLocationRoot(){{
        const header = findLocationHeader();
        if (header) {{
          let root = header;
          let best = header;
          for (let i = 0; i < 8 && root.parentElement; i++) {{
            root = root.parentElement;
            if (!visible(root)) continue;
            const rect = root.getBoundingClientRect();
            const plausibleFilterRoot = rect.top < 320 && rect.height <= 700 && rect.width >= 200;
            const text = clean(root.innerText || root.textContent || '');
            if (text.includes('地点') && plausibleFilterRoot) best = root;
            if (plausibleFilterRoot && text.includes('地点') && text.includes(targetCity) && cityCount(text) >= 4) {{
              return root;
            }}
          }}
          return best;
        }}
        const candidates = visibleElements
          .map((el) => {{
            const text = clean(el.innerText || el.textContent || '');
            const rect = el.getBoundingClientRect();
            return {{el, text, rect, count: cityCount(text)}};
          }})
          .filter((item) => item.rect.top < 700 && item.text.includes(targetCity) && item.count >= 4)
          .sort((a, b) => a.text.length - b.text.length);
        return candidates[0] && candidates[0].el;
      }}
      let root = findLocationRoot();
      let expanded = false;
      if (!root || !clean(root.innerText || root.textContent || '').includes(targetCity) || cityCount(clean(root.innerText || root.textContent || '')) < 4) {{
        const header = findLocationHeader();
        if (header) {{
          expanded = true;
        }}
        return JSON.stringify({{
          ok: false,
          mode,
          error: 'zhilian_city_options_collapsed',
          action: 'expand_location',
          expanded,
          clickPoint: header ? clickPoint(header) : null,
          city: targetCity,
          url: href,
          title
        }});
      }}
      const options = Array.from(root.querySelectorAll('a,button,label,li,span,div'))
        .filter(visible)
        .filter((el) => directText(el) === targetCity)
        .map((el) => {{
          const rect = el.getBoundingClientRect();
          const className = String(el.className || '');
          const selected = /active|selected|checked|current/.test(className);
          return {{el, rect, selected}};
        }})
        .filter((item) => item.rect.top < 700)
        .sort((a, b) => (a.rect.width * a.rect.height) - (b.rect.width * b.rect.height));
      if (!options.length) {{
        return JSON.stringify({{
          ok: false,
          mode,
          error: 'zhilian_city_option_not_found',
          city: targetCity,
          rootText: clean(root.innerText || root.textContent || '').slice(0, 500),
          url: href,
          title
        }});
      }}
      const option = options[0];
      if (option.selected) {{
        return JSON.stringify({{ok: true, mode, city: targetCity, alreadySelected: true, url: href, title}});
      }}
      return JSON.stringify({{
        ok: true,
        mode,
        city: targetCity,
        applied: true,
        action: 'select_city',
        clickPoint: clickPoint(option.el),
        urlBefore: href,
        title
      }});
    }})()
    """


def build_zhilian_snapshot_script(limit: int = 20) -> str:
    safe_limit = max(1, int(limit))
    return f"""
    (function(){{
      const selectorVersion = "{ZHILIAN_SELECTOR_VERSION}";
      const limit = {safe_limit};
      const text = (document.body && (document.body.innerText || document.body.textContent) || '').trim();
      const title = document.title || '';
      const href = location.href || '';
      const loginRequired = /passport|login|登录[/]注册|请登录|扫码登录|验证码登录|手机验证码|安全验证|滑块/.test(href + '\\n' + title + '\\n' + text.slice(0, 800));
      function visible(el){{
        const style = window.getComputedStyle(el);
        const rect = el.getBoundingClientRect();
        return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 40 && rect.height > 20;
      }}
      function clean(value){{
        return String(value || '').replace(/\\s+/g, ' ').trim();
      }}
      function lines(value){{
        return String(value || '').split(/\\n+/).map(clean).filter(Boolean);
      }}
      const ctaLabels = new Set(['立即投递', '立即沟通', '继续沟通', '继续聊', '聊一聊', '投递简历', '申请职位', '收藏', '举报']);
      function looksLikeMeta(line){{
        return !line || ctaLabels.has(line)
          || /^\\d+(?:\\.\\d+)?[-~—至]\\d+(?:\\.\\d+)?(?:万|千|[kK]|元)/.test(line)
          || /^面议$/.test(line)
          || /^(北京|上海|深圳|广州|杭州|成都|武汉|南京|苏州|西安|郑州|天津|重庆|临沂|长沙|泉州|保定|青岛)/.test(line)
          || /^(经验不限|\\d+-\\d+年|\\d+年以上|应届|本科|大专|硕士|博士|学历不限)/.test(line)
          || /^(高回复率|\\d+小时内回复可能性大|12小时内回复可能性大|今日活跃|昨日活跃|刚刚活跃)$/.test(line);
      }}
      function titleFrom(root, label){{
        const cleanLabel = clean(label);
        if (cleanLabel && !ctaLabels.has(cleanLabel) && cleanLabel.length >= 2 && cleanLabel.length <= 80) return cleanLabel;
        for (const line of lines(root.innerText || root.textContent || '')) {{
          if (line.length < 2 || line.length > 80) continue;
          if (looksLikeMeta(line)) continue;
          return line;
        }}
        return cleanLabel;
      }}
      function cardRoot(anchor){{
        let root = anchor;
        let best = anchor;
        for (let i = 0; i < 9 && root.parentElement; i++) {{
          root = root.parentElement;
          const raw = clean(root.innerText || root.textContent || '');
          const hasJobSignal = /立即投递|立即沟通|今日回复|刚刚活跃|高回复率|\\d+(?:\\.\\d+)?[-~—至]\\d+(?:\\.\\d+)?(?:万|千|[kK]|元)|面议/.test(raw);
          const hasAction = /立即投递|立即沟通|继续沟通|投递简历|申请职位/.test(raw);
          const hasCompanySignal = /公司|集团|有限公司|股份|科技|信息|咨询|人力资源/.test(raw);
          if (raw.length >= 20 && hasJobSignal) {{
            best = root;
            const applyMentions = (raw.match(/立即投递/g) || []).length;
            if (raw.length >= 60 && raw.length <= 2200 && applyMentions <= 2 && (hasAction || hasCompanySignal)) break;
          }}
        }}
        return best;
      }}
      const anchors = Array.from(document.querySelectorAll('a[href]')).filter(visible);
      const navLabels = new Set(['首页', '职位推荐', '城市频道', '政企招聘', '校园招聘', '高端职位', '海外招聘', '驻外专区', '测评及培训', '职Q社区', '我要招人']);
      const cards = [];
      const seen = new Set();
      for (const anchor of anchors) {{
        const url = anchor.href || '';
        const label = clean(anchor.innerText || anchor.textContent || '');
        if (!url || !/[/]jobdetail[/]/.test(url)) continue;
        if (navLabels.has(label)) continue;
        const root = cardRoot(anchor);
        const rawText = clean(root.innerText || root.textContent || '');
        if (!rawText || rawText.length < 20) continue;
        const hasJobSignal = /立即投递|立即沟通|今日回复|刚刚活跃|高回复率|\\d+(?:\\.\\d+)?[-~—至]\\d+(?:\\.\\d+)?(?:万|千|[kK]|元)|面议/.test(rawText);
        if (!hasJobSignal) continue;
        const title = titleFrom(root, label);
        if (!title || ctaLabels.has(title)) continue;
        const key = url.split('?')[0];
        if (seen.has(key)) continue;
        seen.add(key);
        const salary = (rawText.match(/\\d+(?:\\.\\d+)?[-~—至]\\d+(?:\\.\\d+)?万(?:·\\d+薪)?|\\d+(?:\\.\\d+)?[-~—至]\\d+(?:\\.\\d+)?千(?:·\\d+薪)?|\\d+[kK][-~—至]\\d+[kK](?:·\\d+薪)?|\\d+-\\d+元(?:[/]月)?|面议/) || [''])[0];
        const city = (rawText.match(/北京|上海|深圳|广州|杭州|成都|武汉|南京|苏州|西安|郑州|天津|重庆/) || [''])[0];
        cards.push({{
          jobTitle: title,
          jobUrl: url,
          salary,
          cityName: city,
          rawText: rawText.slice(0, 1200)
        }});
        if (cards.length >= limit) break;
      }}
      return JSON.stringify({{
        ok: true,
        platform: 'zhilian',
        selectorVersion,
        url: href,
        title,
        loginRequired,
        candidateCount: cards.length,
        cards,
        bodySnippet: text.slice(0, 1200)
      }});
    }})()
    """
