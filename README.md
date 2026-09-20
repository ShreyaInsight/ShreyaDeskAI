# ShreyaDesk

ShreyaDesk is a local, single-user trading research dashboard for Zerodha Kite Connect. It has a React + Vite frontend and a Python + FastAPI backend.

The scanner is read-only. Auto-Trade consumes its results for paper trading or explicitly enabled live LIMIT orders. Live execution always starts paused; see the safety rules below.

## Shared NSE trading-session calendar

`backend/market_calendar.py` is the common source for session days and hours used
by orders, risk-history coverage, scheduled scans, daily candle caching, quote
overlays, Supertrend and confirmed 3D OCC. The versioned local data file is
`backend/data/nse_calendar.json`; it cites NSE Capital Market circulars and covers
2025–2026, including the January 2026 election closure, weekend Budget sessions,
and published 2025 Muhurat hours. No network request occurs on calendar checks.

An unknown year or an announced special session without recorded official hours
blocks execution with an update-required error. The bundled 2026 Muhurat date is
known, but its hours remain unverified until an official timing circular is added.
Ordinary holidays/weekends have no session. Five minutes before the actual session
close remains the last-order cutoff. Risk history finalizes 45 minutes after that
session closes; only completed sessions require coverage. A holiday neither
creates a missing-history gap nor resets cumulative losses or a kill switch.

The fixed schedule stays **09:00 cache, 12:00 scan without execution, 15:00 scan
with existing execution checks**, all IST. Cron runs daily, and the shared calendar
skips non-session dates. Scan times outside a special session are skipped rather
than moved; manual scans may execute inside the permitted special-session window.
A missed schedule is not replayed later. Manual holiday scans use cached daily
history and scanner fundamentals; they cannot invent a current-session quote.
Search's separate on-demand fundamental research is unaffected.

OCC retains its verified anchor, aggregation and MA formulas. Within published
coverage the official session list determines three-session blocks, even if a
benchmark candle is missing. Before that coverage, existing observed benchmark
candles remain historical chart evidence only; they do not authorize trading in
unknown years. Supertrend likewise retains older broker candles for historical
indicator warm-up, without assigning them invented session hours.

Calendar maintenance (local administrator): read NSE's new annual/amending and
special-session circulars, prepare a reviewed JSON using the existing schema,
retain historical coverage, and update `version`, `updated_at`, `sources`,
`holidays`, and `special_sessions`. While trading is paused, run:

```bash
.venv/bin/python -m backend.market_calendar /path/to/reviewed-nse-calendar.json
```

The installer validates and atomically replaces the local file; the running
process notices updates within about one second. Unknown special hours use null
for both times and stay blocked. Inspect the authenticated
`GET /api/market-calendar` endpoint for version, covered years, today's session
hours, closure reason or missing-calendar error. NSE can amend calendars during
the year: review new circulars as well as the annual release; this command does
not automatically discover or approve special-session times.

## Broker account ownership

Live orders, positions and GTT records store the verified Kite `user_id` as
`account_id`. Accounting reconciliation and GTT creation, cancellation, recovery
and modification verify the connected account against the stored owner before
using broker state. Missing or mismatched identity blocks the action, persists a
live-trading pause and displays a broker-account safety alert.

A different account completing OAuth does not replace the existing connection.
The dashboard shows an account-change confirmation screen. Choose **Keep original
account** while its positions, orders or GTTs remain unresolved. Once all exposure
and legacy ownership are resolved, switching requires typing
`SWITCH BROKER ACCOUNT TO <new user_id>`. Historical records retain their original
owner. A switch leaves trading paused and requires a separately confirmed new
P&L baseline and live activation; it never clears an existing kill-switch latch.
Use **User → Reconnect to Kite** to start the OAuth flow. Candidate credentials
remain private to the initiating dashboard session and expire after ten minutes.

Existing live records are migrated with an unverified (NULL) owner, never assigned
to whichever account happens to be connected. The dashboard safety panel lists
these records. Verify every listed record against your Kite trade history, then
enter the account ID and type `BIND LEGACY RECORDS TO <user_id>`. This is an explicit
ownership attestation, not broker-derived proof; do not confirm if records belong
to different accounts. It assigns only previously unverified records, records an
audit event, and leaves trading paused. Paper records and blocked attempts without
a verified broker submission do not require broker ownership.

Existing broker GTTs remain active independently while local ownership is
unverified, but app reconciliation and GTT changes are blocked until confirmation.
No migration, account switch or deployment automatically resets the baseline,
enables live trading, reassigns historical owners or submits orders.

Authenticated endpoints: `GET /api/broker-account`,
`POST /api/broker-account/bind-legacy`, and `POST /api/broker-account/switch`.
Both POST endpoints also require the existing CSRF protection.

## Live execution ceilings and cumulative kill switch

This section supersedes older Auto-Trade behavior descriptions below.
Every backend startup persists `enabled=false`, `paused=true` before starting
scheduled work. Saving settings also pauses execution. Run exactly one backend
worker as supplied by the systemd service. Existing positions/orders are preserved.

| Control | Configured value | Meaning |
|---|---:|---|
| Symbol ceiling | ₹1,000 | Whole-share BUY value checked against a fresh quote; SELLs exempt |
| Concurrent positions | 20 | Open symbols plus unresolved BUY symbols; no overlapping entries |
| Daily BUY submissions | 5 | Reserved submissions per IST date; SELLs do not count |
| Cumulative loss stop | ₹2,500 | Persistent latch, no automatic date reset |
| Total intended capital | ₹20,000 | Symbol ceiling × slot count; no percentage-of-free-balance gate |

Live sizing is `floor(max_symbol_value / current_quote)`. There is no separate
max-order-value, fixed allocation, allocation percentage fallback, deployed-capital
percentage or separate deployed-capital input on the live path. Whole-share
rounding, available broker funds, and fees mean deployment may be below ₹20,000.
Settings must remain positive and `symbol ceiling × positions <= ₹20,000`.
Paper-only allocation controls remain separate.

The boundary rejects overlapping entries and counts unresolved BUY reservations
as occupied slots. A completed/reconciled close frees a slot. SQLite `BEGIN
IMMEDIATE` encloses the daily-count check and durable BUY reservation; rejected,
cancelled, and uncertain submitted BUYs retain their daily allowance usage.
Scanner skips have no reservation and do not count. A new IST day gets a fresh
BUY allowance, but neither P&L nor the kill-switch latch resets.

LIMIT orders are mandatory, and chasing is disabled. The ₹1,000 symbol ceiling
applies only to BUYs. SELLs can close the entire matching position regardless of
its appreciated value, with no order splitting and no daily SELL-count limit.
Manual, scanner and risk exits use the same exemption. Broker-session checks,
manual kill-switch enforcement and unresolved-order protection still apply.
Caps cover app-managed positions/reservations; manual Kite trades are outside
allocation accounting.

### Cumulative P&L and reset

`backend/risk.py` persists the risk state separately from editable configuration.
A manual reset captures `cumulative_loss_baseline` and a snapshot of every
app-managed live trade, keyed by its position ID, with recorded entry, quantity,
exit/current price and calculated P&L. It requires **CLEAR KILL SWITCH**,
authentication, a CSRF token, verified app-trade data and no unresolved app orders.
It leaves trading paused; **ENABLE LIVE TRADING** remains a separate action.
Deployment never initializes the baseline or clears a latch automatically.

```text
Each closed app trade: (recorded exit price - recorded entry price) × quantity
Each open app trade:   (current symbol quote - recorded entry price) × quantity
Algo cumulative P&L = sum(current app-trade P&L) - sum(reset trade snapshots)
Trigger: algo cumulative P&L <= -max_cumulative_loss
```

Only `autotrade_positions` rows in live mode contribute. Quotes are requested only
for app-managed open positions. Kite's overall positions/day P&L and holdings API
are never read by the cumulative monitor, baseline reset or reconciliation flow.
Manual holdings cannot trigger or offset the algo's loss stop. Recorded closed P&L
must agree with the app's recorded entry/exit arithmetic; missing or inconsistent
trade records block new BUYs. Fees and taxes remain outside this calculation.
A pre-reset unrealized loss is excluded even after the same position closes.

Per-trade snapshots are persisted every 30 seconds after initialization, even while
paused. Snapshots finalize 45 minutes after the shared calendar
session close (16:15 IST on regular days). Missing completed trading sessions block
new BUYs until app history is verified or the baseline is explicitly reset;
non-trading holidays and weekends require no snapshots. These daily
snapshots are coverage evidence and are **not summed into the loss calculation**.
Available current algo P&L can still trigger the latch while history is missing.

The **Reconcile missing algo trade history** control lists app position IDs for the
selected day. Supply a verified day-end price for every position then open. The
backend supplies recorded prices for exits already completed by that date, derives
all P&L from the app's recorded entries and quantities, and rejects missing,
duplicate or unknown trade IDs. If no app trades existed, confirm an empty snapshot.
Unrestricted portfolio P&L totals are not accepted. This requires the typed phrase
**RECONCILE ALGO HISTORY**, is audited, pauses execution and never clears the latch
or changes current cumulative P&L. The coverage record is traceable to app trades.

Existing broker-wide day records/audits remain archived and are never read by the
active calculation. An old initialized baseline without per-trade snapshots is
marked as requiring an explicit app-only reset; migration preserves its timestamp
and any kill latches instead of silently resetting risk history.

On loss breach the app persists `enabled=false`, `paused=true`, `kill_switch=true`
and attempts cancellation of outstanding app orders. The latch survives midnight,
restart, settings saves and paper/live mode changes. It blocks new BUYs without
liquidating holdings. A separately confirmed manual SELL remains possible, subject
to the existing broker-session and unresolved-order checks.

The standalone **Kill switch — stop all orders** also latches persistently and
attempts cancellation. Unlike the loss-only latch, it blocks manual SELL submissions
too. Cancellation failures and unknown broker acceptance remain visible; reservations
are not released just because cancellation was requested. Already submitted broker
orders may fill. A ₹2,500 trigger is not a guarantee losses cannot exceed ₹2,500.

