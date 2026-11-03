"""Loopback-only local workspace server."""
from pathlib import Path
import base64
import hashlib
import json
import os
import sqlite3
import uuid
from io import BytesIO

from fastapi import FastAPI, Header, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from pypdf import PdfReader
from docx import Document
import uvicorn

app = FastAPI(title="PaperAgent Local Workspace", docs_url=None, redoc_url=None, openapi_url=None)


class MaterialInput(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    data: str = Field(max_length=7_000_000)


class GrantInput(MaterialInput):
    allow_cloud: bool = False


class QueueInput(BaseModel):
    operation_id: str = Field(min_length=8, max_length=100, pattern=r'^[a-zA-Z0-9_-]+$')
    file_ref: str = Field(min_length=8, max_length=100, pattern=r'^[a-zA-Z0-9_-]+$')
    sha256: str = Field(pattern=r'^[0-9a-f]{64}$')


def local_db():
    path = Path(os.getenv('PAPERAGENT_LOCAL_DB', str(Path.home() / '.paperagent' / 'local.sqlite3')))
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=15, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.executescript('''CREATE TABLE IF NOT EXISTS materials(file_ref TEXT PRIMARY KEY, filename TEXT NOT NULL, data TEXT NOT NULL, sha256 TEXT NOT NULL, active INTEGER NOT NULL);
    CREATE TABLE IF NOT EXISTS local_queue(operation_id TEXT PRIMARY KEY, file_ref TEXT NOT NULL, sha256 TEXT NOT NULL, status TEXT NOT NULL, result_json TEXT);''')
    return conn


def require_origin(origin):
    if origin not in {'http://127.0.0.1:8766', 'http://localhost:8766'}:
        raise HTTPException(403, '来源不允许')


@app.post('/local/material/read')
def read_material(body: MaterialInput, origin: str = Header(default='')):
    require_origin(origin)
    if '/' in body.filename or '\\' in body.filename:
        raise HTTPException(422, '文件名不能包含路径')
    suffix = Path(body.filename).suffix.lower()
    if suffix not in {'.txt', '.md', '.pdf', '.docx'}:
        raise HTTPException(422, '仅支持 PDF、DOCX、TXT 和 Markdown')
    try:
        raw = base64.b64decode(body.data, validate=True)
        if len(raw) > 5_000_000:
            raise ValueError('文件过大')
        if suffix == '.pdf':
            reader = PdfReader(BytesIO(raw))
            texts = [(reader.pages[i].extract_text() or '') for i in range(min(len(reader.pages), 5))]
            pages = [{'page':i+1, 'text':text[:3500], 'truncated':len(text) > 3500} for i, text in enumerate(texts)]
            total = len(reader.pages)
        elif suffix == '.docx':
            document = Document(BytesIO(raw))
            text = '\n'.join(p.text for p in document.paragraphs)
            table_text = '\n'.join(' | '.join(cell.text for cell in row.cells) for table in document.tables for row in table.rows)
            text = '\n'.join(part for part in (text, table_text) if part)
            pages = [{'page':1, 'text':text[:3500], 'truncated':len(text) > 3500}]
            total = 1
        else:
            text = raw.decode('utf-8')
            pages = [{'page':1, 'text':text[:3500], 'truncated':len(text) > 3500}]
            total = 1
    except Exception as exc:
        raise HTTPException(422, '文件无法读取') from exc
    return {'filename':body.filename, 'sha256':hashlib.sha256(raw).hexdigest(), 'total_pages':total, 'pages':pages, 'pages_truncated':len(pages) < total, 'locator_kind':'page' if suffix == '.pdf' else 'text_section'}


@app.post('/local/materials', status_code=201)
def grant_material(body: GrantInput, origin: str = Header(default='')):
    require_origin(origin)
    if not body.allow_cloud:
        raise HTTPException(403, '需要明确授权')
    preview = read_material(body, origin)
    file_ref = 'file_' + uuid.uuid4().hex
    with local_db() as c:
        c.execute('INSERT INTO materials VALUES(?,?,?,?,1)', (file_ref, body.filename, body.data, preview['sha256']))
    return {'file_ref':file_ref, 'sha256':preview['sha256'], 'filename':body.filename, 'total_pages':preview['total_pages']}


@app.post('/local/materials/{file_ref}/revoke')
def revoke_material(file_ref: str, origin: str = Header(default='')):
    require_origin(origin)
    with local_db() as c:
        if not c.execute('UPDATE materials SET active=0 WHERE file_ref=?', (file_ref,)).rowcount:
            raise HTTPException(404, '材料不存在')
    return {'file_ref':file_ref, 'status':'revoked'}


@app.post('/local/queue', status_code=201)
def enqueue(body: QueueInput, origin: str = Header(default='')):
    require_origin(origin)
    with local_db() as c:
        c.execute('BEGIN IMMEDIATE')
        try:
            material = c.execute('SELECT active,sha256 FROM materials WHERE file_ref=?', (body.file_ref,)).fetchone()
            if not material or not material['active']:
                raise HTTPException(403, '材料授权不存在或已撤销')
            if material['sha256'] != body.sha256:
                raise HTTPException(409, '材料版本不匹配')
            existing = c.execute('SELECT * FROM local_queue WHERE operation_id=?', (body.operation_id,)).fetchone()
            if existing and (existing['file_ref'] != body.file_ref or existing['sha256'] != body.sha256):
                raise HTTPException(409, '操作内容不一致')
            if not existing:
                c.execute('INSERT INTO local_queue VALUES(?,?,?,?,?)', (body.operation_id, body.file_ref, body.sha256, 'ready', None))
            c.execute('COMMIT')
            return {'operation_id':body.operation_id, 'status': existing['status'] if existing else 'ready'}
        except Exception:
            c.execute('ROLLBACK')
            raise


@app.get('/local/queue/{operation_id}')
def queue_status(operation_id: str, origin: str = Header(default='')):
    require_origin(origin)
    with local_db() as c:
        row = c.execute('SELECT operation_id,status FROM local_queue WHERE operation_id=?', (operation_id,)).fetchone()
    if not row:
        raise HTTPException(404, '操作不存在')
    return dict(row)


@app.post('/local/queue/{operation_id}/execute')
def execute_queue(operation_id: str, origin: str = Header(default='')):
    require_origin(origin)
    with local_db() as c:
        row = c.execute('SELECT q.*,m.filename,m.data,m.active FROM local_queue q JOIN materials m ON m.file_ref=q.file_ref WHERE q.operation_id=?', (operation_id,)).fetchone()
        if not row:
            raise HTTPException(404, '操作不存在')
        if not row['active']:
            raise HTTPException(403, '材料授权已撤销')
        result = read_material(MaterialInput(filename=row['filename'],data=row['data']), origin)
        if result['sha256'] != row['sha256']:
            raise HTTPException(409, '材料版本不匹配')
        c.execute("UPDATE local_queue SET status='awaiting_upload',result_json=? WHERE operation_id=?", (json.dumps(result,ensure_ascii=False),operation_id))
        return result


@app.post('/local/queue/{operation_id}/ack')
def acknowledge_queue(operation_id: str, origin: str = Header(default='')):
    require_origin(origin)
    with local_db() as c:
        row = c.execute('SELECT status FROM local_queue WHERE operation_id=?', (operation_id,)).fetchone()
        if not row:
            raise HTTPException(404, '操作不存在')
        if row['status'] not in {'awaiting_upload', 'confirmed'}:
            raise HTTPException(409, '操作尚未产生结果')
        c.execute("UPDATE local_queue SET status='confirmed' WHERE operation_id=?", (operation_id,))
    return {'operation_id':operation_id, 'status':'confirmed'}


app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="workspace")


def main():
    uvicorn.run(app, host="127.0.0.1", port=8766)
