"""
Riset & saran range LP paling optimal dari harga sekarang.

Simulasi di riwayat 24 koin sampel: di tiap momen (tiap jam) dibuka posisi LP Uniswap dengan berbagai
range (batas bawah -5..-90%, batas atas +2..+200%, atau full range), lalu ditinggal selama jam tidur.
Hasil tiap range = nilai posisi di akhir (rumus Uniswap v3/v4: hanya tergantung harga akhir) + fee.

Fee dihitung dari data nyata: hanya saat harga di dalam range, sebanding dengan volume koin itu per 15
menit (dibanding rata-rata 24 jam sebelumnya, dibatasi 20x supaya lonjakan di koin mati tidak meledak),
dikali konsentrasi likuiditas range itu (berapa kali lebih banyak likuiditas per dolar dibanding full
range) dan tingkat fee pool. Tingkat fee pool (fee pool x volume 24 jam / likuiditas aktif) tidak tersimpan
untuk masa lalu, jadi diukur live untuk koin yang sedang dicek; ukuran posisimu ikut dihitung karena
likuiditasmu sendiri ikut membagi fee (range sempit di pool tipis tidak bisa menghasilkan fee tak hingga).

Range terbaik = yang paling menumbuhkan modal kalau diulang tiap malam di situasi serupa: rata-rata
log(1 + hasil), adil antar koin (rata-rata per koin dulu), per kelompok vonis (OK / CAUTION / AVOID) x
volatilitas 6 jam. Kriteria ini menghukum kerugian besar lebih berat daripada rata-rata biasa. Kalau
range terbaik pun negatif, tidak membuka LP lebih baik.

  python -m dump_alert.ranges --build    # tulis dump_alert/ranges.npz dari koin sampel (±7 menit)
  python -m dump_alert.ranges --eval     # uji leave-one-coin-out
"""
import argparse
import bisect
import json
import math
import os
import sys

import numpy as np

H = 3600
DATA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ranges.npz")
LO = (5, 10, 15, 20, 25, 30, 40, 50, 60, 70, 80, 90)            # batas bawah, persen turun
HI = (2, 5, 10, 15, 20, 30, 50, 75, 100, 150, 200)              # batas atas, persen naik
FULL = (100, 0)                                                 # penanda full range
BLOCK = 900          # resolusi jalur harga: 15 menit
STEPS = 48           # 12 jam ke depan
WCAP = 20.0          # bobot volume per 5 menit maksimal 20x rata-rata 24 jam sebelumnya
MIN_TURNOVER = 0.002  # momen dipakai kalau volume 24 jam sebelumnya >= 0,2% market cap (koin masih hidup)
MIN_COINS = 3
TWO_SIDED_UP = 10    # "two-sided": batas atas minimal +10% (posisi tidak hampir semuanya USDG)


def grid():
    return [(l, u) for l in LO for u in HI] + [FULL]


def is_full(rng):
    return rng[0] >= 100


def concentration(rng):
    """Likuiditas per dolar dibanding full range (full range = 1)."""
    if is_full(rng):
        return 1.0
    sa, sb = math.sqrt(1 - rng[0] / 100), math.sqrt(1 + rng[1] / 100)
    return 2 / (2 - sa - 1 / sb)


def lp_values(P, rng):
    """Nilai posisi (relatif ke modal awal, dalam USDG) untuk rasio harga akhir P."""
    if is_full(rng):
        return np.sqrt(P)
    sa, sb = math.sqrt(1 - rng[0] / 100), math.sqrt(1 + rng[1] / 100)
    sp = np.sqrt(P)
    v = np.where(sp <= sa, P * (1 / sa - 1 / sb), np.where(sp >= sb, sb - sa, 2 * sp - sa - P / sb))
    return v / (2 - sa - 1 / sb)


