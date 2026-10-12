"""
Fitur tambahan dari data on-chain yang sudah ditarik (tanpa panggilan RPC baru), dihitung hanya dari
data sampai waktu t. Tiap fitur punya alasan mekanis kenapa bisa mendahului dump:

- profit3   : % supply dipegang wallet yang sudah untung >= 3x (atau dapat token gratis): makin besar,
              makin banyak yang tergoda ambil untung.
- free      : % supply dipegang wallet yang tidak pernah membeli (claim/airdrop/insider): modal nol.
- early     : % supply masih dipegang wallet yang masuk di jam pertama (sniper/insider).
- buyers2/sellers2, buy_ratio2: jumlah wallet unik yang beli / jual dalam 2 jam terakhir.
- new6      : wallet pembeli baru 6 jam terakhir dibanding rata-rata 6 jam dalam 48 jam: pembeli baru
              habis saat harga tinggi = tanda kelelahan.
- liq_chg6  : perubahan likuiditas (cadangan token di pool x akar harga) 6 jam: LP menarik likuiditas
              sebelum dump.
- liq_share : cadangan token di pool / supply (pool tipis dibanding market cap).
- holders6  : perubahan jumlah holder 6 jam.
- top_buy4  : porsi pembeli terbesar dalam volume beli 4 jam: pump yang digerakkan satu wallet.
"""
import bisect
import collections
import math

from .signals import INFLOW

H = 3600
NAMES = ("profit3", "free", "early", "buyers2", "sellers2", "buy_ratio2", "new6", "liq_chg6", "liq_share",
         "holders6", "top_buy4")


def flow_features(tok, times):
    """{t: {fitur}} untuk tiap t di `times` (urut naik)."""
    E, S = tok.events, tok.supply
    pt, pv = tok._pt, [p[2] for p in tok.prices]
    if not E or not pt:
        return {}
    t_buy = next((e[0] for e in E if e[1] == "buy"), None)
    if t_buy is None:
        return {}

    def price(t):
        k = bisect.bisect_right(pt, t) - 1
        return pv[k] if k >= 0 else None

    unit = 10 ** tok.ds["decimals"]
    res_t, res_v, R = [], [], 0.0  # cadangan token di semua market
    for x in tok.ds["transfers"]:
        d = (x[5] if x[4] in tok.markets else 0) - (x[5] if x[3] in tok.markets else 0)
        if d:
            R += d / unit
            res_t.append(x[1])
            res_v.append(R)

    def reserve(t):
        j = bisect.bisect_right(res_t, t) - 1
        return res_v[j] if j >= 0 else 0.0

    dust = S * 1e-6
    bal, cost, bought, first = collections.Counter(), collections.Counter(), set(), {}
    holders = 0
    trades = collections.deque()      # (t, kind, addr, v) beli/jual 6 jam terakhir
    firsts = collections.deque()      # waktu pembeli baru 48 jam terakhir
    hist_holders = []                 # (t, jumlah holder)
    out, i = {}, 0
    for t in times:
        while i < len(E) and E[i][0] < t:
            et, k, a, v = E[i]
            i += 1
            was = bal[a] > dust
            if k in INFLOW:
                if k == "buy":
                    p = price(et) or 0.0
                    cost[a] += v * p
                    if a not in bought:
                        bought.add(a)
                        firsts.append(et)
                    trades.append((et, "buy", a, v))
                first.setdefault(a, et)
                bal[a] += v
            else:
                if bal[a] > 0:
                    cost[a] *= max(0.0, bal[a] - v) / bal[a]
                bal[a] -= v
                if k == "sell":
                    trades.append((et, "sell", a, v))
            now = bal[a] > dust
            holders += now - was
        while trades and trades[0][0] < t - 6 * H:
            trades.popleft()
        while firsts and firsts[0] < t - 48 * H:
            firsts.popleft()
        hist_holders.append((t, holders))
        p = price(t)
        if not p:
            continue
        big = [(a, b) for a, b in bal.items() if b > S * 1e-4]
        profit3 = sum(b for a, b in big if cost[a] <= 0 or p * b / cost[a] >= 3) / S * 100
        free = sum(b for a, b in big if a not in bought) / S * 100
        early = sum(b for a, b in big if first.get(a, 1e18) <= t_buy + H) / S * 100
        b2 = {a for et, kk, a, v in trades if kk == "buy" and et >= t - 2 * H}
        s2 = {a for et, kk, a, v in trades if kk == "sell" and et >= t - 2 * H}
        n6 = sum(1 for x in firsts if x >= t - 6 * H)
        avg6 = len(firsts) / 8 if t - t_buy >= 48 * H else None
        p6 = price(t - 6 * H)
        r_now, r6 = reserve(t), reserve(t - 6 * H)
        liq_chg6 = (r_now * math.sqrt(p) / (r6 * math.sqrt(p6)) - 1) if p6 and r6 > 0 and r_now > 0 else None
        j = bisect.bisect_left([x[0] for x in hist_holders[-200:]], t - 6 * H)
        h6 = hist_holders[-200:][j][1] if hist_holders[-200:] and j < len(hist_holders[-200:]) else None
        buys4 = collections.Counter()
        for et, kk, a, v in trades:
            if kk == "buy" and et >= t - 4 * H:
                buys4[a] += v
        tot4 = sum(buys4.values())
        out[t] = {
            "profit3": profit3, "free": free, "early": early,
            "buyers2": len(b2), "sellers2": len(s2),
            "buy_ratio2": len(b2) / (len(b2) + len(s2)) if b2 or s2 else None,
            "new6": n6 / avg6 if avg6 else None,
            "liq_chg6": liq_chg6, "liq_share": r_now / S * 100,
            "holders6": (holders / h6 - 1) if h6 else None,
            "top_buy4": max(buys4.values()) / tot4 if tot4 else None,
        }
    return out
