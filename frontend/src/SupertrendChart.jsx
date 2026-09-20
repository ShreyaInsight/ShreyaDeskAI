import {useEffect,useState} from 'react'

export default function SupertrendChart({api,symbol,onSymbol}) {
  const [data,setData]=useState(null),[error,setError]=useState(''),[draft,setDraft]=useState('')
  useEffect(()=>{
    let cancelled=false
    setData(null);setError('');setDraft(symbol)
    if(symbol)api(`/api/scanner/chart/${encodeURIComponent(symbol)}?strategy=supertrend`).then(value=>{if(!cancelled)setData(value)}).catch(e=>{if(!cancelled)setError(e.message)})
    return()=>{cancelled=true}
  },[symbol])
  const bars=(data?.bars || []).slice(-120)
  const values=bars.flatMap(b=>[b.low,b.high,b.supertrend_value]).filter(v=>v!=null)
  const minimum=values.length?Math.min(...values):0,maximum=values.length?Math.max(...values):1
  const pad=Math.max((maximum-minimum)*.12,1),bottom=minimum-pad,top=maximum+pad
  const width=Math.max(400,bars.length*8+110)
  const x=i=>60+i*(width-110)/Math.max(1,bars.length-1),y=v=>20+(top-v)/(top-bottom)*340
  const color=b=>b.direction<0?'#85b95e':'#ef6a5b'
  return <section className="overview-section"><h3>Supertrend price chart · 1D</h3><form className="signal-actions" onSubmit={event=>{event.preventDefault();onSymbol(draft.trim().toUpperCase())}}><label className="number-field"><span>Chart symbol</span><input value={draft} onChange={e=>setDraft(e.target.value)} placeholder="e.g. APLAPOLLO" maxLength={40} /></label><button className="secondary-button" disabled={!draft.trim()}>View chart</button></form>{error && <p role="alert">{error}</p>}{symbol && !data && !error && <p role="status">Loading completed candles...</p>}{data && <><p className="muted">{symbol} · ATR {data.atr_length} · Factor {data.factor} · Through {data.as_of || 'no cached history'} · Last 120 completed candles</p><p className="muted">Green ▲ bullish flip · Red ▼ bearish flip. Hover a candle for OHLC and Supertrend values.</p>{!bars.length?<p>No cached candles. Run the selected scanner first.</p>:<div className="table-wrap"><svg role="img" aria-label={`${symbol} daily candles with Supertrend and flip markers`} viewBox={`0 0 ${width} 400`} style={{width,minWidth:'100%',height:400,background:bars.at(-1).direction<0?'rgba(133,185,94,.05)':'rgba(239,106,91,.05)'}}>
    {[0,1,2,3,4].map(i=>{const value=bottom+(top-bottom)*i/4;return <g key={i}><line x1="50" x2={width-15} y1={y(value)} y2={y(value)} stroke="currentColor" opacity=".12"/><text x="2" y={y(value)+4} fill="currentColor" fontSize="10">{value.toFixed(1)}</text></g>})}
    {bars.map((b,i)=>{const candleColor=b.close>=b.open?'#85b95e':'#ef6a5b';const previous=bars[i-1];return <g key={b.date}><title>{`${b.date} · O ${b.open} H ${b.high} L ${b.low} C ${b.close} · ST ${b.supertrend_value ?? 'warming up'}${b.bullFlip?' · Bullish flip':b.bearFlip?' · Bearish flip':''}`}</title><line x1={x(i)} x2={x(i)} y1={y(b.high)} y2={y(b.low)} stroke={candleColor}/><rect x={x(i)-2.5} y={y(Math.max(b.open,b.close))} width="5" height={Math.max(1,Math.abs(y(b.open)-y(b.close)))} fill={candleColor}/>{b.supertrend_value!=null && <circle cx={x(i)} cy={y(b.supertrend_value)} r="1" fill={color(b)}/>} {previous?.supertrend_value!=null && b.supertrend_value!=null && <path data-testid="supertrend-line" d={previous.direction!==b.direction?`M ${x(i-1)} ${y(previous.supertrend_value)} H ${x(i)} V ${y(b.supertrend_value)}`:`M ${x(i-1)} ${y(previous.supertrend_value)} L ${x(i)} ${y(b.supertrend_value)}`} stroke={color(b)} strokeWidth="2" fill="none"/>}{b.bullFlip && <polygon data-testid="bull-flip" points={`${x(i)},${y(b.low)+7} ${x(i)-4},${y(b.low)+15} ${x(i)+4},${y(b.low)+15}`} fill="#85b95e"/>}{b.bearFlip && <polygon data-testid="bear-flip" points={`${x(i)},${y(b.high)-7} ${x(i)-4},${y(b.high)-15} ${x(i)+4},${y(b.high)-15}`} fill="#ef6a5b"/>}{(i%20===0 || i===bars.length-1) && <text x={x(i)} y="388" fontSize="10" fill="currentColor" textAnchor="middle">{b.date}</text>}</g>})}
  </svg></div>}</>}</section>
}
