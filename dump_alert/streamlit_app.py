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
from dump_alert.analogs import GROUP_LABEL  # noqa: E402
from dump_alert.fetch import Fetcher, QuotaError, data_path, eth_usd, make_rpc, parse_keys  # noqa: E402
from dump_alert.levels import ZONE  # noqa: E402
from dump_alert.lp_check import (ANALOG_GROUPS, SIGNS, STEP, VERDICTS, assess, load_reference,  # noqa: E402
                                 lp_value, parse_time, pct, truncate)
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
TITLE = {0: "OK · fine to open an LP", 1: "CAUTION · high risk", 2: "AVOID · don't open an LP now"}
ICON = {0: "✅", 1: "⚠️", 2: "🚫"}
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
SHORT = {"pump": "Ran up ≥20% (4h)", "lipat": "≥1.8x its 24h low", "pucuk": "≤10% below the 24h high",
         "retest": "ATH retest pattern", "borong": "Whales bought ≥0.9% (6h)", "jual": "Whales sold ≥1.5% (6h)",
         "baru": "New big wallets ≥1.5% (2h)", "ath4j": "New 4h-chart ATH"}

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
    return "" if v is None else "pos" if v > 0 else "neg" if v < 0 else ""


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
           alt.Tooltip("whale6h:Q", title="Whales, 6h: + sold / − bought (% of supply)", format="+.2f"),
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
    price = alt.layer(*layers).properties(height=260, width=CHART_W)
    whale = alt.layer(
        base.mark_line(strokeWidth=2, color=ACCENT).encode(
            y=alt.Y("whale6h:Q", title="Whales sold(+)/bought(−) %",
                    scale=alt.Scale(domain=[max(df["whale6h"].min(), -5), max(df["whale6h"].max(), 2)], clamp=True))),
        alt.Chart(pd.DataFrame({"y": [1.5]})).mark_rule(color=STATUS["AVOID"], strokeDash=[4, 4]).encode(y="y:Q"),
        alt.Chart(pd.DataFrame({"y": [1.5], "label": ["danger line: whales sold 1.5%"]})).mark_text(
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
    rec = a.get("best_range")
    best = rec["best"]["range"] if rec and not rec["skip"] else None
    up_usd = dep.get("up_usd")
    up = 0
    if up_usd:
        want = 40 if not best or rg.is_full(best) else max(40, best[1] + 10)
        up = min(len(up_usd) - 1, want, 160) // band * band
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
        edges = pd.DataFrame([{"y": -best[0], "label": f"Best range low −{best[0]:g}%"}]
                             + ([{"y": best[1], "label": f"Best range high +{best[1]:g}%"}] if best[1] <= up else []))
        over.append(alt.Chart(edges).mark_rule(color=MINT, strokeDash=[4, 4], strokeWidth=1).encode(y="y:Q"))
        over.append(alt.Chart(rng).mark_rule(color=MINT, strokeWidth=4).encode(y="y0:Q", y2="y1:Q", x=alt.datum(xmax)))
    # labels on the right edge; drop a label when it would collide with a more important one (≥ 4% apart)
    labels = [(0, f"now · {money(px * supply, usd, qu)}", 0)]
    if best and not rg.is_full(best):
        labels += [(-best[0], f"best range low −{best[0]:g}%", 1)] + ([(best[1], f"best range high +{best[1]:g}%", 1)]
                                                                      if best[1] <= up else [])
    zones = []
    for z in lv["zones"]:
        mid = z["drop"] * 100
        if mid <= lo_y:
            continue
        zones.append({"y0": max(lo_y, ((1 + z["drop"]) * (1 - ZONE) - 1) * 100), "y1": ((1 + z["drop"]) * (1 + ZONE) - 1) * 100})
        labels.append((mid, f"{pct(z['drop'])} {z['strength']} · {money(z['price'] * supply, usd, qu)}",
                       {"strong": 2, "medium": 3, "weak": 4}[z["strength"]]))
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

def usdg_section(a):
    """Which USDG pool the analysis uses (where you LP), or a warning if there is none."""
    u = a["usdg"]
    if u is None:
        return
    main = u["main"]
    if not main:
        ui(f'<div class="hl-alert warn">⚠️ <b>This coin has no active USDG pool yet.</b> Only a pool against '
           f'{h(a["market"].get("pair_symbol") or a["quote"])} was found, so the analysis uses that pool'
           + ("; USD values use today's ETH price" if a["usd"] else "") + '.</div>')
        return
    since = ""
    if u["since_block"] and u["pre"]:
        since = (f' Price history from before this pool existed comes from the {h(u["pre"])} pool, converted to USDG, '
                 'so the ATH and older patterns still count.')
    ui(f'<div class="hl-note">Judged on the <b>USDG pool with a {main["fee"] / 1e4:g}% fee</b> (the deepest'
       + (f' of {len(u["pools"])} USDG pools' if len(u["pools"]) > 1 else "")
       + f'), active liquidity ≈ ${compact(u["depth"])}. Prices, market cap and ranges are in USD.{since}</div>')
    if len(u["pools"]) > 1:
        with st.expander(f"All USDG pools ({len(u['pools'])})"):
            total = sum(p["swaps"] for p in u["pools"]) or 1
            rows = [[("✓ " if p is main else "") + f"{p['pool_id'][:10]}…{p['pool_id'][-4:]}", f"{p['fee'] / 1e4:g}%",
                     f"{p['swaps'] / total * 100:.0f}%", f"${compact(2 * p['usdg_side'])}"] for p in u["pools"]]
            table(["Pool (ID)", "Fee", "Share of swaps", "Active liquidity"], rows)
            ui('<p class="hl-small">Share of swaps comes from a sample of recent transactions. Active liquidity = the '
               'liquidity around the current price, expressed as a full-range equivalent. Prices across USDG pools stay '
               'almost equal (arbitrage), so the verdict applies to all of them; what differs is the fee and depth.</p>')


def summary_section(a):
    """The conclusions of the main analyses in one box, right under the verdict."""
    cells = []
    p, hours = a["model_p"], f"{a['sleep_h']:g}h"
    if p is not None:
        cells.append(("Dump risk", f"<b>{p * 100:.0f}%</b> chance of a ≥30% drop within {hours}"
                      + (f" · {a['analogs']['n30']} of {a['analogs']['n']} similar moments dumped" if a.get("analogs") else "")))
    rec = a.get("best_range")
    if rec:
        b = rec["best"]
        cells.append(("Best range", f"<b>skip</b> — no range came out ahead at today's fee level ({rec['fee']['r'] * 100:.2f}%/day)"
                      if rec["skip"] else f"<b>{h(b['label'])}</b> · average {pct1(b['mean'])}, bad case {pct1(b['p10'])} over {hours}"))
    lv = a.get("levels") or {}
    firm = [z for z in lv.get("zones", []) if z["strength"] in ("strong", "medium") and z["drop"] >= -0.5]
    if firm:
        cells.append(("First support", f"<b>{pct(firm[0]['drop'])}</b> ({firm[0]['strength']}, {firm[0]['touches']} bounce{'' if firm[0]['touches'] == 1 else 's'})"))
    w = lv.get("whale")
    if w and w["drop1"] is not None:
        cells.append(("Whale risk", f"largest wallet selling all → <b>{pct(-w['drop1'])}</b>"))
    if cells:
        ui('<div class="hl-card"><div class="hl-kv" style="margin:0">' + "".join(
            f'<div class="k">{k}</div><div class="v">{v}</div>' for k, v in cells) + "</div></div>")


def risk_section(a):
    """Three independent estimates of the dump risk while you sleep."""
    cal, q, p = a["calib"], a["analogs"], a["model_p"]
    if cal is None and q is None and p is None:
        return
    hours = f"{a['sleep_h']:g}h"
    section("Dump risk estimate", f"Chance of a ≥30% price drop within the next {hours}, from three independent angles.")
    items = []
    if p is not None:
        m = a["model"] or {}
        bin_ = next((b for b in m.get("calibration", []) if b["lo"] <= p < b["hi"]), None)
        note = (f"when it said {b_lo(bin_)}–{b_hi(bin_)}% in testing, dumps happened {bin_['rate'] * 100:.0f}% of the time"
                if bin_ and bin_["rate"] is not None and bin_["n"] >= 30 else "calibration not available")
        items.append(("Model", f"{p * 100:.0f}%", "neg" if p >= 0.35 else "warn-t" if p >= 0.15 else "pos", note))
    if q is not None:
        items.append(("Similar past moments", f"{q['n30']} of {q['n']}", "neg" if q["p30"] >= 0.35 else
                      "warn-t" if q["p30"] >= 0.15 else "pos", f"dumped ≥30% within 8h ({q['p30'] * 100:.0f}% weighted)"))
    vt = ((a["model"] or {}).get("verdict_test") or {}).get(a["mode"])
    if vt and vt[a["level"]]["p30"] is not None:
        r = vt[a["level"]]["p30"]
        items.append((f"When the tool said {a['verdict']}", f"{r * 100:.0f}%", "neg" if r >= 0.35 else "warn-t" if r >= 0.15
                      else "pos", f"a ≥30% drop followed within {a['sleep_h']:g}h (tested on the sample coins)"))
    elif cal is not None:
        items.append((f"Past {a['verdict']} verdicts", f"{cal['p30'] * 100:.0f}%", "neg" if cal["p30"] >= 0.35 else
                      "warn-t" if cal["p30"] >= 0.2 else "pos", f"dumped ≥30% within {cal['sleep_h']:g}h ({cal['n_tokens']} coins)"))
    tiles(items, "two" if len(items) == 2 else "")
    ui('<p class="hl-small"><b>Model</b> = a statistical model trained on the sample coins (price action, whales, holder '
       'profit, activity, coin age, liquidity). It drives the verdict: under 15% → OK, 15–35% → CAUTION, 35%+ → AVOID, and '
       'a hard danger sign from the rules always means at least CAUTION. <b>Similar past moments</b> = the closest matching '
       'hours in the sample coins and what happened next. The last tile shows how often a dump really followed this '
       'verdict when the tool was tested on coins it had not seen.</p>')
    if p is not None:
        band = 0 if p < 0.15 else 1 if p < 0.35 else 2
        word = ("Low", "Elevated", "High")[band]
        txt = f"<b>{word} dump risk</b>: the model puts a ≥30% drop within {hours} at {p * 100:.0f}%"
        if q is not None:
            qb = 0 if q["p30"] < 0.15 else 1 if q["p30"] < 0.35 else 2
            txt += (f", and {q['n30']} of {q['n']} similar past moments dumped. "
                    + ("Both angles agree." if qb == band else "The two angles disagree, so treat the number as uncertain."))
        else:
            txt += "."
        conclude(txt, LEVEL_CLASS[band])


def b_lo(b):
    return f"{b['lo'] * 100:.0f}"


def b_hi(b):
    return f"{min(b['hi'], 1) * 100:.0f}"


def reasons_section(a):
    section("Why this verdict?")
    items = [("bad", f"❌ {r}") for r in a["reasons"]] + [("warn", f"⚠️ {r}") for r in a["minor_reasons"]]
    if not items:
        items = [("good", "✅ None of the danger signs are active right now.")]
    bullets(items)
    if a["minor_reasons"]:
        ui('<p class="hl-small">⚠️ = minor warning; two or more = CAUTION. ❌ = major warning; one is enough for AVOID.</p>')
    for s in a["similar"]:
        ui(f'<p class="hl-small">The same situation ({h(s["desc"])}) happened {s["n"]} times in the reference coins: '
           f'the typical low over the next 8h was {pct(s["median_dd"])}, and price fell ≥30% in {s["p30"] * 100:.0f}% '
           'of cases.</p>')
    hard = [x for x in a["filters"] if x["hit"] and x["keras"]]
    parts = []
    if a["factors"]:
        parts.append("the model's biggest worries are " + "; ".join(h(x) for x in a["factors"]))
    if hard:
        parts.append(f"{len(hard)} hard danger sign{'s' if len(hard) > 1 else ''} on, so the verdict is at least CAUTION")
    if a["calm_factors"]:
        parts.append("on the calm side: " + "; ".join(h(x) for x in a["calm_factors"][:2]))
    txt = f"<b>{a['verdict']}</b> — " + ("; ".join(parts) if parts else "no danger sign is active and the model sees little risk")
    conclude(txt + ".", LEVEL_CLASS[a["level"]])


def liquidity_section(a):
    """Real LP liquidity around the price (vertical profile) + support zones from price action."""
    lv = a.get("levels")
    if not lv or (not lv["zones"] and not lv["depth"]):
        return
    usd, qu, dep = a["usd"], a["quote"], lv["depth"]
    section("Liquidity & support", "How much selling (and buying) the pools absorb around the current price, what "
                                   "traded at each price, and where buyers stepped in before.")
    items = []
    if dep:
        up = dep.get("up_usd")
        items.append(("info", f"To push the price down 10% / 30% / 50%, someone has to sell about ${compact(dep['usd'][10])} / "
                              f"${compact(dep['usd'][30])} / ${compact(dep['usd'][50])} worth of tokens"
                              + (f"; to push it up 10% / 30%, buy about ${compact(up[10])} / ${compact(up[30])}" if up and len(up) > 30 else "")
                              + f" (LP liquidity in {lv['n_pools']} USDG & ETH pools, linked by arbitrage)."))
    w = lv.get("whale")
    if w and w["drop1"] is not None:
        items.append(("warn" if w["drop1"] >= 0.3 else "info",
                      f"The largest wallet holds {w['top1'] / a['supply'] * 100:.1f}% of supply: if it sold everything at once, "
                      f"the price would drop about {w['drop1'] * 100:.0f}%."
                      + (f" The top-10 wallets ({w['top10'] / a['supply'] * 100:.0f}% of supply): "
                         + (f"about {w['drop10'] * 100:.0f}%." if w["drop10"] is not None else "more than 95%.")
                         if w["top10"] else "")))
    for a0, a1 in lv["holes"]:
        items.append(("bad", f"Thin liquidity between {pct(a0)} and {pct(a1)}: if price falls into this gap it can slide fast."))
    if dep and dep.get("inconsistent"):
        items.append(("warn", "Part of the liquidity map was inconsistent (pools with special hooks); treat the numbers as rough."))
    bullets(items)
    if dep:
        ch, _ = liquidity_profile(a, lv)
        st.altair_chart(ch, theme=None)
        ui('<p class="hl-small">Left: USD of LP liquidity in each 2% price band — green below the price (it pays sellers, '
           'so it slows a drop), purple above (it sells to buyers, so it slows a pump). Right: how much traded at each '
           'price in the last 7 days. Shaded bands = support zones; the light band between dashed lines = the best range '
           'below. Live snapshot, refreshed at most every 15 minutes.</p>')
    if lv["zones"]:
        rows = []
        for z in lv["zones"]:
            age = f"{z['age_h']:.0f}h ago (+{z['bounce'] * 100:.0f}%)" if z["age_h"] is not None else "—"
            rows.append([money(z["price"] * a["supply"], usd, qu), (pct(z["drop"]), "neg"),
                         (z["strength"], {"strong": "yes", "medium": "", "weak": "no"}[z["strength"]]), str(z["touches"]), age,
                         f"${compact(z['liq_usd'])}" if z["liq_usd"] is not None else "—",
                         f"${compact(z['sell_usd'])}" if z["sell_usd"] is not None else "—"])
        table(["Support (mcap)", "Distance", "Strength", "Bounces (7d)", "Last bounce", "Liquidity in zone",
               "Selling needed to get there"], rows)
    t = lv.get("test") or {}
    if t.get("strong") and t.get("plain"):
        r = lambda k: f"{t[k]['rate'] * 100:.0f}%" if t.get(k) and t[k]["rate"] is not None else "—"
        ui(f'<p class="hl-small">Support = a ±{ZONE * 100:.0f}% zone around a 7-day swing low that bounced ≥10%, a thick '
           'liquidity band, or a high-volume price. Strength = how many times price bounced there in the last 7 days '
           f'(3+ = strong), plus thick LP liquidity. Tested on the sample coins, when price touched a zone within 24h: strong zones '
           f'held {r("strong")} of the time, medium {r("medium")}, weak {r("weak")}, and ordinary levels {r("plain")}. '
           'Supports help you place the lower bound; they are not a guarantee, and dump-prone coins break them often.</p>')
    conclude(*liquidity_conclusion(a, lv))


def liquidity_conclusion(a, lv):
    usd, qu, dep, w = a["usd"], a["quote"], lv["depth"], lv.get("whale")
    parts, kind = [], "info"
    zs = [z for z in lv["zones"] if z["drop"] >= -0.5]
    firm = [z for z in zs if z["strength"] in ("strong", "medium")]
    if firm:
        z = firm[0]
        parts.append(f"first real floor: the {z['strength']} support at <b>{pct(z['drop'])}</b> "
                     f"(mcap {h(money(z['price'] * a['supply'], usd, qu))}, {z['touches']} bounce{'' if z['touches'] == 1 else 's'} in 7 days)")
        nxt = [x for x in firm[1:]]
        if nxt:
            parts[-1] += f", then {pct(nxt[0]['drop'])}"
    elif zs:
        parts.append(f"only weak supports nearby (closest {pct(zs[0]['drop'])}), so there is no proven floor")
        kind = "warn"
    else:
        parts.append("no support zone within −50%")
        kind = "warn"
    if dep:
        up = dep.get("up_usd")
        if up and len(up) > 10 and dep["usd"][10]:
            ratio = up[10] / dep["usd"][10]
            side = ("about balanced" if 0.67 <= ratio <= 1.5 else "heavier above the price (pumps get sold into)"
                    if ratio > 1.5 else "heavier below the price (dips get bought)")
            parts.append(f"liquidity within ±10% is {side}")
        if lv["holes"]:
            a0, a1 = lv["holes"][0]
            parts.append(f"a thin gap between {pct(a0)} and {pct(a1)} lets price slide fast")
            kind = "warn"
    if w and w["drop1"] is not None:
        if w["drop1"] >= 0.3:
            parts.append(f"whale risk is high: the largest wallet alone could push price down {w['drop1'] * 100:.0f}%")
            kind = "bad" if w["drop1"] >= 0.5 else "warn"
        else:
            parts.append(f"the largest wallet could move price only {w['drop1'] * 100:.0f}%")
    text = "; ".join(parts) + "."
    return text[0].upper() + text[1:], kind


def range_section(a):
    """Best LP range from the current price, from the range research on the sample coins."""
    rec, usd, f, qu = a.get("best_range"), a["usd"], a["now"], a["quote"]
    if not rec:
        return range_section_basic(a)
    fy, best, hours = rec["fee"], rec["best"], f"{a['sleep_h']:g}h"
    section("Best LP range from the current price",
            f"Researched on {rec['coins']} sample coins: at every past hour that looked like now "
            f"(same verdict and volatility, {rec['n']:,} moments), LP positions with 133 different ranges were opened and left "
            f"for {hours}. Fees come from the real trading volume that followed, scaled to this pool's fee level today.")
    fee_txt = (f"this pool's {fee_pct(fy['rate'])} fee × ${compact(fy['vol24'])} traded in 24h ÷ ${compact(fy['tvl'])} active "
               f"liquidity ≈ <b>{fy['r'] * 100:.2f}% per day</b> for a full-range LP" if fy["rate"] is not None and not fy["assumed"]
               else f"<b>{fy['r'] * 100:.2f}% per day</b> assumed")
    ui(f'<div class="hl-note">Fee level now: {fee_txt}. Your ${compact(fy["position"])} position would be '
       f'{fy["share"] * 100:.2f}% of the pool\'s liquidity' + (" — big enough to dilute your own fees" if fy["share"] > 0.05 else "")
       + "." + ("".join(f" Note: {h(n)}." for n in fy["notes"])) + "</div>")
    px, fdv = f["px"], f["px"] * a["supply"]
    rng = best["range"]
    if rec["skip"]:
        ui('<div class="hl-alert warn">⚠️ <b>At this fee level, no range came out ahead</b> in similar past moments — '
           'not opening is the best choice. If you still open one, the least-bad range is shown below.</div>')
    if rg.is_full(rng):
        lo_tile = ("Lower bound", "none", "", "full range")
        hi_tile = ("Upper bound", "none", "", "full range")
    else:
        lo_tile = ("Lower bound (min price)", f"−{rng[0]:g}%", "warn-t",
                   f"{price_str(px * (1 - rng[0] / 100), usd, qu)} · mcap {money(fdv * (1 - rng[0] / 100), usd, qu)}")
        hi_tile = ("Upper bound (max price)", f"+{rng[1]:g}%", "pos",
                   f"{price_str(px * (1 + rng[1] / 100), usd, qu)} · mcap {money(fdv * (1 + rng[1] / 100), usd, qu)}")
    tiles([lo_tile, hi_tile,
           (f"Average result ({hours})", pct1(best["mean"]), sign_class(best["mean"]),
            f"fees {pct1(best['fees'])} · price {pct1(best['price'])}"),
           ("Bad case (1 in 10)", pct1(best["p10"]), sign_class(best["p10"]),
            f"loss >20%: {best['big_loss'] * 100:.1f}% of the time")])
    if not rg.is_full(rng):
        u = best["usdg"]
        ui(f'<p class="hl-small" style="margin-bottom:8px">You start with about <b>{u * 100:.0f}% {pair_unit(a)}</b> and '
           f'{(1 - u) * 100:.0f}% coin. If the price falls, the position buys the coin step by step; below −{rng[0]:g}% '
           f'you hold only the coin. Above +{rng[1]:g}% you hold only {pair_unit(a)} — no loss, it just stops earning fees.</p>')
    rows = []
    seen = set()
    opts = [("Best range", best), ("Best two-sided range", rec["two_sided"]), ("Full range", rec["full"])]
    if rec["mine"]:
        plan = "full range" if a["range_pct"] <= 0 else f"±{a['range_pct']:g}%"
        opts.append((f"Your plan ({plan})", rec["mine"]))
    for name, c in opts:
        if c["range"] in seen:
            continue
        seen.add(c["range"])
        rows.append([name, c["label"], f"{c['usdg'] * 100:.0f}%", (pct1(c["mean"]), sign_class(c["mean"])),
                     (pct1(c["median"]), sign_class(c["median"])), (pct1(c["p10"]), sign_class(c["p10"])),
                     f"{c['big_loss'] * 100:.1f}%", pct1(c["fees"]), f"{c['inside'] * 100:.0f}%"])
    table(["", "Range", f"Start in {pair_unit(a)}", f"Average ({hours})", "Typical", "Bad case (1 in 10)", "Loss > 20%",
           "Fees", "In range at the end"], rows)
    with st.expander("Range map: every lower × upper bound"):
        st.altair_chart(range_map(rec), theme=None, width="stretch")
        ui('<p class="hl-small">Each cell = growth per night if you opened that range in every similar moment (the average '
           'of log returns: big losses count extra, like they do for your real balance). Blue grows your money, red loses '
           'it. The outlined cell is the best range.</p>')
    near = [z for z in (a.get("levels") or {}).get("zones", [])
            if not rg.is_full(rng) and -rng[0] / 100 <= z["drop"] < 0 and z["strength"] in ("strong", "medium")]
    if near:
        ui('<p class="hl-small">Support inside this range: ' + ", ".join(
            f"{pct(z['drop'])} ({z['strength']})" for z in near) + ' — bounces there keep your position earning.</p>')
    test = range_test_note(rec)
    if test:
        ui(f'<p class="hl-small">{test}</p>')
    ui('<p class="hl-small">Why ranges below the price win: most sample coins drifted down after busy moments, and an LP '
       'range is "short volatility" — a drop through it leaves you holding the coin, while a pump above it only caps your '
       'gain. A range that sits mostly below the price earns fees while buying the dip slowly. If you expect a pump, the '
       'two-sided row keeps more upside. Past behaviour of 24 coins, not a guarantee.</p>')
    conclude(*range_conclusion(a, rec))


def fee_pct(rate):
    return f"{rate * 100:.2f}".rstrip("0").rstrip(".") + "%"


def range_test_note(rec):
    test = rec.get("test")
    if not test:
        return None
    t = min(test, key=lambda x: abs(math.log(x["r"] / max(rec["r"], 1e-4))) + abs(math.log(x["share"] / max(rec["share"], 1e-4))) * 0.5)
    o, fu, fx = t["optimal"], t["full"], t["-40/+50"]
    return (f"Tested on coins it had not seen (fee level {t['r'] * 100:g}%/day, position {t['share'] * 100:g}% of the pool): "
            f"choosing the range this way — and skipping when nothing comes out ahead — grew money "
            f"{o['growth'] * 100:+.2f}% per night vs {fu['growth'] * 100:+.2f}% for full range and {fx['growth'] * 100:+.2f}% for "
            f"−40%/+50%; losses over 20% happened {o['big_loss'] * 100:.1f}% of the time vs {fu['big_loss'] * 100:.1f}% and "
            f"{fx['big_loss'] * 100:.1f}%. Better than full range on {t['beats_full']} of {t['coins']} coins.")


def range_conclusion(a, rec):
    best, mine, hours = rec["best"], rec["mine"], f"{a['sleep_h']:g}h"
    if rec["skip"]:
        txt = (f"<b>Skip this one</b> at today's fee level ({rec['fee']['r'] * 100:.2f}%/day): even the least-bad range "
               f"({h(best['label'])}) averaged {pct1(best['mean'])} in {hours} in similar moments.")
        if mine and mine["range"] != best["range"]:
            txt += f" Your plan averaged {pct1(mine['mean'])}, with a 1-in-10 bad case of {pct1(mine['p10'])}."
        return txt, "warn"
    txt = (f"Open <b>{h(best['label'])}</b>: about {pct1(best['mean'])} on average over {hours} (fees {pct1(best['fees'])}), "
           f"and in the worst 1 of 10 similar moments {pct1(best['p10'])}.")
    if mine and mine["range"] != best["range"]:
        better = mine["growth"] < best["growth"]
        txt += (f" Your plan averaged {pct1(mine['mean'])} with a bad case of {pct1(mine['p10'])}"
                + (" — the best range is the safer bet." if better else "."))
    if a["level"] == 2:
        return txt + " But the verdict is AVOID: the safest choice is still not to open.", "bad"
    return txt, "good" if best["mean"] > 0 else "warn"


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
                      f"Your planned ±{a['range_pct']:g}% range: about {ra['breach'] * 100:.0f}% chance of breaking its lower "
                      "bound while you sleep." + (" Too tight to leave overnight." if bad else "")))
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
    section("Similar past moments", "Every hour of history in the sample coins was compared with right now on pattern, "
                                    "price action, whales, technicals and coin profile. Here is what happened next.")
    kind = "bad" if q["p30"] >= 0.35 else "warn" if q["p30"] >= 0.15 else "good"
    ui(f'<div class="hl-alert {kind}"><b>{q["n30"]} of the {q["n"]} closest moments</b> (from {q["tokens"]} coins) dropped '
       f'≥30% within 8 hours ({q["p30"] * 100:.0f}% weighted by similarity). Typical low over the next 8h: '
       f'{pct(q["median_dd"])}' + (f', typical high: {pct(q["median_rise"])}' if q["median_rise"] is not None else "")
       + '.</div>')
    gs = q["group_sim"]
    cells = [(GROUP_LABEL[g], f"{gs[g] * 100:.0f}%" if gs.get(g) is not None else "—", "", None) for g in GROUP_LABEL]
    tiles(cells[:4])
    rows = []
    for e in q["examples"][:6]:
        g = e["groups"]
        rows.append([e["symbol"], when(e["t"]), f"{e['sim'] * 100:.0f}%"]
                    + [f"{g[x] * 100:.0f}%" if g.get(x) is not None else "—" for x in ("pattern", "price", "whale", "tech")]
                    + [(pct(e["dd"]), "neg" if e["dd"] <= -0.3 else ""),
                       (pct(e["rise"]), "pos") if e["rise"] is not None else "—"])
    table(["Coin", "When (UTC)", "Match", "Pattern", "Price", "Whales", "Tech", "Next 8h low", "Next 8h high"], rows)
    used = ", ".join(GROUP_LABEL[g].lower() for g in ANALOG_GROUPS)
    ui(f'<p class="hl-small">Match % per side (100% = identical). The outcome uses {used}; whale similarity is shown for '
       'context only, because in testing on the sample coins it made the matches slightly less predictive. At most one '
       'moment per coin per 12 hours, so one event is not counted many times.</p>')
    ex = q["examples"][0] if q["examples"] else None
    calm = q["p30"] < 0.15
    txt = (f"{'Calm' if calm else 'Risky' if q['p30'] >= 0.35 else 'Mixed'} history for setups like this: "
           f"{q['p30'] * 100:.0f}% of the closest moments dumped ≥30% within 8h and the typical 8h low was {pct(q['median_dd'])}.")
    if ex:
        txt += f" Closest match: {h(ex['symbol'])} on {when(ex['t'])} ({ex['sim'] * 100:.0f}% similar), which then went {pct(ex['dd'])} at worst."
    if q["sim"] < 0.5:
        txt += " Matches are loose (average similarity under 50%), so this angle counts less."
    conclude(txt, kind)


