"""
Sinyal pra-dump dari dataset dump_alert.fetch.

Semua sinyal di titik waktu t hanya memakai data sebelum t (tanpa mengintip masa depan),
jadi aman dipakai untuk backtest (analyze.py, lp_check.py) maupun penilaian live.
"""
import bisect
import collections
import heapq

ZERO = "0x" + "0" * 40
H = 3600

# Ambang default, diturunkan dari kasus PULP (Okt 2026). Kalibrasi ulang dengan lebih banyak token.
DEFAULTS = {
    "step": 300,         # resolusi evaluasi (detik)
    "whale_pct": 0.3,    # whale = wallet yang pernah memegang >= 0.3% supply
    "whale_net": 0.75,   # A: whale net jual >= 0.75% supply dalam 60 menit
    "div_ret": 0.10,     # B: harga naik >= 10% dalam 60 menit ...
    "div_net": 0.5,      #    ... sementara whale net jual >= 0.5% supply
    "fomo_surge": 2.0,   # C: jumlah transaksi beli 30 menit >= 2x rata-rata 6 jam ...
    "fomo_net": 0.5,     #    ... sementara whale net jual >= 0.5% supply
    "top_drop": 3.0,     # D: porsi top-50 wallet turun >= 3 poin dalam 6 jam sementara holder bertambah
}
RULES = {
    "A": "whale distribusi",
    "B": "divergensi: harga naik, whale jual",
    "C": "FOMO exit: pembeli melonjak, whale jual",
    "D": "retail menyerap: top-50 turun, holder naik",
}
LEVELS = ["AMAN", "WASPADA", "BAHAYA"]


INFLOW = ("buy", "xin", "recv", "lpout")  # jenis event yang menambah saldo wallet


def net_events(transfers, markets, contracts, unit, txids=None, lp_txs=()):
    """Netting transfer per transaksi -> [(t, jenis, wallet, jumlah token)].

    buy/sell  : saldo market (PoolManager / pool / bonding curve) berubah di transaksi itu.
                Router/aggregator otomatis ter-netting nol, jadi jual lewat router tetap terhitung.
    lp        : token masuk market sebelum ada pembelian pertama (setoran likuiditas awal), atau di
                transaksi likuiditas (`lp_txs`, hash yang berisi ModifyLiquidity tanpa Swap).
    lpout     : token keluar market ke wallet di transaksi likuiditas (tarik LP), bukan beli.
    recv      : wallet menerima token dari kontrak non-market (claim, airdrop, vesting).
    xin/xout  : transfer antar wallet.
    Kalau `txids` diberikan, nomor urut transaksi tiap event ditambahkan ke list itu.
    """
    out, bought = [], [False]

    def flush(delta, t, n, h):
        mk = sum(delta[m] for m in markets)
        sources = any(v < 0 for a, v in delta.items() if a not in contracts and a not in markets and a != ZERO)
        liq = h in lp_txs
        for a, v in delta.items():
            if not v or a in markets or a in contracts or a == ZERO:
                continue
            if liq and mk:
                k = "lp" if v < 0 else "lpout"
            elif mk > 0 and v < 0:
                k = "sell" if bought[0] else "lp"
            elif mk < 0 and v > 0:
                k = "buy"
            elif v > 0:
                k = "xin" if sources or delta[ZERO] < 0 else "recv"
            else:
                k = "xout"
            out.append((t, k, a, abs(v) / unit))
            if txids is not None:
                txids.append(n)
        if mk < 0 and not liq:
            bought[0] = True

    cur, t_cur, n, delta = None, 0, 0, collections.Counter()
    for _, t, h, f, to, raw in transfers:
        if h != cur:
            if cur is not None:
                flush(delta, t_cur, n, cur)
            cur, t_cur, n, delta = h, t, n + 1, collections.Counter()
        delta[f] -= raw
        delta[to] += raw
    if cur is not None:
        flush(delta, t_cur, n, cur)
    return out


class Token:
    def __init__(self, ds: dict):
        self.ds = ds
        self.address = ds["token"]
        self.symbol = ds.get("symbol", "?")
        self.supply = ds["supply"]
        self.markets = set(ds.get("markets", []))
        self.contracts = {a for a, c in ds.get("contracts", {}).items() if c}
        self.prices = ds.get("prices", [])
        self.quote = (ds.get("market") or {}).get("quote_symbol", "")
        self._pt = [p[0] for p in self.prices]
        self.event_tx = []
        self.events = net_events(ds["transfers"], self.markets, self.contracts, 10 ** ds["decimals"], self.event_tx,
                                 set(ds.get("lp_txs", ())))

    def px(self, t):
        """Harga terakhir yang diketahui pada/sebelum t."""
        i = bisect.bisect_right(self._pt, t) - 1
        return self.prices[i][2] if i >= 0 else None

    def fdv(self, t):
        p = self.px(t)
        return p * self.supply if p else None


