import {BrokerAccountNotice, BrokerAccountSwitch} from './BrokerAccountPanel.jsx'
import {sortHoldings, holdingsCsv} from './holdingsExport.js'
import TelegramSettings from './TelegramSettings.jsx'
import SearchPanel from './SearchPanel.jsx'
import SupertrendChart from './SupertrendChart.jsx'
import { StrictMode, useEffect, useRef, useState } from 'react'
import { createRoot } from 'react-dom/client'
import {
  ArrowUpRight,
  ArrowDownRight,
  ArrowUp,
  ArrowUpDown,
  Activity,
  BarChart3,
  Check,
  ChevronRight,
  ClipboardList,
  CircleHelp,
  Download,
  FlaskConical,
  KeyRound,
  LayoutDashboard,
  LogOut,
  Menu,
  RefreshCw,
  Pause,
  Play,
  SlidersHorizontal,
  ShieldCheck,
  Settings,
  TrendingDown,
  TrendingUp,
  Wallet,
  X,
} from 'lucide-react'
import './styles.css'
import AnalyticsPanel from './AnalyticsPanel'
import SessionActivity from './SessionActivity'

const tabs = [
  { id: 'overview', label: 'Overview', icon: LayoutDashboard },
  { id: 'signals', label: 'Signals', icon: Activity },
  { id: 'search', label: 'Search', icon: BarChart3 },
  { id: 'settings', label: 'Settings', icon: Settings },
  { id: 'holdings', label: 'Holdings', icon: Wallet },
  { id: 'analytics', label: 'Analytics', icon: AnalysisIcon },
  { id: 'demo', label: 'Demo Trading', icon: FlaskConical },
  { id: 'live', label: 'Live Trading', icon: LiveTradingIcon },
]
const navigationTabs = tabs.filter(({ id }) => id !== 'user')

function KiteIcon() {
  return <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 90 60" width="17" height="17" aria-label="Kite"><path fill="#f6461a" d="M30 0 0 30l30 30 30-30L90 0z" /><path fill="#db342c" d="m30 60 30-30 30 30z" /></svg>
}

function LiveTradingIcon() {
  return <svg viewBox="0 0 24 24" width="17" height="17" fill="none" aria-hidden="true"><path d="M7 14 9.293 11.707a1 1 0 0 1 1.414 0l1.586 1.586a1 1 0 0 0 1.414 0L17 10m0 0v2.5m0-2.5h-2.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" /><path d="M22 12c0 4.714 0 7.071-1.464 8.536C19.071 22 16.714 22 12 22s-7.071 0-8.536-1.464C2 19.071 2 16.714 2 12s0-7.071 1.464-8.536C4.929 2 7.286 2 12 2s7.071 0 8.536 1.464C21.51 4.438 21.836 5.807 21.945 8" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" /></svg>
}

function AnalysisIcon() {
  return <svg viewBox="0 0 24 24" width="17" height="17" fill="none" aria-hidden="true"><path d="m5.83 12.12-2.83 2.88M8 7a3 3 0 1 0 3 3 3 3 0 0 0-3-3Zm3 10h6m-2-4h2" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" /><path d="M8 3h12a1 1 0 0 1 1 1v16a1 1 0 0 1-1 1H8a1 1 0 0 1-1-1v-3" stroke="currentColor" strokeLinecap="round" strokeLinejoin="round" strokeWidth="2" /></svg>
}

const CircleDollarSign = KiteIcon

const indexOptions = [
  ['NIFTY50', 'Nifty 50 Index'], ['NIFTYNEXT50', 'Nifty Next 50 Index'], ['NIFTY100', 'Nifty 100 Index'],
  ['NIFTYNEXT100', 'Nifty Next 100'], ['NIFTY200', 'Nifty 200 Index'], ['NIFTYTOTALMARKET', 'Nifty Total Market Index'],
  ['NIFTY500', 'Nifty 500 Index'], ['NIFTY500MULTICAP502525', 'Nifty 500 Multicap 50:25:25 Index'],
  ['NIFTY500LARGEMIDSMALLEQUAL', 'Nifty500 LargeMidSmall Equal-Cap Weighted'], ['NIFTYMIDCAP150', 'Nifty Midcap150 Index'],
  ['NIFTYMIDCAP50', 'Nifty Midcap 50 Index'], ['NIFTYMIDCAPSELECT', 'Nifty Midcap Select Index'],
  ['NIFTYMIDCAP100', 'Nifty Midcap 100 Index'], ['NIFTYSMALLCAP500', 'Nifty Smallcap 500'],
  ['NIFTYSMALLCAP250', 'Nifty Smallcap 250 Index'], ['NIFTYSMALLCAP50', 'Nifty Smallcap 50 Index'],
  ['NIFTYSMALLCAP100', 'Nifty Smallcap 100 Index'], ['NIFTYMICROCAP250', 'Nifty Microcap 250 Index'],
  ['NIFTYLARGEMIDCAP250', 'Nifty LargeMidcap 250 Index'], ['NIFTYMIDSMALLCAP400', 'Nifty MidSmallcap 400 Index'],
  ['NIFTYMIDSMALLCAP4005050', 'Nifty MidSmallcap400 50:50'], ['NIFTYINDIAFPI150', 'Nifty India FPI 150'],
  ['INDIAVIX', 'India Vix Index'],
]

async function api(path, options = {}) {
  let response
  try {
    const csrf = document.cookie.split('; ').find((item) => item.startsWith('shreyadesk_csrf='))?.split('=')[1]
    response = await fetch(path, {
      credentials: 'same-origin',
      cache: 'no-store',
      headers: { 'Content-Type': 'application/json', ...(csrf && options.method && options.method !== 'GET' ? { 'X-CSRF-Token': decodeURIComponent(csrf) } : {}) },
      ...options,
    })
  } catch (error) {
    throw new Error(`Could not reach ${path}. Check the VM connection.`)
  }
  const body = await response.json().catch(() => ({}))
  if (!response.ok) {
    if (!['/api/session','/api/login'].includes(path) && (response.status === 401 || body.detail === 'Complete Kite login for this dashboard session first.')) window.dispatchEvent(new Event('session-invalidated'))
    throw new Error(body.detail || `${path} returned HTTP ${response.status}.`)
  }
  return body
}

function App() {
  const [session,setSession]=useState(null)
  const [checkingSession,setCheckingSession]=useState(true)
  const [tab,setTab]=useState('overview')
  const sequence=useRef(0), authenticated=useRef(false)
  async function checkSession({preserveView=false} = {}) {
    const version=++sequence.current
    if (!preserveView) setCheckingSession(true)
    try {
      const data=await api('/api/session')
      if(version!==sequence.current)return
      authenticated.current=true;setSession(data)
    } catch {
      if(version!==sequence.current)return
      authenticated.current=false;setSession(null)
    } finally {
      if(version===sequence.current) {setCheckingSession(false);delete document.documentElement.dataset.sessionHidden}
    }
  }
  useEffect(()=>{
    checkSession()
    const hide=()=>{document.documentElement.dataset.sessionHidden='true';sequence.current+=1}
    const show=event=>{if(event.persisted)checkSession({preserveView:true})}
    const visibility=()=>{if(document.visibilityState==='visible' && authenticated.current)checkSession({preserveView:true});else if(document.visibilityState==='hidden' && authenticated.current)hide()}
    window.addEventListener('pagehide',hide)
    window.addEventListener('pageshow',show)
    document.addEventListener('visibilitychange',visibility)
    window.addEventListener('session-invalidated',checkSession)
    return ()=>{sequence.current+=1;window.removeEventListener('pagehide',hide);window.removeEventListener('pageshow',show);document.removeEventListener('visibilitychange',visibility);window.removeEventListener('session-invalidated',checkSession)}
  },[])
  async function signOut() {
    try {await api('/api/logout',{method:'POST'})} finally {await checkSession()}
  }
  function navigate(next) {setTab(next);checkSession()}
  if(checkingSession)return <div className="loading-screen" role="status"><RefreshCw size={18} className="spin" /> Checking your secure session</div>
  if(!session)return <Login onAuthenticated={checkSession} />
  if(session.account_change)return <BrokerAccountSwitch api={api} change={session.account_change} onResolved={checkSession}/>
  if(!session.connected)return <KiteConnectGate onDisconnect={signOut} onRetry={checkSession} session={session} />
  return <Dashboard tab={tab} setTab={navigate} onLogout={checkSession} />
}

