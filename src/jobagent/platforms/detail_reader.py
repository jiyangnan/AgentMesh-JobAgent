"""Bounded detail reads, without navigating the user's collection tab."""
from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlsplit

from jobagent.platforms.discovery import CollectionError

_SELECTORS = {
    "boss": ".job-sec-text, .job-detail-section .text",
    "liepin": ".job-description, .job-intro-container, .job-description-box",
    "zhilian": ".describtion__detail-content, .job-description, .job-detail-description",
    "51job": ".job_msg, .job-detail, .job-info",
}


class _DetailHTML(HTMLParser):
    """Parse inert response text locally; never render returned HTML."""
    def __init__(self, platform):
        super().__init__(convert_charrefs=True)
        self.classes = set(re.findall(r"\.([\w-]+)", _SELECTORS[platform]))
        self.classes.discard("job-detail-section")  # Container alone is too broad.
        self.classes.discard("text")  # Generic site text is not a job description.
        self.stack = []
        self.parts = []
        self.body = []

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        match = bool(set(values.get("class", "").split()) & self.classes)
        parent = self.stack[-1] if self.stack else ("", False, False)
        capture = parent[1] or match
        ignored = parent[2] or tag in {"script", "style", "noscript", "template"}
        if tag not in {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"}:
            self.stack.append((tag, capture, ignored))
        elif capture and tag == "br":
            self.parts.append("\n")

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                if self.stack[index][1]:
                    self.parts.append("\n")
                del self.stack[index:]
                break

    def handle_data(self, value):
        if self.stack and not self.stack[-1][2]:
            self.body.append(value)
            if self.stack[-1][1]:
                self.parts.append(value)


def _background_detail(reader, platform, url):
    result = reader.read_detail_document(url)
    if result.get("error"):
        code = result["error"]
        if code == "detail_user_action_required":
            raise CollectionError(code, "Platform login or verification interrupted read-only collection",
                user_prompt="平台要求登录或安全验证，请在当前专用 Chrome 检查，随后继续同一 Discover。",
                details={"requires_user_action": True, "retryable": False, "request_preserved": True})
        raise CollectionError(code, "Background detail read paused; no page navigation was attempted",
            details={"retryable": True, "request_preserved": True})
    parser = _DetailHTML(platform)
    parser.feed(str(result.get("html") or ""))
    text = " ".join(parser.body)
    return {"url": result.get("url", ""),
            "restricted": bool(re.search(r"安全验证|滑动验证|访问过于频繁|访问受限|请完成验证", text)),
            "login": bool(re.search(r"登录后查看|请先登录|登录后继续", text)),
            "jd": "".join(parser.parts).strip()[:20000]}


def detail_url_allowed(platform, url):
    try:
        parsed = urlsplit(url)
        host = parsed.hostname or ""
        domains = {"boss": ("zhipin.com",), "liepin": ("liepin.com",),
                   "zhilian": ("zhaopin.com",), "51job": ("51job.com", "51job.com.cn")}[platform]
        if (parsed.scheme != "https" or parsed.port not in {None, 443} or parsed.username
                or parsed.password or not any(host == d or host.endswith("." + d) for d in domains)):
            return False
        path = parsed.path
        if platform == "boss":
            return bool(re.fullmatch(r"/job_detail/[^/]+\.html", path))
        if platform == "liepin":
            return bool(re.fullmatch(r"/(?:job|a)/[^/]+\.(s?html)", path))
        if platform == "zhilian":
            return bool(re.fullmatch(r"/jobdetail/[^/]+(?:/)?", path)
                        or (host == "jobs.zhaopin.com" and re.fullmatch(r"/[^/]+\.html?", path)))
        if path.rstrip("/") == "/pc/jobdetail":
            return bool(dict(parse_qsl(parsed.query)).get("jobId"))
        return host == "jobs.51job.com" and bool(re.fullmatch(r"/(?:[^/]+/)?[0-9]+\.html", path))
    except (ValueError, KeyError, TypeError):
        return False


def read_detail(reader, platform, item):
    url = item["url"]
    if not detail_url_allowed(platform, url):
        raise ValueError("Only the signed official job detail URL can be read")
    if platform == "boss" and callable(getattr(reader, "read_boss_detail", None)):
        return _validated_detail(platform, item, reader.read_boss_detail(item))
    if callable(getattr(reader, "read_detail_document", None)):
        value = _background_detail(reader, platform, url)
        return _validated_detail(platform, item, value)
    # Compatibility for pre-existing adapters. The v2 CollectionReader always
    # takes the non-navigating branch above, including missing-detail results.
    opened = reader.open_url_in_new_tab(url, wait_seconds=3)
    if opened.get("error"):
        if opened["error"] == "detail_user_action_required":
            raise CollectionError("detail_user_action_required", "Login or verification interrupted detail collection",
                user_prompt="请在当前专用 Chrome 完成登录或验证，随后继续同一 Discover。",
                details={"requires_user_action": True, "retryable": False, "request_preserved": True})
        raise CollectionError("detail_navigation_failed", "Detail navigation did not complete", details={"retryable": True})
    script = """(() => {
      const text = document.body?.innerText || '';
      const restricted = /安全验证|滑动验证|访问过于频繁|访问受限|请完成验证/.test(text);
      const login = /登录后查看|请先登录|登录后继续/.test(text);
      const nodes = [...document.querySelectorAll(SELECTORS)];
      return JSON.stringify({url:location.href,restricted,login,
        jd:nodes.filter(n=>n.getClientRects().length).map(n=>n.innerText).join('\\n').slice(0,20000)});
    })()""".replace("SELECTORS", json.dumps(_SELECTORS[platform]))
    result = reader._exec_js(script)
    if isinstance(result, dict) and result.get("error"):
        raise CollectionError("detail_read_failed", "Detail read did not complete", details={"retryable": True})
    value = result.get("result", result) if isinstance(result, dict) else result
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, dict):
        raise CollectionError("detail_read_failed", "Detail page result was not readable")
    return _validated_detail(platform, item, value)


def _validated_detail(platform, item, value):
    url = item["url"]
    if value.get("restricted") or value.get("login"):
        raise CollectionError("detail_user_action_required", "Platform verification or login is required",
            user_prompt="请在当前专用 Chrome 完成平台验证或登录，完成后继续同一 Discover。",
            details={"requires_user_action": True, "retryable": False, "request_preserved": True})
    actual = urlsplit(value.get("url", ""))
    expected = urlsplit(url)
    if (not detail_url_allowed(platform, value.get("url", ""))
            or (actual.hostname, actual.path) != (expected.hostname, expected.path)
            or (platform == "51job" and dict(parse_qsl(actual.query)).get("jobId")
                != dict(parse_qsl(expected.query)).get("jobId"))):
        raise CollectionError("detail_identity_mismatch", "Detail navigation changed the signed job identity")
    jd = str(value.get("jd") or "").strip()
    return {"id": item["id"], "url": url, "status": "available" if jd else "unavailable", "jd": jd}