def timeline(tok: Token, cfg: dict | None = None, start: int | None = None, end: int | None = None):
    """Nilai sinyal dan rule yang menyala tiap cfg['step'] detik."""
    c = {**DEFAULTS, **(cfg or {})}
    E, S, step = tok.events, tok.supply, c["step"]
    if not E:
        return []
    t_first = E[0][0]
    # pembanding 6 jam baru dipakai sesudah fase launch (1 jam setelah beli pertama), supaya
    # pembagian supply dari deployer/claim di awal tidak terbaca sebagai distribusi whale
    t_settled = next((e[0] for e in E if e[1] == "buy"), t_first) + H
    end = end or max(E[-1][0], tok._pt[-1] if tok._pt else 0)
    start = max(start or 0, (t_first // step + 1) * step)
    start -= start % step
    whale_thr, dust = S * c["whale_pct"] / 100, S * 1e-7
    bal, peak = collections.Counter(), collections.Counter()
    holders, i = 0, 0
    wflow, buys30, buys6h, hist = (collections.deque() for _ in range(4))
    rows = []
    # mulai 6 jam lebih awal supaya pembanding 6 jam sudah terisi di baris pertama
    for t in range(max(start - 6 * H, (t_first // step + 1) * step), end + 1, step):
        while i < len(E) and E[i][0] < t:
            et, k, a, v = E[i]
            i += 1
            before = bal[a]
            bal[a] += v if k in INFLOW else -v
            if (before > dust) != (bal[a] > dust):
                holders += 1 if bal[a] > dust else -1
            if bal[a] > peak[a]:
                peak[a] = bal[a]
            if k in ("buy", "sell") and peak[a] >= whale_thr:
                wflow.append((et, v if k == "sell" else -v, a))
            if k == "buy":
                buys30.append(et)
                buys6h.append(et)
        for dq, w in ((wflow, H), (buys30, 1800), (buys6h, 6 * H)):
            while dq and (dq[0][0] if isinstance(dq[0], tuple) else dq[0]) < t - w:
                dq.popleft()
        top50 = sum(heapq.nlargest(50, (v for v in bal.values() if v > 0))) / S * 100
        hist.append((t, top50, holders))
        while hist and hist[0][0] < t - 6 * H:
            hist.popleft()
        if t < start:
            continue
        whale_net = sum(w[1] for w in wflow) / S * 100
        sellers = len({w[2] for w in wflow if w[1] > 0})
        elapsed = min(6 * H, t - t_first)
        avg30 = len(buys6h) / (elapsed / 1800) if elapsed > 0 else 0
        surge = len(buys30) / avg30 if avg30 else 0.0
        h0 = hist[0]
        full6 = t - h0[0] >= 6 * H - step and h0[0] >= t_settled
        d_top = top50 - h0[1] if full6 else None
        d_hold = (holders / h0[2] - 1) * 100 if full6 and h0[2] else None
        p, p1 = tok.px(t), tok.px(t - H)
        r1h = p / p1 - 1 if p and p1 else None
        rules = []
        if whale_net >= c["whale_net"]:
            rules.append("A")
        if r1h is not None and r1h >= c["div_ret"] and whale_net >= c["div_net"]:
            rules.append("B")
        if surge >= c["fomo_surge"] and whale_net >= c["fomo_net"]:
            rules.append("C")
        if d_top is not None and d_top <= -c["top_drop"] and d_hold > 0:
            rules.append("D")
        rows.append({"t": t, "px": p, "whale_net": whale_net, "whale_sellers": sellers, "r1h": r1h,
                     "surge": surge, "top50": top50, "holders": holders, "d_top6h": d_top,
                     "d_hold6h": d_hold, "rules": rules, "level": min(2, len(rules))})
    return rows


def launch_risk(tok: Token) -> dict:
    """Faktor struktural di jam-jam awal: sniping, supply murah (claim/airdrop), konsentrasi."""
    E, S, T = tok.events, tok.supply, tok.ds["transfers"]
    t_buy = next((e[0] for e in E if e[1] == "buy"), None)
    if t_buy is None:
        return {}
    unit = 10 ** tok.ds["decimals"]
    seed = sum(x[5] for x in T if x[4] in tok.markets and x[3] not in tok.markets and x[1] <= t_buy) / unit
    buy5 = sum(v for t, k, a, v in E if k == "buy" and t <= t_buy + 5)
    buyers60 = collections.Counter()
    for t, k, a, v in E:
        if t > t_buy + 60:
            break
        if k == "buy":
            buyers60[a] += v
    recv24 = collections.Counter()
    for t, k, a, v in E:
        if t > t_buy + 24 * H:
            break
        if k == "recv":
            recv24[a] += v
    sold6 = sum(v for t, k, a, v in E if t <= t_buy + 6 * H and a in recv24 and k == "sell")
    moved6 = sum(v for t, k, a, v in E if t <= t_buy + 6 * H and a in recv24 and k == "xout")
    bal = collections.Counter()
    for t, k, a, v in E:
        if t > t_buy + H:
            break
        bal[a] += v if k in INFLOW else -v
    r = {
        "first_buy": t_buy,
        "seed_tokens": seed,
        "snipe_5s_pct_pool": 100 * buy5 / seed if seed else None,
        "snipe_60s_pct_pool": 100 * sum(buyers60.values()) / seed if seed else None,
        "snipers_60s": len(buyers60),
        "top_sniper_pct_supply": 100 * max(buyers60.values(), default=0) / S,
        "recv_24h_pct_supply": 100 * sum(recv24.values()) / S,
        "recv_wallets": len(recv24),
        "recv_sold_6h_pct": 100 * sold6 / sum(recv24.values()) if recv24 else 0.0,
        "recv_moved_6h_pct": 100 * moved6 / sum(recv24.values()) if recv24 else 0.0,
        "top10_1h_pct": 100 * sum(heapq.nlargest(10, (v for v in bal.values() if v > 0))) / S,
    }
    flags = []
    if (r["snipe_60s_pct_pool"] or 0) >= 30:
        flags.append(f"snipers took {r['snipe_60s_pct_pool']:.0f}% of the pool's tokens in the first 60 seconds")
    if r["recv_24h_pct_supply"] >= 30:
        flags.append(f"{r['recv_24h_pct_supply']:.0f}% of supply was handed out outside the pool (claims/airdrops) within 24h")
    if r["recv_sold_6h_pct"] >= 25:
        flags.append(f"claim/airdrop receivers sold {r['recv_sold_6h_pct']:.0f}% of their allocation within 6h")
    if r["top10_1h_pct"] >= 30:
        flags.append(f"top-10 wallets held {r['top10_1h_pct']:.0f}% of supply 1 hour after launch")
    r["flags"] = flags
    return r


def find_dumps(prices, drop=0.5, window=12 * H):
    """Episode harga jatuh >= drop dari puncak dalam <= window detik."""
    ts = [p[0] for p in prices]
    res, j, j0 = [], 0, 0
    while j < len(prices):
        t, _, p = prices[j]
        k = max(j0, bisect.bisect_left(ts, t - window))
        top = max(prices[m][2] for m in range(k, j + 1))
        if p <= top * (1 - drop):
            # puncak = titik terakhir sebelum jatuh yang masih >= 95% harga tertinggi jendela
            pk = max(m for m in range(k, j + 1) if prices[m][2] >= 0.95 * top)
            e = bisect.bisect_right(ts, t + window)
            tr = min(range(j, e), key=lambda m: prices[m][2])
            res.append({"peak_t": prices[pk][0], "peak": prices[pk][2], "cross_t": t,
                        "trough_t": prices[tr][0], "trough": prices[tr][2]})
            j0, j = tr, tr + 1
            continue
        j += 1
    return res


def backtest(tok: Token, rows, horizon=6 * H, dd=0.30):
    """Seberapa sering tiap rule menyala dan peluang drawdown >= dd dalam `horizon` sesudahnya."""
    P = tok.prices
    if not P:
        return None
    ts = [p[0] for p in P]
    ev = []
    for r in rows:
        if not r["px"] or ts[-1] < r["t"] + horizon:
            continue
        a, b = bisect.bisect_right(ts, r["t"]), bisect.bisect_right(ts, r["t"] + horizon)
        if a == b:
            continue
        ev.append((r, min(P[m][2] for m in range(a, b)) / r["px"] - 1))
    if not ev:
        return None
    base = sum(d <= -dd for _, d in ev) / len(ev)
    out = {"n": len(ev), "baseline": base, "rules": {}}
    groups = {k: (lambda r, k=k: k in r["rules"]) for k in RULES}
    groups["WASPADA+"] = lambda r: r["level"] >= 1
    groups["BAHAYA"] = lambda r: r["level"] >= 2
    for name, cond in groups.items():
        hit = [d for r, d in ev if cond(r)]
        if hit:
            p = sum(d <= -dd for d in hit) / len(hit)
            out["rules"][name] = {"fired_pct": 100 * len(hit) / len(ev), "p_dd": p,
                                  "lift": p / base if base else None, "median_dd": sorted(hit)[len(hit) // 2]}
        else:
            out["rules"][name] = {"fired_pct": 0.0, "p_dd": None, "lift": None, "median_dd": None}
    return out