def comparison(a):
    """This coin vs the reference coins right before their crashes."""
    cmp, names = a["compare"], names_text(a["ref_names"])
    section("Compared with crashes in the reference coins",
            "The signs visible at the top right before each big crash, and which of them this coin shows now.")
    if not cmp:
        ui('<div class="hl-alert warn">No reference data yet (reference.json is empty).</div>')
        return
    b = cmp["best"]
    name = f"<b>{h(b['symbol'])} right before its {pct(b['drop'])} crash</b> ({when(b['peak_t'])} UTC)"
    if b["sim"] >= 0.8:
        ui(f'<div class="hl-alert bad">🚨 Very similar to {name}: {b["match"]} of {b["total"]} signs match.</div>')
    elif b["sim"] >= 0.5:
        ui(f'<div class="hl-alert warn">⚠️ Somewhat similar to {name}: {b["match"]} of {b["total"]} signs match.</div>')
    else:
        ui(f'<div class="hl-alert good">👍 Not similar. The closest is {name}, with only {b["match"]} of {b["total"]} '
           'signs matching.</div>')

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
        rows.append(["Signs in common", f"{len(cmp['now'])} active"] + [f"{c['match']} of {c['total']}" for c in crash_list])
        return header, rows

    table(*build(cmp["crashes"][:4]), sum_last=True)
    if b["sim"] >= 0.8:
        conclude(f"This coin looks like <b>{h(b['symbol'])} right before its {pct(b['drop'])} crash</b> — treat it as a strong "
                 "warning, even if other angles look calm.", "bad")
    elif b["sim"] >= 0.5:
        conclude(f"Partly similar to {h(b['symbol'])} before its {pct(b['drop'])} crash ({b['match']} of {b['total']} signs). "
                 "Not a crash setup yet, but watch for the missing signs.", "warn")
    else:
        conclude(f"No resemblance to the pre-crash setups of the reference coins (best match {b['match']} of {b['total']} "
                 "signs).", "good")
    ui('<p class="hl-small">Each reference column = conditions at the price top right before that crash (the 4 most '
       'similar shown). ✓ = the sign was present. Last row: how many of that crash\'s signs this coin shows now.</p>')
    if len(cmp["crashes"]) > 4:
        with st.expander(f"See all {len(cmp['crashes'])} reference crashes"):
            table(*build(cmp["crashes"]), sum_last=True)
            ui('<p class="hl-small">' + h("Market cap at the top: " + ", ".join(
                f"{c['symbol']} {when(c['peak_t'])[:-6]} "
                f"{money(c['fdv'], 1.0 if c['quote'] == 'USDG' else a['eth_usd'] if c['quote'] in ('ETH', 'WETH') else None, c['quote'])}"
                for c in cmp["crashes"]) + ".") + "</p>")
    with st.expander("What each sign means & how reliable it is"):
        ui('<ul class="hl-list">' + "".join(f'<li class="info">{h(label)}</li>' for _, label, _, _ in SIGNS) + "</ul>")
        lines = []
        for x in a["similarity_test"]:
            bk = x["buckets"]
            hi, lo = bk.get("≥80%"), bk.get("<50%")
            if not hi or not lo or not hi[0] or not lo[0]:
                continue
            p_hi, p_lo = hi[2] / hi[0], lo[2] / lo[0]
            ok = p_hi > p_lo * 1.5
            lines.append(f'<li class="{"good" if ok else "warn"}"><b>{h(x["symbol"])}</b> '
                         f'({"confirmed" if ok else "not confirmed"}): at ≥80% similarity, {p_hi * 100:.0f}% fell ≥50% '
                         f'within 8h; below 50%, {p_lo * 100:.0f}%.</li>')
        ui('<p class="hl-small">Cross-test: each reference coin is matched only against the pre-crash conditions of the '
           'other coins, then we look at what happened 8 hours later.</p><ul class="hl-list">' + "".join(lines) + "</ul>"
           '<p class="hl-small">Crashes without at least 3 signs are not used. Treat this as a strong hint, not a '
           f'prediction. ≥80% similarity to {h(names)} raises an OK verdict to CAUTION.</p>')


