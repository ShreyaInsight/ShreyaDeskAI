"""Strategy identity and isolated access to saved scanner runs."""
import json


def name(config):
    return config.get('strategy', 'occ')


def matches(saved, selected):
    if name(saved) != name(selected):
        return False
    if name(selected) == 'supertrend':
        from .supertrend_scan import strategy_key
        return saved.get('supertrend_filter_version') == 1 and strategy_key(saved) == strategy_key(selected)
    return True


def selected_run(conn, config):
    for row in conn.execute('SELECT id,ran_at,payload FROM scanner_runs ORDER BY id DESC'):
        payload = json.loads(row['payload'])
        if matches(payload.get('config', {}), config):
            return row, payload
    return None, None


def results_snapshot():
    from .scanner import db, DEFAULT_CONFIG
    conn = db()
    try:
        conn.execute('BEGIN')
        row = conn.execute('SELECT payload FROM scanner_config WHERE id=1').fetchone()
        config = {**DEFAULT_CONFIG, **(json.loads(row['payload']) if row else {})}
        run, payload = selected_run(conn, config)
        results = [] if run is None else [json.loads(r['payload']) for r in conn.execute('SELECT payload FROM scanner_signals WHERE run_id=? ORDER BY trigger_date DESC', (run['id'],))]
        results = [r for r in results if r.get('strategy','occ') == name(config)]
        last = None if run is None else dict(run_id=run['id'],ran_at=run['ran_at'],scanned=payload.get('count',0),duration_seconds=payload.get('duration_seconds'),skipped_quotes=payload.get('skipped_quotes',[]),strategy=name(config))
        return dict(config=config,results=results,last_run=last)
    finally:
        conn.close()


def position_strategy(position):
    from .scanner import DB_PATH
    import sqlite3
    run_id=dict(position).get('scan_run_id')
    if run_id is None:return 'occ'
    conn=sqlite3.connect(f'file:{DB_PATH}?mode=ro',uri=True)
    conn.row_factory=sqlite3.Row
    try:
        row=conn.execute('SELECT payload FROM scanner_runs WHERE id=?',(run_id,)).fetchone()
        return name(json.loads(row['payload']).get('config',{})) if row else 'occ'
    finally:conn.close()
