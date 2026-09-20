"""Best-effort notifications: bounded memory enqueue only on trading call sites."""
import os,re,json,sqlite3,threading,queue,time,hashlib
from pathlib import Path
from datetime import datetime,timezone
import requests

from .runtime_paths import TELEGRAM_DB_PATH
DB_PATH=TELEGRAM_DB_PATH
DEFAULTS={'enabled':True,'orders':True,'safety':True,'connection':True,'service':True,'capacity':True,'scanner':False,'paper':True}
settings=dict(DEFAULTS)
messages=queue.Queue(maxsize=512)
urgent=queue.Queue(maxsize=64)
stop=threading.Event()
thread=None
monitor=None
status={'last_success':None,'last_error':None,'sent':0,'failed':0,'dropped':0}
seen={}


def stamp():return datetime.now(timezone.utc).isoformat()


def db():
 c=sqlite3.connect(DB_PATH,timeout=2)
 c.execute('CREATE TABLE IF NOT EXISTS config(id INTEGER PRIMARY KEY,payload TEXT)')
 c.execute('CREATE TABLE IF NOT EXISTS delivered_keys(key TEXT PRIMARY KEY,created_at TEXT)')
 c.execute('CREATE TABLE IF NOT EXISTS deliveries(id INTEGER PRIMARY KEY,created_at TEXT,kind TEXT,status TEXT,detail TEXT)')
 return c


def clean(value):
 text=str(value).split('Traceback')[0]
 for key,value in os.environ.items():
  if value and len(value)>=4 and any(k in key.upper() for k in ('TOKEN','SECRET','PASSWORD','API_KEY')):text=text.replace(value,'[redacted]')
 text=re.sub(r'https?://\S+','[URL removed]',text)
 text=re.sub(r'(?i)(access_token|api_secret|password|api_key|authorization|request_token)\s*[:=].*',r'\1=[redacted]',text)
 text=re.sub(r'\b[A-Za-z0-9_-]{32,}\b','[redacted]',text)
 return text.replace('\n',' ')[:400]


def emit(kind, message='', *, key=None, category='orders', order_id=None, stage=None):
 """No HTTP, disk IO, blocking lock acquisition or waiting here. Never raises."""
 try:
  if not settings['enabled'] or not settings.get(category,True):return False
  target=urgent if category in {'safety','connection'} else messages
  target.put_nowait({'kind':kind,'message':str(message)[:1000],'key':key,'category':category,'order_id':order_id,'stage':stage,'time':stamp()})
  return True
 except Exception:
  status['dropped']+=1
  return False


def record(kind,outcome,detail):
 c=db()
 try:
  c.execute('INSERT INTO deliveries(created_at,kind,status,detail) VALUES (?,?,?,?)',(stamp(),kind,outcome,detail))
  c.execute('DELETE FROM deliveries WHERE id NOT IN (SELECT id FROM deliveries ORDER BY id DESC LIMIT 100)');c.commit()
 finally:c.close()


def render(event):
 if event['order_id'] is None:return clean(event['message'])
 from . import autotrade as a
 c=sqlite3.connect(f'file:{a.DB_PATH}?mode=ro',uri=True);c.row_factory=sqlite3.Row
 try:row=c.execute('SELECT * FROM autotrade_orders WHERE id=?',(event['order_id'],)).fetchone()
 finally:c.close()
 if not row:return None
 row=dict(row)
 if row['mode']=='paper' and not settings['paper']:return None
 stage=event['stage'] or row['status']
 qty=row.get('filled_quantity') if stage=='COMPLETE' else row['requested_qty']
 price=row.get('fill_price') if stage=='COMPLETE' else row['requested_price']
 side='SELL' if any(x in row['signal_source'].upper() for x in ('SELL','EXIT','CLOSE')) else 'BUY'
 message=f"{row['mode'].upper()} {side} {row['symbol']} · {stage} · qty {qty} · ₹{price} · broker ID {row['kite_order_id'] or 'none'} · app #{row['id']}"
 if row.get('error_message'):message+=' · '+clean(row['error_message'])
 return message


def deliver(event):
 if not settings['enabled'] or not settings.get(event['category'],True):return
 message=render(event)
 if message is None:return
 key=event['key'] or hashlib.sha256((event['kind']+message).encode()).hexdigest()
 durable=key.startswith(('order:','gtt:','allowance:','capacity:'))
 if durable:
  c=db()
  try:
   if c.execute('SELECT 1 FROM delivered_keys WHERE key=?',(key,)).fetchone():return
  finally:c.close()
 if key in seen and time.monotonic()-seen[key]<600:return
 seen[key]=time.monotonic()
 if len(seen)>2048:
  for k in list(seen)[:1024]:seen.pop(k,None)
 token=os.getenv('TELEGRAM_BOT_TOKEN','').strip();chat=os.getenv('TELEGRAM_CHAT_ID','').strip()
 error=None
 if not token or not chat:error='Bot token or chat ID is missing on the server'
 else:
  try:
   response=requests.post(f'https://api.telegram.org/bot{token}/sendMessage',json={'chat_id':chat,'text':f"ShreyaDesk · {event['kind']}\n{clean(message)}\n{event['time']}",'link_preview_options':{'is_disabled':True}},timeout=(3,5),allow_redirects=False)
   if response.status_code!=200 or response.json().get('ok') is not True:
    error=f'Telegram rejected delivery (HTTP {response.status_code}); check bot/chat permissions and credentials'
  except Exception:error='Telegram delivery unavailable or timed out; trading is unaffected'
 if error:
  status.update(last_error=error,failed=status['failed']+1);record(event['kind'],'FAILED',error)
 else:
  status.update(last_success=stamp(),last_error=None,sent=status['sent']+1);record(event['kind'],'SENT','Telegram acknowledged delivery')
  if durable:
   c=db()
   try:
    c.execute('INSERT OR IGNORE INTO delivered_keys VALUES (?,?)',(key,stamp()))
    c.execute("DELETE FROM delivered_keys WHERE created_at < datetime('now','-90 days')");c.commit()
   finally:c.close()


