"""Claude calls for contract scanning and Intel drafts, on JSA's API key.

Uses Claude Opus 5.5 (CLAUDE_MODEL to change it). A contract photo plus the extraction prompt is
a few thousand tokens, so a scan costs a few cents. Server-side refusal fallbacks are on, so a
safety-classifier decline is retried on a fallback model inside the same call.
"""
import base64
import re

import anthropic

from . import config

_client = None
MEDIA = {'image/jpeg', 'image/png', 'image/webp', 'image/gif'}


class ClaudeError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def client():
    global _client
    if _client is None:
        if not config.ANTHROPIC_API_KEY:
            raise ClaudeError('not_configured', 'Contract scanning is not set up on this server yet.')
        _client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    return _client


def _image_block(data_url):
    m = re.match(r'^data:([\w/+.-]+);base64,(.*)$', data_url, re.S)
    if not m or m.group(1) not in MEDIA:
        raise ClaudeError('bad_request', 'Images must be JPEG, PNG, WebP or GIF.')
    data = re.sub(r'\s+', '', m.group(2))
    base64.b64decode(data, validate=True)
    return {'type': 'image', 'source': {'type': 'base64', 'media_type': m.group(1), 'data': data}}


def ask(messages, images=(), max_tokens=16000, effort='medium'):
    """messages: a string, or [{role, content}] ending with a user turn (the page's `sample` input).
    images: data URLs attached to the last user turn. Returns (text, truncated)."""
    if isinstance(messages, str):
        messages = [{'role': 'user', 'content': messages}]
    msgs = [{'role': m['role'], 'content': str(m['content'])} for m in messages if m.get('role') in ('user', 'assistant')]
    if not msgs or msgs[-1]['role'] != 'user':
        raise ClaudeError('bad_request', 'The last message must be from the user.')
    if images:
        msgs[-1]['content'] = [*(_image_block(u) for u in list(images)[:4]), {'type': 'text', 'text': msgs[-1]['content']}]
    try:
        r = client().beta.messages.create(
            model=config.CLAUDE_MODEL,
            max_tokens=max_tokens,
            output_config={'effort': effort},
            betas=['server-side-fallback-2026-07-01'],
            fallbacks='default',
            messages=msgs,
        )
    except anthropic.BadRequestError as e:
        raise ClaudeError('bad_request', e.message)
    except anthropic.RateLimitError:
        raise ClaudeError('rate_limited', 'Claude is busy. Try again in a minute.')
    except anthropic.APIStatusError as e:
        raise ClaudeError('unavailable', f'Claude returned an error ({e.status_code}). Try again shortly.')
    except anthropic.APIConnectionError:
        raise ClaudeError('unavailable', "Couldn't reach Claude. Try again shortly.")
    if r.stop_reason == 'refusal':
        raise ClaudeError('refused', 'Claude declined this request.')
    text = ''.join(b.text for b in r.content if getattr(b, 'type', '') == 'text')
    return text, r.stop_reason == 'max_tokens'