def tech_rows(t):
    """(label, value text, explanation, feature names) for the technical view."""
    out = []
    et, st_ = t.get("ema_trend"), t.get("structure")
    if et is not None:
        trend = "Uptrend" if et > 0.02 and (st_ or 0) >= 0 else "Downtrend" if et < -0.02 and (st_ or 0) <= 0 else "Sideways / mixed"
        swing = {1: "higher highs and higher lows", -1: "lower highs and lower lows", 0: "mixed swings"}.get(st_, "not enough swings")
        out.append(("Trend (1h)", trend, f"EMA20 is {pct(et)} vs EMA50; swings show {swing}.", ["ema_trend", "structure"]))
    if t.get("ema_gap") is not None:
        g = t["ema_gap"]
        out.append(("Stretch", pct(g), "above its 1h EMA20" + (" — stretched, pullbacks are common from here" if g > 0.15 else "")
                    if g >= 0 else "below its 1h EMA20", ["ema_gap"]))
    if t.get("rsi1h") is not None:
        r = t["rsi1h"]
        out.append(("RSI 1h", f"{r:.0f}", "overbought — rallies often pause here" if r >= 70 else
                    "oversold — bounces are common, but so are further drops in dumps" if r <= 30 else "neutral",
                    ["rsi1h"]))
    if t.get("vwap_gap") is not None:
        out.append(("vs 24h VWAP", pct(t["vwap_gap"]), "vs the volume-weighted average price of the last 24h", ["vwap_gap"]))
    if t.get("bb_pctb") is not None:
        b = t["bb_pctb"]
        out.append(("Bollinger (1h)", f"{b * 100:.0f}%", "position inside the bands (100% = upper band); band width "
                    f"{t['bb_width'] * 100:.0f}% of price" if t.get("bb_width") is not None else "position inside the bands",
                    ["bb_pctb", "bb_width"]))
    if t.get("vol_ratio2") is not None:
        v = t["vol_ratio2"]
        out.append(("Volume", f"{v:.1f}×", "last 2h volume vs the 24h average" + (" — volume spike" if v >= 2 else ""),
                    ["vol_ratio2"]))
    if t.get("buy_share4") is not None:
        out.append(("Buy pressure", f"{t['buy_share4'] * 100:.0f}%", "of the last 4h volume was buying", ["buy_share4"]))
    if t.get("buyers2") is not None:
        out.append(("Activity", f"{t['buyers2']} / {t['sellers2']}", "unique buyers / sellers in the last 2h",
                    ["buyers2", "sellers2"]))
    if t.get("profit3") is not None:
        out.append(("Holders in big profit", f"{t['profit3']:.0f}%", "of supply sits with holders up ≥3x (or who got tokens "
                    "for free) — potential sellers", ["profit3"]))
    if t.get("early") is not None:
        out.append(("First-hour wallets", f"{t['early']:.1f}%", "of supply is still held by wallets that came in during "
                    "the first hour (snipers/insiders)", ["early"]))
    if t.get("liq_chg6") is not None:
        c = t["liq_chg6"]
        out.append(("Liquidity change 6h", pct(c), "LPs are pulling liquidity" if c <= -0.15 else
                    "LPs are adding liquidity" if c >= 0.15 else "stable", ["liq_chg6"]))
    if t.get("new6") is not None:
        out.append(("New buyers", f"{t['new6']:.1f}×", "new wallets buying in the last 6h vs the 48h norm", ["new6"]))
    return out


