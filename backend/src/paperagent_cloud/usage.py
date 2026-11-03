"""Atomic reservations before paid requests; idempotent settlement and inventory."""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import math
import uuid
from fastapi import HTTPException

KINDS = ('llm_input_tokens','llm_output_tokens','radar_runs','storage_bytes')


def db():
    from paperagent_cloud.app import db
    return db()


def migrate(c):
    c.executescript('''
      CREATE TABLE IF NOT EXISTS usage_events(id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
        kind TEXT NOT NULL, quantity REAL NOT NULL, meta TEXT NOT NULL, created TEXT NOT NULL,
        request_key TEXT NOT NULL, month TEXT NOT NULL, UNIQUE(user_id,kind,request_key));
      CREATE TABLE IF NOT EXISTS quotas(user_id TEXT NOT NULL, kind TEXT NOT NULL,
        monthly_limit REAL NOT NULL CHECK(monthly_limit>=0), PRIMARY KEY(user_id,kind));
      CREATE TABLE IF NOT EXISTS usage_reservations(user_id TEXT NOT NULL, request_key TEXT NOT NULL,
        kind TEXT NOT NULL, quantity REAL NOT NULL, month TEXT NOT NULL,
        state TEXT NOT NULL DEFAULT 'reserved', PRIMARY KEY(user_id,request_key,kind));
    ''')


def month_now():
    return datetime.now(timezone.utc).strftime('%Y-%m')


def _valid(kind, qty):
    if kind not in KINDS or not math.isfinite(qty) or (qty < 0 and kind != 'storage_bytes'):
        raise ValueError('Invalid usage kind or quantity')


def _total(c, uid, kind, month):
    if kind == 'storage_bytes':
        return c.execute('SELECT COALESCE(SUM(quantity),0) FROM usage_events WHERE user_id=? AND kind=?',(uid,kind)).fetchone()[0]
    return c.execute('SELECT COALESCE(SUM(quantity),0) FROM usage_events WHERE user_id=? AND kind=? AND month=?',(uid,kind,month)).fetchone()[0]


def _reserved(c, uid, kind, month):
    if kind == 'storage_bytes':
        return c.execute("SELECT COALESCE(SUM(quantity),0) FROM usage_reservations WHERE user_id=? AND kind=? AND state='reserved'",(uid,kind)).fetchone()[0]
    return c.execute("SELECT COALESCE(SUM(quantity),0) FROM usage_reservations WHERE user_id=? AND kind=? AND month=? AND state='reserved'",(uid,kind,month)).fetchone()[0]


def _check(c, uid, kind, qty, month):
    used = _total(c,uid,kind,month)
    reserved = _reserved(c,uid,kind,month)
    quota = c.execute('SELECT monthly_limit FROM quotas WHERE user_id=? AND kind=?',(uid,kind)).fetchone()
    if used + qty < 0:
        raise ValueError('Storage release exceeds current inventory')
    if qty >= 0 and quota and used + reserved + qty > quota[0]:
        raise HTTPException(429,'用量配额不足')


def _insert(c, uid, kind, qty, key, month, meta):
    c.execute('INSERT INTO usage_events VALUES(?,?,?,?,?,?,?,?)',
        (uuid.uuid4().hex,uid,kind,qty,json.dumps(meta,sort_keys=True),datetime.now(timezone.utc).isoformat(),key,month))


def record_usage(user_id, kind, quantity, request_key, meta=None):
    """Server-owned meter. Storage negative deltas release inventory; no public write API."""
    _valid(kind,quantity)
    if not request_key:
        raise ValueError('Usage requires an idempotency key')
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        prior = c.execute('SELECT quantity,meta FROM usage_events WHERE user_id=? AND kind=? AND request_key=?',(user_id,kind,request_key)).fetchone()
        if prior:
            if prior['quantity'] != quantity or prior['meta'] != json.dumps(meta or {},sort_keys=True):
                raise HTTPException(409,'用量请求标识冲突')
            return
        month = month_now()
        _check(c,user_id,kind,quantity,month)
        _insert(c,user_id,kind,quantity,request_key,month,meta or {})


