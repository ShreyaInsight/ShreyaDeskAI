"""Read-only, app-owned order/trade analytics; no broker portfolio P&L."""
import math
from datetime import datetime
from . import autotrade as a


def audit_skip(symbol, action, mode, reason, run_id=None, position_id=None):
    conn = a.connection()
    try:
        conn.execute('INSERT INTO analytics_skips(timestamp,symbol,action,mode,reason,scan_run_id,position_id) VALUES (?,?,?,?,?,?,?)',
                     (a.now(), symbol, action, mode, reason, run_id, position_id))
        conn.commit()
    finally:
        conn.close()


def stamp(value):
    if not value: return None
    try:
        dt = datetime.fromisoformat(str(value))
        return (dt.replace(tzinfo=a.IST) if dt.tzinfo is None else dt.astimezone(a.IST)).isoformat()
    except ValueError: return None


def action(source):
    text = source.upper()
    if text.startswith('BUY'): return 'BUY'
    if text.startswith('SELL') or 'EXIT' in text or 'CLOSE' in text: return 'SELL'
    return 'UNKNOWN'


def snapshot(mode):
    conn = a.connection()
    try:
        positions = [dict(r) for r in conn.execute('SELECT * FROM autotrade_positions WHERE mode=? ORDER BY id DESC',(mode,))]
        orders = [dict(r) for r in conn.execute('SELECT * FROM autotrade_orders WHERE mode=? ORDER BY id DESC',(mode,))]
        gtt_events = [dict(r) for r in conn.execute('SELECT e.*,p.symbol FROM autotrade_gtt_events e JOIN autotrade_positions p ON p.id=e.position_id WHERE p.mode=? ORDER BY e.id',(mode,))]
        skips = [dict(r) for r in conn.execute('SELECT * FROM analytics_skips WHERE mode=? ORDER BY id DESC',(mode,))]
    finally: conn.close()
    quotes = {}; warnings = []
    symbols = sorted({p['symbol'] for p in positions if p['status']=='OPEN'})
    if symbols:
        try:
            kite = a.get_kite()
            for offset in range(0,len(symbols),500):
                quotes.update(kite.ltp(['NSE:'+s for s in symbols[offset:offset+500]]))
        except Exception:
            warnings.append('Some current prices could not be fetched. Missing unrealized P&L is not treated as zero.')
    now = datetime.now(a.IST)
    from .gtt import public
    for p in positions:
        p.update(public(p['id']) if mode=='live' else {})
        p['gtt_timeline'] = [e for e in gtt_events if e['position_id']==p['id']]
        if p['status']=='OPEN' and mode=='live' and p.get('gtt_status') not in {'ACTIVE','NOT_REQUIRED'}:
            warnings.append(f"{p['symbol']}: {p.get('gtt_message','GTT protection needs attention')}")
        p['entry_time'] = stamp(p['entry_time']); p['exit_time'] = stamp(p['exit_time'])
        p['timestamp'] = p['entry_time']
        p['ltp'] = quotes.get('NSE:'+p['symbol'],{}).get('last_price')
        if not isinstance(p['ltp'],(int,float)) or not math.isfinite(p['ltp']) or p['ltp']<=0: p['ltp']=None
        p['pnl'] = p['realized_pnl'] if p['status'] in {'CLOSED','EXIT_PENDING'} else ((p['ltp']-p['entry_price'])*p['quantity']+float(p['realized_pnl'] or 0) if p['ltp'] is not None else None)
        cost=p['entry_price']*p['quantity']
        p['pnl_pct'] = p['pnl']/cost*100 if p['pnl'] is not None and cost>0 else None
        end=datetime.fromisoformat(p['exit_time']) if p['exit_time'] else now
        p['duration_hours']=max(0,(end-datetime.fromisoformat(p['entry_time'])).total_seconds()/3600) if p['entry_time'] else None
        p['charges']=None
    logs=[]; accepted=[]
    for o in orders:
        o['timestamp']=stamp(o['timestamp']);o['fill_time']=stamp(o['fill_time'])
        if mode=='live' and o['position_id'] and o['signal_source'].startswith('BUY'): o.update(public(o['position_id']))
        o['action']=action(o['signal_source']);o['product']='CNC' if mode=='live' else 'SIMULATED'
        # Historical paper orders did not persist a separate fill price.
        if mode=='paper' and o['status']=='FILLED': o['fill_price']=o['requested_price']
        if o['status']=='COMPLETE' and o.get('filled_quantity') is None:o['filled_quantity']=o['requested_qty']
        if o.get('filled_quantity') and o['status'] not in {'COMPLETE','FILLED','PARTIAL_UNRESOLVED'}:
            o['display_status']='PARTIAL / '+o['status']
        else:o['display_status']=o['status']
        reached=bool(o['kite_order_id']) if mode=='live' else o['status']=='FILLED'
        if reached:accepted.append(o)
        kind = 'REJECTED' if o['status']=='REJECTED' else 'BLOCKED' if o['status']=='BLOCKED' else 'UNCERTAIN' if o['error_message'] else 'OK' if reached else 'PENDING'
        logs.append(dict(id='order-'+str(o['id']),order_id=o['id'],symbol=o['symbol'],mode=mode,action=o['action'],timestamp=o['timestamp'],outcome_type=kind,
                         outcome=o['error_message'] or ('Simulated fill' if mode=='paper' and reached else 'Broker accepted; '+o['display_status'] if reached else 'Awaiting broker confirmation'),
                         check=o['signal_source'],position_id=o['position_id'],scan_run_id=o['scan_run_id'],kite_order_id=o['kite_order_id']))
    for e in gtt_events:
        logs.append(dict(id='gtt-'+str(e['id']),order_id=None,symbol=e['symbol'],mode=mode,action='GTT',timestamp=stamp(e['timestamp']),outcome_type='FAILED' if any(word in e['event'].lower() for word in ['failed','uncertain','unavailable']) else 'OK',outcome=e['message'],check=e['event'],position_id=e['position_id'],scan_run_id=None,kite_order_id=None,gtt_id=e['trigger_id']))
    for s in skips:
        logs.append(dict(id='skip-'+str(s['id']),order_id=None,symbol=s['symbol'],mode=mode,action=s['action'],timestamp=stamp(s['timestamp']),outcome_type='SKIPPED',outcome=s['reason'],check=s['reason'],position_id=s['position_id'],scan_run_id=s['scan_run_id'],kite_order_id=None))
    return dict(mode=mode,orders=accepted,trades=positions,logs=sorted(logs,key=lambda r:r['timestamp'] or '',reverse=True),warnings=warnings,updated_at=now.isoformat())
