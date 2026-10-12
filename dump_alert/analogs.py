"""
Pencari situasi mirip: kondisi koin sekarang dibandingkan dengan setiap momen (tiap jam) di riwayat koin
sampel, lewat lima sisi:

- pattern  : tanda-tanda sebelum crash (naik tajam, sudah berlipat, di pucuk, retest ATH, whale borong/
             jual, dompet besar baru, tembus ATH 4 jam), status pola retest
- price    : bentuk grafik 24 jam terakhir (harga 2, 4, ... 24 jam lalu dan 1..6 jam lalu relatif ke
             harga sekarang), naik 4 jam, jarak dari puncak 24 jam / ATH
- whale    : jual/beli bersih whale 6 jam sekarang dan 6/12/18 jam lalu, 1 jam, dompet besar baru, supply
             yang dipegang holder untung >= 3x / insider / sniper, top-10
- tech     : RSI 1 jam & 15 menit, jarak ke EMA20, tren EMA, VWAP, Bollinger, struktur swing, lonjakan
             volume, porsi volume beli
- context  : umur koin, jumlah pembeli & penjual unik 2 jam, cadangan pool / supply, volatilitas

Hasil: momen paling mirip (paling banyak satu per koin per 12 jam) dan apa yang terjadi 8 jam sesudahnya.
Library momen dibuat dari data koin sampel (python -m dump_alert.analogs --build) dan disimpan di
dump_alert/analogs.json.gz supaya website tidak butuh data mentahnya.
"""
import argparse
import bisect
import gzip
import json
import math
import os
import sys

import numpy as np

from .flow import flow_features
from .lp_check import forward_rise, reference_paths, scan, signs_of
from .signals import Token
from .technicals import tech_features

H = 3600
LIB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "analogs.json.gz")
SIGN_CODES = ("pump", "lipat", "pucuk", "retest", "borong", "jual", "baru", "ath4j")
GROUPS = {
    "pattern": [f"s_{c}" for c in SIGN_CODES] + ["pat_gagal", "pat_breakdown"],
    "price": [f"p2h_{k}" for k in range(1, 13)] + [f"p1h_{k}" for k in range(1, 7)] + ["r4h", "off24", "ext24", "off_ath"],
    "whale": ["wn6", "wn6_6", "wn6_12", "wn6_18", "wn1", "fresh", "profit3", "early", "free", "top10"],
    "tech": ["rsi1h", "rsi15", "ema_gap", "ema_trend", "vwap_gap", "bb_pctb", "bb_width", "structure", "vol_ratio2",
             "buy_share4"],
    "context": ["log_age", "buyers2", "sellers2", "liq_share", "vol6"],
}
GROUP_LABEL = {"pattern": "Pattern", "price": "Price action", "whale": "Whales", "tech": "Technicals",
               "context": "Coin profile"}
NAMES = [n for g in GROUPS.values() for n in g]
K = 30            # jumlah momen mirip yang dipakai
SPREAD = 12 * H   # momen dari koin yang sama minimal berjarak 12 jam (supaya tidak satu kejadian dihitung berkali-kali)
MISSING = 1.5     # jarak untuk sisi yang datanya kosong (kemiripan 25%)


def moment(tok, rows_by_t, row_ts, t, pt, pv, ff, tf):
    """Vektor fitur (dict) untuk waktu t. rows_by_t: baris features()/scan() per waktu."""
    k = bisect.bisect_right(row_ts, t) - 1
    if k < 0:
        return None
    r = rows_by_t[row_ts[k]]
    j = bisect.bisect_right(pt, t) - 1
    if j < 0:
        return None
    p = pv[j]

    def path(sec):
        i = bisect.bisect_right(pt, t - sec) - 1
        return math.log(pv[i] / p) if i >= 0 and t - sec >= pt[0] else None

    def wn6_at(sec):
        i = bisect.bisect_right(row_ts, t - sec) - 1
        return rows_by_t[row_ts[i]]["wn6"] if i >= 0 and t - sec >= row_ts[0] else None

    st = (r["pattern"] or {}).get("state")
    signs = signs_of(r)
    v = {f"s_{c}": float(c in signs) for c in SIGN_CODES}
    v.update(pat_gagal=float(st == "GAGAL"), pat_breakdown=float(st == "BREAKDOWN"))
    v.update({f"p2h_{n}": path(n * 2 * H) for n in range(1, 13)})
    v.update({f"p1h_{n}": path(n * H) for n in range(1, 7)})
    v.update(r4h=r["r4h"], off24=r["off24"], ext24=r["ext24"], off_ath=r["px"] / r["ath"] - 1)
    v.update(wn6=r["wn6"], wn6_6=wn6_at(6 * H), wn6_12=wn6_at(12 * H), wn6_18=wn6_at(18 * H), wn1=r["wn1"],
             fresh=r["fresh"], top10=r["top10"])
    v.update({n: (ff or {}).get(n) for n in ("profit3", "early", "free", "buyers2", "sellers2", "liq_share")})
    v.update({n: (tf or {}).get(n) for n in GROUPS["tech"]})
    v.update(log_age=math.log1p(r["age_h"]), vol6=r["vol6"])
    return v


