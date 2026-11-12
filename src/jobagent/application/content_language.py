"""One explicit account preference, independent of UI and job geography."""
from __future__ import annotations

import shlex
import uuid

from jobagent.application import targeting
from jobagent.infra import cloud_client
from jobagent.infra.account_state import current_account_ref
from jobagent.infra.interaction_protocol import build_host_presentations, build_interaction_required
from jobagent.infra.interaction_state import clear_pending_interaction, load_pending_interaction, save_pending_interaction

LANGUAGES = {'en': 'English', 'zh': '简体中文', 'ja': '日本語', 'ko': '한국어', 'fr': 'Français'}


def _remember(locale):
    value = targeting.request()
    value['content_locale'] = locale
    value['interaction_language'] = 'zh-CN' if locale == 'zh' else locale
    targeting._save_request(value)


def _present(pending):
    card = pending['interaction']
    return {'ok': False, 'error': 'interaction_required', 'requires_user_action': True,
            'interaction': card, 'host_presentations': build_host_presentations(card),
            'user_prompt': card['fallback_text'], 'request_preserved': True,
            'onboarding': {'stage': 'content_language'},
            'next_suggested': f"jobagent interaction respond --interaction-id {shlex.quote(card['interaction_id'])} --choice <language>"}


def ensure(continuation):
    waiting = load_pending_interaction()
    preference = cloud_client.content_language()
    locale = preference.get('content_locale')
    if locale in LANGUAGES and preference.get('confirmed') is True:
        if waiting and waiting.get('stage') == 'content_language':
            if (waiting.get('context') or {}).get('account_ref') != current_account_ref():
                return {'ok': False, 'error': 'interaction_context_changed', 'request_preserved': True}
            clear_pending_interaction()
        _remember(locale)
        return None
    if locale is not None or preference.get('confirmed') is not False:
        raise ValueError('The service did not return a verified content-language preference')
    if waiting and waiting.get('stage') == 'content_language':
        return _present(waiting)
    # Retain any existing setup answer/card. It will be restored by its owner
    # after this prerequisite finishes; never overwrite an in-flight task card.
    if waiting:
        return None
    ident = 'jobagent:content-language:' + uuid.uuid4().hex
    prompt = ('Choose the language for Job Agent content and interviews. '
              'This is independent of page language and job location. '
              'You can change it later in Job Agent Settings.')
    fields = [{'field_id': 'content_language', 'type': 'single', 'label': 'Content and interview language',
               'required': True, 'allow_other': False, 'default_option_ids': [],
               'options': [{'option_id': key, 'label': label} for key, label in LANGUAGES.items()]}]
    card = build_interaction_required(interaction_id=ident, product_id='job_agent', kind='content_language',
        title='Content and interview language', prompt=prompt, fields=fields,
        fallback_text=prompt+'\n'+' · '.join(f'{key}: {name}' for key, name in LANGUAGES.items()),
        continuation_action='jobagent.interaction.respond', idempotency_key=ident)
    return _present(save_pending_interaction(card, stage='content_language',
        context={'account_ref': current_account_ref(), 'continuation': continuation}))


def respond(args):
    ident = str(args.interaction_id or '')
    if not ident.startswith('jobagent:content-language:'):
        return None
    waiting = load_pending_interaction() or {}
    if waiting.get('interaction_id') != ident or waiting.get('stage') != 'content_language':
        return {'ok': False, 'error': 'interaction_not_pending', 'request_preserved': True}
    context = waiting.get('context') or {}
    if not context.get('account_ref') or context['account_ref'] != current_account_ref():
        return {'ok': False, 'error': 'interaction_context_changed', 'request_preserved': True}
    if args.choice not in LANGUAGES or any(getattr(args, key, None) for key in
            ('resume_id', 'attachment_id', 'target_location', 'target_locations', 'target_role', 'target_city', 'exclude_index', 'location_id', 'radius_id')):
        return {'ok': False, 'error': 'invalid_interaction_response'}
    preference = cloud_client.content_language(args.choice)
    if preference.get('content_locale') != args.choice or preference.get('confirmed') is not True:
        raise ValueError('Content language confirmation was not acknowledged')
    _remember(args.choice)
    clear_pending_interaction()
    return {'ok': True, 'event': 'content_language_saved', 'content_locale': args.choice,
            'request_preserved': True, 'next_suggested': context['continuation']}
