import pytest
from concurrent.futures import ThreadPoolExecutor
from fastapi import HTTPException
from fastapi.testclient import TestClient
from paperagent_cloud import app as cloud


@pytest.fixture
def usage(tmp_path, monkeypatch):
    monkeypatch.setenv('PAPERAGENT_CLOUD_DB',str(tmp_path/'usage.sqlite'))
    cloud.init_db()
    from paperagent_cloud import usage
    return usage


def test_usage_idempotency_storage_and_concurrent_reservation(usage):
    usage.record_usage('u','radar_runs',1,'one')
    usage.record_usage('u','radar_runs',1,'one')
    assert usage.summary('u')['totals']['radar_runs'] == 1
    with pytest.raises(HTTPException): usage.record_usage('u','radar_runs',2,'one')
    usage.record_usage('u','storage_bytes',10,'upload')
    usage.record_usage('u','storage_bytes',-4,'delete')
    assert usage.summary('u')['totals']['storage_bytes'] == 6
    with cloud.db() as c:
        c.execute('INSERT INTO quotas VALUES(?,?,?)',('u','llm_input_tokens',10))
    def reserve(i):
        try: usage.reserve('u',str(i),{'llm_input_tokens':7}); return True
        except HTTPException as exc: assert exc.status_code == 429; return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(pool.map(reserve,range(2))) == 1


def test_cloud_quota_blocks_before_http_and_usage_is_user_scoped(usage, monkeypatch):
    from paperagent.harness.llm import LLMClient
    with TestClient(cloud.app) as client:
        auth=client.post('/api/v1/auth/register',json={'username':'quota','password':'test-password-123'}).json()
        headers={'Authorization':'Bearer '+auth['token']}
        sid=client.post('/api/v1/spaces',headers=headers,json={'name':'test'}).json()['id']
        monkeypatch.setattr(cloud,'LLMClient',lambda: LLMClient(base_url='https://example.invalid',api_key='test',model='test',protocol='openai'))
        calls=[]
        class Response:
            status_code=200
            def json(self): return {'usage':{'prompt_tokens':12,'completion_tokens':3},'choices':[{'message':{'content':'{"reply":"done"}'}}]}
        monkeypatch.setattr('paperagent.harness.llm.requests.post',lambda *a,**k: calls.append(k) or Response())
        with cloud.db() as c: c.execute('INSERT INTO quotas VALUES(?,?,?)',(auth['user_id'],'llm_input_tokens',1))
        path=f'/api/v1/spaces/{sid}/messages'
        assert client.post(path,headers=headers,json={'text':'hello','request_id':'usage_test'}).status_code == 429
        assert calls == []
        with cloud.db() as c: c.execute('DELETE FROM quotas')
        assert client.post(path,headers=headers,json={'text':'hello','request_id':'usage_test'}).status_code == 200
        assert client.post(path,headers=headers,json={'text':'hello','request_id':'usage_test'}).status_code == 200
        result=client.get('/api/v1/usage/summary',headers=headers).json()
        assert result['totals']['llm_input_tokens']==12 and result['totals']['llm_output_tokens']==3
        assert len(calls)==1 and calls[0]['json']['max_tokens']>0
        assert client.get('/api/v1/usage/summary').status_code == 401


def test_storage_release_and_old_reservation_remain_inventory(usage):
    usage.record_usage('u','storage_bytes',100,'upload')
    with cloud.db() as c: c.execute('INSERT INTO quotas VALUES(?,?,?)',('u','storage_bytes',50))
    usage.record_usage('u','storage_bytes',-10,'release')
    assert usage.summary('u')['totals']['storage_bytes']==90
    with cloud.db() as c: c.execute('DELETE FROM quotas')
    usage.reserve('other','upload-old',{'storage_bytes':8})
    with cloud.db() as c:
        c.execute("UPDATE usage_reservations SET month='2000-01' WHERE user_id='other'")
        c.execute('INSERT INTO quotas VALUES(?,?,?)',('other','storage_bytes',10))
    with pytest.raises(HTTPException) as caught: usage.record_usage('other','storage_bytes',5,'new')
    assert caught.value.status_code==429


def test_startup_reconciles_uncertain_reservations_to_visible_estimates(usage):
    usage.reserve('u','interrupted',{'llm_input_tokens':10})
    cloud.startup()
    first=usage.summary('u')
    assert first['reserved']['llm_input_tokens']==0
    assert first['totals']['llm_input_tokens']==10
    assert first['estimated_events']==1
    cloud.startup()
    assert usage.summary('u')==first


def test_anthropic_callback_includes_cached_input(monkeypatch):
    from paperagent.harness.llm import LLMClient
    class Response:
        status_code=200
        def json(self):
            return {'usage':{'input_tokens':10,'cache_read_input_tokens':5,'cache_creation_input_tokens':7,'output_tokens':2},
                    'content':[{'type':'text','text':'{"ok":true}'}]}
    monkeypatch.setattr('paperagent.harness.llm.requests.post',lambda *a,**k:Response())
    received=[]
    client=LLMClient(base_url='https://test.invalid',api_key='test',model='test',protocol='anthropic',usage_callback=received.append)
    assert client.chat_json('system','user')=={'ok':True}
    assert received==[{'llm_input_tokens':22,'llm_output_tokens':2}]
