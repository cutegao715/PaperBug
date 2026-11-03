"""Project-scoped, explicitly enabled lifecycle synchronization only."""
import os
from urllib.parse import urlsplit
import requests
from paperagent.workspace.state_store import read_json, write_json, state_lock
from paperagent.domain.entities import iso, utcnow


def cloud_origin(value):
    parts = urlsplit(value)
    if parts.username or parts.password or parts.query or parts.fragment or parts.path not in ('','/'):
        raise ValueError('Cloud URL must be an origin without credentials or path')
    if parts.scheme != 'https' and not (parts.scheme == 'http' and parts.hostname in ('localhost','127.0.0.1','::1')):
        raise ValueError('Cloud requires HTTPS (except loopback)')
    if not parts.hostname:
        raise ValueError('Cloud host required')
    return value.rstrip('/')


class CloudSync:
    def __init__(self, ws, transport=None):
        self.ws = ws
        self.path = ws.dir / 'meta.json'
        self.transport = transport or self._request

    def _request(self, method, origin, path, body=None, extra_headers=None):
        # Credentials are deliberately not stored in the project or sent to arbitrary origins.
        configured = cloud_origin(os.environ.get('PAPERAGENT_CLOUD_URL',''))
        if origin != configured:
            raise ValueError('Cloud URL differs from configured credential origin')
        token = os.environ.get('PAPERAGENT_CLOUD_TOKEN','')
        device = os.environ.get('PAPERAGENT_CLOUD_DEVICE_TOKEN','')
        if not token or not device:
            raise ValueError('Cloud login and device credentials required')
        account = read_json(self.ws.root/'account.json')
        binding = read_json(self.path)
        expected_user = account.get('user_id') or binding.get('user_id')
        if account and account.get('origin') != origin:
            raise ValueError('Account belongs to a different cloud origin')
        if expected_user and path != '/api/v1/auth/me':
            identity = requests.get(origin+'/api/v1/auth/me', headers={'Authorization':'Bearer '+token},
                                    timeout=10, allow_redirects=False)
            if identity.status_code != 200 or identity.json().get('user_id') != expected_user:
                raise ValueError('Cloud credentials belong to another account')
        extra_headers = extra_headers or {}
        if set(extra_headers) - {'X-Lease-Token'}:
            raise ValueError('Unsupported transport header')
        response = requests.request(method, origin + path, json=body,
            headers={'Authorization':'Bearer '+token,'X-Device-Token':device, **extra_headers}, timeout=10, allow_redirects=False)
        if response.status_code >= 300:
            raise RuntimeError(f'Cloud request failed ({response.status_code})')
        return response.json()

    def metadata(self):
        data = read_json(self.path)
        if not data:
            return None
        if data.get('project_id') != self.ws.project_id or data.get('workspace_path') != str(self.ws.dir.resolve()):
            raise ValueError('Binding does not match this project directory')
        for key in ('space_id','device_id','cloud_url'):
            if not isinstance(data.get(key),str) or not data[key]:
                raise ValueError('Incomplete project binding')
        if data.get('sync_mode') not in ('local_only','cloud_sync'):
            raise ValueError('Invalid sync mode')
        cloud_origin(data['cloud_url'])
        return data

    def challenge(self, cloud_url, space_id, device_id):
        origin = cloud_origin(cloud_url)
        with state_lock(self.path):
            if self.metadata():
                raise ValueError('Project already bound; disable synchronization before any manual migration')
            return self.transport('POST',origin,f'/api/v1/spaces/{space_id}/bind_challenge',
                {'device_id':device_id,'project_id':self.ws.project_id})

    def bind(self, cloud_url, space_id, device_id, challenge, confirmed=False):
        if not confirmed:
            raise ValueError('Explicit project binding confirmation required')
        origin = cloud_origin(cloud_url)
        with state_lock(self.path):
            if self.metadata():
                raise ValueError('Project already bound')
            result = self.transport('POST',origin,f'/api/v1/spaces/{space_id}/bind_confirm',
                {'device_id':device_id,'project_id':self.ws.project_id,'challenge':challenge})
            if (result.get('space_id'),result.get('device_id'),result.get('project_id')) != (space_id,device_id,self.ws.project_id):
                raise ValueError('Cloud confirmed a different binding')
            data = {**result,'cloud_url':origin,'workspace_path':str(self.ws.dir.resolve()),
                    'bound_at':iso(utcnow()),'sync_mode':'local_only'}
            write_json(self.path,data)
            return data

    def set_mode(self, mode, confirmed=False):
        if mode not in ('local_only','cloud_sync') or (mode == 'cloud_sync' and not confirmed):
            raise ValueError('Enabling cloud synchronization requires explicit confirmation')
        with state_lock(self.path):
            data = self.metadata()
            if not data:
                raise ValueError('Project is not bound')
            # Local disable succeeds even offline and prevents any further background network calls.
            if mode == 'local_only':
                data['sync_mode'] = mode
                data['cloud_mode_pending'] = True
                write_json(self.path,data)
                try:
                    result = self.transport('POST',data['cloud_url'],f"/api/v1/spaces/{data['space_id']}/sync",
                                            {'sync_mode':'local_only'})
                    data['cloud_mode_pending'] = result.get('sync_mode') != 'local_only'
                except Exception:
                    pass  # Never undo local disable because the cloud is unavailable.
                write_json(self.path,data)
                return data
            result = self.transport('POST',data['cloud_url'],f"/api/v1/spaces/{data['space_id']}/sync",
                                    {'sync_mode':mode,'confirmed':True})
            if result.get('sync_mode') != mode:
                raise ValueError('Cloud did not enable synchronization')
            data['sync_mode'] = mode
            data['cloud_mode_pending'] = False
            write_json(self.path,data)
            return data

    def set_preferences(self, *, intents=False, profile_intents=False, notifications=False, confirmed=False):
        if (intents or profile_intents or notifications) and not confirmed:
            raise ValueError('Explicit synchronization scope confirmation required')
        if profile_intents and not intents:
            raise ValueError('Enable intent sync before profile intents')
        with state_lock(self.ws.root/'domain'/'sync_preferences.lock'), state_lock(self.path):
            data=self.metadata()
            if not data: raise ValueError('Project is not bound')
            if notifications:
                for other in (self.ws.root/'projects').glob('*/meta.json'):
                    if other!=self.path and read_json(other).get('sync_preferences',{}).get('notifications'):
                        raise ValueError('Only one notification relay destination may be enabled per workspace root')
            pref={'intents':intents,'profile_intents':profile_intents,'notifications':notifications}
            old=data.get('sync_preferences',{})
            data['sync_preferences']={key:bool(old.get(key)) and value for key,value in pref.items()}
            write_json(self.path,data)  # revocations take effect locally even if the control request fails
            self.transport('POST',data['cloud_url'],f"/api/v1/spaces/{data['space_id']}/sync/preferences",
                {**pref,'confirmed':confirmed,'device_id':data['device_id'],'project_id':self.ws.project_id})
            data['sync_preferences']=pref
            write_json(self.path,data)
            return data

    def sync(self):
        from paperagent.domain.lifecycle import LifecycleService
        with state_lock(self.path):
            data = self.metadata()
            if not data:
                return {'status':'unbound'}
            if data['sync_mode'] != 'cloud_sync':
                return {'status':'local_only'}
            path = f"/api/v1/spaces/{data['space_id']}"
            remote = self.transport('GET',data['cloud_url'],path+'/sync')
            if remote['sync_mode'] != 'cloud_sync':
                data['sync_mode'] = 'local_only'
                write_json(self.path,data)
                return {'status':'local_only'}
            lifecycle = LifecycleService(self.ws)
            lifecycle.get()  # lazily seed legacy snapshots before reading the outbox
            pending = lifecycle.pending_events()
            for event in pending[:100]:
                body = {k:event[k] for k in ('idempotency_key','stage_from','state','reason','actor')}
                body['legacy_history'] = event.get('legacy_history', [])
                body.update(device_id=data['device_id'],project_id=self.ws.project_id)
                result = self.transport('POST',data['cloud_url'],path+'/lifecycle/events',body)
                lifecycle.accept_remote([result])
            for _ in range(20):
                cursor = lifecycle.remote_cursor()
                rows = self.transport('GET',data['cloud_url'],path+f'/lifecycle/events?after_seq={cursor}')['events']
                lifecycle.accept_remote(rows)
                if len(rows) < 500:
                    break
            lifecycle.get()
            return {'status':'synced','pending':len(lifecycle.pending_events()),'server_seq':lifecycle.remote_cursor()}


def sync_workspaces(root):
    from paperagent.workspace.workspace import Workspace
    from pathlib import Path
    log = []
    # Workspace projects are direct children of root/projects, never recursively discover arbitrary metadata.
    for path in (Path(root) / 'projects').glob('*/meta.json'):
        try:
            ws = Workspace(path.parent.name, root)
            result = CloudSync(ws).sync()
            if result['status'] == 'synced':
                log.append(f'lifecycle sync {ws.project_id}: {result["pending"]} pending')
        except Exception:
            log.append(f'lifecycle sync {path.parent.name}: deferred; local work remains available')
    return log