Risk tables: `autotrade_risk_state`, `autotrade_app_pnl_days`, `autotrade_risk_audit`.
BUY count reservations: `autotrade_buy_reservations`. Never delete reservations or
position history to bypass the controls.

New authenticated endpoints:

```text
POST /api/autotrade/kill-switch
POST /api/autotrade/kill-switch/clear       {"confirm_phrase":"CLEAR KILL SWITCH"}
GET  /api/autotrade/risk/history-positions?day=YYYY-MM-DD
POST /api/autotrade/risk/reconcile-day      {"day":"YYYY-MM-DD","prices":[{"position_id":1,"price":95}],"confirm_phrase":"RECONCILE ALGO HISTORY"}
```

Verification uses fake brokers and temporary databases:

```bash
.venv/bin/python -m pip install -r backend/requirements-dev.txt
.venv/bin/python -m unittest discover -s backend/tests -v
cd frontend
npm run build
```

UFW remains enabled with default-deny inbound, HTTP/HTTPS allowed and SSH restricted
to the configured administration IP and Tailscale. Update SSH rules before changing
administration networks.

## Features

- Zerodha Kite Connect login using API key, API secret, and request token.
- Backend-only access-token storage. The access token is never returned to React or rendered in the browser.
- Local session reuse across frontend hot reloads and backend development reloads.
- Zerodha profile display, including the profile avatar when `avatar_url` is available.
- OCC Strategy Optimized scanner for CNC long-only equity research.
- Daily NSE candle processing.
- Configurable moving-average type, period, delay, risk percentages, ADX option, and market universe.
- BUY and EXIT signal results with entry/current price, stop loss, TSL, target, volume, valuation, and price changes.
- Official NSE Indices constituent CSV support.
- Nifty 50, Nifty Next 50, Nifty 100, Nifty Next 100, Nifty 200, Nifty Total Market, Nifty 500, midcap, smallcap, microcap, factor, and India VIX universes.
- SQLite persistence for scanner configuration, scan runs, and signal history.
- Automatic scans follow the shared-calendar timetable documented below (12:00 and 15:00 IST).
- Sortable result columns.
- Drag-and-drop result-column ordering with Symbol pinned on the left.
- Column order saved in browser `localStorage`.
- CSV export following the current result-column order.
- Responsive dark dashboard UI with orange `#FF4500` accent styling.

## Architecture

```text
Browser
  |
  | React + Vite on http://127.0.0.1:5173
  | /api requests are proxied by Vite
  v
FastAPI on http://127.0.0.1:8000
  |
  +-- KiteConnect SDK -- profile, instruments, historical candles
  +-- NSE Indices -- official constituent CSVs
  +-- NSE quote API -- best-effort P/E values
  +-- scanner.sqlite3 -- config, runs, and signal history
  +-- .kite_token.json -- local session file; ignored by Git
```

### Frontend

- `frontend/src/main.jsx` contains authentication state, dashboard navigation, profile loading, OCC results, Settings, sorting, drag-and-drop columns, and CSV download.
- `frontend/src/styles.css` contains the visual system and responsive layout.
- `frontend/vite.config.js` proxies `/api` calls to FastAPI during development.

### Backend

- `backend/main.py` creates the FastAPI application, handles Kite login/profile/session routes, loads official index universes, and registers scanner routes.
- `backend/scanner.py` contains the moving averages, OCC crossover logic, ADX calculation, risk-management calculations, SQLite storage, result history, and scheduler.
- `backend/autotrade.py` contains namespaced Auto-Trade persistence, paper order generation, safety gates, position/order logs, and the live-order stub.
- `backend/requirements.txt` contains Python dependencies.

## Requirements

- Python 3.10 or newer.
- Node.js 18 or newer.
- An active Zerodha Kite Connect developer application.
- A Kite API key and API secret.
- A request token generated through the Kite login flow.
- A Kite Connect subscription that permits the required historical-data API access.

## Installation

Run these commands from the project root:

```powershell
python -m pip install -r backend/requirements.txt
```

Install frontend packages:

```powershell
Set-Location frontend
npm install
Set-Location ..
```

Optional environment file:

```powershell
New-Item -ItemType Directory -Force "$HOME/.local/share/shreyadesk"
Copy-Item .env.example "$HOME/.local/share/shreyadesk/shreyadesk.env"
```

`.env.example` currently contains:

```text
FRONTEND_ORIGIN=http://localhost:5173
```

Use `FRONTEND_ORIGIN` when the frontend is served from another origin. The Vite development server normally uses `http://127.0.0.1:5173` or `http://localhost:5173`.

## Running the application

Open two PowerShell terminals in the project root.

Terminal 1, FastAPI:

```powershell
python -m uvicorn backend.main:app --reload --port 8000
```

Terminal 2, Vite:

```powershell
Set-Location frontend
npm run dev
```

Open:

- Frontend: http://127.0.0.1:5173/
- Backend health: http://127.0.0.1:8000/api/health
- FastAPI docs: http://127.0.0.1:8000/docs

For a production frontend bundle:

```powershell
Set-Location frontend
npm run build
```

## Linux VM deployment

### Security bootstrap

Before starting the service, configure dashboard authentication. Do not put a plaintext password in the private environment file:

```bash
python3 deploy/create-password-hash.py
${EDITOR:-vi} "$HOME/.local/share/shreyadesk/shreyadesk.env"
```

Set `SHREYADESK_USERNAME` and paste the generated `SHREYADESK_PASSWORD_HASH`. The dashboard login now creates a server-side session in a secure HttpOnly, SameSite cookie. All API routes except `/api/health` and `/api/login` require that session and a CSRF token for state-changing requests. Order submission is disabled during backend startup. Cumulative P&L sampling can run while paused; execution requires an authenticated live enable action.

Set `KITE_API_KEY` and `KITE_API_SECRET` to the real values from the Kite developer application. After dashboard authentication, `GET /api/kite/login` redirects the user to Kite's hosted login URL. Kite returns to `GET /api/kite/callback?request_token=...&status=success`; the backend exchanges the short-lived request token immediately, stores the access token server-side, and redirects back to the frontend without exposing either token. Both routes must be used over HTTPS.

For the firewall, first replace the placeholder with your fixed home IP and verify SSH access from that network:

```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow from YOUR_HOME_PUBLIC_IP to any port 22 proto tcp
sudo ufw allow 80/tcp
sudo ufw enable
sudo ufw status verbose
```

Do not enable this until `YOUR_HOME_PUBLIC_IP` is replaced and a second SSH session has been tested.

The included deployment files run the API as a systemd service and serve the built React application through Nginx. The API listens only on `127.0.0.1`; only Nginx needs to be exposed publicly.

On the VM, from the project root:

```bash
chmod +x deploy/setup-vm.sh
./deploy/setup-vm.sh
```

The script creates `.venv`, installs the backend requirements, runs `npm ci` and `npm run build`, creates a private `shreyadesk.env` from `.env.example` when needed, and enables `shreyadesk.service`. Node.js 18 or newer and `sudo` must already be installed. The installer fills the service template with the current project path, Linux user, and private runtime directory.

Install Nginx using the VM's package manager, then publish the frontend:

```bash
sudo cp deploy/nginx-shreyadesk.conf /etc/nginx/sites-available/shreyadesk
sudo ln -s /etc/nginx/sites-available/shreyadesk /etc/nginx/sites-enabled/shreyadesk
sudo nginx -t
sudo systemctl reload nginx
```

The template uses `server_name _;` and works as the default site. Replace it with your domain or VM IP if the machine hosts multiple sites. The API proxy allows up to ten minutes for large universes because historical candles are fetched from the broker API. When no fundamental filters are enabled, ratios are refreshed in the background so they do not delay scan completion.

Set `FRONTEND_ORIGIN` in `$HOME/.local/share/shreyadesk/shreyadesk.env` to the exact public URL used in the browser, for example `http://your-domain.example`. Keep port `8000` closed in the VM firewall and allow only SSH plus HTTP/HTTPS. The Kite token is stored in `$HOME/.local/share/shreyadesk/.kite_token.json` (mode `600`). Never commit tokens, private environment files, or trading databases. Add TLS with Certbot before using the dashboard over the public internet.

Useful checks after deployment:

```bash
curl http://127.0.0.1:8000/api/health
sudo systemctl status shreyadesk
sudo journalctl -u shreyadesk -f
```

## Kite login and token security

1. Enter the Kite API key.
2. Enter the Kite API secret.
3. Enter the request token generated by Kite.
4. Select **Connect securely**.
5. FastAPI calls `KiteConnect.generate_session()`.
6. The backend writes the API key, access token, and connection timestamp to `.kite_token.json` in the private runtime directory.
7. The HTTP response contains only `{ "connected": true }`.
8. React calls `/api/profile` to load profile data separately.

The browser never receives the access token. React does not store it in state, `localStorage`, cookies, query parameters, or rendered HTML.

The private runtime directory is outside the checkout. Do not commit tokens, environment files, credentials, or database files.

The Kite access token normally expires according to Kite's session rules. The local file avoids unnecessary login during development, but it does not bypass Kite expiration or revocation. Use **Disconnect** to delete the local session and force a new login.

## Dashboard

### Overview

Shows local connection status, profile synchronization status, and the current environment.

### User

Calls `GET /api/profile` and displays:

- User name
- User ID
- Products
- Exchanges
- Avatar from Zerodha's `avatar_url`, with an initial fallback if no URL is supplied

### Signals

Shows the latest saved OCC scanner events. The page displays the selected market universe, MA configuration, active BUY count, event count, read-only status, and session-filter status.

Available actions:

- **Run scan now** calls `POST /api/scanner/run`.
- **Download CSV** exports the currently available rows in the current column order.
- Click a column header to sort ascending or descending.
- Drag any non-Symbol table header to another position.
- Symbol stays pinned to the left while horizontally scrolling.

Column order is saved under the browser key `occ-signal-columns`. The Symbol column is always rendered first. New columns are appended automatically to an older saved layout.

### Settings

The Settings page persists scanner configuration in SQLite. Changes apply to the next manual or scheduled scan.

Settings groups:

