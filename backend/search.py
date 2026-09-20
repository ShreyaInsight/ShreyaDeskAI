"""Independent research consumer; never calls scanner runs or trading actions."""
import json
import math
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime
from . import scanner as s, market_cache as cache
from .supertrend import finalized, calculate

FIELDS = ['Market Cap','Current Price','High/Low (52-week)','Stock P/E','Book Value','Dividend Yield','ROCE','ROE','Face Value','EPS','Price to Book Value','Interest Coverage','Sales Growth','EVEBITDA','ROE 5Yr Variance','ROE Prev Annual','Change in Promoter Holding (3Yr)','Promoter Holding %','ROE 3Yr','Sales Growth 3Years','RSI','MACD','PEG Ratio','Altman Z Score','EPS Growth 3Years','Piotroski Score','Avg Volume (1 week)']
# Explicit research heuristics, not execution criteria. [weight, weak endpoint, strong endpoint]
DEFAULT_WEIGHTS = {
 'long': {'ROE':[20,0,20], 'ROCE':[20,0,20], 'EPS Growth 3Years':[20,0,20], 'Sales Growth 3Years':[15,0,20], 'PEG Ratio':[15,3,1], 'Piotroski Score':[10,0,9]},
 'short': {'RSI':[20,30,70], 'MACD histogram %':[20,-1,1], 'OCC state':[20,-1,1], 'Supertrend state':[25,-1,1], 'Relative volume':[15,0.5,2]}}


@contextmanager
def connection():
    c=sqlite3.connect(s.DB_PATH,timeout=30)
    c.execute('CREATE TABLE IF NOT EXISTS search_cache(key TEXT PRIMARY KEY, day TEXT NOT NULL, payload TEXT NOT NULL)')
    try:
        yield c
        c.commit()
    finally:
        c.close()


def stored(key):
    with connection() as c:
        row=c.execute('SELECT day,payload FROM search_cache WHERE key=?',(key,)).fetchone()
    return (row[0],json.loads(row[1])) if row else None


def put(key,payload):
    with connection() as c:c.execute('INSERT OR REPLACE INTO search_cache VALUES (?,?,?)',(key,str(cache.today()),json.dumps(payload,allow_nan=False)))


def weights(payload=None):
    if payload is not None:
        if set(payload)!=set(DEFAULT_WEIGHTS):raise ValueError('Both score groups are required')
        for group,components in DEFAULT_WEIGHTS.items():
            if not isinstance(payload[group],dict) or set(payload[group])!=set(components):raise ValueError('Invalid score components')
            for key,values in payload[group].items():
                if not isinstance(values,list) or len(values)!=3 or any(type(v) not in (float,int) or not math.isfinite(v) for v in values):raise ValueError('Weights and thresholds must be finite numbers')
                if values[0]<0 or values[0]>100 or values[1]==values[2]:raise ValueError('Invalid weight or equal thresholds')
            if sum(v[0] for v in payload[group].values())<=0:raise ValueError('At least one positive weight is required')
        put('weights',payload)
    row=stored('weights')
    return row[1] if row else DEFAULT_WEIGHTS


def symbols(query):
    # Read cached official universe only: opening Search never refreshes the scanner.
    with cache.db() as c:
        rows={r['key']:json.loads(r['payload']) for r in c.execute("SELECT key,payload FROM scanner_market_master WHERE key IN ('official_equities','kite_nse')")}
    universe=cache.match_equities(rows.get('kite_nse',[]),rows.get('official_equities',[]))
    q=query.strip().upper()
    return [{'symbol':k,'name':v.get('name',k)} for k,v in sorted(universe.items(), key=lambda item:(item[0]!=q,item[0])) if not q or q in k or q in v.get('name','').upper()][:30]


def field(value=None,status=None,source='provider'):
    if isinstance(value,(int,float)) and not math.isfinite(value):value=None
    return dict(value=value,status=status or ('SUCCESS' if value is not None else 'UNAVAILABLE'),source=source)


def fundamentals(symbol):
    key='fundamentals:'+symbol
    previous=stored(key)
    if previous and previous[0]==str(cache.today()) and previous[1]['status']!='FAILED':return previous[1]
    data=s.screener_fundamentals(symbol,detailed=True)
    ratios=data.get('_ratios',{})
    status=data.get('_status','FAILED')
    aliases={'Price to Book Value':'Price to book value','Promoter Holding %':'Promoter holding','Sales Growth 3Years':'Sales growth 3Years','EPS Growth 3Years':'EPS growth 3Years','PEG Ratio':'PEG Ratio'}
    normalized={k.lower().strip():v for k,v in ratios.items()}
    result={name:field(normalized.get(aliases.get(name,name).lower()),status='FAILED' if status=='FAILED' else None,source='reference' if name=='Face Value' else 'provider') for name in FIELDS}
    # Fields absent from the provider are unavailable, never substituted with zero.
    value={'status':status,'as_of':str(cache.today()),'fields':result,'source':'Screener.in'}
    put(key,value);return value


