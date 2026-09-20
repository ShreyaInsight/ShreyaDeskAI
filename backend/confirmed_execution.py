"""Confirmed signals share existing preflight, risk checks and LIMIT submission."""
from datetime import datetime
from . import autotrade as a
from .confirmed_resolution import enabled, strategy_key, eligibility
from .analytics import audit_skip


@a.serialized
def process(signals, run_id):
    from .scanner import get_config
    from .market_cache import live_quotes, overlay_quote
    from .alternate_resolution import session_calendar
    from datetime import date
    cfg=get_config(); execution=a.get_config(); mode=execution['mode']
    candidates=[]
    current=datetime.now(a.IST)
    for signal in signals:
        reason=None
        if not enabled(cfg) or signal.get('strategy_key')!=strategy_key(cfg):reason='Confirmed strategy settings changed; scan again'
        elif not execution['enabled'] or execution['paused'] or execution['kill_switch']:reason='Execution disabled, paused or kill switch latched'
        elif not signal.get('confirmed_eligible'):reason=signal.get('confirmed_reason','Confirmed crossover is not eligible')
        elif signal.get('execution_session')!=current.date().isoformat():reason='Confirmed execution session expired; scan again'
        elif not a._market_is_open():reason='Waiting for permitted trading session'
        if reason:
            audit_skip(signal['symbol'],'BUY' if signal['signal_type']=='BUY' else 'SELL',mode,reason,run_id)
            continue
        candidates.append(signal)
    if not candidates:return
    try: quotes=live_quotes(a.get_kite(),sorted({r['symbol'] for r in candidates}))
    except Exception:
        for s in candidates:audit_skip(s['symbol'],'BUY' if s['signal_type']=='BUY' else 'SELL',mode,'Fresh confirmed-execution quotes unavailable',run_id)
        return
    for s in candidates:
        symbol=s['symbol']; side='BUY' if s['signal_type']=='BUY' else 'SELL'; block=s['confirmed_block_end']
        try:
            latest_cfg=get_config()
            if not enabled(latest_cfg) or strategy_key(latest_cfg)!=s['strategy_key']:
                raise ValueError('Confirmed strategy changed before submission; scan again')
            end=date.fromisoformat(block)
            sessions=session_calendar(end,current.date())
            q=quotes.get('NSE:'+symbol)
            ok,reason=eligibility(end,end,sessions,q,current)
            if not ok:raise ValueError(reason)
            overlay_quote([],q,current)  # Validate OHLCV without inventing a candle.
            price=float(q['last_price'])
            conn=a.connection()
            try:
                duplicate=conn.execute('SELECT 1 FROM autotrade_confirmed_events WHERE symbol=? AND mode=? AND side=? AND block_end=?',(symbol,mode,side,block)).fetchone()
                position=conn.execute("SELECT * FROM autotrade_positions WHERE symbol=? AND mode=? AND status='OPEN'",(symbol,mode)).fetchone()
            finally:conn.close()
            if duplicate:raise ValueError('Confirmed 3D crossover already processed')
            if side=='SELL':
                if execution['exit_rule'] not in {'scanner_exit','whichever_first'}:raise ValueError('Scanner exits disabled by exit rule')
                if not position:raise ValueError('No open app position to close')
                if datetime.fromisoformat(position['entry_time']).astimezone(a.IST).date()>end:raise ValueError('Exit crossover predates the app position')
            if mode=='paper':
                a.process_paper_signals([{**s,'trigger_price':price,'_confirmed_key':block}],run_id)
            elif side=='BUY':
                quantity=int(execution['max_symbol_value']//price)
                a._record_live_order(symbol,'confirmed 3D signal','BUY',quantity,price,run_id=run_id,confirmed_key=block,confirmed_strategy=s['strategy_key'])
            else:
                a._record_live_order(symbol,'confirmed 3D exit','SELL',position['quantity'],price,run_id=run_id,position_id=position['id'],confirmed_key=block,confirmed_strategy=s['strategy_key'])
        except (ValueError,TypeError,KeyError) as error:
            audit_skip(symbol,side,mode,str(error),run_id)