def tech_section(a):
    t = a.get("tech") or {}
    rows = tech_rows(t)
    if not rows:
        return
    ft = a.get("feature_test")
    section("Technical view", "Classic indicators plus on-chain holder data. The tag shows whether the item predicted "
                              "≥30% dumps within 8h when tested on the sample coins.")
    cells = []
    for label, val, expl, feats in rows:
        tag = ('<span class="hl-tag yes">useful signal</span>' if any(useful(ft, n) for n in feats)
               else '<span class="hl-tag">context only</span>') if ft else ""
        cells.append(f'<div class="k">{h(label)}</div><div class="v"><b>{h(val)}</b> · {h(expl)}{tag}</div>')
    ui('<div class="hl-kv">' + "".join(cells) + "</div>")
    ui('<p class="hl-small">"Useful signal" = across the sample coins it ranked dump moments above calm ones '
       '(median AUC ≥0.58 or ≤0.42) in the same direction for at least 70% of coins. Classic indicators like RSI, EMA '
       'and VWAP mostly did not; holder profit, activity, volume spikes and volatility did.</p>')
    trend = rows[0][1] if rows and rows[0][0].startswith("Trend") else None
    parts = []
    if a["factors"]:
        parts.append("pushing risk up: " + "; ".join(h(x) for x in a["factors"]))
    if a["calm_factors"]:
        parts.append("keeping it down: " + "; ".join(h(x) for x in a["calm_factors"]))
    txt = ("; ".join(parts) + ". " if parts else "") + (f"Chart trend (1h): {trend.lower()}. " if trend else "")
    txt += "Treat RSI, EMA and VWAP as context, not as a reason to open or skip."
    conclude(txt[0].upper() + txt[1:], "warn" if a["factors"] else "info")


