import json
from pathlib import Path

from jobagent.platforms.boss import boss_job_id, parse_boss_job
from jobagent.platforms.job51 import job51_job_id, parse_job51_job
from jobagent.platforms.liepin import liepin_job_id, parse_liepin_job
from jobagent.platforms.zhilian import parse_zhilian_job, zhilian_job_id

FIXTURES = Path(__file__).parent / "fixtures"


def test_boss_public_parser_extracts_stable_identity():
    raw = json.loads((FIXTURES / "boss/search_joblist_page1.json").read_text())["zpData"][
        "jobList"
    ][0]
    job = parse_boss_job(raw, city_name="深圳")
    assert boss_job_id(raw) == "abc123"
    assert job.name == "AI产品经理"
    assert job.url.endswith("abc123.html")


def test_liepin_public_parser_extracts_stable_identity():
    payload = json.loads((FIXTURES / "liepin/search_joblist_page1.json").read_text())
    raw = payload["data"]["jobList"][0]
    job = parse_liepin_job(raw, city_name="深圳")
    assert liepin_job_id(raw)
    assert job.name and job.url


def test_zhilian_public_parser_extracts_stable_identity():
    payload = json.loads((FIXTURES / "zhilian/search_results_page1.json").read_text())
    raw = payload["data"]["results"][0]
    job = parse_zhilian_job(raw, city_name="深圳")
    assert zhilian_job_id(raw)
    assert job.name and job.url


def test_51job_public_parser_extracts_stable_identity():
    payload = json.loads((FIXTURES / "job51/search_results_page1.json").read_text())
    raw = payload["cards"][0]
    job = parse_job51_job(raw, city_name="深圳")
    assert job51_job_id(raw)
    assert job.name and job.url
