import json
import os
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


class ModelHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        self.rfile.read(int(self.headers['Content-Length']))
        response = json.dumps({'choices': [{'message': {'content': '{"reply":"研究回答"}'}}]}).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(response)))
        self.end_headers()
        self.wfile.write(response)

    def log_message(self, *args):
        pass


def wait_server(url):
    for _ in range(100):
        try:
            return requests.get(url, timeout=0.3)
        except requests.RequestException:
            time.sleep(0.1)
    raise AssertionError('server did not start')


def test_two_services_over_http(tmp_path):
    model = ThreadingHTTPServer(('127.0.0.1', 0), ModelHandler)
    thread = threading.Thread(target=model.serve_forever, daemon=True)
    thread.start()
    cloud_port, desktop_port = free_port(), free_port()
    cloud_url, desktop_url = f'http://127.0.0.1:{cloud_port}', f'http://127.0.0.1:{desktop_port}'
    cloud_env = {**os.environ, 'PYTHONPATH': str(ROOT / 'backend' / 'src'), 'PAPERAGENT_CLOUD_DB': str(tmp_path / 'db.sqlite'), 'PAPERAGENT_CLOUD_ORIGINS': desktop_url, 'PAPERAGENT_LLM_BASE_URL': f'http://127.0.0.1:{model.server_port}', 'PAPERAGENT_LLM_API_KEY':'test-key', 'PAPERAGENT_LLM_MODEL':'test-model', 'PAPERAGENT_LLM_PROTOCOL':'openai'}
    desktop_env = {**os.environ, 'PYTHONPATH': str(ROOT / 'frontend' / 'src')}
    processes = []
    def start_cloud():
        p = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'paperagent_cloud.app:app', '--host', '127.0.0.1', '--port', str(cloud_port)], cwd=tmp_path, env=cloud_env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        processes.append(p)
        wait_server(cloud_url + '/docs')
        return p
    try:
        cloud = start_cloud()
        processes.append(subprocess.Popen([sys.executable, '-m', 'uvicorn', 'paperagent_desktop.app:app', '--host', '127.0.0.1', '--port', str(desktop_port)], cwd=tmp_path, env=desktop_env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        assert 'PaperAgent' in wait_server(desktop_url + '/').text
        assert requests.get(desktop_url + '/app.js', timeout=2).status_code == 200
        def account(name):
            r = requests.post(cloud_url+'/api/v1/auth/register', json={'username':name,'password':'test-password-123'}, timeout=5)
            assert r.status_code == 201
            return {'Authorization': 'Bearer ' + r.json()['token'], 'Origin':desktop_url}
        a, b = account('alice'), account('bob')
        space = requests.post(cloud_url+'/api/v1/spaces', headers=a, json={'name':'研究'}, timeout=5).json()['id']
        path = cloud_url+f'/api/v1/spaces/{space}/messages'
        assert requests.get(path, headers=b, timeout=5).status_code == 404
        response = requests.post(path, headers=a, json={'text':'你好', 'request_id':'test_http_123'}, timeout=10)
        assert response.json()['reply'] == '研究回答'
        assert response.headers['Access-Control-Allow-Origin'] == desktop_url
        cloud.terminate(); cloud.wait(timeout=5)
        start_cloud()
        assert len(requests.get(path, headers=a, timeout=5).json()) == 2
    finally:
        for p in processes:
            if p.poll() is None:
                p.terminate()
                p.wait(timeout=5)
        model.shutdown()
        model.server_close()
