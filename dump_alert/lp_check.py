"""
Cek layak tidaknya membuka posisi LP sebuah token Robinhood Chain sebelum ditinggal tidur.

Masukkan contract address: program menarik data on-chain (inkremental), menilai filter price
action & whale saat ini (termasuk pola "retest ATH lalu gagal" seperti CRADLE dan PULP),
mencocokkan dengan kejadian serupa di token referensi, memberi vonis LAYAK / HATI-HATI /
JANGAN, peluang historis drop selama jam tidur, dan simulasi rugi LP kalau harga jatuh.

  ALCHEMY_API_KEY=... python -m dump_alert.lp_check 0xCONTRACT [--range 20] [--sleep 8]
  python -m dump_alert.lp_check --backtest           # uji filter di semua token di dump_alert/data/
  python -m dump_alert.lp_check --build-reference    # perbarui dump_alert/reference.json dari data/
"""
import argparse
import bisect
import calendar
import collections
import heapq
import json
import math
import os
import time

from . import levels
from .fetch import Fetcher, data_path, eth_usd, make_rpc, sqrt_to_price
from .patterns import retest_state
from .signals import INFLOW, ZERO, Token, find_dumps, launch_risk

H = 3600
H4 = 4 * H
STEP = 300
WHALE_PCT = 0.3   # whale = wallet yang pernah pegang >= 0.3% supply
BAD_DD = -0.30    # "dump" untuk kalibrasi: harga turun >= 30% dari harga masuk dalam jam tidur
REF_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reference.json")
REF_TOKENS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reference_tokens.txt")

# Filter KERAS (vonis JANGAN): di CRADLE dan PULP (Okt 2026) keduanya menaikkan peluang drop
# selama 8 jam dibanding saat tidak aktif. Filter LUNAK: lemah atau hanya di satu token.
FILTERS = [
    ("K1", True, "whales net-sold >= 1.5% of supply in 6h"),
    ("K2", True, "price fell >= 20% in 4h (falling knife)"),
    ("K3", True, "near the 24h high (<= 10% below it) after a >= 2x run from the 24h low"),
    ("K4", True, "launch phase: < 2h since the first buy"),
    ("K5", True, "ATH retest zone: back near the ATH after a >= 25% pullback"),
    ("K6", True, "failed ATH retest: rejected >= 15% from the retest high / broke range support"),
    ("L1", False, "whales net-sold >= 0.75% of supply in 1h"),
    ("L2", False, "6h volatility >= 8% per 5 minutes"),
    ("L3", False, "vertical pump >= 50% in 4h"),
    ("L4", False, "new large wallets bought >= 2% of supply in 2h (flippers)"),
    ("L5", False, "history: >= 25% of the time followed by a >= 30% drop within the sleep window"),
    ("L6", False, "launch risk (sniping / cheap claimed supply)"),
    ("L7", False, "new ATH on the 4h chart (verdict at least CAUTION)"),
]
DESC = {c: d for c, _, d in FILTERS}
EPISODE_CODES = ("K1", "K2", "K3", "K5", "K6")  # dicocokkan dengan kejadian di token referensi
VERDICTS = ["OK", "CAUTION", "AVOID"]


def pct(v, signed=True):
    return "-" if v is None else (f"{v * 100:+.0f}%" if signed else f"{v * 100:.0f}%")


def fmt_t(t):
    return time.strftime("%m-%d %H:%M", time.gmtime(t))


# ---------------------------------------------------------------- fitur

