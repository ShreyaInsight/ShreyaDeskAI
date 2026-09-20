"""Broker-owned OCO protection. No price-based SELL decisions or guessed fills."""
import json
import math
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from kiteconnect.exceptions import InputException
from . import autotrade as a

SAFE_INACTIVE = {'deleted', 'cancelled', 'expired', 'rejected', 'disabled'}


def get(position_id):
    conn = a.connection()
    row = conn.execute('SELECT * FROM autotrade_gtts WHERE position_id=?',(position_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def position(position_id):
    conn = a.connection(); row = conn.execute('SELECT * FROM autotrade_positions WHERE id=?',(position_id,)).fetchone(); conn.close()
    return dict(row) if row else None


def update(pid, state, message=None, event=None, **fields):
    conn = a.connection()
    try:
        conn.execute('BEGIN IMMEDIATE')
        old = conn.execute('SELECT * FROM autotrade_gtts WHERE position_id=?',(pid,)).fetchone()
        conn.execute("INSERT INTO autotrade_gtts(position_id,state,last_error,updated_at) VALUES (?,?,?,?) ON CONFLICT(position_id) DO UPDATE SET state=excluded.state,last_error=excluded.last_error,updated_at=excluded.updated_at",(pid,state,message,a.now()))
        if not old:
            conn.execute('UPDATE autotrade_gtts SET account_id=(SELECT account_id FROM autotrade_positions WHERE id=?) WHERE position_id=?',(pid,pid))
        for key,value in fields.items():
            if key not in {'trigger_id','quantity','sl','tp','attempts','request_json','expires_at'}: raise ValueError('Invalid GTT state field')
            conn.execute(f'UPDATE autotrade_gtts SET {key}=? WHERE position_id=?',(value,pid))
        if event and (not old or old['state']!=state or old['last_error']!=message or old['trigger_id']!=fields.get('trigger_id',old['trigger_id'])):
            conn.execute('INSERT INTO autotrade_gtt_events(position_id,trigger_id,event,message,timestamp) VALUES (?,?,?,?,?)',
                         (pid,fields.get('trigger_id',old['trigger_id'] if old else None),event,message or state,a.now()))
        conn.commit()
    finally: conn.close()


def mark_waiting(pid):
    if a.config_for_mode(a.get_config(), 'live')['exit_rule']=='scanner_exit':
        update(pid,'NOT_REQUIRED','Scanner-only exit rule: partial BUY is still awaiting final broker status.')
        return
    update(pid,'WAITING_FILL','Partially filled BUY: awaiting final broker status before setting GTT for the final quantity.',event='GTT pending')


def payload(p, kite):
    from .market_cache import instruments
    rows = instruments(kite)
    tick = next((r.get('tick_size') for r in rows if r.get('tradingsymbol')==p['symbol'] and r.get('segment')=='NSE'),None)
    if tick is None or not math.isfinite(float(tick)) or float(tick)<=0: raise ValueError('Verified NSE instrument tick size unavailable')
    tick = Decimal(str(tick))
    rounded = lambda value: float((Decimal(str(value))/tick).quantize(Decimal('1'),rounding=ROUND_HALF_UP)*tick)
    sl,tp = rounded(p['current_sl']),rounded(p['current_tp'])
    ltp = float(kite.ltp(['NSE:'+p['symbol']])['NSE:'+p['symbol']]['last_price'])
    if not math.isfinite(ltp) or not 0 < sl < ltp < tp:
        raise ValueError('Current price is outside valid SL/TP triggers; position requires attention (no market exit will be substituted)')
    orders = [dict(exchange='NSE',tradingsymbol=p['symbol'],transaction_type='SELL',quantity=p['quantity'],product='CNC',order_type='LIMIT',price=price) for price in (sl,tp)]
    return dict(trigger_type='two-leg',tradingsymbol=p['symbol'],exchange='NSE',trigger_values=[sl,tp],last_price=ltp,orders=orders)


def pending(pid):
    conn=a.connection()
    rows=[r for r in a.unresolved_orders(conn) if r['position_id']==pid]
    conn.close()
    return rows


@a.serialized
def ensure(pid, kite=None):
    """Place only after BUY is terminal; never retry ambiguous broker acceptance."""
    p=position(pid)
    if not p or p['mode']!='live': return
    kite=kite or a.get_kite()
    from .broker_identity import verify
    old=get(pid)
    verify(kite,p,*([old] if old else []))
    if p['status']=='CLOSED':
        if old and old['state']=='TRIGGERED': update(pid,'CLOSED',event='GTT exit filled')
        return
    if p['status']!='OPEN': return
    if old and old['state'] in {'PLACING','UNKNOWN'}:
        update(pid,'UNKNOWN','GTT acceptance unknown. Verify the trigger in Kite and reconcile its ID; automatic retry and app SELL are blocked.',event='GTT uncertain')
        return
    if old and old['trigger_id']:
        return  # Reconciliation owns existing triggers, never create alongside one.
    if a.config_for_mode(a.get_config(), 'live')['exit_rule']=='scanner_exit':
        update(pid,'NOT_REQUIRED','Scanner-only exit rule: no SL/TP GTT requested.')
        return
    if pending(pid): return
    if old and old['state']=='FAILED' and old['attempts']>=3: return
    kite=kite or a.get_kite()
    # Validation/auth/cache failures occur before submission and can be retried.
    try: request=payload(p,kite)
    except Exception as error:
        update(pid,'FAILED',f'Unprotected position: {error}',event='GTT failed',attempts=(old['attempts'] if old else 0)+1)
        return
    for attempt in range((old['attempts'] if old else 0)+1,4):
        update(pid,'PLACING','Awaiting broker GTT confirmation',event='GTT placement pending',attempts=attempt,
               quantity=p['quantity'],sl=request['trigger_values'][0],tp=request['trigger_values'][1],request_json=json.dumps(request))
        try:
            result=kite.place_gtt(**request)
            trigger_id=result.get('trigger_id') if isinstance(result,dict) else None
            if not trigger_id: raise ValueError('GTT response omitted trigger ID')
            update(pid,'ACTIVE',event='GTT Set successfully',trigger_id=str(trigger_id))
            return
        except InputException as error:
            # Explicit broker rejection, unlike a timeout, proves no trigger exists.
            update(pid,'FAILED',f'Unprotected position: GTT rejected: {error}',event='GTT failed')
        except Exception as error:
            update(pid,'UNKNOWN',f'GTT acceptance unknown: {error}. Verify in Kite before retrying.',event='GTT uncertain')
            return


def validate_trigger(p, record, remote):
    if not isinstance(remote,dict) or str(remote.get('id'))!=str(record['trigger_id']): raise ValueError('GTT identity could not be verified')
    condition=remote.get('condition') or {}
    legs=remote.get('orders') or []
    if remote.get('type')!='two-leg' or condition.get('exchange')!='NSE' or condition.get('tradingsymbol')!=p['symbol'] or len(legs)!=2:
        raise ValueError('Broker GTT no longer matches this app position')
    for leg in legs:
        if any(leg.get(k)!=v for k,v in {'exchange':'NSE','tradingsymbol':p['symbol'],'transaction_type':'SELL','product':'CNC','order_type':'LIMIT','quantity':record['quantity']}.items()):
            raise ValueError('GTT leg changed externally; manual reconciliation required')
    if condition.get('trigger_values')!=[record['sl'],record['tp']] or [leg.get('price') for leg in legs]!=[record['sl'],record['tp']]:
        raise ValueError('GTT prices changed externally; manual reconciliation required')


def sync(pid, remote, kite):
    p=position(pid); record=get(pid)
    from .broker_identity import verify
    verify(kite,p,record)
    validate_trigger(p,record,remote)
    state=str(remote.get('status','')).lower()
    results=[(i,leg.get('result')) for i,leg in enumerate(remote['orders']) if leg.get('result')]
    if results or state=='triggered':
        if record['state'] not in {'TRIGGER_FAILED','CLOSED'}:
            update(pid,'TRIGGERED','GTT triggered; awaiting verified SELL completion.',event='GTT triggered')
        if len(results)!=1:
            raise ValueError('Triggered GTT has missing or multiple order results; inspect Kite')
        i,result=results[0]; outcome=result.get('order_result') or {}; oid=outcome.get('order_id')
        leg_name='SL' if i==0 else 'TP'
        try:
            from .telegram_notifications import emit
            emit('GTT triggered',f"{p['symbol']} · {leg_name} · broker order {oid or 'not accepted'}",key=f"gtt:{record['trigger_id']}:{leg_name}",category='safety')
        except Exception:pass
        if not oid:
            update(pid,'TRIGGER_FAILED',f"GTT {leg_name} triggered but SELL was not accepted: {outcome.get('rejection_reason') or 'Order ID unavailable'}",event=f'GTT {leg_name} failed')
            return
        conn=a.connection()
        try:
            conn.execute('BEGIN IMMEDIATE')
            existing=conn.execute("SELECT id,position_id FROM autotrade_orders WHERE mode='live' AND kite_order_id=? AND account_id=?",(str(oid),p['account_id'])).fetchone()
            if existing and existing['position_id']!=pid: raise ValueError('GTT order belongs to another app position')
            if existing:
                owner=conn.execute('SELECT account_id FROM autotrade_orders WHERE id=?',(existing['id'],)).fetchone()[0]
                if owner!=p['account_id']: raise ValueError('GTT order account mismatch')
                local=existing['id']
            else:
                leg=remote['orders'][i]
                local=conn.execute("INSERT INTO autotrade_orders(timestamp,symbol,signal_source,mode,order_type,requested_qty,requested_price,status,kite_order_id,position_id) VALUES (?,?,?,'live','LIMIT',?,?,'PENDING',?,?)",
                    (str(result.get('timestamp') or a.now()),p['symbol'],f'SELL / GTT {leg_name}',leg['quantity'],leg['price'],str(oid),pid)).lastrowid
                conn.execute('UPDATE autotrade_orders SET account_id=? WHERE id=?',(p['account_id'],local))
                conn.execute('INSERT INTO autotrade_gtt_events(position_id,trigger_id,event,message,timestamp) VALUES (?,?,?,?,?)',
                    (pid,record['trigger_id'],f'GTT {leg_name} triggered',f'Broker SELL order {oid}; awaiting fill confirmation',a.now()))
            conn.commit()
        finally: conn.close()
        order=a.reconcile_order(local)
        if order['status'] in {'REJECTED','CANCELLED'}:
            update(pid,'TRIGGER_FAILED',f"GTT {leg_name} SELL {order['status']}: {order['error_message'] or 'Remaining position is unprotected'}",event=f'GTT {leg_name} failed')
        elif position(pid)['status']=='CLOSED':
            update(pid,'CLOSED',event=f'GTT {leg_name} exit filled')
        elif order['status']=='COMPLETE':
            update(pid,'TRIGGER_FAILED','Completed GTT SELL left remaining app quantity; protection requires reconciliation.',event=f'GTT {leg_name} incomplete exit')
        return
    if state=='active':
        if record['quantity']!=p['quantity']: raise ValueError('GTT quantity differs from remaining app position')
        expiry=remote.get('expires_at')
        warning=None
        if expiry:
            deadline=datetime.fromisoformat(str(expiry))
            if deadline.tzinfo is None: deadline=deadline.replace(tzinfo=a.IST)
            if deadline-datetime.now(a.IST)<timedelta(days=7): warning='GTT expires within 7 days; renew in Kite and reconcile before expiry.'
        update(pid,'ACTIVE',warning,event='GTT expiry warning' if warning else None,expires_at=str(expiry) if expiry else None)
    elif state in SAFE_INACTIVE:
        update(pid,state.upper(),f'GTT {state}; remaining position has no active GTT.',event=f'GTT {state}')
    else: raise ValueError('Unrecognized broker GTT status')


@a.serialized
def cancel_before_exit(pid, reason, kite):
    record=get(pid)
    from .broker_identity import verify
    verify(kite,position(pid),*([record] if record else []))
    if modification(pid): raise ValueError('GTT modification awaits broker reconciliation')
    if not record: return
    if record['state'] in {'PLACING','UNKNOWN'}: raise ValueError('GTT placement acceptance is unknown')
    if not record['trigger_id']: return
    remote=kite.get_gtt(record['trigger_id'])
    validate_trigger(position(pid),record,remote)
    if remote.get('status')=='active':
        # Deletion can race with triggering; only a subsequent broker read decides.
        try: kite.delete_gtt(record['trigger_id'])
        except Exception: pass
        remote=kite.get_gtt(record['trigger_id'])
    sync(pid,remote,kite)
    results = [leg['result'] for leg in remote.get('orders',[]) if leg.get('result')]
    if remote.get('status') == 'triggered' and len(results) == 1:
        outcome = results[0].get('order_result') or {}
        oid = outcome.get('order_id')
        if oid:
            conn=a.connection()
            order=conn.execute('SELECT status FROM autotrade_orders WHERE kite_order_id=? AND position_id=?',(str(oid),pid)).fetchone();conn.close()
            if not order or order['status'] not in {'COMPLETE','CANCELLED','REJECTED'}:
                raise ValueError('GTT SELL is still unresolved; another SELL is blocked')
        elif outcome.get('status') != 'failed':
            raise ValueError('GTT SELL acceptance unknown')
        update(pid,'TRIGGER_FAILED',f'GTT exit reconciled before {reason}',event='GTT exit reconciled')
        return
    if remote.get('status') not in SAFE_INACTIVE or results:
        raise ValueError('GTT triggered or cancellation is unconfirmed; reconcile broker SELL before another exit')
    update(pid,'CANCELLED',f'GTT cancelled before {reason}',event='GTT cancelled')


@a.serialized
def reconcile_all():
    conn=a.connection(); rows=[dict(r) for r in conn.execute("SELECT * FROM autotrade_positions WHERE mode='live' AND status='OPEN'")]; conn.close()
    if not rows: return
    try:
        kite=a.get_kite()
        from .broker_identity import verify
        for p in rows:
            record=get(p['id'])
            verify(kite,p,*([record] if record else []))
        book=kite.get_gtts()
        if not isinstance(book,list): raise ValueError('Invalid broker GTT list')
        by_id={str(r['id']):r for r in book}
    except Exception as error:
        for p in rows:
            old=get(p['id'])
            update(p['id'],'UNVERIFIED' if old and old['trigger_id'] else old['state'] if old else 'UNVERIFIED',f'GTT status unavailable: {error}',event='GTT status unavailable')
        return
    for p in rows:
        try:
            record=get(p['id'])
            if record and record['trigger_id']:
                # The list omits inactive triggers older than 7 days. Fetch by ID.
                remote=by_id.get(record['trigger_id'])
                if remote is None: remote=kite.get_gtt(record['trigger_id'])
                confirm_modification(p['id'],remote)
                sync(p['id'],remote,kite)
                config=a.config_for_mode(a.get_config(), 'live')
                if config['mode']=='live' and config['exit_rule']=='scanner_exit' and remote.get('status')=='active':
                    cancel_before_exit(p['id'],'scanner-only exit rule',kite)
            else: ensure(p['id'],kite)
        except Exception as error:
            old=get(p['id'])
            update(p['id'],'UNVERIFIED' if old and old['trigger_id'] else old['state'] if old else 'UNVERIFIED',f'GTT reconciliation required: {error}',event='GTT reconciliation failed')


def public(pid):
    record=get(pid)
    if modification(pid):
        return dict(gtt_status='MODIFYING',gtt_id=record['trigger_id'] if record else None,gtt_message='SL/TP update awaits broker verification; previous local levels retained.')
    if not record: return dict(gtt_status='NOT_CHECKED',gtt_id=None,gtt_message='Protection not yet checked')
    return dict(gtt_status=record['state'],gtt_id=record['trigger_id'],gtt_message=record['last_error'] or ('GTT Set successfully' if record['state']=='ACTIVE' else record['state']))


@a.serialized
def resolve(pid, trigger_id, phrase):
    if phrase != 'RECONCILE GTT': raise ValueError('Type RECONCILE GTT to confirm')
    p=position(pid)
    if not p or p['mode']!='live' or p['status']!='OPEN': raise ValueError('Open live position required')
    if pending(pid): raise ValueError('Reconcile outstanding orders first')
    record=get(pid);kite=a.get_kite()
    from .broker_identity import verify
    verify(kite,p,*([record] if record else []))
    if trigger_id:
        if not record or record['state'] not in {'PLACING','UNKNOWN'}: raise ValueError('Attach an ID only for an uncertain GTT placement')
        candidate={**record,'trigger_id':str(trigger_id)}
        remote=kite.get_gtt(str(trigger_id))
        validate_trigger(p,candidate,remote)
        conn=a.connection();exists=conn.execute('SELECT position_id FROM autotrade_gtts WHERE trigger_id=? AND position_id!=? AND account_id=?',(str(trigger_id),pid,p['account_id'])).fetchone();conn.close()
        if exists: raise ValueError('GTT already belongs to another position')
        update(pid,'VERIFYING',event='GTT ID manually reconciled',trigger_id=str(trigger_id))
        sync(pid,remote,kite)
    else:
        if record and record['state'] in {'PLACING','UNKNOWN'}: raise ValueError('Provide the broker GTT ID; uncertain placement cannot be retried blindly')
        if record and record['trigger_id']:
            cancel_before_exit(pid,'explicit protection renewal',kite)
        update(pid,'RETRY',event='GTT protection retry requested',trigger_id=None,attempts=0)
        ensure(pid,kite)
    return public(pid)


def modification(pid):
    conn=a.connection()
    try:
        row=conn.execute('SELECT payload FROM autotrade_gtt_modifications WHERE position_id=?',(pid,)).fetchone()
        return json.loads(row[0]) if row else None
    finally: conn.close()


def confirm_modification(pid, remote):
    request=modification(pid)
    if not request: return
    p=position(pid);record=get(pid)
    sl,tp=request['trigger_values']
    candidate={**record,'sl':sl,'tp':tp,'quantity':request['orders'][0]['quantity']}
    if not p or p['mode']!='live' or p['status']!='OPEN' or p['quantity']!=candidate['quantity']:
        raise ValueError('Position changed while modifying protection; reconcile first')
    try:
        validate_trigger(p,candidate,remote)
    except ValueError:
        # Original GTT may trigger before modification reaches the broker.
        if remote.get('status')!='triggered': raise
        validate_trigger(p,record,remote)
        conn=a.connection()
        conn.execute('DELETE FROM autotrade_gtt_modifications WHERE position_id=?',(pid,));conn.commit();conn.close()
        return  # Caller reconciles the original triggered SELL without changing levels.
    if remote.get('status') not in {'active','triggered'}:
        raise ValueError('Modified GTT is not active; reconciliation required')
    conn=a.connection()
    try:
        conn.execute('BEGIN IMMEDIATE')
        conn.execute('UPDATE autotrade_positions SET current_sl=?,current_tp=? WHERE id=?',(sl,tp,pid))
        conn.execute("UPDATE autotrade_gtts SET sl=?,tp=?,request_json=?,state='ACTIVE',last_error=NULL,updated_at=? WHERE position_id=?",(sl,tp,json.dumps(request),a.now(),pid))
        conn.execute('DELETE FROM autotrade_gtt_modifications WHERE position_id=?',(pid,))
        conn.execute('INSERT INTO autotrade_gtt_events(position_id,trigger_id,event,message,timestamp) VALUES (?,?,?,?,?)',(pid,record['trigger_id'],'GTT modified',f'Broker verified SL {sl}, TP {tp}',a.now()))
        conn.commit()
    finally: conn.close()


@a.serialized
def apply_existing(phrase):
    if phrase != 'APPLY LIVE SL TP': raise ValueError('Type APPLY LIVE SL TP to confirm')
    config=a.config_for_mode(a.get_config(),'live')
    if config['exit_rule']=='scanner_exit': raise ValueError('Save a GTT exit rule before applying SL/TP')
    kite=a.get_kite()
    conn=a.connection()
    rows=[dict(r) for r in conn.execute("SELECT * FROM autotrade_positions WHERE mode='live' AND status='OPEN'")]
    conn.close();results=[]
    for p in rows:
        pid=p['id']
        try:
            record=get(pid)
            from .broker_identity import verify
            verify(kite,p,*([record] if record else []))
            if modification(pid): raise ValueError('Previous GTT modification awaits broker reconciliation')
            if pending(pid): raise ValueError('Outstanding order: reconcile before changing protection')
            if not record or not record['trigger_id'] or record['state']!='ACTIVE':
                raise ValueError('No verified active GTT; use protection recovery first')
            remote=kite.get_gtt(record['trigger_id']);validate_trigger(p,record,remote)
            if remote.get('status')!='active' or any(leg.get('result') for leg in remote['orders']):
                raise ValueError('GTT already triggered or inactive; reconcile first')
            if record['quantity']!=p['quantity']: raise ValueError('GTT quantity mismatch; reconcile first')
            request=payload({**p,'current_sl':p['entry_price']*(1-config['sl_pct']/100),'current_tp':p['entry_price']*(1+config['tp_pct']/100)},kite)
            conn=a.connection()
            conn.execute('INSERT INTO autotrade_gtt_modifications VALUES (?,?)',(pid,json.dumps(request)));conn.commit();conn.close()
            update(pid,'MODIFYING','Awaiting broker verification of changed SL/TP',event='GTT modification pending')
            try: kite.modify_gtt(record['trigger_id'],**request)
            except InputException:
                conn=a.connection();conn.execute('DELETE FROM autotrade_gtt_modifications WHERE position_id=?',(pid,));conn.commit();conn.close()
                update(pid,record['state'],'Broker rejected SL/TP change; previous levels retained.',event='GTT modification rejected')
                raise
            except Exception:
                pass  # Never retry an ambiguous write; verify the existing trigger by ID.
            remote=kite.get_gtt(record['trigger_id'])
            confirm_modification(pid,remote)
            sync(pid,remote,kite)
            if remote.get('status')=='triggered':
                raise ValueError('GTT triggered during update; broker exit reconciliation performed')
            results.append(dict(position_id=pid,symbol=p['symbol'],status='UPDATED',sl=request['trigger_values'][0],tp=request['trigger_values'][1]))
        except Exception as error:
            old=get(pid)
            update(pid,'MODIFYING' if modification(pid) else old['state'] if old else 'NOT_CHECKED',str(error),event='GTT modification failed')
            results.append(dict(position_id=pid,symbol=p['symbol'],status='FAILED',reason=str(error)))
    return {'results':results,'sl_pct':config['sl_pct'],'tp_pct':config['tp_pct']}
