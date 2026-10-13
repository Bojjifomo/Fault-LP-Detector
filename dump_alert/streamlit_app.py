"""
LP Guard web app: paste a Robinhood Chain token address → the coin is checked against reference coins right
before their crashes, against similar past moments in the sample coins, and on liquidity, supports and
technicals, then gets an OK / CAUTION / AVOID verdict for opening an LP you leave overnight, a recommended
range, charts, and a simulation of LP losses. Hyperliquid-style dark theme, injected as CSS in this file
so other Streamlit apps in the repo are not affected.

  streamlit run dump_alert/streamlit_app.py

Needs an Alchemy API key: ALCHEMY_API_KEYS (several keys, rotated) or ALCHEMY_API_KEY in secrets /
environment variables, or entered in the sidebar (several allowed, comma separated).
"""
import html
import math
import os
import sys
import time
import traceback

# streamlit runs this file directly; add the repo root so dump_alert & lp_analysis can be imported
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def _drop_stale_modules():
    """Streamlit Cloud pulls new commits into the running app and re-runs this file, but keeps the dump_alert
    modules it already imported, so new app code ends up calling old functions (TypeError: unexpected keyword
    argument). When a file changed after they were loaded, drop them so the imports below load them fresh."""
    pkg = sys.modules.get("dump_alert")
    if pkg is None:
        return
    dirs = [os.path.join(ROOT, d) for d in ("dump_alert", "lp_analysis")]
    files = [os.path.join(d, f) for d in dirs if os.path.isdir(d) for f in os.listdir(d)]
    if max((os.path.getmtime(f) for f in files if os.path.isfile(f)), default=0) > getattr(pkg, "_loaded_at", 0):
        for name in [m for m in sys.modules if m.split(".")[0] in ("dump_alert", "lp_analysis")]:
            del sys.modules[name]


_drop_stale_modules()
_import_t = time.time()

import altair as alt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from dump_alert import ranges as rg  # noqa: E402
from dump_alert.guide import GUIDE  # noqa: E402
from dump_alert.fetch import Fetcher, QuotaError, data_path, eth_usd, make_rpc, parse_keys  # noqa: E402
from dump_alert.levels import ZONE  # noqa: E402
from dump_alert.lp_check import (SIGNS, STEP, VERDICTS, assess, load_reference,  # noqa: E402
                                 lp_value, parse_mcap, parse_time, pct, truncate)
from dump_alert.patterns import STATES  # noqa: E402
from dump_alert.signals import Token  # noqa: E402
from dump_alert.study import useful  # noqa: E402

sys.modules["dump_alert"].__dict__.setdefault("_loaded_at", _import_t)

# Hyperliquid-like palette. Status colors pass the validator (CVD, contrast >= 3:1) on panel #0F1A1F;
# the *_TEXT versions are lighter for text on dark backgrounds.
BG, PANEL, PANEL2, BORDER = "#0B1519", "#0F1A1F", "#13232A", "#1D2E35"
TEXT, MUTED, ACCENT, MINT = "#F6FEFD", "#8D9B99", "#50D2C1", "#97FCE4"
STATUS = {"OK": "#1F9E78", "CAUTION": "#C2831F", "AVOID": "#D9576F"}
STATUS_TEXT = {0: "#50D2C1", 1: "#F2B84B", 2: "#ED7088"}
LEVEL_CLASS = {0: "good", 1: "warn", 2: "bad"}
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
SHORT = {"pump": "Up 20%+ in 4 hours", "lipat": "1.8x+ its 24h low", "pucuk": "Within 10% of its 24h high",
         "retest": "Back at its old high (retest)", "borong": "Big holders bought 0.9%+ (6h)",
         "jual": "Big holders sold 1.5%+ (6h)", "baru": "New big wallets 1.5%+ (2h)", "ath4j": "New high on the 4h chart"}

CSS = f"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500;600&display=swap');
:root {{ --bg:{BG}; --panel:{PANEL}; --panel2:{PANEL2}; --border:{BORDER}; --text:{TEXT}; --muted:{MUTED};
        --accent:{ACCENT}; --mint:{MINT}; --good:{STATUS['OK']}; --warn:{STATUS['CAUTION']};
        --bad:{STATUS['AVOID']}; --good-t:{STATUS_TEXT[0]}; --warn-t:{STATUS_TEXT[1]}; --bad-t:{STATUS_TEXT[2]};
        --mono:'JetBrains Mono', ui-monospace, monospace; }}
.stApp, [data-testid="stAppViewContainer"], [data-testid="stMain"] {{ background:var(--bg) !important; color:var(--text);
        font-family:Inter, system-ui, sans-serif; }}
[data-testid="stHeader"] {{ background:transparent !important; }}
[data-testid="stSidebar"] {{ background:var(--panel) !important; border-right:1px solid var(--border); }}
.block-container {{ max-width:960px; padding-top:2.2rem; }}
[data-testid="stMarkdownContainer"], [data-testid="stMarkdownContainer"] p, [data-testid="stMarkdownContainer"] li,
[data-testid="stCaptionContainer"] {{ color:var(--text); }}
[data-testid="stCaptionContainer"], [data-testid="stCaptionContainer"] p {{ color:var(--muted) !important; }}
[data-testid="stWidgetLabel"] p {{ color:var(--muted) !important; font-size:12px !important; letter-spacing:.03em;
        text-transform:uppercase; }}
[data-testid="stForm"] {{ background:var(--panel); border:1px solid var(--border) !important; border-radius:12px;
        padding:18px 18px 8px; }}
[data-baseweb="input"], [data-baseweb="base-input"], [data-baseweb="input"] > div {{ background:var(--panel2) !important;
        border-color:var(--border) !important; border-radius:8px !important; }}
[data-baseweb="input"] input {{ color:var(--text) !important; font-family:var(--mono); caret-color:var(--accent);
        -webkit-text-fill-color:var(--text) !important; }}
[data-baseweb="input"] input::placeholder {{ color:var(--muted) !important; -webkit-text-fill-color:var(--muted) !important; }}
[data-baseweb="input"]:focus-within {{ border-color:var(--accent) !important; }}
/* slider (Streamlit 1.5x+): track = anak pertama, thumb = anak kedua; isian track berupa gradien
   warna primer yang dihitung dinamis, jadi warnanya digeser merah→teal dengan hue-rotate */
[data-testid="stSlider"] [role="group"] > div > div:first-child {{ filter:hue-rotate(172deg) saturate(.75) brightness(1.05); }}
[data-testid="stSlider"] [role="group"] > div > div:nth-child(2) {{ background-color:var(--accent) !important;
        box-shadow:0 0 0 4px rgba(80,210,193,.18) !important; }}
[data-testid="stSliderThumbValue"] p, [data-testid="stSliderTickBar"] p {{ color:var(--accent) !important;
        font-family:var(--mono); }}
[data-testid="stTextInputRootElement"] {{ background-color:var(--panel2) !important; border:1px solid var(--border) !important;
        border-radius:8px !important; }}
[data-testid="stTextInputRootElement"]:focus-within {{ border-color:var(--accent) !important; }}
[data-testid="stTextInputField"] {{ color:var(--text) !important; -webkit-text-fill-color:var(--text) !important;
        font-family:var(--mono) !important; caret-color:var(--accent); }}
