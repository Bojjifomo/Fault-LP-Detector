"""
Indikator teknikal klasik dari data yang sudah ada (sampel harga 5 menit + transaksi beli/jual),
hanya memakai data sampai waktu t:

- ema_gap     : harga vs EMA20 candle 1 jam (seberapa jauh harga melar dari rata-ratanya)
- ema_trend   : EMA20 vs EMA50 candle 1 jam (tren naik > 0, turun < 0)
- rsi1h       : RSI 14 candle 1 jam (> 70 jenuh beli, < 30 jenuh jual)
- rsi15       : RSI 14 candle 15 menit
- vwap_gap    : harga vs VWAP 24 jam (harga rata-rata tertimbang volume)
- bb_pctb     : posisi harga di Bollinger Band 1 jam (1 = di pita atas, 0 = pita bawah)
- bb_width    : lebar Bollinger Band (volatilitas)
- structure   : struktur swing 1 jam: +1 higher high + higher low, -1 lower high + lower low, 0 campur
- bos_down    : harga sudah tembus di bawah swing low terakhir (break of structure turun)
- vol_ratio2  : volume USD 2 jam terakhir dibanding rata-rata 2 jam dalam 24 jam
- buy_share4  : porsi volume beli dari total volume 4 jam (tekanan beli vs jual)
- bear_div    : harga di dekat high 24 jam tapi RSI 1 jam lebih rendah dari saat high sebelumnya
"""
import bisect
import math

H = 3600
NAMES = ("ema_gap", "ema_trend", "rsi1h", "rsi15", "vwap_gap", "bb_pctb", "bb_width", "structure", "bos_down",
         "vol_ratio2", "buy_share4", "bear_div")


def candles(pt, pv, size, t_end):
    """Close tiap candle `size` detik yang sudah selesai sebelum t_end, plus high/low dari sampel."""
    out = []
    a = 0
    if not pt:
        return out
    start = pt[0] - pt[0] % size
    for c0 in range(start, t_end - size + 1, size):
        b = bisect.bisect_left(pt, c0 + size, a)
        if b > a:
            seg = pv[a:b]
            out.append((c0, seg[-1], max(seg), min(seg)))
        a = b
    return out


def ema(vals, n):
    if not vals:
        return []
    k, e, out = 2 / (n + 1), vals[0], []
    for v in vals:
        e = v * k + e * (1 - k)
        out.append(e)
    return out


def rsi(closes, n=14):
    if len(closes) <= n:
        return None
    gains = [max(0.0, b - a) for a, b in zip(closes, closes[1:])]
    losses = [max(0.0, a - b) for a, b in zip(closes, closes[1:])]
    g, l = sum(gains[:n]) / n, sum(losses[:n]) / n
    for gg, ll in zip(gains[n:], losses[n:]):  # Wilder smoothing
        g, l = (g * (n - 1) + gg) / n, (l * (n - 1) + ll) / n
    return 100.0 if l == 0 else 100 - 100 / (1 + g / l)


def swings(cs, k=2):
    """Swing high/low (fraktal) dari candle: high/low tertinggi/terendah dibanding k candle kiri & kanan."""
    hi, lo = [], []
    for i in range(k, len(cs) - k):
        h, l = cs[i][2], cs[i][3]
        if all(h >= cs[j][2] for j in range(i - k, i + k + 1)):
            hi.append((cs[i][0], h))
        if all(l <= cs[j][3] for j in range(i - k, i + k + 1)):
            lo.append((cs[i][0], l))
    return hi, lo


def tech_features(tok, times):
    """{t: {indikator}} untuk tiap t di `times`."""
    pt, pv = tok._pt, [p[2] for p in tok.prices]
    if len(pt) < 20:
        return {}
    trades = [(t, k, v) for t, k, a, v in tok.events if k in ("buy", "sell")]
    tt = [x[0] for x in trades]
    out = {}
    for t in times:
        k = bisect.bisect_right(pt, t) - 1
        if k < 0:
            continue
        px = pv[k]
        a = bisect.bisect_left(pt, t - 10 * 86400)  # cukup 10 hari terakhir
        c1 = candles(pt[a:k + 1], pv[a:k + 1], H, t)
        c15 = candles(pt[bisect.bisect_left(pt, t - 2 * 86400):k + 1],
                      pv[bisect.bisect_left(pt, t - 2 * 86400):k + 1], 900, t)
        closes = [c[1] for c in c1]
        f = {n: None for n in NAMES}
        if len(closes) >= 20:
            e20, e50 = ema(closes, 20), ema(closes, 50)
            f["ema_gap"] = px / e20[-1] - 1
            f["ema_trend"] = e20[-1] / e50[-1] - 1 if len(closes) >= 50 else None
            win = closes[-20:]
            m = sum(win) / 20
            sd = math.sqrt(sum((x - m) ** 2 for x in win) / 20)
            if sd > 0:
                f["bb_pctb"] = (px - (m - 2 * sd)) / (4 * sd)
                f["bb_width"] = 4 * sd / m
        f["rsi1h"] = rsi(closes[-60:] + [px])
        f["rsi15"] = rsi([c[1] for c in c15][-60:] + [px])
        hi, lo = swings(c1[-72:])
        if len(hi) >= 2 and len(lo) >= 2:
            hh, hl = hi[-1][1] > hi[-2][1], lo[-1][1] > lo[-2][1]
            f["structure"] = 1 if hh and hl else -1 if not hh and not hl else 0
        if lo:
            f["bos_down"] = float(px < lo[-1][1])
        # VWAP & volume dari transaksi (harga = sampel terakhir sebelum transaksi)
        i0, i1 = bisect.bisect_left(tt, t - 24 * H), bisect.bisect_left(tt, t)
        pv_sum = v_sum = 0.0
        buckets = [0.0] * 12
        buy4 = sell4 = 0.0
        for et, kind, v in trades[i0:i1]:
            j = bisect.bisect_right(pt, et) - 1
            if j < 0:
                continue
            usd = v * pv[j]
            pv_sum += usd
            v_sum += v
            buckets[min(11, int((t - et) // (2 * H)))] += usd
            if et >= t - 4 * H:
                if kind == "buy":
                    buy4 += usd
                else:
                    sell4 += usd
        if v_sum:
            f["vwap_gap"] = px / (pv_sum / v_sum) - 1
        if sum(buckets[1:]) > 0:
            f["vol_ratio2"] = buckets[0] / (sum(buckets[1:]) / 11)
        if buy4 + sell4 > 0:
            f["buy_share4"] = buy4 / (buy4 + sell4)
        # divergence turun: harga dekat high 24 jam, RSI lebih rendah dari RSI saat swing high sebelumnya
        if hi and f["rsi1h"] is not None:
            w24 = pv[bisect.bisect_left(pt, t - 24 * H):k + 1]
            prev = [h for h in hi if h[0] < t - 3 * H]
            if prev and px >= 0.97 * max(w24):
                ci = next((i for i, c in enumerate(c1) if c[0] == prev[-1][0]), None)
                r_prev = rsi(closes[max(0, ci - 59):ci + 1]) if ci is not None else None
                f["bear_div"] = float(r_prev is not None and px >= prev[-1][1] and f["rsi1h"] < r_prev - 5)
            else:
                f["bear_div"] = 0.0
        out[t] = f
    return out
