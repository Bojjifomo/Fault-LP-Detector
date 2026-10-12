"""
Support teknikal + likuiditas: di level harga mana jualan kemungkinan besar tertahan.

Dua sumber, digabung jadi zona support di bawah harga sekarang:
1. Likuiditas LP sungguhan (peta tick Uniswap v4 dari fetch.liquidity_book): berapa USD yang dibayar
   pool ke penjual untuk menurunkan harga ke tiap level, dijumlah dari semua pool USDG & ETH token itu
   (pool-pool ini terhubung lewat arbitrase). Pita dengan likuiditas tebal = "tembok" pembeli.
2. Price action: swing low yang pernah memantul (makin sering & makin tinggi pantulannya makin kuat)
   dan volume profile (harga tempat banyak transaksi terjadi).
"""
import bisect
import math

from .signals import INFLOW

GRID = 95            # kurva kedalaman per 1% penurunan, 0..-95%
ZONE = 0.04          # lebar zona support ±4% (skala log)
SWING_SCALES = (2, 8)  # jam: swing low = harga terendah dalam ±2 jam / ±8 jam
MIN_BOUNCE = 0.10    # pantulan minimal 10% dalam 6 jam supaya dihitung support
LOOKBACK_D = 7       # hari riwayat untuk swing low & volume profile
H = 3600


def depth_curve(pool, dec_token):
    """Kurva kedalaman jual satu pool: usd[d] = USD yang dibayar pool ke penjual dan tok[d] = token
    yang harus dijual untuk menurunkan harga token d% (d = 0..GRID). Juga menandai kalau rekonstruksi
    likuiditas tidak konsisten (likuiditas negatif)."""
    s0 = pool["sqrt"] / 2 ** 96
    L, c1 = pool["liquidity"], pool["token_is_c1"]
    nets = dict((t, n) for t, n in pool["ticks"])
    qdec, k = pool["quote_decimals"], pool["usd_per_quote"] or 0.0
    cur = pool["tick"]
    # urutan kejadian searah harga token turun: token c0 -> tick turun, token c1 -> tick naik
    if c1:
        crossings = sorted((t, 1.0001 ** (t / 2)) for t in nets if t > cur)
        level = lambda d: s0 / math.sqrt(1 - d)       # sqrtP saat harga token turun d
    else:
        crossings = sorted(((t, 1.0001 ** (t / 2)) for t in nets if t <= cur), reverse=True)
        level = lambda d: s0 * math.sqrt(1 - d)
    events = [(level(d / 100), "grid", d) for d in range(1, GRID + 1)] + [(sp, "tick", t) for t, sp in crossings]
    events.sort(key=lambda e: e[0], reverse=not c1)   # c1: sqrtP naik; c0: sqrtP turun
    usd, tok = [0.0] * (GRID + 1), [0.0] * (GRID + 1)
    cu = ct = 0.0
    s, bad = s0, False
    for sp, kind, x in events:
        if c1:   # token (c1) masuk, quote (c0) keluar
            q_out, t_in = L * (1 / s - 1 / sp), L * (sp - s)
        else:    # token (c0) masuk, quote (c1) keluar
            q_out, t_in = L * (s - sp), L * (1 / sp - 1 / s)
        cu += q_out / 10 ** qdec * k
        ct += t_in / 10 ** dec_token
        s = sp
        if kind == "grid":
            usd[x], tok[x] = cu, ct
            if x == GRID:
                break
        else:
            L = L - nets[x] if not c1 else L + nets[x]
            if L < 0:
                bad, L = True, 0
    return usd, tok, bad


def book_depth(book, dec_token):
    """Gabungan kurva kedalaman semua pool di peta likuiditas."""
    if not book or not book.get("pools"):
        return None
    total_u, total_t = [0.0] * (GRID + 1), [0.0] * (GRID + 1)
    pools, bad = [], False
    for p in book["pools"]:
        if not p.get("usd_per_quote"):
            continue
        u, t, b = depth_curve(p, dec_token)
        bad |= b
        for d in range(GRID + 1):
            total_u[d] += u[d]
            total_t[d] += t[d]
        pools.append({"pool_id": p["pool_id"], "quote": p["quote"], "fee": p["fee"], "usd": u,
                      "usd30": u[30], "usd50": u[50]})
    band = [0.0] + [total_u[d] - total_u[d - 1] for d in range(1, GRID + 1)]
    return {"usd": total_u, "tok": total_t, "band": band, "pools": pools, "inconsistent": bad,
            "ts": book["ts"]}