[data-testid="stTextInputField"]::placeholder {{ color:var(--muted) !important; -webkit-text-fill-color:var(--muted) !important; }}
button[kind="primaryFormSubmit"], [data-testid="stBaseButton-primaryFormSubmit"] {{ background:var(--accent) !important;
        color:#04211D !important; border:none !important; font-weight:600 !important; border-radius:8px !important; }}
button[kind="primaryFormSubmit"]:hover, [data-testid="stBaseButton-primaryFormSubmit"]:hover {{ background:var(--mint) !important; }}
button[kind="primaryFormSubmit"] p, [data-testid="stBaseButton-primaryFormSubmit"] p {{ color:#04211D !important; }}
[data-testid="stExpander"] details {{ background:var(--panel) !important; border:1px solid var(--border) !important;
        border-radius:10px !important; }}
[data-testid="stExpander"] summary, [data-testid="stExpander"] summary p {{ color:var(--text) !important; }}
[data-testid="stExpander"] summary {{ background:var(--panel) !important; border-radius:10px; }}
[data-testid="stExpander"] summary:hover {{ background:var(--panel2) !important; }}
[data-testid="stExpander"] summary:hover p {{ color:var(--accent) !important; }}
[data-testid="stAlert"] {{ background:var(--panel2) !important; border:1px solid var(--border) !important; color:var(--text) !important; }}
code {{ background:var(--panel2) !important; color:var(--mint) !important; border-radius:6px; padding:1px 6px; }}
a {{ color:var(--accent) !important; }}
.hl-head {{ display:flex; align-items:center; gap:10px; margin-bottom:4px; }}
.hl-logo {{ font-weight:700; font-size:13px; letter-spacing:.12em; color:var(--accent); text-transform:uppercase; }}
.hl-pill {{ display:inline-block; padding:2px 10px; border-radius:999px; background:var(--panel2);
        border:1px solid var(--border); font-size:12px; color:var(--muted); }}
.hl-title {{ font-size:30px; font-weight:700; margin:2px 0 4px; color:var(--text); letter-spacing:-.01em; }}
[data-testid="stMarkdownContainer"] p.hl-sub {{ color:var(--muted); font-size:14px; margin:0 0 18px; }}
.hl-card {{ background:var(--panel); border:1px solid var(--border); border-radius:12px; padding:16px 18px; margin:10px 0 14px; }}
.hl-verdict {{ border-left:4px solid var(--good); }}
.hl-verdict.good {{ border-left-color:var(--good); background:linear-gradient(90deg, rgba(31,158,120,.20), var(--panel) 65%); }}
.hl-verdict.warn {{ border-left-color:var(--warn); background:linear-gradient(90deg, rgba(194,131,31,.20), var(--panel) 65%); }}
.hl-verdict.bad {{ border-left-color:var(--bad); background:linear-gradient(90deg, rgba(217,87,111,.20), var(--panel) 65%); }}
.hl-verdict .t {{ font-size:22px; font-weight:700; margin:0 0 4px; }}
.hl-verdict.good .t {{ color:var(--good-t); }} .hl-verdict.warn .t {{ color:var(--warn-t); }} .hl-verdict.bad .t {{ color:var(--bad-t); }}
.hl-verdict .s {{ color:var(--text); font-size:14px; opacity:.9; }}
.hl-note {{ background:rgba(80,210,193,.07); border:1px solid rgba(80,210,193,.35); border-radius:10px; padding:10px 14px;
        color:var(--mint); font-size:14px; margin:0 0 14px; }}
.hl-tiles {{ display:grid; grid-template-columns:repeat(4, minmax(0, 1fr)); gap:10px; margin:6px 0 6px; }}
.hl-tiles.two {{ grid-template-columns:repeat(2, minmax(0, 1fr)); }}
@media (max-width:640px) {{ .hl-tiles {{ grid-template-columns:repeat(2, minmax(0, 1fr)); }} }}
.hl-tile {{ background:var(--panel); border:1px solid var(--border); border-radius:10px; padding:12px 14px; min-width:0; }}
.hl-tile .k {{ color:var(--muted); font-size:12px; letter-spacing:.02em; }}
.hl-tile .v {{ font-family:var(--mono); font-size:22px; font-weight:600; color:var(--text); margin-top:2px;
        white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }}
.hl-tile .v.big {{ font-size:30px; }}
.hl-tile .d {{ color:var(--muted); font-size:12px; margin-top:2px; font-family:var(--mono); }}
.pos {{ color:var(--good-t) !important; }} .neg {{ color:var(--bad-t) !important; }} .warn-t {{ color:var(--warn-t) !important; }}
.hl-meta {{ color:var(--muted); font-size:12px; font-family:var(--mono); margin:2px 0 8px; word-break:break-all; }}
.hl-h {{ display:flex; align-items:center; gap:8px; font-size:17px; font-weight:600; color:var(--text); margin:26px 0 8px; }}
.hl-h::before {{ content:""; width:3px; height:16px; background:var(--accent); border-radius:2px; }}
.hl-alert {{ border-radius:10px; padding:10px 14px; margin:4px 0 10px; font-size:14px; border:1px solid; }}
.hl-alert.good {{ background:rgba(31,158,120,.12); border-color:rgba(31,158,120,.5); color:var(--good-t); }}
.hl-alert.warn {{ background:rgba(194,131,31,.12); border-color:rgba(194,131,31,.5); color:var(--warn-t); }}
.hl-alert.bad {{ background:rgba(217,87,111,.12); border-color:rgba(217,87,111,.5); color:var(--bad-t); }}
.hl-alert b {{ color:var(--text); }}
.hl-scroll {{ overflow-x:auto; border:1px solid var(--border); border-radius:10px; background:var(--panel); }}
.hl-scroll.tall {{ max-height:360px; overflow-y:auto; }}
table.hl {{ width:100%; border-collapse:collapse; font-size:13px; }}
table.hl th {{ color:var(--muted); font-weight:500; text-align:left; padding:9px 12px; border-bottom:1px solid var(--border);
        white-space:nowrap; position:sticky; top:0; background:var(--panel); }}
table.hl td {{ padding:8px 12px; border-bottom:1px solid rgba(29,46,53,.6); font-family:var(--mono); color:var(--text);
        white-space:nowrap; }}
table.hl td.lbl {{ font-family:Inter, system-ui, sans-serif; color:var(--muted); }}
table.hl tr:last-child td {{ border-bottom:none; }}
table.hl tr:hover td {{ background:var(--panel2); }}
table.hl tr.sum td {{ color:var(--text); font-weight:600; background:rgba(80,210,193,.05); }}
table.hl .yes {{ color:var(--good-t); }} table.hl .no {{ color:var(--muted); }}
.hl-list {{ margin:4px 0 8px; padding:0; list-style:none; }}
.hl-list li {{ padding:8px 12px; margin:6px 0; background:var(--panel); border:1px solid var(--border); border-radius:8px;
        font-size:14px; color:var(--text); }}
.hl-list li.bad {{ border-left:3px solid var(--bad); }} .hl-list li.warn {{ border-left:3px solid var(--warn); }}
.hl-list li.good {{ border-left:3px solid var(--good); }} .hl-list li.info {{ border-left:3px solid var(--accent); }}
[data-testid="stMarkdownContainer"] p.hl-small {{ color:var(--muted); font-size:12px; margin:6px 0 0; line-height:1.5; }}
.hl-code {{ font-family:var(--mono); background:var(--panel2); border:1px solid var(--border); border-radius:8px;
        padding:10px 12px; color:var(--mint); font-size:13px; overflow-x:auto; white-space:nowrap; }}
.hl-legend {{ display:flex; gap:14px; flex-wrap:wrap; font-size:12px; color:var(--muted); margin:2px 0 0; }}
.hl-legend span::before {{ content:""; display:inline-block; width:10px; height:10px; border-radius:2px; margin-right:6px;
        vertical-align:-1px; background:var(--c); }}
.hl-tag {{ display:inline-block; font-size:11px; padding:1px 7px; border-radius:999px; margin-left:6px;
        border:1px solid var(--border); color:var(--muted); vertical-align:1px; }}
.hl-tag.yes {{ color:var(--good-t); border-color:rgba(31,158,120,.5); }}
.hl-kv {{ display:grid; grid-template-columns:minmax(150px, 210px) 1fr; gap:8px 14px; font-size:14px; margin:6px 0 4px; }}
.hl-kv .k {{ color:var(--muted); }}
.hl-kv .v b {{ color:var(--text); }}
@media (max-width:640px) {{ .hl-kv {{ grid-template-columns:1fr; gap:2px 0; }} .hl-kv .k {{ margin-top:8px; }} }}
[data-testid="stNumberInputContainer"], [data-testid="stNumberInput"] [data-baseweb="input"] {{
        background:var(--panel2) !important; border:1px solid var(--border) !important; border-radius:8px !important; }}
[data-testid="stNumberInput"] input {{ background:var(--panel2) !important; color:var(--text) !important;
        -webkit-text-fill-color:var(--text) !important; font-family:var(--mono) !important; caret-color:var(--accent); }}
[data-testid="stNumberInputStepDown"], [data-testid="stNumberInputStepUp"] {{ background:var(--panel2) !important;
        color:var(--muted) !important; border:none !important; }}
[data-testid="stNumberInputStepDown"]:hover, [data-testid="stNumberInputStepUp"]:hover {{ color:var(--accent) !important; }}
.hl-verdict.info {{ border-left-color:var(--accent); background:linear-gradient(90deg, rgba(80,210,193,.14), var(--panel) 65%); }}
.hl-verdict.info .t {{ color:var(--mint); }}
.hl-dec {{ margin:8px 0 10px; padding-left:18px; }}
.hl-dec li {{ margin:4px 0; font-size:14px; color:var(--text); line-height:1.5; }}
.hl-dec li b {{ color:var(--text); }}
.hl-verdict .s b {{ color:var(--text); }}
[data-baseweb="tab-list"] {{ gap:6px; border-bottom:1px solid var(--border); }}
[data-baseweb="tab"] {{ background:transparent !important; }}
[data-baseweb="tab"] p {{ color:var(--muted) !important; font-size:15px !important; font-weight:600; }}
[data-baseweb="tab"][aria-selected="true"] p {{ color:var(--accent) !important; }}
[data-baseweb="tab-highlight"] {{ background-color:var(--accent) !important; }}
[data-baseweb="tab-border"] {{ background-color:var(--border) !important; }}
[data-testid="stRadio"] label p {{ color:var(--text) !important; }}
ol.hl-steps {{ margin:4px 0 8px; padding-left:22px; }}
ol.hl-steps li {{ margin:6px 0; font-size:15px; color:var(--text); line-height:1.5; }}
.hl-gboxes {{ display:grid; grid-template-columns:repeat(2, minmax(0, 1fr)); gap:10px; }}
@media (max-width:640px) {{ .hl-gboxes {{ grid-template-columns:1fr; }} }}
.hl-card.hl-mini {{ margin:0; padding:10px 14px; }}
.hl-card.hl-mini .t {{ font-size:15px; }}
.hl-card.hl-mini .s {{ font-size:13px; }}
[data-testid="stMarkdownContainer"] p.hl-gwhat {{ font-size:14px; color:var(--text); margin:2px 0 8px; }}
[data-testid="stMarkdownContainer"] p.hl-glab {{ font-size:12px; color:var(--muted); text-transform:uppercase;
        letter-spacing:.06em; margin:8px 0 0; }}
.hl-gpair {{ display:grid; grid-template-columns:repeat(2, minmax(0, 1fr)); gap:10px; margin:8px 0 4px; }}
@media (max-width:640px) {{ .hl-gpair {{ grid-template-columns:1fr; }} }}
.hl-gpair > div {{ background:var(--panel2); border:1px solid var(--border); border-radius:8px; padding:8px 12px;
        font-size:13px; color:var(--text); line-height:1.5; }}
.hl-gpair .good {{ border-left:3px solid var(--good); }} .hl-gpair .good b {{ color:var(--good-t); }}
.hl-gpair .bad {{ border-left:3px solid var(--bad); }} .hl-gpair .bad b {{ color:var(--bad-t); }}
.hl-chips {{ display:flex; flex-wrap:wrap; gap:8px 16px; margin:6px 0 4px; font-size:13px; color:var(--text); }}
.hl-chips span::before {{ content:""; display:inline-block; width:12px; height:12px; border-radius:3px; margin-right:6px;
        vertical-align:-1px; background:var(--c); }}
ul.hl-checks {{ list-style:none; padding:0; margin:4px 0 8px; }}
ul.hl-checks li {{ padding:7px 12px; margin:5px 0; background:var(--panel); border:1px solid var(--border); border-radius:8px;
        font-size:14px; color:var(--text); }}
ul.hl-checks li::before {{ content:"☐ "; color:var(--accent); font-weight:700; }}
.hl-conc {{ background:var(--panel2); border:1px solid var(--border); border-left:3px solid var(--accent); border-radius:8px;
        padding:10px 14px; margin:12px 0 4px; font-size:14px; color:var(--text); line-height:1.5; }}
.hl-conc .lab {{ display:block; font-size:11px; letter-spacing:.08em; text-transform:uppercase; color:var(--accent);
        font-weight:600; margin-bottom:2px; }}
.hl-conc.good {{ border-left-color:var(--good); }} .hl-conc.good .lab {{ color:var(--good-t); }}
.hl-conc.warn {{ border-left-color:var(--warn); }} .hl-conc.warn .lab {{ color:var(--warn-t); }}
.hl-conc.bad {{ border-left-color:var(--bad); }} .hl-conc.bad .lab {{ color:var(--bad-t); }}
</style>
"""

st.set_page_config(page_title="LP Guard · check before you LP", page_icon="🛡️", layout="centered")


def ui(markup):
    st.markdown(markup, unsafe_allow_html=True)


def h(text):
    """Escape dynamic text (on-chain token names can contain HTML) and `$` (Streamlit reads it as math)."""
    return html.escape(str(text)).replace("$", "&#36;")


def api_keys():
    """All Alchemy keys: secrets / environment (ALCHEMY_API_KEYS and ALCHEMY_API_KEY) plus extra keys from the
    sidebar. Sidebar keys only apply to this browser session (never put in the server environment)."""
    found = []
    for name in ("ALCHEMY_API_KEYS", "ALCHEMY_API_KEY"):
        try:
            found.append(st.secrets.get(name))
        except Exception:  # no secrets.toml
            pass
        found.append(os.environ.get(name))
    keys = parse_keys(*found)
    extra = st.sidebar.text_input("Extra Alchemy API key" if keys else "Alchemy API key", type="password",
                                  help="Free at alchemy.com. You can add several, comma separated: they are used in "
                                       "turn, and a key that runs out of quota is skipped automatically.")
    keys = parse_keys(keys, extra)
    if keys:
        st.sidebar.caption(f"{len(keys)} Alchemy keys used in rotation." if len(keys) > 1 else "1 Alchemy key.")
    return keys


def key_status(rpc):
    """Status of each key in the sidebar (never shows the key itself)."""
    rows = [f"key #{i}: {h(s)} · {cu / 1000:,.0f}k CU since the app started" for i, s, cu in rpc.status()]
    st.sidebar.caption("  \n".join(rows))


@st.cache_data(ttl=600, show_spinner=False)
def eth_price(_keys):
    return eth_usd(_keys)


def compact(v):
    for d, s in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(v) >= d:
            return f"{v / d:,.2f}{s}"
    return f"{v:,.1f}"


def money(v, usd, unit="ETH"):
    return f"${compact(v * usd)}" if usd else f"{compact(v)} {unit}"


def price_str(p, usd, unit):
    """Token price with enough significant digits (memecoin prices are tiny)."""
    v = p * (usd or 1)
    s = f"{v:.4g}" if v >= 1e-4 else f"{v:.3e}"
    return f"${s}" if usd else f"{s} {unit}"


def pair_unit(a):
    """The asset your LP is paired with: USDG when the coin is judged on its USDG pool."""
    return "USDG" if (a["usdg"] or {}).get("main") else a["quote"]


def when(t):
    g = time.gmtime(t)
    return f"{MONTHS[g.tm_mon - 1]} {g.tm_mday} {g.tm_hour:02d}:{g.tm_min:02d}"


def sign_class(v):
    return "" if v is None else "pos" if v > 1e-6 else "neg" if v < -1e-6 else ""


def tiles(items, cls=""):
    """items: (title, value, value color class, small caption)."""
    cells = "".join(f'<div class="hl-tile"><div class="k">{h(k)}</div><div class="v {c}">{h(v)}</div>'
                    + (f'<div class="d">{h(d)}</div>' if d else "") + "</div>" for k, v, c, d in items)
    ui(f'<div class="hl-tiles {cls}">{cells}</div>')


def table(header, rows, sum_last=False, tall=False):
    head = "".join(f"<th>{h(x)}</th>" for x in header)
    body = []
    for i, r in enumerate(rows):
        cls = ' class="sum"' if sum_last and i == len(rows) - 1 else ""
        cells = []
        for j, x in enumerate(r):
            if isinstance(x, tuple):  # (text, class)
                cells.append(f'<td class="{x[1]}">{h(x[0])}</td>')
            else:
                cells.append(f'<td class="lbl">{h(x)}</td>' if j == 0 else f"<td>{h(x)}</td>")
        body.append(f"<tr{cls}>{''.join(cells)}</tr>")
    ui(f'<div class="hl-scroll{" tall" if tall else ""}"><table class="hl"><thead><tr>{head}</tr></thead>'
       f'<tbody>{"".join(body)}</tbody></table></div>')


def section(title, sub=None):
    ui(f'<div class="hl-h">{h(title)}</div>' + (f'<p class="hl-small" style="margin:-4px 0 8px">{sub}</p>' if sub else ""))


def pct1(v):
    """Percent with one decimal (range results are often small)."""
    return f"{v * 100:+.1f}%"


def conclude(text, kind="info"):
    """Conclusion box at the end of an analysis (text may contain <b>; dynamic parts already escaped)."""
    ui(f'<div class="hl-conc {kind}"><span class="lab">Conclusion</span>{text}</div>')


def bullets(items):
    if items:
        ui('<ul class="hl-list">' + "".join(f'<li class="{c}">{h(t)}</li>' for c, t in items) + "</ul>")


def names_text(names):
    if not names:
        return "the reference coins"
    return " & ".join(names) if len(names) <= 2 else ", ".join(names[:-1]) + " & " + names[-1]


def short_names(names, n=3):
    return names_text(names) if len(names) <= n else ", ".join(names[:n]) + f" and {len(names) - n} more"


CHART_W = 840  # plot width of fixed-width charts (fits the 960px column with axes)
AXIS = dict(labelColor=MUTED, titleColor=MUTED, gridColor=BORDER, domainColor=BORDER, tickColor=BORDER,
            labelFont="JetBrains Mono, ui-monospace, monospace", titleFontWeight="normal", labelFontSize=11,
            titleFontSize=11)


def chart(a):
    usd, qu = a["usd"], a["quote"]
    k = usd or 1
    rows = a["rows"]
    df = pd.DataFrame({
        "time": pd.to_datetime([r["t"] for r in rows], unit="s"),
        "mcap": [r["px"] * a["supply"] * k for r in rows],
        "whale6h": [r["wn6"] for r in rows],
        "verdict": [VERDICTS[r["level"]] for r in rows],
    })
    df["until"] = df["time"] + pd.Timedelta(seconds=STEP)
    unit = "USD" if usd else a["quote"]
    fmt = "$~s" if usd else "~s"
    x = alt.X("time:T", title=None, axis=alt.Axis(format="%d %b %H:%M", grid=False))
    nearest = alt.selection_point(nearest=True, on="pointerover", fields=["time"], empty=False)
    tip = [alt.Tooltip("time:T", title="Time (UTC)", format="%d %b %H:%M"),
           alt.Tooltip("mcap:Q", title=f"Market cap ({unit})", format=",.0f"),
           alt.Tooltip("whale6h:Q", title="Big holders, 6h: + sold / − bought (% of all coins)", format="+.2f"),
           alt.Tooltip("verdict:N", title="Verdict")]
    base = alt.Chart(df).encode(x=x)
    line = base.mark_line(strokeWidth=2, color=ACCENT).encode(
        y=alt.Y("mcap:Q", title=f"Market cap ({unit})", axis=alt.Axis(format=fmt)))
    area = base.mark_area(color=ACCENT, opacity=0.08).encode(y="mcap:Q")
    layers = [area, line,
              base.mark_point(opacity=0).encode(y="mcap:Q").add_params(nearest),
              line.mark_point(filled=True, size=70, color=MINT, stroke=PANEL, strokeWidth=2).encode(
                  opacity=alt.condition(nearest, alt.value(1), alt.value(0))),
              base.mark_rule(color=MUTED).encode(tooltip=tip).transform_filter(nearest)]
    pat = a["pattern"]
    if pat:  # ATH-retest pattern levels with direct labels
        h1, hr, low = (pat[x] * a["supply"] for x in ("h1", "retest", "low"))
        if abs(hr / h1 - 1) < 0.08:  # labels too close: one label for both lines
            lv = [{"y": max(h1, hr) * k, "label": f"Previous high {money(h1, usd, qu)} / retest {money(hr, usd, qu)}"},
                  {"y": min(h1, hr) * k, "label": ""}]
        else:
            lv = [{"y": h1 * k, "label": f"Previous high {money(h1, usd, qu)}"},
                  {"y": hr * k, "label": f"Retest high {money(hr, usd, qu)}"}]
        lv = pd.DataFrame(lv + [{"y": low * k, "label": f"Support {money(low, usd, qu)}"}])
        layers += [alt.Chart(lv).mark_rule(color=MUTED, strokeDash=[4, 4], strokeWidth=1).encode(y="y:Q"),
                   alt.Chart(lv).mark_text(align="left", dx=4, dy=-6, color=MUTED, fontSize=11).encode(
                       y="y:Q", text="label:N", x=alt.value(0))]
    ex = a.get("exits") or {}
    marks = []
    if ex.get("take") and not ex.get("single"):
        marks.append({"y": ex["take"]["price"] * a["supply"] * k, "label": f"take profit +{ex['take']['up'] * 100:.0f}%"})
    if ex.get("floor"):
        marks.append({"y": ex["floor"]["price"] * a["supply"] * k, "label": f"floor {pct(ex['floor']['drop'])}"})
    if marks:
        md = pd.DataFrame(marks)
        layers += [alt.Chart(md).mark_rule(color=STATUS_TEXT[1], strokeDash=[6, 3], strokeWidth=1).encode(y="y:Q"),
                   alt.Chart(md).mark_text(align="right", dx=-4, dy=-6, color=STATUS_TEXT[1], fontSize=11).encode(
                       y="y:Q", text="label:N", x=alt.value(CHART_W))]
    price = alt.layer(*layers).properties(height=260, width=CHART_W)
    whale = alt.layer(
        base.mark_line(strokeWidth=2, color=ACCENT).encode(
            y=alt.Y("whale6h:Q", title="Big holders sold(+)/bought(−) %",
                    scale=alt.Scale(domain=[max(df["whale6h"].min(), -5), max(df["whale6h"].max(), 2)], clamp=True))),
        alt.Chart(pd.DataFrame({"y": [1.5]})).mark_rule(color=STATUS["AVOID"], strokeDash=[4, 4]).encode(y="y:Q"),
        alt.Chart(pd.DataFrame({"y": [1.5], "label": ["danger line: big holders sold 1.5%"]})).mark_text(
            align="left", dx=4, dy=-6, color=MUTED, fontSize=11).encode(y="y:Q", text="label:N", x=alt.value(0)),
    ).properties(height=110, width=CHART_W)
    strip = alt.Chart(df).mark_rect().encode(
        x=x, x2="until:T",
        color=alt.Color("verdict:N", legend=None, scale=alt.Scale(domain=list(STATUS), range=list(STATUS.values()))),
        tooltip=[alt.Tooltip("time:T", title="Time (UTC)", format="%d %b %H:%M"), "verdict:N"],
    ).properties(height=22, width=CHART_W)
    ch = (alt.vconcat(price, whale, strip, spacing=8).resolve_scale(x="shared")
          .configure(background="transparent", font="Inter, system-ui, sans-serif")
          .configure_view(strokeWidth=0).configure_axis(**AXIS))
    return ch, df


# Liquidity profile colors, validated for the dark panel (CVD ΔE 12.9, contrast ≥ 3:1): buy-side (below price)
# and sell-side (above price) LP liquidity; traded volume is a single neutral series in its own panel.
BID, ASK, VOL_C = "#2FA898", "#9085E9", "#5F737A"
BELOW, ABOVE = "Below price: absorbs selling", "Above price: absorbs buying"
# diverging ramp for the range map (red = loses money, blue = grows), neutral gray midpoint
DIV_NEG, DIV_MID, DIV_POS = "#E66767", "#383835", "#3987E5"


def pct_label(axis_values="datum.value"):
    return (f"{axis_values} > 0 ? '+' + {axis_values} + '%' : {axis_values} == 0 ? 'now' : "
            f"'−' + (-{axis_values}) + '%'")


def liquidity_profile(a, lv, band=2, down=80):
    """Vertical liquidity profile (like VPVR): LP liquidity in each 2% price band below and above the current
    price, next to what traded at those prices in the last 7 days. Support zones and the best range on top."""
    dep, supply, usd, qu = lv["depth"], a["supply"], a["usd"], a["quote"]
    k, px = usd or 1, a["now"]["px"]
    pick = safe_pick(a)[0]
    best = pick["range"] if pick else None
    up_usd = dep.get("up_usd")
    up = 0
    if up_usd:
        want = 40 if not best or rg.is_full(best) else max(40, best[1] + 10)
        tk = (a.get("exits") or {}).get("take")
        if tk and not is_single(a):
            want = max(want, tk["up"] * 100 + 10)
        up = int(min(len(up_usd) - 1, want, 160)) // band * band
    rows = []
    for d0 in range(0, down, band):
        rows.append({"y0": -d0 - band, "y1": -d0, "liq": dep["usd"][d0 + band] - dep["usd"][d0], "side": BELOW,
                     "band": f"−{d0}% to −{d0 + band}%", "cum": dep["usd"][d0 + band],
                     "mcap": px * (1 - (d0 + band / 2) / 100) * supply * k})
    for u0 in range(0, up, band):
        rows.append({"y0": u0, "y1": u0 + band, "liq": up_usd[u0 + band] - up_usd[u0], "side": ABOVE,
                     "band": f"+{u0}% to +{u0 + band}%", "cum": up_usd[u0 + band],
                     "mcap": px * (1 + (u0 + band / 2) / 100) * supply * k})
    df = pd.DataFrame(rows)
    df["cum_label"] = ["Selling needed to get here" if s == BELOW else "Buying needed to get here" for s in df["side"]]
    vol = lv.get("volume") or {}
    vdf = pd.DataFrame([{"y0": b, "y1": b + band, "vol": vol.get(b, 0.0) * k,
                         "band": (f"+{b}% to +{b + band}%" if b >= 0 else f"−{-b - band}% to −{-b}%")}
                        for b in range(-down, up, band)])
    lo_y = -down
    xmax = max(df["liq"].max(), 1.0) * 1.5   # free space on the right for the labels
    y = alt.Y("y0:Q", title="Price vs now", scale=alt.Scale(domain=[lo_y, max(up, 4)], nice=False),
              axis=alt.Axis(values=list(range(lo_y, up + 1, 10)), labelExpr=pct_label(), grid=False))
    tip = [alt.Tooltip("band:N", title="Price band"),
           alt.Tooltip("mcap:Q", title="Market cap here" + (" (USD)" if usd else f" ({qu})"), format=",.0f"),
           alt.Tooltip("liq:Q", title="LP liquidity in this band (USD)", format="$,.0f"),
           alt.Tooltip("cum:Q", title="Total to push price here (USD)", format="$,.0f")]
    over = []
    if best and not rg.is_full(best):
        rng = pd.DataFrame([{"y0": -best[0], "y1": min(best[1], up) if up else 0}])
        over.append(alt.Chart(rng).mark_rect(color=MINT, opacity=0.07).encode(y="y0:Q", y2="y1:Q", x=alt.datum(0),
                                                                               x2=alt.datum(xmax)))
        edges = pd.DataFrame([{"y": -best[0], "label": f"Safe range low -{best[0]:g}%"}]
                             + ([{"y": best[1], "label": f"Safe range high +{best[1]:g}%"}] if 0 < best[1] <= up else []))
        over.append(alt.Chart(edges).mark_rule(color=MINT, strokeDash=[4, 4], strokeWidth=1).encode(y="y:Q"))
        over.append(alt.Chart(rng).mark_rule(color=MINT, strokeWidth=4).encode(y="y0:Q", y2="y1:Q", x=alt.datum(xmax)))
    # labels on the right edge; drop a label when it would collide with a more important one (≥ 4% apart)
    labels = [(0, f"now · {money(px * supply, usd, qu)}", 0)]
    if best and not rg.is_full(best):
        labels += [(-best[0], f"safe range low -{best[0]:g}%", 1)] + ([(best[1], f"safe range high +{best[1]:g}%", 1)]
                                                                      if 0 < best[1] <= up else [])
    zones = []
    for z in lv["zones"]:
        mid = z["drop"] * 100
        if mid <= lo_y:
            continue
        zones.append({"y0": max(lo_y, ((1 + z["drop"]) * (1 - ZONE) - 1) * 100), "y1": ((1 + z["drop"]) * (1 + ZONE) - 1) * 100})
        labels.append((mid, f"{pct(z['drop'])} {z['strength']} · {money(z['price'] * supply, usd, qu)}",
                       {"strong": 2, "medium": 3, "weak": 4}[z["strength"]]))
    res = []
    ex = a.get("exits") or {}
    for z in ex.get("zones") or []:
        mid = z["up"] * 100
        if mid >= up:
            continue
        res.append({"y0": ((1 + z["up"]) * (1 - ZONE) - 1) * 100, "y1": min(up, ((1 + z["up"]) * (1 + ZONE) - 1) * 100)})
        labels.append((mid, f"+{mid:.0f}% {z['strength']} resistance · {money(z['price'] * supply, usd, qu)}",
                       {"strong": 2, "medium": 3, "weak": 4}[z["strength"]]))
    if ex.get("take") and not ex.get("single") and ex["take"]["up"] * 100 < up:
        labels.append((ex["take"]["up"] * 100, f"take profit +{ex['take']['up'] * 100:.0f}%", 1))
        over.append(alt.Chart(pd.DataFrame([{"y": ex["take"]["up"] * 100}])).mark_rule(
            color=STATUS_TEXT[1], strokeDash=[6, 3], strokeWidth=1.5).encode(y="y:Q"))
    if res:
        over.insert(0, alt.Chart(pd.DataFrame(res)).mark_rect(color=ASK, opacity=0.12).encode(
            y="y0:Q", y2="y1:Q", x=alt.datum(0), x2=alt.datum(xmax)))
    kept = []
    for yv, text, pri in sorted(labels, key=lambda x: x[2]):
        if all(abs(yv - kv) >= 4 for kv, _, _ in kept):
            kept.append((yv, text, pri))
    if zones:
        over.insert(0, alt.Chart(pd.DataFrame(zones)).mark_rect(color=MINT, opacity=0.10).encode(
            y="y0:Q", y2="y1:Q", x=alt.datum(0), x2=alt.datum(xmax)))
    now = alt.Chart(pd.DataFrame([{"y": 0}])).mark_rule(color=TEXT, strokeWidth=1).encode(y="y:Q")
    lab = alt.Chart(pd.DataFrame([{"y": yv, "label": t} for yv, t, _ in kept])).mark_text(
        align="right", baseline="bottom", dx=-4, dy=-2, color=TEXT, fontSize=11).encode(y="y:Q", text="label:N",
                                                                                      x=alt.datum(xmax))
    bars = alt.Chart(df).mark_bar(yOffset=-1, y2Offset=1, cornerRadiusEnd=3).encode(
        y=y, y2="y1:Q", x=alt.X("liq:Q", title=f"LP liquidity per {band}% band (USD)", axis=alt.Axis(format="$~s", tickCount=5),
                                scale=alt.Scale(domain=[0, xmax], nice=False)), x2=alt.datum(0),
        color=alt.Color("side:N", scale=alt.Scale(domain=[BELOW, ABOVE], range=[BID, ASK]),
                        legend=alt.Legend(orient="top", title=None, labelColor=TEXT, labelFontSize=12, symbolType="square")),
        tooltip=tip)
    left = alt.layer(*over, bars, now, lab).properties(width=560, height=470)
    vmax = max(vdf["vol"].max(), 1.0) * 1.1
    vbars = alt.Chart(vdf).mark_bar(yOffset=-1, y2Offset=1, cornerRadiusEnd=3, color=VOL_C).encode(
        y=alt.Y("y0:Q", scale=alt.Scale(domain=[lo_y, max(up, 4)], nice=False), axis=None), y2="y1:Q",
        x=alt.X("vol:Q", title="Traded here, last 7 days (USD)", axis=alt.Axis(format="$~s", tickCount=3),
                scale=alt.Scale(domain=[0, vmax], nice=False)), x2=alt.datum(0),
        tooltip=[alt.Tooltip("band:N", title="Price band"), alt.Tooltip("vol:Q", title="Traded, 7 days (USD)", format="$,.0f")])
    right = alt.layer(vbars, now).properties(width=200, height=470)
    ch = (alt.hconcat(left, right, spacing=10).resolve_scale(y="shared")
          .configure(background="transparent", font="Inter, system-ui, sans-serif")
          .configure_view(strokeWidth=0).configure_axis(**AXIS))
    return ch, df


def range_map(rec):
    """Heatmap: growth per night for each lower × upper bound, best cell outlined."""
    lo, hi, heat = rec["lo"], rec["hi"], rec["heat"]
    rows = [{"lower": f"−{l}%", "upper": f"+{u}%", "g": heat[i][j], "range": f"−{l}% / +{u}%"}
            for i, l in enumerate(lo) for j, u in enumerate(hi)]
    df = pd.DataFrame(rows)
    m = max(abs(df["g"].min()), abs(df["g"].max()), 0.002)
    best = rec["best"]["range"]
    xs, ys = [f"+{u}%" for u in hi], [f"−{l}%" for l in lo]
    base = alt.Chart(df).encode(x=alt.X("upper:O", sort=xs, title="Upper bound", axis=alt.Axis(labelAngle=0)),
                                y=alt.Y("lower:O", sort=ys, title="Lower bound"))
    cells = base.mark_rect(stroke=PANEL, strokeWidth=2, cornerRadius=2).encode(
        color=alt.Color("g:Q", scale=alt.Scale(domain=[-m, 0, m], range=[DIV_NEG, DIV_MID, DIV_POS]),
                        legend=alt.Legend(title="Growth / night", format="+.1%", labelColor=MUTED, titleColor=MUTED,
                                          gradientLength=160)),
        tooltip=[alt.Tooltip("range:N", title="Range"), alt.Tooltip("g:Q", title="Growth per night", format="+.2%")])
    layers = [cells]
    if not rg.is_full(best):
        b = df[(df["lower"] == f"−{best[0]}%") & (df["upper"] == f"+{best[1]}%")]
        layers += [alt.Chart(b).mark_rect(fill=None, stroke=TEXT, strokeWidth=2, cornerRadius=2).encode(
            x=alt.X("upper:O", sort=xs), y=alt.Y("lower:O", sort=ys)),
            alt.Chart(b).mark_text(color=TEXT, fontSize=10, fontWeight="bold", text="best").encode(
                x=alt.X("upper:O", sort=xs), y=alt.Y("lower:O", sort=ys))]
    return (alt.layer(*layers).properties(height=330)
            .configure(background="transparent", font="Inter, system-ui, sans-serif")
            .configure_view(strokeWidth=0).configure_axis(**AXIS))


# ---------------------------------------------------------------- sections

def usd_of(v):
    """Signed dollar amount, e.g. +$12 / -$9.40 / $0."""
    if abs(v) < 0.005:
        return "$0"
    s = "-" if v < 0 else "+"
    v = abs(v)
    return f"{s}${compact(v)}" if v >= 1000 else f"{s}${v:,.2f}" if v < 10 else f"{s}${v:,.0f}"


def dollars(v):
    return f"${v:,.0f}"


def lost_or_made(v):
    """'lost $53' for a loss, 'made +$4' for a gain (no double minus)."""
    return f"lost {dollars(-v)}" if v < 0 else f"made {usd_of(v)}"


def flow_side(w):
    r = w["buy"] / (w["buy"] + w["sell"]) if w["buy"] + w["sell"] else 0.5
    return "mostly buying" if r >= 0.6 else "mostly selling" if r <= 0.4 else "about even"


def of_them(n, verb_one, verb_many):
    return "none of them " + verb_many if n == 0 else f"1 of them {verb_one}" if n == 1 else f"{n} of them {verb_many}"


def risk_word(p):
    return "low" if p < 0.15 else "medium" if p < 0.35 else "high"


def how(title="How is this calculated?"):
    return st.expander(title)


def usdg_section(a):
    """Which USDG pool the analysis uses (where you LP), or a warning if there is none."""
    u = a["usdg"]
    if u is None:
        return
    main = u["main"]
    if not main:
        ui(f'<div class="hl-alert warn">⚠️ <b>This coin has no active USDG pool yet.</b> We used its '
           f'{h(a["market"].get("pair_symbol") or a["quote"])} pool instead'
           + ("; dollar values use today's ETH price" if a["usd"] else "") + '.</div>')
        return
    with st.expander(f"Pool used: USDG pool with a {main['fee'] / 1e4:g}% fee (${compact(u['depth'])} liquidity)"):
        ui(f'<p class="hl-small">This is the biggest of {len(u["pools"])} USDG pools for this coin. All prices, market caps and '
           'ranges on this page are in USD. Prices are almost the same in every pool, so the verdict counts for all of them; '
           'only the fee and the size differ.'
           + (f' Price history from before this pool existed comes from the {h(u["pre"])} pool, converted to USD.'
              if u["since_block"] and u["pre"] else "") + '</p>')
        if len(u["pools"]) > 1:
            total = sum(p["swaps"] for p in u["pools"]) or 1
            rows = [[("✓ " if p is main else "") + f"{p['pool_id'][:10]}…{p['pool_id'][-4:]}", f"{p['fee'] / 1e4:g}%",
                     f"{p['swaps'] / total * 100:.0f}%", f"${compact(2 * p['usdg_side'])}"] for p in u["pools"]]
            table(["Pool", "Fee", "Share of trades", "Liquidity"], rows)


SINGLE, TWO = "Single-sided USDG (0% to −X%)", "Two-sided (USDG + coin)"


def is_single(a):
    return bool(a.get("single_sided"))


def plan_label(a):
    """Your planned range as text, e.g. '-65% to 0%', '-65% / +65%' or 'full range'."""
    if a["range_pct"] <= 0:
        return "full range"
    return f"-{a['range_pct']:g}% to 0%" if is_single(a) else f"-{a['range_pct']:g}% / +{a.get('upper_pct', a['range_pct']):g}%"


def safe_pick(a):
    """(range aman yang disarankan, info range aman, tidak menguntungkan?) dari hasil riset range."""
    rec = a.get("best_range")
    sf = (rec or {}).get("safe") or {}
    pick = sf.get("pick")
    return pick, sf, bool(pick) and pick["growth"] < 0


def stay_txt(c):
    return f"{c['stay'] * 100:.0f}%"


def decision_card(a):
    """One clear answer at the top: should I open an LP now, and how?"""
    lvl, rec, p, q = a["level"], a.get("best_range"), a["model_p"], a.get("analogs")
    hours, pos = f"{a['sleep_h']:g} hours", a["position_usd"]
    pick, sf, skip = safe_pick(a)
    if lvl == 2:
        cls, title, todo = "bad", "🚫 Don't open an LP now", "Wait and check again later. The chance of a big drop is high."
    elif lvl == 1:
        cls, title = "warn", "⚠️ Risky — only small, and keep the alarm on"
        if skip:
            todo = "Better wait: the crash risk is higher than usual and the fees don't pay for it."
        else:
            todo = ("If you still open: use a small amount" + (f", the safe range <b>{h(pick['label'])}</b>" if pick else "")
                    + " and keep the alarm on.")
    elif skip:
        cls, title = "info", "✅ Low crash risk — but fees are too low to be worth it now"
        todo = ("Better wait until more people trade this coin (more fees)."
                + (f" If you open anyway, use the safe range <b>{h(pick['label'])}</b>." if pick else ""))
    else:
        cls, title = "good", "✅ OK to open an LP"
        todo = (f"Open the safe range <b>{h(pick['label'])}</b> and keep the alarm on." if pick
                else "Keep the alarm on while the position is open.")
    lines = []
    doubt = p is not None and q and q["p30"] >= max(0.2, p + 0.1)
    if doubt and lvl < 2:
        todo += " Similar past moments crashed more often than the model says, so use a smaller amount than usual."
    if p is not None:
        txt = f"<b>Crash risk: {risk_word(p)}.</b> Our model gives a {p * 100:.0f}% chance that the price falls 30% or more in the next {hours}."
        if doubt:
            txt += (f" But {q['n30']} of the {q['n']} most similar past moments did crash, so the real risk may be higher.")
        lines.append(txt)
    if pick and is_single(a):
        c = pick
        if skip:
            lines.append(f"<b>Safe range: {h(c['label'])}</b> — the price never fell to its bottom in {stay_txt(c)} of similar "
                         f"nights, but at today's fees ({rec['fee']['r'] * 100:.2f}% a day) it {lost_or_made(c['mean'] * pos)} "
                         f"on average with {dollars(pos)}.")
        elif lvl == 2:
            lines.append(f"<b>Range:</b> if you open anyway, the safe range is {h(c['label'])} (the price never fell to its "
                         f"bottom in {stay_txt(c)} of similar nights), but it {lost_or_made(c['p10'] * pos)} of {dollars(pos)} "
                         "on a bad night (1 in 10).")
        else:
            lines.append(f"<b>Safe range: {h(c['label'])}.</b> In {stay_txt(c)} of similar nights the price never fell to the "
                         f"bottom of it (it did in {c['hit_low'] * 100:.1f}%). With {dollars(pos)}: about "
                         f"{usd_of(c['mean'] * pos)} on average over {hours} (fees {usd_of(c['fees'] * pos)}); bad night "
                         f"{usd_of(c['p10'] * pos)}.")
    elif pick:
        c = pick
        if skip:
            lines.append(f"<b>Safe range: {h(c['label'])}</b> — it stayed in range the whole night in {stay_txt(c)} of similar "
                         f"nights, but at today's fees ({rec['fee']['r'] * 100:.2f}% a day) it {lost_or_made(c['mean'] * pos)} "
                         f"on average with {dollars(pos)}.")
        elif lvl == 2:
            lines.append(f"<b>Range:</b> if you open anyway, the safe range is {h(c['label'])} (stayed in range {stay_txt(c)} "
                         f"of similar nights), but it {lost_or_made(c['p10'] * pos)} of {dollars(pos)} on a bad night (1 in 10).")
        else:
            lines.append(f"<b>Safe range: {h(c['label'])}.</b> It stayed in range the whole night in {stay_txt(c)} of similar "
                         f"nights and touched the lower bound in {c['hit_low'] * 100:.1f}%. With {dollars(pos)}: about "
                         f"{usd_of(c['mean'] * pos)} on average over {hours} (fees {usd_of(c['fees'] * pos)}); bad night "
                         f"{usd_of(c['p10'] * pos)}.")
    ex = a.get("exits") or {}
    tk = ex.get("take")
    o = a.get("open_pos")
    if is_single(a) and lvl < 2 and not o:
        st_ = [z for z in ex.get("stall") or [] if z["strength"] in ("strong", "medium")]
        lines.append("<b>Exit:</b> automatic. You start with only USDG, so there is nothing to sell above today's price. If "
                     "the price dips into your range and comes back up, your LP sells the coin back into USDG by itself"
                     + (f"; a bounce may stall near the floor at {pct(st_[0]['drop'])}." if st_ else "."))
    elif tk and lvl < 2 and not o:
        lines.append(f"<b>Take profit:</b> around +{tk['up'] * 100:.0f}% (mcap {h(money(tk['price'] * a['supply'], a['usd'], a['quote']))}"
                     f", just under the {tk['zone']['strength']} resistance at +{tk['zone']['up'] * 100:.0f}%). Upper bound at "
                     f"+{tk['upper_bound']}% sells automatically there.")
    if o:
        g = o["good"]
        txt = (f"<b>Your open position</b> (opened at mcap {h(pos_mcap(a, o['entry_px']))}): {STATE_TXT[o['state']].lower()}, "
               f"worth about {h(dollars(pos * o['value']))} ({vs_deposit(o['value'])}, fees not included).")
        if o["state"] == "above":
            txt += " It is 100% USDG: nothing to sell."
        elif g:
            txt += (f" Good exit: mcap {h(pos_mcap(a, g['price']))} ({g['from_now'] * 100:+.0f}%"
                    + (", top of your range" if g["kind"] == "top" else f", under the {g['zone']['strength']} resistance") + ").")
        if o["stop"] and o["state"] != "above":
            txt += f" Close it if the price falls below mcap {h(pos_mcap(a, o['stop']['price']))}."
        lines.insert(0, txt)
        todo = ("Close your open position now: the chance of a big drop is high. Check again later before opening a new one."
                if lvl == 2 else "Open position: follow the exit plan below and keep the alarm on. New position: " + todo)
    hs = a.get("holder_summary")
    if hs:
        lines.append(f"<b>Big holders:</b> the 10 biggest wallets hold {hs['share'] * 100:.0f}% of all coins; "
                     + of_them(hs["big_profit"], "is up 3x or more", "are up 3x or more")
                     + (f", and {len(hs['sold24'])} sold in the last 24 hours." if hs["sold24"] else "."))
    fl = {w["hours"]: w for w in a.get("flow") or []}
    if 4 in fl:
        w = fl[4]
        k = a["usd"] or 1
        lines.append(f"<b>Money flow (last 4 hours):</b> {flow_side(w)} ({usd_of(w['net'] * k)} net).")
    ui(f'<div class="hl-card hl-verdict {cls}"><div class="t">{title}</div>'
       '<ul class="hl-dec">' + "".join(f"<li>{x}</li>" for x in lines) + "</ul>"
       f'<div class="s"><b>What to do:</b> {todo}</div></div>')


STATE_TXT = {"in": "In your range", "above": "Above your range", "below": "Below your range"}


def pos_mcap(a, price):
    return money(price * a["supply"], a["usd"], a["quote"])


def vs_deposit(v):
    return f"{(v - 1) * 100:+.1f}% vs what you put in"


def chance(x):
    return "—" if x is None else f"{x * 100:.0f}%"


def target_name(t):
    return ("Top of your range (automatic exit)" if t["kind"] == "top" else
            f"{t['zone']['strength'].capitalize()} resistance")


def position_section(a):
    """Exit plan for a position that is already open: where it stands, what it is worth, and where to get out."""
    o = a["open_pos"]
    pos, hours = a["position_usd"], f"{a['sleep_h']:g} hours"
    rng = f"-{o['lower']:g}% to 0%" if o["upper"] == 0 else f"-{o['lower']:g}% / +{o['upper']:g}%"
    kind = "single-sided USDG" if o["upper"] == 0 else "two-sided"
    now_off = a["now"]["px"] / o["entry_px"] - 1
    section("Your open position: exit plan",
            f"Opened at mcap {pos_mcap(a, o['entry_px'])} with range {rng} ({kind}), {dollars(pos)} put in. The price is now "
            f"mcap {pos_mcap(a, a['now']['px'])}, {now_off * 100:+.1f}% from your entry.")
    if not 0.02 <= a["now"]["px"] / o["entry_px"] <= 50:
        ui('<div class="hl-alert warn">Your entry market cap is very far from today\'s. Check the number (e.g. 8.38M, not '
           '8.38K).</div>')
    good, stop, bottom, st_ = o["good"], o["stop"], o["bottom"], o["state"]
    tiles([("Where the price is", STATE_TXT[st_], "pos" if st_ == "in" else "warn-t" if st_ == "below" else "",
            f"range mcap {pos_mcap(a, o['low_px'])} – {pos_mcap(a, o['top_px'])}"),
           ("Your position now", dollars(pos * o["value"]), sign_class(o["value"] - 1),
            f"{vs_deposit(o['value'])} · {o['coin'] / o['value'] * 100:.0f}% coin, {o['usdg'] / o['value'] * 100:.0f}% USDG"
            if o["value"] > 0 else ""),
           ("Good exit", pos_mcap(a, good["price"]) if good else "—", "pos" if good else "",
            (f"{good['from_now'] * 100:+.0f}% from now · " + ("top of range, automatic" if good["kind"] == "top" else
                                                            f"just under the {good['zone']['strength']} resistance"))
            if good else "already 100% USDG"),
           ("Exit if it breaks", pos_mcap(a, stop["price"]) if stop else "—", "warn-t" if stop else "",
            f"{stop['from_now'] * 100:+.0f}% from now · {stop['zone']['strength']} floor" if stop else "no strong floor below")])
    plan = []   # (class, label, text) — label in bold; texts are built from numbers and fixed words only
    if st_ == "above":
        plan.append(("good", "Nothing to exit:", f"your position is 100% USDG now — {dollars(pos * o['value'])}"
                     + (", your full deposit" if o["upper"] == 0 else f" ({vs_deposit(o['value'])})")
                     + ", plus the fees it earned."))
        plan.append(("info", "Options:", f"withdraw and keep the USDG, or leave it: it only buys the coin again if the price "
                     f"falls back below mcap {pos_mcap(a, o['top_px'])}. While the price stays above, it earns no fees. To earn "
                     "fees again, close it and open a new range from today's price (see the range section below)."))
    else:
        if st_ == "below":
            plan.append(("warn", "Now:", f"the price is below your range, so your position is all coin — "
                         f"{dollars(pos * o['value'])} ({vs_deposit(o['value'])}) — and it earns no fees. It moves 1:1 with "
                         f"the price until it gets back into your range at mcap {pos_mcap(a, o['low_px'])} "
                         f"({(o['low_px'] / a['now']['px'] - 1) * 100:+.0f}%)."))
        top = next((t for t in o["targets"] if t["kind"] == "top"), None)
        auto = (f"at mcap {pos_mcap(a, top['price'])} ({top['from_now'] * 100:+.0f}%) your LP is 100% USDG again by itself: "
                f"{dollars(pos * top['value'])}" + (", your full deposit back" if o["upper"] == 0 else f" ({vs_deposit(top['value'])})")
                + f", plus fees. The price got there in {chance(top['reach'])} of nights like this.") if top else ""
        if good and good["kind"] == "resistance":
            z = good["zone"]
            plan.append(("good", "Scenario 1 — the bounce stalls (good exit):",
                         f"close the LP by hand around mcap {pos_mcap(a, good['price'])} ({good['from_now'] * 100:+.0f}%), "
                         f"just under the {z['strength']} resistance ({res_why(z, a['usd'])}). Your position would be worth "
                         f"about {dollars(pos * good['value'])} ({vs_deposit(good['value'])}). Levels like this turned the price "
                         f"back {chance(good['hold'])} of the time in tests; the price got there in {chance(good['reach'])} of "
                         "nights like this."))
            nx = o["after"]
            txt = "if the price closes 5%+ above that level, it is broken: "
            if nx and nx["kind"] == "resistance":
                txt += (f"hold for the next {nx['zone']['strength']} resistance at mcap {pos_mcap(a, nx['price'])} (worth "
                        f"{dollars(pos * nx['value'])}). All the way up, {auto}")
            else:
                txt += "hold for the top of your range — " + auto
            plan.append(("info", "Scenario 2 — breakout:", txt))
        elif top:
            plan.append(("good", "Scenario 1 — the price climbs back (good exit):",
                         auto + " No strong resistance stands in the way, so let it exit by itself."))
        if stop:
            txt = (f"if the price falls below mcap {pos_mcap(a, stop['price'])} ({stop['from_now'] * 100:+.0f}%, the "
                   f"{stop['zone']['strength']} floor breaks), close the LP — worth about {dollars(pos * stop['value'])} then. "
                   f"Chance it falls that far in {hours}: {chance(stop['reach'])}.")
        else:
            txt = "there is no strong floor below the price to lean on; keep the alarm on."
        if bottom and st_ == "in":
            txt += (f" If you don't close, below the bottom of your range (mcap {pos_mcap(a, bottom['price'])}, "
                    f"{bottom['from_now'] * 100:+.0f}%) you hold only coin, worth {dollars(pos * bottom['value'])} there "
                    f"(chance in {hours}: {chance(bottom['reach'])}).")
        plan.append(("warn", f"Scenario {2 if good and good['kind'] == 'top' else 3} — drop:", txt))
    if a["level"] == 2:
        plan.insert(0, ("bad", "Verdict AVOID:", f"closing now ({dollars(pos * o['value'])}) is the safe choice."))
    if o["momentum"]:
        plan.append(("info", "Momentum is strong:", "medium and weak resistances usually break in this state, so only strong "
                     "ones count as a good exit."))
    ui('<ul class="hl-list">' + "".join(f'<li class="{c}"><b>{lab}</b> {t}</li>' for c, lab, t in plan) + "</ul>")
    if st_ != "above":
        ch = position_chart(a, o)
        st.altair_chart(ch, theme=None)
        ui('<p class="hl-small">The line = what your position is worth at each market cap (fees not included). Up = higher '
           'price. Purple bands = resistance above the price, green bands = floors below it, the shaded area = your range.</p>')
    if st_ == "above":
        conclude(*position_conclusion(a, o))
        return
    res = [t for t in o["targets"] if t["kind"] == "resistance"]
    shown = [t for t in o["targets"] if t["kind"] == "top" or t in res[:5] or t is good]
    rows = []
    for t in sorted(shown + [x for x in (o["now"], o["stop"], o["bottom"]) if x], key=lambda t: -t["price"]):
        name = {"now": "Now", "stop": "Floor — exit if it breaks", "bottom": "Bottom of your range"}.get(t["kind"]) or target_name(t)
        if t is good:
            name = "✓ Good exit: " + name
        rows.append([name, pos_mcap(a, t["price"]), (f"{t['from_now'] * 100:+.0f}%" if t["kind"] != "now" else "—"),
                     dollars(pos * t["value"]), (f"{(t['value'] - 1) * 100:+.1f}%", sign_class(t["value"] - 1)),
                     chance(t.get("hold")) if t["kind"] == "resistance" else "—",
                     chance(t["reach"]) if t["kind"] != "now" else "—"])
    table(["", "Market cap", "From now", "Your position worth", "vs put in", "Turned price back (tests)", f"Gets there ({hours})"],
          rows)
    ui('<p class="hl-small">"Your position worth" leaves out the fees your LP already earned (Uniswap shows them). "Turned price '
       'back" = how often levels of that strength held when the price reached them, in tests on the 24 sample coins (it '
       'depends on momentum). "Gets there" = how often the price reached that level during a night like this (this coin\'s '
       'own history and similar moments in other coins). Resistance now also counts floors the price fell through: in tests, '
       'a level with 2+ broken floors turned the price back about 45% of the time, versus 28% for a random level.</p>')
    conclude(*position_conclusion(a, o))


def position_conclusion(a, o):
    pos = a["position_usd"]
    good, stop = o["good"], o["stop"]
    if o["state"] == "above":
        return (f"Your position is 100% USDG ({h(dollars(pos * o['value']))} plus fees): no exit needed. Withdraw, or close it and "
                "open a new range from today's price if you want fees again."), "good"
    txt = f"Your position is worth about {h(dollars(pos * o['value']))} now ({vs_deposit(o['value'])}). "
    if good and good["kind"] == "resistance":
        txt += (f"Good exit: around <b>mcap {h(pos_mcap(a, good['price']))}</b> ({good['from_now'] * 100:+.0f}%), just under the "
                f"{good['zone']['strength']} resistance — worth about {h(dollars(pos * good['value']))}. If it breaks through, "
                "hold for the next level.")
    elif good:
        txt += (f"Good exit: the top of your range at <b>mcap {h(pos_mcap(a, good['price']))}</b> "
                f"({good['from_now'] * 100:+.0f}%), where the LP is 100% USDG again by itself.")
    if stop:
        txt += f" Close it if the price falls below mcap {h(pos_mcap(a, stop['price']))} ({stop['from_now'] * 100:+.0f}%)."
    if a["level"] == 2:
        return "The verdict is AVOID: closing now is the safe choice. " + txt, "bad"
    return txt, "good" if o["state"] == "in" and (o["value"] >= 0.95) else "warn"


def position_chart(a, o):
    """Vertical: market cap (up) vs what the position is worth; range shaded, resistance & floor bands, exit lines."""
    pos, sup, k = a["position_usd"], a["supply"], a["usd"] or 1
    px, e = a["now"]["px"], o["entry_px"]
    lo_p = min(o["low_px"] * 0.85, (o["stop"] or o["now"])["price"] * 0.9, px * 0.9)
    hi_p = max(o["top_px"] * 1.12, px * 1.1)
    n = 120
    ps = [lo_p * (hi_p / lo_p) ** (i / n) for i in range(n + 1)]
    df = pd.DataFrame({"mcap": [p * sup * k for p in ps], "worth": [pos * lp_value(p / e - 1, o["lower"], o["upper"]) for p in ps]})
    df["from_now"] = [(p / px - 1) * 100 for p in ps]
    w0, w1 = df["worth"].min(), max(df["worth"].max(), pos)
    xmin = max(0.0, w0 - (w1 - w0) * 0.08)
    xmax = w1 + (w1 - xmin) * 0.6   # free space on the right for the labels
    y = alt.Y("mcap:Q", title="Market cap" + (" (USD)" if a["usd"] else f" ({a['quote']})"),
              scale=alt.Scale(type="log", domain=[lo_p * sup * k, hi_p * sup * k], nice=False),
              axis=alt.Axis(format="$~s" if a["usd"] else "~s", grid=False, tickCount=6))
    x = alt.X("worth:Q", title="Your position worth (USD, fees not included)", axis=alt.Axis(format="$~s", tickCount=5),
              scale=alt.Scale(domain=[xmin, xmax], nice=False, zero=False))
    layers = [alt.Chart(pd.DataFrame([{"y0": max(lo_p, o["low_px"]) * sup * k, "y1": min(hi_p, o["top_px"]) * sup * k}])).mark_rect(
        color=MINT, opacity=0.06).encode(y="y0:Q", y2="y1:Q", x=alt.datum(xmin), x2=alt.datum(xmax))]
    bands = []
    for t in o["targets"]:
        if t["kind"] == "resistance":
            z = t["zone"]
            bands.append({"y0": z["price"] * (1 - ZONE) * sup * k, "y1": min(hi_p, z["price"] * (1 + ZONE)) * sup * k, "c": "res"})
    for z in (a.get("levels") or {}).get("zones", []):
        if z["price"] * (1 + ZONE) > lo_p and z["strength"] in ("strong", "medium"):
            bands.append({"y0": max(lo_p, z["price"] * (1 - ZONE)) * sup * k, "y1": z["price"] * (1 + ZONE) * sup * k, "c": "sup"})
    if bands:
        bd = pd.DataFrame(bands)
        layers.append(alt.Chart(bd).mark_rect(opacity=0.14).encode(
            y="y0:Q", y2="y1:Q", x=alt.datum(xmin), x2=alt.datum(xmax),
            color=alt.Color("c:N", scale=alt.Scale(domain=["res", "sup"], range=[ASK, BID]), legend=None)))
    nearest = alt.selection_point(nearest=True, on="pointerover", fields=["mcap"], empty=False)
    line = alt.Chart(df).mark_line(color=ACCENT, strokeWidth=2).encode(y=y, x=x, order="mcap:Q")
    tip = [alt.Tooltip("mcap:Q", title="Market cap", format="$,.0f" if a["usd"] else ",.0f"),
           alt.Tooltip("from_now:Q", title="From now (%)", format="+.0f"),
           alt.Tooltip("worth:Q", title="Your position worth", format="$,.0f")]
    layers += [line, alt.Chart(df).mark_point(opacity=0).encode(y=y, x=x, tooltip=tip).add_params(nearest),
               alt.Chart(df).mark_point(filled=True, size=70, color=MINT, stroke=PANEL, strokeWidth=2).encode(
                   y=y, x=x, opacity=alt.condition(nearest, alt.value(1), alt.value(0)))]
    marks = [(px, f"now · {pos_mcap(a, px)} · {dollars(pos * o['value'])}", TEXT, [], 0),
             (o["top_px"], f"top of range · {pos_mcap(a, o['top_px'])}", MINT, [4, 4], 2),
             (o["low_px"], f"bottom of range · {pos_mcap(a, o['low_px'])}", MINT, [4, 4], 3)]
    if o["good"] and o["good"]["kind"] == "resistance":
        marks.append((o["good"]["price"], f"good exit · {pos_mcap(a, o['good']['price'])} · {dollars(pos * o['good']['value'])}",
                      STATUS_TEXT[1], [6, 3], 1))
    if o["stop"]:
        marks.append((o["stop"]["price"], f"exit if it breaks · {pos_mcap(a, o['stop']['price'])}", STATUS_TEXT[2], [6, 3], 1))
    kept = []
    for yv, text, c, dash, pri in sorted(marks, key=lambda m: m[4]):
        if lo_p <= yv <= hi_p and all(abs(math.log(yv / kv)) >= 0.035 for kv, *_ in kept):
            kept.append((yv, text, c, dash, pri))
    for yv, text, c, dash, _ in kept:
        md = pd.DataFrame([{"y": yv * sup * k, "label": text}])
        layers += [alt.Chart(md).mark_rule(color=c, strokeDash=dash, strokeWidth=1.5 if dash else 1).encode(y="y:Q"),
                   alt.Chart(md).mark_text(align="right", baseline="bottom", dx=-4, dy=-2, color=TEXT, fontSize=11).encode(
                       y="y:Q", text="label:N", x=alt.datum(xmax))]
    return (alt.layer(*layers).properties(width=CHART_W, height=380)
            .configure(background="transparent", font="Inter, system-ui, sans-serif")
            .configure_view(strokeWidth=0).configure_axis(**AXIS))


def risk_section(a):
    """How likely a crash is while you sleep, from three angles."""
    cal, q, p = a["calib"], a["analogs"], a["model_p"]
    if cal is None and q is None and p is None:
        return
    hours = f"{a['sleep_h']:g} hours"
    section("How likely is a crash while you sleep?", f"A crash here means the price falls 30% or more within {hours}.")
    items = []
    if p is not None:
        items.append(("Our model", f"{p * 100:.0f}%", "neg" if p >= 0.35 else "warn-t" if p >= 0.15 else "pos",
                      f"{risk_word(p)} (under 15% = low, over 35% = high)"))
    if q is not None:
        items.append(("Similar past moments", f"{q['n30']} of {q['n']}", "neg" if q["p30"] >= 0.35 else
                      "warn-t" if q["p30"] >= 0.15 else "pos", "crashed within 8 hours"))
    vt = ((a["model"] or {}).get("verdict_test") or {}).get(a["mode"])
    if vt and vt[a["level"]]["p30"] is not None:
        r = vt[a["level"]]["p30"]
        items.append((f"Past '{a['verdict']}' verdicts", f"{r * 100:.0f}%", "neg" if r >= 0.35 else "warn-t" if r >= 0.15
                      else "pos", "were followed by a crash (tested on the sample coins)"))
    tiles(items, "two" if len(items) == 2 else "")
    with how():
        m = a["model"] or {}
        bin_ = next((b for b in m.get("calibration", []) if p is not None and b["lo"] <= p < b["hi"]), None)
        ui('<ul class="hl-list">'
           '<li class="info"><b>Our model</b> looks at price action, big holders, holder profits, trading activity, coin age '
           'and liquidity, and learned from 24 sample coins what usually comes before a crash.'
           + (f' When it said {b_lo(bin_)}–{b_hi(bin_)}% in tests, a crash really followed {bin_["rate"] * 100:.0f}% of the time.'
              if bin_ and bin_["rate"] is not None and bin_["n"] >= 30 else "") + '</li>'
           '<li class="info"><b>Similar past moments</b>: we searched every past hour of the sample coins for the 30 moments '
           'that look most like right now, and counted how many crashed in the next 8 hours.</li>'
           f'<li class="info"><b>Past verdicts</b>: how often a crash followed when this tool said {h(a["verdict"])} on coins '
           'it had not seen before.</li>'
           '<li class="info">The verdict uses the model: under 15% → OK, 15–35% → CAUTION, over 35% → AVOID. A strong danger '
           'sign always makes it at least CAUTION.</li></ul>')
    if p is not None:
        band = 0 if p < 0.15 else 1 if p < 0.35 else 2
        txt = f"<b>Crash risk is {risk_word(p)}</b> ({p * 100:.0f}%)."
        if q is not None:
            qb = 0 if q["p30"] < 0.15 else 1 if q["p30"] < 0.35 else 2
            if qb > band:
                txt += (f" But {q['n30']} of the 30 most similar past moments did crash, so the real risk may be higher than "
                        "the model says. Use a smaller amount.")
                band = max(band, 1)
            elif qb < band:
                txt += f" Similar past moments look calmer ({q['n30']} of 30 crashed)."
            else:
                txt += " Similar past moments agree."
        conclude(txt, LEVEL_CLASS[band])


def b_lo(b):
    return f"{b['lo'] * 100:.0f}"


def b_hi(b):
    return f"{min(b['hi'], 1) * 100:.0f}"


def reasons_section(a):
    section("What pushes the risk up or down?")
    rules = [("bad", f"❌ {r}") for r in a["reasons"] if not r.startswith("Our model gives")] + \
            [("warn", f"⚠️ {r}") for r in a["minor_reasons"] if not r.startswith("Our model gives")]
    up = [("warn", f"⬆ {x[0].upper() + x[1:]}") for x in a["factors"]]
    down = [("good", f"⬇ {x[0].upper() + x[1:]}") for x in a["calm_factors"]]
    if rules:
        ui('<p class="hl-small" style="margin:0 0 2px"><b>Danger signs</b> (❌ strong: one is enough for AVOID · ⚠️ warning: '
           'two or more = CAUTION)</p>')
        bullets(rules)
    else:
        bullets([("good", "✅ No danger sign is active right now.")])
    if up:
        ui('<p class="hl-small" style="margin:8px 0 2px"><b>Pushing the risk up</b></p>')
        bullets(up)
    if down:
        ui('<p class="hl-small" style="margin:8px 0 2px"><b>Keeping the risk down</b></p>')
        bullets(down)
    for s in a["similar"]:
        ui(f'<p class="hl-small">This situation ({h(s["desc"])}) happened {s["n"]} times in the sample coins: usually price '
           f'dipped to {pct(s["median_dd"])} in the next 8 hours, and it crashed 30%+ in {s["p30"] * 100:.0f}% of cases.</p>')
    parts = []
    if rules:
        parts.append(f"{len(rules)} danger sign{'s' if len(rules) > 1 else ''} on")
    if a["factors"]:
        parts.append(f"biggest worry: {h(a['factors'][0])}")
    if a["calm_factors"]:
        parts.append(f"main comfort: {h(a['calm_factors'][0])}")
    txt = (". ".join(p_[0].upper() + p_[1:] for p_ in parts) if parts else "Nothing stands out") + "."
    conclude(txt[0].upper() + txt[1:], LEVEL_CLASS[a["level"]])


def move_section(a):
    """How far the price usually moves while you sleep."""
    mv = a.get("move") or []
    if not mv:
        return
    hours = f"{a['sleep_h']:g} hours"
    section("How far can the price move while you sleep?",
            f"The lowest and highest price during {hours}, measured over many past windows.")
    rows = []
    for m in mv:
        d, u = m["down"], m["up"]
        rows.append([m["source"], f"-{d[50]}% / +{u[50]}%", f"-{d[80]}% / +{u[80]}%", f"-{d[90]}% / +{u[90]}%",
                     f"{m['n']:,}"])
    table(["Based on", "Half of the nights", "8 of 10 nights", "9 of 10 nights", "Windows"], rows)
    ui('<p class="hl-small">Read it like this: "in 8 of 10 nights, the price did not fall more than X% and did not rise '
       'more than Y% at any point during the night". A range narrower than that often ends up out of range.</p>')
    m = mv[0]
    d, u = m["down"], m["up"]
    txt = (f"In 8 of 10 past {a['sleep_h']:g}-hour windows ({m['source'].lower()}), the price stayed between <b>-{d[80]}%</b> and "
           f"<b>+{u[80]}%</b>. In the worst 1 of 10 it fell more than {d[90]}%.")
    if a["range_pct"] > 0:
        inside = a["range_pct"] >= d[80]
        txt += (f" Your lower bound of -{a['range_pct']:g}% " + ("is below the usual drop." if inside else
                                                                  "is above the usual drop, so price will often fall through it."))
    conclude(txt, "warn" if d[80] >= 30 else "info")


def holders_section(a):
    """The biggest wallets: how much they hold, what they paid, and what they did lately."""
    top, hs = a.get("holders") or [], a.get("holder_summary")
    if not top:
        return
    usd, qu, S, t = a["usd"], a["quote"], a["supply"], a["now"]["t"]
    section("Who holds the coin?", "The 10 biggest wallets (pools and contracts not counted). Big holders sitting on big "
                                   "profits are the ones who can crash the price.")
    rows = []
    for i, x in enumerate(top, 1):
        last = x["last"]
        if last:
            age = (t - last[0]) / 3600
            word = {"buy": "bought", "sell": "sold", "xin": "received", "recv": "received", "xout": "sent out"}[last[1]]
            amt = last[2] / S * 100
            amt = f"{amt:.2f}%" if amt >= 0.01 else "<0.01%"
            lm = f"{word} {amt} · " + (f"{age:.0f}h ago" if age >= 1 else "<1h ago")
        else:
            lm = "—"
        prof = ("got it free", "warn-t") if x["free"] else ((f"{x['x']:.1f}x", "neg" if x["x"] >= 3 else "") if x["x"] else "—")
        c24 = x["chg24"] * 100
        rows.append([str(i), f"{x['wallet'][:6]}…{x['wallet'][-4:]}", f"{x['share'] * 100:.2f}%",
                     money(x["entry"] * S, usd, qu) if x["entry"] else "—", prof, lm,
                     (f"{c24:+.2f}%", "neg" if c24 <= -0.05 else "pos" if c24 >= 0.05 else "") if abs(c24) >= 0.005 else "—"])
    table(["#", "Wallet", "Holds", "Bought at (mcap)", "Profit now", "Last move", "Change 24h"], rows)
    ui('<p class="hl-small">"Holds" = share of all coins. "Bought at" = the market cap at their average buy price. "Profit '
       'now" = how many times their buy price the coin is worth today ("got it free" = never bought: airdrop, claim or a '
       'transfer from another wallet). "Change 24h" = how much of all coins they bought (+) or sold (-) in 24 hours.</p>')
    w = (a.get("levels") or {}).get("whale")
    parts = [f"The 10 biggest wallets hold <b>{hs['share'] * 100:.0f}%</b> of all coins, and <b>"
             + of_them(hs["big_profit"], "is up 3x or more", "are up 3x or more") + "</b> (or got their coins free)"]
    if hs["sold6"]:
        parts.append(of_them(len(hs["sold6"]), "sold", "sold") + " in the last 6 hours")
    elif hs["sold24"]:
        parts.append(of_them(len(hs["sold24"]), "sold", "sold") + " in the last 24 hours")
    else:
        parts.append("none of them sold in the last 24 hours")
    if w and w.get("drop10") is not None:
        parts.append(f"if all 10 sold at once, the price would fall about {w['drop10'] * 100:.0f}%")
    elif w and w.get("top10"):
        parts.append("if all 10 sold at once, the price would fall more than 95%")
    risky = hs["share"] >= 0.25 and hs["big_profit"] >= 5
    txt = "; ".join(parts) + "." + (" A few sells from them can crash the price — watch these wallets." if risky else "")
    conclude(txt, "bad" if risky and hs["sold6"] else "warn" if risky or hs["sold6"] else "info")


def flow_section(a):
    """Money coming in (buys) and going out (sells)."""
    fl = a.get("flow") or []
    if not fl:
        return
    k = a["usd"] or 1
    section("Is money coming in or going out?", "All buys and sells of this coin in every pool.")
    rows = []
    for w in fl:
        rows.append([f"Last {w['hours']}h", f"${compact(w['buy'] * k)}", f"${compact(w['sell'] * k)}",
                     (usd_of(w["net"] * k), sign_class(w["net"])), f"{w['buyers']} / {w['sellers']}",
                     f"${compact(w['max_buy'] * k)} / ${compact(w['max_sell'] * k)}",
                     (f"{w['whale_net'] * 100:+.2f}%", sign_class(w["whale_net"])) if abs(w["whale_net"]) >= 0.0001 else "—"])
    table(["", "Bought", "Sold", "Net", "Buyers / sellers", "Biggest buy / sell", "Big wallets net"], rows)
    ui('<p class="hl-small">"Big wallets net" = what wallets that ever held 0.3%+ of all coins bought (+) or sold (-), as a '
       'share of all coins.</p>')
    f1, f4, f24 = (next((w for w in fl if w["hours"] == x), None) for x in (1, 4, 24))
    side = flow_side
    txt = f"Last hour: <b>{side(f1)}</b> ({usd_of(f1['net'] * k)}). Last 4 hours: {side(f4)} ({usd_of(f4['net'] * k)}). "
    txt += f"Last 24 hours: {side(f24)} ({usd_of(f24['net'] * k)})."
    kind = "info"
    if f4["whale_net"] <= -0.005:
        txt += f" Big wallets are selling ({f4['whale_net'] * 100:.2f}% of all coins in 4 hours) — a warning sign."
        kind = "warn"
    elif f4["whale_net"] >= 0.005:
        txt += f" Big wallets are buying ({f4['whale_net'] * 100:+.2f}% of all coins in 4 hours)."
    if f24["whale_net"] <= -0.01 and f4["whale_net"] > -0.005:
        txt += (f" Over 24 hours, big wallets sold {abs(f24['whale_net']) * 100:.1f}% of all coins (net) — they have been taking "
                "profit.")
        kind = "warn"
    if f1["sell"] > 2 * f1["buy"] and f1["sell"] > 0:
        kind = "warn"
    conclude(txt, kind)


def liquidity_section(a):
    """Real LP liquidity around the price (vertical profile) + price floors from price action."""
    lv = a.get("levels")
    if not lv or (not lv["zones"] and not lv["depth"]):
        return
    usd, qu, dep = a["usd"], a["quote"], lv["depth"]
    section("Liquidity and price floors", "How much money sits in the pools around the price, and where buyers stepped in "
                                          "before.")
    items = []
    if dep:
        up = dep.get("up_usd")
        items.append(("info", f"To push the price down 10% / 30% / 50%, someone must sell about ${compact(dep['usd'][10])} / "
                              f"${compact(dep['usd'][30])} / ${compact(dep['usd'][50])}."
                              + (f" To push it up 10% / 30%: buy about ${compact(up[10])} / ${compact(up[30])}."
                                 if up and len(up) > 30 else "")))
    w = lv.get("whale")
    if w and w["drop1"] is not None:
        items.append(("warn" if w["drop1"] >= 0.3 else "info",
                      f"The biggest wallet holds {w['top1'] / a['supply'] * 100:.1f}% of all coins. If it sold everything at "
                      f"once, the price would fall about {w['drop1'] * 100:.0f}%."))
    for a0, a1 in lv["holes"]:
        items.append(("bad", f"Almost no liquidity between {pct(a0)} and {pct(a1)}: if the price gets there, it can fall fast."))
    if dep and dep.get("inconsistent"):
        items.append(("warn", "Part of the liquidity map looked odd (pools with special hooks), so treat these numbers as rough."))
    bullets(items)
    if dep:
        ch, _ = liquidity_profile(a, lv)
        st.altair_chart(ch, theme=None)
        ui('<p class="hl-small">Each bar = money in the pools at that price. <b>Green</b> (below the price) buys from sellers, '
           'so it slows a fall. <b>Purple</b> (above) sells to buyers, so it slows a pump. <b>Grey</b> (right) = how much was '
           'traded at that price in the last 7 days. Green-grey bands = price floors, purple bands = resistance, white dashed '
           + ('line = the lower bound of the safe range (the range runs from there up to "now"). '
              if is_single(a) else 'lines = the safe range, orange dashed line = take profit. ')
           + 'Updated every 15 minutes at most.</p>')
    if lv["zones"]:
        rows = []
        for z in lv["zones"]:
            age = f"{z['age_h']:.0f}h ago (+{z['bounce'] * 100:.0f}%)" if z["age_h"] is not None else "—"
            rows.append([money(z["price"] * a["supply"], usd, qu), (pct(z["drop"]), "neg"),
                         (z["strength"], {"strong": "yes", "medium": "", "weak": "no"}[z["strength"]]), str(z["touches"]), age,
                         f"${compact(z['sell_usd'])}" if z["sell_usd"] is not None else "—"])
        table(["Price floor (mcap)", "How far", "Strength", "Bounces (7 days)", "Last bounce", "Selling needed to get there"],
              rows)
    t = lv.get("test") or {}
    with how("How are price floors found?"):
        r = lambda k: f"{t[k]['rate'] * 100:.0f}%" if t.get(k) and t[k]["rate"] is not None else "—"
        ui(f'<p class="hl-small">A price floor is a ±{ZONE * 100:.0f}% zone where the price bounced 10%+ in the last 7 days, '
           'where a lot of liquidity sits, or where a lot was traded. Strong = 3 or more bounces. In tests on the sample coins, '
           f'when the price came back to a floor within 24 hours, strong floors held {r("strong")} of the time, medium '
           f'{r("medium")}, weak {r("weak")} (a random price level held {r("plain")}). Floors break often in coins that '
           'are about to dump.</p>')
    conclude(*liquidity_conclusion(a, lv))


def liquidity_conclusion(a, lv):
    usd, qu, dep, w = a["usd"], a["quote"], lv["depth"], lv.get("whale")
    parts, kind = [], "info"
    zs = [z for z in lv["zones"] if z["drop"] >= -0.5]
    firm = [z for z in zs if z["strength"] in ("strong", "medium")]
    if firm:
        z = firm[0]
        parts.append(f"the first real floor is at <b>{pct(z['drop'])}</b> (mcap {h(money(z['price'] * a['supply'], usd, qu))}, "
                     f"bounced {z['touches']} time{'' if z['touches'] == 1 else 's'} this week)")
    elif zs:
        parts.append(f"there are only weak floors nearby (closest {pct(zs[0]['drop'])})")
        kind = "warn"
    else:
        parts.append("there is no price floor within -50%")
        kind = "warn"
    if dep and lv["holes"]:
        a0, a1 = lv["holes"][0]
        parts.append(f"between {pct(a0)} and {pct(a1)} there is almost no liquidity, so a fall there can be fast")
        kind = "warn"
    if w and w["drop1"] is not None:
        if w["drop1"] >= 0.3:
            parts.append(f"one wallet alone could push the price down {w['drop1'] * 100:.0f}%")
            kind = "bad" if w["drop1"] >= 0.5 else "warn"
        else:
            parts.append(f"no single wallet can move the price more than {w['drop1'] * 100:.0f}%")
    text = "; ".join(parts) + "."
    return text[0].upper() + text[1:], kind


def range_section(a):
    """Safe LP range from the current price: the tightest range that rarely goes out of range, plus a safety margin."""
    rec, usd, f, qu = a.get("best_range"), a["usd"], a["now"], a["quote"]
    if not rec:
        return range_section_basic(a)
    if (rec.get("safe") or {}).get("mode") == "single":
        return range_section_single(a)
    pick, sf, skip = safe_pick(a)
    hours, pos = f"{a['sleep_h']:g} hours", a["position_usd"]
    tgt = f"{sf['target'] * 100:.0f}%"
    section("Which LP range should you open?",
            f"The tightest range that stayed in range for the whole {hours} in at least {tgt} of similar nights, plus a "
            f"{sf['margin']}% safety margin on both sides.")
    fee_note(a, rec)
    px, fdv = f["px"], f["px"] * a["supply"]
    rng = pick["range"]
    if not sf["feasible"]:
        ui(f'<div class="hl-alert bad">🚨 The price swings too much: no range down to -95% stayed in range in {tgt} of similar '
           'nights. Only a full-range position is safe from going out of range — or skip this coin.</div>')
    elif skip:
        ui('<div class="hl-alert warn">⚠️ <b>At today\'s fees, the safe range lost money on average</b> in similar nights. Waiting '
           'is the better choice. If you still open, use the range below.</div>')
    if rg.is_full(rng):
        lo_tile, hi_tile = ("Lower bound", "none", "", "full range"), ("Upper bound", "none", "", "full range")
    else:
        lo_tile = ("Lower bound (min price)", f"-{rng[0]:g}%", "warn-t",
                   f"{price_str(px * (1 - rng[0] / 100), usd, qu)} · mcap {money(fdv * (1 - rng[0] / 100), usd, qu)}")
        hi_tile = ("Upper bound (max price)", f"+{rng[1]:g}%", "pos",
                   f"{price_str(px * (1 + rng[1] / 100), usd, qu)} · mcap {money(fdv * (1 + rng[1] / 100), usd, qu)}")
    by = " · ".join(f"{k} {v * 100:.0f}%" for k, v in pick["stay_by"].items())
    tiles([lo_tile, hi_tile,
           ("Stays in range all night", stay_txt(pick), "pos" if pick["stay"] >= 0.9 else "warn-t",
            f"touches the lower bound {pick['hit_low'] * 100:.1f}% · {by}"),
           (f"Average result ({hours})", usd_of(pick["mean"] * pos), sign_class(pick["mean"]),
            f"fees {usd_of(pick['fees'] * pos)} · bad night {usd_of(pick['p10'] * pos)}")])
    opt = sf["optimal"]
    if sf["feasible"] and not rg.is_full(rng):
        ui(f'<p class="hl-small" style="margin-bottom:8px">Tightest safe range: <b>{h(opt["label"])}</b> (stayed in range '
           f'{stay_txt(opt)} of nights, touched the lower bound {opt["hit_low"] * 100:.1f}%). We added <b>{sf["margin"]}%</b> on '
           f'both sides ({"10% because the risk or the volatility is higher than usual" if sf["margin"] == 10 else "5% because the risk is low"}). '
           f'You start with about {pick["usdg"] * 100:.0f}% {pair_unit(a)} and {(1 - pick["usdg"]) * 100:.0f}% coin.</p>')
    names = {"margin5": "With 5% margin", "margin10": "With 10% margin", "optimal": "Tightest safe (no margin)",
             "usual": "Your usual lower bound", "mine": "Your plan"}
    rows, seen = [], set()
    for c in sf["rows"]:
        if c["range"] in seen:
            continue
        seen.add(c["range"])
        name = (f"✓ Recommended ({sf['margin']}% margin)" if c is pick else names.get(c["tag"], ""))
        rows.append(range_row(name, c, pos))
    hi_name = "Highest average" + (" (often out of range)" if rec["best"].get("stay", 1) < 0.75 else "")
    for name, c in ((hi_name, rec["best"]), ("Full range", rec["full"])):
        if c["range"] not in seen and "stay" in c:
            seen.add(c["range"])
            rows.append(range_row(name, c, pos))
    table(["", "Range", "Stays in range", "Hits the bottom", "Average", "Bad night", "Fees", f"{pair_unit(a)} at start"], rows)
    ui(f'<p class="hl-small">"Stays in range" = the price never left the range during the whole {hours} (the lower of: '
       f'this coin\'s own history and similar moments in other coins). "Hits the bottom" = the price touched the lower bound. '
       f'"Bad night" = the worst 1 in 10 nights. Dollar amounts are for your {dollars(pos)}, averaged over similar past '
       'nights. Wider ranges go out of range less often but earn fewer fees.</p>')
    with st.expander("Range map: average result of every lower × upper bound"):
        st.altair_chart(range_map(rec), theme=None, width="stretch")
        ui('<p class="hl-small">Each square = how that range did on average in similar moments (blue = made money, red = lost). '
           'Big losses count extra. The outlined square has the highest average, but narrow ranges like that go out of range '
           'often.</p>')
    near = [z for z in (a.get("levels") or {}).get("zones", [])
            if not rg.is_full(rng) and -rng[0] / 100 <= z["drop"] < 0 and z["strength"] in ("strong", "medium")]
    if near:
        ui('<p class="hl-small">Price floors inside this range: ' + ", ".join(
            f"{pct(z['drop'])} ({z['strength']})" for z in near) + ' — bounces there keep the position earning.</p>')
    with how("How was this tested?"):
        ui(f'<p class="hl-small">{safe_test_note(rec) or ""} {range_test_note(rec) or ""}</p>')
    conclude(*range_conclusion(a, rec))


def fee_note(a, rec):
    fy, pos = rec["fee"], a["position_usd"]
    if fy["rate"] is not None and not fy["assumed"]:
        fee_txt = (f"This pool takes a {fee_pct(fy['rate'])} fee per trade. ${compact(fy['vol24'])} was traded in 24 hours and the "
                   f"pools hold ${compact(fy['tvl'])}, so a full-range LP earns about <b>{fy['r'] * 100:.2f}% a day</b>.")
    else:
        fee_txt = f"We assumed a full-range LP earns about <b>{fy['r'] * 100:.2f}% a day</b>."
    ui(f'<div class="hl-note">{fee_txt} Your {dollars(pos)} would be {fy["share"] * 100:.2f}% of the pool'
       + (" — big enough to shrink your own share of the fees" if fy["share"] > 0.05 else "") + "."
       + "".join(f" Note: {h(n)}." for n in fy["notes"]) + "</div>")


def range_section_single(a):
    """Single-sided USDG: the range runs from today's price (0%) down to the lower bound. Only the lower bound is chosen:
    the closest one the price did not reach during the night in >= target of similar nights, plus a safety margin."""
    rec, usd, f, qu = a["best_range"], a["usd"], a["now"], a["quote"]
    pick, sf, skip = safe_pick(a)
    hours, pos = f"{a['sleep_h']:g} hours", a["position_usd"]
    tgt = f"{sf['target'] * 100:.0f}%"
    section("How deep should your range go?",
            f"Single-sided USDG: your range runs from today's price (0%) down to the lower bound. We look for the closest lower "
            f"bound the price did not fall to during the whole {hours} in at least {tgt} of similar nights, then go "
            f"{sf['margin']}% deeper for safety.")
    ui('<div class="hl-note"><b>How single-sided USDG works.</b> At the start your position is 100% USDG and holds no coin. '
       'If the price falls into your range, your USDG buys the coin bit by bit — that is when you earn fees, and also where a '
       'loss can come from. If the price falls below the lower bound you hold only coin and stop earning. If the price goes '
       '<b>up</b>, nothing happens: you stay 100% USDG, no loss and no fees. That is why the <b>upper bound is 0%</b> (today\'s '
       'price) and only the lower bound matters.</div>')
    fee_note(a, rec)
    px, fdv = f["px"], f["px"] * a["supply"]
    lo = pick["range"][0]
    if not sf["feasible"]:
        ui(f'<div class="hl-alert bad">🚨 The price swings too much: even at -95% the price fell to the bottom in more than '
           f'{100 - sf["target"] * 100:.0f}% of similar nights. Skipping this coin is safer.</div>')
    elif skip:
        ui('<div class="hl-alert warn">⚠️ <b>At today\'s fees, the safe range lost money on average</b> in similar nights. Waiting '
           'is the better choice. If you still open, use the range below.</div>')
    by = " · ".join(f"{k} {v * 100:.0f}%" for k, v in pick["stay_by"].items())
    tiles([("Lower bound (min price)", f"-{lo:g}%", "warn-t",
            f"{price_str(px * (1 - lo / 100), usd, qu)} · mcap {money(fdv * (1 - lo / 100), usd, qu)}"),
           ("Upper bound", "0%", "", f"today's price · {price_str(px, usd, qu)} · mcap {money(fdv, usd, qu)}"),
           ("Never hits the bottom", stay_txt(pick), "pos" if pick["stay"] >= 0.9 else "warn-t",
            f"hits it {pick['hit_low'] * 100:.1f}% · {by}"),
           (f"Average result ({hours})", usd_of(pick["mean"] * pos), sign_class(pick["mean"]),
            f"fees {usd_of(pick['fees'] * pos)} · bad night {usd_of(pick['p10'] * pos)}")])
    opt = sf["optimal"]
    if sf["feasible"]:
        ui(f'<p class="hl-small" style="margin-bottom:8px">Closest safe lower bound: <b>-{opt["range"][0]:g}%</b> (the price fell '
           f'to it in {opt["hit_low"] * 100:.1f}% of similar nights). We went <b>{sf["margin"]}%</b> deeper '
           f'({"10% because the risk or the volatility is higher than usual" if sf["margin"] == 10 else "5% because the risk is low"}). '
           f'The price was inside your range (earning fees) about {pick["earn"] * 100:.0f}% of the time in those nights; the '
           'rest of the time it was above today\'s price.</p>')
    names = {"margin5": "With 5% margin", "margin10": "With 10% margin", "optimal": "Closest safe (no margin)",
             "usual": "Your usual lower bound", "mine": "Your plan"}
    rows, seen = [], set()
    for c in sf["rows"]:
        if c["range"] in seen:
            continue
        seen.add(c["range"])
        name = (f"✓ Recommended ({sf['margin']}% margin)" if c is pick else
                ("Highest average" if c["mean"] > 0 else "Smallest loss") if c["tag"] == "best" else names.get(c["tag"], ""))
        rows.append(single_row(name, c, pos))
    table(["", "Range", "Hits the bottom", "Earning fees", "Average", "Bad night", "Fees"], rows)
    ui(f'<p class="hl-small">"Hits the bottom" = the price fell to the lower bound at some point during the {hours} (the higher '
       'of: this coin\'s own history and similar moments in other coins); from there you hold only coin. "Earning fees" = share '
       f'of the time the price was inside the range. "Bad night" = the worst 1 in 10 nights. Dollar amounts are for your '
       f'{dollars(pos)}, averaged over similar past nights. A deeper lower bound hits the bottom less often but earns fewer '
       'fees per dollar.</p>')
    with st.expander("Every lower bound from -20% to -90%"):
        table(["Range", "Hits the bottom", "Earning fees", "Average", "Bad night", "Fees"],
              [single_row(None, c, pos)[1:] for c in sf["ladder"]])
    near = [z for z in (a.get("levels") or {}).get("zones", [])
            if -lo / 100 <= z["drop"] < 0 and z["strength"] in ("strong", "medium")]
    if near:
        ui('<p class="hl-small">Price floors inside this range: ' + ", ".join(
            f"{pct(z['drop'])} ({z['strength']})" for z in near) + ' — the price often bounces there, which keeps the '
           'position earning.</p>')
    with how("How was this tested?"):
        ui(f'<p class="hl-small">{single_test_note(rec) or ""}</p>')
    conclude(*single_conclusion(a, rec))


def single_row(name, c, pos):
    return [name, c["label"], (f"{c['hit_low'] * 100:.1f}%", "neg" if c["hit_low"] >= 0.1 else ""),
            f"{c['earn'] * 100:.0f}%", (usd_of(c["mean"] * pos), sign_class(c["mean"])),
            (usd_of(c["p10"] * pos), sign_class(c["p10"])), usd_of(c["fees"] * pos)]


def single_test_note(rec):
    t = rec.get("safe_test_single")
    if not t:
        return None
    r = t["res"]
    m5, m10, opt = r.get("margin5"), r.get("margin10"), r.get("optimal")
    u = [r.get(f"-{x}") for x in (60, 65, 70)]
    return (f"Tested on coins the tool had not seen (each lower bound picked only from the other coins). Aiming for "
            f"{t['target'] * 100:.0f}%, the closest safe lower bound was really hit on {opt['hit_low'] * 100:.1f}% of nights; with a "
            f"5% margin {m5['hit_low'] * 100:.1f}%, with 10% {m10['hit_low'] * 100:.1f}%. Fixed lower bounds of -60 / -65 / -70% "
            f"were hit on {u[0]['hit_low'] * 100:.1f}% / {u[1]['hit_low'] * 100:.1f}% / {u[2]['hit_low'] * 100:.1f}% of nights. "
            f"Average per night at a {t['r'] * 100:g}% daily fee level: margin 5% {m5['mean'] * 100:+.2f}%, margin 10% "
            f"{m10['mean'] * 100:+.2f}%, -65% {u[1]['mean'] * 100:+.2f}%; bad night (1 in 10): {m5['p10'] * 100:+.1f}% / "
            f"{m10['p10'] * 100:+.1f}% / {u[1]['p10'] * 100:+.1f}%. Deeper ranges lose less on a bad night but earn less, so the "
            "averages end up close.")


def single_conclusion(a, rec):
    pick, sf, skip = safe_pick(a)
    mine, hours, pos = sf["rows"], f"{a['sleep_h']:g} hours", a["position_usd"]
    if not sf.get("feasible"):
        return "The price swings too much: even a -95% lower bound gets hit too often. Skipping is safer.", "bad"
    txt = (f"Open <b>{h(pick['label'])}</b> (upper bound 0% = today's price). In {stay_txt(pick)} of similar nights the price "
           f"never fell to the bottom of it. Average {usd_of(pick['mean'] * pos)} over {hours}, bad night "
           f"{usd_of(pick['p10'] * pos)}.")
    usual = sorted((c for c in sf["rows"] if c["tag"] == "usual"), key=lambda c: c["range"][0])
    if usual:
        txt += (" Your usual -" + " / -".join(f"{c['range'][0]:g}" for c in usual) + "% were hit on "
                + " / ".join(f"{c['hit_low'] * 100:.1f}%" for c in usual) + " of nights, with "
                + " / ".join(usd_of(c["fees"] * pos) for c in usual) + f" in fees (recommended: {usd_of(pick['fees'] * pos)}).")
    own = next((c for c in mine if c["tag"] == "mine"), None)
    if own:
        txt += f" Your plan ({h(own['label'])}) was hit on {own['hit_low'] * 100:.1f}% of nights."
    if skip:
        return txt + " At today's fees it lost money on average, so waiting is better.", "warn"
    if a["level"] == 2:
        return txt + " But the verdict is AVOID, so the safest choice is still not to open.", "bad"
    return txt, "good" if pick["mean"] > 0 else "warn"


def range_row(name, c, pos):
    return [name, c["label"], (stay_txt(c), "pos" if c["stay"] >= 0.9 else "warn-t" if c["stay"] >= 0.75 else "neg"),
            (f"{c['hit_low'] * 100:.1f}%", "neg" if c["hit_low"] >= 0.1 else ""),
            (usd_of(c["mean"] * pos), sign_class(c["mean"])), (usd_of(c["p10"] * pos), sign_class(c["p10"])),
            usd_of(c["fees"] * pos), f"{c['usdg'] * 100:.0f}%"]


def safe_test_note(rec):
    t = rec.get("safe_test")
    if not t:
        return None
    r = t["res"]
    m5, m10, opt = r.get("margin5"), r.get("margin10"), r.get("optimal")
    u = [r.get(f"-{x}") for x in (60, 65, 70)]
    return (f"Safe ranges were tested on coins the tool had not seen (each range picked only from the other coins). "
            f"Aiming for {t['target'] * 100:.0f}%, the tightest range really stayed in range {opt['stay'] * 100:.1f}% of nights "
            f"and touched the lower bound {opt['hit_low'] * 100:.1f}%; with a 5% margin {m5['stay'] * 100:.1f}% / "
            f"{m5['hit_low'] * 100:.1f}%; with 10% {m10['stay'] * 100:.1f}% / {m10['hit_low'] * 100:.1f}%. Lower bounds of "
            f"-60 / -65 / -70% touched the bottom on {u[0]['hit_low'] * 100:.1f}% / {u[1]['hit_low'] * 100:.1f}% / "
            f"{u[2]['hit_low'] * 100:.1f}% of nights.")


def fee_pct(rate):
    return f"{rate * 100:.2f}".rstrip("0").rstrip(".") + "%"


def range_test_note(rec):
    test = rec.get("test")
    if not test:
        return None
    t = min(test, key=lambda x: abs(math.log(x["r"] / max(rec["r"], 1e-4))) + abs(math.log(x["share"] / max(rec["share"], 1e-4))) * 0.5)
    return (f"For comparison, the range with the highest average (often out of range) did better than full range on "
            f"{t['beats_full']} of {t['coins']} unseen coins at a {t['r'] * 100:g}% daily fee level.")


def range_conclusion(a, rec):
    pick, sf, skip = safe_pick(a)
    mine, hours, pos = rec["mine"], f"{a['sleep_h']:g} hours", a["position_usd"]
    if not sf.get("feasible"):
        return ("The price swings too much for a normal range: only full range stays in range reliably. Skipping is "
                "safer."), "bad"
    txt = (f"Safe range <b>{h(pick['label'])}</b>: it stayed in range the whole night in {stay_txt(pick)} of similar nights and "
           f"touched the lower bound in {pick['hit_low'] * 100:.1f}%. Average {usd_of(pick['mean'] * pos)} over {hours}, "
           f"bad night {usd_of(pick['p10'] * pos)}.")
    usual = sorted((c for c in sf["rows"] if c["tag"] == "usual"), key=lambda c: c["range"][0])
    if usual:
        txt += (" Your usual lower bounds " + " / ".join(f"-{c['range'][0]:g}%" for c in usual) + " touched the bottom on "
                + " / ".join(f"{c['hit_low'] * 100:.1f}%" for c in usual) + " of nights, with "
                + " / ".join(usd_of(c["fees"] * pos) for c in usual) + f" in fees (safe range: {usd_of(pick['fees'] * pos)}).")
    if mine and mine["range"] != pick["range"] and "stay" in mine:
        txt += f" Your plan ({h(mine['label'])}) stayed in range {stay_txt(mine)} of nights."
    if skip:
        return txt + " At today's fees it lost money on average, so waiting is better.", "warn"
    if a["level"] == 2:
        return txt + " But the verdict is AVOID, so the safest choice is still not to open.", "bad"
    return txt, "good" if pick["mean"] > 0 else "warn"


def res_why(z, usd):
    """Short reasons why a level is a resistance."""
    why = []
    if z["touches"]:
        why.append(f"pushed back {z['touches']}x this week")
    if z.get("flips"):
        why.append(f"{z['flips']} old floor{'s' if z['flips'] > 1 else ''} the price fell through")
    if z["ath"]:
        why.append("all-time high")
    if (z.get("under") or 0) >= 0.01:
        why.append(f"{z['under'] * 100:.1f}% of coins at break-even")
    if (z.get("wall_ratio") or 0) >= 2:
        why.append("sell wall in the pools")
    if z.get("vol_share", 0) >= 0.05:
        why.append("busy trading price")
    return ", ".join(why) or "high-volume or holder level"


def reach_txt(z):
    parts = [f"{v * 100:.0f}% {k}" for k, v in (("this coin", z.get("reach_own")), ("similar", z.get("reach_peer"))) if v is not None]
    return " · ".join(parts) or "—"


def exit_section(a):
    """Where to take profit: strong resistance above the price, tested on the sample coins, plus a momentum check."""
    e = a.get("exits")
    if not e:
        return
    if e.get("single"):
        return None if a.get("open_pos") else exit_section_single(a, e)
    usd, qu = a["usd"], a["quote"]
    hours = f"{a['sleep_h']:g} hours"
    section("Where should you take profit?",
            "Resistance = a price where the price was pushed back before, where many holders get their money back, or where "
            "sellers wait in the pools. Taking profit there avoids giving the gains back.")
    take, zones = e["take"], e["zones"]
    if not zones:
        ui('<div class="hl-alert info">No resistance found above the price (up to +300%). There is no clear exit level: rely '
           'on your upper bound and the alarm.</div>')
        return
    mom = e["momentum"]
    ui(f'<div class="hl-note">Momentum now: <b>{"strong" if mom else "calm"}</b>. '
       + ("In tests, when the price ran into a level with strong momentum (RSI 70+ or volume 2x), medium and weak levels "
          "usually broke; only strong levels still held about 4 times in 10." if mom else
          "In tests, when the price arrived calmly, strong and medium levels both turned it back about 4 times in 10.")
       + "</div>")
    if take:
        z = take["zone"]
        hold = f"{z['hold'] * 100:.0f}%" if z.get("hold") is not None else "—"
        drop = ((e.get("test") or {}).get(z["strength"]) or {}).get("all", {}).get("drop")
        tiles([("Take profit at", f"+{take['up'] * 100:.0f}%", "pos",
                f"mcap {money(take['price'] * a['supply'], usd, qu)} · just under the +{z['up'] * 100:.0f}% resistance"),
               ("Strength", z["strength"], "pos" if z["strength"] == "strong" else "warn-t" if z["strength"] == "medium" else "",
                res_why(z, usd)),
               ("Turned the price back in tests", hold, "", f"then it fell ~{drop * 100:.0f}% (typical)" if drop else ""),
               (f"Chance to reach in {hours}", f"{(z.get('reach') or 0) * 100:.0f}%", "", reach_txt(z))])
        pick = safe_pick(a)[0]
        ub = take["upper_bound"]
        plan = [("good", f"Automatic exit: set your upper bound at +{ub}% (just under this level). If the price climbs into "
                         f"the resistance, your LP sells the coin for {pair_unit(a)} on the way up — profit is locked even "
                         "while you sleep.")]
        if pick and not rg.is_full(pick["range"]) and pick["range"][1] > ub:
            plan[-1] = (plan[-1][0], plan[-1][1] + f" The safe range goes up to +{pick['range'][1]:g}%; lowering it to +{ub}% "
                                                   "earns a bit more fees per dollar but leaves no room above the resistance.")
        plan.append(("info", f"Or close the LP by hand when the price reaches {price_str(take['price'], usd, qu)} "
                             f"(mcap {money(take['price'] * a['supply'], usd, qu)})."))
        nx = e.get("next")
        if nx:
            plan.append(("info", f"If the price closes 5%+ above it, the level is broken. Next resistance: +{nx['up'] * 100:.0f}% "
                                 f"(mcap {money(nx['price'] * a['supply'], usd, qu)}, {nx['strength']})."))
        fl = e.get("floor")
        if fl:
            plan.append(("warn", f"Exit early on the way down if the price breaks the floor at {pct(fl['drop'])} (mcap "
                                 f"{money(fl['price'] * a['supply'], usd, qu)}), or when the alarm says AVOID."))
        if e.get("whales_selling"):
            plan.append(("bad", "Big holders are selling right now (0.5%+ of all coins in 4 hours): take profit earlier rather "
                                "than later."))
        bullets(plan)
    resistance_details(a, e)
    conclude(*exit_conclusion(a, e))


def resistance_details(a, e):
    """Resistance table, technical check and test notes (used by both position types)."""
    usd, qu, zones = a["usd"], a["quote"], e["zones"]
    rows = []
    for z in zones:
        rows.append([money(z["price"] * a["supply"], usd, qu), (f"+{z['up'] * 100:.0f}%", "pos"),
                     (z["strength"], {"strong": "yes", "medium": "", "weak": "no"}[z["strength"]]), str(z["touches"]),
                     f"{z['under'] * 100:.1f}%" if z.get("under") is not None else "—",
                     f"${compact(z['wall'])}" if z.get("wall") is not None else "—",
                     f"${compact(z['buy_usd'])}" if z.get("buy_usd") is not None else "—",
                     reach_txt(z), f"{z['hold'] * 100:.0f}%" if z.get("hold") is not None else "—"])
    table(["Resistance (mcap)", "How far", "Strength", "Push-backs (7d)", "At break-even", "Pool sellers", "Buy to reach",
           f"Reach ({a['sleep_h']:g}h)", "Held in tests"], rows)
    ui('<p class="hl-small">Each resistance is a ±4% zone around the level shown. "At break-even" = share of all coins held '
       'by wallets whose average buy price sits in this zone (they are at a loss now and often sell when they get their money '
       'back). "Pool sellers" = LP liquidity in the zone that sells to buyers. "Buy to reach" = dollars of buying needed to '
       'push the price there. "Reach" = how often the price touched this level during a night like this (this coin\'s own '
       'history and similar moments in other coins).</p>')
    sig = e["signals"]
    srows = []
    for x in sig:
        t = x.get("test") or {}
        on, off = (t.get("on") or {}).get("rate"), (t.get("off") or {}).get("rate")
        eff = ("—" if on is None or off is None else
               f"held {on * 100:.0f}% vs {off * 100:.0f}% → " + ("level breaks more often" if on < off - 0.03 else
                                                                 "level holds more often" if on > off + 0.03 else "no clear effect"))
        srows.append([x["text"], ("✓ now" if x["on"] else "—", "yes" if x["on"] else "no"), eff])
    with st.expander("Technical check: will the resistance hold?"):
        table(["Signal", "Now", "In tests, when this signal was on at the resistance"], srows)
        ui('<p class="hl-small">Surprising but consistent: "overbought" signals (RSI 70+, volume spike, above the upper band) '
           'meant the price was more likely to <b>break through</b> resistance, not reverse. Momentum carries price through '
           'weaker levels. Use these to judge whether a level will hold, not as a sell signal on their own.</p>')
    with how("How are resistances found and tested?"):
        rt = e.get("test") or {}
        def cell(k, m):
            x = (rt.get(k) or {}).get(m) or {}
            return f"{x['rate'] * 100:.0f}%" if x.get("rate") is not None else "—"
        table(["Level", "Calm arrival", "With momentum", "All"],
              [[k.capitalize() if k != "plain" else "Random level (no resistance)", cell(k, "calm"), cell(k, "momentum"),
                cell(k, "all")] for k in ("strong", "medium", "weak", "plain")])
        ui('<p class="hl-small">Resistance levels come from: prices that turned the price down 10%+ in the last 7 days, old '
           'floors the price has fallen through (a floor often becomes a ceiling), the all-time high, sell walls in the pools, '
           'busy trading prices, and prices where many holders bought (they sell at break-even). Strong = pushed back 3+ times, '
           'or 1–2 times with 3%+ of all coins at break-even there or 2+ old floors there. Tested on the '
           '24 sample coins every 2 hours: a level "held" when the price touched it within 24 hours, did not go more than 5% '
           'above it in the next 6 hours, and fell 5%+.</p>')


def exit_section_single(a, e):
    """Single-sided USDG: nothing to sell above today's price. The exit is automatic when the price comes back to today's
    price; floors inside the range are where a bounce may stall; resistance above only matters for a two-sided position."""
    usd, qu, sup = a["usd"], a["quote"], a["supply"]
    px = e["auto_exit"]
    section("When does your position exit?",
            "With only USDG there is nothing to sell above today's price. What matters is what happens if the price dips into "
            "your range and comes back.")
    stall = e.get("stall") or []
    strong = [z for z in stall if z["strength"] in ("strong", "medium")]
    tiles([("Automatic exit at", "0%", "pos", f"today's price · {price_str(px, usd, qu)} · mcap {money(px * sup, usd, qu)}"),
           ("Bounce may stall at", pct(strong[0]["drop"]) if strong else "—", "warn-t" if strong else "",
            f"{strong[0]['strength']} floor · mcap {money(strong[0]['price'] * sup, usd, qu)}" if strong else
            "no strong floor inside your range"),
           ("Lower bound", f"-{e['lower']:g}%", "warn-t",
            f"below it you hold only coin · mcap {money(px * (1 - e['lower'] / 100) * sup, usd, qu)}")])
    plan = [("good", f"Automatic exit: if the price dips into your range and then climbs back to today's price "
                     f"({price_str(px, usd, qu)}, mcap {money(px * sup, usd, qu)}), your LP sells the coin back into USDG on the "
                     "way up. Back at 0% you are 100% USDG again, with the fees on top — even while you sleep.")]
    if stall:
        plan.append(("info", "On the way back up, a floor the price fell through often turns into a ceiling (in tests on the "
                             "sample coins, levels with 2+ broken floors turned the price back about 45% of the time, versus "
                             "28% for a random level). Floors inside your range: " + ", ".join(
                                 f"{pct(z['drop'])} ({z['strength']}, mcap {money(z['price'] * sup, usd, qu)})" for z in stall)
                     + ". If the bounce stops under one of them and you don't want to wait, close the LP there by hand "
                       "(you get back part USDG, part coin)."))
    plan.append(("warn", f"If the price falls through the lower bound (-{e['lower']:g}%) you hold only coin and stop earning. "
                         "Decide then: sell the coin, or wait for a bounce back into the range. Close early when the alarm "
                         "says AVOID."))
    if e.get("whales_selling"):
        plan.append(("bad", "Big holders are selling right now (0.5%+ of all coins in 4 hours): a dip into your range is more "
                            "likely tonight."))
    bullets(plan)
    if e.get("zones"):
        with st.expander("Resistance above today's price (only matters for a two-sided position)"):
            ui('<p class="hl-small">Your single-sided position holds no coin above today\'s price, so these levels do not '
               'affect it. They matter if you open two-sided (USDG + coin): then the upper bound is where your coin is sold.</p>')
            if e.get("take"):
                tk = e["take"]
                ui(f'<p class="hl-small">Two-sided take-profit: around <b>+{tk["up"] * 100:.0f}%</b> (mcap '
                   f'{money(tk["price"] * sup, usd, qu)}), just under the {tk["zone"]["strength"]} resistance at '
                   f'+{tk["zone"]["up"] * 100:.0f}%.</p>')
            resistance_details(a, e)
    txt = (f"Your exit is automatic at today's price (mcap {h(money(px * sup, usd, qu))}): any coin bought on a dip is sold "
           "back into USDG when the price returns.")
    if strong:
        txt += (f" If a bounce stalls, the first spot to watch is the {strong[0]['strength']} floor at {pct(strong[0]['drop'])} "
                f"(mcap {h(money(strong[0]['price'] * sup, usd, qu))}).")
    txt += f" Below -{e['lower']:g}% you hold only coin."
    conclude(txt, "good")


def exit_conclusion(a, e):
    usd, qu = a["usd"], a["quote"]
    take = e["take"]
    if not take:
        return "No resistance strong and far enough to aim for. Use your upper bound and the alarm as the exit.", "info"
    z = take["zone"]
    txt = (f"Take profit around <b>+{take['up'] * 100:.0f}%</b> (mcap {h(money(take['price'] * a['supply'], usd, qu))}), just "
           f"under the {z['strength']} resistance at +{z['up'] * 100:.0f}% ({h(res_why(z, usd))}). Set the upper bound at +{take['upper_bound']}% for an "
           f"automatic exit. It is reached in about {(z.get('reach') or 0) * 100:.0f}% of nights like this; when reached, "
           f"such levels turned the price back about {(z.get('hold') or 0) * 100:.0f}% of the time.")
    if e["momentum"] and z["strength"] != "strong":
        txt += " Momentum is strong now, so this level may break: watch the next one."
    fl = e.get("floor")
    if fl:
        txt += f" On the way down, exit if the floor at {pct(fl['drop'])} breaks."
    return txt, "good" if z["strength"] == "strong" else "info"


def range_section_basic(a):
    """Recommended LP range from the current price (lower and upper bound)."""
    ra, plan, usd, f, qu = a["range_advice"], a.get("range_plan"), a["usd"], a["now"], a["quote"]
    section("Recommended LP range", f"From the current price, for a position you leave alone for {a['sleep_h']:g} hours.")
    if not plan or ra.get("p80_round") is None:
        ui('<div class="hl-alert warn">Not enough data yet to recommend a range.</div>')
        return
    px, fdv = f["px"], f["px"] * a["supply"]
    lo, hi = plan["floor"], plan["ceiling"]
    inside = plan["inside"]
    tiles([("Lower bound (min price)", f"−{lo}%", "warn-t", f"{price_str(px * (1 - lo / 100), usd, qu)} · mcap {money(fdv * (1 - lo / 100), usd, qu)}"),
           ("Upper bound (max price)", f"+{hi}%", "pos", f"{price_str(px * (1 + hi / 100), usd, qu)} · mcap {money(fdv * (1 + hi / 100), usd, qu)}"),
           ("Stayed inside this range", f"{inside * 100:.0f}%" if inside is not None else "—", "",
            f"of similar {a['sleep_h']:g}h windows" + ("" if plan["own"] else " (estimate)")),
           ("Current price", price_str(px, usd, qu), "", f"mcap {money(fdv, usd, qu)}")])
    items = []
    if plan["floor_why"] == "support" and plan["support"]:
        z = plan["support"]
        items.append(("info", f"Lower bound sits just below the {z['strength']} support zone at {pct(z['drop'])} (mcap "
                              f"{money(z['price'] * a['supply'], usd, qu)}): if price bounces there, your position stays active. "
                              f"Historically, in 8 of 10 similar cases price did not fall more than {ra['p80_round']}% within "
                              f"{a['sleep_h']:g}h."))
    else:
        items.append(("info", f"Lower bound: in 8 of 10 similar cases, price did not fall more than {lo}% within "
                              f"{a['sleep_h']:g}h."))
    if ra.get("p90_round") and ra["p90_round"] > lo:
        items.append(("info", f"Extra-safe lower bound: −{ra['p90_round']}% (held in 9 of 10 cases)."))
    items.append(("info", f"Upper bound: in 8 of 10 similar {a['sleep_h']:g}h windows, price did not rise more than +{hi}%. "
                          f"Above the upper bound your LP turns into 100% {pair_unit(a)} — no loss, it just stops earning fees."))
    res = [r for r in (plan.get("resistance") or []) if 0.05 <= r["up"] <= hi / 100]
    if res:
        r = res[0]
        items.append(("info", f"Price may stall at the {'ATH' if r['ath'] else 'resistance'} {pct(r['up'])} "
                              f"(mcap {money(r['price'] * a['supply'], usd, qu)}), inside your range."))
    items.append(("info", f"If price falls to the lower bound, this LP is worth about {pct(lp_value(-lo / 100, lo, hi) - 1)} "
                          f"(in {pair_unit(a)}) and is fully in the token; below it you hold only the token and lose "
                          "1:1 with the price."))
    if ra["breach"] is not None and a["range_pct"] > 0:
        bad = ra["breach"] > 0.2
        items.append(("bad" if bad else "good",
                      f"Your planned lower bound of -{a['range_pct']:g}%: about {ra['breach'] * 100:.0f}% chance of breaking "
                      "it while you sleep." + (" Too tight to leave overnight." if bad else "")))
    if ra.get("p90_round", 0) >= 70:
        items.append(("warn", "The safe lower bound is very wide. Consider full range, a small position, or skipping this one."))
    if a["level"] == 2:
        items.append(("bad", "The verdict is AVOID: even with this range, opening now is not recommended. A wide range only "
                             "softens the loss; when price falls, your LP turns into the token."))
    bullets(items)
    src = f"this coin's own history ({plan['n']:,} windows)" if plan["own"] else \
        f"{h(short_names(a['ref_names']))} with the same verdict ({plan['n']:,} windows)"
    ui(f'<p class="hl-small">Based on {src}. Lower bound also checked against the reference coins with the same verdict '
       f'({ra["n_peer"]:,} windows); the deeper one is used.</p>')


def analog_section(a):
    q = a.get("analogs")
    if not q:
        return
    section("What happened after similar moments?",
            "We compared right now with every past hour in the sample coins (chart pattern, price moves, indicators, coin "
            "profile) and picked the 30 closest matches.")
    kind = "bad" if q["p30"] >= 0.35 else "warn" if q["p30"] >= 0.15 else "good"
    ui(f'<div class="hl-alert {kind}"><b>{q["n30"]} of the {q["n"]} closest moments</b> (from {q["tokens"]} coins) crashed 30%+ '
       f'within 8 hours. Usually the price dipped to {pct(q["median_dd"])} at its lowest'
       + (f' and reached {pct(q["median_rise"])} at its highest' if q["median_rise"] is not None else "") + '.</div>')
    gs = q["group_sim"]
    names = {"pattern": "Pattern match", "price": "Price-move match", "whale": "Big-holder match", "tech": "Indicator match"}
    tiles([(names[g], f"{gs[g] * 100:.0f}%" if gs.get(g) is not None else "—", "", None) for g in names])
    rows = []
    for e in q["examples"][:6]:
        g = e["groups"]
        rows.append([e["symbol"], when(e["t"]), f"{e['sim'] * 100:.0f}%"]
                    + [f"{g[x] * 100:.0f}%" if g.get(x) is not None else "—" for x in ("pattern", "price", "whale", "tech")]
                    + [(pct(e["dd"]), "neg" if e["dd"] <= -0.3 else ""),
                       (pct(e["rise"]), "pos") if e["rise"] is not None else "—"])
    table(["Coin", "When (UTC)", "Match", "Pattern", "Price", "Big holders", "Indicators", "Lowest next 8h",
           "Highest next 8h"], rows)
    with how():
        ui('<p class="hl-small">Match = how alike that moment was to now (100% = identical), overall and per side. At most one '
           'moment per coin per 12 hours is used, so one event is not counted many times. The big-holder side is shown but '
           'not used for the result, because in tests it made the matches slightly worse.</p>')
    ex = q["examples"][0] if q["examples"] else None
    word = "calm" if q["p30"] < 0.15 else "risky" if q["p30"] >= 0.35 else "mixed"
    txt = (f"Moments like this were <b>{word}</b>: {q['n30']} of 30 crashed within 8 hours, and usually the price dipped to "
           f"{pct(q['median_dd'])}.")
    if ex:
        txt += f" The closest match, {h(ex['symbol'])} on {when(ex['t'])}, went {pct(ex['dd'])} at its lowest."
    if q["sim"] < 0.5:
        txt += " The matches are not very close, so trust this part less."
    conclude(txt, kind)


def comparison(a):
    """This coin vs the reference coins right before their crashes."""
    cmp = a["compare"]
    section("Does it look like a coin right before a crash?",
            "At the top, right before each big crash in the sample coins, we noted which warning signs were showing. "
            "Here they are next to this coin.")
    if not cmp:
        ui('<div class="hl-alert warn">No reference data yet.</div>')
        return
    b = cmp["best"]
    have = [SHORT[c] for c in b["signs"] if c in cmp["now"]]
    miss = [SHORT[c] for c in b["signs"] if c not in cmp["now"]]
    name = f"<b>{h(b['symbol'])} right before its {pct(b['drop'])} crash</b> ({when(b['peak_t'])} UTC)"
    if b["sim"] >= 0.8:
        ui(f'<div class="hl-alert bad">🚨 Looks very much like {name}: {b["match"]} of its {b["total"]} signs are here.</div>')
    elif b["sim"] >= 0.5:
        ui(f'<div class="hl-alert warn">⚠️ Looks partly like {name}: {b["match"]} of its {b["total"]} signs are here.</div>')
    else:
        ui(f'<div class="hl-alert good">👍 Does not look like a coin before a crash. The closest is {name}, with only '
           f'{b["match"]} of its {b["total"]} signs.</div>')

    def cell(code, signs, values):
        return (("✓ " if code in signs else "— ") + values.get(code, ""), "yes" if code in signs else "no")

    def build(crash_list):
        header = ["Sign", "This coin"]
        for c in crash_list:
            g = time.gmtime(c["peak_t"])
            header.append(f"{c['symbol']} {MONTHS[g.tm_mon - 1]} {g.tm_mday}")
        rows = [["Crash that followed", ("?", "no")] + [(pct(c["drop"]), "neg") for c in crash_list]]
        for code, _, _, _ in SIGNS:
            rows.append([SHORT[code], cell(code, cmp["now"], cmp["values"])]
                        + [cell(code, c["signs"], c["values"]) for c in crash_list])
        rows.append(["Signs in common", f"{len(cmp['now'])} showing"] + [f"{c['match']} of {c['total']}" for c in crash_list])
        return header, rows

    table(*build(cmp["crashes"][:4]), sum_last=True)
    ui('<p class="hl-small">✓ = the sign was showing. Each column = a coin at its top, right before it crashed (the 4 most '
       'similar shown). Last row = how many of that coin\'s signs this coin shows now.</p>')
    if b["sim"] >= 0.8:
        conclude(f"This coin looks like <b>{h(b['symbol'])} right before its {pct(b['drop'])} crash</b>. Treat it as a strong "
                 "warning, even if other parts look calm.", "bad")
    elif b["sim"] >= 0.5:
        txt = (f"{len(have)} of the {b['total']} signs {h(b['symbol'])} had before its {pct(b['drop'])} crash are here"
               + (f" ({h(', '.join(have).lower())})" if have else "") + ".")
        if miss:
            txt += f" Still missing: <b>{h(', '.join(miss).lower())}</b>. If that shows up too, get out."
        conclude(txt, "warn")
    else:
        conclude(f"No crash setup here: the closest crash shares only {b['match']} of {b['total']} signs.", "good")
    if len(cmp["crashes"]) > 4:
        with st.expander(f"See all {len(cmp['crashes'])} crashes"):
            table(*build(cmp["crashes"]), sum_last=True)
    with how("What each sign means & how reliable it is"):
        ui('<ul class="hl-list">' + "".join(f'<li class="info">{h(label)}</li>' for _, label, _, _ in SIGNS) + "</ul>")
        lines = []
        for x in a["similarity_test"]:
            bk = x["buckets"]
            hi, lo = bk.get("≥80%"), bk.get("<50%")
            if not hi or not lo or not hi[0] or not lo[0]:
                continue
            p_hi, p_lo = hi[2] / hi[0], lo[2] / lo[0]
            ok = p_hi > p_lo * 1.5
            lines.append(f'<li class="{"good" if ok else "warn"}"><b>{h(x["symbol"])}</b>: when it looked 80%+ like another '
                         f'coin before a crash, it fell 50%+ within 8 hours {p_hi * 100:.0f}% of the time; when it looked '
                         f'less than 50% alike, {p_lo * 100:.0f}%.</li>')
        ui('<p class="hl-small">Check on each sample coin (compared only with the other coins\' crashes):</p>'
           '<ul class="hl-list">' + "".join(lines) + "</ul>"
           '<p class="hl-small">Crashes with fewer than 3 signs are not used. Looking 80%+ like a coin before a crash turns an '
           'OK verdict into CAUTION.</p>')


def tech_rows(t):
    """(label, value text, explanation, feature names) for the indicator list."""
    out = []
    et, st_ = t.get("ema_trend"), t.get("structure")
    if et is not None:
        trend = "Going up" if et > 0.02 and (st_ or 0) >= 0 else "Going down" if et < -0.02 and (st_ or 0) <= 0 else "Sideways"
        swing = {1: "higher highs and higher lows", -1: "lower highs and lower lows", 0: "mixed swings"}.get(st_, "not enough swings")
        out.append(("Trend (1h chart)", trend, f"short average {pct(et)} vs long average; {swing}", ["ema_trend", "structure"]))
    if t.get("ema_gap") is not None:
        g = t["ema_gap"]
        out.append(("Distance from average", pct(g), ("above the 1h average" + (" — stretched, pullbacks are common" if g > 0.15 else ""))
                    if g >= 0 else "below the 1h average", ["ema_gap"]))
    if t.get("rsi1h") is not None:
        r = t["rsi1h"]
        out.append(("RSI (1h)", f"{r:.0f}", "overbought — pumps often pause here" if r >= 70 else
                    "oversold — bounces are common, but dumps can keep going" if r <= 30 else "neutral", ["rsi1h"]))
    if t.get("vwap_gap") is not None:
        out.append(("vs 24h average price", pct(t["vwap_gap"]), "compared with the average traded price of the last 24h",
                    ["vwap_gap"]))
    if t.get("bb_pctb") is not None:
        b = t["bb_pctb"]
        out.append(("Bollinger (1h)", f"{b * 100:.0f}%", "where the price sits in its usual band (100% = top)"
                    + (f"; band is {t['bb_width'] * 100:.0f}% of price wide" if t.get("bb_width") is not None else ""),
                    ["bb_pctb", "bb_width"]))
    if t.get("vol_ratio2") is not None:
        v = t["vol_ratio2"]
        out.append(("Volume", f"{v:.1f}×", "last 2 hours vs normal" + (" — volume spike" if v >= 2 else ""), ["vol_ratio2"]))
    if t.get("buy_share4") is not None:
        out.append(("Buying share", f"{t['buy_share4'] * 100:.0f}%", "of the last 4 hours' trading was buying", ["buy_share4"]))
    if t.get("buyers2") is not None:
        out.append(("Activity", f"{t['buyers2']} / {t['sellers2']}", "different buyers / sellers in the last 2 hours",
                    ["buyers2", "sellers2"]))
    if t.get("profit3") is not None:
        out.append(("Holders in big profit", f"{t['profit3']:.0f}%", "of all coins sit with holders up 3x or more (or who got "
                    "them free) — possible sellers", ["profit3"]))
    if t.get("early") is not None:
        out.append(("First-hour wallets", f"{t['early']:.1f}%", "of all coins are still with wallets that bought in the first "
                    "hour (snipers, insiders)", ["early"]))
    if t.get("liq_chg6") is not None:
        c = t["liq_chg6"]
        out.append(("Liquidity change (6h)", pct(c), "LPs are pulling money out" if c <= -0.15 else
                    "LPs are adding money" if c >= 0.15 else "stable", ["liq_chg6"]))
    if t.get("new6") is not None:
        out.append(("New buyers", f"{t['new6']:.1f}×", "new wallets buying in the last 6 hours vs normal", ["new6"]))
    return out


def tech_section(a):
    t = a.get("tech") or {}
    rows = tech_rows(t)
    if not rows:
        return
    ft = a.get("feature_test")
    section("Chart and on-chain indicators", "Classic chart indicators plus holder data. The tag shows whether it helped "
                                             "predict crashes when we tested it on the sample coins.")
    cells = []
    for label, val, expl, feats in rows:
        tag = ('<span class="hl-tag yes">predicts crashes</span>' if any(useful(ft, n) for n in feats)
               else '<span class="hl-tag">info only</span>') if ft else ""
        cells.append(f'<div class="k">{h(label)}</div><div class="v"><b>{h(val)}</b> · {h(expl)}{tag}</div>')
    ui('<div class="hl-kv">' + "".join(cells) + "</div>")
    trend = rows[0][1] if rows and rows[0][0].startswith("Trend") else None
    txt = (f"The chart is <b>{trend.lower()}</b>. " if trend else "")
    if a["factors"]:
        txt += f"The indicator that worries the model most: {h(a['factors'][0])}. "
    txt += ('Classic signals like RSI and moving averages did not predict crashes in our tests ("info only"), so don\'t open '
            "or skip just because of them.")
    conclude(txt, "warn" if a["factors"] else "info")


def sleep_section(a):
    pos, hours = a["position_usd"], f"{a['sleep_h']:g} hours"
    section(f"If you sleep {hours}")
    rng = "full range" if a["range_pct"] <= 0 else f"{plan_label(a)} range"
    d30, d50 = a["sims"][1], a["sims"][2]
    items = [("info", f"If the price falls 30% while you sleep, your {dollars(pos)} in a {rng} becomes about "
                      f"{dollars(pos * d30[1])} ({usd_of(pos * (d30[1] - 1))}). If it falls 50%: about {dollars(pos * d50[1])}.")]
    if is_single(a):
        items.append(("info", f"If the price goes up while you sleep, nothing changes: you still have {dollars(pos)} in "
                              f"{pair_unit(a)} (and no fees, because the price is above your range)."))
    if a["hist_rate"] is not None:
        items.append(("info", f"In its own history, this coin fell 30%+ within {hours} {a['hist_rate'] * 100:.0f}% of the time."))
    items += [("info", line) for line in a["info"]]
    bullets(items)
    pick = safe_pick(a)[0]
    txt = f"A 30% drop would cost your {rng} about {usd_of(pos * (d30[1] - 1))}"
    extra = ""
    if pick and not rg.is_full(pick["range"]) and pick["range"] != (a["range_pct"], a.get("upper_pct")):
        b30 = float(rg.lp_values(np.array([0.7]), pick["range"])[0]) - 1
        txt += f", or about {usd_of(pos * b30)} with the safe range ({h(pick['label'])})"
        if b30 < d30[1] - 1:
            extra = ((" The safe range is less deep, so a big drop costs more, but drops that big stayed rare in nights "
                      "like this; a deeper range costs less in a crash but earns fewer fees.") if is_single(a) else
                     (" The safe range is tighter, so a big drop costs more, but drops that big stayed rare in nights like "
                      "this; a wider range costs less in a crash but earns fewer fees."))
    conclude(txt + "." + extra + " Only put in an amount you can lose, and keep the alarm on.",
             "bad" if d30[1] - 1 <= -0.25 else "warn")
    with st.expander("LP value table"):
        table(["Price move", f"Your {rng}", "Full range", f"Just holding {pair_unit(a)}"],
              [[f"{d * 100:.0f}%", (dollars(pos * v), "neg"), (dollars(pos * full), "neg"), dollars(pos)]
               for d, v, full in a["sims"]])


def render(a):
    usd, f, qu = a["usd"], a["now"], a["quote"]
    decision_card(a)
    ui('<p class="hl-small" style="margin:-6px 0 10px">📖 Not sure how to read a part? Open the <b>How to read the results</b> '
       'tab at the top of the page.</p>')
    if a.get("open_pos"):
        position_section(a)
    if a["breakout"]:
        ui(f'<div class="hl-note">{h(a["breakout"])}</div>')
    fdv = f["px"] * a["supply"]
    off = f["px"] / f["ath"] - 1
    u = a["usdg"] or {}
    main = u.get("main")
    liq = (("USDG pool size", f"${compact(u['depth'])}", "", f"fee {main['fee'] / 1e4:g}% · coin age {f['age_h'] / 24:.1f} days")
           if main else ("Pool size", f"{compact(f['eth_res'])} {a['quote']}", "", f"coin age {f['age_h'] / 24:.1f} days"))
    tiles([("Market cap", money(fdv, usd, qu), "", None),
           ("From all-time high", pct(off), sign_class(off), f"ATH {money(f['ath'] * a['supply'], usd, qu)}"),
           ("Last 4 hours", pct(f["r4h"]), sign_class(f["r4h"]), None), liq])
    ui(f'<div class="hl-meta">{h(a["name"])} ({h(a["symbol"])}) · {h(a["address"])} · data up to {when(f["t"])} UTC</div>')
    usdg_section(a)
    risk_section(a)
    reasons_section(a)
    move_section(a)
    holders_section(a)
    flow_section(a)
    liquidity_section(a)
    range_section(a)
    exit_section(a)
    analog_section(a)
    comparison(a)
    tech_section(a)

    section("Last 72 hours")
    ch, df = chart(a)
    st.altair_chart(ch, theme=None)
    ui('<div class="hl-legend">' + "".join(f'<span style="--c:{c}">{v}</span>' for v, c in STATUS.items())
       + '</div><p class="hl-small">Top: market cap. Middle: big holders selling (+) or buying (-) over 6 hours, as % of all '
       'coins. Bottom: the verdict this tool would have given at each moment.</p>')
    rows72 = a["rows"]
    if rows72:
        chg = rows72[-1]["px"] / rows72[0]["px"] - 1
        hi72 = max(r["px"] for r in rows72)
        bad_share = sum(r["level"] == 2 for r in rows72) / len(rows72)
        wn = rows72[-1]["wn6"]
        off = 1 - rows72[-1]["px"] / hi72
        txt = (f"In the last {(rows72[-1]['t'] - rows72[0]['t']) / 3600:.0f} hours the price moved {pct(chg)} and "
               + (f"is now {off * 100:.0f}% below the high of that period." if off >= 0.005 else "is at the high of that period.")
               + f" The tool would have said AVOID {bad_share * 100:.0f}% of that time. Big holders "
               f"{'sold' if wn > 0 else 'bought'} {abs(wn):.2f}% of all coins (net) in the last 6 hours.")
        conclude(txt, "bad" if wn >= 1.5 else "warn" if bad_share >= 0.3 else "info")
    with st.expander("Chart data (table, last 24h)"):
        rows = [[when(r["t"]), money(r["px"] * a["supply"], usd, qu), (f"{r['wn6']:+.2f}%", sign_class(-r["wn6"])),
                 VERDICTS[r["level"]]] for r in a["rows"][::-1][:288]]
        table(["Time (UTC)", "Market cap", "Big holders 6h (+sold / -bought)", "Verdict"], rows, tall=True)

    sleep_section(a)

    section("Alarm while the position is open")
    ui('<p class="hl-small" style="margin-bottom:6px">Get a Telegram message when the verdict turns AVOID '
       '(set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID first):</p>'
       f'<div class="hl-code">python -m dump_alert.watch --token {h(a["address"])}</div>')

    with st.expander("Technical details: all filters"):
        rows = []
        for x in a["filters"]:
            mark = ("❌" if x["keras"] else "⚠️") if x["hit"] else "✅"
            rows.append([f"{mark} {x['code']}", (x["desc"], "lbl"), x["detail"]])
        table(["", "Filter", "Current value"], rows)
        rule = VERDICTS[a["rule_level"]]
        mp = f'{a["model_p"] * 100:.0f}%' if a["model_p"] is not None else "n/a"
        ui(f'<p class="hl-small">Rules alone would say <b>{rule}</b>; model {mp}; mode '
           f'{"conservative" if a["mode"] == "conservative" else "balanced"}. K = strong danger sign (any K → at least CAUTION, '
           'AVOID in conservative mode). L = warning (two or more → CAUTION in the rules).</p>')
        pat = a["pattern"]
        if pat:
            ui(f'<p class="hl-small">Price pattern: {h(STATES[pat["state"]])}. High {h(money(pat["h1"] * a["supply"], usd, qu))} '
               f'({when(pat["h1_t"])}) → down to {h(money(pat["low"] * a["supply"], usd, qu))} '
               f'({pct(pat["low"] / pat["h1"] - 1)}) → retest {h(money(pat["retest"] * a["supply"], usd, qu))} '
               f'({when(pat["retest_t"])}).</p>')


def guide_page():
    """Tab 'How to read the results': a step-by-step reading guide in English or Indonesian."""
    lang = st.radio("Language / Bahasa", ["English", "Bahasa Indonesia"], horizontal=True, key="guide_lang")
    g = GUIDE["id" if lang.startswith("Bahasa") else "en"]
    ui(f'<p class="hl-sub" style="margin:6px 0 4px">{g["intro"]}</p>')
    ui(f'<div class="hl-h">{g["quick_title"]}</div><ol class="hl-steps">' + "".join(f"<li>{x}</li>" for x in g["quick"])
       + "</ol>")
    ui(f'<div class="hl-h">{g["pos_title"]}</div><ul class="hl-list">' + "".join(f'<li class="info">{x}</li>' for x in g["pos"])
       + "</ul>")
    ui(f'<div class="hl-h">{g["boxes_title"]}</div><div class="hl-gboxes">' + "".join(
        f'<div class="hl-card hl-verdict {c} hl-mini"><div class="t">{t}</div><div class="s">{d}</div></div>'
        for c, t, d in g["boxes"]) + "</div>")
    ui(f'<div class="hl-h">{g["sections_title"]}</div>')
    for i, (title, what, how_, good, bad) in enumerate(g["sections"], 1):
        with st.expander(f"{i}. {title}"):
            extra = ""
            if title == "Liquidity and price floors":
                extra = ('<div class="hl-chips">' + "".join(f'<span style="--c:{c}">{n}</span>' for c, n in (
                    (BID, "below the price"), (ASK, "above the price"), (VOL_C, "traded, 7 days"),
                    ("rgba(151,252,228,.35)", "price floor / safe range"), ("rgba(144,133,233,.45)", "resistance"),
                    (STATUS_TEXT[1], "take profit"))) + "</div>")
            elif title == "Last 72 hours":
                extra = '<div class="hl-chips">' + "".join(f'<span style="--c:{c}">{v}</span>' for v, c in STATUS.items()) + "</div>"
            ui(f'<p class="hl-gwhat"><b>{g["what"]}:</b> {what}</p>{extra}<p class="hl-glab">{g["how"]}</p><ul class="hl-list">'
               + "".join(f'<li class="info">{x}</li>' for x in how_) + '</ul><div class="hl-gpair">'
               + f'<div class="good"><b>✔ {g["good"]}</b>' + "".join(f"<div>{x}</div>" for x in good) + "</div>"
               + f'<div class="bad"><b>✖ {g["bad"]}</b>' + "".join(f"<div>{x}</div>" for x in bad) + "</div></div>")
    ui(f'<div class="hl-h">{g["check_title"]}</div><ul class="hl-checks">' + "".join(f"<li>{x}</li>" for x in g["check"])
       + "</ul>")
    ui(f'<div class="hl-h">{g["glossary_title"]}</div><div class="hl-kv">' + "".join(
        f'<div class="k">{k}</div><div class="v">{v}</div>' for k, v in g["glossary"]) + "</div>")
    ui(f'<div class="hl-h">{g["limits_title"]}</div>')
    bullets([("warn", x) for x in g["limits"]])


def main():
    ui(CSS)
    ref = load_reference()
    names = [t["symbol"] for t in (ref or {}).get("tokens", [])]
    ui('<div class="hl-head"><span class="hl-logo">◆ LP Guard</span><span class="hl-pill">Robinhood Chain</span></div>'
       '<div class="hl-title">Check a coin before you LP and sleep</div>'
       '<p class="hl-sub">Paste a coin\'s contract address. You get one clear answer — open, be careful, or wait — plus the '
       f'best LP range, who holds the coin and where money is flowing, all learned from {len(names) or 24} past coins.</p>')
    keys = api_keys()
    t_check, t_guide = st.tabs(["🔍 Check a coin", "📖 How to read the results"])
    with t_guide:
        guide_page()
    with t_check:
        check_page(keys, ref)


def check_page(keys, ref):
    kind = st.radio("Position type", [SINGLE, TWO], horizontal=True,
                    help="Single-sided USDG: you add only USDG, the range runs from today's price (0%) down to your lower "
                         "bound. Two-sided: you add USDG and the coin, the range goes below and above today's price.")
    single = kind == SINGLE
    with st.form("check"):
        ca = st.text_input("Contract address", placeholder="0x…")
        c1, c2 = st.columns(2)
        if single:
            rng = c1.slider("Your lower bound (-%)", 5, 95, 65, step=5,
                            help="How far below today's price your range goes. Your range is from this to 0% (today's price).")
            upper = 0
            c2.markdown('<p class="hl-small" style="margin-top:6px"><b>Upper bound: 0% (today\'s price).</b> With only USDG the '
                        'top of your range is the price when you open. Above it you just hold USDG.</p>', unsafe_allow_html=True)
        else:
            rng = c1.slider("Your lower bound (-%)", 0, 95, 65, step=5,
                            help="How far below today's price your range goes. 0 = full range.")
            upper = c2.slider("Your upper bound (+%)", 5, 300, 65, step=5, help="How far above today's price your range goes.")
        c5, c6 = st.columns(2)
        sleep_h = c5.slider("Hours you'll be away", 2, 12, 8)
        target = c6.select_slider("Never hit the bottom on at least" if single else "Stay in range on at least",
                                  ["80%", "90%", "95%"], value="90%",
                                  help=("How often the price should have stayed above the suggested lower bound for the whole "
                                        "time you are away, in similar past nights. Higher = deeper lower bound, fewer fees."
                                        if single else
                                        "How often the suggested range should have stayed in range for the whole time "
                                        "you are away, in similar past nights. Higher = wider range, fewer fees."))
        c3, c4 = st.columns(2)
        position = c3.number_input("Position size (USD)", min_value=10, max_value=10_000_000, value=1000, step=100,
                                   help="How much you plan to put in. Used to show results in dollars. In a small pool, "
                                        "a big position earns less per dollar because you share the fees with yourself.")
        conservative = c4.toggle("Extra careful mode", help="Says OK less often. In tests on busy coins, fewer crashes "
                                 "slipped through as OK (3.5% instead of 8.4%).")
        c7, c8 = st.columns(2)
        entry_txt = c7.text_input("Already open? Market cap when you opened (optional)", placeholder="e.g. 8.38M",
                                  help="Have a position open already? Enter the market cap at the moment you opened it "
                                       "(the top of your range for single-sided USDG). You get an exit plan for that "
                                       "position, using your lower bound above. Leave empty to plan a new position.")
        at = c8.text_input("Rewind to a past time (optional, UTC)", placeholder="2026-10-10 15:40",
                           help="See what the tool would have said at that moment, e.g. right before an old dump.")
        go = st.form_submit_button("Check now", type="primary", width="stretch")
    if not go:
        ui(f'<div class="hl-note" style="margin-top:10px">{GUIDE["en"]["nudge"]}</div>')
        return
    address = ca.strip().lower()
    if not (address.startswith("0x") and len(address) == 42):
        ui('<div class="hl-alert bad">That contract address is not valid (it must be 0x + 40 characters).</div>')
        return
    entry_mcap = parse_mcap(entry_txt)
    if entry_txt.strip() and entry_mcap is None:
        ui('<div class="hl-alert bad">Could not read that market cap. Write it like 8.38M, 850K or 8380000.</div>')
        return
    if not keys:
        ui('<div class="hl-alert bad">Add an Alchemy API key first (sidebar).</div>')
        return
    as_of = None
    if at.strip():
        try:
            as_of = parse_time(at)
        except ValueError:
            ui('<div class="hl-alert bad">Time format: YYYY-MM-DD HH:MM (UTC), e.g. 2026-10-10 15:40</div>')
            return
    rpc = make_rpc(keys=keys)
    path = data_path(address)
    try:
        with st.status("Pulling on-chain data…", expanded=True) as status:
            ds = Fetcher(rpc, address, path, progress=status.write).run(STEP)
            status.update(label="Data ready", state="complete", expanded=False)
    except Exception as e:
        quota = isinstance(e, QuotaError)
        if os.path.exists(path):
            nxt = ('Everything pulled so far is <b>saved</b>: press <b>Check now</b> again to continue from where it stopped '
                   '(quota already used is not wasted).')
        else:
            nxt = 'Then press <b>Check now</b> again.' if quota else 'Make sure this is an ERC-20 token on Robinhood Chain.'
        if quota:
            ui(f'<div class="hl-alert bad">Alchemy quota used up / key rejected: {h(rpc._redact(e)[:400])}<br>'
               f'Add another key (Secrets <code>ALCHEMY_API_KEYS</code> or the sidebar). {nxt}</div>')
        else:
            ui(f'<div class="hl-alert bad">Could not pull the data: {h(rpc._redact(e)[:300])}<br>{nxt}</div>')
        key_status(rpc)
        return
    key_status(rpc)
    if as_of:
        ds = truncate(ds, as_of)
        ui(f'<div class="hl-note">Rewind mode: judged as if now were {when(as_of)} UTC (liquidity and pool data are '
           'today\'s).</div>')
    try:
        with st.spinner("Comparing with the sample coins…"):
            a = assess(Token(ds), rng, sleep_h, ref, eth_price(keys), history_h=72,
                       mode="conservative" if conservative else "balanced", position_usd=float(position),
                       upper_pct=float(upper), stay_target=int(target[:-1]) / 100, single_sided=single,
                       entry_mcap=entry_mcap)
        if not a:
            ui('<div class="hl-alert warn">This coin has no Uniswap pool or no buys yet, so it cannot be judged.</div>')
            return
        render(a)
    except Exception as e:  # Streamlit Cloud hides the message of uncaught errors; show it here (keys redacted)
        traceback.print_exc()
        ui(f'<div class="hl-alert bad">Something went wrong while analysing this coin: {h(type(e).__name__)}: '
           f'{h(rpc._redact(e)[:300])}<br>The pulled data is saved. Press <b>Check now</b> again; if it keeps '
           'happening, send a screenshot of the error details below.</div>')
        with st.expander("Error details"):
            st.code(rpc._redact(traceback.format_exc())[-4000:], language=None)


main()
