"""Machine translation for the console's "Auto-translate" button.

Keys and URLs are set in the console (Organization > Backend); each falls back to
the environment variable named below. Providers, tried in this order:

0. Your own LibreTranslate server, if LIBRETRANSLATE_URL is set (optionally
   LIBRETRANSLATE_API_KEY). Free and private; falls back to the chain below if it's down.
1. Google Cloud Translation API, if GOOGLE_TRANSLATE_API_KEY is set (paid, reliable).
2. DeepL API, if DEEPL_API_KEY is set (free tier: 500k characters/month, best quality).
3. Google Translate's free web endpoint via deep-translator. Google blocks it per
   network with a CAPTCHA when it sees automated traffic; when that happens it is
   skipped for an hour.
4. MyMemory via deep-translator (no key). Roughly 5,000 characters/day per IP,
   or 50,000/day if MYMEMORY_EMAIL is set.
"""
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

CLOUD_URL = 'https://translation.googleapis.com/language/translate/v2'
GOOGLE_BLOCK_SECONDS = 60 * 60
MYMEMORY_MAX_CHARS = 450  # The service rejects more than 500 per request
MYMEMORY_LOCALES = {'en': 'en-GB', 'es': 'es-ES', 'fr': 'fr-FR', 'uk': 'uk-UA'}

_google_blocked_until = 0.0


class TranslationError(Exception):
    pass


def config():
    """Translation settings: console values first, then environment variables."""
    from base.models import SiteSettings

    site = SiteSettings.get_settings()
    return {
        'libretranslate_url': site.backend_value('libretranslate_url', 'LIBRETRANSLATE_URL'),
        'libretranslate_api_key': site.backend_value('libretranslate_api_key', 'LIBRETRANSLATE_API_KEY'),
        'google_translate_api_key': site.backend_value('google_translate_api_key', 'GOOGLE_TRANSLATE_API_KEY'),
        'deepl_api_key': site.backend_value('deepl_api_key', 'DEEPL_API_KEY'),
        'mymemory_email': site.backend_value('mymemory_email', 'MYMEMORY_EMAIL'),
    }


def translate_texts(texts, source, target):
    """Translate a list of strings from `source` to `target` language codes."""
    return translate_with_provider(texts, source, target)[0]


def translate_with_provider(texts, source, target):
    """(translations, name of the provider that produced them)."""
    if not texts:
        return [], None
    cfg = config()
    if cfg['libretranslate_url']:
        try:
            return _translate_libre(texts, source, target, cfg['libretranslate_url'], cfg['libretranslate_api_key']), 'LibreTranslate'
        except TranslationError as exc:
            print(f'[auto-translate] LibreTranslate failed ({exc}); falling back to other providers.')
    if cfg['google_translate_api_key']:
        return _translate_cloud(texts, source, target, cfg['google_translate_api_key']), 'Google Cloud Translation'
    if cfg['deepl_api_key']:
        return _translate_deepl(texts, source, target, cfg['deepl_api_key']), 'DeepL'

    global _google_blocked_until
    if time.time() >= _google_blocked_until:
        try:
            return _translate_google_free(texts, source, target), 'Google Translate (free)'
        except GoogleBlocked:
            _google_blocked_until = time.time() + GOOGLE_BLOCK_SECONDS
            print('[auto-translate] Google is blocking this network; using MyMemory for the next hour.')
        except TranslationError as exc:
            print(f'[auto-translate] Google failed ({exc}); falling back to MyMemory.')
    return _translate_mymemory(texts, source, target, cfg['mymemory_email']), 'MyMemory'


def google_free_blocked():
    """Seconds left on a remembered Google CAPTCHA block (0 if none)."""
    return max(0, int(_google_blocked_until - time.time()))


# ---------------------------------------------------------------------------
# Self-hosted and keyed providers
# ---------------------------------------------------------------------------

def _translate_libre(texts, source, target, base_url, api_key=''):
    """One POST to LibreTranslate's /translate with every line of every text.

    Lines are sent separately (with Markdown prefixes held back) so headings
    and lists keep their structure, as with MyMemory.
    """
    segments = [_split_markdown_lines(text) for text in texts]
    bodies = [body for lines in segments for kind, _prefix, body in lines if kind == 'text']
    if not bodies:
        return list(texts)

    payload = {'q': bodies, 'source': source, 'target': target, 'format': 'text'}
    if api_key:
        payload['api_key'] = api_key
    request = urllib.request.Request(
        base_url.rstrip('/') + '/translate', data=json.dumps(payload).encode(),
        # A real User-Agent: Cloudflare-fronted servers reject urllib's default with error 1010.
        headers={'Content-Type': 'application/json', 'User-Agent': 'TownHall/1.0 (auto-translate)'},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            result = json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors='replace')[:300]
        raise TranslationError(f'LibreTranslate {exc.code}: {detail}') from exc
    except (urllib.error.URLError, ValueError, TimeoutError) as exc:
        raise TranslationError(f'LibreTranslate unreachable at {base_url}: {exc}') from exc

    translated = result.get('translatedText')
    if isinstance(translated, str):
        translated = [translated]
    if not isinstance(translated, list) or len(translated) != len(bodies):
        raise TranslationError(f'LibreTranslate returned an unexpected response: {str(result)[:200]}')

    remaining = iter(translated)
    return [
        ''.join(part if kind == 'raw' else prefix + next(remaining) for kind, prefix, part in lines)
        for lines in segments
    ]


def _translate_cloud(texts, source, target, api_key):
    body = urllib.parse.urlencode(
        [('q', t) for t in texts] + [('source', source), ('target', target), ('format', 'text'), ('key', api_key)]
    ).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(CLOUD_URL, data=body), timeout=15) as response:
            payload = json.loads(response.read().decode())
    except (urllib.error.URLError, ValueError) as exc:
        raise TranslationError(f'Google Cloud Translation: {exc}') from exc
    return [item['translatedText'] for item in payload['data']['translations']]


