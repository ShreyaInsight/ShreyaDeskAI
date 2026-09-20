"""Idempotent accounting of cumulative broker fills, including partial orders."""
import math
from . import autotrade as a


def apply(order, detail, conn):
    if not order.get('account_id'): raise ValueError('Order ownership is unverified; accounting blocked')
    raw = detail.get('filled_quantity')
    if raw is None:
        if detail.get('status') == 'COMPLETE':
            raise ValueError('COMPLETE order missing actual filled quantity')
        return None
    qty = int(raw)
    if qty != float(raw) or not 0 <= qty <= order['requested_qty']:
        raise ValueError('Invalid broker filled quantity')
    prior = int(order.get('accounted_quantity') or 0)
    if qty < prior:
        raise ValueError('Broker filled quantity regressed')
    if not qty: return None
    price = float(detail.get('average_price') or 0)
    if not math.isfinite(price) or price <= 0:
        raise ValueError('Partial fill requires a verified average fill price')
    value = qty * price
    prior_value = float(order.get('accounted_value') or 0)
    if qty == prior and value == prior_value:
        existing=conn.execute('SELECT status FROM autotrade_positions WHERE id=?',(order['position_id'],)).fetchone()
        if not existing or existing['status']!='EXIT_PENDING' or detail.get('status')!='COMPLETE':
            return order['position_id']
    side = 'BUY' if order['signal_source'].startswith('BUY') else 'SELL'
    from datetime import datetime
    dt=datetime.fromisoformat(str(detail.get('exchange_update_timestamp') or a.now()))
    stamp=(dt.replace(tzinfo=a.IST) if dt.tzinfo is None else dt.astimezone(a.IST)).isoformat()
    if side == 'BUY':
        row = conn.execute("SELECT * FROM autotrade_positions WHERE mode='live' AND kite_order_id_entry=? AND account_id=?",(order['kite_order_id'],order['account_id'])).fetchone()
        if row and row['account_id'] != order['account_id']: raise ValueError('Order/position account mismatch')
        if row and row['sold_quantity']:
            raise ValueError('BUY fill changed after an exit; reconciliation required')
        if not row:
            config = a.config_for_mode(a.get_config(), 'live')
            cur = conn.execute("""INSERT INTO autotrade_positions(symbol,mode,status,entry_time,entry_price,quantity,current_sl,current_tp,kite_order_id_entry,scan_run_id)
                VALUES (?,'live','OPEN',?,?,?,?,?,?,?)""",(order['symbol'],stamp,price,qty,price*(1-config['sl_pct']/100),price*(1+config['tp_pct']/100),order['kite_order_id'],order['scan_run_id']))
            pid = cur.lastrowid
            conn.execute('UPDATE autotrade_positions SET account_id=? WHERE id=?',(order['account_id'],pid))
        else:
            pid = row['id']
            if qty != prior or value != prior_value:
                conn.execute('UPDATE autotrade_positions SET quantity=?,entry_price=?,current_sl=?,current_tp=? WHERE id=?',
                             (qty,price,price*row['current_sl']/row['entry_price'],price*row['current_tp']/row['entry_price'],pid))
        conn.execute('UPDATE autotrade_orders SET position_id=? WHERE id=?',(pid,order['id']))
    else:
        pid = order['position_id']
        row = conn.execute('SELECT * FROM autotrade_positions WHERE id=?',(pid,)).fetchone()
        if not row: raise ValueError('SELL fill has no matching app position')
        if row['account_id'] != order['account_id']: raise ValueError('Order/position account mismatch')
        delta = qty-prior
        if delta > row['quantity'] or (row['status']=='CLOSED' and delta):
            raise ValueError('SELL fill exceeds remaining app quantity')
        sold = row['sold_quantity']+delta
        proceeds = row['sold_value']+value-prior_value
        remaining = row['quantity']-delta if row['status']=='OPEN' else 0
        pnl = proceeds-row['entry_price']*sold
        if remaining:
            conn.execute('UPDATE autotrade_positions SET quantity=?,sold_quantity=?,sold_value=?,realized_pnl=? WHERE id=?',(remaining,sold,proceeds,pnl,pid))
        else:
            final_status='CLOSED' if detail.get('status')=='COMPLETE' else 'EXIT_PENDING'
            conn.execute("UPDATE autotrade_positions SET status=?,quantity=?,sold_quantity=?,sold_value=?,realized_pnl=?,exit_time=?,exit_price=?,exit_reason=?,kite_order_id_exit=? WHERE id=?",
                         (final_status,sold,sold,proceeds,pnl,stamp,proceeds/sold,order['signal_source'],order['kite_order_id'],pid))
    conn.execute('UPDATE autotrade_orders SET accounted_quantity=?,accounted_value=? WHERE id=?',(qty,value,order['id']))
    if qty != prior or value != prior_value:
        conn.execute('INSERT INTO autotrade_fill_events(order_id,position_id,side,quantity,value,timestamp) VALUES (?,?,?,?,?,?)', (order['id'],pid,side,qty,value,stamp))
    return pid


def positions_at_day(rows, day):
    """Reconstruct quantity and realized proceeds from cumulative fill observations."""
    from datetime import datetime
    conn=a.connection()
    events=[dict(r) for r in conn.execute('SELECT * FROM autotrade_fill_events ORDER BY id')]
    conn.close()
    result=[]
    for original in rows:
        row=original.copy()
        own=[e for e in events if e['position_id']==row['id']]
        if not own:
            result.append(row);continue
        latest={}
        for event in own:
            dt=datetime.fromisoformat(event['timestamp'])
            if dt.tzinfo is None:dt=dt.replace(tzinfo=a.IST)
            if dt.astimezone(a.IST).date()<=day:latest[event['order_id']]=event
        buys=[e for e in latest.values() if e['side']=='BUY']
        sells=[e for e in latest.values() if e['side']=='SELL']
        if any(e['side']=='BUY' for e in own):
            quantity=sum(e['quantity'] for e in buys)
            if not quantity:continue
            entry=sum(e['value'] for e in buys)/quantity
        else:
            # A position created before fill-ledger migration has its original cost.
            quantity=row['quantity']+(row['sold_quantity'] if row['status']=='OPEN' else 0)
            entry=row['entry_price']
        sold=sum(e['quantity'] for e in sells);proceeds=sum(e['value'] for e in sells)
        if sold>quantity:raise ValueError('Historical SELL quantity exceeds recorded BUY fills')
        row.update(quantity=quantity-sold if sold<quantity else quantity,entry_price=entry,
                   sold_quantity=sold,sold_value=proceeds,realized_pnl=proceeds-entry*sold,
                   status='CLOSED' if sold==quantity else 'OPEN',exit_price=proceeds/sold if sold==quantity else None)
        result.append(row)
    return result