def usdg_share(rng):
    """Porsi USDG dari modal saat posisi dibuka (sisanya koin)."""
    if is_full(rng):
        return 0.5
    sa, sb = math.sqrt(1 - rng[0] / 100), math.sqrt(1 + rng[1] / 100)
    return (1 - sa) / (2 - sa - 1 / sb)


def label(rng):
    return "Full range" if is_full(rng) else f"−{rng[0]:g}% / +{rng[1]:g}%"


# ---------------------------------------------------------------- data koin sampel

def token_paths(tok, rows):
    """Jalur harga 15 menit (rasio ke harga sekarang) dan bobot volume untuk tiap baris (12 jam ke depan).
    Baris tanpa 12 jam data ke depan atau dengan volume terlalu kecil dilewati."""
    pt, pv = tok._pt, np.array([x[2] for x in tok.prices])
    if len(pt) < 2:
        return []
    dt = 300
    base = pt[0] - pt[0] % dt
    nb = (pt[-1] - base) // dt + 2
    vol = np.zeros(nb)
    for t, k, _, v in tok.events:
        if k in ("buy", "sell") and base <= t <= pt[-1]:
            j = bisect.bisect_right(pt, t) - 1
            if j >= 0:
                vol[(t - base) // dt] += v * pv[j]
    cum = np.concatenate([[0.0], np.cumsum(vol)])
    out = []
    per_block = BLOCK // dt
    for r in rows:
        t = r["t"]
        if t + STEPS * BLOCK > pt[-1]:
            continue
        p0 = pv[bisect.bisect_right(pt, t) - 1]
        b = (t - base) // dt
        trail = (cum[b + 1] - cum[max(0, b + 1 - 288)]) / 288        # rata-rata volume per 5 menit, 24 jam
        if trail <= 0 or trail * 288 < MIN_TURNOVER * p0 * tok.supply or b + 1 + STEPS * per_block > nb:
            continue
        marks = t + BLOCK * np.arange(1, STEPS + 1)
        path = pv[np.searchsorted(pt, marks, side="right") - 1] / p0
        w = np.minimum(vol[b + 1:b + 1 + STEPS * per_block] / trail, WCAP).reshape(STEPS, per_block).mean(1)
        out.append((t, path, w))
    return out


def build(paths):
    """Momen tiap jam dari koin sampel: vonis (model leave-one-token-out + pengaman), volatilitas, jalur."""
    from . import study
    from .lp_check import combine_level
    from .signals import Token
    data, toks = {}, {}
    for p in paths:
        tok = Token(json.load(open(p)))
        _, rs = study.rows_for(None, tok=tok)
        if len(rs) >= 20:
            for r in rs:
                r["tok"] = tok.symbol
            data[tok.symbol], toks[tok.symbol] = rs, tok
            print(f"{tok.symbol}: {len(rs)} titik", file=sys.stderr)
    preds = study.loto(data, study.MODEL_FEATURES)
    tokens, cols = [], {k: [] for k in ("tok", "t", "level", "vol6", "P", "W")}
    for sym, rs in data.items():
        tok = toks[sym]
        by_t = {r["t"]: (r, p) for r, p in zip(rs, preds[sym])}
        hourly = rs[::2]
        n = 0
        for t, path, w in token_paths(tok, hourly):
            r, p = by_t[t]
            cols["tok"].append(len(tokens))
            cols["t"].append(t)
            cols["level"].append(combine_level(r["level"], float(p), "balanced"))
            cols["vol6"].append(r["x"].get("vol6") if r["x"].get("vol6") is not None else np.nan)
            cols["P"].append(path)
            cols["W"].append(w)
            n += 1
        tokens.append({"symbol": sym, "address": tok.address, "n": n})
        print(f"{sym}: {n} momen", file=sys.stderr)
    vol6 = np.array(cols["vol6"], float)
    return {"tokens": json.dumps(tokens), "tok": np.array(cols["tok"], np.int16), "t": np.array(cols["t"], np.int64),
            "level": np.array(cols["level"], np.int8), "vol6": vol6.astype(np.float32),
            "vol_q": np.nanpercentile(vol6, [100 / 3, 200 / 3]),
            "P": np.array(cols["P"], np.float16), "W": np.array(cols["W"], np.float16)}


# ---------------------------------------------------------------- saran range

class Ranges:
    def __init__(self, d):
        self.tokens = json.loads(str(d["tokens"]))
        self.tok, self.t, self.level = d["tok"].astype(int), d["t"], d["level"].astype(int)
        self.vol6, self.vol_q = d["vol6"].astype(float), d["vol_q"]
        self.P, self.W = d["P"].astype(float), d["W"].astype(float)
        self.test = json.loads(str(d["test"])) if "test" in d else None
        self.vb = np.where(np.isnan(self.vol6), 1, np.digitize(np.nan_to_num(self.vol6), self.vol_q))

    @classmethod
    def load(cls, path=DATA_PATH):
        if not os.path.exists(path):
            return None
        with np.load(path, allow_pickle=False) as d:
            return cls({k: d[k] for k in d.files})

    def vol_bucket(self, vol6):
        return 1 if vol6 is None or (isinstance(vol6, float) and math.isnan(vol6)) else int(np.digitize(vol6, self.vol_q))

    def moments(self, level, vb, exclude=None):
        """Momen serupa: vonis sama & kelompok volatilitas sama (cadangan: vonis sama saja) -> (mask, kelompok)."""
        base = self.level == level
        if exclude is not None:
            base &= self.tok != exclude
        m = base & (self.vb == vb)
        if len({int(x) for x in self.tok[m]}) >= MIN_COINS and m.sum() >= 50:
            return m, vb
        return base, None

    def outcomes(self, mask, rng, steps, r, share):
        """(hasil total, bagian harga, fee, masih di dalam range di akhir) per momen."""
        P, W = self.P[mask, :steps], self.W[mask, :steps]
        end = P[:, -1]
        price = lp_values(end, rng) - 1
        k = concentration(rng)
        lo, hi = (0.0, np.inf) if is_full(rng) else (1 - rng[0] / 100, 1 + rng[1] / 100)
        inr = (P >= lo) & (P <= hi)
        fees = r * (W * inr).sum(1) / (86400 / BLOCK) * k / (1 + k * share)
        return price + fees, price, fees, (end >= lo) & (end <= hi)

    def stats(self, mask, rng, steps, r, share):
        net, price, fees, inside = self.outcomes(mask, rng, steps, r, share)
        coins = self.tok[mask]
        g = np.log(np.maximum(1 + net, 1e-3))
        per = [(g[coins == c].mean(), net[coins == c].mean()) for c in np.unique(coins) if (coins == c).sum() >= 5]
        if len(per) < MIN_COINS:
            per = [(g.mean(), net.mean())]
        return {"range": rng, "label": label(rng), "growth": float(np.mean([x[0] for x in per])),
                "mean": float(np.mean([x[1] for x in per])), "median": float(np.median(net)),
                "p10": float(np.percentile(net, 10)), "big_loss": float(np.mean(net < -0.2)),
                "fees": float(fees.mean()), "price": float(price.mean()), "inside": float(inside.mean()),
                "usdg": usdg_share(rng), "k": concentration(rng)}

    def recommend(self, level, vol6, sleep_h, r, share, exclude=None, user=None):
        """Range terbaik untuk situasi sekarang. r = tingkat fee pool per hari (setara full range),
        share = modal posisimu / likuiditas aktif pool (setara full range). user = range rencanamu."""
        steps = int(min(STEPS, max(1, round(sleep_h * H / BLOCK))))
        mask, vb = self.moments(level, self.vol_bucket(vol6), exclude)
        if mask.sum() < 30:
            return None
        cands = [self.stats(mask, g, steps, r, share) for g in grid()]
        best = max(cands, key=lambda c: c["growth"])
        two = max((c for c in cands if not is_full(c["range"]) and c["range"][1] >= TWO_SIDED_UP), key=lambda c: c["growth"])
        full = cands[-1]
        mine = None
        if user is not None:
            mine = next((c for c in cands if c["range"] == user), None) or self.stats(mask, user, steps, r, share)
        heat = [[next(c["growth"] for c in cands if c["range"] == (l, u)) for u in HI] for l in LO]
        return {"best": best, "two_sided": two, "full": full, "mine": mine, "skip": best["growth"] < 0,
                "heat": heat, "lo": list(LO), "hi": list(HI), "n": int(mask.sum()),
                "coins": len({int(x) for x in self.tok[mask]}), "vol_bucket": vb, "level": level,
                "steps": steps, "r": r, "share": share, "test": self.test}


# ---------------------------------------------------------------- tingkat fee live

DYNAMIC_FEE = 0x800000
ASSUMED_R = 0.01


def pool_tvls(book):
    """{pool_id: likuiditas aktif setara full range dalam USD} dari peta likuiditas."""
    out = {}
    for p in (book or {}).get("pools", []):
        if not p.get("usd_per_quote") or not p.get("sqrt"):
            continue
        sq = p["sqrt"] / 2 ** 96
        qv = p["liquidity"] / sq if p["token_is_c1"] else p["liquidity"] * sq   # cadangan virtual sisi quote
        out[p["pool_id"]] = 2 * qv / 10 ** p["quote_decimals"] * p["usd_per_quote"]
    return out


def fee_yield(tok, m, t, usd, position_usd):
    """Tingkat fee per hari untuk LP full range di pool utama: fee pool x volume 24 jam / likuiditas aktif semua
    pool (volume dianggap terbagi sebanding likuiditas), plus porsi posisimu terhadap likuiditas pool itu."""
    book = m.get("book")
    tv = pool_tvls(book)
    if not tv:
        tv = {p["pool_id"]: 2 * p.get("usdg_side", 0.0) for p in (m.get("usdg_pools") or [])}
    if usd is None and book and tok.quote in ("ETH", "WETH"):
        usd = book.get("eth_usd")
    pt, pv = tok._pt, [x[2] for x in tok.prices]
    vol = 0.0
    for et, k, _, v in tok.events:
        if k in ("buy", "sell") and t - 24 * H < et <= t:
            j = bisect.bisect_right(pt, et) - 1
            if j >= 0:
                vol += v * pv[j]
    vol *= usd or 0.0
    total, pool = sum(tv.values()), tv.get(m.get("pool_id"))
    fee, notes, assumed = m.get("fee"), [], False
    rate = None if fee is None or fee & DYNAMIC_FEE else fee / 1e6
    if rate == 0:
        notes.append("this pool pays LPs no swap fee (the hook takes it)")
    if not vol:
        r = 0.0
        notes.append("no trades in the last 24h, so no fees")
    elif not total:
        r, assumed = ASSUMED_R, True
        notes.append("pool liquidity is unknown, so a 1%/day fee yield is assumed")
    elif rate is None:
        r, assumed = 0.01 * vol / total, True
        notes.append("the pool fee is dynamic (set by a hook), so a 1% fee is assumed")
    else:
        r = rate * vol / total
    pool = pool or total
    share = min(10.0, position_usd / pool) if pool else 0.0
    return {"r": r, "vol24": vol, "tvl": total, "tvl_pool": pool, "fee": fee, "rate": rate, "share": share,
            "position": position_usd, "assumed": assumed, "notes": notes}


# ---------------------------------------------------------------- uji

def evaluate(rs, r_list=(0.01, 0.02, 0.04), shares=(0.005, 0.02, 0.1), sleep_h=8.0):
    """Leave-one-coin-out: range untuk tiap momen koin X dipilih hanya dari koin lain (vonis x volatilitas),
    lalu hasil nyatanya di koin X dibandingkan dengan full range dan range tetap."""
    steps = int(round(sleep_h * H / BLOCK))
    fixed = {"full": FULL, "-20/+20": (20, 20), "-40/+50": (40, 50), "-50/+100": (50, 100)}
    out = []
    G = lambda x: np.log(np.maximum(1 + x, 1e-3))
    for r in r_list:
        for share in shares:
            res = {k: [] for k in ["optimal"] + list(fixed)}
            pooled = {k: [] for k in res}
            for c in range(len(rs.tokens)):
                me = rs.tok == c
                if me.sum() < 20:
                    continue
                got = np.zeros(me.sum())
                idx_me = np.nonzero(me)[0]
                for lv in np.unique(rs.level[me]):
                    for vb in np.unique(rs.vb[me & (rs.level == lv)]):
                        sel = (rs.level[idx_me] == lv) & (rs.vb[idx_me] == vb)
                        mask, _ = rs.moments(lv, vb, exclude=c)
                        if mask.sum() < 30:
                            continue
                        best = max((rs.stats(mask, g, steps, r, share) for g in grid()), key=lambda s: s["growth"])
                        m_sel = np.zeros(len(rs.tok), bool)
                        m_sel[idx_me[sel]] = True
                        net = rs.outcomes(m_sel, best["range"], steps, r, share)[0]
                        got[sel] = 0.0 if best["growth"] < 0 else net
                res["optimal"].append((G(got).mean(), got.mean()))
                pooled["optimal"].append(got)
                for k, rng in fixed.items():
                    net = rs.outcomes(me, rng, steps, r, share)[0]
                    res[k].append((G(net).mean(), net.mean()))
                    pooled[k].append(net)
            row = {"r": r, "share": share, "coins": len(res["optimal"])}
            for k in res:
                p = np.concatenate(pooled[k])
                row[k] = {"growth": float(np.mean([x[0] for x in res[k]])), "mean": float(np.mean([x[1] for x in res[k]])),
                          "p10": float(np.percentile(p, 10)), "big_loss": float(np.mean(p < -0.2))}
            row["beats_full"] = int(sum(a[0] > b[0] for a, b in zip(res["optimal"], res["full"])))
            out.append(row)
            print(f"r={r * 100:g}%/day share={share * 100:g}%: optimal growth {row['optimal']['growth'] * 100:+.2f}% "
                  f"big loss {row['optimal']['big_loss'] * 100:.1f}% | full {row['full']['growth'] * 100:+.2f}% "
                  f"{row['full']['big_loss'] * 100:.1f}% | -40/+50 {row['-40/+50']['growth'] * 100:+.2f}% "
                  f"{row['-40/+50']['big_loss'] * 100:.1f}% | beats full {row['beats_full']}/{row['coins']}", file=sys.stderr)
    return out


def main():
    ap = argparse.ArgumentParser(description="Riset range LP optimal di koin sampel")
    ap.add_argument("--build", nargs="*", metavar="DATA_JSON", help="tulis ranges.npz (default: reference_tokens.txt)")
    ap.add_argument("--eval", action="store_true", help="uji leave-one-coin-out dan simpan hasilnya ke ranges.npz")
    args = ap.parse_args()
    if args.build is not None:
        from .lp_check import reference_paths
        d = build(args.build or reference_paths())
        np.savez_compressed(DATA_PATH, **d)
        print(f"{DATA_PATH}: {len(json.loads(d['tokens']))} koin, {len(d['t'])} momen")
    if args.eval:
        rs = Ranges.load()
        test = evaluate(rs)
        with np.load(DATA_PATH, allow_pickle=False) as f:
            d = {k: f[k] for k in f.files}
        d["test"] = json.dumps(test)
        np.savez_compressed(DATA_PATH, **d)
        print(json.dumps(test, indent=1))


if __name__ == "__main__":
    main()