1. **Market universe**
   - Select one of the supported NSE indices.
   - Changing the index downloads that index's official constituent list and replaces the saved watchlist.

2. **Moving average logic**
   - MA type
   - MA period
   - Offset/sigma
   - ALMA offset
   - Delay bars

3. **Risk management**
   - Fixed stop-loss percentage
   - TSL activation percentage
   - TSL trail percentage
   - Take-profit percentage

4. **Filters and data**
   - NSE session filter toggle
   - ADX filter toggle
   - Lookback days
  - Latest signal within a configurable number of days
  - P/E filter, with above/below operator and threshold
  - Maximum percentage below the trailing 52-week high

P/E values are loaded from Screener.in during the scan, with an NSE fallback. A blank threshold or `none` operator disables that filter. The 52-week-high distance is calculated as `(52-week high - current price) / 52-week high * 100`; for example, a value of `10` keeps stocks no more than 10% below their 52-week high.

The latest-signal setting filters each symbol using its most recent BUY or EXIT crossover date. Blank means no additional trigger-date restriction. Average 30-day volume is displayed in the Signals table with compact K or M units.

## Supported index universes

The application fetches constituents from the official NSE Indices website at `https://www.niftyindices.com/IndexConstituent/`.

| UI name | Internal name | Typical constituent count |
|---|---|---:|
| Nifty 50 Index | `NIFTY50` | 50 |
| Nifty Next 50 Index | `NIFTYNEXT50` | 50 |
| Nifty 100 Index | `NIFTY100` | 100 |
| Nifty Next 100 | `NIFTYNEXT100` | 100 |
| Nifty 200 Index | `NIFTY200` | 200 |
| Nifty Total Market Index | `NIFTYTOTALMARKET` | approximately 750 |
| Nifty 500 Index | `NIFTY500` | 500 |
| Nifty 500 Multicap 50:25:25 Index | `NIFTY500MULTICAP502525` | 500 |
| Nifty500 LargeMidSmall Equal-Cap Weighted | `NIFTY500LARGEMIDSMALLEQUAL` | 500 |
| Nifty Midcap150 Index | `NIFTYMIDCAP150` | 150 |
| Nifty Midcap 50 Index | `NIFTYMIDCAP50` | 50 |
| Nifty Midcap Select Index | `NIFTYMIDCAPSELECT` | 25 |
| Nifty Midcap 100 Index | `NIFTYMIDCAP100` | 100 |
| Nifty Smallcap 500 | `NIFTYSMALLCAP500` | approximately 500 |
| Nifty Smallcap 250 Index | `NIFTYSMALLCAP250` | 250 |
| Nifty Smallcap 50 Index | `NIFTYSMALLCAP50` | 50 |
| Nifty Smallcap 100 Index | `NIFTYSMALLCAP100` | 100 |
| Nifty Microcap 250 Index | `NIFTYMICROCAP250` | approximately 250 |
| Nifty LargeMidcap 250 Index | `NIFTYLARGEMIDCAP250` | 250 |
| Nifty MidSmallcap 400 Index | `NIFTYMIDSMALLCAP400` | 400 |
| Nifty MidSmallcap400 50:50 | `NIFTYMIDSMALLCAP4005050` | 400 |
| Nifty India FPI 150 | `NIFTYINDIAFPI150` | 150 |
| India Vix Index | `INDIAVIX` | 1 index instrument |

Some NSE CSVs contain more rows than the marketing name suggests because of the site's current constituent file format. The application uses every valid symbol returned by the CSV.

## OCC scanner algorithm

The scanner uses daily OHLCV candles from Kite. It processes each symbol in the selected watchlist and uses the latest available historical candle as the current data point.

### Data flow for one scan

1. Read the saved scanner configuration from SQLite.
2. Load the selected official NSE constituent CSV.
3. Download the full NSE instrument list from Kite.
4. Map each constituent's `tradingsymbol` to its Kite `instrument_token`.
5. Request daily historical candles for each mapped token.
6. Calculate the selected moving average on `close`.
7. Calculate the selected moving average on `open`.
8. Apply the configured delay by shifting both MA series by the selected number of bars.
9. Detect bullish and bearish flips.
10. Apply the optional ADX filter to BUY events.
11. Use the latest event for each symbol.
12. Calculate risk levels and market metrics.
13. Sort results by trigger date, newest first.
14. Save the run and every result to SQLite.
15. Return the result list to the frontend.

The scanner uses a data range longer than the visible lookback so rolling indicators have warm-up candles. The range is:

```text
today - (max(lookback_days, adx_lookback) + length * 4 + 30 days)
```

### BUY and EXIT rules

For each candle, the scanner creates:

```text
close_ma = MA(close, length)
open_ma  = MA(open, length)
```

If `delay > 0`, both series are shifted by that many bars before crossover evaluation:

```text
close_ma = close_ma.shift(delay)
open_ma  = open_ma.shift(delay)
```

A BUY event occurs when the close MA changes from at-or-below the open MA to above it:

```text
previous_close_ma <= previous_open_ma
and current_close_ma > current_open_ma
```

An EXIT event occurs when the close MA changes from at-or-above the open MA to below it:

```text
previous_close_ma >= previous_open_ma
and current_close_ma < current_open_ma
```

There is no short-side signal. EXIT is a flattening event for a long-only CNC workflow.

The scanner stores the latest BUY or EXIT event found for each symbol. A symbol without an event is omitted from the result list.

### Moving-average formulas

The available MA types are selectable in Settings.

#### SMA

```text
SMA[i] = (src[i] + src[i-1] + ... + src[i-length+1]) / length
```

#### EMA

The implementation uses pandas `ewm` with `adjust=False`, span equal to `length`, and `min_periods=length`:

```text
EMA = ewm(src, span=length, adjust=False)
```

#### DEMA

```text
first = EMA(src, length)
second = EMA(first, length)
DEMA = 2 * first - second
```

#### TEMA

```text
first = EMA(src, length)
second = EMA(first, length)
third = EMA(second, length)
TEMA = 3 * (first - second) + third
```

#### WMA

The latest value receives the largest linear weight:

```text
weights = [1, 2, ..., length]
WMA = dot(window, weights) / sum(weights)
```

#### VWMA

```text
VWMA = rolling_sum(close * volume, length) / rolling_sum(volume, length)
```

VWMA requires volume to be present in the Kite candle response.

#### SMMA

The first defined value is seeded with an SMA. Every later value uses the recursive smoothed formula:

```text
SMMA[length - 1] = SMA(src[0:length])
SMMA[i] = (SMMA[i-1] * (length - 1) + src[i]) / length
```

This is the default MA type and is the manual implementation used by `backend/scanner.py`.

#### HullMA

```text
half_length = floor(length / 2)
sqrt_length = round(sqrt(length))
raw = 2 * WMA(src, half_length) - WMA(src, length)
HullMA = WMA(raw, sqrt_length)
```

The implementation clamps intermediate lengths to at least one.

#### LSMA

LSMA uses a rolling linear regression. For each window, it calculates the least-squares slope against x values `0` through `length - 1`, then evaluates the regression at the final position plus the configured offset/sigma value:

```text
slope = covariance(x, values) / variance(x)
LSMA = mean(values) + slope * ((length - 1) + offset - mean(x))
```

#### ALMA

ALMA creates Gaussian weights across the rolling window:

```text
center = alma_offset * (length - 1)
scale = length / sigma
weight[j] = exp(-((j - center)^2) / (2 * scale^2))
weight = weight / sum(weight)
ALMA = dot(window, weight)
```

The UI default ALMA offset is `0.85`. The Settings `Offset / sigma` value is used as sigma, clamped to at least one during calculation.

#### SSMA

SSMA uses the two-pole super-smoother coefficients:

```text
a1 = exp(-1.414 * pi / length)
b1 = 2 * a1 * cos(1.414 * pi / length)
c2 = b1
c3 = -a1^2
c1 = 1 - c2 - c3
```

The recursive values are:

```text
SSMA[0] = src[0]
SSMA[1] = c1 * (src[1] + src[0]) / 2 + c2 * SSMA[0]
SSMA[i] = c1 * (src[i] + src[i-1]) / 2
          + c2 * SSMA[i-1]
          + c3 * SSMA[i-2]
```

#### TMA

```text
first_length = ceil(length / 2)
second_length = floor(length / 2) + 1
TMA = SMA(SMA(src, first_length), second_length)
```

## ADX filter

ADX is calculated from daily high, low, and close values with a default length of 14.

1. True range is the maximum of:
   - `high - low`
   - `abs(high - previous_close)`
   - `abs(low - previous_close)`
2. Positive and negative directional movement are calculated from high/low changes.
3. ATR, directional indicators, DX, and ADX use Wilder-style exponential smoothing with `alpha = 1 / length`.
4. When enabled, only BUY events with ADX above the threshold are retained.

The configuration supports:

- Manual threshold, default `20`.
- Auto threshold, using the rolling median ADX over `adx_lookback`, default `200` bars.

## Risk-management algorithm

Risk levels are informational only. They do not create or modify Kite orders.

Let `entry_price` be the close price on the latest signal event.

### Fixed stop loss

```text
fixed_stop = entry_price * (1 - sl_pct / 100)
```

Default: `sl_pct = 2.0`.

### TSL activation trigger

```text
activation_price = entry_price * (1 + tsl_activation_pct / 100)
```

Default: `tsl_activation_pct = 2.0`.

### Delayed trailing stop

The trail begins at the fixed stop. It remains inactive until a later candle high reaches the activation price.

```text
trail = fixed_stop
active = false

for each high after the signal:
    if high >= activation_price:
        active = true
    if active:
        candidate = high * (1 - tsl_pct / 100)
        trail = max(trail, candidate)
```

Default: `tsl_pct = 1.5`.

The displayed TSL value is `Not active` until activation. Once active, the trail can only ratchet upward.

### Take profit

```text
take_profit = entry_price * (1 + tp_pct / 100)
```

Default: `tp_pct = 5.0`.

## Results table metrics

The Signals table includes:

- **Symbol**: Kite/NSE trading symbol. This column is fixed during horizontal scrolling.
- **Signal**: `BUY` or `EXIT`.
- **Trigger date**: Date of the latest detected event.
- **Trigger price**: Close price on the event candle.
- **Current price**: Close price on the latest available candle.
- **Stop loss**: Fixed percentage stop from the trigger price.
- **TSL**: Current trailing stop, or `Not active` while waiting for activation.
- **Take profit**: Fixed percentage target from the trigger price.
- **% from entry**: `(current_price / trigger_price - 1) * 100`.
- **Avg volume (30d)**: Mean daily volume over the last 30 loaded candles.
- **P/E**: Best-effort `pdSymbolPe` value from NSE's equity quote API. Displays `N/A` if unavailable.
- **1D change %**: Latest close versus the previous loaded daily close.
- **1M change %**: Latest close versus the close approximately 22 trading candles earlier.
- **1Y change %**: Latest close versus the close approximately 253 trading candles earlier. Displays `N/A` if insufficient history exists.

The scanner saves these fields in the signal payload, so they are also returned by the results and history APIs.

## Sorting, moving columns, and CSV export

### Sorting

Click a table header to sort ascending. Click it again to sort descending. Numeric fields are compared numerically; text and dates are compared as strings.

### Moving columns

Drag a table header onto another header. The Symbol column is intentionally fixed and cannot be moved. All other result columns can be reordered.

The browser stores the order in:

```text
localStorage key: occ-signal-columns
```

To reset the order manually in the browser console:

```javascript
localStorage.removeItem('occ-signal-columns')
```

Refresh the page after removing the key.

### CSV

The Download CSV button creates a browser download named similar to:

```text
occ-signals-2026-09-03.csv
```

The CSV includes Symbol first and then the remaining columns in the user's saved order.

## Persistence and database

The scanner creates `scanner.sqlite3` in the private runtime directory (`$HOME/.local/share/shreyadesk` by default).

### `scanner_config`

Stores one active configuration record:

- Selected index name
- MA type and parameters
- Delay
- ADX settings
- Risk percentages
- Session-filter setting
- Lookback days
- Current watchlist symbols

When the index changes, the official CSV is fetched and the saved watchlist is replaced.

### `scanner_runs`

Stores each manual or scheduled run:

- Run ID
- Run timestamp
- Configuration snapshot and result count

### `scanner_signals`

Stores every signal result generated by a run:

- Symbol
- Signal type
- Trigger date
- Complete JSON result payload
- Run ID

Delete `scanner.sqlite3` from the private runtime directory only when you intentionally want to reset scanner settings and history. The next configuration request will recreate defaults.

## API reference

All `/api` routes are used by the Vite frontend. The same scanner routes are also registered without `/api` for compatibility with the feature specification.

### Health

```http
GET /api/health
```

Response:

```json
{"status":"ok"}
```

### Session status

```http
GET /api/session
```

Returns whether `.kite_token.json` contains a saved session. It does not return the token.

### Login

```http
POST /api/login
Content-Type: application/json
```

Request:

```json
{
  "api_key": "your-api-key",
  "api_secret": "your-api-secret",
  "request_token": "request-token-from-kite"
}
```

Success response:

```json
{"connected":true}
```

### Profile

```http
GET /api/profile
```

Requires a saved Kite session. Returns Zerodha profile data, including `user_name`, `user_id`, `products`, `exchanges`, and possibly `avatar_url`.

### Legacy SMA scanner

```http
POST /api/signals
```

Request:

```json
{
  "short_sma": 6,
  "long_sma": 30,
  "lookback_days": 365,
  "max_stocks": 25
}
```

This route remains available for the original Nifty 100 SMA crossover scanner. The OCC Signals page uses the newer `/api/scanner/*` routes.

### Get scanner config

```http
GET /api/scanner/config
GET /scanner/config
```

Returns the persisted configuration. The default configuration is:

```json
{
  "index_name": "NIFTY100",
  "ma_type": "SMMA",
  "length": 5,
  "offset_sigma": 6,
  "alma_offset": 0.85,
  "delay": 0,
  "adx_enabled": false,
  "adx_threshold": 20.0,
  "adx_auto": false,
  "adx_lookback": 200,
  "sl_pct": 2.0,
  "tsl_activation_pct": 2.0,
  "tsl_pct": 1.5,
  "tp_pct": 5.0,
  "session_filter": true,
  "lookback_days": 365,
  "watchlist": []
}
```

An empty watchlist is replaced with the selected index's official constituents when saved.

### Save scanner config

```http
POST /api/scanner/config
POST /scanner/config
Content-Type: application/json
```

The request accepts the same fields returned by the GET route. `index_name` must be one of the supported internal names. `ma_type` must be one of the 12 supported MA names.

Example:

```json
{
  "index_name": "NIFTYMIDCAP150",
  "ma_type": "SMMA",
  "length": 5,
  "offset_sigma": 6,
  "alma_offset": 0.85,
  "delay": 0,
  "adx_enabled": false,
  "adx_threshold": 20,
  "adx_auto": false,
  "adx_lookback": 200,
  "sl_pct": 2,
  "tsl_activation_pct": 2,
  "tsl_pct": 1.5,
  "tp_pct": 5,
  "session_filter": true,
  "lookback_days": 365,
  "watchlist": []
}
```

### Run scanner

```http
POST /api/scanner/run
POST /scanner/run
```

Requires a valid saved Kite session. The request has no body because the backend uses the persisted configuration.

The response contains:

- `run_id`
- `ran_at`
- `scanned`
- `returned`
- `results`

### Latest results

```http
GET /api/scanner/results
GET /scanner/results
```

Returns the most recent saved result set and the current scanner configuration.

### Symbol history

```http
GET /api/scanner/results/{symbol}
GET /scanner/results/{symbol}
```

Returns all saved events for the requested symbol, newest first.

### Logout

```http
POST /api/logout
```

Deletes `.kite_token.json` locally and returns `{ "connected": false }`.

## Scheduler

`backend/scanner.py` creates an APScheduler background scheduler using the `Asia/Kolkata` timezone. It registers:

```text
Monday-Friday at 15:20 IST
```

The scheduled job calls the same `run_scan()` function as the manual API endpoint. It uses the saved configuration and saved Kite session, then stores a new run and its results in SQLite.

The scheduler requires the FastAPI process to remain running. It is intended for local development or a small single-user deployment. For production, run one backend worker so the job is not duplicated across multiple worker processes.

## Auto-Trade safety and implementation status

The Auto-Trade section is designed as a paper-first execution workflow. Paper Trading and Live Trading have separate sections. Paper Trading is disabled by default and has an always-available pause control. Live Trading requires the exact typed confirmation and successful Kite profile, margin, and market-window checks before it becomes active.

### Confirmed choices

- Live order type: LIMIT only, without chasing; see the cumulative safety controls above.
- Paper fill: the current paper consumer fills at the scanner signal trigger price. This is deterministic for pipeline testing.
- Backtest fill: the current endpoint is a safe orchestration boundary and does not place orders. Full historical walk-forward simulation should be completed after the original Pine source and historical test fixtures are available.
- Storage: Auto-Trade tables are namespaced in the existing `scanner.sqlite3` database.
- Scanner hook: completed scanner results are passed to `process_signals()` after scanner persistence.
- Live monitoring: the live order path remains disabled. Intraday five-minute monitoring should be enabled only after Kite permissions, market-hours behavior, and reconciliation are tested.

### Paper-mode behavior

When Auto-Trade is enabled in Paper mode, a BUY result can create one simulated OPEN position if:

1. Auto-Trade is enabled and not paused.
2. The symbol has no existing OPEN position in the current mode.
3. The concurrent-position limit is not reached.
4. The deployed-capital limit is not reached.
5. The calculated quantity is at least one share.

Quantity is calculated as:

```text
floor((available_paper_cash * per_trade_pct / 100) / trigger_price)
```

The simulated order and position are both written to the Auto-Trade order log and positions table. Scanner EXIT results close matching paper positions when the configured exit rule honors scanner exits.

### Live-mode status

The UI and backend independently require the exact `ENABLE LIVE TRADING` confirmation phrase. Live orders are regular NSE/CNC LIMIT orders, accepted only during a verified NSE session, stopping five minutes before its close after the Kite session, profile, equity-trading status, and available margin have been checked. Each request is saved as `PENDING` before submission, then reconciled against Kite's order history before a position is opened or closed.

### Auto-Trade endpoints

```text
GET  /api/autotrade/config
POST /api/autotrade/config
POST /api/autotrade/enable
POST /api/autotrade/pause
GET  /api/autotrade/positions
POST /api/autotrade/positions/{id}/close
GET  /api/autotrade/orders?limit=100
GET  /api/autotrade/stats
POST /api/autotrade/backtest/run
```

Auto-Trade database tables:

- `autotrade_config`: singleton mode, sizing, limits, risk, exit rule, enable state, and paper cash.
- `autotrade_positions`: paper/live position lifecycle and realized P&L.
- `autotrade_orders`: every order attempt, including skipped, filled, and error states.
- `autotrade_backtest_runs`: reserved run summary/equity-curve storage.
- `autotrade_backtest_trades`: reserved per-trade backtest storage.

Live execution calls Kite's `place_order()` only after the safety checks above. Pending orders are reconciled on startup and during the 30-second live monitor; that monitor uses Kite LTP data to submit risk exits when an open position reaches its saved stop loss or take-profit level. Monitor the Kite order log while live mode is active.

## Data-source behavior and limitations

- Historical data comes from the already configured Kite Connect account; no paid vendor was added.
- Index membership comes from official NSE Indices constituent CSV files.
- P/E first tries the NSE quote endpoint. If NSE blocks the request or omits its P/E field, the backend fetches the latest annual EPS from the fundamentals fallback and calculates `P/E = current_price / EPS`. Negative, zero, missing, or unavailable EPS displays as `N/A`.
- Scanner performance: historical candle requests use bounded parallel workers with isolated Kite clients. Technical results are returned before slow fundamentals pages finish; P/E values are enriched asynchronously and saved into SQLite for later refreshes.
- Daily candle timestamps from Kite can be timezone-aware. The indicator code normalizes candle timestamps to UTC before date comparisons.
- The session-filter setting is persisted. The primary scanner timeframe is daily, and Kite's daily equity candles are already session-based. Intraday session-window enforcement is not currently needed by the daily path.
- Alternate-resolution/MTF scanning is not implemented.
- The scanner itself does not place orders, manage positions, or track a portfolio. Auto-Trade is a separate consumer and currently supports paper positions only; live placement is disabled.
- The watchlist UI currently selects official index universes. The API can accept a custom symbol list, but there is not yet a dedicated add/remove-symbol editor in the Settings page.