def sleep_section(a):
    lvl = a["level"]
    section(f"If you sleep {a['sleep_h']:g} hours")
    items = []
    if a["hist_rate"] is not None:
        items.append(("info", f"In its own history, this coin fell ≥30% within {a['sleep_h']:g}h {a['hist_rate'] * 100:.0f}% "
                              "of the time."))
    cal = a["calib"]
    if cal:
        items.append(("info", f"In {short_names(a['ref_names'])}, whenever the verdict was {VERDICTS[lvl]}: a ≥30% drop within "
                              f"{cal['sleep_h']:g}h happened {cal['p30'] * 100:.0f}% of the time, ≥50% {cal['p50'] * 100:.0f}%."))
    rng = "full range" if a["range_pct"] <= 0 else f"a ±{a['range_pct']:g}% range"
    d30, d50 = a["sims"][1], a["sims"][2]
    items.append(("info", f"If price drops 30% while you sleep, your LP ({rng}) loses about {pct(d30[1] - 1)}; if it drops "
                          f"50%, about {pct(d50[1] - 1)} (in {pair_unit(a)})."))
    items += [("info", line) for line in a["info"]]
    bullets(items)
    rec = a.get("best_range")
    plan = "full-range LP" if a["range_pct"] <= 0 else f"±{a['range_pct']:g}% LP"
    txt = f"A 30% drop while you sleep costs your {plan} about {pct(d30[1] - 1)} and a 50% drop about {pct(d50[1] - 1)}"
    if rec and not rg.is_full(rec["best"]["range"]):
        b30, b50 = (float(rg.lp_values(np.array([x]), rec["best"]["range"])[0]) - 1 for x in (0.7, 0.5))
        txt += f"; with the best range ({h(rec['best']['label'])}) about {pct(b30)} and {pct(b50)}"
    conclude(txt + ". Size the position so that loss is acceptable, and keep the alarm on.",
             "bad" if d30[1] - 1 <= -0.25 else "warn")
    with st.expander("LP value simulation table"):
        table(["Price move", f"LP {rng}", "LP full range", f"Holding {pair_unit(a)}"],
              [[f"{d * 100:.0f}%", (pct(v - 1), "neg"), (pct(full - 1), "neg"), "0%"] for d, v, full in a["sims"]])


