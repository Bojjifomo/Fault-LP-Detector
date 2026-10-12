"""
Laporan pola pra-dump untuk satu atau beberapa dataset dump_alert.fetch, plus perbandingan.

  python -m dump_alert.analyze dump_alert/data/0xA.json [dump_alert/data/0xB.json ...]

Hasil: dump_alert/out/<SIMBOL>_report.md, dan compare_<A>_<B>.md kalau lebih dari satu token.
"""
import argparse
import bisect
import collections
import json
import os
import time

from .signals import LEVELS, RULES, Token, backtest, find_dumps, launch_risk, timeline

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
H = 3600


def fmt_t(t):
    return time.strftime("%m-%d %H:%M", time.gmtime(t)) if t else "-"


def num(v, f="{:.2f}"):
    return "-" if v is None else f.format(v)


def dur(sec):
    m = int(abs(sec) // 60)
    return f"{m // 60}j{m % 60:02d}m" if m >= 60 else f"{m}m"


def row_at(rows, t):
    i = bisect.bisect_right([r["t"] for r in rows], t) - 1
    return rows[i] if i >= 0 else None


def top_sellers(tok, a, b, n=10):
    """Wallet dengan net jual terbesar di [a, b] beserta asal tokennya."""
    net, first_sell, src = collections.Counter(), {}, collections.defaultdict(collections.Counter)
    for t, k, w, v in tok.events:
        if t <= b:
            if k in ("buy", "recv", "xin"):
                src[w][k] += v
        if a <= t <= b:
            if k == "sell":
                net[w] += v
                first_sell.setdefault(w, t)
            elif k == "buy":
                net[w] -= v
    out = []
    for w, v in net.most_common(n):
        if v <= 0:
            break
        s = src[w]
        out.append((w, v, first_sell.get(w), s["buy"], s["recv"], s["xin"]))
    return out


def analyze(tok: Token):
    rows = timeline(tok)
    dumps = find_dumps(tok.prices)
    res = {"tok": tok, "rows": rows, "dumps": [], "risk": launch_risk(tok), "bt": backtest(tok, rows)}
    for d in dumps:
        lo = d["peak_t"] - 6 * H
        win = [r for r in rows if lo <= r["t"] <= d["cross_t"]]
        fired = collections.Counter(k for r in win for k in r["rules"])
        res["dumps"].append({
            **d,
            "first_watch": next((r for r in win if r["level"] >= 1), None),
            "first_danger": next((r for r in win if r["level"] >= 2), None),
            "fired": fired,
            "snaps": [(lbl, row_at(rows, d["peak_t"] + off)) for lbl, off in
                      (("T-6j", -6 * H), ("T-3j", -3 * H), ("T-1j", -H), ("T-30m", -1800), ("puncak", 0))]
                     + [("turun 50%", row_at(rows, d["cross_t"]))],
            "sellers": top_sellers(tok, d["peak_t"], d["trough_t"]),
        })
    return res


def report(res) -> str:
    tok, ds, q = res["tok"], res["tok"].ds, res["tok"].quote
    m = ds.get("market") or {}
    P = tok.prices
    L = [f"# Pola pra-dump: {ds.get('name')} ({tok.symbol})", "",
         f"- Kontrak: `{tok.address}` · supply {tok.supply:,.0f}",
         f"- Market: Uniswap {m.get('kind', '-')} `{m.get('pool_id') or m.get('pool') or '-'}` vs {q}"
         + (f", hooks `{m['hooks']}`" if m.get("hooks") and m["hooks"] != "0x" + "0" * 40 else ""),
         f"- Data: {len(ds['transfers']):,} transfer, {len(tok.events):,} event wallet, "
         f"{fmt_t(ds['transfers'][0][1])} s/d {fmt_t(ds['transfers'][-1][1])} UTC"]
    if P:
        hi = max(P, key=lambda p: p[2])
        L.append(f"- FDV ({q}): awal {P[0][2] * tok.supply:,.1f} · tertinggi {hi[2] * tok.supply:,.1f} "
                 f"({fmt_t(hi[0])}) · terakhir {P[-1][2] * tok.supply:,.1f}")
    r = res["risk"]
    L += ["", "## Risiko struktural saat launch", ""]
    if r:
        L += ["| Metrik | Nilai |", "|---|---|",
              f"| Token di pool saat beli pertama | {r['seed_tokens']:,.0f} |",
              f"| Dibeli dalam 5 detik pertama | {num(r['snipe_5s_pct_pool'], '{:.1f}')}% token pool |",
              f"| Dibeli dalam 60 detik pertama | {num(r['snipe_60s_pct_pool'], '{:.1f}')}% token pool, {r['snipers_60s']} wallet |",
              f"| Sniper terbesar | {r['top_sniper_pct_supply']:.2f}% supply |",
              f"| Dibagikan di luar pool (claim/airdrop) 24 jam | {r['recv_24h_pct_supply']:.1f}% supply ke {r['recv_wallets']} wallet |",
              f"| Penerima claim yang menjual dalam 6 jam | {r['recv_sold_6h_pct']:.0f}% jatah (dipindah ke wallet lain {r['recv_moved_6h_pct']:.0f}%) |",
              f"| Top-10 wallet, 1 jam setelah launch | {r['top10_1h_pct']:.1f}% supply |", ""]
        L += [f"- ⚠️ {f}" for f in r["flags"]] or ["- tidak ada flag"]
    L += ["", "## Episode dump (turun ≥50% dari puncak dalam ≤12 jam)", ""]
    if not res["dumps"]:
        L.append("Tidak ada episode dump di data ini.")
    for n, d in enumerate(res["dumps"], 1):
        fw, fd = d["first_watch"], d["first_danger"]
        L += [f"### Dump #{n}: puncak {fmt_t(d['peak_t'])} → dasar {fmt_t(d['trough_t'])}", "",
              f"FDV {d['peak'] * tok.supply:,.1f} → {d['trough'] * tok.supply:,.1f} {q} "
              f"({(d['trough'] / d['peak'] - 1) * 100:.0f}%), tembus −50% pada {fmt_t(d['cross_t'])}.", ""]
        for lbl, a in (("WASPADA pertama", fw), ("BAHAYA pertama", fd)):
            if a:
                rel = a["t"] - d["peak_t"]
                when = f"{dur(rel)} {'sebelum' if rel < 0 else 'sesudah'} puncak"
                save = (a["px"] / d["trough"] - 1) * 100 if a["px"] else None
                L.append(f"- {lbl}: {fmt_t(a['t'])} ({when}, {dur(d['cross_t'] - a['t'])} sebelum −50%), "
                         f"FDV saat itu {num(a['px'] and a['px'] * tok.supply, '{:,.1f}')}; "
                         f"keluar di sini {num(save, '{:.0f}')}% di atas harga dasar. Rule: {', '.join(a['rules'])}")
            else:
                L.append(f"- {lbl}: tidak menyala dalam 6 jam sebelum puncak s/d −50%")
        L += ["", "| Waktu | FDV | Whale net jual 60m | Whale jual | Harga 60m | Lonjakan beli | Δ top-50 6j | Δ holder 6j | Rule |",
              "|---|---|---|---|---|---|---|---|---|"]
        for lbl, s in d["snaps"]:
            if s:
                L.append(f"| {lbl} {fmt_t(s['t'])} | {num(s['px'] and s['px'] * tok.supply, '{:,.0f}')} | "
                         f"{s['whale_net']:+.2f}% | {s['whale_sellers']} | {num(s['r1h'] and 100 * s['r1h'], '{:+.0f}%')} | "
                         f"{s['surge']:.1f}x | {num(s['d_top6h'], '{:+.1f}')} | {num(s['d_hold6h'], '{:+.1f}%')} | "
                         f"{', '.join(s['rules']) or '-'} |")
        if d["sellers"]:
            L += ["", "Net seller terbesar selama dump:", "",
                  "| Wallet | Net jual | Jual pertama | Asal: beli | claim/airdrop | transfer masuk |", "|---|---|---|---|---|---|"]
            for w, v, fs, b, rc, xi in d["sellers"]:
                L.append(f"| `{w}` | {v / tok.supply * 100:.2f}% | {fmt_t(fs)} | {b / 1e6:,.1f}M | {rc / 1e6:,.1f}M | {xi / 1e6:,.1f}M |")
        L.append("")
    bt = res["bt"]
    L += ["## Kualitas rule (backtest di token ini)", ""]
    if bt:
        L += [f"Baseline: drawdown ≥30% dalam 6 jam terjadi di {bt['baseline'] * 100:.0f}% titik waktu ({bt['n']} titik).", "",
              "| Rule | Menyala (% waktu) | Peluang DD ≥30%/6j | Lift | Median DD 6j |", "|---|---|---|---|---|"]
        for k, v in bt["rules"].items():
            name = f"{k}: {RULES[k]}" if k in RULES else k
            L.append(f"| {name} | {v['fired_pct']:.1f}% | {num(v['p_dd'] and 100 * v['p_dd'], '{:.0f}%')} | "
                     f"{num(v['lift'], '{:.1f}x')} | {num(v['median_dd'] and 100 * v['median_dd'], '{:.0f}%')} |")
        L += ["", "Lift > 1 berarti setelah rule menyala, peluang dump lebih tinggi dari biasanya. "
                  "Satu token = sampel kecil; jangan anggap angka ini pasti."]
    else:
        L.append("Data harga belum cukup untuk backtest.")
    return "\n".join(L) + "\n"


def compare(results) -> str:
    syms = [r["tok"].symbol for r in results]
    L = [f"# Perbandingan pola pra-dump: {' vs '.join(syms)}", "", "## Risiko launch", "",
         "| Metrik | " + " | ".join(syms) + " |", "|---|" + "---|" * len(syms)]
    keys = [("snipe_5s_pct_pool", "Sniping 5 dtk (% token pool)", "{:.0f}%"),
            ("snipe_60s_pct_pool", "Sniping 60 dtk (% token pool)", "{:.0f}%"),
            ("snipers_60s", "Wallet sniper 60 dtk", "{}"),
            ("recv_24h_pct_supply", "Claim/airdrop 24 jam (% supply)", "{:.0f}%"),
            ("recv_sold_6h_pct", "Penerima claim jual ≤6 jam", "{:.0f}%"),
            ("top10_1h_pct", "Top-10 wallet +1 jam", "{:.0f}%")]
    for k, lbl, f in keys:
        L.append(f"| {lbl} | " + " | ".join(num(r["risk"].get(k), f) for r in results) + " |")
    L += ["", "## Sinyal dalam 6 jam sebelum tiap dump", "",
          "| Token | Dump | Turun | " + " | ".join(f"{k}" for k in RULES) + " | Alert pertama |",
          "|---|---|---|" + "---|" * len(RULES) + "---|"]
    seen = collections.Counter()
    total = 0
    for r in results:
        for n, d in enumerate(r["dumps"], 1):
            total += 1
            for k in RULES:
                seen[k] += bool(d["fired"][k])
            a = d["first_danger"] or d["first_watch"]
            first = f"{LEVELS[a['level']]} {dur(a['t'] - d['peak_t'])} {'sebelum' if a['t'] < d['peak_t'] else 'sesudah'} puncak" if a else "-"
            L.append(f"| {r['tok'].symbol} | #{n} {fmt_t(d['peak_t'])} | {(d['trough'] / d['peak'] - 1) * 100:.0f}% | "
                     + " | ".join("✅" if d["fired"][k] else "·" for k in RULES) + f" | {first} |")
    L += ["", "Rule: " + "; ".join(f"{k} = {v}" for k, v in RULES.items()), ""]
    if total:
        common = [k for k in RULES if seen[k] == total]
        L.append("**Pola yang muncul sebelum semua dump:** "
                 + (", ".join(f"{k} ({RULES[k]})" for k in common) if common else "tidak ada rule yang muncul di semua dump"))
    return "\n".join(L) + "\n"


def main():
    ap = argparse.ArgumentParser(description="Laporan pola pra-dump token Robinhood Chain")
    ap.add_argument("data", nargs="+", help="file JSON dari dump_alert.fetch")
    args = ap.parse_args()
    os.makedirs(OUT_DIR, exist_ok=True)
    results = []
    for path in args.data:
        res = analyze(Token(json.load(open(path))))
        results.append(res)
        out = os.path.join(OUT_DIR, f"{res['tok'].symbol}_report.md")
        with open(out, "w") as f:
            f.write(report(res))
        print(f"{res['tok'].symbol}: {len(res['dumps'])} episode dump, flag launch: "
              f"{len(res['risk'].get('flags', []))} → {out}")
    if len(results) > 1:
        out = os.path.join(OUT_DIR, "compare_" + "_".join(r["tok"].symbol for r in results) + ".md")
        with open(out, "w") as f:
            f.write(compare(results))
        print(f"perbandingan → {out}")


if __name__ == "__main__":
    main()
