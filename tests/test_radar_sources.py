import pytest
from paperagent.domain.store import DomainStore


def test_multisource_failure_dedup_cursor_and_offline(tmp_path):
    from paperagent.radar.sources import SourceBatch, OfflineSource
    from paperagent.radar.pipeline import LiteratureRadar
    store = DomainStore(tmp_path)
    store.add_standing_intent(description='watch', topic='research')
    item = dict(title='Research accuracy', doi='10.1234/a', source='fixture')
    calls = []
    class Pages:
        name = 'pages'
        def fetch(self, topic, since, cursor=None):
            calls.append((topic, since, cursor))
            return SourceBatch([item], next_cursor='next' if cursor is None else None,
                               coverage={'kind': 'publication_date', 'from': since})
    class Bad:
        name = 'bad'
        def fetch(self, *args): raise TimeoutError()
    radar = LiteratureRadar(store, sources=[Pages(), Bad(), OfflineSource([item])])
    assert radar.run()['new_hits'] == 1
    assert LiteratureRadar(store, sources=[Pages()]).run()['new_hits'] == 0
    assert calls[0][2] is None and calls[1][2] == 'next'
    assert calls[0][1] == calls[1][1]
    assert (store.dir/'radar_sources.json').exists()


def test_openalex_query_and_cursor(monkeypatch):
    from paperagent.radar import sources
    calls = []
    class Response:
        def raise_for_status(self): pass
        def json(self):
            return {'results': [{'display_name': 'Research', 'doi': None}], 'meta': {'next_cursor': 'abc'}}
    monkeypatch.setattr(sources, '_get', lambda url, **kw: (calls.append((url, kw)) or Response()))
    source = sources.OpenAlexSource()
    batch = source.fetch('research', '2024-01-01', 'token')
    params = calls[0][1]['params']
    assert params['search'] == 'research'
    assert params['filter'] == 'from_publication_date:2024-01-01'
    assert params['cursor'] == 'token'
    assert batch.next_cursor == 'abc'
    assert batch.items[0]['doi'] is None


def test_rss_current_window_and_topic_mapping(monkeypatch):
    from paperagent.radar import sources
    calls = []
    class Response:
        text = '''<rss><channel><pubDate>Sun, 01 Nov 2026 00:00:00 GMT</pubDate>
        <item><title>Machine learning evidence</title><link>https://arxiv.org/abs/2611.00001</link>
        <pubDate>Sun, 01 Nov 2026 00:00:00 GMT</pubDate></item></channel></rss>'''
        def raise_for_status(self): pass
    monkeypatch.setattr(sources, '_get', lambda url, **kw: (calls.append(url) or Response()))
    batch = sources.ArxivRSSSource().fetch('machine learning', '2020-01-01')
    assert calls == ['https://rss.arxiv.org/rss/cs.LG']
    assert len(batch.items) == 1
    assert batch.coverage['kind'] == 'current_feed_only'
    assert batch.next_cursor is None
    assert sources.ArxivRSSSource().fetch('unmapped topic', '2020-01-01').items == []


@pytest.mark.network
def test_live_arxiv():
    from paperagent.radar.sources import ArxivRSSSource
    batch = ArxivRSSSource().fetch('machine learning', '2026-01-01')
    assert batch.coverage['kind'] == 'current_feed_only'