def drop_for_tokens(depth, amount):
    """Penurunan harga (%) kalau `amount` token dijual sekaligus ke semua pool (None kalau > -95%)."""
    t = depth["tok"]
    k = bisect.bisect_left(t, amount)
    if k > GRID:
        return None
    if k == 0:
        return 0.0
    a, b = t[k - 1], t[k]
    return (k - 1 + (amount - a) / (b - a)) / 100 if b > a else k / 100


def swing_lows(pt, pv, t_now, scale_h):
    """Swing low = harga terendah dalam ±scale_h jam, diikuti pantulan >= MIN_BOUNCE dalam 6 jam."""
    out = []
    a = bisect.bisect_left(pt, t_now - LOOKBACK_D * 86400)
    w = scale_h * H
    for i in range(a, len(pt)):
        t, p = pt[i], pv[i]
        if t + w / 2 > t_now:  # belum cukup data sesudahnya untuk tahu ini titik balik
            break
        lo, hi = bisect.bisect_left(pt, t - w), bisect.bisect_right(pt, t + w)
        if p > min(pv[lo:hi]) or (i > lo and pv[i - 1] == p):
            continue
        after = pv[i:bisect.bisect_right(pt, t + 6 * H)]
        bounce = max(after) / p - 1
        if bounce >= MIN_BOUNCE:
            out.append((t, p, bounce))
    return out


