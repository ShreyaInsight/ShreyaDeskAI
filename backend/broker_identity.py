"""Fail-closed ownership checks. No record is rebound by a token change."""
import json
from . import autotrade as a


class AccountIdentityError(ValueError):
    pass


def state():
    c=a.connection()
    try:
        row=c.execute('SELECT payload FROM broker_identity_state WHERE id=1').fetchone()
        return json.loads(row[0]) if row else {}
    finally:c.close()


def save(values):
    c=a.connection()
    try:
        c.execute('INSERT INTO broker_identity_state VALUES (1,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload',(json.dumps(values),));c.commit()
    finally:c.close()


def owner():
    known=state().get('account_id')
    if known:return known
    from . import risk
    return risk.load().get('broker_user_id')


def profile_id(kite):
    try:
        value=kite.profile().get('user_id')
        if not isinstance(value,str) or not value.strip():raise ValueError()
        return value.strip()
    except Exception as error:
        raise AccountIdentityError('Broker account identity could not be verified; reconnect Kite before continuing.') from error


def alert(message):
    # Persist pause without get_config recursion, and before notification delivery.
    c=a.connection()
    try:
        c.execute('BEGIN IMMEDIATE')
        row=c.execute('SELECT payload FROM autotrade_config WHERE id=1').fetchone()
        config=json.loads(row[0]) if row else dict(a.DEFAULT_CONFIG)
        config.update(enabled=False,paused=True)
        c.execute('INSERT INTO autotrade_config VALUES (1,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload,updated_at=excluded.updated_at',(json.dumps(config),a.now()))
        row=c.execute('SELECT payload FROM broker_identity_state WHERE id=1').fetchone()
        current=json.loads(row[0]) if row else {}
        current['error']=message
        c.execute('INSERT INTO broker_identity_state VALUES (1,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload',(json.dumps(current),));c.commit()
    finally:c.close()
    try:
        from .telegram_notifications import emit
        emit('BROKER ACCOUNT SAFETY ALERT',message,category='safety',key='account:'+message)
    except Exception:pass


def verify(kite, *records, initialize=False):
    try:
        actual=profile_id(kite)
        expected=owner()
        if expected and actual!=expected:
            raise AccountIdentityError(f'Broker account mismatch: workspace belongs to {expected}, connected account is {actual}. Trading paused. Reconnect the original account or resolve the account switch.')
        for record in records:
            record=dict(record)
            if record.get('mode')=='paper':continue
            if not record.get('account_id'):
                raise AccountIdentityError('Legacy broker record has unverified ownership. Confirm legacy ownership before reconciliation.')
            if record['account_id']!=actual:
                raise AccountIdentityError(f"Broker account mismatch: record belongs to {record['account_id']}, connected account is {actual}. Action blocked.")
        if not expected:
            if not initialize:raise AccountIdentityError('Broker workspace identity has not been initialized. Connect Kite or initialize the baseline first.')
            if legacy_counts()['total']:
                raise AccountIdentityError('Legacy broker records need explicit ownership confirmation before initialization.')
            save({**state(),'account_id':actual,'error':None})
        # Clear only a transient profile-read failure after a fresh successful
        # check. Ownership/OAuth mismatch alerts still need their explicit flow.
        current=state()
        if current.get('error')=='Broker account identity could not be verified; reconnect Kite before continuing.':
            save({**current,'error':None})
        return actual
    except AccountIdentityError as error:
        alert(str(error));raise


def legacy_counts():
    c=a.connection()
    try:
        counts={table:c.execute(f"SELECT COUNT(*) FROM {table} WHERE account_id IS NULL"+('' if table=='autotrade_gtts' else " AND mode='live' AND "+("status!='BLOCKED'" if table=='autotrade_orders' else '1=1'))).fetchone()[0] for table in ('autotrade_positions','autotrade_orders','autotrade_gtts')}
        return {**counts,'total':sum(counts.values())}
    finally:c.close()


def blockers():
    c=a.connection()
    try:
        positions=c.execute("SELECT COUNT(*) FROM autotrade_positions WHERE mode='live' AND status!='CLOSED'").fetchone()[0]
        orders=len(a.unresolved_orders(c))
        gtts=c.execute("SELECT COUNT(*) FROM autotrade_gtts WHERE (trigger_id IS NOT NULL OR state IN ('PLACING','UNKNOWN')) AND state NOT IN ('CLOSED','CANCELLED','DELETED','EXPIRED','REJECTED','DISABLED')").fetchone()[0]
        changes=c.execute('SELECT COUNT(*) FROM autotrade_gtt_modifications').fetchone()[0]
        return dict(positions=positions,orders=orders,gtts=gtts,modifications=changes)
    finally:c.close()


