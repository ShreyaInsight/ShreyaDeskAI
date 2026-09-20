"""Read-only delivery inventory and selected-strategy research; never invokes execution or risk."""
from datetime import datetime, timedelta
import math
import sqlite3


def positive(value):
    try:
        number = float(value)
        return number if math.isfinite(number) and number > 0 else None
    except (ValueError, TypeError):
        return None


def split_inventory(holdings, net_positions, app_positions, quotes):
    inventory = {}
    for h in holdings:
        if h.get('product', 'CNC') != 'CNC':
            continue
        key = f"{h['exchange']}:{h['tradingsymbol']}"
        qty = int(h.get('quantity', 0)) + int(h.get('t1_quantity', 0)) - int(h.get('used_quantity', 0))
        inventory[key] = dict(symbol=h['tradingsymbol'], exchange=h['exchange'], quantity=max(0, qty),
                              average_cost=positive(h.get('average_price')), token=h.get('instrument_token'),
                              uncertain=bool(h.get('discrepancy')))
    for p in net_positions:
        if p.get('product') != 'CNC' or p.get('exchange') not in ('NSE', 'BSE'):
            continue
        # Negative delivery positions represent sales already deducted via used_quantity.
        qty = max(0, int(p.get('quantity', 0)))
        if not qty:
            continue
        key = f"{p['exchange']}:{p['tradingsymbol']}"
        row = inventory.setdefault(key, dict(symbol=p['tradingsymbol'], exchange=p['exchange'], quantity=0,
                                            average_cost=None, token=p.get('instrument_token'), uncertain=False))
        old_qty = row['quantity']; old_cost = row['average_cost']; cost = positive(p.get('average_price'))
        row['average_cost'] = ((old_cost or 0)*old_qty + (cost or 0)*qty)/(old_qty+qty) if cost and (old_cost or not old_qty) else None
        row['quantity'] += qty
    algo = {}; warnings = []
    for p in app_positions:
        key = f"NSE:{p['symbol']}"
        row = algo.setdefault(key, dict(symbol=p['symbol'], exchange='NSE', quantity=0, cost=0, position_ids=[]))
        row['quantity'] += p['quantity']; row['cost'] += p['quantity']*p['entry_price']; row['position_ids'].append(p['id'])
    def display(key, row, cost, note=None):
        ltp = positive(quotes.get(key, {}).get('last_price'))
        return dict(symbol=row['symbol'], exchange=row['exchange'], quantity=row['quantity'], average_cost=cost,
                    ltp=ltp, pnl=(ltp-cost)*row['quantity'] if ltp and cost else None, note=note,
                    instrument_token=inventory.get(key, {}).get('token'), position_ids=row.get('position_ids', []))
    algo_rows = []; personal = []
    for key, row in algo.items():
        note = None
        if row['quantity'] > inventory.get(key, {}).get('quantity', 0):
            note = 'App quantity exceeds broker inventory; ownership needs reconciliation.'
            warnings.append(f"{row['symbol']}: {note}")
        algo_rows.append(display(key, row, row['cost']/row['quantity'], note))
    for key, row in inventory.items():
        own = algo.get(key, {}).get('quantity', 0)
        remaining = row['quantity']-own
        if remaining <= 0:
            continue
        note = 'Estimated: broker blended average; separate personal purchase costs are unavailable.' if own else None
        if row['uncertain']:
            note = 'Broker reports a cost discrepancy; average cost and P&L unavailable.'
        personal.append(display(key, {**row, 'quantity': remaining}, None if row['uncertain'] else row['average_cost'], note))
    return {'algo': sorted(algo_rows, key=lambda r:r['symbol']), 'personal': sorted(personal, key=lambda r:r['symbol']), 'warnings': warnings}