def resistances(tok, now_t, px_now, max_up=3.0):
    """Swing high 7 hari terakhir di atas harga sekarang (harga tertinggi dalam ±8 jam yang lalu turun >=10%
    dalam 6 jam), digabung per zona ±4%, plus ATH. Urut dari yang terdekat."""
    pt, pv = tok._pt, [p[2] for p in tok.prices]
    k = bisect.bisect_right(pt, now_t)
    pt, pv = pt[:k], pv[:k]
    if not pv:
        return []
    out = []
    a, w = bisect.bisect_left(pt, now_t - LOOKBACK_D * 86400), 8 * H
    for i in range(a, len(pt)):
        t, p = pt[i], pv[i]
        if t + w / 2 > now_t:
            break
        lo, hi = bisect.bisect_left(pt, t - w), bisect.bisect_right(pt, t + w)
        if p < max(pv[lo:hi]) or p <= px_now * 1.03 or p > px_now * (1 + max_up):
            continue
        after = pv[i:bisect.bisect_right(pt, t + 6 * H)]
        if 1 - min(after) / p >= MIN_BOUNCE:
            out.append(p)
    ath = max(pv)
    zones = []
    for p in sorted(out):
        if zones and math.log(p / zones[-1][0]) <= 2 * ZONE:
            zones[-1].append(p)
        else:
            zones.append([p])
    res = [{"price": sorted(z)[len(z) // 2], "up": sorted(z)[len(z) // 2] / px_now - 1, "touches": len(z), "ath": False}
           for z in zones]
    if ath > px_now * 1.03 and not any(abs(math.log(ath / r["price"])) <= 2 * ZONE for r in res):
        res.append({"price": ath, "up": ath / px_now - 1, "touches": 1, "ath": True})
    return sorted(res, key=lambda r: r["price"])


def volume_profile(events, pt, pv, t_now):
    """Volume transaksi (USD) per pita harga log 1%, dari beli & jual LOOKBACK_D hari terakhir."""
    vol = {}
    start = t_now - LOOKBACK_D * 86400
    for t, kind, _, v in events:
        if t < start or kind not in ("buy", "sell") or t > t_now:
            continue
        k = bisect.bisect_right(pt, t) - 1
        if k < 0:
            continue
        p = pv[k]
        b = round(math.log(p) / math.log(1.01))
        vol[b] = vol.get(b, 0.0) + v * p
    return vol


def supports(tok, now_t, px_now, depth=None, max_drop=0.8):
    """Zona support di bawah harga sekarang (sampai -max_drop), dari swing low, volume profile, dan
    likuiditas LP. Tiap zona: level harga, jarak, likuiditas USD di zona, jumlah pantulan, porsi
    volume, tekanan jual yang dibutuhkan untuk sampai ke sana, dan kekuatannya."""
    pt, pv = tok._pt, [p[2] for p in tok.prices]
    k = bisect.bisect_right(pt, now_t)
    pt, pv = pt[:k], pv[:k]
    lows = []
    for sc in SWING_SCALES:
        lows += [(t, p, b, sc) for t, p, b in swing_lows(pt, pv, now_t, sc)]
    vol = volume_profile(tok.events, pt, pv, now_t)
    vol_total = sum(vol.values()) or 1.0

    # kandidat level: swing low, pita likuiditas tebal, pita volume tinggi
    cands = [p for _, p, _, _ in lows if p < px_now]
    if depth:  # tembok likuiditas: pita yang jauh lebih tebal dari sekitarnya
        band = depth["band"]
        for d in range(2, int(max_drop * 100)):
            if band[d] >= max(band[d - 1], band[d + 1]) and local_ratio(depth, d, 1) >= 2:
                cands.append(px_now * (1 - d / 100))
    if vol:
        top = sorted(vol.items(), key=lambda x: -x[1])[:5]
        cands += [1.01 ** b for b, v in top if v >= 0.05 * vol_total and 1.01 ** b < px_now]
    cands = [c for c in cands if px_now * (1 - max_drop) <= c < px_now * 0.99]
    if not cands:
        return []
    # gabungkan kandidat yang berdekatan (±ZONE) jadi zona
    cands.sort(reverse=True)
    groups = [[cands[0]]]
    for c in cands[1:]:
        if math.log(groups[-1][0] / c) <= 2 * ZONE:
            groups[-1].append(c)
        else:
            groups.append([c])
    zones = []
    for g in groups:
        level = sorted(g)[len(g) // 2]
        lo, hi = level * (1 - ZONE), level * (1 + ZONE)
        hits = sorted((t, b) for t, p, b, _ in lows if lo <= p <= hi)
        touches, last_t = [], None
        for t, b in hits:  # pantulan yang berdekatan (< 2 jam) dihitung satu
            if last_t is None or t - last_t >= 2 * H:
                touches.append(b)
            last_t = t
        v = sum(x for b, x in vol.items() if lo <= 1.01 ** b <= hi) / vol_total
        drop = 1 - level / px_now
        z = {"price": level, "drop": -drop, "touches": len(touches), "bounce": max(touches) if touches else 0.0,
             "last_touch": hits[-1][0] if hits else None, "vol_share": v, "liq_usd": None, "sell_usd": None}
        if depth:
            d0, d1 = max(0, int((1 - hi / px_now) * 100)), min(GRID, int(math.ceil((1 - lo / px_now) * 100)))
            z["liq_usd"] = depth["usd"][d1] - depth["usd"][d0]
            z["sell_usd"] = depth["usd"][min(GRID, int(round(drop * 100)))]
        zones.append(z)
    # kekuatan, dikalibrasi di riwayat CRADLE/PULP/JOLLY (support_test): yang paling menentukan adalah
    # seberapa baru zona terakhir memantul (<24 jam bertahan ±35%, 1-3 hari ±23%, lebih lama ±0%) dan
    # besar pantulannya (>=40%); jumlah pantulan & volume hampir tidak berpengaruh. Likuiditas LP tebal
    # (>=1,5x pita sekitarnya) ikut menambah, berdasarkan mekanika pool (belum bisa diuji historis).
    for z in zones:
        age = (now_t - z["last_touch"]) / H if z["last_touch"] else None
        score = (2 if age is not None and age < 24 else 1 if age is not None and age < 72 else 0)
        score += 1 if z["bounce"] >= 0.4 else 0
        if depth:
            z["liq_ratio"] = r = local_ratio(depth, -z["drop"] * 100, ZONE * 100)
            score += 2 if r >= 2.5 else 1 if r >= 1.5 else 0
        z["age_h"], z["score"] = age, score
        z["strength"] = "strong" if score >= 3 else "medium" if score >= 2 else "weak"
    zones.sort(key=lambda z: -z["price"])
    return zones


def local_ratio(depth, d, half, around=15):
    """Ketebalan likuiditas di pita d±half (persen) dibanding pita sekitarnya (±around, di luar zona).
    Pembanding lokal, karena posisi full range secara alami makin tebal per 1% saat harga makin turun."""
    band = depth["band"]
    inner = [band[i] for i in range(max(1, int(d - half)), min(GRID, int(d + half)) + 1)]
    outer = [band[i] for i in range(max(1, int(d - around)), min(GRID, int(d + around)) + 1)
             if abs(i - d) > half]
    if not inner or not outer or not sum(outer):
        return 0.0
    return (sum(inner) / len(inner)) / (sum(outer) / len(outer))


def holes(depth, max_drop=0.8, min_width=10):
    """Rentang penurunan (>= min_width %) dengan likuiditas sangat tipis: kalau harga masuk ke sini, bisa
    meluncur cepat. Tipis = pita < 25% rata-rata pita."""
    if not depth:
        return []
    span = int(max_drop * 100)
    avg = depth["usd"][span] / span if depth["usd"][span] else 0
    out, start = [], None
    for d in range(1, span + 1):
        thin = depth["band"][d] < 0.25 * avg
        if thin and start is None:
            start = d
        if (not thin or d == span) and start is not None:
            end = d if thin else d - 1
            if end - start + 1 >= min_width:
                out.append((-start / 100, -end / 100))
            start = None
    return out


def whale_impact(tok, now_t, depth):
    """Penurunan harga kalau wallet terbesar / top-10 wallet (EOA) menjual semua sekaligus."""
    if not depth:
        return None
    bal = {}
    for t, kind, a, v in tok.events:
        if t > now_t:
            break
        bal[a] = bal.get(a, 0.0) + (v if kind in INFLOW else -v)
    top = sorted((v for v in bal.values() if v > 0), reverse=True)
    if not top:
        return None
    return {"top1": top[0], "top10": sum(top[:10]),
            "drop1": drop_for_tokens(depth, top[0]), "drop10": drop_for_tokens(depth, sum(top[:10]))}


def support_test(tokens, step_h=2, window_h=24):
    """Uji di riwayat token pembanding: tiap `step_h` jam, zona support dari price action (tanpa peta
    likuiditas, karena likuiditas historis tidak tersimpan) dibandingkan dengan level biasa di -10/-20/
    -30/-40%. Zona yang disentuh dalam `window_h` jam dihitung 'bertahan' kalau 6 jam sesudahnya harga
    tidak tembus >5% di bawah zona dan sempat memantul >=5%."""
    keys = ("zone", "strong", "medium", "weak", "plain")
    st = {k: [0, 0] for k in keys}
    for tok in tokens:
        pt, pv = tok._pt, [p[2] for p in tok.prices]
        if len(pt) < 10:
            continue

        def test(level, t):
            lo, hi = level * (1 - ZONE), level * (1 + ZONE)
            for i in range(bisect.bisect_right(pt, t), bisect.bisect_right(pt, t + window_h * H)):
                if pv[i] <= hi:
                    seg = pv[i:bisect.bisect_right(pt, pt[i] + 6 * H)]
                    return True, min(seg) >= lo * 0.95 and max(seg) >= pv[i] * 1.05
            return False, False

        t = pt[0] + 2 * 86400
        while t < pt[-1] - window_h * H:
            px = pv[bisect.bisect_right(pt, t) - 1]
            zs = [z for z in supports(tok, t, px, None, max_drop=0.6) if z["drop"] <= -0.05]
            for z in zs:
                tested, held = test(z["price"], t)
                if tested:
                    for k in ("zone", z["strength"]):
                        st[k][0] += 1
                        st[k][1] += held
            for d in (0.1, 0.2, 0.3, 0.4):
                lvl = px * (1 - d)
                if not any(abs(math.log(lvl / z["price"])) < 2 * ZONE for z in zs):
                    tested, held = test(lvl, t)
                    if tested:
                        st["plain"][0] += 1
                        st["plain"][1] += held
            t += step_h * H
    return {k: {"n": n, "held": h, "rate": h / n if n else None} for k, (n, h) in st.items()}


def floor_hint(zones, floor):
    """Kaitkan batas bawah range saran (`floor`, persen positif) dengan zona support: zona kuat/sedang
    di atas batas bawah (yang akan menahan duluan), dan alternatif batas bawah tepat di bawah zona
    support yang dekat dengan batas bawah (supaya range masih mencakup zona pantulnya)."""
    good = [z for z in zones if z["strength"] in ("strong", "medium")]
    above = [z for z in good if -z["drop"] * 100 < floor]
    alt = None
    for z in sorted(good, key=lambda z: abs(-z["drop"] * 100 - floor)):
        low = (1 - (1 + z["drop"]) * (1 - ZONE)) * 100  # tepi bawah zona, persen turun
        if floor - 5 <= low <= floor + 10:
            a = min(95, math.ceil(low + 3))
            if a != floor:
                alt = {"floor": a, "zone": z}
            break
    return {"above": above, "alt": alt}