def render(a):
    usd, f, lvl, qu = a["usd"], a["now"], a["level"], a["quote"]
    ui(f'<div class="hl-card hl-verdict {LEVEL_CLASS[lvl]}"><div class="t">{ICON[lvl]} {h(TITLE[lvl])}</div>'
       f'<div class="s">{h(a["advice"])}</div></div>')
    if a["breakout"]:
        ui(f'<div class="hl-note">{h(a["breakout"])}</div>')

    fdv = f["px"] * a["supply"]
    off = f["px"] / f["ath"] - 1
    u = a["usdg"] or {}
    main = u.get("main")
    liq = (("USDG pool liquidity", f"${compact(u['depth'])}", "", f"fee {main['fee'] / 1e4:g}% · age {f['age_h'] / 24:.1f} days")
           if main else ("Pool liquidity", f"{compact(f['eth_res'])} {a['quote']}", "", f"age {f['age_h'] / 24:.1f} days"))
    tiles([("Market cap", money(fdv, usd, qu), "", None),
           ("From ATH", pct(off), sign_class(off), f"ATH {money(f['ath'] * a['supply'], usd, qu)}"),
           ("Last 4 hours", pct(f["r4h"]), sign_class(f["r4h"]), None), liq])
    ui(f'<div class="hl-meta">{h(a["name"])} ({h(a["symbol"])}) · {h(a["address"])} · data up to {when(f["t"])} UTC</div>')
    summary_section(a)
    usdg_section(a)
    risk_section(a)
    reasons_section(a)
    liquidity_section(a)
    range_section(a)
    analog_section(a)
    comparison(a)
    tech_section(a)

    section("Last 72 hours")
    ch, df = chart(a)
    st.altair_chart(ch, theme=None)
    ui('<div class="hl-legend">' + "".join(f'<span style="--c:{c}">{v}</span>' for v, c in STATUS.items())
       + '</div><p class="hl-small">Top: market cap. Middle: whales selling (+) or buying (−) over 6h, % of supply. '
       'Bottom: the verdict the tool would have given every 5 minutes.</p>')
    rows72 = a["rows"]
    if rows72:
        chg = rows72[-1]["px"] / rows72[0]["px"] - 1
        hi72 = max(r["px"] for r in rows72)
        bad_share = sum(r["level"] == 2 for r in rows72) / len(rows72)
        wn = rows72[-1]["wn6"]
        off = 1 - rows72[-1]["px"] / hi72
        txt = (f"Over the last {(rows72[-1]['t'] - rows72[0]['t']) / 3600:.0f}h price moved {pct(chg)} and "
               + (f"now sits {off * 100:.0f}% below the period high." if off >= 0.005 else "is at the period high.")
               + f" The tool would have said AVOID "
               f"{bad_share * 100:.0f}% of the time. Whales {'sold' if wn > 0 else 'bought'} {abs(wn):.2f}% of supply (net) in the last 6h.")
        conclude(txt, "bad" if wn >= 1.5 else "warn" if bad_share >= 0.3 else "info")
    with st.expander("Chart data (table, last 24h)"):
        rows = [[when(r["t"]), money(r["px"] * a["supply"], usd, qu), (f"{r['wn6']:+.2f}%", sign_class(-r["wn6"])),
                 VERDICTS[r["level"]]] for r in a["rows"][::-1][:288]]
        table(["Time (UTC)", "Market cap", "Whales 6h (+sold/−bought)", "Verdict"], rows, tall=True)

    sleep_section(a)

    section("Alarm while the position is open")
    ui('<p class="hl-small" style="margin-bottom:6px">Get woken up when the verdict turns AVOID '
       '(set TELEGRAM_BOT_TOKEN & TELEGRAM_CHAT_ID):</p>'
       f'<div class="hl-code">python -m dump_alert.watch --token {h(a["address"])}</div>')

    with st.expander("Technical details: all filters"):
        rows = []
        for x in a["filters"]:
            mark = ("❌" if x["keras"] else "⚠️") if x["hit"] else "✅"
            rows.append([f"{mark} {x['code']}", (x["desc"], "lbl"), x["detail"]])
        table(["", "Filter", "Current value"], rows)
        rule = VERDICTS[a["rule_level"]]
        mp = f'{a["model_p"] * 100:.0f}%' if a["model_p"] is not None else "n/a"
        ui(f'<p class="hl-small">Rules alone would say <b>{rule}</b>; model estimate {mp}; mode '
           f'{"conservative" if a["mode"] == "conservative" else "balanced"}. K = hard filter (any K → at least CAUTION, '
           'AVOID in conservative mode). L = soft filter (two or more → CAUTION in the rules).</p>')
        pat = a["pattern"]
        if pat:
            ui(f'<p class="hl-small">Price pattern: {h(STATES[pat["state"]])}. High {h(money(pat["h1"] * a["supply"], usd, qu))} '
               f'({when(pat["h1_t"])}) → down to {h(money(pat["low"] * a["supply"], usd, qu))} '
               f'({pct(pat["low"] / pat["h1"] - 1)}) → retest {h(money(pat["retest"] * a["supply"], usd, qu))} '
               f'({when(pat["retest_t"])}).</p>')


