# ShreyaDesk Local Project Brief

This is a local, single-user trading research dashboard for Indian equities using
Zerodha Kite Connect. It combines a React/Vite frontend with a Python/FastAPI
backend. The project is intended for private local use and controlled deployment.

## Key Features

- Zerodha Kite Connect login and broker profile display.
- Read-only market research and equity scanning.
- OCC, RSI, Supertrend, SMA, and confirmed 3D signal workflows.
- Search and market-data views with cached daily history.
- Holdings, positions, orders, GTTs, and account-ownership checks.
- Paper trading and guarded live LIMIT-order execution.
- Persistent risk controls, kill switch, cumulative loss monitoring, and order caps.
- NSE trading-session calendar used by scans, risk history, and execution checks.
- Telegram notification and command support when configured.
- Responsive dashboard UI with browser end-to-end tests.

## Project Structure

```text
backend/       FastAPI application, trading logic, risk controls, and tests
frontend/      React/Vite dashboard and Playwright tests
deploy/        systemd, nginx, and server setup files
audit/         Local audit notes and verification material
*.csv          Market-data exports used by research workflows
```

## Local Requirements

- Python 3.11 or newer
- Node.js and npm
- A Zerodha Kite Connect application only if broker integration is needed

## Local Setup

From the project root:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r backend/requirements.txt
cd frontend
npm install
cd ..
```

Create the local environment file from the template:

```bash
cp .env.example .env
```

Fill in only the values required for the local features being tested. Never
commit `.env`, broker tokens, Telegram tokens, password hashes, or databases.

## Run Locally

Start the backend from the project root:

```bash
.venv/bin/uvicorn backend.main:app --host 127.0.0.1 --port 8000 --reload
```

In another terminal, start the frontend:

```bash
cd frontend
npm run dev
```

Open:

```text
http://127.0.0.1:5173
```

The Vite development server proxies `/api` requests to the backend on port
`8000`. The backend allows the frontend origin configured by `FRONTEND_ORIGIN`.

## Verification

Run backend tests:

```bash
.venv/bin/python -m unittest discover -s backend/tests -v
```

Build the frontend:

```bash
cd frontend
npm run build
```

Run browser tests when the required local test services are available:

```bash
npm run test:e2e
```

## Safety Notes

- Live execution starts paused and must be explicitly enabled.
- Live orders are LIMIT orders and are subject to account, session, risk, and
  allocation checks.
- Do not delete risk history, reservations, position records, or audit records
  to bypass a control.
- Verify the connected broker account before reconciling or changing live data.
- Keep the backend and its secrets on a protected server; do not expose secrets
  through frontend environment variables.
- This project is not financial advice and should be tested in paper mode first.

## Typical Private Deployment

For a live demo, host the React frontend on Vercel and run the stateful FastAPI
backend on a protected VM or another server with persistent storage. Configure
the backend's `FRONTEND_ORIGIN` with the Vercel URL and proxy frontend `/api`
requests to the backend. Keep broker, Telegram, authentication, and database
configuration on the backend only.