## TradingView parity note

The source Pine Script referenced in the feature request was not present in this workspace. The scanner therefore implements the documented OCC formulas and long-only risk rules in `backend/scanner.py`, but exact TradingView parity has not been certified.

For a formal parity check:

1. Add the original `.pine` file to the project.
2. Compare its `variant()` implementation with `moving_average()` and the individual MA helpers.
3. Compare its delayed trailing-stop block with `risk_levels()`.
4. Use identical symbols, daily timeframe, date range, exchange session, and warm-up history.
5. Spot-check 5-10 symbols and record each BUY/EXIT bar date.
6. Add regression fixtures for any differences discovered.

Small differences in EMA seeding, rounding, ALMA sigma interpretation, LSMA offset, SSMA coefficients, or available warm-up history can move a crossover by one or more bars.

## Troubleshooting

### The browser is blank

Confirm both processes are running:

```powershell
Invoke-WebRequest http://127.0.0.1:5173/ -UseBasicParsing
Invoke-WebRequest http://127.0.0.1:8000/api/health -UseBasicParsing
```

The frontend should return HTML and the backend should return `{"status":"ok"}`.

### The app asks for login again

Check that `.kite_token.json` exists in the private runtime directory. The saved token may also have expired. Use a new request token when required.

### The scan returns no rows

Open the Signals tab and run the scan again. Check:

- The saved index and watchlist in Settings.
- The Kite session is still valid.
- The selected symbols exist in the Kite NSE instrument list.
- The selected MA period has enough historical warm-up data.
- ADX is not filtering all BUY events.
- NSE has not temporarily blocked the constituent or quote request.

### P/E displays `N/A`

The backend first tries NSE's `pdSymbolPe`. If that is unavailable, it uses the latest annual EPS from the fundamentals fallback and calculates `current price / EPS`. `N/A` means the symbol has no usable positive EPS, the fundamentals page is unavailable, or the request was rate-limited. Price and technical values are independent of this field.

### The frontend build fails

Run from the frontend directory:

```powershell
npm install
npm run build
```

### The backend cannot import

Run from the project root:

```powershell
python -m pip install -r backend/requirements.txt
python -c "from backend.main import app; print(app.title)"
```

## Validation commands

Backend compilation:

```powershell
python -m py_compile backend/main.py backend/scanner.py
```

Frontend production build:

```powershell
Set-Location frontend
npm run build
```

Official index loading smoke test:

```powershell
python -c "from backend.main import index_symbols; print(len(index_symbols('NIFTY50'))); print(len(index_symbols('NIFTY500')))"
```

## Git and sensitive files

The following remain outside the repository in `$HOME/.local/share/shreyadesk` by default:

- `shreyadesk.env`
- `.kite_token.json`
- `scanner.sqlite3` and `telegram.sqlite3`
- `backend/__pycache__/`
- `frontend/node_modules/`
- `frontend/dist/`

Never commit API secrets, request tokens, access tokens, or personal account data.

## Holdings view

The sidebar Holdings page separates live app-tracked open positions from personal
CNC delivery inventory (settled + T1 - used holdings, plus positive net CNC positions).
Paper positions are excluded. Shared-symbol app quantities are subtracted once;
app average cost uses recorded entries. Personal shared-symbol costs use the broker's
blended average and are explicitly estimates, not separately identified tax lots.
Ownership mismatches are displayed and never modify app positions. Partial fills or
external sales may require order reconciliation before attribution becomes accurate.

Both tables show average cost, fresh LTP, quantity, unrealized P&L before fees/taxes,
and the latest BUY/EXIT event for the strategy saved in Settings. Refresh reloads
inventory, strategy selection and signals. OCC reuses `scanner.prepare_frame`;
Supertrend reuses finalized 1D candles, saved ATR length/factor and the scanner
ADX candidate filter. Universe, age and fundamental result filters do not hide
holdings. These are indicator events, not filtered scanner eligibility. Missing quotes/signals show unavailable.
Authenticated GET `/api/holdings` and `/api/holdings/signal` are read-only, never run
scanner execution, and do not feed personal portfolio data into the loss monitor.

### OCC / TradingView comparison (APLAPOLLO investigation)

The live scanner calculates OCC directly on **1D** candles. `OCC.pine` defaults to
alternate resolution ON with multiplier 3, which calculates **3D** moving averages
on a 1D chart. Those are different strategies and their crossovers need not match.
The original Pine `lookahead_on` behavior is retained for historical comparison
only; it is not replicated in live order generation. Enable **Show ShreyaDesk 1D
comparison?** in the Pine script for chart-series values in the Data Window and
raw 1D crossover markers, alongside the unchanged alternate-resolution strategy.
Use standard 1D candles and matching SMMA(5), delay and ADX settings for comparison.
The comparison table shows the latest bar; use the Data Window for an older date.

Kite candles now retain their IST calendar date during indicator calculation.
Previously midnight IST became the preceding UTC date, misdating events and the
live same-day entry check. Saved historical scan rows are not rewritten; run a new
scan while trading is paused to replace the display with corrected dates.
A read-only APLAPOLLO candle check found a 4 September 2026 1D BUY crossover:
Open MA 2183.702993 / Close MA 2196.234970, following a 3 September bearish crossover.
The old scan displayed that BUY as 3 September. User-supplied 3D chart values for
4 September were 2082.1 / 2115.3; these are not the same input timeframe.
Latest crossover labels refer to historical events, not necessarily today's entry
or an open strategy position. TradingView strategy fill arrows can appear later
than the raw crossover and depend on simulated position state and exits.

### Alternate resolution — verified 3D comparison

Settings now includes **Use alternate resolution** and **Alternate multiplier 3**.
ON means base 1D ×3 = three NSE trading sessions, not three calendar days. The
boundary anchor (2 September 2026) and 181 overlapping boundaries were verified
against the supplied `NSE_DLY_APLAPOLLO, 3D.csv`. The shared NSE calendar
keeps holidays and missing symbol candles from shifting the grouping; older
pre-coverage chart dates retain observed NIFTY 50 history. Only ×3 is
currently supported; other multipliers are rejected until their alignment is verified.

MA and delay run on aggregated OHLCV; the resulting series map back to daily bars
with historical lookahead behavior. ADX and market metrics stay on daily candles.
These signals are marked `comparison_only` and never dispatched to auto-trading;
the execution consumer independently rejects them. They can repaint, including
within the developing final group, and must not be used as executable signals.
Disabling alternate resolution restores the original daily calculation.

The supplied CSV was exported on a 3D chart with ×3 enabled, so its indicator
columns represent 9D. Its OHLC columns are the 3D reference. Computing SMMA(5) from
those candles matches the new daily-mapped series on 4 September 2026:
Open 2082.132744, Close 2115.279265, no new BUY. This supersedes the earlier
statement that alternate resolution was unavailable. Holdings uses the same setting.

### Mobile login disclosure tests

At widths up to 720px, **Enter dashboard credentials** reveals the existing login
form and focuses the username. Desktop always shows the form. Disclosure survives
viewport resizing within the page and resets on a fresh load; nothing is stored.

Run `cd frontend`, `npx playwright install chromium`, then `npm run test:e2e`.
The Playwright server uses the real password/session routes with test credentials,
a fake broker, a temporary database and disabled background jobs. Tests cover
mobile tap/reveal, errors, authentication, dashboard access, desktop visibility,
breakpoint resizing and reload reset. Existing login navigation first reaches the
broker gate; the tests reload to recheck the fake saved broker session and reach
the dashboard. That existing behavior is not changed by the disclosure feature.
Use `PLAYWRIGHT_CHROMIUM_EXECUTABLE` to select an already installed Chromium.

### Universe and volume filters

Settings offers mutually exclusive **Index** and **Full NSE** modes. Index retains
the chosen index and its existing watchlist behavior. Full NSE ignores that
watchlist and intersects the cached Kite NSE instrument master with the official
NSE equity list, with additional segment and non-equity exclusions described below.
Switching back retains the index selection. Settings apply on the next scan without
a restart. Daily history preparation runs at 08:30 IST and is also guarded on scan.

**Exclude volume below/above** are independent, optional bounds in shares. Values
exactly at either bound pass. Blank is disabled; zero is a real bound. A minimum
above the maximum is rejected. **Today's volume > 30-day average volume** compares
the latest daily candle with the preceding 30 daily candles, excluding itself.
The relative rule rejects missing/invalid volume or fewer than 30 prior sessions.
Weekend/holiday scans use the latest available trading day; intraday volume is
partial. Bounds apply to that same latest daily volume, even in alternate mode.

Universe selection precedes candle loading; volume filtering precedes OCC indicator
calculation and existing result/fundamental filtering. Full OHLCV history is retained
for indicators. Result metadata includes `current_volume`, `previous_30d_avg_volume`
and `volume_date`; existing average-volume display semantics are unchanged.
Defaults preserve Index mode and leave both bounds and the relative rule disabled.

## Verified equity universe and daily history cache

Full NSE now intersects Kite's daily instrument master with NSE's official
`https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv`. EQ symbols map
exactly; official BE/BZ entries map to their exchange-series suffix. Segment and
exchange must both be NSE; index rows, `-SG` symbols and whole-word GOVT/TBILL/BOND/
TREASURY names are excluded as an additional check. On verification the official
list contained 2,570 listings and 2,542 matched Kite instruments: type EQ only,
segment NSE only, zero index rows and zero `-SG` rows. This is the official equity
master scope, not a claim to include every ETF or separate SME instrument.

