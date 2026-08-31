"""Dashboard Publisher (M7) — a deterministic publish job, not an agent.

Section 2 of the design charter is explicit about this one: "refresh
dashboard, publish KPIs, generate the weekly email" is a scheduled
data-pull-and-render task, not judgment work. It becomes the last step of
the month-end *workflow*, not a tenth agent. Every number in the package
this module builds is either read straight out of the deterministic
services layer (statements.py, metrics.py) or passed through unchanged from
an already-completed agent report (FP&A, CFO) — this file makes no LLM
calls and no judgment calls of its own. If a number here is wrong, that's a
bug in this code or in statements.py/metrics.py, never a bad prompt.

Code-enforced gate, mirroring every other structural gate in this project
(Controller's materiality check, the orchestrator's Gate 1 re-check, Gate
2's human-approval check): this module refuses to build or publish a
package for a period that isn't closed. That is the literal M7 success bar
from Section 13 — "Full package generated only after both gates clear" —
enforced here in code, not left to caller discipline in the orchestrator.
`build_close_package` and `publish` both re-check `period_status` directly
against the database rather than trusting a boolean an orchestrator might
pass in, for the same reason Gate 1 re-checks Controller's verdict instead
of trusting Controller's own self-report.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from html import escape
from pathlib import Path

from services import statements as stmt
from services import metrics

AGENT_NAME = "dashboard_publisher"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _log(conn: sqlite3.Connection, action: str, tool_name: str, inputs: dict,
          outputs: dict, notes: str | None = None) -> None:
    conn.execute(
        """INSERT INTO audit_log
           (log_id, timestamp, agent, action, tool_name, inputs, outputs,
            related_entity_type, related_entity_id, notes)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (f"log_{uuid.uuid4().hex[:12]}", _now(), AGENT_NAME, action, tool_name,
         json.dumps(inputs), json.dumps(outputs), "period_status", None, notes),
    )
    conn.commit()