function Login({ onAuthenticated }) {
  const [credentialsRevealed, setCredentialsRevealed] = useState(false)
  const credentialsForm = useRef(null)
  useEffect(() => {
    if (!credentialsRevealed || !credentialsForm.current) return
    const form = credentialsForm.current
    const target = Array.from(form.querySelectorAll('input')).find(input => !input.value) || form.querySelector('button[type="submit"]')
    target?.focus({ preventScroll: true })
  }, [credentialsRevealed])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')


  async function submit(event) {
    event.preventDefault()
    const credentials = new FormData(event.currentTarget)
    setBusy(true)
    setError('')
    try {
      await api('/api/login', { method: 'POST', body: JSON.stringify({ username: credentials.get('username'), password: credentials.get('password') }) })
      onAuthenticated()
    } catch (loginError) {
      setError(loginError.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <main className={`auth-layout login-layout ${credentialsRevealed ? 'credentials-visible' : 'intro-visible'}`}>
      <section className="auth-intro">
        <div className="auth-brand"><img className="brand-logo" src="/weblogo.svg" alt="" /><span>Shreya<span className="accent">Desk</span></span></div>
        <p className="eyebrow">KITE CONNECT / PRIVATE CONSOLE</p>
        <h1>Your market desk,<br /><em>quietly in control.</em></h1>
        <p className="intro-copy">A focused workspace for your Zerodha account. Connect once, then keep your session ready while you build.</p>
        <div className="intro-footer"><span className="status-dot" /> Local session storage enabled <ChevronRight size={14} /></div>
        {!credentialsRevealed && <button className="primary-button mobile-credentials-reveal" type="button" aria-expanded={false} aria-controls="dashboard-credentials" onClick={() => setCredentialsRevealed(true)}><KeyRound size={17} />Enter dashboard credentials<ArrowUpRight size={16} /></button>}
      </section>

      <section className="auth-panel">
        <div className="panel-topline"><span>01</span><span>Secure connection</span></div>
        <div className="form-heading">
          <div className="icon-box"><KeyRound size={19} /></div>
          <div><h2>Sign in to ShreyaDesk</h2><p>Use your saved dashboard username and password.</p></div>
        </div>
        <form id="dashboard-credentials" name="dashboard-login" action="/api/login" method="post" autoComplete="on" ref={credentialsForm} className={credentialsRevealed ? 'dashboard-credentials is-revealed' : 'dashboard-credentials'} onSubmit={submit}>
          <Field label="Dashboard username" name="username" autoComplete="username" placeholder="Enter dashboard username" />
          <Field label="Dashboard password" name="password" autoComplete="current-password" placeholder="Enter dashboard password" type="password" />
          {error && <div className="error-message"><X size={15} /> {error}</div>}
          <button className="primary-button" type="submit" disabled={busy}>{busy ? <RefreshCw size={17} className="spin" /> : <ShieldCheck size={17} />}{busy ? 'Signing in...' : 'Sign in securely'}<ArrowUpRight size={16} /></button>
        </form>
        <div className="privacy-note"><ShieldCheck size={15} /><span>Your access token is kept on this backend and never sent to the browser.</span></div>
      </section>
    </main>
  )
}

function KiteConnectGate({ onDisconnect, onRetry, session }) {
  return <main className="auth-layout kite-connect-layout"><section className="auth-intro"><div className="auth-brand"><img className="brand-logo" src="/weblogo.svg" alt="" /><span>Shreya<span className="accent">Desk</span></span></div><p className="eyebrow">KITE CONNECT / PRIVATE CONSOLE</p><h1>Your market desk,<br /><em>ready to connect.</em></h1><p className="intro-copy">Your dashboard session is secure. Connect your Zerodha account to continue to the trading workspace.</p><div className="intro-footer"><span className="status-dot" /> Dashboard session is active <ChevronRight size={14} /></div></section><section className="auth-panel"><div className="panel-topline"><span>02</span><span>Broker connection</span></div><div className="form-heading"><div className="icon-box"><KeyRound size={19} /></div><div><h2>Connect to Kite</h2><p>Continue to Zerodha's hosted login. The request token returns to the protected backend callback.</p></div></div>{session?.kite_pending && <p role="status">Kite login is awaiting completion for this dashboard session. Returning with Back does not complete it.</p>}{session?.connection_error && <p role="alert">{session.connection_error}</p>}<button className="secondary-button" onClick={onRetry}>Check connection again</button><a className="kite-connect-button" href="/api/kite/login"><KiteIcon /> Connect to Kite <ArrowUpRight size={16} /></a><div className="privacy-note"><ShieldCheck size={15} /><span>Your API secret and access token stay on the backend.</span></div><button className="logout-button gate-disconnect" onClick={onDisconnect}><LogOut size={16} /> Sign out</button></section></main>
}

function Field({ label, name, autoComplete, placeholder, type = 'text' }) {
  return <label className="field"><span>{label}</span><input required id={`dashboard-${name}`} name={name} type={type} placeholder={placeholder} autoComplete={autoComplete} autoCapitalize="none" spellCheck={false} /></label>
}

function Dashboard({ tab, setTab, onLogout }) {
  const [profile, setProfile] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false)

  async function loadProfile() {
    setLoading(true)
    setError('')
    try { setProfile(await api('/api/profile')) } catch (profileError) { setError(profileError.message) } finally { setLoading(false) }
  }

  useEffect(() => { loadProfile() }, [])
  useEffect(() => {
    document.body.classList.toggle('mobile-menu-open', mobileMenuOpen)
    return () => document.body.classList.remove('mobile-menu-open')
  }, [mobileMenuOpen])

  async function logout() {
    await api('/api/logout', { method: 'POST' }).catch(() => {})
    onLogout()
  }

  function navigate(nextTab) { setTab(nextTab); setMobileMenuOpen(false) }

  return <main className="app-shell">
    <aside className="sidebar">
      <div className="sidebar-brand"><div className="brand-mark"><img className="brand-logo" src="/weblogo.svg" alt="" /></div><span>Shreya<span className="accent">Desk</span></span></div>
      <div className="workspace-label">Workspace <span>LOCAL</span></div>
      <nav>{navigationTabs.map(({ id, label, icon: Icon }) => <button key={id} className={tab === id ? 'nav-item active' : 'nav-item'} onClick={() => navigate(id)}><Icon size={17} /> {label}</button>)}</nav>
      <div className="sidebar-bottom"><div className="connection-card"><span className="status-dot" /><div><strong>Connected</strong><small>Session is active</small></div></div><button className="logout-button" onClick={logout}><LogOut size={16} /> Disconnect</button></div>
    </aside>
    <section className="content-area">
      <header className="mobile-header"><div className="sidebar-brand"><div className="brand-mark"><img className="brand-logo" src="/weblogo.svg" alt="" /></div><span>Shreya<span className="accent">Desk</span></span></div><div className="mobile-header-actions"><button className="avatar avatar-button" type="button" aria-label="Open User profile" onClick={() => navigate('user')}>{profile?.avatar_url ? <img src={profile.avatar_url} alt="" /> : profile?.user_name?.slice(0, 1) || 'K'}</button><button className="mobile-menu-button" aria-label={mobileMenuOpen ? 'Close navigation menu' : 'Open navigation menu'} onClick={() => setMobileMenuOpen((open) => !open)}>{mobileMenuOpen ? <X size={23} /> : <Menu size={23} />}</button></div></header>
      {mobileMenuOpen && <div className="mobile-menu-overlay"><div className="mobile-menu-top"><div className="sidebar-brand"><div className="brand-mark"><img className="brand-logo" src="/weblogo.svg" alt="" /></div><span>Shreya<span className="accent">Desk</span></span></div><button className="mobile-menu-button" aria-label="Close navigation menu" onClick={() => setMobileMenuOpen(false)}><X size={23} /></button></div><div className="mobile-menu-divider" /><nav className="mobile-menu-nav" aria-label="Mobile navigation">{navigationTabs.map(({ id, label, icon: Icon }, index) => <button key={id} style={{'--menu-index': index}} aria-current={tab === id ? 'page' : undefined} className={tab === id ? 'mobile-menu-item active' : 'mobile-menu-item'} onClick={() => navigate(id)}><Icon size={21} aria-hidden="true" />{label}</button>)}</nav><div className="mobile-menu-bottom"><button className="logout-button" onClick={logout}><LogOut size={19} aria-hidden="true" /> Disconnect</button></div></div>}
      <header className="topbar"><div><p className="eyebrow">PERSONAL TRADING WORKSPACE</p><h1>{tabs.find((item) => item.id === tab)?.label}</h1></div><div className="topbar-actions"><button className="avatar avatar-button" type="button" aria-label="Open User profile" onClick={() => navigate('user')}>{profile?.avatar_url ? <img src={profile.avatar_url} alt="" /> : profile?.user_name?.slice(0, 1) || 'K'}</button></div></header>
      {tab !== 'user' && <BrokerAccountNotice api={api}/>}
      {tab === 'search' ? <SearchPanel api={api} /> : tab === 'analytics' ? <AnalyticsPanel api={api} /> : tab === 'user' ? <UserPanel profile={profile} loading={loading} error={error} onRefresh={loadProfile} /> : tab === 'holdings' ? <HoldingsPanel /> : tab === 'signals' ? <SignalsPanel /> : tab === 'settings' ? <SettingsPanel /> : tab === 'demo' ? <DemoTradingPanel /> : tab === 'live' ? <LiveTradingPage /> : tab === 'overview' ? <OverviewPanel profile={profile} /> : <PlaceholderPanel type={tab} profile={profile} />}
    </section>
  </main>
}

function UserPanel({ profile, loading, error, onRefresh }) {
  return <div className="dashboard-content"><div className="welcome-row"><div><p className="section-kicker">ACCOUNT IDENTITY</p><h2>Welcome back{profile?.user_name ? `, ${profile.user_name.split(' ')[0]}` : ''}.</h2><p className="muted">Your profile is pulled directly from your connected Kite account.</p></div><button className="secondary-button" onClick={onRefresh} disabled={loading}><RefreshCw size={16} className={loading ? 'spin' : ''} /> Refresh</button></div>{error ? <div className="error-message large"><X size={15} /> {error}</div> : <div className="profile-grid">{[['User name', profile?.user_name], ['User ID', profile?.user_id], ['Products', profile?.products?.join(', ')], ['Exchanges', profile?.exchanges?.join(', ')]].map(([label, value]) => <div className="profile-item" key={label}><span>{label}</span><strong>{loading ? 'Loading...' : value || 'Not provided'}</strong></div>)}</div>}<div className="security-banner"><ShieldCheck size={20} /><div><strong>Private by design</strong><p>Your access token remains on the backend. Session activity below records dashboard access.</p></div></div><a className="kite-connect-button" href="/api/kite/login">Reconnect to Kite</a><BrokerAccountNotice api={api} alwaysShow /><SessionActivity api={api} /><TelegramSettings api={api} /></div>
}

function SignalsPanel() {
  const [scanProgress, setScanProgress] = useState(null)
  const [signals, setSignals] = useState([])
  const [config, setConfig] = useState(null)
  const [chartSymbol,setChartSymbol] = useState('')
  const isSupertrend = config?.strategy === 'supertrend'
  const [lastRun, setLastRun] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [sort, setSort] = useState({ key: 'trigger_date', direction: 'desc' })
  const [freshOnly, setFreshOnly] = useState(false)
  const todayIST = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Kolkata', year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date())
  const isCurrentCandidate = signal => ['confirmed_3d','supertrend_1d'].includes(signal.occ_mode) && signal.confirmed_eligible === true && signal.execution_session === todayIST
  const filteringFresh = freshOnly && (isSupertrend || (config?.use_alternate_resolution && config?.alternate_mode === 'confirmed'))
  const ownSignals = signals.filter(signal=>(signal.strategy || 'occ') === (config?.strategy || 'occ'))
  const visibleSignals = filteringFresh ? ownSignals.filter(isCurrentCandidate) : ownSignals
  const occColumns = [['signal_type', 'Latest crossover'], ['trigger_date', 'Crossover / confirmation date'], ['trigger_price', 'Trigger price'], ['current_price', 'Current price'], ['stop_loss', 'Stop loss'], ['trailing_stop', 'TSL'], ['take_profit', 'Take profit'], ['change_from_entry_pct', '% from entry'], ['current_volume', 'Volume (1D)'], ['avg_volume_30d', 'Avg volume (30d)'], ['rsi_14_1d', 'RSI (14 · 1D)'], ['pe_ratio', 'P/E'], ['week_52_high_distance_pct', '% below 52W high'], ['change_1d_pct', '1D change %'], ['change_1m_pct', '1M change %'], ['change_1y_pct', '1Y change %']]
  const signalColumns = isSupertrend ? [...occColumns.map(([key,label])=>[key,key==='signal_type'?'Latest flip':key==='trigger_date'?'Confirmed flip date':label]),['supertrend_value','Supertrend (1D)'],['direction','Trend']] : occColumns
  const [columnOrder, setColumnOrder] = useState(() => { try { const saved = JSON.parse(localStorage.getItem('occ-signal-columns')); return saved ? [...saved.filter((key) => signalColumns.some(([column]) => column === key)), ...signalColumns.map(([key]) => key).filter((key) => !saved.includes(key))] : signalColumns.map(([key]) => key) } catch { return signalColumns.map(([key]) => key) } })
  const [draggedColumn, setDraggedColumn] = useState(null)

  const displayColumns = [...columnOrder.filter(key=>signalColumns.some(([k])=>k===key)), ...signalColumns.map(([key])=>key).filter(key=>!columnOrder.includes(key))]
  useEffect(()=>{
    setChartSymbol('')
    try {const saved=JSON.parse(localStorage.getItem(isSupertrend?'st-signal-columns':'occ-signal-columns'));setColumnOrder(saved || signalColumns.map(([key])=>key))} catch {setColumnOrder(signalColumns.map(([key])=>key))}
  },[isSupertrend])

  async function loadResults() {
    try { const response = await api('/api/scanner/results'); setSignals(response.results); setConfig(response.config); setLastRun(response.last_run) } catch (signalError) { setError(signalError.message) }
  }

  useEffect(() => { loadResults() }, [])

  async function runScan() {
    setLoading(true); setError(''); setScanProgress(null)
    try {
      const started = await api('/api/scanner/run', { method: 'POST' })
      await new Promise((resolve, reject) => {
        let attempts = 0
        const poll = async () => {
          try {
            attempts += 1
            const job = await api(`/api/scanner/run/${started.job_id}`)
            setScanProgress(job)
            if (job.status === 'completed') {
              const response = job.result
              setSignals(response.results); setLastRun({ run_id: response.run_id, ran_at: response.ran_at, scanned: response.scanned, duration_seconds: response.duration_seconds })
              await loadResults()
              let refreshAttempts = 0
              const refreshFundamentals = async () => {
                refreshAttempts += 1
                await loadResults()
                if (refreshAttempts < 10) window.setTimeout(refreshFundamentals, 3000)
              }
              window.setTimeout(refreshFundamentals, 1500)
              resolve(); return
            }
            if (job.status === 'failed') { reject(new Error(job.error)); return }
            if (attempts >= 1800) { reject(new Error('Scanner is still running. Refresh Signals later to see the completed run.')); return }
            window.setTimeout(poll, 2000)
          } catch (pollError) { reject(pollError) }
        }
        poll()
      })
    } catch (scanError) { setError(scanError.message) } finally { setLoading(false) }
  }

  function sortedSignals() {
    return [...visibleSignals].sort((left, right) => {
      const a = left[sort.key] ?? ''
      const b = right[sort.key] ?? ''
      const comparison = typeof a === 'number' && typeof b === 'number' ? a - b : String(a).localeCompare(String(b))
      return sort.direction === 'asc' ? comparison : -comparison
    })
  }

  function changeSort(key) {
    setSort((current) => ({ key, direction: current.key === key && current.direction === 'asc' ? 'desc' : 'asc' }))
  }

  function downloadCsv() {
    const columns = ['symbol', ...displayColumns]
    const csv = [columns.join(','), ...sortedSignals().map((signal) => columns.map((column) => JSON.stringify(signal[column] ?? '')).join(','))].join('\n')
    const url = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }))
    const link = document.createElement('a'); link.href = url; link.download = `occ-signals-${new Date().toISOString().slice(0, 10)}.csv`; link.click(); URL.revokeObjectURL(url)
  }

  function moveColumn(target) {
    if (!draggedColumn || draggedColumn === target) return
    const next = columnOrder.filter((column) => column !== draggedColumn)
    next.splice(next.indexOf(target), 0, draggedColumn)
    setColumnOrder(next)
    localStorage.setItem(isSupertrend ? 'st-signal-columns' : 'occ-signal-columns', JSON.stringify(next))
  }

  const columnLabel = Object.fromEntries(signalColumns)
  const formatCell = (signal, key) => {
    if (key === 'direction') return signal.direction === -1 ? 'Uptrend' : 'Downtrend'
    if (key === 'signal_type') return <span className={`signal-badge ${signal.signal_type.toLowerCase()}`}>{signal.signal_type === 'BUY' ? <ArrowUp size={13} /> : <ArrowDownRight size={13} />}{signal.signal_type}</span>
    if (key === 'trailing_stop') return signal.trailing_stop ? `${signal.trailing_stop.toFixed(2)}${signal.tsl_active ? '' : ' (waiting)'}` : 'Not active'
    if (key === 'trigger_date') return <span className="date-cell">{signal.trigger_date}{['confirmed_3d','supertrend_1d'].includes(signal.occ_mode) && <small style={{display:'block'}} title={signal.confirmed_reason}>{isCurrentCandidate(signal) ? 'Current-session candidate · risk checks required' : signal.execution_session !== todayIST ? 'Saved result · run a fresh scan' : signal.confirmed_reason}</small>}</span>
    if (key === 'current_volume') {
      const value = signal[key]
      if (value == null || !Number.isFinite(Number(value))) return 'N/A'
      return <span title={`Shares traded on ${signal.volume_date || signal.rsi_date || 'the latest session'} as of the last scan; partial during market hours`}>{Number(value).toLocaleString('en-IN', {maximumFractionDigits: 0})}</span>
    }
    if (key === 'avg_volume_30d') {
      if (signal[key] == null) return 'N/A'
      const value = Number(signal[key]); const divisor = value >= 1000000 ? 1000000 : value >= 1000 ? 1000 : 1; const suffix = divisor === 1000000 ? ' M' : divisor === 1000 ? ' K' : ''; const compact = Math.floor((value / divisor) * 100) / 100
      return `${compact.toFixed(2)}${suffix}`
    }
    if (key === 'rsi_14_1d') return signal[key] == null ? 'N/A' : <span title={`Daily RSI as of ${signal.rsi_date || 'latest session'}; provisional during the session`}>{Number(signal[key]).toFixed(2)}</span>
    if (key === 'pe_ratio') return signal[key] == null ? 'N/A' : signal[key].toFixed(2)
    if (key === 'week_52_high_distance_pct') return signal[key] == null ? 'N/A' : `${signal[key].toFixed(2)}%`
    if (key === 'change_from_entry_pct' && signal.current_price == null) return 'N/A'
    if (key === 'change_from_entry_pct') return `${(((signal.current_price - signal.trigger_price) / signal.trigger_price) * 100).toFixed(2)}%`
    if (key.startsWith('change_')) return signal[key] == null ? 'N/A' : `${signal[key].toFixed(2)}%`
    return signal[key] == null ? 'N/A' : Number(signal[key]).toFixed(2)
  }

  return <div className="dashboard-content signals-content">
    <div className="welcome-row"><div><p className="section-kicker">{config?.universe_mode === 'full_nse' ? 'FULL NSE' : config?.index_name || 'NIFTY100'} / CNC LONG ONLY</p><h2>{isSupertrend ? 'Latest Supertrend flips.' : 'Latest OCC crossovers.'}</h2><p className="muted">{isSupertrend ? `Daily Supertrend · ATR ${config.st_atr_length ?? 10} · Factor ${config.st_factor ?? 3} · Completed candles only` : `MA ${config?.ma_type || 'SMMA'} ${config?.length || 5} scans daily NSE candles for bullish flips.`}</p></div><div className="signal-actions"><button className="secondary-button" onClick={downloadCsv} disabled={!signals.length}><Download size={15} /> Download CSV</button><button className="primary-button scan-button" onClick={runScan} disabled={loading}>{loading ? <RefreshCw size={16} className="spin" /> : <Activity size={16} />}{loading ? 'Running scan...' : 'Run scan now'}<ArrowUpRight size={15} /></button></div></div>
    {error && <div className="error-message large"><X size={15} /> {error}</div>}
    <p className="muted">{isSupertrend ? 'Signals are confirmed daily flips, never intrabar. BUY is a bullish flip; EXIT is a bearish flip, not a short order. Execution is eligible only in the next trading session, after existing risk checks. Select a symbol to view its candle chart.' : <>OCC calculation: {config?.use_alternate_resolution ? config?.alternate_mode === 'confirmed' ? '1D × 3 = 3D · Confirmed execution · Completed blocks only; next-session risk checks apply' : '1D × 3 = 3D · Alternate ON · Historical comparison only; orders blocked' : '1D · Alternate OFF'}. Dates show the crossover in comparison mode and the final 3D session in confirmed mode. TradingView strategy entry arrows normally appear on the following bar; they are not crossover timestamps. A new scan refreshes prices, but only a new crossover creates a new signal.</>}</p><div className="results-meta"><span><strong>{signals.filter((signal) => signal.signal_type === 'BUY').length}</strong> latest bullish events</span><span>{signals.length} latest events</span><span>Last scanned: {lastRun?.ran_at ? new Intl.DateTimeFormat('en-IN', { dateStyle: 'medium', timeStyle: 'short', timeZone: 'Asia/Kolkata' }).format(new Date(lastRun.ran_at)) + ' IST' : 'Never'}</span>{lastRun?.duration_seconds != null && <span>Completed in {lastRun.duration_seconds}s</span>}<span>{(isSupertrend || (config?.use_alternate_resolution && config?.alternate_mode === 'confirmed')) ? 'Confirmed execution mode' : 'Read-only scanner'}</span><span>{isSupertrend ? 'Closed 1D candles' : config?.session_filter ? 'NSE session filter on' : 'Session filter off'}</span></div>
    {(isSupertrend || (config?.use_alternate_resolution && config?.alternate_mode === 'confirmed')) && <label className="muted"><input type="checkbox" checked={freshOnly} onChange={event => setFreshOnly(event.target.checked)} /> Current-session candidates only</label>}
    {filteringFresh && !visibleSignals.length && <p role="status" className="muted">No current-session confirmed candidates in these results. Run a fresh scan; a new BUY requires a newly confirmed candle flip. Existing risk checks and duplicate-order protection still apply.</p>}
    {!!lastRun?.skipped_quotes?.length && <details className="muted"><summary>{lastRun.skipped_quotes.length} symbols excluded: {isSupertrend ? 'unavailable or invalid candle history' : 'unavailable or invalid live quotes'}</summary><ul>{lastRun.skipped_quotes.map(item => <li key={item.symbol}>{item.symbol}: {item.reason}</li>)}</ul><p>These symbols produced no signals in this run. Retry when valid quotes are available.</p></details>}
    {loading && <p className="muted" role="status">{scanProgress?.stage || 'Starting scan'}{scanProgress?.total ? ` · ${scanProgress.processed || 0} / ${scanProgress.total} stocks processed` : ''}</p>}
    <div className="table-wrap"><table className="signal-table occ-table"><thead><tr><th className="sticky-symbol">Symbol</th>{displayColumns.map((key) => <th key={key} draggable onDragStart={() => setDraggedColumn(key)} onDragOver={(event) => event.preventDefault()} onDrop={() => moveColumn(key)}><button className="sort-button" onClick={() => changeSort(key)}>{columnLabel[key]}<ArrowUpDown size={12} /></button></th>)}</tr></thead><tbody>{sortedSignals().map((signal) => <tr key={`${signal.symbol}-${signal.trigger_date}`}><td className="ticker-cell sticky-symbol">{isSupertrend ? <button type="button" className="sort-button" onClick={()=>setChartSymbol(signal.symbol)} aria-label={`Chart ${signal.symbol}`}>{signal.symbol}</button> : signal.symbol}<small>{signal.company}</small></td>{displayColumns.map((key) => { const entryChange = key === 'change_from_entry_pct' ? ((signal.current_price - signal.trigger_price) / signal.trigger_price) * 100 : signal[key]; return <td key={key} className={(key.startsWith('change_') || key === 'change_from_entry_pct') ? (entryChange >= 0 ? 'gain' : 'loss') : ''}>{formatCell(signal, key)}</td>})}</tr>)}</tbody></table></div>
    {!signals.length && <div className="scanner-empty"><Activity size={22} /><strong>No saved scanner results.</strong><p>Run the scanner now or configure the selected strategy first.</p></div>}
    {isSupertrend && <SupertrendChart api={api} symbol={chartSymbol} onSymbol={setChartSymbol} />}
  </div>
}

