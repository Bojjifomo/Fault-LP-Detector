"""
Uji fitur secara jujur: apakah sebuah data benar-benar membantu memprediksi dump, atau cuma kelihatan
masuk akal?

Untuk tiap token: titik tiap 30 menit sepanjang riwayat, label = harga turun >= 30% dalam 8 jam
sesudahnya. Lalu:
1. AUC tiap fitur sendiri-sendiri (0,5 = tebak koin, 1 = sempurna), per token dan gabungan.
2. Model logistik leave-one-token-out: dilatih tanpa token X, diuji di token X. Dibandingkan dengan
   vonis aturan (LAYAK/HATI-HATI/JANGAN) dan dengan model tanpa fitur baru.

  python -m dump_alert.study dump_alert/data/0x....json [...]
"""
import argparse
import bisect
import json
import math
import sys

import numpy as np

from .flow import NAMES as FLOW_NAMES
from .flow import flow_features
from .lp_check import scan
from .signals import Token
from .technicals import NAMES as TECH_NAMES
from .technicals import tech_features

H = 3600
BASE = ("r1h", "r4h", "r24h", "ext24", "off24", "off_ath", "vol6", "wn1", "wn6", "fresh", "top10", "log_age",
        "ath4h_break", "pat_retest", "pat_gagal", "pat_breakdown")
EXTRA = ("hour_sin", "hour_cos")


def row_x(r, pt, pv, ff=None, tf=None):
    """Fitur satu titik (baris features()/scan() + fitur flow & teknikal di waktu yang sama)."""
    k = bisect.bisect_right(pt, r["t"] - 24 * H) - 1
    st = (r["pattern"] or {}).get("state")
    return {"r1h": r["r1h"], "r4h": r["r4h"], "r24h": r["px"] / pv[k] - 1 if k >= 0 else None,
            "ext24": r["ext24"], "off24": r["off24"], "off_ath": r["px"] / r["ath"] - 1, "vol6": r["vol6"],
            "wn1": r["wn1"], "wn6": r["wn6"], "fresh": r["fresh"], "top10": r["top10"],
            "log_age": math.log1p(r["age_h"]), "ath4h_break": float(r["ath4h_break"]),
            "pat_retest": float(st == "RETEST"), "pat_gagal": float(st == "GAGAL"),
            "pat_breakdown": float(st == "BREAKDOWN"),
            "hour_sin": math.sin(2 * math.pi * (r["t"] % 86400) / 86400),
            "hour_cos": math.cos(2 * math.pi * (r["t"] % 86400) / 86400), **(ff or {}), **(tf or {})}


def rows_for(path, every=6, sleep_h=8.0, tok=None):
    tok = tok or Token(json.load(open(path)))
    rows = [r for r in scan(tok, sleep_h)[::every] if r["dd"] is not None]
    if not rows:
        return tok, []
    pt, pv = tok._pt, [p[2] for p in tok.prices]
    ff = flow_features(tok, [r["t"] for r in rows])
    tf = tech_features(tok, [r["t"] for r in rows])
    out = []
    for r in rows:
        out.append({"t": r["t"], "x": row_x(r, pt, pv, ff.get(r["t"]), tf.get(r["t"])), "level": r["level"],
                    "y30": r["dd"] <= -0.3, "y50": r["dd"] <= -0.5, "dd": r["dd"]})
    return tok, out


def feature_test(data, names, label="y30"):
    """AUC tiap fitur per token: median, arah, dan berapa token yang searah (untuk label di website)."""
    out = {}
    for n in names:
        per = []
        for rs in data.values():
            xx = np.array([r["x"].get(n) if r["x"].get(n) is not None else np.nan for r in rs], float)
            yy = np.array([r[label] for r in rs])
            m = ~np.isnan(xx)
            a = auc(xx[m], yy[m]) if m.sum() else None
            if a is not None:
                per.append(a)
        if per:
            med = float(np.median(per))
            sign = 1 if med >= 0.5 else -1
            out[n] = {"median": round(med, 3), "dir": sign, "consistent": sum((a - 0.5) * sign > 0 for a in per),
                      "n": len(per)}
    return out


def useful(ft, n):
    """Fitur dianggap membantu kalau AUC median >= 0,58 (atau <= 0,42) dan searah di >= 70% token."""
    x = (ft or {}).get(n)
    return bool(x and abs(x["median"] - 0.5) >= 0.08 and x["consistent"] >= 0.7 * x["n"])