def token_moments(tok, every=H, sleep_h=8.0, rows=None, times=None):
    """Momen (t, vektor, dd 8 jam, naik 8 jam) tiap `every` detik sepanjang riwayat token."""
    rows = rows if rows is not None else scan(tok, sleep_h)
    if not rows:
        return []
    by_t = {r["t"]: r for r in rows}
    row_ts = sorted(by_t)
    pt, pv = tok._pt, [p[2] for p in tok.prices]
    if times is None:
        times = [t for t in row_ts if t % every == 0 or every <= 300]
    ff, tf = flow_features(tok, times), tech_features(tok, times)
    rise = forward_rise(tok, sleep_h)
    out = []
    for t in times:
        v = moment(tok, by_t, row_ts, t, pt, pv, ff.get(t), tf.get(t))
        if v is None:
            continue
        r = by_t[row_ts[bisect.bisect_right(row_ts, t) - 1]]
        j = bisect.bisect_right(pt, t) - 1
        out.append({"t": t, "v": v, "dd": r.get("dd"), "rise": rise[j] if j >= 0 else None, "level": r.get("level")})
    return out


class Library:
    def __init__(self, data):
        self.tokens = data["tokens"]
        self.names = data["names"]
        self.mu, self.sd = np.array(data["mu"]), np.array(data["sd"])
        m = data["moments"]
        self.tok = np.array([x[0] for x in m])
        self.t = np.array([x[1] for x in m])
        self.dd = np.array([x[2] for x in m], float)
        self.rise = np.array([x[3] if x[3] is not None else np.nan for x in m], float)
        X = np.array([[np.nan if y is None else y for y in x[4:]] for x in m], float)
        self.Z = np.clip((X - self.mu) / self.sd, -4, 4)
        self.idx = {g: [self.names.index(n) for n in ns if n in self.names] for g, ns in GROUPS.items()}

    @classmethod
    def load(cls, path=LIB_PATH):
        if not os.path.exists(path):
            return None
        with gzip.open(path, "rt") as fh:
            return cls(json.load(fh))

    def zvec(self, v):
        x = np.array([np.nan if v.get(n) is None else v[n] for n in self.names], float)
        return np.clip((x - self.mu) / self.sd, -4, 4)

    def group_dist(self, z, mask=None):
        """Jarak RMS per grup (nilai kosong dilewati) -> {grup: array jarak ke tiap momen}."""
        out = {}
        Z = self.Z if mask is None else self.Z[mask]
        for g, ix in self.idx.items():
            d = Z[:, ix] - z[ix]
            ok = ~np.isnan(d)
            n = ok.sum(1)
            s = np.where(ok, d, 0.0) ** 2
            out[g] = np.where(n >= max(1, len(ix) // 3), np.sqrt(s.sum(1) / np.maximum(n, 1)), np.nan)
        return out

    def query(self, v, exclude=None, k=K, groups=None, spread=SPREAD):
        """Momen paling mirip (tanpa token `exclude`). Mengembalikan ringkasan hasil 8 jam sesudahnya."""
        mask = np.ones(len(self.tok), bool)
        if exclude is not None:
            mask &= self.tok != exclude
        mask &= ~np.isnan(self.dd)
        z = self.zvec(v)
        gd = self.group_dist(z, mask)
        use = groups or list(GROUPS)
        # sisi yang datanya kosong (mis. koin baru launch, belum ada riwayat 24 jam) dihitung tidak mirip,
        # supaya momen dengan data tidak lengkap tidak terlihat sangat mirip
        D = np.mean(np.vstack([np.where(np.isnan(gd[g]), MISSING, gd[g]) for g in use]), axis=0)
        idx_all = np.nonzero(mask)[0]
        order = np.argsort(D)
        picked, taken = [], {}
        for o in order:
            if not np.isfinite(D[o]):
                break
            i = idx_all[o]
            ti, tt = int(self.tok[i]), int(self.t[i])
            if any(abs(tt - x) < spread for x in taken.get(ti, ())):
                continue
            taken.setdefault(ti, []).append(tt)
            picked.append((o, i))
            if len(picked) >= k:
                break
        if not picked:
            return None
        sim = lambda d: max(0.0, 1 - d / 2)  # jarak RMS z-score 0 -> 100%, 1 -> 50%, >=2 -> 0%
        w = np.array([max(sim(D[o]), 1e-3) for o, _ in picked])
        dd = np.array([self.dd[i] for _, i in picked])
        rise = np.array([self.rise[i] for _, i in picked])
        examples = []
        for o, i in picked[:8]:
            examples.append({"symbol": self.tokens[int(self.tok[i])]["symbol"], "t": int(self.t[i]),
                             "sim": sim(D[o]), "dd": float(self.dd[i]),
                             "rise": None if np.isnan(self.rise[i]) else float(self.rise[i]),
                             "groups": {g: (None if np.isnan(gd[g][o]) else sim(gd[g][o])) for g in GROUPS}})
        return {"n": len(picked), "p30": float((w * (dd <= -0.3)).sum() / w.sum()),
                "p50": float((w * (dd <= -0.5)).sum() / w.sum()), "n30": int((dd <= -0.3).sum()),
                "median_dd": float(np.median(dd)), "median_rise": float(np.nanmedian(rise)) if np.isfinite(rise).any() else None,
                "sim": float(np.mean([sim(D[o]) for o, _ in picked])),
                "group_sim": {g: float(np.nanmean([sim(gd[g][o]) for o, _ in picked if not np.isnan(gd[g][o])]))
                              if any(not np.isnan(gd[g][o]) for o, _ in picked) else None for g in GROUPS},
                "examples": examples, "tokens": len({int(self.tok[i]) for _, i in picked})}


def build(paths, every=H):
    tokens, moments = [], []
    for ti, p in enumerate(paths):
        tok = Token(json.load(open(p)))
        ms = [m for m in token_moments(tok, every) if m["dd"] is not None]
        tokens.append({"symbol": tok.symbol, "address": tok.address, "n": len(ms)})
        for m in ms:
            moments.append((ti, m))
        print(f"{tok.symbol}: {len(ms)} momen", file=sys.stderr)
    X = np.array([[np.nan if m["v"].get(n) is None else m["v"][n] for n in NAMES] for _, m in moments], float)
    mu = np.nanmean(X, 0)
    sd = np.nanstd(X, 0)
    mu, sd = np.where(np.isnan(mu), 0, mu), np.where((np.isnan(sd)) | (sd < 1e-9), 1, sd)
    rnd = lambda x: None if x is None or (isinstance(x, float) and math.isnan(x)) else round(float(x), 4)
    return {"tokens": tokens, "names": NAMES, "mu": [round(float(x), 6) for x in mu], "sd": [round(float(x), 6) for x in sd],
            "moments": [[ti, m["t"], rnd(m["dd"]), rnd(m["rise"])] + [rnd(m["v"].get(n)) for n in NAMES] for ti, m in moments]}


def evaluate(lib, groups=None):
    """Leave-one-token-out: tiap momen token X dicocokkan hanya ke token lain -> AUC peluang dump."""
    from .study import auc
    out = {}
    for ti, tk in enumerate(lib.tokens):
        sel = np.nonzero(lib.tok == ti)[0]
        if len(sel) < 20:
            continue
        ps, ys = [], []
        for i in sel[::2]:
            v = {n: (None if np.isnan(x) else x * s + m) for n, x, s, m in zip(lib.names, lib.Z[i], lib.sd, lib.mu)}
            q = lib.query(v, exclude=ti, groups=groups)
            if q:
                ps.append(q["p30"])
                ys.append(lib.dd[i] <= -0.3)
        out[tk["symbol"]] = auc(ps, ys)
    return out


def main():
    ap = argparse.ArgumentParser(description="Library momen untuk pencarian situasi mirip")
    ap.add_argument("--build", nargs="*", metavar="DATA_JSON", help="buat library (default: reference_tokens.txt)")
    ap.add_argument("--eval", action="store_true", help="uji leave-one-token-out")
    args = ap.parse_args()
    if args.build is not None:
        data = build(args.build or reference_paths())
        with gzip.open(LIB_PATH, "wt") as fh:
            json.dump(data, fh, separators=(",", ":"))
        print(f"{LIB_PATH}: {len(data['tokens'])} token, {len(data['moments'])} momen")
    if args.eval:
        lib = Library.load()
        res = evaluate(lib)
        vals = [v for v in res.values() if v is not None]
        print("semua sisi   AUC: " + ", ".join(f"{s} {v:.2f}" for s, v in res.items() if v is not None)
              + f" | median {np.median(vals):.3f}")
        for g in GROUPS:
            r = evaluate(lib, [x for x in GROUPS if x != g])
            v = [x for x in r.values() if x is not None]
            print(f"tanpa {g:8s} median AUC {np.median(v):.3f}")


if __name__ == "__main__":
    main()
