"""Small, local design system: no remote fonts or assets."""

CSS = """
<style>
:root { --ink: #15243b; --muted: #57677c; --line: #dce3ed; --blue: #2457d6;
  --soft: #f3f6fb; --amber: #9b5700; --green: #166348; }
.stApp { background: #f7f9fc; }
.block-container { max-width: 1480px; padding: 2rem 2.75rem 4rem; }
header[data-testid="stHeader"] { background: transparent; }
h1,h2,h3 { color: var(--ink); letter-spacing: -.025em; }
h1 { font-size: 2.6rem !important; font-weight: 750 !important; padding-bottom: .1rem !important; }
h2 { font-size: 1.45rem !important; }
h3 { font-size: 1.1rem !important; }
.eyebrow { font-size: .75rem; font-weight: 700; color: var(--blue); letter-spacing: .13em; text-transform: uppercase; }
.subtitle { color: var(--muted); font-size: 1rem; margin: 0 0 1.4rem; }
.local-tag { display:inline-flex; gap:.45rem; align-items:center; border:1px solid var(--line); border-radius:24px; padding:.35rem .7rem; background:white; font-size:.78rem; color:var(--muted); }
.local-tag:before { content:''; width:6px; height:6px; background:var(--green); border-radius:50%; }
.context-strip { display:flex; flex-wrap:wrap; gap:.75rem 2rem; padding:1rem 1.25rem; border:1px solid var(--line); border-radius:12px; background:white; margin:1rem 0 1.5rem; }
.context-item { font-size:.9rem; color:var(--ink); }
.context-item span { color:var(--muted); margin-right:.45rem; }
.kpi { border:1px solid var(--line); background:white; border-radius:12px; padding:1.15rem 1.3rem; min-height:124px; }
.kpi-label { font-size:.82rem; color:var(--muted); margin-bottom:.65rem; }
.kpi-value { font-size:1.65rem; font-weight:700; color:var(--ink); font-variant-numeric:tabular-nums; letter-spacing:-.02em; }
.kpi-note { font-size:.75rem; color:var(--muted); margin-top:.3rem; }
.kpi-amber { background:#fff9ed; border-color:#eed6a4; }
.kpi-amber .kpi-value { color:var(--amber); }
.badge { display:inline-block; border-radius:5px; padding:.2rem .5rem; font-size:.8rem; font-weight:600; line-height:1.6; }
.badge-match { background:#e7f3ed; color:#166348; }
.badge-diff { background:#fff0cf; color:#8a4d00; }
.badge-missing { background:#e9eef8; color:#344e7e; }
.formula { padding:1rem 1.2rem; margin:.3rem 0 1rem; border:1px solid var(--line); border-left:3px solid var(--blue); border-radius:8px; background:white; color:var(--ink); font-size:1rem; font-variant-numeric:tabular-nums; overflow-wrap:anywhere; }
.evidence-text { background:#fffefa; border:1px solid #e3dcc8; border-left:3px solid #d8ad42; border-radius:6px; padding:1rem; white-space:pre-wrap; overflow-wrap:anywhere; font-family:inherit; font-size:.92rem; color:#283247; }
.step-card { background:white; border:1px solid var(--line); border-radius:12px; padding:1.3rem; min-height:140px; }
.step-number { color:var(--blue); font-size:.8rem; font-weight:700; margin-bottom:.5rem; }
.step-title { color:var(--ink); font-weight:650; margin-bottom:.35rem; }
.step-copy { color:var(--muted); font-size:.88rem; line-height:1.5; }
[data-testid="stVerticalBlockBorderWrapper"] { background:white; border-radius:12px; }
[data-testid="stFileUploader"] { background:white; }
[data-testid="stDataFrame"] { border-radius:10px; }
[data-testid="stButton"] button { min-height:2.7rem; font-weight:600; border-radius:7px; }
[data-testid="stButton"] button:focus-visible { outline:3px solid #9db8ff; outline-offset:2px; }
[data-testid="stButton"] button[kind="primary"] { background:var(--blue); border-color:var(--blue); }
.stTabs [data-baseweb="tab-list"] { gap:1.5rem; border-bottom:1px solid var(--line); }
.stTabs [data-baseweb="tab"] { color:var(--muted); padding-top:.8rem; padding-bottom:.8rem; }
.stTabs [aria-selected="true"] { color:var(--blue); font-weight:650; }
@media (max-width:800px) { .block-container { padding:1.3rem 1rem 3rem; } h1 { font-size:2rem !important; } .kpi-value { font-size:1.3rem; } }
@media (prefers-reduced-motion:reduce) { *,*:before,*:after { transition:none !important; animation:none !important; } }
</style>
"""