def features(tok: Token, start=None, end=None, step=STEP):
    """Fitur price action & whale tiap `step` detik (hanya data sebelum t)."""
    E, S, P = tok.events, tok.supply, tok.prices
    t_buy = next((e[0] for e in E if e[1] == "buy"), None)
    if not P or t_buy is None:
        return []
    pt, pv = tok._pt, [p[2] for p in P]
    ath = list(pv)
    for n in range(1, len(ath)):
        ath[n] = max(ath[n - 1], ath[n])
    # close candle 4 jam (UTC, seperti DexScreener) = harga terakhir di candle itu
    bclose = {}
    for t_, v in zip(pt, pv):
        bclose[t_ // H4] = v
    bkeys = sorted(bclose)
    bmax = []
    for bk in bkeys:
        bmax.append(max(bmax[-1], bclose[bk]) if bmax else bclose[bk])
    unit = 10 ** tok.ds["decimals"]
    res_t, res_v, R = [], [], 0.0  # cadangan token di market dari transfer mentah
    for x in tok.ds["transfers"]:
        d = (x[5] if x[4] in tok.markets else 0) - (x[5] if x[3] in tok.markets else 0)
        if d:
            R += d / unit
            res_t.append(x[1])
            res_v.append(R)
    whale = S * WHALE_PCT / 100
    bal, peak, first = collections.Counter(), collections.Counter(), {}
    wflow, fresh = collections.deque(), collections.deque()
    rows, i = [], 0
    end = end or max(pt[-1], E[-1][0])
    for t in range((pt[0] // step + 1) * step, end + 1, step):
        while i < len(E) and E[i][0] < t:
            et, k, a, v = E[i]
            i += 1
            bal[a] += v if k in INFLOW else -v
            if bal[a] > peak[a]:
                peak[a] = bal[a]
            if k == "buy":
                first.setdefault(a, et)
            if k in ("buy", "sell") and peak[a] >= whale:
                wflow.append((et, v if k == "sell" else -v))
                if k == "buy" and et - first[a] <= 6 * H:
                    fresh.append((et, v))
        while wflow and wflow[0][0] < t - 6 * H:
            wflow.popleft()
        while fresh and fresh[0][0] < t - 2 * H:
            fresh.popleft()
        if start and t < start:
            continue
        k = bisect.bisect_right(pt, t) - 1
        if k < 0:
            continue
        p = pv[k]

        def back(sec):
            m = bisect.bisect_right(pt, t - sec) - 1
            return p / pv[m] - 1 if m >= 0 and t - sec >= pt[0] else None

        w24 = pv[bisect.bisect_left(pt, t - 24 * H):k + 1]
        a6 = bisect.bisect_left(pt, t - 6 * H)
        # varians return per `step` detik, dinormalkan kalau jarak sampel lebih jarang (riwayat lama)
        lr = [math.log(pv[n] / pv[n - 1]) ** 2 * step / max(step, pt[n] - pt[n - 1]) for n in range(a6 + 1, k + 1)]
        j = bisect.bisect_left(res_t, t) - 1
        Rt = res_v[j] if j >= 0 else 0.0
        top10 = sum(heapq.nlargest(10, (b for b in bal.values() if b > 0)))
        jb = bisect.bisect_left(bkeys, t // H4)  # candle 4 jam yang sudah selesai sebelum candle sekarang
        ath4h = bmax[jb - 1] if jb > 0 else None
        rows.append({
            "t": t, "px": p, "ath": ath[k], "age_h": (t - t_buy) / H, "r1h": back(H), "r4h": back(4 * H),
            "ext24": p / min(w24) - 1, "off24": p / max(w24) - 1,
            "vol6": (sum(lr) / len(lr)) ** 0.5 * 100 if lr else 0.0,
            "wn1": sum(w[1] for w in wflow if w[0] >= t - H) / S * 100,
            "wn6": sum(w[1] for w in wflow) / S * 100,
            "fresh": sum(f[1] for f in fresh) / S * 100,
            "top10": top10 / S * 100, "reserve": Rt, "eth_res": Rt * p,
            "impact_top10": 1 - (Rt / (Rt + top10)) ** 2 if Rt > 0 else None,
            "pattern": retest_state(pt, pv, t, ath[k]),
            "ath4h": ath4h, "ath4h_break": ath4h is not None and p > ath4h,
        })
    return rows


def forward_dd(tok: Token, sleep_h: float):
    """Untuk tiap sampel harga: drawdown terdalam dalam `sleep_h` jam ke depan (None kalau belum lewat)."""
    pt, pv = tok._pt, [p[2] for p in tok.prices]
    out = []
    for n, t in enumerate(pt):
        e = bisect.bisect_right(pt, t + sleep_h * H)
        out.append(min(pv[n + 1:e]) / pv[n] - 1 if e > n + 1 and pt[-1] >= t + sleep_h * H else None)
    return out


def forward_rise(tok: Token, sleep_h: float):
    """Untuk tiap sampel harga: kenaikan tertinggi dalam `sleep_h` jam ke depan (None kalau belum lewat)."""
    pt, pv = tok._pt, [p[2] for p in tok.prices]
    out = []
    for n, t in enumerate(pt):
        e = bisect.bisect_right(pt, t + sleep_h * H)
        out.append(max(pv[n + 1:e]) / pv[n] - 1 if e > n + 1 and pt[-1] >= t + sleep_h * H else None)
    return out


class History:
    """Peluang drop >= 30% selama jam tidur di riwayat token sendiri, hanya dari jendela yang sudah selesai."""

    def __init__(self, tok: Token, sleep_h: float):
        self.pt, self.sleep = tok._pt, sleep_h * H
        dd = forward_dd(tok, sleep_h)
        self.cum = [0]
        for d in dd:
            self.cum.append(self.cum[-1] + (d is not None and d <= BAD_DD))

    def rate(self, t, min_hours=24):
        n = bisect.bisect_right(self.pt, t - self.sleep)
        if not self.pt or t - self.pt[0] < min_hours * H or n == 0:
            return None
        return self.cum[n] / n


# ---------------------------------------------------------------- filter & vonis

def pattern_text(pat, px):
    if not pat:
        return "no ATH retest pattern"
    s = (f"high → pullback {pct(pat['low'] / pat['h1'] - 1)} → retest at {pat['retest'] / pat['h1'] * 100:.0f}% "
         f"of the high; price now {pct(px / pat['retest'] - 1)} from the retest high")
    return s + (", below range support" if pat["state"] == "BREAKDOWN" else "")


def whale_text(wn):
    return f"bought {-wn:.1f}%" if wn < 0 else f"sold {wn:.1f}%"


RETEST_SHORT = {"RETEST": "retest", "TURUN": "fading", "GAGAL": "failed", "BREAKDOWN": "broke down"}


# Tanda yang terlihat di puncak tepat sebelum crash CRADLE & PULP (bahasa sederhana).
# (kode, label, aktif?, nilai yang ditampilkan)
SIGNS = [
    ("pump", "Price just ran up hard (≥20% in 4h)",
     lambda r: r["r4h"] is not None and r["r4h"] >= 0.20, lambda r: pct(r["r4h"])),
    ("lipat", "Price is already ≥1.8x its 24h low",
     lambda r: r["ext24"] >= 0.8, lambda r: f"{r['ext24'] + 1:.1f}x"),
    ("pucuk", "Price is near its 24h high (≤10% below)",
     lambda r: r["off24"] >= -0.10, lambda r: pct(r["off24"])),
    ("retest", "ATH retest pattern (back near the ATH after a ≥25% dip)",
     lambda r: r["pattern"] is not None, lambda r: RETEST_SHORT[r["pattern"]["state"]] if r["pattern"] else "no"),
    ("borong", "Whales net-bought ≥0.9% of supply in 6h (price being pumped)",
     lambda r: r["wn6"] <= -0.9, lambda r: whale_text(r["wn6"])),
    ("jual", "Whales net-sold ≥1.5% of supply in 6h",
     lambda r: r["wn6"] >= 1.5, lambda r: whale_text(r["wn6"])),
    ("baru", "New large wallets bought ≥1.5% of supply in 2h",
     lambda r: r["fresh"] >= 1.5, lambda r: f"{r['fresh']:.1f}%"),
    ("ath4j", "New ATH on the 4h chart (above the highest previous 4h close)",
     lambda r: bool(r.get("ath4h_break")),
     lambda r: pct(r["px"] / r["ath4h"] - 1) if r.get("ath4h") else "-"),
]


def signs_of(r):
    return {code for code, _, test, _ in SIGNS if test(r)}


def sign_values(r):
    return {code: fmt(r) for code, _, _, fmt in SIGNS}


MIN_SIGNS = 3  # crash tanpa tanda yang jelas tidak bisa dijadikan pembanding


def crashes(tok: Token, rows, n=3, drop=0.6, after_h=24):
    """Potret kondisi di puncak sebelum crash terdalam (bukan crash saat launch) yang punya
    minimal MIN_SIGNS tanda."""
    t_buy = next((e[0] for e in tok.events if e[1] == "buy"), None)
    if t_buy is None or not rows:
        return []
    ts = [r["t"] for r in rows]
    out = []
    for d in sorted((d for d in find_dumps(tok.prices, drop=drop) if d["peak_t"] - t_buy >= after_h * H),
                    key=lambda d: d["trough"] / d["peak"]):
        i = bisect.bisect_right(ts, d["peak_t"]) - 1
        if i < 0 or len(signs_of(rows[i])) < MIN_SIGNS:
            continue
        if len(out) == n:
            break
        r = rows[i]
        out.append({"symbol": tok.symbol, "address": tok.address, "peak_t": d["peak_t"],
                    "drop": round(d["trough"] / d["peak"] - 1, 3), "fdv": round(d["peak"] * tok.supply, 3),
                    "quote": tok.quote, "signs": sorted(signs_of(r)), "values": sign_values(r),
                    "level": r["level"], "ath4h_break": bool(r.get("ath4h_break"))})
    return out


def evaluate(f: dict, hist_rate, launch_flags):
    r4, pat = f["r4h"], f.get("pattern")
    state = pat["state"] if pat else None
    hits = {
        "K1": f["wn6"] >= 1.5,
        "K2": r4 is not None and r4 <= -0.20,
        "K3": f["off24"] >= -0.10 and f["ext24"] >= 1.0,
        "K4": f["age_h"] < 2,
        # tembus ATH chart 4 jam: retest/gagal tidak lagi memicu JANGAN, tapi vonis minimal HATI-HATI (L7)
        "K5": state in ("RETEST", "TURUN") and not f.get("ath4h_break"),
        "K6": state in ("GAGAL", "BREAKDOWN") and not f.get("ath4h_break"),
        "L1": f["wn1"] >= 0.75,
        "L2": f["vol6"] >= 8,
        "L3": r4 is not None and r4 >= 0.50,
        "L4": f["fresh"] >= 2,
        "L5": hist_rate is not None and hist_rate >= 0.25,
        "L6": bool(launch_flags),
        "L7": bool(f.get("ath4h_break")),
    }
    detail = {
        "K1": f"whale net sell 6h {f['wn6']:+.2f}% of supply",
        "K2": f"price 4h {pct(r4)}",
        "K3": f"{pct(f['off24'])} from the 24h high, {f['ext24'] + 1:.1f}x the 24h low",
        "K4": f"age {f['age_h']:.1f}h",
        "K5": pattern_text(pat, f["px"]),
        "K6": pattern_text(pat, f["px"]),
        "L1": f"whale net sell 1h {f['wn1']:+.2f}% of supply",
        "L2": f"volatility {f['vol6']:.1f}% per 5 minutes",
        "L3": f"price 4h {pct(r4)}",
        "L4": f"{f['fresh']:.1f}% of supply bought by new large wallets",
        "L5": "not enough history (< 24h)" if hist_rate is None else f"{hist_rate * 100:.0f}% of the time",
        "L6": "; ".join(launch_flags) if launch_flags else "no flags",
        "L7": (f"price {pct(f['px'] / f['ath4h'] - 1)} above the highest previous 4h close"
               if f.get("ath4h") else "no previous 4h candle yet"),
    }
    keras = [c for c, k, _ in FILTERS if k and hits[c]]
    lunak = [c for c, k, _ in FILTERS if not k and hits[c]]
    level = 2 if keras else 1 if (len(lunak) >= 2 or hits["L7"]) else 0
    return level, hits, detail


def lp_value(drop: float, range_pct: float, upper_pct=None) -> float:
    """Nilai posisi LP (dalam aset pasangan, mis. USDG, relatif ke awal) kalau harga berubah dengan faktor (1 + drop).

    range_pct = lebar range ± dari harga masuk (mis. 20 = -20%..+20%); 0 = full range.
    upper_pct = batas atas kalau range tidak simetris (mis. range_pct=35, upper_pct=70 -> -35%..+70%).
    """
    r = 1 + drop
    if range_pct <= 0:
        return math.sqrt(r)
    sa, sb = math.sqrt(1 - range_pct / 100), math.sqrt(1 + (range_pct if upper_pct is None else upper_pct) / 100)

    def v(p):
        sp = math.sqrt(p)
        if sp <= sa:
            return p * (1 / sa - 1 / sb)
        if sp >= sb:
            return sb - sa
        return 2 * sp - sa - p / sb

    return v(r) / v(1)


# ---------------------------------------------------------------- riwayat, backtest, referensi

def scan(tok: Token, sleep_h=8.0, start=None):
    """Vonis & filter tiap titik riwayat, plus drawdown yang benar-benar terjadi sesudahnya."""
    flags = launch_risk(tok).get("flags", [])
    hist = History(tok, sleep_h)
    dd, dd24 = forward_dd(tok, sleep_h), forward_dd(tok, 24)
    rows = features(tok, start=start)
    for f in rows:
        k = bisect.bisect_right(tok._pt, f["t"]) - 1  # drawdown relatif ke harga yang sama dengan f["px"]
        f["level"], f["hits"], _ = evaluate(f, hist.rate(f["t"]), flags)
        f["dd"], f["dd24"] = dd[k], dd24[k]
    return rows


def episodes(tok: Token, rows, gap=3 * H):
    """Awal tiap kejadian filter KERAS (aktif lagi sesudah >= 3 jam tidak aktif) dan hasilnya."""
    out, last = [], {}
    for f in rows:
        for c in EPISODE_CODES:
            if not f["hits"][c]:
                continue
            if f["t"] - last.get(c, -10 ** 12) > gap and f["dd"] is not None:
                st = f["pattern"]["state"] if f["pattern"] else None
                out.append({"symbol": tok.symbol, "address": tok.address, "code": c, "t": f["t"],
                            "fdv": round(f["px"] * tok.supply, 3), "quote": tok.quote, "state": st,
                            "dd": round(f["dd"], 4), "dd24": None if f["dd24"] is None else round(f["dd24"], 4)})
            last[c] = f["t"]
    return out


def reference_paths():
    """File data token pembanding dari reference_tokens.txt."""
    out = []
    for line in open(REF_TOKENS):
        ca = line.split("#")[0].strip().lower()
        if ca:
            out.append(data_path(ca))
    return out


def similarity(signs, crash_list):
    """Kemiripan tertinggi (0..1) dengan potret sebelum crash."""
    return max((len(signs & set(c["signs"])) / len(c["signs"]) for c in crash_list if c["signs"]), default=0.0)


def sim_bucket(x):
    return "≥80%" if x >= 0.8 else "50-79%" if x >= 0.5 else "<50%"


def build_reference(paths, sleep_h=8.0):
    ref = {"generated": int(time.time()), "sleep_h": sleep_h, "tokens": [], "verdicts": [], "episodes": [],
           "crashes": [], "similarity_test": []}
    scanned = []
    for path in paths:
        tok = Token(json.load(open(path)))
        rows = scan(tok, sleep_h)
        if not rows:
            continue
        scanned.append((tok, rows))
        ref["tokens"].append({"symbol": tok.symbol, "address": tok.address, "from": rows[0]["t"], "to": rows[-1]["t"]})
        ref["episodes"] += episodes(tok, rows)
        ref["crashes"] += crashes(tok, rows)
    # uji silang: tiap token dicocokkan hanya ke potret crash token LAIN
    for tok, rows in scanned:
        others = [c for c in ref["crashes"] if c["address"] != tok.address]
        b = collections.defaultdict(lambda: [0, 0, 0])
        for f in rows:
            if f["dd"] is not None and others:
                x = b[sim_bucket(similarity(signs_of(f), others))]
                x[0] += 1
                x[1] += f["dd"] <= -0.3
                x[2] += f["dd"] <= -0.5
        ref["similarity_test"].append({"symbol": tok.symbol, "address": tok.address, "buckets": dict(b)})
    ref["support_test"] = levels.support_test([tok for tok, _ in scanned])
    # model peluang dump (leave-one-token-out) + uji fitur; vonis akhir = model + pengaman filter keras
    from . import study
    data, toks = {}, {}
    for tok, _ in scanned:
        _, rs = study.rows_for(None, tok=tok)
        if len(rs) >= 20:
            for r in rs:
                r["tok"] = tok.symbol
            data[tok.symbol], toks[tok.symbol] = rs, tok
    names = list(study.BASE) + list(study.FLOW_NAMES) + list(study.EXTRA) + list(study.TECH_NAMES)
    ref["feature_test"] = study.feature_test(data, names)
    model, preds = study.train_model(data) if len(data) >= 3 else (None, {})
    ref["dump_model"] = model
    # statistik per vonis akhir (titik tiap 30 menit, prediksi model tanpa koin itu sendiri)
    ref["verdicts"], ref["drops"], ref["rises"] = [], [], []
    vt = {mode: [[0, 0, 0] for _ in range(3)] for mode in MODES}
    for sym, rs in data.items():
        tok = toks[sym]
        lv = {mode: [combine_level(r["level"], p_, mode) for r, p_ in zip(rs, preds.get(sym, [None] * len(rs)))]
              for mode in MODES}
        for mode in MODES:
            for r, x in zip(rs, lv[mode]):
                vt[mode][x][0] += 1
                vt[mode][x][1] += r["dd"] <= -0.3
                vt[mode][x][2] += r["dd"] <= -0.5
        for x in range(3):
            d = [r["dd"] for r, l_ in zip(rs, lv["balanced"]) if l_ == x]
            ref["verdicts"].append({"address": tok.address, "level": x, "n": len(d),
                                    "n30": sum(v <= -0.3 for v in d), "n50": sum(v <= -0.5 for v in d)})
        k = [bisect.bisect_right(tok._pt, r["t"]) - 1 for r in rs]
        for w in DROP_WINDOWS:  # sebaran turun & naik terdalam per vonis, untuk saran range (tidur 4/8/12 jam)
            dd, ru = forward_dd(tok, w), forward_rise(tok, w)
            hd = {x: [0] * 101 for x in range(3)}
            hr = {x: [0] * RISE_BINS for x in range(3)}
            for j, l_ in zip(k, lv["balanced"]):
                if dd[j] is not None:
                    hd[l_][drop_bin(dd[j])] += 1
                if ru[j] is not None:
                    hr[l_][rise_bin(ru[j])] += 1
            ref["drops"].append({"address": tok.address, "sleep_h": w, "hist": hd})
            ref["rises"].append({"address": tok.address, "sleep_h": w, "hist": hr})
    if model:
        model["thresholds"] = list(MODEL_T)
        model["verdict_test"] = {mode: [{"n": n, "p30": a / n if n else None, "p50": b / n if n else None}
                                        for n, a, b in vt[mode]] for mode in MODES}
    return ref


DROP_WINDOWS = (4, 8, 12)
# Vonis akhir: peluang dump dari model (< 15% OK, 15-35% CAUTION, >= 35% AVOID), dengan pengaman: filter keras
# aturan (whale jual, falling knife, launch, retest ATH) membuat vonis minimal CAUTION ("balanced"), atau
# vonis = yang paling berat dari aturan & model ("conservative"). Dipilih dari uji leave-one-token-out di 24
# koin sampel: saat koin ramai, OK lebih sering dan lebih aman, AVOID jauh lebih tegas daripada aturan saja.
MODEL_T = (0.15, 0.35)
MODES = ("balanced", "conservative")


def combine_level(rule_level, p, mode="balanced"):
    if p is None:
        return rule_level
    m = 0 if p < MODEL_T[0] else 1 if p < MODEL_T[1] else 2
    return max(m, rule_level) if mode == "conservative" else max(m, 1 if rule_level == 2 else 0)


def drop_bin(d):
    """Penurunan terdalam (negatif) -> bin 0..100 (persen turun, dibulatkan ke atas)."""
    return min(100, max(0, math.ceil(-d * 100 - 1e-9)))


RISE_BINS = 301  # kenaikan 0..300% (lebih tinggi masuk bin terakhir)


def rise_bin(r):
    return min(RISE_BINS - 1, max(0, math.ceil(r * 100 - 1e-9)))


def hist_quantile(hist, q):
    """Persen (turun/naik) yang tidak terlampaui di q bagian kasus, dari histogram per 1%."""
    n = sum(hist)
    if not n:
        return None
    acc = 0
    for pct_drop, c in enumerate(hist):
        acc += c
        if acc >= q * n:
            return pct_drop
    return len(hist) - 1


def range_advice(tok: Token, level, t, sleep_h, range_pct, ref):
    """Saran batas bawah range: penurunan yang tidak terlampaui di 80% / 90% kasus serupa,
    dari riwayat koin sendiri dan dari token pembanding dengan vonis yang sama (ambil yang lebih dalam)."""
    own = [0] * 101
    dd = forward_dd(tok, sleep_h)
    for tt, d in zip(tok._pt, dd):
        if d is not None and tt + sleep_h * H <= t:
            own[drop_bin(d)] += 1
    peer = [0] * 101
    if ref and ref.get("drops"):
        w = min(DROP_WINDOWS, key=lambda x: abs(x - sleep_h))
        for x in ref["drops"]:
            if x["address"] != tok.address and x["sleep_h"] == w:
                for i, c in enumerate(x["hist"][str(level)] if str(level) in x["hist"] else x["hist"][level]):
                    peer[i] += c
    out = {"n_own": sum(own), "n_peer": sum(peer)}
    for key, q in (("p80", 0.8), ("p90", 0.9)):
        cands = [v for v in (hist_quantile(own, q) if sum(own) >= 100 else None, hist_quantile(peer, q)) if v is not None]
        out[key] = max(cands) if cands else None
    breach = []
    for h in (own if sum(own) >= 100 else None, peer):
        if h and sum(h):
            breach.append(sum(h[int(range_pct) + 1:]) / sum(h) if range_pct > 0 else 0.0)
    out["breach"] = max(breach) if breach else None  # peluang harga turun melewati batas bawah range-mu
    for key in ("p80", "p90"):
        if out[key] is not None:
            out[key + "_round"] = min(95, 5 * math.ceil(out[key] / 5))
    return out


def peer_hist(ref, key, address, level, sleep_h, n):
    h = [0] * n
    if ref and ref.get(key):
        w = min(DROP_WINDOWS, key=lambda x: abs(x - sleep_h))
        for x in ref[key]:
            if x["address"] != address and x["sleep_h"] == w:
                for i, c in enumerate(x["hist"][str(level)] if str(level) in x["hist"] else x["hist"][level]):
                    h[i] += c
    return h


def range_plan(tok: Token, level, t, sleep_h, ref, floor):
    """Range LP lengkap dari harga sekarang. Batas bawah = `floor` (persen turun). Batas atas = kenaikan
    yang tidak terlampaui di 80% kasus serupa selama jam tidur (riwayat koin sendiri kalau >= 100 titik,
    kalau tidak dari token pembanding dengan vonis yang sama), dibulatkan ke atas ke 5%, minimal +10%.
    Plus peluang harga tetap di dalam range sepanjang jam tidur."""
    dd, ru = forward_dd(tok, sleep_h), forward_rise(tok, sleep_h)
    pairs = [(d, r) for tt, d, r in zip(tok._pt, dd, ru) if d is not None and r is not None and tt + sleep_h * H <= t]
    own_r = [0] * RISE_BINS
    for _, r in pairs:
        own_r[rise_bin(r)] += 1
    peer_r = peer_hist(ref, "rises", tok.address, level, sleep_h, RISE_BINS)
    peer_d = peer_hist(ref, "drops", tok.address, level, sleep_h, 101)
    own = len(pairs) >= 100
    p80 = hist_quantile(own_r if own else peer_r, 0.8)
    if p80 is None:
        return None
    ceiling = min(300, max(10, 5 * math.ceil(p80 / 5)))
    if own:
        inside = sum(1 for d, r in pairs if d >= -floor / 100 and r <= ceiling / 100) / len(pairs)
    elif sum(peer_r) and sum(peer_d):  # perkiraan: anggap turun & naik independen
        inside = (sum(peer_d[:floor + 1]) / sum(peer_d)) * (sum(peer_r[:ceiling + 1]) / sum(peer_r))
    else:
        inside = None
    return {"floor": floor, "ceiling": ceiling, "inside": inside, "own": own, "n": len(pairs) if own else sum(peer_r)}


def _json_default(o):
    """Angka numpy (int64/float64/bool_) -> tipe Python biasa saat menulis reference.json."""
    if hasattr(o, "item"):
        return o.item()
    raise TypeError(f"{type(o).__name__} tidak bisa ditulis ke JSON")


def load_reference(path=REF_PATH):
    return json.load(open(path)) if os.path.exists(path) else None


def backtest(paths, sleep_h=8.0):
    stats = {}
    for path in paths:
        tok = Token(json.load(open(path)))
        rows = scan(tok, sleep_h)
        g = collections.defaultdict(list)
        for f in rows:
            if f["dd"] is not None:
                g[f["level"]].append(f["dd"])
        g["dumps"] = dump_entries(tok, rows)
        g["exits"] = exit_alerts(rows, sleep_h)
        g["episodes"] = episodes(tok, rows)
        stats[tok.symbol] = g
    return stats


def dump_entries(tok, rows):
    """Vonis kalau LP dibuka 1 jam / 30 menit sebelum puncak tiap episode dump."""
    ts = [r["t"] for r in rows]
    out = []
    for d in find_dumps(tok.prices):
        lv = []
        for off in (H, 1800, 0):
            i = bisect.bisect_right(ts, d["peak_t"] - off) - 1
            lv.append(rows[i]["level"] if i >= 0 else None)
        out.append((d, lv))
    return out


def exit_alerts(rows, sleep_h):
    """Posisi dibuka saat LAYAK lalu harga jatuh >= 30% selama tidur: apakah vonis JANGAN (alert watch)
    muncul sebelum harga menembus -30%, dan di harga berapa."""
    n_bad, n_early, at_alert = 0, 0, []
    for i, r in enumerate(rows):
        if r["level"] != 0 or r["dd"] is None or r["dd"] > BAD_DD:
            continue
        n_bad += 1
        for x in rows[i + 1:]:
            if x["t"] > r["t"] + sleep_h * H or x["px"] / r["px"] - 1 <= BAD_DD:
                break
            if x["level"] == 2:
                n_early += 1
                at_alert.append(x["px"] / r["px"] - 1)
                break
    return n_bad, n_early, at_alert


def summarize(g):
    n = sum(len(g.get(lvl, [])) for lvl in range(3))
    lines = []
    for lvl in range(3):
        d = g.get(lvl, [])
        if d:
            lines.append(f"  {VERDICTS[lvl]:9s} {100 * len(d) / n:3.0f}% waktu | drop >=30%: {sum(x <= -0.3 for x in d) / len(d) * 100:3.0f}% "
                         f"| drop >=50%: {sum(x <= -0.5 for x in d) / len(d) * 100:3.0f}% | median {sorted(d)[len(d) // 2] * 100:+.0f}%")
    return lines


def pooled(stats):
    g = collections.defaultdict(list)
    for s in stats.values():
        for lvl in range(3):
            g[lvl] += s.get(lvl, [])
    return g


# ---------------------------------------------------------------- penilaian sekarang

def parse_time(s: str) -> int:
    """'2026-10-10 15:30' (UTC) -> unix detik."""
    return calendar.timegm(time.strptime(s.strip(), "%Y-%m-%d %H:%M"))


def truncate(ds: dict, t: int) -> dict:
    """Salinan dataset seolah data hanya sampai waktu t (untuk memutar ulang penilaian di masa lalu)."""
    return {**ds, "transfers": [x for x in ds["transfers"] if x[1] < t],
            "prices": [p for p in ds["prices"] if p[0] <= t]}


def reasons(hits, f, rate, flags):
    """Plain-English reasons for the verdict: (main reasons from hard filters, minor warnings)."""
    pat = f.get("pattern")
    main, minor = [], []
    if hits["K5"]:
        main.append(f"Price came back to its all-time high after a {pct(pat['low'] / pat['h1'] - 1)} dip. CRADLE and PULP "
                    "crashed right after this same pattern.")
    if hits["K6"]:
        main.append(f"Price tried to break its all-time high and failed; it is already {pct(f['px'] / pat['retest'] - 1)} "
                    "below that try" + (" and broke through its floor." if pat["state"] == "BREAKDOWN" else "."))
    if hits["K1"]:
        main.append(f"Big holders are selling: {f['wn6']:.1f}% of all coins sold (net) in the last 6 hours.")
    if hits["K2"]:
        main.append(f"Price already fell {pct(f['r4h'])} in 4 hours. Coins falling this fast usually keep falling.")
    if hits["K3"]:
        main.append(f"Price is near its 24h high after a {f['ext24'] + 1:.1f}x run. Holders often take profit here.")
    if hits["K4"]:
        main.append("The coin launched less than 2 hours ago. Early buyers are still selling.")
    if hits["L1"]:
        minor.append(f"Big holders sold {f['wn1']:.1f}% of all coins in the last hour.")
    if hits["L2"]:
        minor.append("Price is swinging very hard right now.")
    if hits["L3"]:
        minor.append(f"Price is up {pct(f['r4h'])} in 4 hours (a very steep pump).")
    if hits["L4"]:
        minor.append(f"New big wallets bought {f['fresh']:.1f}% of all coins in 2 hours. They often sell quickly.")
    if hits["L5"]:
        minor.append(f"In its own history, this coin fell 30%+ within 8 hours {rate * 100:.0f}% of the time.")
    if hits["L6"]:
        minor.append("At launch: " + "; ".join(flags) + ".")
    return main, minor


def breakout_note(f, ref, address):
    """'New 4h ATH' note + how many reference crashes happened right after the same condition."""
    if not f.get("ath4h_break"):
        return None
    cs = [c for c in (ref or {}).get("crashes", []) if c["address"] != address]
    n = sum(1 for c in cs if c.get("ath4h_break"))
    s = f"📈 New high on the 4h chart: price is {pct(f['px'] / f['ath4h'] - 1)} above the best 4h close so far."
    if cs:
        s += (f" Breakouts can keep going, but {n} of {len(cs)} big crashes in the sample coins started right after a "
              "breakout like this.")
    return s


def _age(h):
    return f"{h:.0f} hours" if h < 48 else f"{h / 24:.0f} days"


# kalimat sederhana untuk faktor model (yang menaikkan risiko / yang menurunkan risiko)
FACTOR_TEXT = {
    "off_ath": lambda x: f"price is right at its all-time high ({pct(x)})" if x > -0.3 else f"price is {pct(x)} below its all-time high",
    "ext24": lambda x: f"price already went {x + 1:.1f}x from its 24h low",
    "r4h": lambda x: f"price moved {pct(x)} in the last 4 hours",
    "vol6": lambda x: f"price is swinging a lot ({x:.1f}% per 5 minutes)",
    "fresh": lambda x: f"new big wallets bought {x:.1f}% of all coins in 2 hours",
    "wn6": lambda x: f"big holders {'sold' if x > 0 else 'bought'} {abs(x):.1f}% of all coins in 6 hours",
    "log_age": lambda x: f"the coin is still young ({_age(math.expm1(x))} old)",
    "buyers2": lambda x: f"trading is very hot ({x:.0f} buyers in 2 hours)",
    "sellers2": lambda x: f"many wallets are selling ({x:.0f} sellers in 2 hours)",
    "profit3": lambda x: f"{x:.0f}% of all coins sit with holders who are up 3x or more (they may take profit)",
    "early": lambda x: f"first-hour buyers (snipers) still hold {x:.0f}% of all coins",
    "vol_ratio2": lambda x: f"trading volume is {x:.1f}× its normal level",
    "liq_share": lambda x: f"little liquidity for its size (the pools hold only {x:.1f}% of all coins)",
    "ath4h_break": lambda x: "it just made a new high on the 4h chart",
    "pat_retest": lambda x: "price came back to its old high (a common top pattern)",
    "pat_gagal": lambda x: "price failed to break its old high",
    "top_buy4": lambda x: f"buying comes from many small wallets (the biggest buyer made {x * 100:.0f}% of 4h buys)",
    "bb_width": lambda x: f"price is moving in a wide band ({x * 100:.0f}% of price)",
    "rsi1h": lambda x: f"RSI (1h) is {x:.0f}",
    "hour_sin": lambda x: "this time of day has seen more crashes",
    "hour_cos": lambda x: "this time of day has seen more crashes",
}
CALM_TEXT = {
    "off_ath": lambda x: f"price is {pct(x)} below its all-time high",
    "ext24": lambda x: f"price is only {x + 1:.1f}x its 24h low",
    "r4h": lambda x: f"price moved {pct(x)} in 4 hours",
    "vol6": lambda x: f"price is fairly calm ({x:.1f}% per 5 minutes)",
    "log_age": lambda x: f"the coin is {_age(math.expm1(x))} old",
    "buyers2": lambda x: f"trading is calm ({x:.0f} buyers in 2 hours)",
    "sellers2": lambda x: f"few wallets are selling ({x:.0f} in 2 hours)",
    "liq_share": lambda x: f"good liquidity for its size (the pools hold {x:.1f}% of all coins)",
    "profit3": lambda x: f"only {x:.0f}% of all coins sit with holders up 3x or more",
    "early": lambda x: f"first-hour buyers hold only {x:.0f}% of all coins",
    "top_buy4": lambda x: f"the biggest buyer made {x * 100:.0f}% of 4h buys",
    "bb_width": lambda x: f"price is moving in a narrow band ({x * 100:.0f}% of price)",
    "vol_ratio2": lambda x: f"trading volume is {x:.1f}× its normal level",
    "fresh": lambda x: f"new big wallets bought only {x:.1f}% in 2 hours",
    "wn6": lambda x: f"big holders {'sold' if x > 0 else 'bought'} {abs(x):.1f}% of all coins in 6 hours",
    "rsi1h": lambda x: f"RSI (1h) is {x:.0f}",
}


def model_factors(contrib, x, n=3, sign=1):
    """Faktor yang paling menaikkan (sign=1) atau menurunkan (sign=-1) peluang dump, dalam kalimat biasa."""
    out, seen = [], set()
    texts = FACTOR_TEXT if sign > 0 else CALM_TEXT
    for name, c in sorted(contrib.items(), key=lambda kv: -kv[1] * sign):
        if c * sign <= 0.15 or len(out) >= n:
            break
        txt = texts.get(name)
        if txt is None or x.get(name) is None:
            continue
        t = txt(x[name])
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def assess(tok: Token, range_pct=20.0, sleep_h=8.0, ref=None, usd_per_eth=None, history_h=48, mode="balanced",
           position_usd=1000.0, upper_pct=None, stay_target=0.9, single_sided=False, entry_mcap=None):
    """Penilaian lengkap untuk titik data terakhir (plus riwayat `history_h` jam untuk grafik);
    dipakai CLI dan web app."""
    if not tok._pt:
        return None
    rows = scan(tok, sleep_h, start=tok._pt[-1] - history_h * H)
    if not rows:
        return None
    f = rows[-1]
    flags = launch_risk(tok).get("flags", [])
    rate = History(tok, sleep_h).rate(f["t"])
    rule_level, hits, detail = evaluate(f, rate, flags)
    tview, analog = current_view(tok, rows, f)
    model_p, factors, calm = None, [], []
    if ref and ref.get("dump_model"):
        from .study import model_prob, row_x
        x = row_x(f, tok._pt, [p[2] for p in tok.prices], tview, tview)
        model_p, contrib = model_prob(ref["dump_model"], x, factors=True)
        factors, calm = model_factors(contrib, x), model_factors(contrib, x, sign=-1)
    level = combine_level(rule_level, model_p, mode)
    q = tok.quote or "quote"
    m = tok.ds.get("market") or {}
    # pengali harga -> USD: 1 untuk harga dari pool USDG, harga ETH hari ini untuk pool ETH, None kalau tidak tahu
    usd = 1.0 if m.get("usd") else usd_per_eth if q in ("ETH", "WETH") else None
    pools = m.get("usdg_pools") or []
    lp = m.get("lp_pool") or m   # pool USDG tempat LP (data lama: sama dengan sumber harga)
    main = next((p for p in pools if p["pool_id"] == lp.get("pool_id")), None)
    usdg = {"pools": pools, "main": main, "depth": 2 * main["usdg_side"] if main else None,
            "since_block": m.get("usdg_from_block"), "pre": (m.get("pre") or {}).get("pair_symbol")
            or (m.get("pre") or {}).get("quote_symbol"),
            # sumber harga lain dari pool LP: harga pasar dari pool paling ramai, plus harga pool USDG-mu sendiri
            "price_pool": None if not main or m.get("pool_id") == main["pool_id"] else
            {"pair": m.get("pair_symbol") or m.get("quote_symbol"), "fee": m.get("fee")},
            "pool_px": pool_price(m, main["pool_id"], tok.ds.get("decimals", 18)) if main else None} \
        if m.get("kind") == "v4" else None

    calib = None
    if ref:
        v = [x for x in ref["verdicts"] if x["address"] != tok.address and x["level"] == level]
        n = sum(x["n"] for x in v)
        if n:
            calib = {"n_tokens": len({x["address"] for x in v}), "n": n, "sleep_h": ref["sleep_h"],
                     "p30": sum(x["n30"] for x in v) / n, "p50": sum(x["n50"] for x in v) / n}
    similar = []
    if ref:
        for c in EPISODE_CODES:
            if not hits[c]:
                continue
            eps = sorted((e for e in ref["episodes"] if e["code"] == c and e["address"] != tok.address),
                         key=lambda e: e["t"])
            if eps:
                d = sorted(e["dd"] for e in eps)
                similar.append({"code": c, "desc": DESC[c], "n": len(eps), "median_dd": d[len(d) // 2],
                                "p30": sum(x <= -0.3 for x in d) / len(d), "examples": eps})

    compare = None
    if ref and ref.get("crashes"):
        now = signs_of(f)
        items = []
        for c in ref["crashes"]:
            if c["address"] == tok.address:
                continue
            fp = set(c["signs"])
            items.append({**c, "match": len(now & fp), "total": len(fp), "sim": similarity(now, [c])})
        items.sort(key=lambda x: (-x["sim"], x["drop"]))
        if items:
            compare = {"now": sorted(now), "values": sign_values(f), "crashes": items, "best": items[0]}
    bumped = False
    if compare and compare["best"]["sim"] >= 0.8 and level == 0:
        level, bumped = 1, True  # mirip sekali dengan kondisi sebelum crash referensi

    info = []
    if main:
        if usdg["depth"] < 20_000:
            info.append(f"The USDG pool is thin (~${usdg['depth']:,.0f}): your position could be a big share of it, "
                        "and small sells move the price a lot.")
    elif lp.get("fee") == 0 and lp.get("hooks"):
        info.append("The main pool's LP fee is 0 (the hook takes the swap fee). LPs in this pool earn no swap fees — "
                    "make sure you LP in a pool that pays fees.")
    if f["impact_top10"] is not None:
        info.append(f"If the top-10 wallets sold everything into the pool, price could fall ~{f['impact_top10'] * 100:.0f}%.")
    if not main and q in ("ETH", "WETH") and f["eth_res"] < 10:
        info.append(f"Thin liquidity (~{f['eth_res']:.1f} ETH in the pool): your position could be a big share of it, "
                    "and small sells move the price a lot.")
    nb = sum(1 for t, k, _, _ in tok.events if k == "buy" and t >= f["t"] - 24 * H)
    ns = sum(1 for t, k, _, _ in tok.events if k == "sell" and t >= f["t"] - 24 * H)
    info.append(f"Last 24h activity: {nb:,} buys, {ns:,} sells.")

    lv = support_levels(tok, f, m, ref)
    ra = range_advice(tok, level, f["t"], sleep_h, range_pct, ref)
    plan = None
    if ra.get("p80_round") is not None:
        floor, why = ra["p80_round"], "history"
        hint = levels.floor_hint(lv["zones"], floor)
        if hint["alt"] and hint["alt"]["floor"] <= (ra.get("p90_round") or 95):  # tepat di bawah support terdekat
            floor, why = hint["alt"]["floor"], "support"
        plan = range_plan(tok, level, f["t"], sleep_h, ref, floor)
        if plan:
            plan.update(floor_why=why, support=hint["alt"]["zone"] if why == "support" else None,
                        resistance=levels.resistances(tok, f["t"], f["px"]))

    upper_pct = 0.0 if single_sided else range_pct if upper_pct is None else upper_pct
    own = own_windows(tok, f["t"], sleep_h)
    best_range = optimal_range(tok, m, f, usd, rule_level, model_p, sleep_h, range_pct, position_usd, upper_pct, stay_target,
                               own, single_sided)
    from . import holders as hold
    top = hold.top_holders(tok, f["t"])
    flow_now = hold.money_flow(tok, f["t"])
    move = expected_move(tok, level, f["t"], sleep_h, ref)
    exits = exit_plan(f, lv, best_range, own, tview, flow_now, ref)
    if single_sided:
        exits.update(single_exit(f, lv, best_range, range_pct))
    # entry_mcap: market cap saat posisi dibuka, dalam USD (atau aset quote kalau kurs USD tidak ada)
    supply = circulating_supply(tok)   # untuk market cap (tanpa koin yang di-burn), seperti DexScreener
    open_pos = (position_plan(f, entry_mcap / (usd or 1.0) / supply, range_pct, upper_pct, lv, best_range, own,
                              exits.get("momentum")) if entry_mcap and range_pct > 0 else None)

    advice = {2: "Don't open an LP now. Wait for the danger signs to clear, then check again.",
              1: "High risk. If you still go in: small size, wide range, and keep an alarm on.",
              0: "Lower risk, not zero. Keep an alarm on while the position is open."}[level]
    main_reasons, minor_reasons = reasons(hits, f, rate, flags)
    if model_p is not None:
        why = (" Main reasons: " + "; ".join(factors) + ".") if factors else ""
        line = f"Our model gives a {model_p * 100:.0f}% chance of a 30%+ drop in the next {sleep_h:g} hours.{why}"
        (main_reasons if model_p >= MODEL_T[0] else minor_reasons).insert(0, line)
        if rule_level == 2 and level < 2 and mode == "balanced":
            main_reasons.append("A strong danger sign is on, so the verdict is at least CAUTION even though the "
                                "model's number is low.")
    if bumped:
        b = compare["best"]
        main_reasons.insert(0, f"Conditions look like {b['symbol']} right before its {pct(b['drop'])} crash "
                               f"({b['match']} of {b['total']} signs match).")
    return {
        "symbol": tok.symbol, "name": tok.ds.get("name"), "address": tok.address, "quote": q,
        "supply": supply, "supply_total": tok.supply, "burned": tok.supply - supply, "usd": usd, "eth_usd": usd_per_eth, "usdg": usdg, "market": m, "now": f, "rows": rows,
        "level": level, "verdict": VERDICTS[level], "rule_level": rule_level, "mode": mode, "factors": factors, "calm_factors": calm,
        "hits": hits, "detail": detail,
        "filters": [{"code": c, "keras": k, "desc": d, "hit": hits[c], "detail": detail[c]} for c, k, d in FILTERS],
        "pattern": f["pattern"], "hist_rate": rate, "calib": calib, "similar": similar, "compare": compare,
        "reasons": main_reasons, "minor_reasons": minor_reasons,
        "breakout": breakout_note(f, ref, tok.address),
        "range_advice": ra, "range_plan": plan, "best_range": best_range, "position_usd": position_usd,
        "holders": top, "holder_summary": hold.holder_summary(top), "flow": flow_now, "move": move, "exits": exits,
        "ref_names": [x["symbol"] for x in (ref or {}).get("tokens", []) if x["address"] != tok.address],
        "similarity_test": [x for x in (ref or {}).get("similarity_test", []) if x["address"] != tok.address],
        "launch_flags": flags, "info": info, "advice": advice, "range_pct": range_pct, "upper_pct": upper_pct,
        "single_sided": single_sided, "open_pos": open_pos,
        "sleep_h": sleep_h, "sims": [(d, lp_value(d, range_pct, upper_pct), lp_value(d, 0)) for d in (-0.2, -0.3, -0.5, -0.7)],
        "levels": lv, "tech": tview, "analogs": analog, "model_p": model_p,
        "feature_test": (ref or {}).get("feature_test"), "model": (ref or {}).get("dump_model"),
    }


def expected_move(tok, level, t, sleep_h, ref):
    """Seberapa jauh harga biasanya turun & naik selama jam tidur (kuantil 50/80/90%): dari riwayat koin sendiri
    (jendela yang sudah selesai, kalau >= 100) dan dari koin sampel dengan vonis yang sama."""
    dd, ru = forward_dd(tok, sleep_h), forward_rise(tok, sleep_h)
    own_d, own_r, n = [0] * 101, [0] * RISE_BINS, 0
    for tt, d, r in zip(tok._pt, dd, ru):
        if d is not None and r is not None and tt + sleep_h * H <= t:
            own_d[drop_bin(d)] += 1
            own_r[rise_bin(r)] += 1
            n += 1
    out = []
    peer_d = peer_hist(ref, "drops", tok.address, level, sleep_h, 101)
    peer_r = peer_hist(ref, "rises", tok.address, level, sleep_h, RISE_BINS)
    for name, hd, hr, cnt in (("This coin's history", own_d, own_r, n), ("Sample coins, same verdict", peer_d, peer_r, sum(peer_d))):
        if cnt >= (100 if name.startswith("This") else 1) and sum(hd) and sum(hr):
            out.append({"source": name, "n": cnt, "down": {q: hist_quantile(hd, q / 100) for q in (50, 80, 90)},
                        "up": {q: hist_quantile(hr, q / 100) for q in (50, 80, 90)}})
    return out


_RANGES = []


# sinyal teknikal saat harga mendekati resistance (diuji di resistance_test): yang membuat resistance lebih sering
# TEMBUS (momentum) dan yang membuatnya lebih sering MENAHAN harga
EXIT_SIGNALS = [
    ("rsi70", "RSI (1h) is 70 or higher", lambda x: (x.get("rsi1h") or 0) >= 70),
    ("volspike", "Volume is 2x normal or more", lambda x: (x.get("vol_ratio2") or 0) >= 2),
    ("bb", "Price is at or above the upper Bollinger band", lambda x: (x.get("bb_pctb") or 0) >= 1.0),
    ("stretch", "Price is 15%+ above its 1h average (EMA20)", lambda x: (x.get("ema_gap") or 0) >= 0.15),
    ("sellers", "More selling than buying in the last 4 hours", lambda x: x.get("buy_share4") is not None and x["buy_share4"] < 0.5),
    ("lower_highs", "Chart makes lower highs (1h)", lambda x: (x.get("structure") or 0) == -1),
]
MIN_TP = 0.08    # level ambil untung paling dekat: +8% dari harga sekarang
OPEN_MIN_TP = 0.05  # posisi yang sudah dibuka: good exit minimal +5% (lebih dekat = praktis tutup sekarang)
BREAK = 0.95     # floor dianggap jebol 5% di bawah tepi bawah zonanya (definisi yang sama dengan support_test)
STOP_MAX_REACH = 0.25  # batas tutup = floor yang jebolnya jarang (<= 1 dari 4 malam): turun biasa tidak memicu


def exit_plan(f, lv, rec, own, tview, flow, ref):
    """Tempat exit / ambil untung: zona resistance di atas harga dengan peluang menahan harga (dari uji, sesuai kekuatan
    zona dan momentum sekarang) dan peluang tersentuh selama jam tidur; level ambil untung = zona terdekat yang cukup
    kuat; saran batas atas tepat di bawahnya; batas keluar darurat (floor terdekat); dan sinyal teknikal sekarang."""
    import numpy as np
    zones, px = list((lv or {}).get("res") or []), f["px"]
    rt = (ref or {}).get("resistance_test") or {}
    x = {**(tview or {}), "wn6": f.get("wn6")}
    mom = levels.momentum(x)
    peer = (rec or {}).get("peer_max")
    for z in zones:
        lo = z["price"] * (1 - levels.ZONE)
        z["reach_own"] = float(np.mean(own[1] >= lo / px)) if own is not None else None
        z["reach_peer"] = float(np.mean(peer >= lo / px)) if peer is not None and len(peer) else None
        ok = [v for v in (z["reach_own"], z["reach_peer"]) if v is not None]
        z["reach"] = max(ok) if ok else None
        st = ((rt.get("zones") or {}).get(z["strength"]) or {}).get("momentum" if mom else "calm") or {}
        z["hold"], z["hold_n"] = st.get("rate"), st.get("n")
    # ambil untung minimal +8% (ruang untuk fee; level yang lebih dekat praktis berarti keluar sekarang), maksimal +150%
    near = [z for z in zones if z["up"] * (1 - levels.ZONE) - levels.ZONE >= MIN_TP and z["up"] <= 1.5]
    # hanya zona strong: di uji harga pasar hanya itu yang lebih sering menahan daripada harga acak (48% vs 37%)
    target = next((z for z in near if z["strength"] == "strong"), None)
    nxt = next((z for z in zones if target and z["price"] > target["price"]), None)
    take = None
    if target:
        lo = target["price"] * (1 - levels.ZONE)
        take = {"zone": target, "price": lo, "up": lo / px - 1, "upper_bound": max(1, math.floor((lo / px - 1) * 100) - 1)}
    floor = next((z for z in (lv or {}).get("zones", []) if z["strength"] in ("strong", "medium")), None)
    test = rt.get("signals") or {}
    sig = [{"code": code, "text": text, "on": bool(fn(x)), "test": test.get(code)} for code, text, fn in EXIT_SIGNALS]
    w4 = next((w for w in flow or [] if w["hours"] == 4), None)
    return {"zones": zones[:6], "take": take, "next": nxt, "floor": floor, "signals": sig, "momentum": mom,
            "whales_selling": bool(w4 and w4["whale_net"] <= -0.005), "test": rt.get("zones")}


def single_exit(f, lv, rec, plan_lower):
    """Exit untuk single-sided USDG (range dari harga sekarang ke bawah). Saat dibuka posisi 100% USDG dan tidak
    memegang koin, jadi resistance di atas harga tidak berpengaruh. Posisi baru memegang koin kalau harga turun ke dalam
    range; kalau harga lalu naik lagi, posisi otomatis menjual kembali ke USDG sampai harga buka (batas atas = exit
    otomatis). Floor di dalam range yang ditembus harga akan berubah jadi resistance saat harga memantul: di situ
    pantulan bisa tertahan, tempat untuk menutup LP kalau tidak mau menunggu sampai harga buka."""
    pick = (((rec or {}).get("safe") or {}).get("pick") or {}).get("range")
    lower = pick[0] if pick else plan_lower
    stall = [z for z in (lv or {}).get("zones", []) if -lower / 100 <= z["drop"] < 0]
    return {"single": True, "lower": lower, "stall": stall, "auto_exit": f["px"]}


def circulating_supply(tok):
    """Supply untuk market cap, sama seperti DexScreener: total supply sekarang (mint - burn ke 0x0, dari riwayat
    transfer) dikurangi koin di alamat burn (0x…dead) yang tidak akan pernah beredar. tok.supply (totalSupply saat data
    pertama ditarik) tetap dipakai untuk porsi 'dari semua koin'."""
    from .holders import BURN
    dec, mint, burn, dead = tok.ds.get("decimals", 18), 0, 0, 0
    for x in tok.ds.get("transfers") or []:
        frm, to, v = x[3], x[4], x[5]
        mint += v if frm == ZERO else 0
        burn += v if to == ZERO else 0
        dead += (v if to in BURN else 0) - (v if frm in BURN else 0)
    total = (mint - burn) / 10 ** dec
    if not 0.5 * tok.supply <= total <= 1.01 * tok.supply:  # mint tidak lewat Transfer standar: pakai totalSupply
        total = tok.supply
    return max(total - max(dead, 0) / 10 ** dec, 0.01 * total)


def pool_price(m, pool_id, dec_token):
    """Harga token di pool `pool_id` (USD) dari peta likuiditas terakhir (paling lama 15 menit); None kalau tidak ada."""
    p = next((x for x in ((m.get("book") or {}).get("pools") or []) if x["pool_id"] == pool_id), None)
    if not p or not p.get("sqrt") or not p.get("usd_per_quote"):
        return None
    return sqrt_to_price(p["sqrt"], p["token_is_c1"], dec_token, p["quote_decimals"]) * p["usd_per_quote"]


def parse_mcap(text):
    """'8.38M', '$8,38m', '850k', '1.2b', '8380000' -> angka (None kalau kosong / tidak terbaca)."""
    t = (text or "").strip().lower().replace("$", "").replace(" ", "").replace("usd", "")
    if not t:
        return None
    mult = 1.0
    for suf, m in (("jt", 1e6), ("rb", 1e3), ("k", 1e3), ("m", 1e6), ("b", 1e9)):
        if t.endswith(suf):
            t, mult = t[:-len(suf)], m
            break
    if mult > 1 and "," in t and "." not in t and t.count(",") == 1:
        t = t.replace(",", ".")
    try:
        v = float(t.replace(",", "")) * mult
    except ValueError:
        return None
    return v if v > 0 else None


def lp_split(r, lower, upper):
    """Isi posisi di rasio harga r = harga / harga buka: (nilai koin, nilai USDG) relatif ke modal awal."""
    sa, sb = math.sqrt(1 - lower / 100), math.sqrt(1 + upper / 100)
    sp = min(max(math.sqrt(r), sa), sb)
    v0 = 2 - sa - 1 / sb if sa < 1 < sb else (1 - sa if sb <= 1 else 1 / sa - 1 / sb)  # nilai di harga buka per likuiditas
    return r * (1 / sp - 1 / sb) / v0, (sp - sa) / v0


def position_plan(f, entry_px, lower, upper, lv, rec, own, mom):
    """Rencana exit untuk posisi yang SUDAH dibuka: range dari harga buka (entry_px) -lower% .. +upper% (upper 0 =
    single-sided USDG). Dari harga sekarang: posisi ada di mana (di atas / di dalam / di bawah range), isinya (koin vs
    USDG) dan nilainya; target di atas harga = zona resistance antara harga sekarang dan batas atas (termasuk floor yang
    sudah ditembus — teruji jadi atap) plus batas atas sendiri (exit otomatis); 'good exit' = tepat di bawah zona
    pertama yang cukup kuat (peluang menolak >= 40% sesuai momentum sekarang), kalau tidak ada: batas atas. Batas
    keluar di bawah = floor strong/medium terdekat. Peluang tersentuh selama jam tidur dari riwayat koin ini & situasi
    serupa. Batas tutup = floor strong/medium pertama yang jebolnya jarang (<= STOP_MAX_REACH malam), supaya turun
    biasa tidak memicu. Nilai relatif ke modal awal, belum termasuk fee yang sudah didapat."""
    import numpy as np
    px = f["px"]
    pa, pb = entry_px * (1 - lower / 100), entry_px * (1 + upper / 100)
    peer_max, peer_min = (rec or {}).get("peer_max"), (rec or {}).get("peer_min")

    def value(p):
        return lp_value(p / entry_px - 1, lower, upper)

    def reach(p):
        up = p >= px
        ok = []
        if own is not None:
            ok.append(float(np.mean(own[1] >= p / px)) if up else float(np.mean(own[0] <= p / px)))
        src = peer_max if up else peer_min
        if src is not None and len(src):
            ok.append(float(np.mean(src >= p / px)) if up else float(np.mean(src <= p / px)))
        return max(ok) if ok else None

    def row(kind, p, **kw):
        return {"kind": kind, "price": p, "from_now": p / px - 1, "value": value(p), "reach": reach(p), **kw}
    state = "above" if px >= pb else "below" if px <= pa else "in"
    coin, usdg = lp_split(px / entry_px, lower, upper)
    out = {"entry_px": entry_px, "lower": lower, "upper": upper, "low_px": pa, "top_px": pb, "state": state,
           "value": value(px), "coin": coin, "usdg": usdg, "break_even": entry_px if upper > 0 else pb,
           "momentum": mom}
    zones = [z for z in (lv or {}).get("res") or [] if z["price"] * (1 - levels.ZONE) < pb * 0.99]
    targets = []
    if state != "above":
        for z in zones:
            lo = z["price"] * (1 - levels.ZONE)
            if lo <= px * 1.02:
                continue
            targets.append(row("resistance", lo, zone=z, hold=z.get("hold"),
                               strong_enough=lo >= px * (1 + OPEN_MIN_TP) and z["strength"] == "strong"))
        targets.append(row("top", pb))
    good = next((t for t in targets if t["kind"] == "resistance" and t["strong_enough"]), None) or \
        next((t for t in targets if t["kind"] == "top"), None)
    after = None
    if good and good["kind"] == "resistance":
        after = next((t for t in targets if t["price"] > good["price"] and (t["kind"] == "top" or t["strong_enough"])), None)
    stop = None
    for z in (lv or {}).get("zones", []):   # urut dari yang terdekat di bawah harga
        p = z["price"] * (1 - levels.ZONE) * BREAK
        if z["strength"] not in ("strong", "medium") or (state == "in" and p <= pa):
            continue
        r_ = reach(p)
        if r_ is None or r_ <= STOP_MAX_REACH:
            stop = row("stop", p, zone=z)
            break
    bottom = row("bottom", pa) if pa < px else None
    out.update(targets=targets, good=good, after=after, stop=stop, bottom=bottom, now=row("now", px))
    return out


def own_windows(tok, t, sleep_h, min_n=100):
    """(terendah, tertinggi) harga relatif tiap jendela jam tidur yang sudah selesai di riwayat koin ini (None kalau
    kurang dari min_n jendela) — untuk menilai seberapa sering sebuah range kena out-of-range."""
    import numpy as np
    dd, ru = forward_dd(tok, sleep_h), forward_rise(tok, sleep_h)
    pairs = [(1 + d, 1 + r) for tt, d, r in zip(tok._pt, dd, ru) if d is not None and r is not None and tt + sleep_h * H <= t]
    if len(pairs) < min_n:
        return None
    a = np.array(pairs)
    return a[:, 0], a[:, 1]


def optimal_range(tok, m, f, usd, rule_level, model_p, sleep_h, range_pct, position_usd, upper_pct=None, stay_target=0.9,
                  own=None, single=False):
    """Range paling optimal dari riset ranges.py untuk situasi sekarang (None kalau datanya tidak ada).
    Dikelompokkan dengan vonis 'balanced' (yang dipakai di riset), apa pun mode tampilannya."""
    from . import ranges
    if not _RANGES:
        _RANGES.append(ranges.Ranges.load())
    rs = _RANGES[0]
    if rs is None:
        return None
    fy = ranges.fee_yield(tok, m, f["t"], usd, position_usd)
    me = next((i for i, x in enumerate(rs.tokens) if x["address"] == tok.address), None)
    up = range_pct if upper_pct is None else upper_pct
    user = (float(range_pct), float(up)) if range_pct > 0 else ranges.FULL
    rec = rs.recommend(combine_level(rule_level, model_p, "balanced"), f.get("vol6"), sleep_h, fy["r"], fy["share"],
                       exclude=me, user=user, own=own, target=stay_target, single=single)
    if rec:
        rec["fee"] = fy
    return rec


def verdict_now(tok, ref, sleep_h=8.0, mode="balanced"):
    """Vonis akhir untuk titik terakhir tanpa analisa lengkap (untuk alarm): (level, rule_level, p, hits, detail, f)."""
    rows = scan(tok, sleep_h, start=tok._pt[-1] - 26 * H) if tok._pt else []
    if not rows:
        return None
    f = rows[-1]
    rule_level, hits, detail = evaluate(f, History(tok, sleep_h).rate(f["t"]), launch_risk(tok).get("flags", []))
    p = None
    if ref and ref.get("dump_model"):
        from .flow import flow_features
        from .study import model_prob, row_x
        from .technicals import tech_features
        t = f["t"]
        view = {**(tech_features(tok, [t]).get(t) or {}), **(flow_features(tok, [t - 6 * H, t]).get(t) or {})}
        p = model_prob(ref["dump_model"], row_x(f, tok._pt, [x[2] for x in tok.prices], view, view))
    return combine_level(rule_level, p, mode), rule_level, p, hits, detail, f


_LIB = []


def current_view(tok, rows, f):
    """Technical view (indikator + data holder) dan situasi mirip di koin sampel untuk titik sekarang."""
    from . import analogs
    from .flow import flow_features
    from .technicals import tech_features
    t = f["t"]
    ff = flow_features(tok, [t - 6 * H, t]).get(t) or {}  # titik 6 jam lalu dibutuhkan untuk perubahan holder
    tf = tech_features(tok, [t]).get(t) or {}
    view = {**tf, **ff}
    if not _LIB:
        _LIB.append(analogs.Library.load())
    lib = _LIB[0]
    analog = None
    if lib is not None:
        by_t = {r["t"]: r for r in rows}
        ts = sorted(by_t)
        v = analogs.moment(tok, by_t, ts, t, tok._pt, [p[2] for p in tok.prices], ff, tf)
        if v is not None:
            me = next((i for i, x in enumerate(lib.tokens) if x["address"] == tok.address), None)
            analog = lib.query(v, exclude=me, groups=ANALOG_GROUPS)
    return view, analog


# sisi yang dipakai untuk menghitung hasil situasi mirip. Uji leave-one-token-out di 24 koin (median AUC):
# semua sisi 0,832, tanpa whale 0,843, tanpa tech 0,834, tanpa pattern 0,831, tanpa price 0,823, tanpa profil
# 0,828 -> whale tidak dipakai untuk hasilnya (aliran whale jarang & berisik untuk pencocokan); semua sisi
# tetap ditampilkan kemiripannya
ANALOG_GROUPS = ["pattern", "price", "tech", "context"]


def support_levels(tok, f, m, ref, max_zones=5):
    """Zona support (price action + likuiditas LP), lubang likuiditas, dan dampak jual whale. Peta
    likuiditas hanya dipakai kalau kondisinya sekarang (bukan mode mundur waktu)."""
    book = m.get("book")
    depth = levels.book_depth(book, tok.ds["decimals"]) if book and book["ts"] >= f["t"] - 1800 else None
    zones = levels.supports(tok, f["t"], f["px"], depth)
    keep = sorted(sorted(zones, key=lambda z: (-z["score"], -z["price"]))[:max_zones], key=lambda z: -z["price"])
    from .holders import cost_basis
    cb = cost_basis(tok, [f["t"]])[f["t"]]
    res = levels.resistance_zones(tok, f["t"], f["px"], depth, cb)
    return {"zones": keep, "depth": depth, "holes": levels.holes(depth),
            "whale": levels.whale_impact(tok, f["t"], depth), "volume": levels.volume_bands(tok, f["t"], f["px"]),
            "test": (ref or {}).get("support_test"), "n_pools": len(depth["pools"]) if depth else 0, "res": res}


def report_text(a) -> str:
    f, q, u = a["now"], a["quote"], a["usdg"] or {}
    fdv = f["px"] * a["supply"]
    if u.get("main"):
        where = f"FDV ${fdv:,.0f} · USDG pool fee {u['main']['fee'] / 1e4:g}%, active liquidity ~${u['depth']:,.0f}"
    else:
        usd = f" (~${fdv * a['usd']:,.0f})" if a["usd"] else ""
        where = f"FDV {fdv:,.1f} {q}{usd} · {q} in pool ~{f['eth_res']:,.1f} · NO active USDG pool"
    L = [f"=== {a['name']} ({a['symbol']}) {a['address']}",
         f"Data up to {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime(f['t']))} · age {f['age_h']:.0f}h · {where}",
         "", f">>> VERDICT: {VERDICTS[a['level']]}", f"    {a['advice']}", ""]
    if a["breakout"]:
        L.append(f" {a['breakout']}")
    for r in a["reasons"]:
        L.append(f" ❌ {r}")
    for r in a["minor_reasons"]:
        L.append(f" ⚠️  {r}")
    cmp = a["compare"]
    if cmp:
        b = cmp["best"]
        L += ["", f"Compared with reference coins right before their crashes — closest: {b['symbol']} {fmt_t(b['peak_t'])} "
                  f"(crash {pct(b['drop'])}), {b['match']} of {b['total']} signs match."]
        head = ["this coin"] + [f"{c['symbol']} {fmt_t(c['peak_t'])[:5]}" for c in cmp["crashes"]]
        L.append(f"  {'':52s}" + "".join(f"{h:>16s}" for h in head))
        for code, label, _, _ in SIGNS:
            cells = [("✅" if code in cmp["now"] else "·")] + [("✅" if code in c["signs"] else "·") for c in cmp["crashes"]]
            L.append(f"  {label[:52]:52s}" + "".join(f"{x:>16s}" for x in cells))
    L += ["", "Filter details:"]
    for x in a["filters"]:
        mark = ("❌" if x["keras"] else "⚠️ ") if x["hit"] else "✅"
        L.append(f" {mark} {x['code']} {x['desc']}: {x['detail']}")
    if a["similar"]:
        L += ["", "Same situation in reference coins:"]
        for s in a["similar"]:
            L.append(f"  {s['code']} {s['desc']}: {s['n']} cases, median low over the next 8h "
                     f"{pct(s['median_dd'])}, drop >=30% in {s['p30'] * 100:.0f}%")
            for e in s["examples"][-5:]:
                L.append(f"     {e['symbol']} {fmt_t(e['t'])}: 8h low {pct(e['dd'])}, 24h low {pct(e['dd24'])}")
    L.append("")
    L += [f" ℹ️  {x}" for x in a["info"]]
    L.append("")
    if a["hist_rate"] is not None:
        L.append(f"This token's history: {a['hist_rate'] * 100:.0f}% of the time followed by a >=30% drop within {a['sleep_h']:g}h.")
    c = a["calib"]
    if c:
        small = " — small sample, treat as rough" if c["n_tokens"] < 5 else ""
        L.append(f"Calibration ({c['n_tokens']} reference coins{small}): when the verdict was {a['verdict']}, a >=30% drop within "
                 f"{c['sleep_h']:g}h happened {c['p30'] * 100:.0f}% of the time (>=50%: {c['p50'] * 100:.0f}%).")
    rng = ("full range" if a["range_pct"] <= 0 else f"range -{a['range_pct']:g}% to 0% (single-sided USDG)"
           if a.get("single_sided") else f"range -{a['range_pct']:g}%/+{a['upper_pct']:g}%")
    ra = a["range_advice"]
    if ra.get("p80_round") is not None:
        L += ["", f"Suggested LP range lower bound ({a['sleep_h']:g}h away):",
              f"  -{ra['p80_round']}%  → 80% chance price stays above it",
              f"  -{ra['p90_round']}%  → 90% chance price stays above it"]
        if ra["breach"] is not None and a["range_pct"] > 0:
            L.append(f"  Your -{a['range_pct']:g}% lower bound: ~{ra['breach'] * 100:.0f}% chance of breaking it")
    br = a.get("best_range")
    if br:
        b = br["best"]
        L += ["", f"Best range from the current price (fee level {br['fee']['r'] * 100:.2f}%/day, {br['n']:,} similar moments): "
                  + ("SKIP — no range came out ahead; least bad " if br["skip"] else "")
                  + f"{b['label']}: average {b['mean'] * 100:+.1f}%, 1-in-10 bad case {b['p10'] * 100:+.1f}%, "
                  f"fees {b['fees'] * 100:+.1f}% over {a['sleep_h']:g}h"]
        sf = br.get("safe") or {}
        pk = sf.get("pick")
        if pk and sf.get("mode") == "single":
            L.append(f"Safe single-sided USDG range: {pk['label']} — hits the bottom {pk['hit_low'] * 100:.1f}% of similar nights, "
                     f"average {pk['mean'] * 100:+.1f}%, 1-in-10 bad case {pk['p10'] * 100:+.1f}%, fees {pk['fees'] * 100:+.1f}%")
    o = a.get("open_pos")
    if o:
        mc = a["supply"] * (a["usd"] or 1)
        L += ["", f"Open position (entry mcap {o['entry_px'] * mc:,.0f}): {o['state']} the range, worth {o['value'] * 100:.1f}% "
                  f"of the deposit (fees not included), {o['coin'] / max(o['value'], 1e-9) * 100:.0f}% coin"]
        for t in sorted(o["targets"] + [x for x in (o["stop"], o["bottom"]) if x], key=lambda t: -t["price"]):
            tag = "GOOD EXIT " if t is o["good"] else ""
            name = t["kind"] if t["kind"] != "resistance" else f"{t['zone']['strength']} resistance"
            L.append(f"  {tag}{name}: mcap {t['price'] * mc:,.0f} ({t['from_now'] * 100:+.0f}%), worth {t['value'] * 100:.1f}%, "
                     f"reached {(t['reach'] or 0) * 100:.0f}% of {a['sleep_h']:g}h windows")
    ex = a.get("exits") or {}
    if ex.get("single") and not o:
        L.append("Exit: automatic at today's price (the LP sells any coin bought on a dip back into USDG); floors inside the "
                 "range where a bounce may stall: " + (", ".join(f"{z['drop'] * 100:+.0f}% ({z['strength']})" for z in ex["stall"])
                                                       or "none"))
    elif ex.get("take"):
        tk = ex["take"]
        L.append(f"Take profit: +{tk['up'] * 100:.0f}% (just under the {tk['zone']['strength']} resistance at "
                 f"+{tk['zone']['up'] * 100:.0f}%); upper bound +{tk['upper_bound']}% for an automatic exit")
    lv = a.get("levels") or {}
    dep, k = lv.get("depth"), a["usd"]
    if lv.get("zones") or dep:
        L += ["", "Support & liquidity:"]
        if dep:
            L.append(f"  selling needed to push price down 10% ≈ ${dep['usd'][10]:,.0f}, 30% ≈ ${dep['usd'][30]:,.0f}, "
                     f"50% ≈ ${dep['usd'][50]:,.0f} ({lv['n_pools']} pools)")
        for z in lv.get("zones", []):
            mc = z["price"] * a["supply"]
            mc = f"${mc * k:,.0f}" if k else f"{mc:,.1f} {q}"
            liq = f", liquidity ${z['liq_usd']:,.0f}" if z["liq_usd"] is not None else ""
            age = f", last bounce {z['age_h']:.0f}h ago (+{z['bounce'] * 100:.0f}%)" if z["age_h"] is not None else ""
            L.append(f"  {z['drop'] * 100:+4.0f}%  mcap {mc}  {z['strength']}{age}{liq}")
        for a0, a1 in lv.get("holes", []):
            L.append(f"  thin liquidity between {a0 * 100:.0f}% and {a1 * 100:.0f}%")
        w = lv.get("whale")
        if w and w["drop1"] is not None:
            L.append(f"  largest wallet sells everything → price {-w['drop1'] * 100:.0f}%")
    L += ["", f"LP value simulation ({rng}) if price drops while you sleep, in {q}:"]
    for d, v, full in a["sims"]:
        L.append(f"  price {d * 100:+.0f}% → LP {(v - 1) * 100:+.0f}% (full range {(full - 1) * 100:+.0f}%, holding {q} 0%)")
    L += ["", f"Alert while the position is open: python -m dump_alert.watch --token {a['address']}"]
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description="Cek kelayakan buka LP token Robinhood Chain sebelum tidur")
    ap.add_argument("token", nargs="?", help="contract address (kosong = ditanya)")
    ap.add_argument("--range", type=float, default=20, help="lebar range LP ± persen; 0 = full range (default 20)")
    ap.add_argument("--sleep", type=float, default=8, help="lama posisi ditinggal, jam (default 8)")
    ap.add_argument("--single", action="store_true", help="single-sided USDG: range dari harga sekarang (0%%) turun ke -RANGE%%")
    ap.add_argument("--entry-mcap", help="posisi sudah dibuka: market cap saat buka (mis. 8.38M) -> rencana exit")
    ap.add_argument("--no-fetch", action="store_true", help="pakai data lokal tanpa update")
    ap.add_argument("--at", help="nilai seolah sekarang adalah waktu ini, UTC 'YYYY-MM-DD HH:MM' (pakai data lokal)")
    ap.add_argument("--backtest", action="store_true", help="uji vonis di semua token di dump_alert/data/")
    ap.add_argument("--build-reference", nargs="*", metavar="DATA_JSON",
                    help="tulis ulang reference.json (default: token di reference_tokens.txt)")
    ap.add_argument("--support-test", action="store_true",
                    help="uji zona support di token pembanding dan simpan hasilnya ke reference.json")
    ap.add_argument("--resistance-test", action="store_true",
                    help="uji zona resistance & sinyal exit di token pembanding dan simpan hasilnya ke reference.json")
    ap.add_argument("--rpc-url")
    args = ap.parse_args()
    if args.support_test:
        ref = load_reference() or {}
        ref["support_test"] = levels.support_test([Token(json.load(open(p))) for p in reference_paths()])
        with open(REF_PATH, "w") as fh:
            json.dump(ref, fh, separators=(",", ":"), default=_json_default)
        for k, v in ref["support_test"].items():
            print(f"{k:18s} dites {v['n']:5d}  bertahan {v['rate'] * 100 if v['rate'] is not None else 0:5.1f}%")
        return
    if args.resistance_test:
        ref = load_reference() or {}
        ref["resistance_test"] = levels.summarize_resistance_test(
            levels.resistance_test([Token(json.load(open(p))) for p in reference_paths()]))
        with open(REF_PATH, "w") as fh:
            json.dump(ref, fh, separators=(",", ":"), default=_json_default)
        for k, v in ref["resistance_test"]["zones"].items():
            print(f"{k:8s} " + "  ".join(f"{m} {x['rate'] * 100 if x['rate'] is not None else 0:5.1f}% (n={x['n']})"
                                         for m, x in v.items()))
        return
    if args.build_reference is not None:
        ref = build_reference(args.build_reference or reference_paths(), 8.0)
        with open(REF_PATH, "w") as fh:
            json.dump(ref, fh, separators=(",", ":"), default=_json_default)
        print(f"{REF_PATH}: {len(ref['tokens'])} token, {len(ref['episodes'])} kejadian, "
              f"{len(ref.get('crashes', []))} potret sebelum crash")
        return
    if args.backtest:
        stats = backtest(reference_paths(), args.sleep)
        name = lambda lv: VERDICTS[lv] if lv is not None else "-"
        for sym, g in stats.items():
            print(f"== {sym}")
            print("\n".join(summarize(g)))
            for d, lv in g["dumps"]:
                print(f"  dump puncak {fmt_t(d['peak_t'])} ({(d['trough'] / d['peak'] - 1) * 100:.0f}%): "
                      f"vonis T-1j {name(lv[0])}, T-30m {name(lv[1])}, puncak {name(lv[2])}")
            nb, ne, at = g["exits"]
            if nb:
                print(f"  masuk saat LAYAK lalu drop >=30%: {nb} titik; alert JANGAN muncul sebelum -30% di {100 * ne / nb:.0f}%"
                      + (f", median harga saat alert {sorted(at)[len(at) // 2] * 100:+.0f}% dari harga masuk" if at else ""))
            by = collections.defaultdict(list)
            for e in g["episodes"]:
                by[e["code"]].append(e["dd"])
            for c in EPISODE_CODES:
                d = sorted(by.get(c, []))
                if d:
                    print(f"  {c} {len(d)} kejadian: median {args.sleep:g}j {d[len(d) // 2] * 100:+.0f}%, "
                          f"drop >=30% {sum(x <= -0.3 for x in d) / len(d) * 100:.0f}%")
        print("== gabungan")
        print("\n".join(summarize(pooled(stats))))
        return
    address = (args.token or input("Contract address: ")).strip().lower()
    if not (address.startswith("0x") and len(address) == 42):
        raise SystemExit("contract address tidak valid")
    path = data_path(address)
    rpc = make_rpc(args.rpc_url)
    if args.at:
        args.no_fetch = True
    if not args.no_fetch:
        ds = Fetcher(rpc, address, path).run(STEP)
    elif os.path.exists(path):
        ds = json.load(open(path))
    else:
        raise SystemExit("belum ada data lokal; jalankan tanpa --no-fetch")
    if args.at:
        ds = truncate(ds, parse_time(args.at))
    a = assess(Token(ds), args.range, args.sleep, load_reference(), None if args.no_fetch else eth_usd(),
               single_sided=args.single, entry_mcap=parse_mcap(args.entry_mcap))
    print(report_text(a) if a else "belum ada data harga/transaksi beli — tidak bisa dinilai.")


if __name__ == "__main__":
    main()
