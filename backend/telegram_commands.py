"""Private owner commands, isolated from trading and notification workers."""
import os
import secrets
import sqlite3
import subprocess
import threading
import time
import requests
from . import telegram_notifications as notifications

VM = os.environ.get('AZURE_VM_NAME', '')
RESOURCE_GROUP = os.environ.get('AZURE_RESOURCE_GROUP', '')
SUBSCRIPTION = os.environ.get('AZURE_SUBSCRIPTION_ID', '')
thread = None
last_error = None
pending = None


def api(method, payload):
    token = os.environ.get('TELEGRAM_BOT_TOKEN', '')
    response = requests.post(f'https://api.telegram.org/bot{token}/{method}',
                             json=payload, timeout=(3, 30), allow_redirects=False)
    response.raise_for_status()
    result = response.json()
    if not result.get('ok'):
        raise RuntimeError('Telegram command API failed')
    return result['result']


def reply(text):
    api('sendMessage', {'chat_id': os.environ['TELEGRAM_COMMAND_USER_ID'], 'text': text})


def deallocate():
    if not all((VM, RESOURCE_GROUP, SUBSCRIPTION)):
        raise RuntimeError('Azure VM command is not configured')
    # Fixed argv: Telegram text is never passed to a shell or Azure CLI.
    subprocess.run(['az', 'vm', 'deallocate', '--resource-group', RESOURCE_GROUP,
                    '--name', VM, '--subscription', SUBSCRIPTION, '--no-wait', '--only-show-errors'],
                   check=True, timeout=45, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL)


def claim(update_id):
    # Claim before handling: ambiguous failures are never automatically replayed.
    c = sqlite3.connect(notifications.DB_PATH, timeout=2)
    try:
        c.execute('CREATE TABLE IF NOT EXISTS command_updates (id INTEGER PRIMARY KEY)')
        result = c.execute('INSERT OR IGNORE INTO command_updates VALUES (?)', (update_id,))
        c.commit()
        return result.rowcount == 1
    finally:
        c.close()


def handle(update):
    global pending
    message = update.get('message', {})
    owner = os.getenv('TELEGRAM_COMMAND_USER_ID', '')
    sender = message.get('from', {})
    chat = message.get('chat', {})
    if (not owner.isdigit() or int(owner) <= 0 or str(sender.get('id')) != owner
        or str(chat.get('id')) != owner or chat.get('type') != 'private'
        or sender.get('is_bot') or message.get('sender_chat')
        or message.get('forward_origin') or message.get('forward_date')):
        return
    age = time.time() - message.get('date', 0)
    if not 0 <= age <= 120 or not claim(update['update_id']):
        return
    parts = message.get('text', '').strip().split()
    if not parts:
        return
    command = parts[0].split('@')[0].lower()
    if command == '/status':
        from . import autotrade
        config = autotrade.get_config()
        reply(f"ShreyaDesk: enabled={config.get('enabled')}, paused={config.get('paused')}, "
              f"kill switch={config.get('kill_switch')}. VM: {VM}. "
              'Updates go to your configured group; commands are private-owner only.')
    elif command == '/stopvm':
        if len(parts) == 1:
            pending = (secrets.token_hex(4), time.monotonic() + 60)
            reply(f'Deallocate {RESOURCE_GROUP}/{VM}? This stops the backend, scans and '
                  'reconciliation. Broker orders and GTTs remain at Kite. Trading will be paused first. '
                  f'Within 60 seconds send: /stopvm confirm {pending[0]}')
        elif (len(parts) == 3 and parts[1] == 'confirm' and pending
              and time.monotonic() < pending[1]
              and secrets.compare_digest(parts[2], pending[0])):
            pending = None
            from . import autotrade
            autotrade.pause()
            reply('Trading paused. Requesting Azure VM deallocation now. This is not a completion confirmation.')
            try:
                deallocate()
                notifications.record('VM deallocation', 'REQUESTED', 'Owner confirmed private command')
            except Exception:
                notifications.record('VM deallocation', 'FAILED', 'Azure request failed; trading remains paused')
                reply('Azure deallocation request failed or timed out. Trading remains paused. Check Azure VM status.')
        else:
            reply('Confirmation invalid or expired. Send /stopvm again for a new confirmation.')
    else:
        reply('Commands: /status and /stopvm. VM shutdown requires a second confirmation.')


def work():
    global last_error
    offset = None
    while not notifications.stop.is_set():
        try:
            if offset is None:
                # Discard pre-start backlog. A restart never acts on old commands.
                rows = api('getUpdates', {'offset': -1, 'limit': 1, 'timeout': 0,
                                          'allowed_updates': ['message']})
                offset = rows[-1]['update_id'] + 1 if rows else 0
            rows = api('getUpdates', {'offset': offset, 'timeout': 20,
                                      'allowed_updates': ['message']})
            last_error = None
            for update in rows:
                offset = update['update_id'] + 1
                try:
                    handle(update)
                except Exception:
                    last_error = 'Command failed; inspect server configuration. No automatic action retry.'
        except Exception:
            last_error = 'Telegram polling unavailable; check bot credentials or competing pollers/webhook.'
            notifications.stop.wait(5)


def start():
    global thread
    owner = os.getenv('TELEGRAM_COMMAND_USER_ID', '')
    if not owner.isdigit() or int(owner) <= 0 or not os.getenv('TELEGRAM_BOT_TOKEN'):
        return
    if thread and thread.is_alive():
        return
    thread = threading.Thread(target=work, name='telegram-private-commands', daemon=True)
    thread.start()


def public():
    return {'running': bool(thread and thread.is_alive()), 'error': last_error,
            'vm': VM, 'resource_group': RESOURCE_GROUP,
            'owner_configured': bool(os.getenv('TELEGRAM_COMMAND_USER_ID'))}