def normalize_holdings(holdings, positions, official, instruments):
    """Resolve BSE demat rows using verified security identity, never symbol alone."""
    app_symbols = {p['symbol'] for p in positions}
    tokens = {i['tradingsymbol']: i['instrument_token'] for i in instruments
              if i.get('exchange') == 'NSE' and i.get('segment') == 'NSE'
              and i.get('instrument_type') == 'EQ' and i.get('instrument_token')}
    candidates = {}
    for row in official:
        symbol = row.get('SYMBOL')
        isin = row.get('ISIN NUMBER', '').strip()
        if isin and row.get('SERIES') == 'EQ' and symbol in app_symbols and symbol in tokens:
            candidates.setdefault(isin, set()).add(symbol)
    result = []
    for original in holdings:
        row = dict(original)
        matches = candidates.get(row.get('isin'), set())
        if row.get('exchange') == 'BSE' and len(matches) == 1:
            symbol = next(iter(matches))
            row.update(exchange='NSE', tradingsymbol=symbol, instrument_token=tokens[symbol])
        result.append(row)
    return result


def current_holdings(kite):
    from . import autotrade
    with sqlite3.connect(f'file:{autotrade.DB_PATH}?mode=ro', uri=True) as conn:
        conn.row_factory = sqlite3.Row
        positions = [dict(r) for r in conn.execute("SELECT * FROM autotrade_positions WHERE mode='live' AND status='OPEN'")]
    holdings = kite.holdings(); net = kite.positions()['net']
    # Demat holdings can be returned on a different exchange from the app entry.
    if any(h.get('exchange') == 'BSE' for h in holdings) and positions:
        from .market_cache import official_equities, instruments
        holdings = normalize_holdings(holdings, positions, official_equities(), instruments(kite))
    keys = sorted({f"{r['exchange']}:{r['tradingsymbol']}" for r in holdings+net if r.get('product','CNC')=='CNC'} | {f"NSE:{r['symbol']}" for r in positions})
    quotes = {}
    for start in range(0, len(keys), 200):
        quotes.update(kite.ltp(keys[start:start+200]))
    result = split_inventory(holdings, net, positions, quotes)
    from .gtt import public
    for row in result['algo']:
        row['gtt_protection'] = [public(pid) for pid in row['position_ids']]
    return {**result, 'updated_at': datetime.now(autotrade.IST).isoformat()}


def strategy_signal(kite, symbol, exchange):
    from . import scanner
    from .main import instrument_tokens
    # Read config without initializing a watchlist or writing scanner state.
    import json
    with sqlite3.connect(f'file:{scanner.DB_PATH}?mode=ro', uri=True) as conn:
        stored = conn.execute('SELECT payload FROM scanner_config WHERE id=1').fetchone()
    config = {**scanner.DEFAULT_CONFIG, **(json.loads(stored[0]) if stored else {})}
    tokens = instrument_tokens(kite) if exchange == 'NSE' else {r['tradingsymbol']:r['instrument_token'] for r in kite.instruments(exchange)}
    token = tokens.get(symbol)
    if not token:
        return {'signal_type': None, 'trigger_date': None, 'error': 'Instrument unavailable'}
    today = datetime.now(scanner.IST).date()
    start = today-timedelta(days=max(config['lookback_days'], config['adx_lookback'], 365)+config['length']*4+30)
    if config.get('strategy') == 'supertrend':
        from .supertrend import parameters
        start = today-timedelta(days=max(config['lookback_days'],365)+parameters(config)[0]*10)
    candles = kite.historical_data(token, start, today, 'day')
    if not candles:
        return {'signal_type': None, 'trigger_date': None, 'error': 'Daily candles unavailable'}
    if config.get('strategy') == 'supertrend':
        from .supertrend import calculate, finalized, parameters
        from .supertrend_scan import filtered_flips
        bars = calculate(finalized(candles), *parameters(config))
        events = filtered_flips(bars, config) if bars else []
        if not events:
            return {'strategy': 'supertrend', 'signal_type': None, 'trigger_date': None}
        event = events[-1]
        return {'strategy': 'supertrend', 'signal_type': 'BUY' if event['bullFlip'] else 'EXIT', 'trigger_date': event['date']}
    frame = scanner.prepare_frame(candles, config)
    events = frame[frame['buy'] | frame['exit']].tail(1)
    if events.empty:
        return {'signal_type': None, 'trigger_date': None}
    event = events.iloc[0]
    return {'signal_type': 'BUY' if bool(event['buy']) else 'EXIT', 'trigger_date': event['date'].strftime('%Y-%m-%d')}


# Compatibility for existing callers; dispatch always follows saved settings.
occ_signal = strategy_signal
