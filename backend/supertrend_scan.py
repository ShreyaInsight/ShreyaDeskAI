"""Daily Supertrend scan and confirmed execution through the existing order gate."""
import hashlib
import json
from datetime import datetime, timedelta, date
from time import perf_counter
from . import scanner as s
from . import market_cache as cache
from .supertrend import cached_bars, parameters, refresh_close
from .holdings import positive


SHARED_KEYS = ('sl_pct', 'tsl_activation_pct', 'tsl_pct', 'tp_pct',
    'pe_filter_operator', 'pe_filter_value', 'min_52w_high_distance_pct',
    'max_52w_high_distance_pct', 'rsi_min', 'rsi_max', 'volume_min', 'volume_max',
    'volume_above_30d_average', 'adx_enabled', 'adx_threshold', 'adx_auto',
    'adx_lookback', 'max_signal_age_days')


def strategy_key(config):
    length, factor = parameters(config)
    keys = ('lookback_days', 'universe_mode', 'index_name', 'watchlist') + SHARED_KEYS
    values = {k: config.get(k, s.DEFAULT_CONFIG.get(k)) for k in keys}
    return hashlib.sha256(json.dumps(['supertrend-filters-v1', length, factor, values], sort_keys=True).encode()).hexdigest()


def filtered_flips(bars, cfg):
    """Filter scanner BUY candidates without modifying the indicator/chart."""
    if not cfg.get('adx_enabled'):
        return [b for b in bars if b['bullFlip'] or b['bearFlip']]
    strength = s.adx(s.pd.DataFrame(bars))
    thresholds = strength.rolling(cfg['adx_lookback']).median() if cfg['adx_auto'] else None
    return [b for i, b in enumerate(bars) if b['bearFlip'] or
            (b['bullFlip'] and strength.iloc[i] >
             (thresholds.iloc[i] if thresholds is not None else cfg['adx_threshold']))]


def eligible(event_day, latest_day, quote, current=None):
    current=current or datetime.now(s.IST)
    from .market_calendar import is_trading_day, no_session_reason, previous_trading_day, get_session_hours
    if not is_trading_day(current.date()):return False,no_session_reason(current.date())
    get_session_hours(current.date())  # Unpublished special-session times fail closed.
    if event_day!=latest_day:return False,'Flip is not on the latest completed daily candle'
    if previous_trading_day(current.date())!=date.fromisoformat(event_day):return False,'Waiting for the next trading session, or flip is outside its next-session window'
    try:
        if cache.day_of(quote.get('timestamp'))!=current.date() or cache.day_of(quote.get('last_trade_time'))!=current.date():
            return False,'Current-session quote required'
    except (ValueError,TypeError,AttributeError):return False,'Current-session quote required'
    return True,'Confirmed daily flip; existing risk checks required'


def run(progress=None, *, execute=True):
    started=perf_counter(); cfg=s.get_config(); kite=s.get_kite()
    if cfg.get('strategy','occ')!='supertrend':raise ValueError('Strategy changed; run the selected scanner again')
    companies,tokens,watchlist=s.scan_universe(cfg,kite)
    start=datetime.now(s.IST).date()-timedelta(days=max(cfg['lookback_days'],365)+parameters(cfg)[0]*10)
    cache.warm_full_universe(progress)
    selected={symbol:tokens[symbol] for symbol in watchlist if symbol in tokens}
    cache.refresh_history(selected,start,progress)
    refresh_close(selected,progress=progress)
    histories=cache.read_histories(list(selected),start)
    try:quotes=cache.live_quotes(kite,list(selected),progress)
    except Exception:quotes={}
    results=[]; excluded=[]
    for i,symbol in enumerate(selected,1):
        try:
            bars=cached_bars(symbol,histories.get(symbol,[]),cfg)
            if not bars or bars[-1]['supertrend_value'] is None:continue
            flips=filtered_flips(bars,cfg)
            if not flips:continue
            event=flips[-1]; latest=bars[-1]; quote=quotes.get('NSE:'+symbol,{})
            ok,reason=eligible(event['date'],latest['date'],quote)
            high=max(b['high'] for b in bars[-252:]); price=positive(quote.get('last_price'))
            metric=s.volume_metrics(bars)
            rsi=s.daily_rsi(bars)
            if not s.passes_volume_filters(metric,cfg) or not s.passes_rsi_filter(rsi,cfg):continue
            age=cfg.get('max_signal_age_days')
            if age is not None and (datetime.now(s.IST).date()-date.fromisoformat(event['date'])).days>age:continue
            result=dict(strategy='supertrend',occ_mode='supertrend_1d',comparison_only=False,
                symbol=symbol,company=companies.get(symbol,symbol),signal_type='BUY' if event['bullFlip'] else 'EXIT',
                trigger_date=event['date'],trigger_price=event['close'],current_price=price,
                supertrend_value=latest['supertrend_value'],direction=latest['direction'],signal_supertrend_value=event['supertrend_value'],
                bullFlip=event['bullFlip'],bearFlip=event['bearFlip'],indicator_date=latest['date'],
                confirmed_eligible=ok,confirmed_reason=reason,confirmed_block_end=event['date'],strategy_key=strategy_key(cfg),
                execution_session=datetime.now(s.IST).date().isoformat(),calculation_timeframe='1D',
                rsi_14_1d=rsi,rsi_date=latest['date'],volume_date=latest['date'],
                avg_volume_30d=metric['previous_30d_avg_volume'],week_52_high=high,
                week_52_high_distance_pct=(high-price)/high*100 if price is not None else None,pe_ratio=None,**metric)
            for field,offset in [('change_1d_pct',2),('change_1m_pct',22),('change_1y_pct',253)]:
                result[field]=(latest['close']/bars[-offset]['close']-1)*100 if len(bars)>=offset else None
            result.update(s.risk_levels(event['close'], [b['high'] for b in bars if b['date'] >= event['date']], cfg))
            results.append(result)
        except (ValueError,TypeError,KeyError) as error:
            excluded.append({'symbol':symbol,'reason':str(error)})
        if progress and (i%50==0 or i==len(selected)):
            progress({'stage':'Calculating confirmed Supertrend','processed':i,'total':len(selected)})
    if selected and len(excluded)==len(selected):raise RuntimeError('No valid Supertrend history; previous results preserved')
    fundamentals_required=cfg.get('pe_filter_operator') not in (None,'none')
    if fundamentals_required:
        s.enrich_results_fundamentals(results)
    else:
        for result in results:
            result['pe_ratio']=s.previous_fundamentals(result['symbol'])['pe_ratio']
    results=[r for r in results if s.passes_fundamental_filters(r,cfg)]
    results.sort(key=lambda r:r['trigger_date'],reverse=True)
    now=datetime.now(s.IST).isoformat(); duration=round(perf_counter()-started,2)
    conn=s.db()
    try:
        metadata={'config':{**cfg,'supertrend_filter_version':1},'count':len(results),'duration_seconds':duration,'skipped_quotes':excluded}
        run_id=conn.execute('INSERT INTO scanner_runs(ran_at,payload) VALUES (?,?)',(now,json.dumps(metadata))).lastrowid
        conn.executemany('INSERT INTO scanner_signals(symbol,signal_type,trigger_date,payload,run_id) VALUES (?,?,?,?,?)',[(r['symbol'],r['signal_type'],r['trigger_date'],json.dumps(r,allow_nan=False),run_id) for r in results])
        conn.commit()
    finally:conn.close()
    if not fundamentals_required:
        s.fundamentals_executor.submit(s.enrich_run_fundamentals,run_id,results)
    if execute:
        process(results,run_id)
    return dict(run_id=run_id,ran_at=now,strategy='supertrend',results=results,scanned=len(selected),returned=len(results),fundamentals_pending=False,duration_seconds=duration,skipped_quotes=excluded)