def auc(score, y):
    score, y = np.asarray(score, float), np.asarray(y, bool)
    pos, neg = y.sum(), (~y).sum()
    if not pos or not neg:
        return None
    order = np.argsort(score, kind="mergesort")
    ranks = np.empty(len(score))
    s = score[order]
    i = 0
    while i < len(s):  # rata-rata rank untuk nilai kembar
        j = i
        while j + 1 < len(s) and s[j + 1] == s[i]:
            j += 1
        ranks[order[i:j + 1]] = (i + j) / 2 + 1
        i = j + 1
    return (ranks[y].sum() - pos * (pos + 1) / 2) / (pos * neg)


def matrix(rows, names, med=None):
    X = np.array([[r["x"].get(n) if r["x"].get(n) is not None else np.nan for n in names] for r in rows], float)
    if med is None:
        med = np.nanmedian(X, axis=0)
        med = np.where(np.isnan(med), 0.0, med)
    X = np.where(np.isnan(X), med, X)
    return X, med


def fit_logistic(X, y, w, lam=1.0, iters=50):
    """Regresi logistik L2 (IRLS), fitur sudah distandarkan. Mengembalikan (bias, koefisien)."""
    n, d = X.shape
    A = np.hstack([np.ones((n, 1)), X])
    beta = np.zeros(d + 1)
    reg = np.full(d + 1, lam)
    reg[0] = 0.0
    for _ in range(iters):
        p = 1 / (1 + np.exp(-np.clip(A @ beta, -30, 30)))
        g = A.T @ (w * (p - y)) + reg * beta
        Hm = (A * (w * p * (1 - p))[:, None]).T @ A + np.diag(reg)
        step = np.linalg.solve(Hm, g)
        beta -= step
        if np.abs(step).max() < 1e-6:
            break
    return beta


class Model:
    def __init__(self, names, lam=1.0, label="y30"):
        self.names, self.lam, self.label = names, lam, label

    def fit(self, rows):
        X, self.med = matrix(rows, self.names)
        self.mu, self.sd = X.mean(0), X.std(0) + 1e-9
        y = np.array([r[self.label] for r in rows], float)
        tok_n = {}
        for r in rows:
            tok_n[r["tok"]] = tok_n.get(r["tok"], 0) + 1
        w = np.array([len(rows) / len(tok_n) / tok_n[r["tok"]] for r in rows])  # tiap token sama berat
        self.beta = fit_logistic((X - self.mu) / self.sd, y, w, self.lam)
        return self

    def predict(self, rows):
        X, _ = matrix(rows, self.names, self.med)
        z = self.beta[0] + ((X - self.mu) / self.sd) @ self.beta[1:]
        return 1 / (1 + np.exp(-np.clip(z, -30, 30)))


def loto(data, names, lam=1.0, label="y30"):
    """Prediksi out-of-sample per token (dilatih tanpa token itu)."""
    preds = {}
    for sym in data:
        train = [r for s, rs in data.items() if s != sym for r in rs]
        preds[sym] = Model(names, lam, label).fit(train).predict(data[sym])
    return preds


def report(data, label="y30"):
    syms = list(data)
    L = [f"Token: {', '.join(f'{s} ({len(data[s])} titik, {sum(r[label] for r in data[s])} dump)' for s in syms)}", ""]
    names = list(BASE) + list(FLOW_NAMES) + list(EXTRA) + list(TECH_NAMES)
    L.append(f"{'fitur':14s} {'gabungan':>8s} {'median/token':>12s}  arah sama di")
    for n in names:
        per = [auc([r["x"].get(n) if r["x"].get(n) is not None else np.nan for r in data[s]], [r[label] for r in data[s]])
               for s in syms]
        allr = [r for s in syms for r in data[s]]
        xs = np.array([r["x"].get(n) if r["x"].get(n) is not None else np.nan for r in allr], float)
        ys = np.array([r[label] for r in allr])
        ok = ~np.isnan(xs)
        g = auc(xs[ok], ys[ok]) if ok.sum() else None
        per_ok = []
        for s in syms:
            xx = np.array([r["x"].get(n) if r["x"].get(n) is not None else np.nan for r in data[s]], float)
            yy = np.array([r[label] for r in data[s]])
            m = ~np.isnan(xx)
            a = auc(xx[m], yy[m]) if m.sum() else None
            if a is not None:
                per_ok.append(a)
        med = float(np.median(per_ok)) if per_ok else None
        same = sum((a - 0.5) * ((g or 0.5) - 0.5) > 0 for a in per_ok)
        del per
        L.append(f"{n:14s} {g if g is None else round(g, 3)!s:>8s} {med if med is None else round(med, 3)!s:>12s}  "
                 f"{same}/{len(per_ok)}")
    L.append("")
    models = {"vonis aturan sekarang": None, "logistik fitur lama": list(BASE), "logistik lama + baru": names}
    res = {}
    for title, feats in models.items():
        per, allp, ally = {}, [], []
        preds = loto(data, feats, label=label) if feats else {s: np.array([r["level"] for r in data[s]], float) for s in syms}
        for s in syms:
            y = [r[label] for r in data[s]]
            per[s] = auc(preds[s], y)
            allp += list(preds[s])
            ally += y
        res[title] = (per, preds)
        vals = [v for v in per.values() if v is not None]
        L.append(f"{title:24s} AUC per token: " + ", ".join(f"{s} {v:.2f}" if v is not None else f"{s} -" for s, v in per.items())
                 + f" | median {np.median(vals):.3f}")
    return "\n".join(L), res