def main():
    ui(CSS)
    ref = load_reference()
    names = [t["symbol"] for t in (ref or {}).get("tokens", [])]
    ui('<div class="hl-head"><span class="hl-logo">◆ LP Guard</span><span class="hl-pill">Robinhood Chain</span></div>'
       '<div class="hl-title">Check a coin before you LP and sleep</div>'
       '<p class="hl-sub">Paste a contract address. The coin is compared with how '
       f'{h(short_names(names, 4)) if names else "the reference coins"} looked right before they crashed, with similar '
       'moments in all sample coins, and checked for liquidity, supports, whales and technicals.</p>')
    keys = api_keys()
    with st.form("check"):
        ca = st.text_input("Contract address", placeholder="0x…")
        c1, c2 = st.columns(2)
        rng = c1.slider("Your planned LP range (±%)", 0, 100, 20, step=5, help="0 = full range")
        sleep_h = c2.slider("Hours you'll be away", 2, 12, 8)
        c3, c4 = st.columns(2)
        position = c3.number_input("Position size (USD)", min_value=10, max_value=10_000_000, value=1000, step=100,
                                   help="Used to estimate your share of the pool's fees: a big position in a thin pool "
                                        "earns less per dollar, because your own liquidity dilutes the fees.")
        conservative = c4.toggle("Conservative mode", help="Use the stricter of the rules and the model. In testing on "
                                 "busy coins it let fewer dumps slip through as OK (3.5% vs 8.4%), but says OK far less often.")
        at = st.text_input("Rewind to a past time (optional, UTC)", placeholder="2026-10-10 15:40",
                           help="Judge the coin as if you checked it at that moment — useful to test the verdict before an old dump.")
        go = st.form_submit_button("Check now", type="primary", width="stretch")
    with st.expander("How to read this"):
        ui('<ul class="hl-list">'
           '<li class="bad"><b>AVOID</b>: the model sees a 35%+ chance of a ≥30% drop while you sleep. In testing, a dump '
           'followed about half of these.</li>'
           '<li class="warn"><b>CAUTION</b>: a 15–35% chance, or a hard danger sign is on (whales selling, price just '
           'crashed, back near the ATH after a dip, or the token just launched).</li>'
           '<li class="good"><b>OK</b>: under 15%. Not a guarantee — coins can crash without warning. Keep an alarm on.</li>'
           '<li class="info">The first check of a coin takes a few minutes to pull its on-chain data; later checks are fast. '
           'Coins without a Uniswap pool (still on a bonding curve) cannot be judged yet.</li></ul>')
    if not go:
        return
    address = ca.strip().lower()
    if not (address.startswith("0x") and len(address) == 42):
        ui('<div class="hl-alert bad">That contract address is not valid (it must be 0x + 40 characters).</div>')
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
                       mode="conservative" if conservative else "balanced", position_usd=float(position))
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
