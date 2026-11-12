import pytest
from jobagent import cli
from jobagent.application import content_language, targeting
from jobagent.infra import cloud_client, state
from jobagent.infra.interaction_state import load_pending_interaction


@pytest.fixture
def local(tmp_path, monkeypatch):
    monkeypatch.setattr(state,'APP_DIR',tmp_path)
    monkeypatch.setattr(state,'STATE_DIR',tmp_path/'state')
    for module in (content_language,targeting):
        monkeypatch.setattr(module,'current_account_ref',lambda:'account-a')
    calls=[]
    preference={'content_locale':None,'confirmed':False}
    def transport(locale=None):
        calls.append(locale)
        if locale:preference.update(content_locale=locale,confirmed=True)
        return dict(preference)
    monkeypatch.setattr(cloud_client,'content_language',transport)
    return preference,calls


def test_first_language_card_precedes_geography_and_does_not_infer(local):
    targeting.from_args(cli.build_parser().parse_args(['round','start','--language','ko']))
    card=targeting.ensure()
    assert card['interaction']['kind']=='content_language'
    field=card['interaction']['fields'][0]
    assert field['default_option_ids']==[]
    assert [option['option_id'] for option in field['options']]==['en','zh','ja','ko','fr']
    assert local[1]==[None]
    args=cli.build_parser().parse_args(['interaction','respond','--interaction-id',card['interaction']['interaction_id'],'--choice','fr'])
    result=content_language.respond(args)
    assert result['content_locale']=='fr'
    assert local[0]=={'content_locale':'fr','confirmed':True}
    assert targeting.request()['interaction_language']=='fr'
    assert load_pending_interaction() is None
    next_card=targeting.ensure()
    assert next_card['interaction']['kind']=='target_region'
    assert next_card['interaction']['title']=='Lieu de travail souhaité'


def test_preference_from_workbench_reused_without_prompt(local):
    local[0].update(content_locale='ko',confirmed=True)
    assert content_language.ensure('jobagent round start') is None
    assert targeting.request()['content_locale']=='ko'
    assert load_pending_interaction() is None


def test_wrong_account_cannot_confirm_card(local,monkeypatch):
    card=content_language.ensure('jobagent round start')
    monkeypatch.setattr(content_language,'current_account_ref',lambda:'account-b')
    args=cli.build_parser().parse_args(['interaction','respond','--interaction-id',card['interaction']['interaction_id'],'--choice','fr'])
    assert content_language.respond(args)['error']=='interaction_context_changed'
    assert local[1]==[None]


def test_invalid_response_does_not_write_or_clear_card(local):
    card=content_language.ensure('jobagent round start')
    args=cli.build_parser().parse_args(['interaction','respond','--interaction-id',card['interaction']['interaction_id'],'--choice','confirm_all'])
    assert content_language.respond(args)['error']=='invalid_interaction_response'
    assert local[1]==[None]
    assert load_pending_interaction()['interaction_id']==card['interaction']['interaction_id']


def test_pending_card_is_resolved_by_explicit_workbench_choice(local):
    content_language.ensure('jobagent round start')
    local[0].update(content_locale='fr', confirmed=True)
    assert content_language.ensure('jobagent round start') is None
    assert load_pending_interaction() is None
    assert targeting.request()['content_locale'] == 'fr'
    assert local[1] == [None, None]


@pytest.mark.parametrize('locale', ['ja', 'ko', 'fr'])
def test_non_chinese_profiles_remain_compatible(locale):
    from jobagent.infra.profile_contract import profile_compatibility_issues
    assert profile_compatibility_issues({'qualitySignals': {'language': locale}}) == []
