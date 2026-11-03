"""Bounded source pages. Publication-date scans and RSS are not update feeds."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Protocol
from urllib.parse import urlparse
from xml.etree import ElementTree as ET

import requests

from paperagent.tools import web
from paperagent.tools.literature_search import _openalex_to_candidate, run


@dataclass
class SourceBatch:
    items: list[dict]
    next_cursor: str | None = None
    coverage: dict = field(default_factory=dict)


class RadarSource(Protocol):
    name: str
    def fetch(self, topic: str, since: str, cursor: str | None = None) -> SourceBatch: ...


def _get(url, **kwargs):
    if not web._allowed_by_robots(url, 10):
        raise ValueError('Source disallows automated access')
    web._rate_limit(urlparse(url).netloc, 3.0 if 'arxiv.org' in url else 1.0)
    response = requests.get(url, timeout=15, headers={'User-Agent': web.DEFAULT_USER_AGENT}, **kwargs)
    response.raise_for_status()
    return response


class LiteratureSearchSource:
    name = 'literature_search'
    def __init__(self, search_fn=None, provider=None):
        self.search_fn = search_fn or run
        self.provider = provider

    def fetch(self, topic, since, cursor=None):
        query = {'query': topic, 'max_results': 5}
        if self.provider:
            query['provider'] = self.provider
        result = self.search_fn(query)
        if result.get('error'):
            raise ValueError('Literature search unavailable')
        return SourceBatch(result.get('candidates') or [], coverage={'kind': 'search_top_results'})


class OfflineSource:
    name = 'offline_stub'
    def __init__(self, items=None):
        self.items = items

    def fetch(self, topic, since, cursor=None):
        items = self.items
        if items is None:
            items = run({'query': topic, 'provider': 'offline_stub'})['candidates']
        return SourceBatch(items, coverage={'kind': 'offline_fixture'})


class OpenAlexSource:
    name = 'openalex_incremental'
    def fetch(self, topic, since, cursor=None):
        params = {'search': topic, 'filter': 'from_publication_date:' + since,
                  'per_page': 100, 'cursor': cursor or '*', 'sort': 'publication_date:asc'}
        key = os.getenv('PAPERAGENT_OPENALEX_API_KEY', '').strip()
        if key:
            params['api_key'] = key
        data = _get('https://api.openalex.org/works', params=params).json()
        items = [_openalex_to_candidate(row).model_dump() for row in data['results']]
        return SourceBatch(items, (data.get('meta') or {}).get('next_cursor'),
                           {'kind': 'publication_date', 'from': since,
                            'note': 'Not updated-date sync; deep paging requires an API key'})


class ArxivRSSSource:
    name = 'arxiv_rss'
    CATEGORIES = {'machine learning': 'cs.LG', '机器学习': 'cs.LG',
                  'artificial intelligence': 'cs.AI', '人工智能': 'cs.AI',
                  'llm': 'cs.CL', 'language model': 'cs.CL', '大语言模型': 'cs.CL',
                  'quantum': 'quant-ph', '量子': 'quant-ph', 'statistics': 'stat', '统计': 'stat'}

    def fetch(self, topic, since, cursor=None):
        category = next((v for k, v in self.CATEGORIES.items() if k in topic.lower()), None)
        if re.fullmatch(r'(cs|math|stat|physics)\.[A-Z]{2}|quant-ph|astro-ph', topic):
            category = topic
        coverage = {'kind': 'current_feed_only', 'requested_since': since,
                    'note': 'RSS has no historical pagination; gaps cannot be backfilled'}
        if not category:
            return SourceBatch([], coverage=coverage | {'reason': 'no_category_mapping'})
        url = 'https://rss.arxiv.org/rss/' + category
        root = ET.fromstring(_get(url).text)
        channel = root.find('channel')
        if channel is None:
            raise ValueError('Invalid RSS channel')
        coverage.update(url=url, feed_date=channel.findtext('pubDate'),
                        fetched_at=datetime.now(timezone.utc).isoformat())
        items = []
        for item in channel.findall('item'):
            title, link = item.findtext('title') or '', item.findtext('link') or ''
            date = item.findtext('pubDate') or channel.findtext('pubDate')
            try:
                published = parsedate_to_datetime(date).date().isoformat() if date else None
            except (ValueError, TypeError):
                published = None
            if published and published < since:
                continue
            author = item.findtext('{http://purl.org/dc/elements/1.1/}creator') or ''
            items.append(dict(title=title, url=link, source='arxiv_rss',
                              authors=[author] if author else [], year=int(published[:4]) if published else None,
                              doi=None))
        return SourceBatch(items, coverage=coverage)


def configured_sources(search_fn=None, provider=None):
    # Explicit provider / injected search preserves callers' offline guarantees.
    if search_fn is not None or provider is not None:
        return [LiteratureSearchSource(search_fn, provider)]
    factories = {'literature_search': LiteratureSearchSource, 'arxiv_rss': ArxivRSSSource,
                 'openalex_incremental': OpenAlexSource, 'offline_stub': OfflineSource}
    names = [s.strip() for s in os.getenv('PAPERAGENT_RADAR_SOURCES', 'literature_search').split(',') if s.strip()]
    unknown = set(names) - factories.keys()
    if unknown:
        raise ValueError('Unknown Radar source configuration')
    return [factories[name]() for name in dict.fromkeys(names)]
