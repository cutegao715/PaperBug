"""Shared, deterministic configuration merge contract (not research facts)."""
import json
from pydantic import BaseModel, ConfigDict, Field, model_validator
from paperagent.domain.entities import StandingIntent

PAYLOAD_FIELDS = {'description','type','topic','structured_query','scope','notify_policy','dedup_key','enabled','created_at'}


class IntentEnvelope(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(pattern=r'^[0-9a-f]{32}$')
    updated: int = Field(ge=0)
    version: int = Field(ge=1)
    origin: str = Field(min_length=1,max_length=100)
    deleted: bool = False
    payload: dict

    @model_validator(mode='after')
    def validate_payload(self):
        if not set(self.payload) <= PAYLOAD_FIELDS:
            raise ValueError('Unexpected intent fields')
        normalized=StandingIntent(intent_id='remote',**{'created_at':'',**self.payload})
        scope = self.payload.get('scope',{})
        if scope not in ({'profile':True},{'project':True}):
            raise ValueError('Intent synchronization requires explicit scope')
        self.payload=normalized.model_dump(include=PAYLOAD_FIELDS)
        return self


def rank(row):
    # Tombstones are terminal for this ID. Recreation uses a new ID.
    return (bool(row['deleted']),row['updated'],row['version'],row['origin'],
            json.dumps(row,sort_keys=True,ensure_ascii=False,separators=(',',':')))