function OpenOrdersTable({ orders }) { return <div className="table-wrap"><table className="signal-table"><thead><tr><th>Time</th><th>Symbol</th><th>Side</th><th>Type</th><th>Quantity</th><th>Price</th><th>Status</th><th>Kite order ID</th></tr></thead><tbody>{orders.map((order) => <tr key={order.id}><td className="date-cell">{order.timestamp.slice(0, 19).replace('T', ' ')}</td><td className="ticker-cell">{order.symbol}</td><td>{order.signal_source.startsWith('BUY') ? 'BUY' : 'SELL'}</td><td>{order.order_type}</td><td>{order.requested_qty}</td><td>{order.requested_price ?? '-'}</td><td>{order.status}</td><td>{order.kite_order_id || '-'}</td></tr>)}</tbody></table>{!orders.length && <div className="table-empty">No open live orders.</div>}</div> }

function GttRecovery({ position, onChanged }) {
  const [phrase, setPhrase] = useState(''), [triggerId, setTriggerId] = useState(''), [error, setError] = useState(''), [busy, setBusy] = useState(false)
  const uncertain = ['UNKNOWN', 'PLACING'].includes(position.gtt_status)
  async function reconcile() {
    setBusy(true); setError('')
    try { await api(`/api/autotrade/positions/${position.id}/gtt/reconcile`, {method:'POST', body:JSON.stringify({confirm_phrase:phrase, trigger_id:triggerId ? Number(triggerId) : null})}); setPhrase(''); await onChanged() }
    catch (err) { setError(err.message) } finally { setBusy(false) }
  }
  return <details><summary>Reconcile protection</summary><p>Verify this position in Kite first. {uncertain ? 'Enter the matching GTT ID to resolve uncertain placement.' : 'This retries protection; an existing GTT must be verified inactive first.'}</p>{uncertain && <input aria-label={`Broker GTT ID for ${position.symbol}`} type="number" min="1" value={triggerId} onChange={e=>setTriggerId(e.target.value)} />}<input aria-label={`GTT confirmation for ${position.symbol}`} placeholder="RECONCILE GTT" value={phrase} onChange={e=>setPhrase(e.target.value)} /><button className="secondary-button" disabled={busy || phrase!=='RECONCILE GTT' || (uncertain && !triggerId)} onClick={reconcile}>Reconcile GTT</button>{error && <p role="alert">{error}</p>}</details>
}

