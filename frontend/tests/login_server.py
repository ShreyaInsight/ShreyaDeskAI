"""Isolated browser fixture: real dashboard authentication, fake broker, no jobs."""
import base64
import hashlib
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import Mock
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from apscheduler.schedulers.background import BackgroundScheduler
BackgroundScheduler.start = lambda *args, **kwargs: None
from backend import main
from fastapi.staticfiles import StaticFiles
import uvicorn
salt = b'playwright-fixture-only'
digest = hashlib.scrypt(b'fixture-password', salt=salt, n=2**14, r=8, p=1, dklen=32)
os.environ['SHREYADESK_USERNAME'] = 'fixture-user'
os.environ['SHREYADESK_PASSWORD_HASH'] = base64.urlsafe_b64encode(salt).decode()+'$'+base64.urlsafe_b64encode(digest).decode()
main.app.router.on_startup.clear()
fixture_session={'api_key':'fixture-key','access_token':'fixture-only','connected_at':'2026-09-06T00:00:00Z'}
main.read_saved_session = lambda: fixture_session.copy()
main.save_session = lambda value: fixture_session.update(value)
main._rate_limit = lambda *args, **kwargs: None  # Rate limits have separate API coverage.
os.environ['FRONTEND_ORIGIN']='http://127.0.0.1:8766'
os.environ['KITE_API_KEY']='fixture-key'
os.environ['KITE_API_SECRET']='fixture-secret'
main.KiteConnect = lambda **kwargs: Mock(generate_session=lambda *args, **kwargs: {'access_token':'fixture-new'},profile=lambda:{'user_id':'FIXTURE'})
main.get_kite = lambda: Mock(profile=lambda: {'user_name':'Fixture User'})
from backend import autotrade, scanner
scratch = tempfile.TemporaryDirectory(prefix='login-playwright-')
autotrade.DB_PATH = scanner.DB_PATH = Path(scratch.name)/'test.sqlite3'
main.TOKEN_PATH = Path(scratch.name)/'kite-token.json'
autotrade.get_kite = main.get_kite
autotrade.connection().close()
scanner.db().close()
scanner.get_config = lambda: {**scanner.DEFAULT_CONFIG, 'watchlist': []}
# Replace only the hosted broker page; keep real dashboard OAuth state/callback routes.
from urllib.parse import urlsplit
from fastapi.responses import HTMLResponse
original_redirect = main.RedirectResponse
def fixture_redirect(url, **kwargs):
    if url.startswith('https://kite.trade/connect/login'):
        url = 'http://localhost:8766/fixture-kite-login?' + urlsplit(url).query
    return original_redirect(url=url, **kwargs)
main.RedirectResponse = fixture_redirect
@main.app.get('/fixture-kite-login')
def fixture_kite_page():
    return HTMLResponse('<h1>Fixture Kite login — credentials not entered</h1>')

main.app.mount('/', StaticFiles(directory=Path(__file__).resolve().parents[1]/'dist', html=True))
uvicorn.run(main.app, host='127.0.0.1', port=8766)