# Fitur model peluang dump: dipilih dari alasan mekanis + hasil uji awal (7 koin); diuji ulang
# leave-one-token-out di semua koin sampel sebelum dipakai.
MODEL_FEATURES = ["off_ath", "ext24", "r4h", "vol6", "fresh", "wn6", "log_age", "buyers2", "sellers2", "profit3",
                  "early", "vol_ratio2", "liq_share", "ath4h_break", "pat_retest", "pat_gagal", "hour_sin",
                  "hour_cos", "top_buy4", "bb_width", "rsi1h"]
CAL_BINS = (0.0, 0.1, 0.2, 0.35, 0.5, 1.01)


def train_model(data, names=MODEL_FEATURES, lam=1.0, label="y30"):
    """Model logistik dari semua koin + hasil uji leave-one-token-out (AUC per koin & kalibrasi)."""
    m = Model(names, lam, label).fit([r for rs in data.values() for r in rs])
    preds = loto(data, names, lam, label)
    per, cal = {}, [[0, 0] for _ in CAL_BINS[:-1]]
    for sym, rs in data.items():
        y = [r[label] for r in rs]
        per[sym] = auc(preds[sym], y)
        for p_, yy in zip(preds[sym], y):
            b = bisect.bisect_right(CAL_BINS, p_) - 1
            cal[b][0] += 1
            cal[b][1] += yy
    return {"names": list(names), "label": label, "med": m.med.tolist(), "mu": m.mu.tolist(), "sd": m.sd.tolist(),
            "beta": m.beta.tolist(), "loto_auc": per,
            "calibration": [{"lo": CAL_BINS[i], "hi": CAL_BINS[i + 1], "n": n, "rate": (k / n) if n else None}
                            for i, (n, k) in enumerate(cal)]}, preds


def model_prob(model, x, factors=False):
    """Peluang dump dari model; factors=True juga mengembalikan kontribusi tiap fitur (z x koefisien)."""
    if not model:
        return (None, {}) if factors else None
    v = np.array([x.get(n) if x.get(n) is not None else np.nan for n in model["names"]], float)
    v = np.where(np.isnan(v), np.array(model["med"]), v)
    z = (v - np.array(model["mu"])) / np.array(model["sd"])
    contrib = z * np.array(model["beta"][1:])
    p = float(1 / (1 + np.exp(-np.clip(model["beta"][0] + contrib.sum(), -30, 30))))
    return (p, dict(zip(model["names"], contrib.tolist()))) if factors else p


def adjust_level(level, p, t_caution, t_avoid):
    """Vonis aturan dinaikkan oleh model: OK -> CAUTION kalau p >= t_caution, CAUTION -> AVOID kalau
    p >= t_avoid. Model tidak pernah menurunkan vonis (filter keras tetap berlaku)."""
    if p is None:
        return level
    if level == 0 and p >= t_caution:
        return 2 if p >= t_avoid else 1
    if level == 1 and p >= t_avoid:
        return 2
    return level


def verdict_rates(data, preds, t_caution, t_avoid, label="y30"):
    """Peluang dump per vonis: aturan saja vs aturan + model (prediksi leave-one-token-out)."""
    out = {"rules": [[0, 0] for _ in range(3)], "combined": [[0, 0] for _ in range(3)]}
    for sym, rs in data.items():
        for p_, r in zip(preds[sym], rs):
            for key, lv in (("rules", r["level"]), ("combined", adjust_level(r["level"], p_, t_caution, t_avoid))):
                out[key][lv][0] += 1
                out[key][lv][1] += r[label]
    return out


def coin_value(data, names, label="y30", lam=1.0):
    """Kontribusi tiap koin sebagai data latih: untuk tiap koin uji o, AUC model yang dilatih dengan koin c
    dikurangi AUC tanpa koin c (keduanya tanpa o). Positif = koin c membuat prediksi di koin lain lebih
    akurat; negatif = koin c mengacaukan."""
    syms = list(data)
    out = {}
    for c in syms:
        deltas = []
        for o in syms:
            if o == c:
                continue
            y = [r[label] for r in data[o]]
            if not any(y) or all(y):
                continue
            with_c = [r for s, rs in data.items() if s != o for r in rs]
            without = [r for s, rs in data.items() if s not in (o, c) for r in rs]
            a1 = auc(Model(names, lam, label).fit(with_c).predict(data[o]), y)
            a0 = auc(Model(names, lam, label).fit(without).predict(data[o]), y)
            deltas.append(a1 - a0)
        out[c] = (float(np.mean(deltas)) if deltas else None, sum(d > 0 for d in deltas), len(deltas))
    return out


