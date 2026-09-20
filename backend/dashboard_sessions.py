"""Dashboard-session OAuth state and credential-free access audit metadata."""
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from . import main as m

metadata = {}


def stamp(): return datetime.now(timezone.utc).isoformat()


def audit(token, kind, request):
    from .autotrade import connection
    with m.session_lock:
        ref=metadata.get(token,{}).get('id')
    conn=connection()
    try:
        conn.execute('CREATE TABLE IF NOT EXISTS dashboard_access_events(id INTEGER PRIMARY KEY,session_ref TEXT,kind TEXT NOT NULL,ip TEXT,user_agent TEXT,created_at TEXT NOT NULL)')
        conn.execute('INSERT INTO dashboard_access_events(session_ref,kind,ip,user_agent,created_at) VALUES (?,?,?,?,?)',
                     (ref,kind,request.client.host if request.client else 'unknown',request.headers.get('user-agent','')[:500],stamp()))
        conn.execute('DELETE FROM dashboard_access_events WHERE created_at<?',((datetime.now(timezone.utc)-timedelta(days=90)).isoformat(),))
        conn.commit()
    finally: conn.close()


def create(token, request):
    with m.session_lock:
        for expired in [key for key,until in m.sessions.items() if until<=m.time.time()]:
            m.sessions.pop(expired,None);metadata.pop(expired,None)
        metadata[token]=dict(id=uuid.uuid4().hex,created_at=stamp(),last_seen=stamp(),ip=request.client.host if request.client else 'unknown',user_agent=request.headers.get('user-agent','')[:500],kite_pending=False)
    audit(token,'dashboard_login',request)


def pending(token):
    with m.session_lock: return bool(metadata.get(token,{}).get('kite_pending'))


def begin(request):
    if request.headers.get('sec-fetch-site')=='cross-site':
        raise m.HTTPException(status_code=403,detail='Start Kite login from this dashboard.')
    token=request.cookies.get('shreyadesk_session')
    nonce=secrets.token_urlsafe(32)
    with m.session_lock:
        if token not in metadata: raise m.HTTPException(status_code=401,detail='Please sign in again.')
        metadata[token].pop('account_candidate',None)
        metadata[token].update(kite_pending=True,oauth_consumed=None,oauth_state=nonce,oauth_expires=m.time.time()+600)
    audit(token,'kite_login_started',request)
    return nonce


def consume(request, state):
    token=request.cookies.get('shreyadesk_session')
    with m.session_lock:
        row=metadata.get(token,{})
        expected=row.get('oauth_state')
        if not state or not expected or not secrets.compare_digest(state,expected) or row.get('oauth_expires',0)<=m.time.time():
            raise m.HTTPException(status_code=403,detail='Kite login was not initiated by this session or has expired. Start Connect to Kite again.')
        row.pop('oauth_state',None)
        row['oauth_consumed']=state
        request.state.kite_flow=state
    return token


def complete(token, request, connection):
    from .autotrade import execution_lock
    from . import broker_identity as identity
    with execution_lock:
        with m.session_lock:
            if token not in metadata or m.sessions.get(token,0)<=m.time.time(): raise m.HTTPException(status_code=401,detail='Dashboard session expired.')
            if metadata[token].get('oauth_consumed') != getattr(request.state,'kite_flow',None) or metadata[token].get('oauth_state'):
                raise m.HTTPException(status_code=409,detail='A newer Kite login was started. Complete that flow instead.')
            accepted=identity.accept_connection(connection)
            if accepted:
                m.save_session(connection)
                metadata[token]['kite_pending']=False
                metadata[token].pop('account_candidate',None)
            else:
                # Keep the old credential active. Candidate exists only in this
                # authenticated session's memory and expires with OAuth intent.
                metadata[token]['account_candidate']=connection
                metadata[token]['account_candidate_expires']=m.time.time()+600
            metadata[token].pop('oauth_consumed',None)
    audit(token,'kite_connected' if accepted else 'broker_account_change_pending',request)
    return accepted


def account_change(token):
    from . import broker_identity as identity
    with m.session_lock:
        row=metadata.get(token,{})
        candidate=row.get('account_candidate')
        if not candidate:return None
        result={'to_account':candidate['account_id'],'expired':row.get('account_candidate_expires',0)<=m.time.time()}
    return {**result,'from_account':identity.owner(),'blockers':identity.blockers(),'legacy':identity.legacy_counts()['total']}


def resolve_account_change(request, action, phrase):
    from . import broker_identity as identity
    from .autotrade import execution_lock
    token=request.cookies.get('shreyadesk_session')
    with execution_lock:
        with m.session_lock:
            row=metadata.get(token)
            if not row or m.sessions.get(token,0)<=m.time.time():raise ValueError('Session expired.')
            candidate=row.get('account_candidate')
            if not candidate:raise ValueError('No pending account switch. Start Connect to Kite again.')
            if action=='keep':
                row.pop('account_candidate',None);row['kite_pending']=False
                identity.save({**identity.state(),'error':None})
            elif action=='switch':
                if row.get('account_candidate_expires',0)<=m.time.time():raise ValueError('Account switch expired. Start Connect to Kite again.')
                kite=m.KiteConnect(api_key=candidate['api_key']);kite.set_access_token(candidate['access_token'])
                if identity.profile_id(kite)!=candidate['account_id']:raise ValueError('Candidate account identity changed; restart OAuth.')
                from . import risk
                previous_identity=identity.state();previous_risk=risk.load()
                try:
                    identity.finish_switch(candidate,phrase)
                    m.save_session(candidate)
                except Exception:
                    identity.save(previous_identity);risk.save(previous_risk)
                    raise
                row.pop('account_candidate',None);row['kite_pending']=False
            else:raise ValueError('Choose keep or switch.')
    audit(token,'broker_account_kept' if action=='keep' else 'broker_account_switched',request)
    return {'resolved':True,'paused':True}


def activity(request):
    from .autotrade import connection
    current=request.cookies.get('shreyadesk_session')
    with m.session_lock:
        active=[{k:v for k,v in row.items() if k in {'id','created_at','last_seen','ip','user_agent','kite_pending'}} | {'current':token==current}
                for token,row in metadata.items() if m.sessions.get(token,0)>m.time.time()]
    conn=connection()
    try:
        conn.execute('CREATE TABLE IF NOT EXISTS dashboard_access_events(id INTEGER PRIMARY KEY,session_ref TEXT,kind TEXT NOT NULL,ip TEXT,user_agent TEXT,created_at TEXT NOT NULL)')
        rows=[dict(r) for r in conn.execute('SELECT * FROM dashboard_access_events ORDER BY id DESC LIMIT 50')]
        latest=conn.execute("SELECT * FROM dashboard_access_events WHERE kind='kite_connected' ORDER BY id DESC LIMIT 1").fetchone()
        return dict(active_sessions=active,recent_logins=rows,last_kite_login=dict(latest) if latest else None)
    finally: conn.close()