`scanner_market_master` stores daily official/Kite lists. `scanner_daily_candles`
is keyed by symbol and IST date; `scanner_history_state` tracks fetch day, requested
history coverage and token. The 09:00 IST job prepares the corrected full
universe on verified session days, including NIFTY 50 benchmark history. The first scan also guards preparation
when the job was missed or the broker session was unavailable. Initial preparation
is necessarily slow (roughly 15 minutes minimum at the verified universe size).
Subsequent same-day scans reuse the completed-day cache. Changed lookback settings
or tokens invalidate relevant coverage. Successful downloads survive retries.
History requests are spaced at least 0.35 seconds apart across three worker threads;
cache preparation is serialized across processes with a file lock. Missing history
never silently falls back to the unfiltered master or old cache.

Full quote batches contain up to 500 symbols and start at least 1.05 seconds apart.
Quotes supply today's open/high/low, LTP as the developing close, and volume.
`ohlc.close` is yesterday's close and is deliberately not used as today's close.
Only quotes whose last trade and packet timestamp belong to today can add today's
candle; weekends/pre-open reuse finalized cached sessions. Quote overlays never
write into finalized history. All volume filters and OCC run locally on these
series. Alternate-resolution mode remains comparison-only and cannot place orders.
The cold-cache first scan may wait for preparation; a warm scan does not request
per-stock historical data. P/E filters still depend on their separate existing
fundamental-data fetches and can add latency.

Quote verification also identified JAYKAY-RE1-BE as a forthcoming rights entitlement
(in the official master, not an ordinary share). `-RE`/`-RE<n>` series are explicitly
excluded. Final verified equity count: 2,542; all were available in the six quote batches.

Full-universe warm-path verification on 2026-09-06 completed 2,542 symbols in
17.92 seconds with alternate-resolution OCC enabled, six real quote calls and
zero historical-data calls. Results were isolated and order dispatch was blocked;
optional external fundamentals enrichment was disabled for this benchmark.
The initial cache fill completed before this measurement. Quote field semantics
and batch sizing follow https://kite.trade/docs/connect/v3/market-quotes/.

## Analytics journal

The Analytics sidebar page provides Order History, Trade History and Order Logs,
with strictly separate live/paper views. `/api/analytics?mode=live|paper` requires
an authenticated dashboard session and reads app records only. Live Order History
requires a stored broker order ID; unknown submissions remain in Order Logs until
reconciled. Paper Order History contains simulated fills and is labelled accordingly.
Submission and actual average-fill prices are distinct. New broker reconciliation
also stores filled quantity, including partial orders. Existing unresolved partial
fills still require the existing manual reconciliation flow; Analytics does not
create or close positions from these fills.

Trade metrics use the filtered position set: gross realized P&L, optional unrealized
P&L, win rate, open/closed counts, average trade/win/loss, elapsed holding duration,
best/worst P&L in rupees and percent, and closed-trade peak-to-trough drawdown.
Open marks come from batched app-symbol LTP requests, never broker portfolio P&L.
Missing marks remain unavailable. Actual charges are not persisted: net P&L and
estimated tax/fee deductions are deliberately not represented as known values.
Date filtering is inclusive in IST, using entry date for positions and attempt date
for orders/logs. Tables sort, paginate in groups of 50, and export all filtered rows
to CSV with ISO IST timestamps; text fields are escaped against spreadsheet formulas.

`analytics_skips` records future scanner skips separately from order rows and BUY
reservations. This includes comparison-only signals, execution pause/latch skips,
unmatched exits and paper sizing/position restrictions. Existing recorded attempts
remain visible; previously unlogged skips cannot be reconstructed. New monitored
SL/TP exits record the precise source; old generic risk exits retain that description.
The current execution engine uses its existing monitor, not broker GTT orders.
Analytics links position IDs but has no order-submission, baseline-reset or risk-limit
mutation endpoints. It does not change the daily BUY counter.

### Invalid live quotes

A broker quote can report a zero open despite positive LTP/volume (observed on
21 symbols on 2026-09-07). The scanner validates each symbol independently:
missing quotes, invalid current timestamps and missing/non-numeric/non-finite/
negative fields (or zero OHLC prices) exclude that symbol from the current scan.
Zero volume is allowed. Valid numeric fields and OCC calculations are preserved.
No substitute open is manufactured and invalid fresh quotes do not produce stale
signals. `skipped_quotes` stores symbol/reason pairs in scan metadata; Signals
shows an expandable exclusion list, including after reload. If all available
symbols have unusable quotes, the scan fails clearly and keeps previous results.
Broker-wide/network errors and indicator errors are not silently swallowed.

## Confirmed 3D execution (additive mode)

Settings → Moving average logic → Use alternate resolution → **3D mode** offers:

- `comparison` (default): the original historical lookahead comparison, unchanged;
  signals cannot dispatch orders.
- `confirmed`: completed three-session blocks only, using the same verified
  session boundaries, aggregation, MA, delay and daily ADX implementation. A
  crossover is dated on the block's final session, not its first session.

Confirmed mode excludes today's quote candle entirely from OCC. Finalized daily
history becomes available at the next day's refresh; even after-hours quote
snapshots are not treated as final exchange candles. An event is actionable only
if it is on the latest completed block and its end date is the immediately
preceding completed exchange session. A current-session quote is required. Thus
weekends/holidays do not invent sessions, and old crossovers cannot replay later.
Missing sessions inside the calculation history exclude that symbol. Current
volume filters and market-price columns still use the latest quote/day, separately
from the completed-block indicator calculation.

Confirmed mode uses the same 12:00 / 15:00 IST schedule as Supertrend.
Broker connectivity and enabled execution remain required. Saving a mode is not an enable action.
Existing typed live enable, startup pause, margin/preflight, LIMIT-only orders,
symbol/position limits, BUY allowance and cumulative-risk checks all remain in
force. The execution dispatcher rechecks mode/strategy and session eligibility;
live orders recheck the strategy immediately before their existing preflight.

`autotrade_confirmed_events` uniquely keys symbol + live/paper mode + side + block
end. A live event is inserted in the same SQLite write transaction as the order
and BUY reservation. Concurrent scans/restarts therefore cannot submit the same
crossover twice. Unknown broker acceptance, rejected orders and completed orders
keep their event record; there is no automatic retry of an already-submitted
crossover. A preflight-blocked attempt consumes neither the crossover nor the BUY
allowance, so it can qualify later within its session. Paper positions and event
claims are committed together, without broker orders or live BUY reservations.

Historical comparison results are never fed into the confirmed dispatcher.
Switching modes requires a new calculation: ZYDUSWELL has no symbol-specific
exception. In read-only verification on 2026-09-07 it independently qualified as
BUY on the completed block ending 2026-09-04; this does not mean an order was sent.
The verified full-universe run processed 2,580 symbols in 28.66 seconds with order
dispatch blocked and isolated result storage. Regression checks cover byte-identical
comparison-frame JSON, exclusion of developing candles, next-session submission,
persistent/concurrent deduplication, uncertainty after broker timeout, unchanged
risk caps, paper isolation and mode persistence without enabling trading.

### OCC dates and fresh candidates

The Signals table reports the comparison crossover date or, in confirmed mode,
the last session of the completed 3D block. TradingView strategy entry arrows are
fills, normally on the next bar with `OG OCC.pine`'s default execution settings.
They are not interchangeable dates. The supplied ANGELONE 1D export crosses on
2 September 2026; its next-bar strategy entry is 3 September, while that 3D block
ends 4 September and is considered for confirmed execution on 7 September.
The 3D chart with alternate multiplier 3 plots a 9D indicator; use its OHLC to
compare underlying 3D candles, and the 1D export's series for the 1D × 3 scanner.

“Current-session candidates only” filters saved results to confirmed signals
whose quote-backed eligibility belongs to today's IST session. It does not mean
an order was submitted, remains unprocessed, or will pass risk checks. Run a
fresh scan to refresh the snapshot. Older crossovers remain available with the
filter off. Repeated scans update prices; they cannot create a crossover where
none exists. No OCC calculation or execution eligibility changes with this UI
filter. Regression tests use both supplied ANGELONE exports.

### Daily RSI scanner column and filters

`RSI (14 · 1D)` uses daily close changes with Wilder's smoothing: seed average
gains/losses from 14 changes (15 closes), then apply `(previous × 13 + current) / 14`.
It always uses the original daily candles, even in either 3D OCC mode. The latest
quote-backed daily close participates, so the value is provisional intraday;
`rsi_date` records the latest candle's session. It is current-session RSI, not RSI
at the historical OCC crossover. With no losses RSI is 100 (including a flat
series, matching the built-in RSI zero-loss branch); with losses but no gains it
is 0. Missing, invalid or insufficient history yields N/A rather than zero.

Settings → RSI filters offers independent, inclusive “Exclude RSI below” and
“Exclude RSI above” bounds in 0–100. Blank disables a bound; both blank preserve
the previous universe. Invalid or inverted ranges are rejected. Filtering uses
unrounded RSI before OCC results are saved/dispatched; only display is rounded
to two decimals. Like existing scanner filters, it applies to both BUY and EXIT
results, not to standalone position risk monitoring or manual closes. Missing
RSI excludes a symbol only when a bound is enabled. Saving applies next scan;
existing saved rows show N/A until regenerated. Sorting and CSV export include
RSI. RSI filters participate in the confirmed execution strategy fingerprint.

Formula reference: https://www.tradingview.com/support/solutions/43000502338-relative-strength-index-rsi/

## Broker GTT protection for live positions

Live SL/TP price monitoring is now broker-managed. `backend/gtt.py` places a
`two-leg` Kite OCO with two CNC LIMIT SELL legs after a BUY reaches a verified
terminal broker status. Quantity and average cost come exclusively from broker
fills. Existing `current_sl` and `current_tp` math is preserved; both trigger and
LIMIT prices are rounded to the instrument's verified exchange tick size.
There is no trailing adjustment and no market-order fallback.

**Saved settings were explicitly preserved for this rollout: SL 2%, TP 5%,
`scanner_exit`.** That rule does not request SL/TP GTTs. Selecting
`risk_levels_only` or `whichever_first` enables protection for app-owned open
positions on the next reconciliation. Existing positions retain their stored
SL/TP prices; changing settings does not silently rewrite historical fill levels.
The 10%/20% example in the request was not applied over the saved values.