def reserve(user_id, request_key, quantities):
    month = month_now()
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        prior = c.execute('SELECT kind,quantity,state FROM usage_reservations WHERE user_id=? AND request_key=?',(user_id,request_key)).fetchall()
        if prior:
            if {r['kind']:r['quantity'] for r in prior} != quantities:
                raise HTTPException(409,'配额预留标识冲突')
            raise HTTPException(409,'模型请求已预留或结算，禁止重复付费调用')
        for kind,qty in quantities.items():
            _valid(kind,qty)
            if qty < 0:
                raise ValueError('Reservations cannot release inventory')
            _check(c,user_id,kind,qty,month)
        for kind,qty in quantities.items():
            c.execute('INSERT INTO usage_reservations VALUES(?,?,?,?,?,?)',(user_id,request_key,kind,qty,month,'reserved'))


def settle(user_id, request_key, quantities=None, estimated=False):
    """Unknown provider outcome is conservatively charged at reserved upper bounds."""
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        rows = c.execute('SELECT * FROM usage_reservations WHERE user_id=? AND request_key=?',(user_id,request_key)).fetchall()
        if not rows:
            raise ValueError('Unknown reservation')
        resolved = {r['kind']: (quantities.get(r['kind'],r['quantity']) if quantities is not None else r['quantity']) for r in rows}
        for kind,qty in resolved.items():
            _valid(kind,qty)
        if all(r['state']=='settled' for r in rows):
            prior = {r['kind']:r['quantity'] for r in c.execute('SELECT kind,quantity FROM usage_events WHERE user_id=? AND request_key=?',(user_id,request_key))}
            if prior != resolved:
                raise HTTPException(409,'结算与既有记录冲突')
            return
        for row in rows:
            _insert(c,user_id,row['kind'],resolved[row['kind']],request_key,row['month'],{'estimated':estimated})
        c.execute("UPDATE usage_reservations SET state='settled' WHERE user_id=? AND request_key=?",(user_id,request_key))


def recover_reservations():
    """Single-instance startup: prior process outcomes are unknown, never free them silently."""
    with db() as c:
        rows = c.execute("SELECT DISTINCT user_id,request_key FROM usage_reservations WHERE state='reserved'").fetchall()
    for row in rows:
        settle(row['user_id'],row['request_key'],estimated=True)


def summary(user_id, month=None):
    month = month or month_now()
    try:
        if datetime.strptime(month,'%Y-%m').strftime('%Y-%m') != month:
            raise ValueError()
    except ValueError:
        raise HTTPException(422,'月份须为 YYYY-MM') from None
    with db() as c:
        totals = {kind:_total(c,user_id,kind,month) for kind in KINDS}
        reserved = {kind:_reserved(c,user_id,kind,month) for kind in KINDS}
        quotas = {r['kind']:r['monthly_limit'] for r in c.execute('SELECT * FROM quotas WHERE user_id=?',(user_id,))}
        estimates = c.execute("SELECT COUNT(*) FROM usage_events WHERE user_id=? AND month=? AND meta LIKE '%true%'",(user_id,month)).fetchone()[0]
    return {'month':month,'totals':totals,'reserved':reserved,'quotas':quotas,
            'estimated_events':estimates,'storage_semantics':'current_inventory'}


class Meter:
    def __init__(self, uid, operation):
        self.uid, self.operation = uid, operation

    def before(self, payload):
        key = self.operation + ':' + uuid.uuid4().hex
        # Byte-based conservative bound; include provider framing, and enforce output max_tokens.
        quantities = {'llm_input_tokens':len(json.dumps(payload,ensure_ascii=False).encode('utf-8')) * 2 + 4096,
                      'llm_output_tokens':payload['max_tokens']}
        reserve(self.uid,key,quantities)
        return key

    def after(self, key, usage, outcome):
        if usage is not None:
            settle(self.uid,key,usage)
        elif outcome == 'rejected':
            settle(self.uid,key,{'llm_input_tokens':0,'llm_output_tokens':0})
        else:
            settle(self.uid,key,estimated=True)


@contextmanager
def metered(user_id, operation):
    from paperagent.harness.llm import usage_meter
    token = usage_meter.set(Meter(user_id,operation))
    try:
        yield
    finally:
        usage_meter.reset(token)