def score(values,settings):
    result={}
    for group,components in settings.items():
        parts=[];total=sum(v[0] for v in components.values());available=0;weighted=0
        for name,(weight,low,high) in components.items():
            value=values.get(name)
            valid=isinstance(value,(int,float)) and math.isfinite(value)
            points=max(0,min(100,(value-low)/(high-low)*100)) if valid else None
            if valid:available+=weight;weighted+=weight*points
            parts.append(dict(name=name,value=value,weight=weight,weak=low,strong=high,points=points))
        result[group]=dict(value=round(weighted/available,1) if available else None,coverage=round(100*available/total,1),components=parts)
    return result


def analyse(symbol):
    if not any(r['symbol']==symbol for r in symbols(symbol)):raise ValueError('Symbol not found in cached NSE equity universe')
    # Config is copied; neither selected strategy nor scanner state is written.
    with sqlite3.connect(f'file:{s.DB_PATH}?mode=ro',uri=True) as c:
        row=c.execute('SELECT payload FROM scanner_config WHERE id=1').fetchone()
    cfg={**s.DEFAULT_CONFIG,**(json.loads(row[0]) if row else {})}
    bars=finalized(cache.read_histories([symbol],date(1900,1,1))[symbol])
    warnings=[];occ=[];st=[]
    if bars:
        st=calculate(bars,10,3)
        try:
            occ_cfg={**cfg,'strategy':'occ','alternate_mode':'confirmed'}
            frame=s.prepare_frame(bars,occ_cfg)
            if cfg.get('use_alternate_resolution'):
                from .alternate_resolution import session_calendar, ANCHOR
                sessions=session_calendar(date.fromisoformat(bars[0]['date']),cache.today())
                offsets={day:i-sessions.index(ANCHOR) for i,day in enumerate(sessions)}
                frame=frame[frame['date'].dt.date.map(lambda day:offsets.get(day,-1)%3==2)]
            occ=[dict(date=r['date'].date().isoformat(),open_series=float(r['open_ma']),close_series=float(r['close_ma']),buy=bool(r['buy']),exit=bool(r['exit'])) for _,r in frame.iterrows() if math.isfinite(r['open_ma']) and math.isfinite(r['close_ma'])]
        except (ValueError,KeyError) as e:warnings.append('OCC overlay unavailable: '+str(e))
    else:warnings.append('No cached candle history. Search does not launch a scanner refresh.')
    funds=fundamentals(symbol);fields=dict(funds['fields'])
    for name in ('RSI','MACD','Avg Volume (1 week)'):
        fields[name]=field(source='cached daily candles')
    values={k:v['value'] for k,v in fields.items()}
    if bars:
        closes=s.pd.Series([b['close'] for b in bars],dtype=float)
        macd=closes.ewm(span=12,adjust=False,min_periods=26).mean()-closes.ewm(span=26,adjust=False,min_periods=26).mean()
        signal=macd.ewm(span=9,adjust=False,min_periods=9).mean()
        last=lambda series:float(series.iloc[-1]) if math.isfinite(series.iloc[-1]) else None
        values.update({'RSI':s.daily_rsi(bars),'MACD':last(macd),'Avg Volume (1 week)':sum(b['volume'] for b in bars[-5:])/5 if len(bars)>=5 else None})
        for name in ('RSI','MACD','Avg Volume (1 week)'):fields[name]=field(values[name],source='cached daily candles')
        fields['Current Price']=field(bars[-1]['close'],source='latest completed daily close')
        fields['High/Low (52-week)']=field(f"{max(b['high'] for b in bars[-252:]):.2f} / {min(b['low'] for b in bars[-252:]):.2f}" if len(bars)>=252 else None,source='252 completed sessions')
        values['MACD histogram %']=(last(macd)-last(signal))/bars[-1]['close']*100 if last(macd) is not None and last(signal) is not None else None
        values['Supertrend state']=-st[-1]['direction'] if st[-1]['supertrend_value'] is not None else None
        values['OCC state']= (1 if occ[-1]['close_series']>occ[-1]['open_series'] else -1 if occ[-1]['close_series']<occ[-1]['open_series'] else 0) if occ else None
        metrics=s.volume_metrics(bars);avg=metrics['previous_30d_avg_volume']
        values['Relative volume']=metrics['current_volume']/avg if avg else None
    settings=weights()
    return dict(symbol=symbol,as_of=bars[-1]['date'] if bars else None,fields=fields,fundamentals_as_of=funds['as_of'],bars=st,occ=occ,occ_mode='Confirmed 3D' if cfg.get('use_alternate_resolution') else '1D',scores=score(values,settings),weights=settings,warnings=warnings)