- GTT intent is persisted before the broker call, and its ID and audit events are
  linked to the app position. Explicit broker rejections get at most three attempts.
  A timeout/malformed acceptance response is **UNKNOWN**, never blindly retried.
  Live Trading → Reconcile protection requires `RECONCILE GTT`; uncertain requests
  require the user-verified broker GTT ID and exact position/leg validation.
- Manual and scanner exits share a cancellation gate. Reserve the app SELL, fetch
  the linked GTT, delete if active, then verify the broker state again. If it
  triggered concurrently, reconcile its resulting SELL first; no competing SELL
  is sent while that order is unresolved. Recheck remaining app quantity before
  submission. These SELLs retain the BUY-only symbol-cap exemption.
- The 30-second job still reconciles orders and samples cumulative app-only P&L;
  it no longer decides SL/TP exits from LTP. GTT status reconciliation runs every
  three minutes, at startup and after Kite login, even when execution is paused.
  Known IDs missing from `get_gtts()` are fetched individually because inactive
  triggers disappear from that list after seven days. An order-history fetch that
  cannot establish a fill leaves an explicit unresolved state; it never guesses.
- An open partial BUY is immediately included in app inventory/P&L but remains
  marked as awaiting final fill status. GTT placement waits for COMPLETE or a
  terminal CANCELLED/REJECTED partial fill with a verified average fill price.
  A still-open BUY is not assumed final or protected. Partial SELLs reduce remaining
  quantity and record realized proceeds; the position closes only once all units
  have broker-confirmed fills. Cumulative fill observations are applied idempotently
  and retained for date-specific app-only P&L reconciliation.
- Broker GTTs remain independent of pause, logout and kill switches. Those actions
  do not delete the linked triggers. Changing to scanner-only exits cancels active
  app GTTs on reconciliation; trigger/cancellation races still block duplicate exits.
- Failed placement, missing/modified triggers, rejection, expiry, and unknown status
  are visible in Live Trading and Analytics. Holdings shows the app protection tag.
  Order History annotates the BUY; Trade History has a GTT timeline; Order Logs
  includes GTT failures. These events do not consume daily BUY allowance.
- Expiry is reported, including a warning within seven days. Automatic renewal is
  deliberately not enabled: an explicit protection renewal verifies the old GTT
  inactive before replacing it. No unverified or expired trigger is labelled active.

A GTT trigger is not a fill: Kite submits a LIMIT order, which may remain unfilled
or be rejected (including for missing sell authorization). Equity GTTs are valid
for one year; DDPI/POA or the applicable CDSL authorization is still required.
Source: https://kite.trade/docs/connect/v3/gtt/ and
https://support.zerodha.com/category/trading-and-markets/charts-and-orders/gtt/articles/why-was-my-sell-gtt-order-rejected

Verification uses isolated SQLite databases and fake brokers; it never places real
orders or clears a production risk baseline. Backend restarts keep trading paused.

## Dashboard session and Kite OAuth security

The dashboard password remains the master credential: a fresh authenticated
session shares the existing verified server-side Kite connection. This is not
per-device 2FA. Starting **Connect to Kite** or **Reconnect to Kite** deliberately
marks only that dashboard session as awaiting OAuth. Back, reload, expired OAuth,
or another session completing OAuth cannot clear that pending state. Its protected
API requests return 403 until its own valid callback completes; other dashboard
sessions continue using the shared connection. Background trading state is not
changed by this session restriction.

OAuth callbacks require a random, single-use state bound to the initiating
session, valid for ten minutes and transported through Kite's `redirect_params`.
Wrong-session, expired, replayed and superseded callbacks cannot save a token.
The legacy POST `/api/kite/login` also requires this state and CSRF protection.
A dashboard session revoked during token exchange cannot complete that exchange.
Kite documents redirect parameters at https://kite.trade/docs/connect/v3/user/.

`/api/session` verifies the token against Kite's profile endpoint rather than
trusting a token file. The frontend revalidates on initial mount, protected-page
navigation, return to a visible tab, bfcache `pageshow`, and OAuth return. Private
UI is hidden while validation is pending. API fetches and responses use no-store;
nginx must also send `Cache-Control: no-store, no-cache, must-revalidate` and
`Pragma: no-cache` for the HTML entry page and API responses.

**User profile → Session activity** shows active sessions, latest completed Kite
login, and the latest 50 access events. SQLite retains audit events for 90 days;
active sessions remain in memory and expire/revoke as before. Metadata contains
IP address, browser-reported user agent, timestamps and a non-secret reference,
never session cookies, OAuth state, API secrets or broker access tokens. nginx
overwrites forwarded client IP with its actual remote peer before proxying to
the loopback-only backend. Existing Disconnect/logout still removes the shared
broker token; it does not cancel broker GTTs.

Tests use temporary databases and a local simulated broker page. They cover two
independent browsers, unfinished OAuth followed by Back, subsequent valid OAuth,
shared access in the other browser, stale bfcache revalidation, audit metadata,
callback binding/replay/races, and no-cache responses. No real orders are sent.

### 52-week distance range and collapsible Settings

Settings → Fundamental filters provides independent optional minimum and maximum
52W-high distance percentages (0–100, inclusive). Distance is the existing
`week_52_high_distance_pct` metric; both bounds apply to scanner results. Leave a
bound blank to disable it. Minimum cannot exceed maximum. Missing distance is
excluded when either bound is enabled. A disabled minimum preserves the existing
confirmed-signal strategy identity. Save settings, then run the scanner to apply.

Each Settings subsection can be collapsed/expanded using its heading, including
with the keyboard. Sections initially collapsed; collapsing does not discard fields
or prevent their values from being saved. Theme and shared Save action remain.

### Overview metrics

Overview uses `/api/overview`, a read-only snapshot scoped to the configured
live/demo mode. Cumulative P&L is all-time realized P&L from app trades (including partial
exits) plus unrealized P&L of open app positions, valued with fresh Kite quotes
rather than scanner results or entry-price fallbacks. It is separate from the
kill-switch baseline and does not reset when the kill switch is cleared. The card
is green for profit, red for loss, and neutral for zero/unavailable values. Missing
quotes make the total unavailable. Personal holdings are excluded. Win rate is
profitable closed app trades divided by all closed app trades; no closed trades
means no rate. Open orders include pending/unresolved app execution records, not
broker GTT triggers. Recent orders use the same mode. All cards refresh together
every 30 seconds or on Refresh; failed refreshes clearly label the prior snapshot.

Tab visibility revalidation keeps the dashboard mounted: returning from another
tab or a minimized window preserves signal sorting, filters, and form state.
Private content is masked while checking, then restored on success. Failed
authentication or pending Kite OAuth still replaces it with the appropriate
login/connection screen; the security check itself is not removed.

## Multiple scanner strategies: OCC and Supertrend

Choose **Settings → Scanner strategy → OCC / Supertrend**, then **Save settings**.
Use the same **Signals** tab and **Run scan now** button. Saved runs, symbol
history and displayed results are selected by strategy; an OCC run finishing
while Supertrend is selected cannot replace the Supertrend results. Switching
back restores that strategy's saved results. A Supertrend parameter/universe
change requires a matching new scan before saved results are shown. Strategy
selection preserves the other strategy's settings and does not enable trading.

Supertrend implements the supplied “Supertrend Only Strategy” indicator layer
on **1D** broker candles, with **ATR length 10** and **factor 3.0** defaults.
ATR uses high–low for the first true-range observation and SMA-seeded Wilder
smoothing thereafter. The final upper/lower bands trail as specified by Pine;
direction +1 means downtrend and −1 means uptrend. Strict direction changes
produce bullish BUY and bearish EXIT markers. Equal closes do not cross a band.
OCC moving averages and alternate resolution remain OCC-only. Both strategies
share risk overlays, P/E and 52W-distance filters, RSI, volume, ADX and signal-age
settings. Supertrend RSI and volume use the latest completed daily session;
ADX filters BUY candidates only, using the existing OCC threshold calculation.
These filters affect scanner results, not the raw Supertrend chart or its math.
Scanner SL/TP/trailing levels are display overlays; actual broker protection stays
in Live Trading. Settings groups start collapsed. NSE session eligibility remains
mandatory for Supertrend execution (there is no session-bypass toggle).
Changing shared settings requires a new matching scan; pre-filter Supertrend runs
are invalidated so old unfiltered results cannot appear as current results.
The existing universe selection remains shared. Auxiliary RSI/volume fields use
completed daily candles in this mode.

No live-quote candle participates in Supertrend. Daily history is reused from
SQLite. Full-universe daily preparation covers the larger of OCC and Supertrend
history requirements, regardless of the selected strategy, so switching does not
repeat the full download. Increasing history parameters can still require a
one-time cache expansion. Each indicator retains its own calculation window; after 15:30 IST, a scan fetches each symbol's completed broker daily
candle at the historical-data rate limit. This extra finalization fetch is cached
for that day. Before that point, the previous finalized session is used. Identical
completed history and parameters reuse cached indicator output, so intraday price
changes cannot repaint the line or flips. A fresh quote is display/execution data
only. There is no new tick subscription or separate scanner page.

Click a Supertrend symbol, or enter a cached symbol under its chart, to see the
last 120 completed candles with a green/red Supertrend line, unsmoothed flip jumps,
▲/▼ markers and faint trend background. `/api/scanner/chart/{symbol}` returns
OHLC and per-bar `supertrend_value`, `direction`, `bullFlip`, `bearFlip`. It requires
authentication and Supertrend selection. The existing saved scanner rows provide
flip history; no external notification/webhook is sent.

Per the explicit request, confirmed Supertrend flips also support execution.
Only a flip on the latest finalized candle can qualify, during its next permitted
trading session with a current-session broker quote. BUY remains CNC long-only;
bearish flips never open shorts. Manual enable/typed confirmation, fresh LIMIT
pricing, caps, daily BUY reservations, loss/manual kill switches, margin checks,
partial-fill reconciliation and configured SL/TP/GTT protection stay in the common
order path. Indicator-only backtest SL/TP/toggle/date inputs are not introduced.
Supertrend uses durable `st:YYYY-MM-DD` crossover reservations to prevent duplicate
orders across scans, changed parameters and restarts; failed/uncertain submissions
retain their reservation as in confirmed OCC. Strategy saves and order dispatch
share the execution lock. Legacy OCC event identities and indicator math stay intact.