def work():
 while not stop.is_set():
  try:
   try:event=urgent.get_nowait()
   except queue.Empty:event=messages.get(timeout=.5)
   try:deliver(event)
   except Exception:status.update(last_error='Notification processing failed; trading is unaffected',failed=status['failed']+1)
   stop.wait(1.1)
  except queue.Empty:continue


def connection_check():
 from .main import get_kite
 from kiteconnect.exceptions import TokenException
 from fastapi import HTTPException
 try:
  get_kite().profile()
  return 'connected'
 except (TokenException,HTTPException):return 'disconnected'
 except Exception:return 'unreachable'


def capacity_check():
 from . import autotrade as a
 c=sqlite3.connect(f'file:{a.DB_PATH}?mode=ro',uri=True,timeout=1)
 try:
  row=c.execute('SELECT payload FROM autotrade_config WHERE id=1').fetchone()
  if not row:return
  config={**a.DEFAULT_CONFIG,**json.loads(row[0])}
  today=datetime.now(a.IST).date().isoformat()
  count=c.execute('SELECT COUNT(*) FROM autotrade_buy_reservations WHERE trading_day=?',(today,)).fetchone()[0]
  if count>=config['max_daily_order_count']:emit('Daily BUY allowance exhausted',f"{count}/{config['max_daily_order_count']} BUY submissions used for {today} IST.",key='allowance:'+today,category='capacity')
  occupied={r[0] for r in c.execute("SELECT symbol FROM autotrade_positions WHERE mode='live' AND status!='CLOSED'")}
  occupied.update(r[0] for r in c.execute("SELECT symbol FROM autotrade_orders WHERE mode='live' AND signal_source LIKE 'BUY%' AND status NOT IN ('COMPLETE','CANCELLED','REJECTED','BLOCKED')"))
  if len(occupied)>=config['max_concurrent_positions']:emit('Position capacity reached',f"{len(occupied)}/{config['max_concurrent_positions']} live position slots including pending BUYs.",key='capacity:'+today,category='capacity')
 finally:c.close()


def watch_connection():
 previous=None
 while not stop.is_set():
  current=connection_check()
  if current!=previous:
   emit('Kite connection',{'connected':'Kite session verified.','disconnected':'Kite session expired or disconnected. Reconnect from the dashboard.','unreachable':'Kite is unreachable; session validity could not be verified.'}[current],key='kite:'+current,category='connection')
  previous=current
  try:capacity_check()
  except Exception:pass
  stop.wait(30)


def start():
 global thread,monitor
 if thread and thread.is_alive():return
 c=db()
 try:
  row=c.execute('SELECT payload FROM config WHERE id=1').fetchone()
  if row:settings.update(json.loads(row[0]))
 finally:c.close()
 stop.clear()
 thread=threading.Thread(target=work,name='telegram-delivery',daemon=True);thread.start()
 monitor=threading.Thread(target=watch_connection,name='kite-notification-health',daemon=True);monitor.start()
 from . import telegram_commands
 telegram_commands.start()
 emit('Service restarted','Backend restarted. Live execution is disabled and paused.',category='service',key='restart:'+stamp())


def public():
 c=db()
 try:rows=[dict(zip(('created_at','kind','status','detail'),r)) for r in c.execute('SELECT created_at,kind,status,detail FROM deliveries ORDER BY id DESC LIMIT 10')]
 finally:c.close()
 from . import telegram_commands
 return {'commands':telegram_commands.public(),'settings':dict(settings),'configured':bool(os.getenv('TELEGRAM_BOT_TOKEN') and os.getenv('TELEGRAM_CHAT_ID')),'worker_running':bool(thread and thread.is_alive()),'queued':messages.qsize()+urgent.qsize(),**status,'recent':rows}


def configure(payload):
 if set(payload)-set(DEFAULTS) or any(type(v) is not bool for v in payload.values()):raise ValueError('Only notification toggles are accepted')
 next_settings={**settings,**payload}
 c=db()
 try:c.execute('INSERT OR REPLACE INTO config VALUES (1,?)',(json.dumps(next_settings),));c.commit()
 finally:c.close()
 settings.update(next_settings)
 return public()


def kite_error(error):
 try:
  from kiteconnect.exceptions import TokenException
  if isinstance(error,TokenException):emit('Kite connection','Kite session expired or invalid. Reconnect from the dashboard.',key='kite:disconnected',category='connection')
 except Exception:pass
