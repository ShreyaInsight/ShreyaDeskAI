"""Read-only overview of app trades; never values personal broker holdings."""
from . import autotrade as a
from .holdings import positive


def snapshot():
    config = a.get_config()
    mode = config['mode']
    conn = a.connection()
    try:
        conn.execute('BEGIN')
        positions = [dict(r) for r in conn.execute('SELECT * FROM autotrade_positions WHERE mode=?', (mode,))]
        orders = [dict(r) for r in conn.execute('SELECT * FROM autotrade_orders WHERE mode=? ORDER BY id DESC LIMIT 6', (mode,))]
        pending = a.unresolved_orders(conn) if mode == 'live' else []
    finally:
        conn.close()
    opened = [p for p in positions if p['status']=='OPEN']
    closed = [p for p in positions if p['status']=='CLOSED']
    quotes = {}
    warnings = []
    keys = sorted({'NSE:'+p['symbol'] for p in opened})
    if keys:
        try:
            kite = a.get_kite()
            for offset in range(0,len(keys),200):
                quotes.update(kite.quote(keys[offset:offset+200]))
        except Exception:
            warnings.append('Current quotes could not all be fetched. P&L is unavailable when any open position lacks a price.')
    pnl = 0
    missing = []
    movers = []
    for p in opened:
        quote = quotes.get('NSE:'+p['symbol'], {})
        price = positive(quote.get('last_price'))
        if price is None:
            missing.append(p['symbol'])
        else:
            pnl += (price-p['entry_price'])*p['quantity']
            previous = positive(quote.get('ohlc',{}).get('close'))
            if previous:
                movers.append({'symbol':p['symbol'],'change':(price/previous-1)*100})
    if missing:
        warnings.append('Missing prices: '+', '.join(sorted(set(missing))))
    known_closed = [p for p in closed if p['realized_pnl'] is not None]
    win_rate = (sum(p['realized_pnl']>0 for p in closed)/len(closed)*100) if closed and len(known_closed)==len(closed) else None
    realized = sum(float(p['realized_pnl'] or 0) for p in positions)
    realized_complete = len(known_closed) == len(closed)
    if not realized_complete:
        warnings.append('Some closed trades lack realized P&L; cumulative P&L is unavailable.')
    cumulative = None if missing or not realized_complete else round(realized + pnl, 2)
    gainers = [p for p in movers if p['change']>0]
    losers = [p for p in movers if p['change']<0]
    return {'config':{k:config[k] for k in ('mode','enabled','paused')},'positions':positions,'orders':orders,'signals':[],
            'metrics':{'openPositions':opened,'currentPnl':None if missing else round(pnl,2),'cumulativePnl':cumulative,'winRate':win_rate,
                       'closedTrades':len(closed),'openOrders':len(pending),'recentOrders':orders,
                       'topGainer':max(gainers,key=lambda p:p['change'],default=None),
                       'topLoser':min(losers,key=lambda p:p['change'],default=None)},
            'warnings':warnings,'updated_at':a.now()}
