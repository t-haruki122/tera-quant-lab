import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

# A separate interpreter builds the actual middleware from env settings without
# reloading modules or leaking a changed app/settings singleton into other tests.
CORS_SCRIPT = """
import json
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.main import app, settings, configure_cors
result = {'origins': settings.cors_origins}
# Deliberately do not enter lifespan: no database or network is needed here.
client = TestClient(app)
for origin in ['http://a.example', 'http://evil.example']:
    for method in ['GET', 'OPTIONS']:
        headers = {'Origin': origin}
        if method == 'OPTIONS':
            headers.update({'Access-Control-Request-Method': 'GET',
                            'Access-Control-Request-Headers': 'Authorization, Content-Type'})
        response = client.request(method, '/', headers=headers)
        result[origin + method] = dict(response.headers)
try:
    configure_cors(FastAPI(), ['*'], allow_credentials=True)
except ValueError:
    result['guard'] = True
print(json.dumps(result))
"""


@pytest.mark.parametrize("value", [None, "", "http://a.example, http://b.example",
                                  '[" http://a.example ", "", "http://b.example"]'])
def test_cors_environment_policy(value, tmp_path):
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite+aiosqlite:///{(tmp_path / 'cors.db').as_posix()}"
    if value is None:
        env.pop("CORS_ORIGINS", None)
    else:
        env["CORS_ORIGINS"] = value
    # Ignore any developer .env by running from a temporary directory. Imports
    # still resolve to this repository, just as they do in pytest.
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
    proc = subprocess.run([sys.executable, "-c", CORS_SCRIPT], cwd=tmp_path, env=env,
                          capture_output=True, text=True, encoding="utf-8", check=True, timeout=30)
    result = json.loads(proc.stdout)
    expected = [] if value in (None, "") else ["http://a.example", "http://b.example"]
    assert result["origins"] == expected
    for method in ("GET", "OPTIONS"):
        allowed = result["http://a.example" + method]
        assert allowed.get("access-control-allow-origin") == ("http://a.example" if expected else None)
        assert "access-control-allow-origin" not in result["http://evil.example" + method]
        for origin in ("http://a.example", "http://evil.example"):
            assert "access-control-allow-credentials" not in result[origin + method]
    assert result["guard"] is True
