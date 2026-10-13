"""
Analisa tambahan dari data yang sudah ditarik (tanpa panggilan RPC baru):

- top_holders : 10 wallet terbesar (bukan kontrak/pool): porsi supply, harga beli rata-rata, untung berapa
                kali, apa yang mereka lakukan 6 / 24 jam terakhir (beli / jual / diam). Holder besar yang
                sudah untung banyak dan mulai menjual = sumber dump.
- money_flow  : uang yang masuk (beli) dan keluar (jual) 1 / 4 / 24 jam terakhir, jumlah pembeli & penjual,
                dan beli/jual bersih wallet besar.
- cost_basis  : harga beli rata-rata & saldo tiap wallet di waktu t — untuk resistance "holder yang sedang rugi"
                (mereka cenderung menjual saat harga kembali ke harga belinya).
"""
import bisect
import collections

from .signals import INFLOW

H = 3600
BURN = {"0x000000000000000000000000000000000000dead", "0xdead000000000000000042069420694206942069"}
BIG = 0.003  # wallet besar = pernah memegang >= 0,3% supply (sama dengan definisi whale di sinyal)


def _price_at(tok):
    pt, pv = tok._pt, [p[2] for p in tok.prices]

    def price(t):
        k = bisect.bisect_right(pt, t) - 1
        return pv[k] if k >= 0 else None
    return price


def top_holders(tok, t, n=10):
    """10 wallet terbesar pada waktu t. Harga beli = rata-rata harga semua pembelian (jual mengurangi modal
    secara proporsional); wallet yang tidak pernah membeli (claim/airdrop/transfer) ditandai 'free'."""
    price, S = _price_at(tok), tok.supply
    bal, cost, bought = collections.Counter(), collections.Counter(), set()
    first, last = {}, {}
    chg6, chg24 = collections.Counter(), collections.Counter()
    for et, k, a, v in tok.events:
        if et > t:
            break
        if a in BURN:
            continue
        first.setdefault(a, et)
        if k in INFLOW:
            if k == "buy":
                cost[a] += v * (price(et) or 0.0)
                bought.add(a)
            bal[a] += v
            d = v
        else:
            if bal[a] > 0:
                cost[a] *= max(0.0, bal[a] - v) / bal[a]
            bal[a] -= v
            d = -v
        if k in ("buy", "sell", "xin", "xout", "recv"):
            last[a] = (et, k, v)
        if et >= t - 6 * H:
            chg6[a] += d
        if et >= t - 24 * H:
            chg24[a] += d
    p = price(t)
    out = []
    for a, b in sorted(((a, b) for a, b in bal.items() if b > 0), key=lambda x: -x[1])[:n]:
        entry = cost[a] / b if a in bought and cost[a] > 0 else None
        out.append({"wallet": a, "share": b / S, "entry": entry, "x": p / entry if entry and p else None,
                    "free": a not in bought, "first": first.get(a), "last": last.get(a),
                    "chg6": chg6[a] / S, "chg24": chg24[a] / S})
    return out


def holder_summary(top):
    """Ringkasan untuk kesimpulan: porsi top-10, berapa yang untung >= 3x / dapat gratis, berapa yang menjual."""
    if not top:
        return None
    return {"share": sum(x["share"] for x in top),
            "big_profit": sum(1 for x in top if x["free"] or (x["x"] or 0) >= 3),
            "free": sum(1 for x in top if x["free"]),
            "sold24": [x for x in top if x["chg24"] <= -0.0005], "bought24": [x for x in top if x["chg24"] >= 0.0005],
            "sold6": [x for x in top if x["chg6"] <= -0.0005]}


def money_flow(tok, t, windows=(1, 4, 24)):
    """Uang masuk/keluar per jendela (dalam aset quote): beli, jual, bersih, pembeli & penjual unik, transaksi
    terbesar, dan beli/jual bersih wallet besar (>= 0,3% supply)."""
    price, S = _price_at(tok), tok.supply
    peak = collections.Counter()
    bal = collections.Counter()
    big = set()
    span = max(windows) * H
    trades = []
    for et, k, a, v in tok.events:
        if et > t:
            break
        bal[a] += v if k in INFLOW else -v
        if bal[a] > peak[a]:
            peak[a] = bal[a]
            if peak[a] >= BIG * S:
                big.add(a)
        if k in ("buy", "sell") and et > t - span:
            trades.append((et, k, a, v * (price(et) or 0.0), v))
    out = []
    for w in windows:
        sel = [x for x in trades if x[0] > t - w * H]
        buy = sum(x[3] for x in sel if x[1] == "buy")
        sell = sum(x[3] for x in sel if x[1] == "sell")
        whale = sum((x[4] if x[1] == "buy" else -x[4]) for x in sel if x[2] in big) / S
        out.append({"hours": w, "buy": buy, "sell": sell, "net": buy - sell,
                    "buyers": len({x[2] for x in sel if x[1] == "buy"}), "sellers": len({x[2] for x in sel if x[1] == "sell"}),
                    "max_buy": max((x[3] for x in sel if x[1] == "buy"), default=0.0),
                    "max_sell": max((x[3] for x in sel if x[1] == "sell"), default=0.0), "whale_net": whale})
    return out


def cost_basis(tok, times, min_share=1e-5):
    """{t: (harga beli rata-rata, saldo)} untuk tiap waktu di `times` (urut naik): semua wallet yang pernah membeli dan
    masih memegang >= min_share supply. Jual mengurangi modal secara proporsional (harga beli rata-rata tetap)."""
    import numpy as np
    price, S = _price_at(tok), tok.supply
    bal, cost, bought = collections.Counter(), collections.Counter(), set()
    E, i, out = tok.events, 0, {}
    for t in times:
        while i < len(E) and E[i][0] <= t:
            et, k, a, v = E[i]
            i += 1
            if a in BURN:
                continue
            if k in INFLOW:
                if k == "buy":
                    cost[a] += v * (price(et) or 0.0)
                    bought.add(a)
                bal[a] += v
            else:
                if bal[a] > 0:
                    cost[a] *= max(0.0, bal[a] - v) / bal[a]
                bal[a] -= v
        rows = [(cost[a] / b, b) for a in bought if (b := bal[a]) > min_share * S and cost[a] > 0]
        out[t] = (np.array([r[0] for r in rows]), np.array([r[1] for r in rows])) if rows else (np.zeros(0), np.zeros(0))
    return out