def _translate_deepl(texts, source, target, api_key):
    from deep_translator import DeeplTranslator

    try:
        # Free-tier keys end in ":fx" and use a different host.
        translator = DeeplTranslator(api_key=api_key, source=source, target=target,
                                     use_free_api=api_key.endswith(':fx'))
        return [translator.translate(text) or text for text in texts]
    except Exception as exc:
        raise TranslationError(f'DeepL: {exc or exc.__class__.__name__}') from exc


# ---------------------------------------------------------------------------
# Free providers
# ---------------------------------------------------------------------------

class GoogleBlocked(TranslationError):
    """Google answered with its /sorry/ "unusual traffic" CAPTCHA page."""


def _translate_google_free(texts, source, target):
    from deep_translator import GoogleTranslator

    try:
        translator = GoogleTranslator(source=source, target=target)
        # One request per text keeps line breaks intact; translate_batch does the same internally.
        return [translator.translate(text) or text for text in texts]
    except Exception as exc:  # deep-translator raises a variety of request/parse errors
        if _print_google_response(translator, texts[0], source, target) == 'captcha':
            raise GoogleBlocked('Google is showing its "unusual traffic" CAPTCHA to this network.') from exc
        raise TranslationError(str(exc) or exc.__class__.__name__) from exc


def _print_google_response(translator, text, source, target):
    """Debug aid: deep-translator discards Google's response before raising, so
    repeat the exact request it made once and print what Google sent back.

    Returns 'captcha' when Google redirected to its /sorry/ abuse page.
    """
    import requests

    params = {'tl': target, 'sl': source, 'q': text}
    try:
        response = requests.get(translator._base_url, params=params, timeout=10)
    except requests.RequestException as exc:
        print(f'[auto-translate] request to Google failed outright: {exc!r}')
        return None
    first = response.history[0] if response.history else response
    print('[auto-translate] Google Translate response')
    print(f'  request:      GET {first.request.url}')
    for hop in response.history:
        print(f'  redirect:     {hop.status_code} -> {hop.headers.get("Location")}')
    print(f'  status:       {response.status_code} {response.reason}')
    print(f'  final url:    {response.url}')
    for header in ('Retry-After', 'Content-Type', 'Server'):
        if header in response.headers:
            print(f'  {header + ":":<13} {response.headers[header]}')
    print(f'  body (first 600 chars):\n{response.text[:600]}')
    return 'captcha' if '/sorry/' in response.url else None


def _translate_mymemory(texts, source, target, email=''):
    from deep_translator import MyMemoryTranslator

    try:
        translator = MyMemoryTranslator(
            source=MYMEMORY_LOCALES.get(source, source), target=MYMEMORY_LOCALES.get(target, target),
            email=email or None,
        )
    except Exception as exc:
        raise TranslationError(f'MyMemory does not support {source} -> {target}: {exc}') from exc

    def translate_piece(piece):
        result = translator.translate(piece) or piece
        if result.startswith('MYMEMORY WARNING'):
            raise TranslationError(
                "MyMemory's free daily limit is used up for this network. Try again tomorrow, set "
                'MYMEMORY_EMAIL for a higher limit, or set DEEPL_API_KEY.'
            )
        return result

    try:
        return [_translate_in_chunks(text, translate_piece) for text in texts]
    except TranslationError:
        raise
    except Exception as exc:
        raise TranslationError(f'MyMemory: {exc or exc.__class__.__name__}') from exc


def _split_markdown_lines(text):
    """[(kind, prefix, part)]: 'raw' for line breaks/blank lines, 'text' for a line's
    translatable words with its Markdown marker (heading, bullet, number, quote) held in prefix."""
    out = []
    for part in re.split(r'(\n)', text):
        if part == '\n' or not part.strip():
            out.append(('raw', '', part))
        else:
            prefix, body = MARKDOWN_PREFIX.match(part).groups()
            out.append(('text', prefix, body))
    return out


def _translate_in_chunks(text, translate_piece):
    """Translate line by line (and sentence by sentence for long lines), keeping the
    original line breaks so Markdown lists, headings and paragraphs survive."""
    out = []
    for kind, prefix, body in _split_markdown_lines(text):
        if kind == 'raw':
            out.append(body)
        elif len(body) <= MYMEMORY_MAX_CHARS:
            out.append(prefix + translate_piece(body))
        else:
            out.append(prefix + ' '.join(translate_piece(chunk) for chunk in _sentence_chunks(body)))
    return ''.join(out)


MARKDOWN_PREFIX = re.compile(r'^(\s*(?:#{1,6}\s+|[-*+]\s+|\d+[.)]\s+|>\s*)*)(.*)$', re.DOTALL)


def _sentence_chunks(line):
    """Group sentences into chunks under the per-request limit."""
    chunks, current = [], ''
    for sentence in re.split(r'(?<=[.!?])\s+', line):
        while len(sentence) > MYMEMORY_MAX_CHARS:  # One very long sentence: hard split on spaces
            cut = sentence.rfind(' ', 0, MYMEMORY_MAX_CHARS)
            if cut <= 0:
                cut = MYMEMORY_MAX_CHARS
            chunks.append(sentence[:cut])
            sentence = sentence[cut:].lstrip()
        if current and len(current) + 1 + len(sentence) > MYMEMORY_MAX_CHARS:
            chunks.append(current)
            current = sentence
        else:
            current = f'{current} {sentence}'.strip()
    if current:
        chunks.append(current)
    return chunks
