"""Focused tests for Zhilian live collection helpers."""

from jobagent.platforms.zhilian.collect import build_zhilian_search_url


def test_build_zhilian_search_url_encodes_verified_beijing_city():
    url = build_zhilian_search_url("数据产品负责人", city="北京", page=1)

    assert url == (
        "https://sou.zhaopin.com/?jl=530&"
        "kw=%E6%95%B0%E6%8D%AE%E4%BA%A7%E5%93%81%E8%B4%9F%E8%B4%A3%E4%BA%BA"
    )


def test_build_zhilian_search_url_encodes_verified_shanghai_city_and_page():
    url = build_zhilian_search_url("AI 产品负责人", city="上海市", page=2)

    assert url == (
        "https://sou.zhaopin.com/?jl=538&"
        "kw=AI%20%E4%BA%A7%E5%93%81%E8%B4%9F%E8%B4%A3%E4%BA%BA&p=2"
    )


def test_build_zhilian_search_url_keeps_ui_fallback_for_unknown_city():
    url = build_zhilian_search_url("BI负责人", city="杭州", page=1)

    assert url == "https://sou.zhaopin.com/?kw=BI%E8%B4%9F%E8%B4%A3%E4%BA%BA"