def final_eval(data, lib, fresh=None, label="y30"):
    """Perbandingan akhir per koin (leave-one-token-out): vonis aturan, model logistik, situasi mirip, dan
    rata-rata model + situasi mirip. `fresh` = koin yang tidak dipakai saat memilih fitur (uji paling bersih)."""
    from .lp_check import ANALOG_GROUPS
    preds = loto(data, MODEL_FEATURES, label=label)
    sym_idx = {t["symbol"]: i for i, t in enumerate(lib.tokens)}
    res = {}
    for sym, rs in data.items():
        y = [r[label] for r in rs]
        out = {"rules": auc([r["level"] for r in rs], y), "model": auc(preds[sym], y)}
        ti = sym_idx.get(sym)
        if ti is not None:
            ap, mp, yy = [], [], []
            by_t = {r["t"]: (p, r) for p, r in zip(preds[sym], rs)}
            for i in np.nonzero(lib.tok == ti)[0]:
                t = int(lib.t[i])
                if t not in by_t:
                    continue
                v = {n: (None if np.isnan(x) else x * sd + mu) for n, x, sd, mu in zip(lib.names, lib.Z[i], lib.sd, lib.mu)}
                q = lib.query(v, exclude=ti, groups=ANALOG_GROUPS)
                if q:
                    ap.append(q["p30"])
                    mp.append(by_t[t][0])
                    yy.append(by_t[t][1][label])
            out["analogs"] = auc(ap, yy)
            out["combo"] = auc([(a + b) / 2 for a, b in zip(ap, mp)], yy)
        res[sym] = out
    lines = [f"{'koin':12s} {'aturan':>7s} {'model':>7s} {'mirip':>7s} {'gabung':>7s}"]
    for sym, o in res.items():
        tag = " *" if fresh and sym in fresh else ""
        lines.append(f"{sym:12s} " + " ".join(f"{o.get(k):7.2f}" if o.get(k) is not None else f"{'-':>7s}"
                                               for k in ("rules", "model", "analogs", "combo")) + tag)
    for title, subset in (("median semua", list(res)), ("median koin baru *", [s for s in res if fresh and s in fresh])):
        if not subset:
            continue
        med = {k: np.median([res[s][k] for s in subset if res[s].get(k) is not None]) for k in ("rules", "model", "analogs", "combo")}
        win = {k: sum(1 for s in subset if res[s].get(k) is not None and res[s]["rules"] is not None
                      and res[s][k] > res[s]["rules"]) for k in ("model", "analogs", "combo")}
        lines.append(f"{title:18s} aturan {med['rules']:.3f} | model {med['model']:.3f} (menang di {win['model']}/{len(subset)}) "
                     f"| mirip {med['analogs']:.3f} ({win['analogs']}) | gabung {med['combo']:.3f} ({win['combo']})")
    return res, "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--label", default="y30", choices=["y30", "y50"])
    ap.add_argument("--value", help="ukur kontribusi tiap koin untuk fitur ini (dipisah koma, atau 'all')")
    ap.add_argument("--final", action="store_true", help="perbandingan akhir aturan vs model vs situasi mirip")
    ap.add_argument("--fresh", default="", help="simbol koin yang tidak dipakai saat memilih fitur (dipisah koma)")
    args = ap.parse_args()
    data = {}
    for p in args.paths:
        tok, rows = rows_for(p)
        if len(rows) >= 20:
            for r in rows:
                r["tok"] = tok.symbol
            data[tok.symbol] = rows
            print(f"{tok.symbol}: {len(rows)} titik", file=sys.stderr)
    if args.final:
        from .analogs import Library
        _, text = final_eval(data, Library.load(), set(args.fresh.split(",")) if args.fresh else None, args.label)
        print(text)
        return
    text, _ = report(data, args.label)
    print(text)
    if args.value:
        names = args.value.split(",") if args.value != "all" else list(BASE) + list(FLOW_NAMES) + list(EXTRA) + list(TECH_NAMES)
        print("\nKontribusi koin sebagai sampel (rata-rata perubahan AUC di koin lain):")
        for c, (d, pos, n) in sorted(coin_value(data, names, args.label).items(), key=lambda kv: -(kv[1][0] or 0)):
            print(f"  {c:12s} {d:+.3f}  (membantu di {pos}/{n} koin)" if d is not None else f"  {c:12s} -")


if __name__ == "__main__":
    main()
