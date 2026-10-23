"""Use the logged-in page's existing client for two read-only Boss endpoints.

This keeps the site's normal request/session handling in the page. It neither
exports credentials nor implements or replaces its verification mechanisms.
"""
from __future__ import annotations

import json
from urllib.parse import urlsplit

_PATHS = frozenset({"/wapi/zpgeek/search/joblist.json", "/wapi/zpgeek/job/detail.json"})


def request_json(cdp, url):
    from jobagent.platforms.discovery import CollectionError
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.netloc != "www.zhipin.com" or parsed.path not in _PATHS:
        raise ValueError("Only Boss listing and detail GET requests are supported")
    script = r"""(async () => {
      const target = new URL(READ_URL);
      if (location.origin !== target.origin) return {readerError:'boss_read_origin_mismatch'};
      const text=(document.body?.innerText || '').slice(0,8000);
      const visibleVerificationFrame=[...document.querySelectorAll('iframe')].some(frame=>
        /captcha|verify|geetest/i.test(frame.src) && frame.getClientRects().length>0);
      if (/\/safe-validate|\/captcha|\/verify/.test(location.pathname)
          || /安全验证|请完成验证|拖动滑块/.test(text) || visibleVerificationFrame)
        return {readerError:'boss_verification_required'};
      // Resolve the already-loaded request client by its interface, not a
      // build-specific module number. Ambiguity stops collection.
      const chunks = window.webpackChunkgeek;
      if (!Array.isArray(chunks)) return {readerError:'boss_page_client_unavailable'};
      let loader;
      chunks.push([['jobagent_readonly_'+crypto.randomUUID()],{},r=>loader=r]);
      chunks.pop();
      if (!loader?.m) return {readerError:'boss_page_client_unavailable'};
      const matches = Object.entries(loader.m).filter(([,factory])=>{
        const source=String(factory);
        return source.includes('X-Requested-With') && source.includes('zp_token')
          && source.includes('interceptors.request.use');
      });
      const clients=[...new Set(matches.flatMap(([id])=>Object.values(loader(id))).filter(value=>
        typeof value?.get==='function' && value.interceptors?.request && value.interceptors?.response))];
      if (clients.length !== 1) return {readerError:'boss_page_client_unavailable'};
      try {
        const result=await clients[0].get(target.pathname, {
          // Do not let session recovery replay this read inside the site client.
          params:Object.fromEntries(target.searchParams), isLoading:false, timeout:8000, retryCount:1});
        if (!result || typeof result!=='object' || JSON.stringify(result).length>2097152)
          return {readerError:'boss_read_response_invalid'};
        return result;
      } catch (_) { return {readerError:'boss_read_transport_failed'}; }
    })()""".replace("READ_URL", json.dumps(url))
    try:
        result = cdp.evaluate(script, await_promise=True, timeout=12)
    except Exception as exc:
        raise CollectionError("boss_read_transport_failed", "Boss read did not complete; preserve the request",
            details={"retryable": False, "request_preserved": True, "automatic_retry_allowed": False}) from exc
    value = result.get("result", {}).get("value") if isinstance(result, dict) else None
    if not isinstance(value, dict) or value.get("readerError"):
        code = value.get("readerError") if isinstance(value, dict) else "boss_read_response_invalid"
        raise CollectionError(code, "Boss background reader needs recovery; browser and task are preserved",
            details={"retryable": False, "request_preserved": True, "automatic_retry_allowed": False})
    if value.get("code") != 0:
        code = "boss_detail_request_rejected" if parsed.path.endswith("/detail.json") else "boss_list_request_rejected"
        raise CollectionError(code, "Boss rejected the read; collection has stopped",
            details={"upstream_code": value.get("code"), "retryable": False,
                     "request_preserved": True, "automatic_retry_allowed": False})
    return value