def build_close_package(conn: sqlite3.Connection, period: str, fpa_report: dict | None = None,
                          cfo_briefing: dict | None = None) -> dict:
    """Assemble the full month-end reporting package for a CLOSED period.

    Refuses outright — builds nothing, returns {"status": "refused", ...} —
    unless period_status.status == 'closed' for this exact period. fpa_report
    and cfo_briefing are optional: this is a deterministic step and has to
    degrade gracefully if either live agent didn't produce a report on a
    given run (MAX_TURNS exhausted, API error) — the financial package
    (statements, KPIs) is never held hostage by an advisory agent's output.
    """
    status_row = conn.execute(
        "SELECT status, closed_at, approved_by FROM period_status WHERE period = ?", (period,)
    ).fetchone()
    if status_row is None or status_row["status"] != "closed":
        found = status_row["status"] if status_row else "no period_status row at all"
        return {
            "status": "refused",
            "reason": (f"period {period} is not closed (found: {found}) — the close package is "
                       "only generated once Controller's readiness gate and the human final-"
                       "approval gate have both cleared."),
        }

    income_statement = stmt.income_statement(conn, period)
    balance_sheet = stmt.balance_sheet(conn, period)
    cash_flow = stmt.cash_flow_statement(conn, period)
    kpis = {
        "gross_margin_pct": metrics.gross_margin_pct(conn, period),
        "payroll_pct_of_revenue": metrics.payroll_pct_of_revenue(conn, period),
        "burn_rate": metrics.burn_rate(conn, period),
        "cash_runway": metrics.cash_runway_months(conn, period),
    }

    forecast_row = conn.execute(
        "SELECT * FROM forecast_assumptions WHERE status='active' ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    active_forecast = dict(forecast_row) if forecast_row else None

    audit_counts = conn.execute(
        "SELECT agent, COUNT(*) AS n FROM audit_log GROUP BY agent ORDER BY n DESC"
    ).fetchall()
    audit_summary = {r["agent"]: r["n"] for r in audit_counts}

    return {
        "status": "ok",
        "period": period,
        "generated_at": _now(),
        "close": {"status": status_row["status"], "closed_at": status_row["closed_at"],
                   "approved_by": status_row["approved_by"]},
        "income_statement": income_statement,
        "balance_sheet": balance_sheet,
        "cash_flow": cash_flow,
        "kpis": kpis,
        "active_forecast_assumption": active_forecast,
        "fpa_report": fpa_report,
        "cfo_briefing": cfo_briefing,
        "audit_log_summary": audit_summary,
    }


def _money(v) -> str:
    if v is None:
        return "—"
    sign = "-" if v < 0 else ""
    return f"{sign}${abs(v):,.2f}"


def _pct(v) -> str:
    return "—" if v is None else f"{v:.2f}%"


def _list_html(items: list[str] | None) -> str:
    if not items:
        return "<p class='muted'>None.</p>"
    return "<ul>" + "".join(f"<li>{escape(str(i))}</li>" for i in items) + "</ul>"


def render_html(package: dict) -> str:
    """Render the assembled package as a single self-contained HTML file.
    No external assets, no JS frameworks — this is a static report, not an
    app; the point is that anyone can open it with nothing else installed."""
    if package.get("status") != "ok":
        # Should never be called this way (publish() checks first), but
        # render something honest rather than crash if it ever is.
        return f"<html><body><h1>Package not available</h1><p>{escape(package.get('reason', ''))}</p></body></html>"

    p = package
    inc = p["income_statement"]
    bs = p["balance_sheet"]
    cf = p["cash_flow"]
    kpi = p["kpis"]
    fpa = p.get("fpa_report")
    cfo = p.get("cfo_briefing")
    forecast = p.get("active_forecast_assumption")

    is_rows = "".join(
        f"<tr><td>{escape(l['name'])}</td><td class='num'>{_money(l['amount'])}</td></tr>"
        for l in inc["revenue"]["lines"]
    ) or "<tr><td colspan='2' class='muted'>No revenue lines.</td></tr>"
    cogs_rows = "".join(
        f"<tr><td>{escape(l['name'])}</td><td class='num'>{_money(l['amount'])}</td></tr>"
        for l in inc["cogs"]["lines"]
    ) or "<tr><td colspan='2' class='muted'>No COGS lines.</td></tr>"
    opex_rows = "".join(
        f"<tr><td>{escape(l['name'])}</td><td class='num'>{_money(l['amount'])}</td></tr>"
        for l in inc["opex"]["lines"]
    ) or "<tr><td colspan='2' class='muted'>No opex lines.</td></tr>"

    cf_rows = "".join(
        f"<tr><td>{escape(r['source_type'])}</td><td class='num'>{_money(r['net'])}</td></tr>"
        for r in cf["by_source"]
    ) or "<tr><td colspan='2' class='muted'>No cash activity.</td></tr>"

    audit_rows = "".join(
        f"<tr><td>{escape(agent)}</td><td class='num'>{n}</td></tr>"
        for agent, n in p["audit_log_summary"].items()
    )

    forecast_html = (
        f"<p><strong>{forecast['monthly_growth_rate_pct']}%</strong> monthly growth, set by "
        f"<code>{escape(forecast['set_by_agent'])}</code> for {escape(forecast['period'])}.</p>"
        f"<p class='muted'>{escape(forecast['basis'])}</p>"
        if forecast else "<p class='muted'>No active forecast assumption on record.</p>"
    )

    if fpa:
        fpa_html = (
            f"<p>{escape(fpa.get('summary', ''))}</p>"
            f"<p><strong>Gross margin trend explained:</strong> {fpa.get('gross_margin_trend_explained')}"
            f" &nbsp;|&nbsp; <strong>Forecast status:</strong> {escape(str(fpa.get('forecast_status', '')))}</p>"
            f"<h4>Material variances</h4>{_list_html(fpa.get('material_variances'))}"
            f"<h4>Findings</h4>{_list_html(fpa.get('findings'))}"
        )
    else:
        fpa_html = "<p class='muted'>FP&A did not produce a report on this run.</p>"

    if cfo:
        cfo_html = (
            f"<p>{escape(cfo.get('summary', ''))}</p>"
            f"<h4>Facts</h4>{_list_html(cfo.get('facts'))}"
            f"<h4>Hypotheses</h4>{_list_html(cfo.get('hypotheses'))}"
            f"<h4>Recommendations</h4>{_list_html(cfo.get('recommendations'))}"
            f"<h4>Open questions</h4>{_list_html(cfo.get('open_questions'))}"
            f"<p class='muted'>Delegated to: {escape(', '.join(cfo.get('agents_delegated_to', [])) or 'none')}</p>"
        )
    else:
        cfo_html = "<p class='muted'>CFO did not produce a briefing on this run.</p>"

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>AURI Finance — Close Package — {escape(p['period'])}</title>
<style>
  body {{ font-family: -apple-system, Helvetica, Arial, sans-serif; max-width: 920px; margin: 0 auto;
          padding: 32px 24px 80px; color: #1a1f2b; background: #fafafa; }}
  h1 {{ font-size: 1.6rem; margin-bottom: 4px; }}
  h2 {{ font-size: 1.15rem; margin-top: 40px; border-bottom: 2px solid #1a1f2b; padding-bottom: 6px; }}
  h4 {{ font-size: 0.95rem; margin: 16px 0 4px; }}
  .muted {{ color: #6b7280; font-style: italic; }}
  .meta {{ color: #6b7280; margin-bottom: 24px; }}
  .badge {{ display: inline-block; background: #16a34a; color: white; padding: 2px 10px;
            border-radius: 999px; font-size: 0.8rem; font-weight: 600; }}
  .kpi-row {{ display: flex; flex-wrap: wrap; gap: 12px; margin: 16px 0; }}
  .kpi {{ background: white; border: 1px solid #e5e7eb; border-radius: 10px; padding: 14px 18px; min-width: 160px; }}
  .kpi .label {{ font-size: 0.78rem; color: #6b7280; text-transform: uppercase; letter-spacing: 0.04em; }}
  .kpi .value {{ font-size: 1.4rem; font-weight: 700; margin-top: 4px; }}
  table {{ width: 100%; border-collapse: collapse; margin-top: 8px; background: white; }}
  td, th {{ padding: 6px 10px; border-bottom: 1px solid #eee; text-align: left; font-size: 0.92rem; }}
  td.num, th.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  tr.total td {{ font-weight: 700; border-top: 2px solid #1a1f2b; }}
  code {{ background: #eef0f3; padding: 1px 5px; border-radius: 4px; font-size: 0.85em; }}
  ul {{ margin: 4px 0; padding-left: 20px; }}
  section {{ background: transparent; }}
</style>
</head>
<body>

<h1>AURI Finance — Month-End Close Package</h1>
<p class="meta">Period <strong>{escape(p['period'])}</strong> &nbsp;·&nbsp;
   <span class="badge">{escape(p['close']['status'].upper())}</span> &nbsp;·&nbsp;
   closed {escape(p['close']['closed_at'] or '—')} &nbsp;·&nbsp;
   approved by {escape(p['close']['approved_by'] or '—')} &nbsp;·&nbsp;
   generated {escape(p['generated_at'])}</p>

<div class="kpi-row">
  <div class="kpi"><div class="label">Gross margin</div><div class="value">{_pct(kpi['gross_margin_pct'])}</div></div>
  <div class="kpi"><div class="label">Payroll % of revenue</div><div class="value">{_pct(kpi['payroll_pct_of_revenue']['pct_of_revenue'])}</div></div>
  <div class="kpi"><div class="label">Net change in cash</div><div class="value">{_money(kpi['burn_rate']['net_change_in_cash'])}</div></div>
  <div class="kpi"><div class="label">Cash runway</div><div class="value">{kpi['cash_runway']['runway_months'] if kpi['cash_runway']['runway_months'] is not None else '∞'} mo</div></div>
</div>

<h2>Income Statement</h2>
<table>
  <tr><th>Revenue</th><th class="num">Amount</th></tr>
  {is_rows}
  <tr class="total"><td>Total revenue</td><td class="num">{_money(inc['revenue']['total'])}</td></tr>
  <tr><th>COGS</th><th class="num"></th></tr>
  {cogs_rows}
  <tr class="total"><td>Total COGS</td><td class="num">{_money(inc['cogs']['total'])}</td></tr>
  <tr class="total"><td>Gross profit ({_pct(inc['gross_margin_pct'])})</td><td class="num">{_money(inc['gross_profit'])}</td></tr>
  <tr><th>Operating expenses</th><th class="num"></th></tr>
  {opex_rows}
  <tr class="total"><td>Total opex</td><td class="num">{_money(inc['opex']['total'])}</td></tr>
  <tr class="total"><td>Operating income</td><td class="num">{_money(inc['operating_income'])}</td></tr>
</table>

<h2>Balance Sheet (as of {escape(bs['as_of_period'])}, all-time cumulative)</h2>
<table>
  <tr><td>Total assets</td><td class="num">{_money(bs['assets']['total'])}</td></tr>
  <tr><td>Total liabilities</td><td class="num">{_money(bs['liabilities']['total'])}</td></tr>
  <tr><td>Paid-in capital</td><td class="num">{_money(bs['equity']['paid_in_capital'])}</td></tr>
  <tr><td>Cumulative net income</td><td class="num">{_money(bs['equity']['cumulative_net_income'])}</td></tr>
  <tr class="total"><td>Total equity</td><td class="num">{_money(bs['equity']['total'])}</td></tr>
  <tr><td colspan="2">Balanced: <strong>{bs['balanced']}</strong></td></tr>
</table>

<h2>Cash Flow</h2>
<table>
  <tr><th>Source</th><th class="num">Net</th></tr>
  {cf_rows}
  <tr><td>Beginning cash</td><td class="num">{_money(cf['beginning_cash'])}</td></tr>
  <tr class="total"><td>Net change</td><td class="num">{_money(cf['net_change_in_cash'])}</td></tr>
  <tr class="total"><td>Ending cash</td><td class="num">{_money(cf['ending_cash'])}</td></tr>
</table>

<h2>Forecast</h2>
{forecast_html}

<h2>FP&amp;A Report</h2>
{fpa_html}

<h2>CFO Briefing</h2>
{cfo_html}

<h2>Audit Trail</h2>
<table>
  <tr><th>Agent</th><th class="num">Log entries</th></tr>
  {audit_rows}
</table>
<p class="muted">Full detail in the <code>audit_log</code> table — every tool call, input, output, and
timestamp behind every number above.</p>

</body>
</html>
"""


def publish(conn: sqlite3.Connection, period: str, output_dir: str, fpa_report: dict | None = None,
            cfo_briefing: dict | None = None) -> dict:
    """Build the package and write it to <output_dir>/<period>/ as both
    close_package.json (machine-readable) and dashboard.html (human-
    readable). Refuses — writes nothing — if the period isn't closed."""
    package = build_close_package(conn, period, fpa_report=fpa_report, cfo_briefing=cfo_briefing)
    if package.get("status") == "refused":
        _log(conn, "publish_refused", "publish_close_package", {"period": period}, package,
             notes=package["reason"])
        return package

    out_dir = Path(output_dir) / period
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "close_package.json"
    html_path = out_dir / "dashboard.html"
    json_path.write_text(json.dumps(package, indent=2))
    html_path.write_text(render_html(package))

    result = {"status": "published", "period": period,
              "json_path": str(json_path), "html_path": str(html_path)}
    _log(conn, "publish", "publish_close_package", {"period": period},
         {"json_path": str(json_path), "html_path": str(html_path)})
    return result