function LivePositionTable({ positions, onClosed }) {
  const [confirmation, setConfirmation] = useState({})
  const [error, setError] = useState('')
  async function close(position) { try { await api(`/api/autotrade/positions/${position.id}/close`, { method: 'POST', body: JSON.stringify({ confirm_phrase: confirmation[position.id] || '' }) }); setConfirmation((current) => ({ ...current, [position.id]: '' })); await onClosed() } catch (closeError) { setError(closeError.message) } }
  return <div>{error && <div className="error-message large"><X size={15} /> {error}</div>}<div className="table-wrap"><table className="signal-table"><thead><tr><th>Symbol</th><th>Entry</th><th>Quantity</th><th>Stop loss</th><th>Take profit</th><th>GTT protection</th><th>Manual close</th></tr></thead><tbody>{positions.map((position) => <tr key={position.id}><td className="ticker-cell">{position.symbol}</td><td>{position.entry_price.toFixed(2)}</td><td>{position.quantity}</td><td>{position.current_sl.toFixed(2)}</td><td>{position.current_tp.toFixed(2)}</td><td style={{minWidth:180,maxWidth:280,whiteSpace:'normal'}}><span role={!['ACTIVE','NOT_REQUIRED'].includes(position.gtt_status) ? 'alert' : undefined}>{position.gtt_message || 'Protection not yet checked'}</span>{position.gtt_id && <small style={{display:'block'}}>GTT #{position.gtt_id}</small>}{position.gtt_status && (!['ACTIVE','NOT_REQUIRED'].includes(position.gtt_status) || position.gtt_message?.includes('expires')) && <GttRecovery position={position} onChanged={onClosed} />}</td><td><input className="live-close-input" value={confirmation[position.id] || ''} onChange={(event) => setConfirmation((current) => ({ ...current, [position.id]: event.target.value }))} placeholder="ENABLE LIVE TRADING" /><button className="secondary-button" disabled={confirmation[position.id] !== 'ENABLE LIVE TRADING'} onClick={() => close(position)}>Close</button></td></tr>)}</tbody></table>{!positions.length && <div className="table-empty">No live positions yet.</div>}</div></div>
}

function PositionTable({ positions }) { return <div className="table-wrap"><table className="signal-table"><thead><tr><th>Symbol</th><th>Mode</th><th>Entry</th><th>Quantity</th><th>Stop loss</th><th>Take profit</th><th>Status</th></tr></thead><tbody>{positions.map((position) => <tr key={position.id}><td className="ticker-cell">{position.symbol}</td><td>{position.mode}</td><td>{position.entry_price.toFixed(2)}</td><td>{position.quantity}</td><td>{position.current_sl.toFixed(2)}</td><td>{position.current_tp.toFixed(2)}</td><td>{position.status}</td></tr>)}</tbody></table>{!positions.length && <div className="table-empty">No Auto-Trade positions yet.</div>}</div> }
function OrderLog({ orders }) { return <div className="table-wrap"><table className="signal-table"><thead><tr><th>Time</th><th>Symbol</th><th>Source</th><th>Mode</th><th>Qty</th><th>Status</th><th>Reason</th></tr></thead><tbody>{orders.map((order) => <tr key={order.id}><td className="date-cell">{order.timestamp.slice(0, 19).replace('T', ' ')}</td><td className="ticker-cell">{order.symbol}</td><td>{order.signal_source}</td><td>{order.mode}</td><td>{order.requested_qty}</td><td>{order.status}</td><td>{order.error_message || '-'}</td></tr>)}</tbody></table>{!orders.length && <div className="table-empty">No order attempts logged.</div>}</div> }
function BacktestPanel() { const [result, setResult] = useState(null); const [loading, setLoading] = useState(false); async function run() { setLoading(true); setResult(null); try { setResult(await api('/api/autotrade/backtest/run', { method: 'POST', body: JSON.stringify({}) })) } finally { setLoading(false) } } return <div className="backtest-panel"><p className="section-kicker">HISTORICAL SIMULATION</p><h2>Validate before capital.</h2><p className="muted">The selected Auto-Trade configuration is simulated without affecting live or paper positions.</p><button className="primary-button scan-button" onClick={run} disabled={loading}>{loading ? 'Preparing...' : 'Run backtest'}</button>{result && <div className="security-banner"><Check size={18} /><div><strong>{result.status}</strong><p>{result.message}</p></div></div>}</div> }

function SettingsPanel() {
  const [config, setConfig] = useState(null)
  const [status, setStatus] = useState('')
  const [error, setError] = useState('')
  useEffect(() => { api('/api/scanner/config').then(setConfig).catch((loadError) => setError(loadError.message)) }, [])
  const update = (field) => (event) => setConfig((current) => ({ ...current, [field]: event.target.type === 'checkbox' ? event.target.checked : event.target.type === 'number' ? (event.target.value === '' ? null : Number(event.target.value)) : event.target.value }))
  async function save(event) { event.preventDefault(); setStatus(''); setError(''); try { setConfig(await api('/api/scanner/config', { method: 'POST', body: JSON.stringify(config) })); setStatus('Settings saved') } catch (saveError) { setError(saveError.message) } }
  if (!config) return <div className="dashboard-content"><div className="loading-screen"><RefreshCw size={18} className="spin" /> Loading scanner settings</div></div>
  return <div className="dashboard-content">
    <div className="welcome-row">
      <div><p className="section-kicker">SCANNER STRATEGY CONFIGURATION</p><h2>Make the scanner yours.</h2><p className="muted">Daily candles, CNC long-only. Changes apply to the next run.</p></div>
      <div><div className="subtabs" role="group" aria-label="Scanner universe">{[['index', 'Index'], ['full_nse', 'Full NSE']].map(([mode, label]) => <button key={mode} type="button" className={(config.universe_mode || 'index') === mode ? 'active' : ''} aria-pressed={(config.universe_mode || 'index') === mode} onClick={() => setConfig(current => ({ ...current, universe_mode: mode }))}>{label}</button>)}</div>{config.universe_mode !== 'full_nse' && <label className="index-picker"><span>Market index</span><select value={config.index_name} onChange={update('index_name')}>{indexOptions.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>}</div>
    </div>
    <label className="number-field"><span>Scanner strategy</span><select aria-label="Scanner strategy" value={config.strategy || 'occ'} onChange={update('strategy')}><option value="occ">OCC</option><option value="supertrend">Supertrend</option></select></label><p className="settings-help">Save to switch the strategy shown in Signals. Each strategy keeps its own results and indicator parameters. Risk overlays and filters are shared; switching alone never submits orders.</p>
    <form className="settings-form" onSubmit={save} onInvalidCapture={event => { const section = event.target.closest('details'); if (section) section.open = true }}>
      {config.strategy !== 'supertrend' && <details className="settings-section settings-disclosure"><summary>Moving average logic</summary><p className="settings-help">Alternate resolution uses 3 trading sessions per candle. Historical comparison preserves the original Pine display and blocks orders. Confirmed execution evaluates completed blocks from finalized history, eligible in the next trading session only. Saving this setting does not enable live trading.</p><div className="settings-grid"><ToggleField label="Use alternate resolution" checked={config.use_alternate_resolution ?? false} onChange={update('use_alternate_resolution')} />{config.use_alternate_resolution && <label className="number-field"><span>3D mode</span><select aria-label="3D mode" value={config.alternate_mode ?? 'comparison'} onChange={update('alternate_mode')}><option value="comparison">Historical comparison — orders blocked</option><option value="confirmed">Confirmed 3D execution</option></select></label>}<NumberField label="Alternate multiplier" value={config.alternate_multiplier ?? 3} onChange={update('alternate_multiplier')} min="3" max="3" /><SelectField label="MA type" value={config.ma_type} onChange={update('ma_type')} options={['SMA', 'EMA', 'DEMA', 'TEMA', 'WMA', 'VWMA', 'SMMA', 'HullMA', 'LSMA', 'ALMA', 'SSMA', 'TMA']} /><NumberField label="MA period" value={config.length} onChange={update('length')} min="2" max="200" /><NumberField label="Offset / sigma" value={config.offset_sigma} onChange={update('offset_sigma')} min="0" max="100" /><NumberField label="ALMA offset" value={config.alma_offset} onChange={update('alma_offset')} min="0" max="1" step="0.05" /><NumberField label="Delay bars" value={config.delay} onChange={update('delay')} min="0" max="20" /></div></details>}
      {config.strategy === 'supertrend' && <details className="settings-section settings-disclosure"><summary>Supertrend settings</summary><div className="settings-grid"><NumberField label="ATR length" value={config.st_atr_length ?? 10} onChange={update('st_atr_length')} min="1" max="200" step="1" /><NumberField label="Factor" value={config.st_factor ?? 3} onChange={update('st_factor')} min="0.01" step="0.01" /></div><p className="settings-help">1D candles only. ATR uses Wilder smoothing. Only these two parameters change the indicator. Shared filters below apply to scanner results, without changing the raw chart flips. Alternate resolution remains OCC-only. Bearish flips close Supertrend-owned positions only; no shorts. Live execution and its existing SL/TP/GTT settings remain in Live Trading.</p></details>}
      <details className="settings-section settings-disclosure"><summary>Risk management</summary><p className="settings-help">Scanner SL/TP and trailing levels are display calculations from the signal price. Actual order protection is configured separately in Live Trading.</p><div className="settings-grid"><NumberField label="Stop loss %" value={config.sl_pct} onChange={update('sl_pct')} min="0" max="100" step="0.1" /><NumberField label="TSL activation %" value={config.tsl_activation_pct} onChange={update('tsl_activation_pct')} min="0" max="100" step="0.1" /><NumberField label="TSL trail %" value={config.tsl_pct} onChange={update('tsl_pct')} min="0" max="100" step="0.1" /><NumberField label="Take profit %" value={config.tp_pct} onChange={update('tp_pct')} min="0" max="500" step="0.1" /></div></details>
      <details className="settings-section settings-disclosure"><summary>Fundamental filters</summary><div className="settings-grid fundamental-grid"><SelectField label="P/E filter" value={config.pe_filter_operator} onChange={update('pe_filter_operator')} options={['none', 'above', 'below']} /><NumberField label="P/E value" value={config.pe_filter_value ?? ''} onChange={update('pe_filter_value')} min="0" max="1000" step="0.1" /><NumberField label="Min distance from 52W high %" value={config.min_52w_high_distance_pct ?? ''} onChange={update('min_52w_high_distance_pct')} min="0" max="100" step="0.1" /><NumberField label="Max distance from 52W high %" value={config.max_52w_high_distance_pct ?? ''} onChange={update('max_52w_high_distance_pct')} min="0" max="100" step="0.1" /></div><p className="settings-help">Leave a value blank to disable that filter. Minimum and maximum distance are inclusive; for example, 10–20 keeps stocks 10% to 20% below their high. 52W high distance is the percentage the current price is below its trailing 252-session high.</p></details>
      <details className="settings-section settings-disclosure"><summary>RSI filters</summary><div className="settings-grid"><NumberField label="Exclude RSI below" value={config.rsi_min ?? ''} onChange={update('rsi_min')} min="0" max="100" step="0.1" /><NumberField label="Exclude RSI above" value={config.rsi_max ?? ''} onChange={update('rsi_max')} min="0" max="100" step="0.1" /></div><p className="settings-help">RSI length 14, daily (1D) closes, using Wilder smoothing regardless of OCC timeframe. {config.strategy === 'supertrend' ? 'Supertrend uses the latest completed daily candle.' : 'Latest session RSI updates with the current price during market hours.'} Optional inclusive bounds (0–100); leave both blank to disable. Missing RSI is excluded only when a bound is enabled. Filters apply to scanner BUY and EXIT results.</p></details>
      <details className="settings-section settings-disclosure"><summary>Volume filters</summary><div className="settings-grid"><NumberField label="Exclude volume below" value={config.volume_min ?? ''} onChange={update('volume_min')} min="0" step="1" /><NumberField label="Exclude volume above" value={config.volume_max ?? ''} onChange={update('volume_max')} min="0" step="1" /><ToggleField label={config.strategy === 'supertrend' ? 'Completed day volume > 30-day average volume' : "Today's volume > 30-day average volume"} checked={config.volume_above_30d_average ?? false} onChange={update('volume_above_30d_average')} /></div><p className="settings-help">Both bounds are optional and inclusive. {config.strategy === 'supertrend' ? 'Uses the latest completed daily volume.' : "Uses the latest trading day's share volume (partial while the market is open)."} The average uses the previous 30 trading sessions, excluding that day. On weekends and holidays, the latest available session is used. Relative filtering requires 30 prior sessions.</p></details>
      <details className="settings-section settings-disclosure"><summary>Filters and data</summary><div className="settings-grid">{config.strategy !== 'supertrend' && <ToggleField label="NSE session filter" checked={config.session_filter} onChange={update('session_filter')} />}<ToggleField label="ADX filter" checked={config.adx_enabled} onChange={update('adx_enabled')} /><NumberField label="Lookback days" value={config.lookback_days} onChange={update('lookback_days')} min="30" max="3650" /><NumberField label="Latest signal within days" value={config.max_signal_age_days ?? ''} onChange={update('max_signal_age_days')} min="1" max="3650" /></div></details>
      {error && <div className="error-message"><X size={15} /> {error}</div>}{status && <div className="success-message"><Check size={15} /> {status}</div>}<button className="primary-button save-button" type="submit"><Check size={16} /> Save settings</button>
    </form>
  </div>
}
function useTradingData() {
  const [data, setData] = useState({ config: null, positions: [], orders: [], openOrders: [], stats: null, signals: [], loading: true, error: '' })

  async function load() {
    try {
      const [config, positions, orders, openOrders, stats, scanner] = await Promise.all([
        api('/api/autotrade/config'), api('/api/autotrade/positions'), api('/api/autotrade/orders'),
        api('/api/autotrade/open-orders'), api('/api/autotrade/stats'), api('/api/scanner/results'),
      ])
      setData({ config, positions: positions.positions, orders: orders.orders, openOrders: openOrders.orders, stats, signals: scanner.results, loading: false, error: '' })
    } catch (loadError) {
      setData((current) => ({ ...current, loading: false, error: loadError.message }))
    }
  }

  useEffect(() => {
    load()
    const timer = window.setInterval(async () => {
      try {
        const [positions, orders, openOrders] = await Promise.all([api('/api/autotrade/positions'), api('/api/autotrade/orders'), api('/api/autotrade/open-orders')])
        setData(current => ({...current, positions:positions.positions, orders:orders.orders, openOrders:openOrders.orders}))
      } catch { /* Preserve the last view; explicit reload reports session errors. */ }
    }, 30000)
    return () => window.clearInterval(timer)
  }, [])
  return { ...data, reload: load }
}

function tradingMetrics(data) {
  const { positions, orders, openOrders, signals } = data
  const openPositions = positions.filter((position) => position.status === 'OPEN')
  const closedPositions = positions.filter((position) => position.status === 'CLOSED')
  const signalBySymbol = Object.fromEntries(signals.map((signal) => [signal.symbol, signal]))
  const valuedPositions = openPositions.map((position) => ({ ...position, market: signalBySymbol[position.symbol] }))
  const currentPnl = valuedPositions.reduce((total, position) => {
    const currentPrice = position.market?.current_price ?? position.entry_price
    return total + (currentPrice - position.entry_price) * position.quantity
  }, 0)
  const winners = closedPositions.filter((position) => Number(position.realized_pnl) > 0).length
  const winRate = closedPositions.length ? (winners / closedPositions.length) * 100 : null
  const movers = valuedPositions.filter((position) => position.market?.change_1d_pct != null).map((position) => ({ ...position, change: Number(position.market.change_1d_pct) }))
  const topGainer = [...movers].sort((left, right) => right.change - left.change)[0]
  const topLoser = [...movers].sort((left, right) => left.change - right.change)[0]
  return { openPositions, currentPnl, winRate, topGainer, topLoser, openOrders: openOrders.length, recentOrders: orders.slice(0, 6) }
}

function OverviewPanel({ profile }) {
  const [data,setData] = useState(null)
  const [error,setError] = useState('')
  const [refreshing,setRefreshing] = useState(false)
  const busy = useRef(false)
  const active = useRef(true)
  async function reload() {
    if(busy.current)return
    busy.current=true;setRefreshing(true)
    try { const value=await api('/api/overview'); if(active.current){setData(value);setError('')} }
    catch(e) {if(active.current)setError(e.message)}
    finally {busy.current=false;if(active.current)setRefreshing(false)}
  }
  useEffect(()=>{active.current=true;reload();const timer=setInterval(reload,30000);return()=>{active.current=false;clearInterval(timer)}},[])
  if(!data)return <div className="dashboard-content">{error ? <><p role="alert">{error}</p><button className="secondary-button" onClick={reload}>Retry overview</button></> : <div className="loading-screen">Loading overview...</div>}</div>
  const metrics = data.metrics
  const pnlClass = metrics.cumulativePnl == null || metrics.cumulativePnl === 0 ? 'neutral' : metrics.cumulativePnl > 0 ? 'gain' : 'loss'
  const formatMoney = (value) => `${value >= 0 ? '+' : '-'}₹${Math.abs(value).toLocaleString('en-IN', { maximumFractionDigits: 2 })}`
  const formatMover = (position) => position ? `${position.symbol} ${position.change >= 0 ? '+' : ''}${position.change.toFixed(2)}%` : 'No portfolio data'

  return <div className="dashboard-content overview-content">
    <div className="welcome-row overview-intro"><div><p className="section-kicker">PORTFOLIO PULSE / {profile?.user_id || 'CONNECTED'}</p><h2>Your trading desk at a glance.</h2><p className="muted">Signals, positions, and execution status in one quiet view.</p></div><button className="secondary-button" onClick={reload} disabled={refreshing}><RefreshCw size={15} className={refreshing ? 'spin' : ''} /> Refresh</button></div>
    {error && <div role="alert" className="error-message large">Refresh failed: {error}. Showing the last successful snapshot.</div>}{data.warnings.map(message=><p role="status" key={message}>{message}</p>)}<p className="muted">{data.config.mode === 'live' ? 'Live algo' : 'Demo'} positions only · Updated {new Date(data.updated_at).toLocaleTimeString('en-IN')} · Refreshes every 30 seconds</p>
    <div className="overview-grid">
      <div className={`overview-card pnl-card pnl-${pnlClass}`}><div className="card-label"><span>Cumulative P&amp;L</span><TrendingUp size={16} /></div><strong className={pnlClass}>{metrics.cumulativePnl == null ? 'Unavailable' : formatMoney(metrics.cumulativePnl)}</strong><small>{metrics.openPositions.length} open position{metrics.openPositions.length === 1 ? '' : 's'} · All-time realized + unrealized P&L</small></div>
      <div className="overview-card"><div className="card-label"><span>Open orders</span><ClipboardList size={16} /></div><strong>{data.loading ? 'Loading...' : metrics.openOrders}</strong><small>Pending or unresolved app orders</small></div>
      <div className="overview-card"><div className="card-label"><span>Win rate</span><Activity size={16} /></div><strong>{data.loading ? 'Loading...' : metrics.winRate == null ? '—' : `${metrics.winRate.toFixed(0)}%`}</strong><small>{data.positions.filter((position) => position.status === 'CLOSED').length} closed trades</small></div>
      <div className="overview-card"><div className="card-label"><span>Mode</span><CircleDollarSign size={16} /></div><strong>{data.loading ? 'Loading...' : data.config?.mode === 'live' ? 'LIVE' : 'DEMO'}</strong><small>{data.config?.enabled && !data.config?.paused ? 'Execution active' : 'Execution paused'}</small></div>
    </div>
    <div className="overview-lower-grid">
      <section className="overview-section"><div className="section-heading"><div><p className="section-kicker">PORTFOLIO MOVERS</p><h3>Today’s strongest moves</h3></div><TrendingUp size={18} /></div><div className="mover-list"><div className="mover-row"><span className="mover-icon gain"><TrendingUp size={15} /></span><div><small>Top daily gainer</small><strong>{formatMover(metrics.topGainer)}</strong></div></div><div className="mover-row"><span className="mover-icon loss"><TrendingDown size={15} /></span><div><small>Top daily loser</small><strong>{formatMover(metrics.topLoser)}</strong></div></div></div></section>
      <section className="overview-section"><div className="section-heading"><div><p className="section-kicker">EXECUTION QUEUE</p><h3>Recent order activity</h3></div><ClipboardList size={18} /></div>{metrics.recentOrders.length ? <div className="activity-list">{metrics.recentOrders.slice(0, 4).map((order) => <div className="activity-row" key={order.id}><span className={`activity-dot ${order.status.toLowerCase()}`} /><div><strong>{order.signal_source?.startsWith('BUY') ? 'BUY' : /SELL|EXIT|CLOSE/i.test(order.signal_source || '') ? 'SELL' : 'ORDER'} {order.symbol}</strong><small>{order.requested_qty} share{order.requested_qty === 1 ? '' : 's'} / {order.status}</small></div><span className="activity-time">{order.timestamp?.slice(11, 16)}</span></div>)}</div> : <div className="mini-empty">No order activity yet.</div>}</section>
    </div>

  </div>
}

function modeSettings(config, mode) {
  return {...config, ...Object.fromEntries(['sl_pct','tp_pct','exit_rule'].map(key => [key, config[`${mode}_${key}`] ?? config[key]]))}
}
function rulesPayload(config, mode) {
  const keys = mode === 'paper' ? ['per_trade_pct','paper_max_positions','paper_max_deployed_pct'] : ['max_symbol_value','max_concurrent_positions','max_daily_order_count','max_cumulative_loss']
  return {settings_mode:mode, ...Object.fromEntries(keys.map(key => [key,config[key]])), ...Object.fromEntries(['sl_pct','tp_pct','exit_rule'].map(key => [`${mode}_${key}`,config[key]]))}
}

function DemoTradingPanel() {
  const data = useTradingData()
  const [config, setConfig] = useState(null)
  const [error, setError] = useState('')
  useEffect(() => { if (data.config) setConfig(modeSettings(data.config, 'paper')) }, [data.config])
  if (!config) return <TradingLoading label="Loading demo trading" />
  const metrics = tradingMetrics({ ...data, config })
  const update = (field) => (event) => setConfig((current) => ({ ...current, [field]: event.target.type === 'number' ? Number(event.target.value) : event.target.value }))
  async function save(event) { event.preventDefault(); try { setConfig(await api('/api/autotrade/config', { method: 'POST', body: JSON.stringify(rulesPayload(config, 'paper')) })); setError(''); await data.reload() } catch (saveError) { setError(saveError.message) } }
  async function enable() { try { setConfig(await api('/api/autotrade/enable', { method: 'POST', body: JSON.stringify({ mode: 'paper', confirm_phrase: '' }) })); await data.reload() } catch (enableError) { setError(enableError.message) } }
  async function pause() { try { setConfig(await api('/api/autotrade/pause', { method: 'POST' })); await data.reload() } catch (pauseError) { setError(pauseError.message) } }
  return <TradingPage className="demo-page" kicker="SIMULATION DESK" title="Demo Trading" description="Test scanner signals with virtual capital. No broker orders are placed."><TradingBanner mode="paper" active={config.mode === 'paper' && config.enabled && !config.paused} /><TradingActions active={config.mode === 'paper' && config.enabled && !config.paused} onPause={pause} onEnable={enable} /><TradingMetricStrip metrics={metrics} mode="demo" /><form className="trading-form" onSubmit={save}><NumberField label="Virtual capital per trade %" value={config.per_trade_pct} onChange={update('per_trade_pct')} min="0.1" max="100" step="0.1" /><NumberField label="Maximum positions" value={config.paper_max_positions} onChange={update('paper_max_positions')} min="1" max="100" /><NumberField label="Maximum deployed %" value={config.paper_max_deployed_pct} onChange={update('paper_max_deployed_pct')} min="1" max="100" /><NumberField label="Stop loss %" value={config.sl_pct} onChange={update('sl_pct')} min="0" max="100" step="0.1" /><NumberField label="Take profit %" value={config.tp_pct} onChange={update('tp_pct')} min="0" max="500" step="0.1" /><SelectField label="Exit rule" value={config.exit_rule} onChange={update('exit_rule')} options={['whichever_first', 'scanner_exit', 'risk_levels_only']} /><button className="primary-button save-button" type="submit"><Check size={15} /> Save demo settings</button></form>{error && <div className="error-message large"><X size={15} /> {error}</div>}<PositionTable positions={data.positions} /></TradingPage>
}

function ApplyExistingProtection({config, onChanged}) {
  const [phrase,setPhrase] = useState('')
  const [busy,setBusy] = useState(false)
  const [result,setResult] = useState(null)
  const [error,setError] = useState('')
  const live = modeSettings(config,'live')
  async function apply() {
    setBusy(true);setError('');setResult(null)
    try {
      setResult(await api('/api/autotrade/apply-existing-protection',{method:'POST',body:JSON.stringify({confirm_phrase:phrase})}))
      setPhrase('');await onChanged()
    } catch (e) {setError(e.message)}
    finally {setBusy(false)}
  }
  return <section className="live-gate">
    <strong>Apply saved SL/TP to existing positions</strong>
    <p>Saved Live SL {live.sl_pct}% / TP {live.tp_pct}%. Save changes above first. Recalculates from each recorded entry price for open live positions with an active GTT. Local levels change only after broker verification.</p>
    <p>Positions without an active GTT, with outstanding orders, or with invalid trigger prices are reported individually. Demo positions are unchanged.</p>
    <div><input aria-label="Apply existing protection confirmation" placeholder="APPLY LIVE SL TP" value={phrase} onChange={e=>setPhrase(e.target.value)} />
      <button className="secondary-button" disabled={busy || phrase!=='APPLY LIVE SL TP' || live.exit_rule==='scanner_exit'} onClick={apply}>{busy?'Updating protection…':'Apply to existing positions'}</button></div>
    {live.exit_rule==='scanner_exit' && <p>Select and save a GTT exit rule above first.</p>}
    {error && <p role="alert" className="error-message">{error}</p>}
    {result && <div role="status">{result.results.length ? result.results.map(row=><p key={row.position_id}><strong>{row.symbol}</strong>: {row.status==='UPDATED'?`Updated — SL ₹${row.sl}, TP ₹${row.tp}`:row.reason}</p>):<p>No open live positions to update.</p>}</div>}
  </section>
}

function LiveTradingPage() {
  const data = useTradingData()
  const [config, setConfig] = useState(null)
  const [serverConfig, setServerConfig] = useState(null)
  const [phrase, setPhrase] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  useEffect(() => {
    if (data.config) { setConfig(modeSettings(data.config, 'live')); setServerConfig(data.config) }
  }, [data.config])
  useEffect(() => {
    let mounted = true
    const timer = setInterval(async () => {
      try { const latest = await api('/api/autotrade/config'); if (mounted) setServerConfig(latest) }
      catch (refreshError) { if (mounted) setError(refreshError.message) }
    }, 30000)
    return () => { mounted = false; clearInterval(timer) }
  }, [])
  if (!config) return <TradingLoading label="Loading live trading" />
  const update = (field) => (event) => setConfig((current) => ({ ...current, [field]: event.target.type === 'number' ? Number(event.target.value) : event.target.value }))
  async function action(path, body) {
    setBusy(true); setError('')
    try {
      setConfig(await api(path, { method: 'POST', ...(body && { body: JSON.stringify(body) }) }))
      setPhrase(''); await data.reload()
    } catch (actionError) { setError(actionError.message) }
    finally { setBusy(false) }
  }
  async function save(event) { event.preventDefault(); await action('/api/autotrade/config', rulesPayload(config, 'live')) }
  const persisted = serverConfig || config
  const active = persisted.mode === 'live' && persisted.enabled && !persisted.paused && !persisted.kill_switch
  return <TradingPage className="live-page" kicker="BROKER EXECUTION" title="Live Trading" description="Whole-share LIMIT orders, sized up to the per-symbol ceiling.">
    <TradingBanner mode="live" active={active} />
    <div className="trading-actions">
      <button className="secondary-button" disabled={busy} onClick={() => action('/api/autotrade/pause')}><Pause size={15} /> Pause execution</button>
      <button className="primary-button" disabled={busy} onClick={() => action('/api/autotrade/kill-switch')}><ShieldCheck size={15} /> Kill switch — stop all orders</button>
    </div>
    <p className="muted">Total capital: ₹20,000. SELL submissions do not consume the daily BUY allowance. No automatic liquidation.</p>
    <form className="trading-form" onSubmit={save}>
      <NumberField label="Maximum value per symbol ₹" value={config.max_symbol_value} onChange={update('max_symbol_value')} min="1" max="20000" step="1" />
      <NumberField label="Concurrent positions (includes pending BUYs)" value={config.max_concurrent_positions} onChange={update('max_concurrent_positions')} min="1" max="20" step="1" />
      <NumberField label="BUY submissions per IST day" value={config.max_daily_order_count} onChange={update('max_daily_order_count')} min="1" step="1" />
      <NumberField label="Cumulative loss stop ₹" value={config.max_cumulative_loss} onChange={update('max_cumulative_loss')} min="1" step="1" />
      <NumberField label="Live stop loss %" value={config.sl_pct} onChange={update('sl_pct')} min="0" max="100" step="0.1" />
      <NumberField label="Live take profit %" value={config.tp_pct} onChange={update('tp_pct')} min="0" max="500" step="0.1" />
      <label className="number-field"><span>Live exit rule</span><select value={config.exit_rule} onChange={update('exit_rule')}><option value="whichever_first">GTT SL/TP + scanner exit (whichever first)</option><option value="risk_levels_only">GTT SL/TP only</option><option value="scanner_exit">Scanner exits only (no GTT)</option></select></label>
      <p className="muted">Live rules are independent of Demo. GTT modes place protection after confirmed BUY fills. Saving pauses execution; it does not enable trading.</p>
      <button className="primary-button save-button" disabled={busy} type="submit"><Check size={15} /> Save limits and pause</button>
    </form>
    {(error || data.error) && <div role="alert" className="error-message large"><X size={15} /> {error || data.error}</div>}
    <ApplyExistingProtection config={persisted} onChanged={data.reload} />
    <RiskControls config={persisted} onChanged={data.reload} />
    <div className="live-gate"><strong>Enable real broker orders</strong>
      <p>Initialize the cumulative baseline first. Enabling never clears a kill switch or resets P&amp;L. LIMIT only; chasing is disabled.</p>
      <div><input aria-label="Live enable confirmation" value={phrase} onChange={(event) => setPhrase(event.target.value)} placeholder="ENABLE LIVE TRADING" />
        <button className="secondary-button" type="button" disabled={busy || phrase !== 'ENABLE LIVE TRADING' || persisted.kill_switch || !persisted.cumulative_loss_baseline || persisted.baseline_requires_reset} onClick={() => action('/api/autotrade/enable', { mode: 'live', confirm_phrase: phrase })}><Play size={14} /> Enable live</button>
      </div>
    </div>
    <OpenOrdersTable orders={data.openOrders} />
    <p className="muted">GTT protection follows the saved exit rule. A trigger submits a LIMIT SELL; it is not a guaranteed fill. Broker sell authorization is still required.</p>
    <LivePositionTable positions={data.positions.filter((row) => row.mode === 'live' && row.status === 'OPEN')} onClosed={data.reload} />
  </TradingPage>
}

function RiskControls({ config, onChanged }) {
  const [phrase, setPhrase] = useState('')
  const [day, setDay] = useState('')
  const [historyRows, setHistoryRows] = useState(null)
  const [prices, setPrices] = useState({})
  const [reconcilePhrase, setReconcilePhrase] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [status, setStatus] = useState('')
  const [risk, setRisk] = useState(config)
  useEffect(() => { setRisk(config); if (config.kill_switch) setStatus('') }, [config])
  useEffect(() => {
    let mounted = true
    setHistoryRows(null); setPrices({})
    if (day) api(`/api/autotrade/risk/history-positions?day=${encodeURIComponent(day)}`)
      .then((result) => { if (mounted) setHistoryRows(result.positions) })
      .catch((loadError) => { if (mounted) setError(loadError.message) })
    return () => { mounted = false }
  }, [day])
  async function submit(path, body, message) {
    setBusy(true); setError(''); setStatus('')
    try {
      setRisk(await api(path, { method: 'POST', body: JSON.stringify(body) }))
      setPhrase(''); setReconcilePhrase(''); setStatus(message); await onChanged()
    } catch (actionError) { setError(actionError.message) }
    finally { setBusy(false) }
  }
  const money = (amount) => amount == null ? 'Unavailable' : `₹${amount.toLocaleString('en-IN', { maximumFractionDigits: 2 })}`
  return <section className="live-gate" aria-label="Cumulative risk controls">
    <strong>{risk.kill_switch ? 'KILL SWITCH LATCHED' : 'Cumulative loss monitor'}</strong>
    <p>{risk.risk_reason}</p>
    <p>Baseline: {risk.cumulative_loss_baseline || 'Not initialized'}. No midnight reset.</p>
    <p>Risk last checked: {risk.risk_checked_at || 'Not checked yet'}. Status refreshes every 30 seconds.</p><button className="secondary-button" type="button" onClick={onChanged}>Refresh risk status</button>
    <p>Algo cumulative P&amp;L: {money(risk.app_cumulative_pnl)}. Only app-managed trades are included; manual holdings are excluded.</p>
    {risk.risk_history_error && <p role="alert" className="loss">BUYs blocked: {risk.risk_history_error}</p>}
    {risk.cancellation_errors?.map((message) => <p className="loss" key={message}>{message}</p>)}
    <p>Clearing captures current P&amp;L as a new zero point and leaves execution paused. It requires all outstanding app orders to be reconciled.</p>
    <div><input aria-label="Clear kill switch confirmation" value={phrase} onChange={(event) => setPhrase(event.target.value)} placeholder="CLEAR KILL SWITCH" />
      <button className="secondary-button" disabled={busy || phrase !== 'CLEAR KILL SWITCH'} onClick={() => submit('/api/autotrade/kill-switch/clear', { confirm_phrase: phrase }, 'Baseline reset. Execution remains paused.')}>Clear kill switch / set baseline</button>
    </div>
    <details><summary>Reconcile missing algo trade history</summary>
      <p>Select a completed IST day. Verify the day-end price for each app position shown. Recorded exits are filled automatically. Account-wide P&amp;L totals are not accepted. This verifies history coverage without adding to or resetting cumulative P&amp;L.</p>
      <label className="number-field"><span>IST date</span><input type="date" value={day} onChange={(event) => setDay(event.target.value)} /></label>
      {day && historyRows === null && <p>Loading app trades…</p>}
      {historyRows?.length === 0 && <p>No app trades existed by this date. Confirming records an empty app-trade snapshot.</p>}
      {historyRows?.map((row) => row.needs_price
        ? <label className="number-field" key={row.position_id}><span>{row.symbol} · Trade #{row.position_id} · {row.quantity} shares · Verified day-end price ₹</span><input type="number" min="0.01" step="0.01" value={prices[row.position_id] ?? ''} onChange={(event) => setPrices((current) => ({ ...current, [row.position_id]: event.target.value }))} /></label>
        : <p key={row.position_id}>{row.symbol} · Trade #{row.position_id} · Recorded exit ₹{row.recorded_exit_price}</p>)}
      <input aria-label="Algo history reconciliation confirmation" value={reconcilePhrase} onChange={(event) => setReconcilePhrase(event.target.value)} placeholder="RECONCILE ALGO HISTORY" />
      <button className="secondary-button" disabled={busy || !day || historyRows === null || historyRows.some((row) => row.needs_price && !(Number(prices[row.position_id]) > 0)) || reconcilePhrase !== 'RECONCILE ALGO HISTORY'} onClick={() => submit('/api/autotrade/risk/reconcile-day', { day, prices: historyRows.filter((row) => row.needs_price).map((row) => ({ position_id: row.position_id, price: Number(prices[row.position_id]) })), confirm_phrase: reconcilePhrase }, 'App-trade history verified. Execution remains paused.')}>Record verified app day</button>
    </details>
    {error && <p role="alert" className="loss">{error}</p>}
    {status && <p role="status">{status}</p>}
  </section>
}

function TradingLoading({ label }) { return <div className="dashboard-content"><div className="loading-screen"><RefreshCw size={18} className="spin" /> {label}</div></div> }
function TradingPage({ children, className, kicker, title, description }) { return <div className={`dashboard-content trading-page ${className}`}><div className="welcome-row"><div><p className="section-kicker">{kicker}</p><h2>{title}</h2><p className="muted">{description}</p></div></div>{children}</div> }
function TradingBanner({ mode, active }) { return <div className={`trade-banner ${mode === 'live' ? 'live' : 'paper'}`}><div><strong>{mode === 'live' ? 'LIVE EXECUTION' : 'DEMO MODE'}</strong><span>{mode === 'live' ? 'Real CNC orders from scanner signals.' : 'Simulated positions only. No real Kite orders.'}</span></div><span className="trade-status">{active ? 'ACTIVE' : 'PAUSED'}</span></div> }
function TradingActions({ active, onPause, onEnable }) { return <div className="trading-actions"><button className="secondary-button" onClick={onPause} disabled={!active}><Pause size={15} /> Pause</button><button className="primary-button scan-button" onClick={onEnable}><Play size={15} /> {active ? 'Running' : 'Enable'}</button></div> }
function TradingMetricStrip({ metrics, mode }) { return <div className="trading-metric-strip"><div><span>Current P&amp;L</span><strong className={metrics.currentPnl >= 0 ? 'gain' : 'loss'}>{metrics.currentPnl >= 0 ? '+' : ''}₹{metrics.currentPnl.toLocaleString('en-IN', { maximumFractionDigits: 2 })}</strong></div><div><span>Open positions</span><strong>{metrics.openPositions.length}</strong></div><div><span>Open orders</span><strong>{metrics.openOrders}</strong></div><div><span>Desk</span><strong>{mode === 'live' ? 'LIVE' : 'DEMO'}</strong></div></div> }

function SelectField({ label, value, onChange, options }) { return <label className="number-field"><span>{label}</span><select value={value} onChange={onChange}>{options.map((option) => <option key={option}>{option}</option>)}</select></label> }
function ToggleField({ label, checked, onChange }) { return <label className="toggle-field"><input type="checkbox" checked={checked} onChange={onChange} /><span>{label}</span></label> }

function LegacySignalsPanel() {
  const [parameters, setParameters] = useState({ short_sma: 6, long_sma: 30, lookback_days: 90, max_stocks: 25 })
  const [signals, setSignals] = useState([])
  const [summary, setSummary] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const update = (field) => (event) => setParameters((current) => ({ ...current, [field]: event.target.type === 'number' ? Number(event.target.value) : event.target.value }))

  async function generate(event) {
    event.preventDefault()
    setLoading(true)
    setError('')
    try {
      const response = await api('/api/signals', { method: 'POST', body: JSON.stringify(parameters) })
      setSignals(response.results)
      setSummary(response)
    } catch (signalError) {
      setError(signalError.message)
      setSignals([])
      setSummary(null)
    } finally {
      setLoading(false)
    }
  }

  return <div className="dashboard-content signals-content">
    <div className="welcome-row"><div><p className="section-kicker">NIFTY 100 SCANNER</p><h2>Find the latest crossovers.</h2><p className="muted">Scan daily candles and rank the freshest SMA events first.</p></div></div>
    <form className="scanner-controls" onSubmit={generate}>
      <div className="controls-heading"><SlidersHorizontal size={17} /><span>Scanner parameters</span></div>
      <div className="control-fields">
        <NumberField label="Short SMA" value={parameters.short_sma} onChange={update('short_sma')} min="2" max="200" />
        <NumberField label="Long SMA" value={parameters.long_sma} onChange={update('long_sma')} min="3" max="400" />
        <NumberField label="Lookback days" value={parameters.lookback_days} onChange={update('lookback_days')} min="2" max="3650" />
        <NumberField label="Max stocks" value={parameters.max_stocks} onChange={update('max_stocks')} min="1" max="100" />
        <button className="primary-button scan-button" type="submit" disabled={loading}>{loading ? <RefreshCw size={16} className="spin" /> : <Activity size={16} />}{loading ? 'Scanning...' : 'Generate signals'}<ArrowUpRight size={15} /></button>
      </div>
    </form>
    {error && <div className="error-message large"><X size={15} /> {error}</div>}
    {summary && !error && <div className="results-meta"><span><strong>{summary.returned}</strong> signals found</span><span>{summary.scanned} Nifty 100 stocks scanned</span><span>{summary.matched_instruments} NSE instruments matched</span><span>{summary.candle_series} candle series loaded</span><span>Latest data {summary.latest_candle_date || 'unavailable'}</span><span>SMA {summary.parameters.short_sma} / SMA {summary.parameters.long_sma}</span></div>}
    {summary && !error ? <SignalTable signals={signals} shortSma={summary.parameters.short_sma} longSma={summary.parameters.long_sma} diagnostics={summary} /> : <div className="scanner-empty"><Activity size={22} /><strong>Ready when you are.</strong><p>Set your windows above and generate a fresh scan.</p></div>}
  </div>
}

function NumberField({ label, value, onChange, min, max, step = '1' }) {
  return <label className="number-field"><span>{label}</span><input type="number" min={min} max={max} step={step} value={value} onChange={onChange} /></label>
}

function SignalTable({ signals, shortSma, longSma, diagnostics }) {
  if (!signals.length) return <div className="scanner-empty"><Activity size={22} /><strong>No crossovers in this window.</strong><p>{diagnostics.candle_series ? 'Market data loaded successfully. Try increasing the lookback days or adjusting the SMA windows.' : 'No historical candle series were loaded. Check the Kite session and NSE instrument mapping.'}</p></div>
  return <div className="table-wrap"><table className="signal-table"><thead><tr><th>Rank</th><th>Ticker</th><th>Company</th><th>Crossover type</th><th>Crossover date</th><th>Close</th><th>SMA {shortSma}</th><th>SMA {longSma}</th></tr></thead><tbody>{signals.map((signal) => <tr key={`${signal.ticker}-${signal.crossover_date}`}><td className="rank-cell">{String(signal.rank).padStart(2, '0')}</td><td className="ticker-cell">{signal.ticker}</td><td className="company-cell">{signal.company}</td><td><span className={`signal-badge ${signal.crossover_type.toLowerCase()}`}>{signal.crossover_type === 'Bullish' ? <ArrowUp size={13} /> : <ArrowDownRight size={13} />}{signal.crossover_type}</span></td><td className="date-cell">{signal.crossover_date}</td><td>{signal.close.toFixed(2)}</td><td>{signal.short_sma.toFixed(2)}</td><td>{signal.long_sma.toFixed(2)}</td></tr>)}</tbody></table></div>
}

function PlaceholderPanel({ type, profile }) {
  const overview = type === 'overview'
  return <div className="dashboard-content"><div className="welcome-row"><div><p className="section-kicker">{overview ? 'SESSION OVERVIEW' : 'ACCOUNT SETTINGS'}</p><h2>{overview ? 'A clear view of your connection.' : 'Account preferences.'}</h2><p className="muted">{overview ? 'Your local Kite session is ready for the next workflow.' : 'More account controls can live here as your workspace grows.'}</p></div></div><div className="stat-row"><div className="stat"><span>Connection</span><strong><i className="status-dot" /> Active</strong></div><div className="stat"><span>Profile</span><strong>{profile ? 'Synced' : 'Pending'}</strong></div><div className="stat"><span>Environment</span><strong>Local</strong></div></div><div className="empty-state"><BarChart3 size={22} /><p>{overview ? 'Your dashboard is ready.' : 'This tab is ready for your next idea.'}</p></div></div>
}

export default App

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <App />
  </StrictMode>,
)


function HoldingsPanel() {
  const [mode, setMode] = useState('algo')
  const [sort, setSort] = useState({key:'symbol',direction:'asc'})
  const [data, setData] = useState(null)
  const [signals, setSignals] = useState({})
  const [strategy, setStrategy] = useState('occ')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [revision, setRevision] = useState(0)
  useEffect(() => {
    const controller = new AbortController()
    let active = true
    async function load() {
      setLoading(true); setError(''); setSignals({}); setData(null)
      try {
        const [result, settings] = await Promise.all([api('/api/holdings', { signal: controller.signal }), api('/api/scanner/config', { signal: controller.signal })])
        if (!active) return
        setStrategy(settings.strategy || 'occ'); setData(result); setLoading(false)
        const unique = [...new Map([...result.algo, ...result.personal].map(row => [`${row.exchange}:${row.symbol}`, row])).entries()]
        for (const [key, row] of unique) {
          if (!active) return
          try {
            const signal = await api(`/api/holdings/signal?symbol=${encodeURIComponent(row.symbol)}&exchange=${row.exchange}`, { signal: controller.signal })
            if (active) setSignals(previous => ({ ...previous, [key]: signal }))
          } catch (err) {
            if (active) setSignals(previous => ({ ...previous, [key]: { error: err.message } }))
          }
          await new Promise(resolve => setTimeout(resolve, 400))
        }
      } catch (err) { if (active) setError(err.message) }
      finally { if (active) setLoading(false) }
    }
    load()
    return () => { active = false; controller.abort() }
  }, [revision])
  const money = value => value == null ? '—' : new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 2 }).format(value)
  const rows = sortHoldings(data?.[mode] || [], sort, signals)
  const columns = [['symbol','Symbol'],['signal_type',strategy === 'supertrend' ? 'Supertrend signal' : 'OCC signal'],['trigger_date','Signal date'],['average_cost','Avg. cost'],['ltp','LTP'],['quantity','Quantity'],['pnl','P&L']]
  function downloadHoldings() {
    const url = URL.createObjectURL(new Blob([holdingsCsv(rows,signals,strategy,mode,data.updated_at)],{type:'text/csv;charset=utf-8'}))
    const link = document.createElement('a'); link.href=url; link.download=`${mode}-holdings-${new Date().toISOString().slice(0,10)}.csv`
    document.body.appendChild(link); link.click(); link.remove(); setTimeout(()=>URL.revokeObjectURL(url),1000)
  }
  return <TradingPage className="holdings-page" kicker="YOUR PORTFOLIO" title="Current holdings" description={`Your algo and personal delivery shares, with ${strategy === 'supertrend' ? 'Supertrend' : 'OCC'} signals from your scanner settings.`}>
    <div className="holdings-toolbar"><div className="subtabs">{[['algo', 'Algo holdings'], ['personal', 'Personal holdings']].map(([id, label]) => <button key={id} className={mode === id ? 'active' : ''} aria-pressed={mode === id} onClick={() => setMode(id)}>{label}{data ? ` (${data[id].length})` : ''}</button>)}</div><button className="secondary-button" disabled={loading || !rows.length} onClick={downloadHoldings}>Export as CSV</button><button className="secondary-button" disabled={loading} onClick={() => setRevision(value => value + 1)}><RefreshCw size={16} /> Refresh</button></div>
    <p className="muted">{mode === 'algo' ? 'Live positions recorded by the app. Paper trades are excluded.' : 'Broker delivery inventory after subtracting app-managed shares. Shared-symbol costs are estimates, as marked below.'}</p>
    {data && <p className="muted">Prices fetched {new Date(data.updated_at).toLocaleString('en-IN', { timeZone: 'Asia/Kolkata' })} IST · P&L excludes fees and taxes.</p>}
    {error && <p role="alert" className="error-message">{error}</p>}
    {data?.warnings.map(message => <p role="status" className="error-message" key={message}>{message}</p>)}
    <div className="table-wrap"><table className="signal-table"><thead><tr>{columns.map(([key,label]) => <th key={key} aria-sort={sort.key===key?(sort.direction==='asc'?'ascending':'descending'):'none'}><button className="sort-button" onClick={()=>setSort(previous=>({key,direction:previous.key===key&&previous.direction==='asc'?'desc':'asc'}))}>{label} <ArrowUpDown size={13} aria-hidden="true" /></button></th>)}</tr></thead><tbody>
      {rows.map(row => { const key = `${row.exchange}:${row.symbol}`; const signal = signals[key]; return <tr key={key}><td className="ticker-cell"><strong>{row.symbol}</strong><small>{row.exchange}</small>{row.gtt_protection?.map((g, i) => <small key={i} title={g.gtt_message}>GTT: {g.gtt_status}{g.gtt_id ? ` #${g.gtt_id}` : ''}</small>)}{row.note && <small className="holding-note">{row.note}</small>}</td><td><span className={`signal-badge ${signal?.signal_type === 'BUY' ? 'buy' : signal?.signal_type === 'EXIT' ? 'exit' : ''}`} title={signal?.error || ''}>{!signal ? 'Loading…' : signal.error ? 'Unavailable' : signal.signal_type || 'No crossover'}</span></td><td>{signal?.trigger_date || '—'}</td><td>{money(row.average_cost)}{row.note?.startsWith('Estimated') ? ' *' : ''}</td><td>{money(row.ltp)}</td><td>{row.quantity}</td><td className={row.pnl > 0 ? 'gain' : row.pnl < 0 ? 'loss' : ''}>{money(row.pnl)}</td></tr> })}
      {!rows.length && <tr><td colSpan={7}>{loading ? 'Loading holdings…' : error ? 'Holdings could not be loaded.' : `No ${mode === 'algo' ? 'algo-managed' : 'personal'} holdings.`}</td></tr>}
    </tbody></table></div>
    <p className="muted">{strategy === 'supertrend' ? 'Supertrend uses completed 1D candles and your saved ATR length, factor and ADX settings.' : 'OCC follows the scanner’s alternate-resolution setting.'} The latest BUY/EXIT event and its IST date are shown for the selected strategy. Held stocks remain visible regardless of scanner universe or result filters. Signals are informational and do not place orders.</p>
  </TradingPage>
}
