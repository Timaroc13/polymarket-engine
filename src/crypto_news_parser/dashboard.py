"""Localhost KPI dashboard for the wallet-flow validation phase.

GET /dashboard serves DASHBOARD_HTML (self-contained: vanilla JS + Chart.js
CDN, dark theme, refreshes every 60s). GET /dashboard/data serves the JSON
assembled by build_dashboard_data(). Both are read-only and unauthenticated
by design — this is a localhost tool.
"""
from __future__ import annotations

from typing import Any

from .paper import get_paper_report
from .storage import (
    get_calibration_timeline,
    get_dashboard_stats,
    get_flow_calibration,
    get_recent_scans,
)

GATE1_N = 30
GATE1_LIFT = 0.05


def build_dashboard_data() -> dict[str, Any]:
    return {
        "calibration": get_flow_calibration(),
        "timeline": get_calibration_timeline(),
        "recent_scans": get_recent_scans(limit=50),
        "stats": get_dashboard_stats(),
        "paper": get_paper_report(),
        "gates": {"gate1_n": GATE1_N, "gate1_lift": GATE1_LIFT},
    }


DASHBOARD_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>polymarket-engine — wallet-flow KPIs</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
<style>
  :root { --bg:#0d1117; --card:#161b22; --border:#30363d; --fg:#e6edf3; --dim:#8b949e;
          --green:#3fb950; --red:#f85149; --amber:#d29922; --blue:#58a6ff; }
  * { box-sizing:border-box; margin:0; }
  body { background:var(--bg); color:var(--fg); font:14px/1.5 'Segoe UI',system-ui,sans-serif; padding:24px; }
  h1 { font-size:18px; margin-bottom:4px; }
  .sub { color:var(--dim); font-size:12px; margin-bottom:20px; }
  .cards { display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:12px; margin-bottom:20px; }
  .card { background:var(--card); border:1px solid var(--border); border-radius:8px; padding:14px 16px; }
  .card .label { color:var(--dim); font-size:11px; text-transform:uppercase; letter-spacing:.05em; }
  .card .value { font-size:26px; font-weight:600; margin-top:2px; }
  .card .hint { color:var(--dim); font-size:11px; margin-top:2px; }
  .good { color:var(--green); } .bad { color:var(--red); } .warn { color:var(--amber); }
  .progress { height:6px; background:var(--border); border-radius:3px; margin-top:8px; overflow:hidden; }
  .progress > div { height:100%; background:var(--blue); }
  .row { display:grid; grid-template-columns:2fr 1fr; gap:12px; margin-bottom:20px; }
  .panel { background:var(--card); border:1px solid var(--border); border-radius:8px; padding:16px; }
  .panel h2 { font-size:13px; color:var(--dim); margin-bottom:10px; text-transform:uppercase; letter-spacing:.05em; }
  table { width:100%; border-collapse:collapse; font-size:13px; }
  th { text-align:left; color:var(--dim); font-weight:500; padding:6px 8px; border-bottom:1px solid var(--border); }
  td { padding:6px 8px; border-bottom:1px solid var(--border); }
  .tier-HIGH { color:var(--red); font-weight:600; }
  .tier-MEDIUM { color:var(--amber); }
  .tier-LOW { color:var(--dim); }
  .empty { color:var(--dim); padding:18px; text-align:center; }
  @media (max-width:900px){ .row { grid-template-columns:1fr; } }
</style>
</head>
<body>
<h1>polymarket-engine · wallet-flow validation</h1>
<div class="sub" id="sub">loading…</div>
<div class="cards" id="cards"></div>
<div class="row">
  <div class="panel"><h2>Lift evolution (per resolution)</h2><canvas id="liftChart" height="110"></canvas><div class="empty" id="liftEmpty" style="display:none">No resolved markets yet — the line starts when the first scanned market resolves.</div></div>
  <div class="panel"><h2>Recent scan tiers</h2><canvas id="tierChart" height="220"></canvas></div>
</div>
<div class="row">
  <div class="panel"><h2>Paper trading equity (flat stake per signal)</h2><canvas id="equityChart" height="110"></canvas><div class="empty" id="equityEmpty" style="display:none">Paper trades appear as scanned markets resolve.</div></div>
  <div class="panel"><h2>Paper results by tier</h2><div id="paperTiers"></div></div>
</div>
<div class="panel"><h2>Latest scans</h2><div id="scans"></div></div>
<script>
let liftChart, tierChart, equityChart;
const fmt = (v, d=2) => v == null ? '–' : Number(v).toFixed(d);
const pct = v => v == null ? '–' : (v*100).toFixed(1) + '%';
const ts = v => v == null ? '–' : new Date(v*1000).toLocaleString();

function card(label, value, cls='', hint='', extra='') {
  return `<div class="card"><div class="label">${label}</div>` +
         `<div class="value ${cls}">${value}</div>` +
         (hint ? `<div class="hint">${hint}</div>` : '') + extra + `</div>`;
}

async function refresh() {
  let d;
  try {
    const r = await fetch('/dashboard/data');
    if (!r.ok) { document.getElementById('sub').textContent =
      'data unavailable (' + r.status + ') — is ENABLE_PERSISTENCE=1?'; return; }
    d = await r.json();
  } catch (e) { document.getElementById('sub').textContent = 'server unreachable'; return; }

  const high = d.calibration.tiers.HIGH, overall = d.calibration.overall, g = d.gates;
  const gatePct = Math.min(100, (high.n / g.gate1_n) * 100);
  const liftCls = high.lift == null ? '' : (high.lift >= g.gate1_lift ? 'good' : (high.lift < 0 ? 'bad' : 'warn'));
  document.getElementById('sub').textContent =
    `last scan: ${ts(d.stats.last_scan_at)} · ${d.stats.scans_total} scans logged · ` +
    `${d.stats.tracked_unresolved} markets awaiting resolution · auto-refreshes every 60s`;
  document.getElementById('cards').innerHTML =
    card('Gate 1 progress (HIGH)', `${high.n} / ${g.gate1_n}`, '', 'resolved HIGH-tier markets',
         `<div class="progress"><div style="width:${gatePct}%"></div></div>`) +
    card('HIGH lift', high.lift == null ? '–' : (high.lift>0?'+':'') + fmt(high.lift,3), liftCls,
         `gate: ≥ +${g.gate1_lift} · win ${pct(high.win_rate)} vs implied ${pct(high.avg_implied)}`) +
    card('Overall n / lift', `${overall.n} / ${overall.lift == null ? '–' : fmt(overall.lift,3)}`, '',
         'all tiers (LOW is the control group)') +
    card('Deployed capital', '$' + fmt(d.stats.deployed, 2), d.stats.deployed > 0 ? 'warn' : '',
         'should stay $0 during validation') +
    card('Resolved / tracked', `${d.stats.tracked_resolved} / ${d.stats.tracked_resolved + d.stats.tracked_unresolved}`,
         '', `excluded from math: ${d.calibration.excluded}`) +
    card('Paper PnL (HIGH)', d.paper.tiers.HIGH.pnl == null || d.paper.tiers.HIGH.trades === 0 ? '–' :
         (d.paper.tiers.HIGH.pnl>=0?'+$':'-$') + Math.abs(d.paper.tiers.HIGH.pnl).toFixed(0),
         d.paper.tiers.HIGH.trades === 0 ? '' : (d.paper.tiers.HIGH.pnl >= 0 ? 'good' : 'bad'),
         `$${d.paper.stake}/signal · fee ${(d.paper.fee*100).toFixed(0)}% · ROI ${pct(d.paper.tiers.HIGH.roi)} · max DD $${fmt(d.paper.max_drawdown,0)}`);

  // Lift evolution
  const tl = d.timeline;
  document.getElementById('liftEmpty').style.display = tl.length ? 'none' : 'block';
  const labels = tl.map(p => p.n), lift = tl.map(p => p.lift), liftHigh = tl.map(p => p.lift_high);
  const cfg = {
    type: 'line',
    data: { labels, datasets: [
      { label: 'lift (overall)', data: lift, borderColor: '#58a6ff', tension:.2, spanGaps:true },
      { label: 'lift (HIGH)', data: liftHigh, borderColor: '#f85149', tension:.2, spanGaps:true },
    ]},
    options: { plugins:{ legend:{ labels:{ color:'#8b949e' } } },
      scales: { x:{ title:{display:true,text:'resolved markets (n)',color:'#8b949e'}, ticks:{color:'#8b949e'}, grid:{color:'#21262d'} },
                y:{ ticks:{color:'#8b949e'}, grid:{color:'#21262d'} } } }
  };
  if (liftChart) { liftChart.data = cfg.data; liftChart.update(); } else { liftChart = new Chart(document.getElementById('liftChart'), cfg); }

  // Tier distribution of recent scans
  const counts = { HIGH:0, MEDIUM:0, LOW:0 };
  d.recent_scans.forEach(s => { if (s.risk_tier in counts) counts[s.risk_tier]++; });
  const tcfg = { type:'doughnut',
    data: { labels:Object.keys(counts), datasets:[{ data:Object.values(counts),
      backgroundColor:['#f85149','#d29922','#30363d'], borderColor:'#161b22' }] },
    options:{ plugins:{ legend:{ labels:{ color:'#8b949e' } } } } };
  if (tierChart) { tierChart.data = tcfg.data; tierChart.update(); } else { tierChart = new Chart(document.getElementById('tierChart'), tcfg); }

  // Paper equity curve + tier table
  const pc = d.paper.curve;
  document.getElementById('equityEmpty').style.display = pc.length ? 'none' : 'block';
  const ecfg = { type:'line',
    data: { labels: pc.map(p => p.n), datasets: [
      { label:'equity (overall)', data: pc.map(p => p.equity), borderColor:'#58a6ff', tension:.2 },
      { label:'equity (HIGH)', data: pc.map(p => p.equity_high), borderColor:'#3fb950', tension:.2 },
    ]},
    options:{ plugins:{ legend:{ labels:{ color:'#8b949e' } } },
      scales:{ x:{ title:{display:true,text:'paper trades',color:'#8b949e'}, ticks:{color:'#8b949e'}, grid:{color:'#21262d'} },
               y:{ ticks:{color:'#8b949e', callback:v=>'$'+v}, grid:{color:'#21262d'} } } } };
  if (equityChart) { equityChart.data = ecfg.data; equityChart.update(); } else { equityChart = new Chart(document.getElementById('equityChart'), ecfg); }

  const ptRows = ['HIGH','MEDIUM','LOW'].map(t => { const b = d.paper.tiers[t];
    return `<tr><td class="tier-${t}">${t}</td><td>${b.trades}</td><td>${pct(b.win_rate)}</td>` +
           `<td class="${b.pnl>0?'good':(b.pnl<0?'bad':'')}">$${fmt(b.pnl,0)}</td><td>${pct(b.roi)}</td></tr>`; }).join('');
  document.getElementById('paperTiers').innerHTML =
    `<table><tr><th>tier</th><th>trades</th><th>win</th><th>PnL</th><th>ROI</th></tr>${ptRows}</table>` +
    `<div class="hint" style="color:var(--dim);font-size:11px;margin-top:8px">flat $${d.paper.stake} per signal at scan-time price, ${(d.paper.fee*100).toFixed(0)}% fee on winnings — money lens only, gates still rule</div>`;

  // Scan table
  const rows = d.recent_scans.map(s =>
    `<tr><td>${ts(s.created_at)}</td><td>${(s.question||s.condition_id).slice(0,70)}</td>` +
    `<td class="tier-${s.risk_tier}">${s.risk_tier}</td><td>${s.signal_score}</td>` +
    `<td>${s.dominant_side ?? '–'}</td><td>$${fmt(s.dominant_side_usdc,0)}</td>` +
    `<td>${pct(s.p_market_at_scan)}</td></tr>`).join('');
  document.getElementById('scans').innerHTML = d.recent_scans.length
    ? `<table><tr><th>scanned</th><th>market</th><th>tier</th><th>score</th><th>side</th><th>new-wallet $</th><th>YES price</th></tr>${rows}</table>`
    : '<div class="empty">No scans logged yet — first one lands within the scan interval.</div>';
}
refresh();
setInterval(refresh, 60000);
</script>
</body>
</html>
"""