An automatic scanner exit only closes a position originating from that strategy's
saved run. Existing legacy positions belong to OCC. Switching does not convert,
liquidate or transfer them: their existing GTT/manual exit paths remain available.
Supertrend SELLs retain the symbol-value exemption and cancel their position's GTT
before submission through the existing close path.

Reference calculation: https://www.tradingview.com/support/solutions/43000634738-supertrend/
Verification includes a fixed numerical band/flip fixture, no intrabar changes,
post-close cache reuse, late-run strategy separation, existing OCC byte-for-byte
comparison regression, risk and duplicate-order checks, and mobile/desktop
strategy/chart tests. Comparing with TradingView requires matching OHLC history,
ATR parameters, and warm-up history; no matching Supertrend TradingView CSV was
provided, so chart-export parity is not claimed.

### Separate Demo and Live execution rules

Demo and Live Trading have independent saved SL/TP percentages and exit rules.
Legacy shared values are copied into both modes without changing protection.
Live Trading exposes Live stop loss %, Live take profit %, and Live exit rule:
GTT SL/TP plus scanner exit (whichever first), GTT only, or scanner only (no GTT).
Demo allocation/position limits and Live symbol/position/BUY/loss limits remain
mode-specific. Forms submit a settings_mode and only that mode's fields; backend
saves protect the other mode against stale or cross-mode fields. Saving settings
still pauses execution and never enables it. Only one execution mode is active
at a time. Scanner strategy selection remains shared. Existing position levels
are not rewritten by changing percentages; new fills use their own mode's rules.
Live fill and GTT reconciliation use Live rules even while Demo is selected.

### Apply saved Live SL/TP to existing positions

Live Trading includes **Apply to existing positions**, requiring the typed phrase
`APPLY LIVE SL TP`. Save Live percentages first. The action applies only to open
live app positions with a verified active two-leg GTT and no unresolved orders.
It computes each level from the recorded entry price and rounds to the verified
instrument tick size. Current price must remain between the new triggers.
The existing GTT ID is modified, then read back and verified before SQLite levels
are committed. Demo/manual holdings are untouched. Each position reports its own
success or failure, and trade timelines record the modification.
Pending modification requests persist across restart. Ambiguous writes are never
blindly retried; reconciliation verifies the broker trigger before committing new
levels, and competing app exits remain blocked while modification is unresolved.
Use protection recovery first for a position without an active GTT. This button
does not enable trading or submit a BUY. Testing uses fake brokers only.

## Search — independent stock research

The Search sidebar page reads the cached official NSE equity/instrument universe
for autocomplete and existing SQLite daily candles for its chart and technicals.
It never starts a scanner run, submits an order, changes the selected strategy,
or saves trading settings. Search preferences live only in `search_cache`.
Authenticated `/api/search/symbols`, `/api/search/{symbol}`, and
`GET/POST /api/search/weights` inherit session, pending-OAuth and CSRF checks.

The 27-field ratio grid uses the existing Screener fetch function's optional
research response, daily-cached independently from the scanner's unchanged P/E
return. Published fields show SUCCESS; absent values show UNAVAILABLE and fetch
failures show FAILED. Loading is displayed as PENDING. Not all requested ratios
are published in the provider's public top-ratio panel; absent ratios are not
invented or filled with zero. Face Value is labeled reference data. RSI(14),
MACD(12,26 with 9 signal for scoring), and average five-session volume are computed
locally. Current Price is explicitly the latest completed candle close, not LTP.

Charts offer 1M/6M/1Yr/3Yr/5Yr/10Yr/Max windows over available history; Search does
not backfill missing years. Independent OCC and Supertrend toggles share the SVG
rendering approach already used by the app. OCC calls the original prepare_frame
on a copied config: 1D or confirmed 3D, never historical lookahead; 3D values first
appear at completed block boundaries and are carried forward for display.
Supertrend calls the existing implementation with ATR10/factor3 on finalized daily
candles. No overlay result is sent to scanner execution or persisted as a signal.

Long/short-term research scores expose each component's raw value, weight and
points, plus available-data weight coverage. Defaults are editable research
heuristics, not recommendations. Each component is linearly scaled from its weak
endpoint (0) to strong endpoint (100), clamped; descending endpoints support PEG.
Missing metrics are excluded and remaining weights renormalized. No available
weight yields an unavailable score. Scores recompute cheaply from cached inputs
on each view and after saving Search-only weights; no broker scan is involved.

## Automatic scanner timetable (Asia/Kolkata / IST)

Verified NSE session days: shared daily candle cache prepares at **09:00**, then the selected
strategy scans at **12:00** (results only, no paper or live order processing)
and **15:00** (execution eligible). Manual Run scan remains execution eligible.
All existing activation, fresh-signal, market-session, duplicate and risk gates
still apply. Scheduled scans never enable trading. Old 09:20 and 15:20 scan
jobs are removed. GTT exits, manual closes and risk monitoring remain independent
of the scanner timetable and continue protecting existing positions.

Scheduled and manual runs share the single-worker scanner queue. A manual scan
does not inherit a noon run's non-executing mode. Delayed 15:00 jobs cannot gain
execution permission on another date, outside its 15:00 hour, or within the
last five minutes before the actual session close. Missed
scan triggers have a 60-second grace period; cache preparation has five minutes.
The server must be running and broker data access valid. The shared calendar
skips holidays; special sessions use their published hours. Missing quotes still
block execution.

## Telegram notifications (notification-only)

Set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in the backend environment. Start
or message the bot from the destination chat first. User → Telegram notifications
contains category toggles, credential-presence status, worker/queue counters,
recent delivery results and Send test notification. Neither credential is returned
to the browser; changing credentials requires an environment update/restart.
Scanner summaries default off; order, safety, connection, service, capacity and
paper-order alerts default on. No incoming commands or webhook are installed.

Trading hooks call a guarded `put_nowait` only. A bounded 512-event queue and
separate 64-event safety/connection queue feed a daemon delivery worker. No HTTP,
notification database writes, futures, joins or sleeps occur on the order/safety
path. Telegram uses 3-second connect / 5-second read timeouts in the worker only.
Failures and dropped/full-queue counts are visible; delivery is best-effort, not
a guaranteed durable alert service. Pending in-memory events can be lost on a
crash. Acknowledged order/GTT and daily-capacity events are deduplicated in a
separate `telegram.sqlite3`; repeated incident warnings are throttled 10 minutes.
Detailed request exceptions/URLs are never logged to the delivery panel; messages
redact credentials and limit human-readable reasons. Full debugging remains local.

Events: broker submission, confirmed fill, rejection/cancellation/uncertain or
blocked order; GTT SL/TP trigger with order ID; persisted manual/loss latch;
P&L history errors; backend restart/paused state; daily BUY allowance and occupied
position capacity. A separate 30-second health thread checks Kite profile even
without dashboard visits; token errors encountered in order reconciliation also
enqueue immediately. Health checks distinguish disconnected from unreachable.
Monitoring and GTT protection continue independently of Telegram preferences.

Verification used a temporary trading database and fake broker to produce demo
fill, manual-latch and token-expiry events. Clearly labeled TEST/SIMULATED messages
were acknowledged by Telegram; no production order or latch was changed. Tests
also block the HTTP worker and use rejected/broken-token responses to verify that
new events enqueue immediately without delaying trading.


### Private Telegram commands

`TELEGRAM_CHAT_ID` is the group destination for outgoing alerts. The separate
`TELEGRAM_COMMAND_USER_ID` preserves the original positive private user/chat ID.
Only messages from that exact user in that exact private bot chat are accepted;
group messages, forwarded messages, other users and stale updates cannot issue
commands. Keep the bot token secret and rotate an exposed token through BotFather.

- `/status`: reports execution enabled/paused/kill-switch flags.
- `/stopvm`: returns a single-use confirmation valid for 60 seconds. Send the
  exact `/stopvm confirm <code>` privately to confirm. The backend persists a
  trading pause, sends an acknowledgement, then requests Azure deallocation of
  the VM named by `AZURE_RESOURCE_GROUP` and `AZURE_VM_NAME` in the configured `AZURE_SUBSCRIPTION_ID`. It does not
  close holdings or cancel broker orders/GTTs. Scans and reconciliation stop
  with the VM; existing broker orders/GTTs remain at Kite.

The Azure CLI must be available and authenticated for the service user, with
permission to deallocate this VM. Acknowledgement means a request is starting,
not that Azure has completed deallocation; check Azure independently. Restart
the VM through Azure, since a stopped backend cannot receive a start command.

Commands run on a separate polling thread; their HTTP/Azure calls never run on
the trading or outgoing notification worker. Update IDs are claimed durably
before handling, old startup backlog is discarded, and confirmation codes are
invalidated by restart. Failed/ambiguous shutdown requests are not retried
automatically. Telegram notification toggles do not disable owner commands.
No webhook or second getUpdates consumer may compete with this poller.
Verification mocks Azure deallocation: no real VM stop is performed by tests.


### Recovering a missed end-of-session app P&L sample

Keeping Kite logged in during market hours does not guarantee the backend records
its post-close snapshot. If no final sample was saved, the next successful risk
check attempts recovery using the app fill ledger and broker daily candles fetched
after the missing date. Every required open-position close must be present; current
LTP, intraday-only samples and unrelated broker holdings are never substitutes.
Unresolved orders or ambiguous historical fills leave coverage blocked. Recovery
is logged as `cache_recovery` with per-position candle evidence. It neither resets
the baseline nor enables trading. A recovered loss-limit breach still latches the
kill switch; durable loss evidence survives a restart until explicit baseline reset.
Transient broker identity read warnings clear after identity verifies successfully;
account mismatch and OAuth-switch warnings still require their explicit resolution.
