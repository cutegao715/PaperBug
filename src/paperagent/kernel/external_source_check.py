"""Provider-scoped metadata checks; never equate absence with fabrication."""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

import requests

from paperagent.tools.references import run_reference_resolve
from paperagent.tools.web import _rate_limit
from paperagent.workspace.state_store import read_json, write_json, state_lock

POLICY = 'metadata-v1'
TTL = 30 * 86400


def normalize(text):
    return unicodedata.normalize('NFKC', str(text or '')).casefold().strip()


def doi_value(value):
    return re.sub(r'^(https?://(dx\.)?doi\.org/|doi:\s*)', '', normalize(value))


def fingerprint(ref, provider='crossref'):
    data = [doi_value(ref.doi), normalize(ref.title), [normalize(a) for a in ref.authors],
            ref.year, provider, POLICY]
    return hashlib.sha256(json.dumps(data, ensure_ascii=False).encode()).hexdigest()


def strict_enabled():
    return os.getenv('PAPERAGENT_REFERENCE_STRICT', '').lower() in {'1', 'true', 'yes'}


def current_result(ref):
    result = ref.verification or {}
    if result.get('fingerprint') != fingerprint(ref, result.get('provider', '')):
        return {}
    try:
        age = time.time() - datetime.fromisoformat(result['checked_at']).timestamp()
        return result if 0 <= age < TTL else {}
    except (KeyError, ValueError, TypeError):
        return {}


def _tokens(text):
    return set(re.findall(r'\w+', normalize(text)))


def _surname(name):
    text = normalize(name)
    return text.split(',')[0].strip() if ',' in text else (text.split() or [''])[-1]


class ExternalSourceChecker:
    def __init__(self, directory, provider='crossref', resolver=None, min_delay=1.0):
        self.path = Path(directory) / '.verification_cache.json'
        self.provider = provider
        self.resolver = resolver or run_reference_resolve
        self.min_delay = min_delay

    def check(self, ref):
        key = fingerprint(ref, self.provider)
        result = dict(status='unchecked', checked_at=datetime.now(timezone.utc).isoformat(),
                      provider=self.provider, fingerprint=key, policy=POLICY)
        if self.provider != 'crossref' or ref.source == 'offline_stub':
            return result | {'reason': 'offline_or_unsupported_provider'}
        with state_lock(self.path):
            cache = read_json(self.path)
            cached = cache.get(key)
            if cached and 0 <= time.time() - cached['time'] < (60 if cached['result']['status'] == 'unchecked' else TTL):
                return cached['result']
            _rate_limit('api.crossref.org', max(0, self.min_delay))
            try:
                response = self.resolver({'doi': doi_value(ref.doi), 'title': ref.title,
                                          'provider': self.provider})
                result.update(self._compare(ref, response))
            except requests.HTTPError as exc:
                status = exc.response.status_code if exc.response is not None else None
                if status == 404 and ref.doi:
                    result.update(status='not_found', explicit_doi_not_found=True,
                                  reason='DOI absent from Crossref; other registration agencies may hold it')
                else:
                    result.update(reason='provider_http_error', http_status=status)
            except (requests.RequestException, ValueError, TypeError, KeyError, AttributeError, IndexError):
                result.update(reason='provider_unavailable_or_invalid_response')
            cache = {k: v for k, v in cache.items() if time.time() - v['time'] < TTL}
            cache[key] = {'time': time.time(), 'result': result}
            write_json(self.path, cache)
        return result

    def _compare(self, ref, response):
        if response.get('error') or 'synthetic' in response.get('provenance', '').lower():
            return {'status': 'unchecked', 'reason': 'untrusted_provider_response'}
        candidates = response.get('candidates')
        if not isinstance(candidates, list):
            return {'status': 'unchecked', 'reason': 'invalid_provider_response'}
        if not candidates:
            return {'status': 'not_found', 'reason': 'no provider search result'}
        title = _tokens(ref.title)
        def score(candidate):
            other = _tokens(candidate.get('title'))
            return len(title & other) / max(1, len(title | other))
        best = max(candidates, key=score)
        if best.get('source') == 'offline_stub':
            return {'status': 'unchecked', 'reason': 'synthetic_metadata'}
        if not ref.authors or not best.get('authors') or ref.year is None or best.get('year') is None:
            return {'status': 'unchecked', 'reason': 'missing_author_or_year'}
        diff = {}
        if score(best) < .85:
            diff['title'] = {'expected': ref.title, 'found': best.get('title')}
        years = best.get('publication_years') or [best['year']]
        if ref.year not in years:
            diff['year'] = {'expected': ref.year, 'found': years}
        if _surname(ref.authors[0]) != _surname(best['authors'][0]):
            diff['author'] = {'expected': ref.authors[0], 'found': best['authors'][0]}
        if ref.doi and doi_value(ref.doi) != doi_value(best.get('doi')):
            diff['doi'] = {'expected': ref.doi, 'found': best.get('doi')}
        return {'status': 'mismatch' if diff else 'verified', 'diff': diff}


def check_reference(ws, ref):
    provider = os.getenv('PAPERAGENT_REFERENCE_PROVIDER', 'offline_stub')
    return ref.model_copy(update={'verification': ExternalSourceChecker(ws.dir, provider).check(ref)})


def verify_workspace(ws):
    """Refresh metadata only, preserving concurrently added references and unknown fields."""
    from paperagent.quality.policies import effective_role
    if effective_role() != 'interactive':
        return ws.list_references()
    refs = [check_reference(ws, ref) for ref in ws.list_references()]
    if not refs:
        return refs
    checked = {r.ref_id: r for r in refs}
    with state_lock(ws.references_path):
        rows = [json.loads(line) for line in ws.references_path.read_text(encoding='utf-8').splitlines() if line.strip()]
        from paperagent.schemas.references import Reference
        for row in rows:
            ref = checked.get(row['ref_id'])
            if ref and fingerprint(Reference.model_validate(row)) == fingerprint(ref):
                row['verification'] = ref.verification
        import uuid
        tmp = ws.references_path.with_name('.references.' + uuid.uuid4().hex + '.tmp')
        try:
            with tmp.open('w', encoding='utf-8') as out:
                out.write(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in rows))
                out.flush(); os.fsync(out.fileno())
            tmp.replace(ws.references_path)
        finally:
            tmp.unlink(missing_ok=True)
    return ws.list_references()
