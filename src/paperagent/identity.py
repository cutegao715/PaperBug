"""Account identity is established by the server, never by a model action."""
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re

from paperagent.workspace.state_store import read_json, write_json


@dataclass(frozen=True)
class Identity:
    user_id: str
    origin: str

    def __post_init__(self):
        from paperagent.tasks.cloud_sync import cloud_origin
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', self.user_id):
            raise ValueError('Invalid user identity')
        object.__setattr__(self, 'origin', cloud_origin(self.origin))

    @property
    def key(self):
        return hashlib.sha256((self.origin+'\n'+self.user_id).encode()).hexdigest()[:32]

    def local_root(self, base):
        return Path(base).resolve()/'accounts'/self.key


def selected_root(base):
    """A process selects one previously verified profile; existing roots stay local-only."""
    base=Path(base).resolve()
    profile=os.getenv('PAPERAGENT_ACCOUNT','')
    if not profile:
        return base
    if not re.fullmatch(r'[a-f0-9]{32}',profile):
        raise ValueError('Invalid account profile')
    root=base/'accounts'/profile
    data=read_json(root/'account.json')
    identity=Identity(**data)
    if identity.key!=profile:
        raise ValueError('Account profile does not match directory')
    return root


def connect_account(base, origin, token):
    import requests
    from paperagent.tasks.cloud_sync import cloud_origin
    origin=cloud_origin(origin)
    response=requests.get(origin+'/api/v1/auth/me',headers={'Authorization':'Bearer '+token},
                          timeout=10,allow_redirects=False)
    if response.status_code!=200:
        raise ValueError('Cloud identity verification failed')
    identity=Identity(response.json()['user_id'],origin)
    write_json(identity.local_root(base)/'account.json',{'user_id':identity.user_id,'origin':identity.origin})
    return identity
