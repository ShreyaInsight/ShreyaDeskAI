import { useEffect, useState } from 'react'
import { Download, RefreshCw, ArrowUpDown, ArrowUpRight } from 'lucide-react'

const money = n => n == null ? '—' : new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 2 }).format(n)
const percent = n => n == null ? '—' : `${n.toFixed(2)}%`
const time = value => value ? new Date(value).toLocaleString('en-IN', { timeZone: 'Asia/Kolkata', dateStyle: 'medium', timeStyle: 'short' }) : '—'
const day = value => value ? new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Kolkata', year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date(value)) : ''
const duration = h => h == null ? '—' : `${Math.floor(h / 24)}d ${Math.floor(h % 24)}h`
const average = values => values.length ? values.reduce((a, b) => a + b, 0) / values.length : null
const columns = {
  orders: [['symbol', 'Symbol'], ['mode', 'Mode'], ['action', 'Side'], ['requested_qty', 'Quantity'], ['filled_quantity', 'Filled qty'], ['order_type', 'Order type'], ['requested_price', 'Limit price'], ['fill_price', 'Average fill'], ['display_status', 'Status'], ['timestamp', 'Submitted · IST'], ['kite_order_id', 'Broker order ID'], ['product', 'Product'], ['gtt_message', 'GTT protection'], ['position_id', 'Trade']],
  trades: [['symbol', 'Symbol'], ['mode', 'Mode'], ['id', 'Trade ID'], ['entry_time', 'Entry · IST'], ['entry_price', 'Entry price'], ['quantity', 'Quantity'], ['exit_time', 'Exit · IST'], ['exit_price', 'Exit price'], ['duration_hours', 'Held'], ['pnl', 'Gross P&L'], ['pnl_pct', 'P&L %'], ['exit_reason', 'Exit reason'], ['gtt_timeline', 'GTT timeline'], ['status', 'Status']],
  logs: [['symbol', 'Symbol'], ['mode', 'Mode'], ['action', 'Attempt'], ['outcome_type', 'Outcome'], ['outcome', 'Details / reason'], ['timestamp', 'Attempted · IST'], ['check', 'Check / source'], ['kite_order_id', 'Broker order ID'], ['scan_run_id', 'Scan run'], ['position_id', 'Trade']],
}
function display(key, value) {
  if (key === 'gtt_timeline') return value?.length ? value.map(e => `${time(e.timestamp)} · ${e.event}: ${e.message}`).join(' | ') : '—'
  if (['entry_price', 'exit_price', 'requested_price', 'fill_price', 'pnl'].includes(key)) return money(value)
  if (key === 'pnl_pct') return percent(value)
  if (['timestamp', 'entry_time', 'exit_time'].includes(key)) return time(value)
  if (key === 'duration_hours') return duration(value)
  return value ?? '—'
}
function exportRows(section, rows) {
  const fields = columns[section]
  const cell = value => {
    let text = String(value ?? '')
    // Text fields must not become spreadsheet formulas when a CSV is opened.
    if (/^[\s]*[=+@-]/.test(text) && typeof value !== 'number') text = "'" + text
    return '"' + text.replaceAll('"', '""') + '"'
  }
  const csv = [fields.map(([, label]) => cell(label)).join(','), ...rows.map(r => fields.map(([key]) => cell(key === 'gtt_timeline' ? (r[key] || []).map(e => `${e.timestamp} ${e.event}: ${e.message}`).join(' | ') : r[key])).join(','))].join('\r\n')
  const url = URL.createObjectURL(new Blob(['\uFEFF', csv], { type: 'text/csv;charset=utf-8' }))
  const a = document.createElement('a'); a.href = url; a.download = `shreyadesk-${rows[0]?.mode || 'analytics'}-${section}.csv`; a.click(); setTimeout(() => URL.revokeObjectURL(url), 1000)
}
export default function AnalyticsPanel({ api }) {
  const [mode, setMode] = useState('live'), [section, setSection] = useState('orders')
  const [data, setData] = useState(null), [error, setError] = useState(''), [loading, setLoading] = useState(true), [revision, setRevision] = useState(0)
  const [symbol, setSymbol] = useState(''), [from, setFrom] = useState(''), [to, setTo] = useState(''), [status, setStatus] = useState(''), [side, setSide] = useState(''), [result, setResult] = useState(''), [linked, setLinked] = useState(null)
  const [reason, setReason] = useState(''), [page, setPage] = useState(0)
  const [includeOpen, setIncludeOpen] = useState(false), [sort, setSort] = useState({ key: 'timestamp', asc: false })
  useEffect(() => {
    const controller = new AbortController(); setLoading(true); setError(''); setData(null)
    api(`/api/analytics?mode=${mode}`, { signal: controller.signal }).then(r => { if (!controller.signal.aborted) setData(r) }).catch(e => { if (!controller.signal.aborted) setError(e.message) }).finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [mode, revision, api])
  const changeSection = value => { setSection(value); setStatus(''); setSide(''); setResult(''); setLinked(null); setReason(''); setSort({ key: 'timestamp', asc: false }) }
  useEffect(() => { setPage(0) }, [mode, section, symbol, from, to, status, side, result, linked, reason, sort, revision])
  const source = data?.mode === mode ? data[section] : []
  const statuses = [...new Set(source.map(r => section === 'logs' ? r.outcome_type : section === 'orders' ? r.display_status : r.status))].sort()
  const invalidRange = from && to && from > to
  const rows = source.filter(r => !invalidRange && (!reason || (r.outcome || '').toLowerCase().includes(reason.toLowerCase())) && r.symbol.toUpperCase().includes(symbol.toUpperCase()) && (!from || day(r.timestamp) >= from) && (!to || day(r.timestamp) <= to) && (!status || (section === 'logs' ? r.outcome_type : section === 'orders' ? r.display_status : r.status) === status) && (!side || r.action === side) && (!result || (r.pnl != null && (result === 'win' ? r.pnl > 0 : result === 'loss' ? r.pnl < 0 : r.pnl === 0))) && (linked == null || r.id === linked)).sort((a, b) => {
    const x = a[sort.key], y = b[sort.key]
    if (x == null) return y == null ? 0 : 1
    if (y == null) return -1
    return (typeof x === 'number' && typeof y === 'number' ? x - y : String(x).localeCompare(String(y))) * (sort.asc ? 1 : -1)
  })
  const closed = rows.filter(r => r.status === 'CLOSED'), open = rows.filter(r => r.status === 'OPEN')
  const valued = closed.filter(r => r.pnl != null), wins = valued.filter(r => r.pnl > 0), losses = valued.filter(r => r.pnl < 0)
  const missing = closed.length !== valued.length || (includeOpen && open.some(r => r.pnl == null))
  const total = missing ? null : [...valued, ...(includeOpen ? open : [])].reduce((s, r) => s + (r.pnl ?? 0), 0)
  let peak = 0, running = 0, drawdown = 0
  for (const r of [...valued].sort((a, b) => String(a.exit_time).localeCompare(String(b.exit_time)))) { running += r.pnl; peak = Math.max(peak, running); drawdown = Math.max(drawdown, peak - running) }
  const extreme = (key, high) => valued.length ? [...valued].filter(r => r[key] != null).sort((a, b) => (a[key] - b[key]) * (high ? -1 : 1))[0] : null
  const best = extreme('pnl', true), worst = extreme('pnl', false), bestPct = extreme('pnl_pct', true), worstPct = extreme('pnl_pct', false)
  const metrics = [
    [includeOpen ? 'Total gross P&L' : 'Realized gross P&L', money(total)], ['Win rate · closed', closed.length && valued.length === closed.length ? percent(wins.length / closed.length * 100) : '—'],
    ['Trades · open / closed', `${open.length} / ${closed.length}`], ['Average closed trade', valued.length === closed.length ? money(average(valued.map(r => r.pnl))) : '—'], ['Average win / loss', `${money(average(wins.map(r => r.pnl)))} / ${money(average(losses.map(r => r.pnl)))}`],
    ['Average hold · closed', duration(average(closed.filter(r => r.duration_hours != null).map(r => r.duration_hours)))], ['Max realized drawdown', valued.length === closed.length ? money(drawdown) : '—'],
    ['Best / worst · ₹', `${money(best?.pnl)} / ${money(worst?.pnl)}`], ['Best / worst · %', `${percent(bestPct?.pnl_pct)} / ${percent(worstPct?.pnl_pct)}`],
  ]
  const openTrade = id => { changeSection('trades'); setSymbol(''); setFrom(''); setTo(''); setLinked(id) }
  return <div className="dashboard-content trading-page analytics-page">
    <div className="welcome-row"><div><p className="section-kicker">STRATEGY JOURNAL</p><h2>Analytics</h2><p className="muted">Follow every order, review each trade, and understand the decisions behind them.</p></div></div>
    <div className="holdings-toolbar"><div className="subtabs" aria-label="Trading mode">{['live', 'paper'].map(m => <button key={m} aria-pressed={mode === m} className={mode === m ? 'active' : ''} onClick={() => { setMode(m); setLinked(null) }}>{m === 'live' ? 'Live trading' : 'Paper trading'}</button>)}</div><button className="secondary-button" disabled={loading} onClick={() => setRevision(n => n + 1)}><RefreshCw size={16} /> Refresh</button></div>
    <div className="subtabs analytics-tabs" aria-label="Analytics sections">{[['orders', 'Order History'], ['trades', 'Trade History'], ['logs', 'Order Logs']].map(([key, label]) => <button key={key} aria-pressed={section === key} className={section === key ? 'active' : ''} onClick={() => changeSection(key)}>{label}</button>)}</div>
    <p className="muted">{section === 'orders' ? mode === 'live' ? 'App orders with a recorded broker order ID. Acceptance is not a guarantee of execution; uncertain submissions remain in Order Logs.' : 'Simulated orders only. These were never sent to an exchange.' : section === 'trades' ? 'App-managed positions only. Duration is elapsed calendar days and hours. Date filters use entry date.' : 'Submissions, blocked attempts and scanner skips. Skip logging starts with this release; older unrecorded attempts cannot be recovered.'}</p>
    <div className="analytics-filters">
      <label>Symbol<input value={symbol} onChange={e => setSymbol(e.target.value)} placeholder="Search symbol" /></label>
      <label>From · IST<input type="date" value={from} onChange={e => setFrom(e.target.value)} /></label><label>To · IST<input type="date" value={to} onChange={e => setTo(e.target.value)} /></label>
      <label>{section === 'logs' ? 'Outcome' : 'Status'}<select value={status} onChange={e => setStatus(e.target.value)}><option value="">All</option>{statuses.map(s => <option key={s}>{s}</option>)}</select></label>
      {section === 'trades' ? <label>Performance<select value={result} onChange={e => setResult(e.target.value)}><option value="">All trades</option><option value="win">Winning</option><option value="loss">Losing</option><option value="flat">Break-even</option></select></label> : <label>Side<select value={side} onChange={e => setSide(e.target.value)}><option value="">BUY & SELL</option><option>BUY</option><option>SELL</option></select></label>}
      {section === 'logs' && <label>Reason<input value={reason} onChange={e => setReason(e.target.value)} placeholder="Search block / failure reason" /></label>}
      <button className="secondary-button" disabled={!rows.length} onClick={() => exportRows(section, rows)}><Download size={16} /> Export CSV</button>
    </div>
    {linked != null && <p className="muted">Showing linked trade #{linked}. <button className="analytics-link" onClick={() => setLinked(null)}>Show all trades</button></p>}
    {invalidRange && <p role="alert" className="error-message">From date must be on or before To date.</p>}
    {error && <p role="alert" className="error-message">{error}</p>}{data?.warnings.map(w => <p key={w} role="status" className="error-message">{w}</p>)}
    {section === 'trades' && <><label className="analytics-checkbox"><input type="checkbox" checked={includeOpen} onChange={e => setIncludeOpen(e.target.checked)} /> Include open unrealized P&L in total</label><div className="analytics-metrics">{metrics.map(([label, value]) => <div className="analytics-metric" key={label}><span>{label}</span><strong>{loading || error ? '—' : value}</strong></div>)}</div><p className="muted">Metrics apply to filtered {mode} trades. P&L is gross, before brokerage, taxes and DP charges; actual charges are not stored, so net P&L is unavailable. Drawdown uses closed-trade P&L, not intraday equity. Missing price data is shown as —.</p></>}
    <div className="results-meta"><span>{rows.length} {section === 'trades' ? 'positions' : 'records'} · {mode.toUpperCase()}</span><span>{data ? `Updated ${time(data.updated_at)} IST` : loading ? 'Loading journal…' : 'Data unavailable'}</span></div>
    <div className="table-wrap"><table className="signal-table analytics-table"><thead><tr>{columns[section].map(([key, label]) => <th key={key} aria-sort={sort.key === key ? sort.asc ? 'ascending' : 'descending' : 'none'}><button onClick={() => setSort(s => ({ key, asc: s.key === key ? !s.asc : true }))}>{label}<ArrowUpDown size={12} /></button></th>)}</tr></thead><tbody>{rows.slice(page * 50, (page + 1) * 50).map(r => <tr key={r.id}>{columns[section].map(([key]) => <td key={key} className={['pnl', 'pnl_pct'].includes(key) ? r[key] > 0 ? 'gain' : r[key] < 0 ? 'loss' : '' : key === 'outcome' || key === 'check' ? 'analytics-reason' : ''}>{key === 'position_id' && r[key] ? <button className="analytics-link" onClick={() => openTrade(r[key])}>#{r[key]} <ArrowUpRight size={12} /></button> : key === 'symbol' ? <strong>{r[key]}</strong> : key === 'mode' || key === 'action' || key === 'outcome_type' || key === 'status' || key === 'display_status' ? <span className={`signal-badge ${r[key] === 'BUY' || r[key] === 'OK' ? 'buy' : r[key] === 'SELL' || r[key] === 'REJECTED' ? 'exit' : ''}`}>{display(key, r[key])}</span> : key === 'gtt_timeline' && r[key]?.length ? <details><summary>GTT events ({r[key].length})</summary><ol>{r[key].map(e => <li key={e.id}><time>{time(e.timestamp)}</time><br /><strong>{e.event}</strong><p>{e.message}</p></li>)}</ol></details> : display(key, r[key])}</td>)}</tr>)}{!rows.length && <tr><td colSpan={columns[section].length}>{loading ? 'Loading analytics…' : error ? 'Analytics could not be loaded. Try Refresh.' : 'No records match this view.'}</td></tr>}</tbody></table></div>
    {rows.length > 50 && <div className="holdings-toolbar analytics-pagination"><button className="secondary-button" disabled={page === 0} onClick={() => setPage(p => p - 1)}>Previous</button><span className="muted">Page {page + 1} of {Math.ceil(rows.length / 50)} · CSV includes all filtered rows</span><button className="secondary-button" disabled={(page + 1) * 50 >= rows.length} onClick={() => setPage(p => p + 1)}>Next</button></div>}
    <p className="muted analytics-footnote">Exit reasons reflect recorded events. Older “risk exit” records do not distinguish SL from TP. GTT trigger events are separate from confirmed SELL fills. CSV exports include the filtered rows with ISO timestamps and IST offsets.</p>
  </div>
}
