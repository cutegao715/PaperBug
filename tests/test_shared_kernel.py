"""Canonical cloud entry and standalone local core contracts."""
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def test_cloud_uses_shared_agent_and_llm():
    from paperagent.agent.llm_agent import LLMAgent
    from paperagent.harness.llm import LLMClient
    from paperagent_cloud import app
    from paperagent_cloud.research import original_agent
    assert app.LLMClient is LLMClient
    assert original_agent.LLMAgent is LLMAgent


def test_local_core_imports_without_cloud_package():
    code = """
import sys
class NoCloud:
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'paperagent_cloud':
            raise ModuleNotFoundError('cloud intentionally unavailable', name=fullname)
sys.meta_path.insert(0, NoCloud())
from paperagent.agent.llm_agent import LLMAgent
from paperagent import server
assert server.app
"""
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_cloud_uses_shared_environment_loader():
    from paperagent.env import load_env_file
    from paperagent_cloud import app
    assert app.load_env_file is load_env_file
