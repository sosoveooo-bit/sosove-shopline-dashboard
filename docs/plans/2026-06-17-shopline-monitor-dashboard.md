# Shopline Monitor Dashboard Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a local full-stack dashboard for monitoring independent-store data, with a Python backend that can talk to Shopline API endpoints and a polished frontend for orders, revenue, products, inventory, and sync health.

**Architecture:** Use a zero-dependency Python HTTP server to serve both the API and the static dashboard. Keep Shopline integration behind a small adapter layer that can run in live mode when env vars are set, or fall back to deterministic sample data when they are not. The frontend will fetch aggregated metrics from the backend and render charts, tables, and connector status without any external JS libraries.

**Tech Stack:** Python standard library, vanilla HTML/CSS/JS, SVG charts, unittest.

---

### Task 1: Backend scaffold

**Files:**
- Create: `shopline_monitor/__init__.py`
- Create: `shopline_monitor/backend.py`
- Create: `shopline_monitor/server.py`

**Step 1: Write the core data model and adapter helpers**

Implement config loading from env vars, response normalization for Shopline-like payloads, sample data generation, and dashboard aggregation.

**Step 2: Run a quick import check**

Run: `python -c "from shopline_monitor.backend import build_dashboard_payload; print(build_dashboard_payload('7d')['kpis']['orders'])"`
Expected: prints a number without raising.

### Task 2: Frontend dashboard

**Files:**
- Create: `shopline_monitor/static/index.html`
- Create: `shopline_monitor/static/styles.css`
- Create: `shopline_monitor/static/app.js`

**Step 1: Build the dashboard layout**

Implement sidebar, KPI cards, line chart, channel table, product table, and connector panel.

**Step 2: Wire fetch calls**

Fetch `/api/metrics`, `/api/connector`, and `/api/sync`, then render the returned JSON into the page.

### Task 3: Tests

**Files:**
- Create: `shopline_monitor/tests/test_backend.py`

**Step 1: Write tests for normalization and aggregation**

Cover sample data fallback, Shopline payload extraction, and KPI calculations.

**Step 2: Run the test file**

Run: `python -m unittest discover shopline_monitor/tests`
Expected: all tests pass.

### Task 4: Run and verify

**Files:**
- Modify: `README.md`

**Step 1: Document the local run command**

Add a short usage note for starting the server and connecting Shopline env vars.

**Step 2: Start the server and verify in browser**

Run: `python -m shopline_monitor.server --port 8787`
Expected: the dashboard loads at `http://127.0.0.1:8787/`.