def process(signals,run_id):
    from . import autotrade as a
    from .analytics import audit_skip
    from .strategies import position_strategy
    with a.execution_lock:
        cfg=s.get_config(); execution=a.get_config(); current=datetime.now(a.IST); mode=execution['mode']
        for row in signals:
            symbol=row['symbol']; side='BUY' if row['signal_type']=='BUY' else 'SELL'
            try:
                if cfg.get('strategy','occ')!='supertrend' or row.get('strategy')!='supertrend' or row.get('strategy_key')!=strategy_key(cfg):raise ValueError('Strategy changed; run Supertrend again')
                if not execution['enabled'] or execution['paused'] or execution['manual_kill_switch'] or (side=='BUY' and execution['kill_switch']):raise ValueError('Execution disabled, paused or kill switch active')
                if not row.get('confirmed_eligible') or row.get('execution_session')!=current.date().isoformat():raise ValueError(row.get('confirmed_reason','Stale signal'))
                if not a._market_is_open():raise ValueError('Waiting for permitted trading session')
                q=cache.live_quotes(a.get_kite(),[symbol]).get('NSE:'+symbol,{})
                ok,reason=eligible(row['trigger_date'],row['indicator_date'],q,current)
                if not ok:raise ValueError(reason)
                cache.overlay_quote([],q,current)  # Validation only; never used in Supertrend math.
                price=float(q['last_price']); event_key='st:'+row['trigger_date']
                conn=a.connection()
                try:
                    duplicate=conn.execute('SELECT 1 FROM autotrade_confirmed_events WHERE symbol=? AND mode=? AND side=? AND block_end=?',(symbol,mode,side,event_key)).fetchone()
                    position=conn.execute("SELECT * FROM autotrade_positions WHERE symbol=? AND mode=? AND status='OPEN'",(symbol,mode)).fetchone()
                finally:conn.close()
                if duplicate:raise ValueError('Supertrend flip already processed')
                if side=='SELL':
                    if execution['exit_rule'] not in {'scanner_exit','whichever_first'}:raise ValueError('Scanner exits disabled by exit rule')
                    if not position:raise ValueError('No open app position; bearish flips never open shorts')
                    if position_strategy(position)!='supertrend':raise ValueError('Position belongs to OCC; Supertrend cannot close it')
                    if datetime.fromisoformat(position['entry_time']).astimezone(a.IST).date()>date.fromisoformat(row['trigger_date']):raise ValueError('Exit flip predates entry')
                if mode=='paper':
                    a.process_paper_signals([{**row,'trigger_price':price,'_confirmed_key':event_key}],run_id)
                else:
                    qty=int(execution['max_symbol_value']//price) if side=='BUY' else position['quantity']
                    a._record_live_order(symbol,'Supertrend signal' if side=='BUY' else 'Supertrend exit',side,qty,price,run_id=run_id,position_id=position['id'] if side=='SELL' else None,confirmed_key=event_key,confirmed_strategy=row['strategy_key'])
            except (ValueError,TypeError,KeyError) as error:
                audit_skip(symbol,side,mode,str(error),run_id)