def legacy_records():
    c=a.connection()
    try:
        rows=[]
        for r in c.execute("SELECT id,symbol,quantity,kite_order_id_entry,status FROM autotrade_positions WHERE mode='live' AND account_id IS NULL"):
            rows.append(dict(kind='Position',id=r['id'],symbol=r['symbol'],quantity=r['quantity'],broker_ref=r['kite_order_id_entry'],status=r['status']))
        for r in c.execute("SELECT id,symbol,requested_qty,kite_order_id,status FROM autotrade_orders WHERE mode='live' AND account_id IS NULL AND status!='BLOCKED'"):
            rows.append(dict(kind='Order',id=r['id'],symbol=r['symbol'],quantity=r['requested_qty'],broker_ref=r['kite_order_id'],status=r['status']))
        for r in c.execute("SELECT g.position_id,p.symbol,g.quantity,g.trigger_id,g.state FROM autotrade_gtts g LEFT JOIN autotrade_positions p ON p.id=g.position_id WHERE g.account_id IS NULL"):
            rows.append(dict(kind='GTT',id=r['position_id'],symbol=r['symbol'],quantity=r['quantity'],broker_ref=r['trigger_id'],status=r['state']))
        return rows
    finally:c.close()


def public():
    value=state()
    from . import risk
    risk_state=risk.public_state()
    return {'risk_history_error':risk_state['risk_history_error'],'risk_checked_at':risk_state['risk_checked_at'],'account_id':owner(),'error':value.get('error'),'legacy':legacy_counts(),'legacy_records':legacy_records(),'blockers':blockers()}


@a.serialized
def bind_legacy(account_id, phrase):
    if phrase!=f'BIND LEGACY RECORDS TO {account_id}':raise ValueError('Type the exact legacy ownership confirmation.')
    kite=a.get_kite();actual=profile_id(kite);expected=owner()
    if actual!=account_id or (expected and expected!=actual):
        message='Legacy ownership confirmation must match both the connected account and the recorded baseline account.'
        alert(message);raise AccountIdentityError(message)
    a.pause()
    c=a.connection()
    try:
        c.execute('BEGIN IMMEDIATE')
        # Never overwrite an existing owner or cross-account relationship.
        accounts={r[0] for table in ('autotrade_positions','autotrade_orders','autotrade_gtts') for r in c.execute(f'SELECT DISTINCT account_id FROM {table} WHERE account_id IS NOT NULL')}
        if accounts-{actual}:raise ValueError('Mixed recorded owners require per-record investigation; bulk legacy binding is blocked.')
        counts=0
        for table in ('autotrade_positions','autotrade_orders','autotrade_gtts'):
            where="account_id IS NULL"+('' if table=='autotrade_gtts' else " AND mode='live'"+(" AND status!='BLOCKED'" if table=='autotrade_orders' else ''))
            counts+=c.execute(f'UPDATE {table} SET account_id=? WHERE {where}',(actual,)).rowcount
        c.execute('INSERT INTO broker_identity_audit(event,account_id,details,timestamp) VALUES (?,?,?,?)',('legacy_owner_confirmed',actual,json.dumps({'records':counts,'attestation':'User verified legacy trades belong to this account'}),a.now()))
        c.commit()
    except Exception:c.rollback();raise
    finally:c.close()
    save({**state(),'account_id':actual,'error':None})
    # Ownership and P&L are separate checks. Re-evaluate, never blindly clear
    # a stale error: quotes, history gaps and loss latches must still apply.
    from . import risk
    try:
        risk.check(kite,block_buy=False)
    except Exception as error:
        risk.record_error(f'Ownership confirmed; risk verification failed: {error}')
    return public()


def accept_connection(connection):
    """Called under execution_lock, after OAuth/profile and session validation."""
    actual=connection['account_id'];expected=owner()
    if expected and expected!=actual:
        alert(f'OAuth account change from {expected} to {actual} awaits confirmation. Trading paused; original Kite connection retained.')
        return False
    if not expected and legacy_counts()['total']:
        # Persist no owner; the user must attest legacy ownership first.
        a.pause()
    else:save({**state(),'account_id':actual,'error':None})
    return True


def finish_switch(connection, phrase):
    actual=connection['account_id']
    if phrase!=f'SWITCH BROKER ACCOUNT TO {actual}':raise ValueError('Type the exact account-switch confirmation.')
    if legacy_counts()['total'] or any(blockers().values()):
        raise ValueError('Resolve existing positions, orders and GTTs using the original account before switching. The original connection has been retained.')
    a.pause()
    # Historical records retain their original ownership. The new account needs
    # a separately confirmed P&L baseline; no loss/manual latch is cleared here.
    from . import risk
    value=risk.load();value.update(baseline_requires_reset=True,history_error='Broker account changed. Explicitly initialize a new account baseline before live activation.')
    risk.save(value)
    save({**state(),'account_id':actual,'error':None})
    c=a.connection()
    try:
        c.execute('INSERT INTO broker_identity_audit(event,account_id,details,timestamp) VALUES (?,?,?,?)',('account_switch_confirmed',actual,'Historical record ownership unchanged; new baseline required',a.now()));c.commit()
    finally:c.close()
