from pathlib import Path
import os
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def test_cloud_has_independent_package():
    assert (ROOT / 'backend' / 'pyproject.toml').is_file()
    result = subprocess.run([sys.executable, '-c', 'import paperagent_cloud.app'], cwd=ROOT / 'backend', capture_output=True, text=True, env={**os.environ, 'PYTHONPATH': str(ROOT / 'backend' / 'src')})
    assert result.returncode == 0, result.stderr


def test_desktop_has_packaged_app_and_resources():
    assert (ROOT / 'frontend' / 'pyproject.toml').is_file()
    result = subprocess.run([sys.executable, '-c', "from fastapi.testclient import TestClient; from paperagent_desktop.app import app; c=TestClient(app); assert c.get('/').status_code == 200; assert c.get('/app.js').status_code == 200"], cwd=ROOT / 'frontend', capture_output=True, text=True, env={**os.environ, 'PYTHONPATH': str(ROOT / 'frontend' / 'src')})
    assert result.returncode == 0, result.stderr
