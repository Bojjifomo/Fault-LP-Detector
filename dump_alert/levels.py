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
UP_GRID = 200        # sisi atas: per 1% kenaikan, 0..+200%
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


def depth_curve_up(pool, dec_token, grid=UP_GRID):
    """Sisi atas: usd[u] = USD yang harus dibayar pembeli ke pool untuk menaikkan harga token u% (u = 0..grid),
    dari likuiditas LP di atas harga sekarang (posisi yang memegang token). None kalau peta tidak mencakup sisi atas."""
    if "up" not in pool:
        return None
    grid = min(grid, int(pool["up"] * 100))
    s0 = pool["sqrt"] / 2 ** 96
    L, c1 = pool["liquidity"], pool["token_is_c1"]
    nets = dict((t, n) for t, n in pool["ticks"])
    qdec, k = pool["quote_decimals"], pool["usd_per_quote"] or 0.0
    cur = pool["tick"]
    # harga token naik: token c1 -> sqrtP turun (tick turun); token c0 -> sqrtP naik (tick naik)
    if c1:
        crossings = sorted(((t, 1.0001 ** (t / 2)) for t in nets if t <= cur), reverse=True)
        level = lambda u: s0 / math.sqrt(1 + u)
    else:
        crossings = sorted((t, 1.0001 ** (t / 2)) for t in nets if t > cur)
        level = lambda u: s0 * math.sqrt(1 + u)
    events = [(level(u / 100), "grid", u) for u in range(1, grid + 1)] + [(sp, "tick", t) for t, sp in crossings]
    events.sort(key=lambda e: e[0], reverse=c1)
    usd = [0.0] * (grid + 1)
    cu, s = 0.0, s0
    for sp, kind, x in events:
        q_in = L * (1 / sp - 1 / s) if c1 else L * (sp - s)
        cu += q_in / 10 ** qdec * k
        s = sp
        if kind == "grid":
            usd[x] = cu
            if x == grid:
                break
        else:
            L = max(0, L - nets[x] if c1 else L + nets[x])
    return usd


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
    ups = [depth_curve_up(p, dec_token) for p in book["pools"] if p.get("usd_per_quote")]
    up = None
    if ups and all(u is not None for u in ups):
        n = min(len(u) for u in ups)
        up = [sum(u[i] for u in ups) for i in range(n)]
    return {"usd": total_u, "tok": total_t, "band": band, "pools": pools, "inconsistent": bad,
            "ts": book["ts"], "up_usd": up, "up_band": [0.0] + [up[i] - up[i - 1] for i in range(1, len(up))] if up else None}


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


def swing_highs(pt, pv, t_now, scale_h):
    """Swing high = harga tertinggi dalam ±scale_h jam, diikuti penurunan >= MIN_BOUNCE dalam 6 jam (ditolak)."""
    out = []
    a = bisect.bisect_left(pt, t_now - LOOKBACK_D * 86400)
    w = scale_h * H
    for i in range(a, len(pt)):
        t, p = pt[i], pv[i]
        if t + w / 2 > t_now:
            break
        lo, hi = bisect.bisect_left(pt, t - w), bisect.bisect_right(pt, t + w)
        if p < max(pv[lo:hi]) or (i > lo and pv[i - 1] == p):
            continue
        after = pv[i:bisect.bisect_right(pt, t + 6 * H)]
        drop = 1 - min(after) / p
        if drop >= MIN_BOUNCE:
            out.append((t, p, drop))
    return out


def resistance_zones(tok, now_t, px_now, depth=None, cb=None, max_up=3.0):
    """Zona resistance di atas harga sekarang (sampai +max_up), dari: swing high 7 hari yang menolak harga >= 10%, ATH,
    tembok jual LP (pita likuiditas sisi atas yang jauh lebih tebal dari sekitarnya), harga dengan volume tinggi, dan
    kumpulan holder yang sedang rugi (harga beli rata-rata mereka di zona itu: cenderung menjual saat balik modal).
    cb = (harga beli rata-rata, saldo) wallet dari holders.cost_basis."""
    pt, pv = tok._pt, [p[2] for p in tok.prices]
    k = bisect.bisect_right(pt, now_t)
    pt, pv = pt[:k], pv[:k]
    if not pv:
        return []
    S, top = tok.supply, px_now * (1 + max_up)
    highs = []
    for sc in SWING_SCALES:
        highs += [(t, p, d) for t, p, d in swing_highs(pt, pv, now_t, sc)]
    ath = max(pv)
    cands = [p for _, p, _ in highs]
    if ath > px_now * 1.01:
        cands.append(ath)
    vol = volume_profile(tok.events, pt, pv, now_t)
    vol_total = sum(vol.values()) or 1.0
    cands += [1.01 ** b for b, v in sorted(vol.items(), key=lambda x: -x[1])[:5] if v >= 0.05 * vol_total]
    up = (depth or {}).get("up_band")
    if up:
        for u in range(2, min(len(up) - 1, int(max_up * 100))):
            if up[u] >= max(up[u - 1], up[u + 1]) and local_ratio(depth, u, 1, key="up_band") >= 2:
                cands.append(px_now * (1 + u / 100))
    under = None
    if cb is not None and len(cb[0]):
        m = cb[0] > px_now
        under = (cb[0][m], cb[1][m])
        if len(under[0]):
            bins = {}
            for e, b in zip(*under):
                key = round(math.log(e) / math.log(1.02))
                bins[key] = bins.get(key, 0.0) + b
            cands += [1.02 ** key for key, b in bins.items() if b >= 0.01 * S]
    cands = sorted(c for c in cands if px_now * 1.01 < c <= top)
    if not cands:
        return []
    groups = [[cands[0]]]
    for c in cands[1:]:
        if math.log(c / groups[-1][0]) <= 2 * ZONE:
            groups[-1].append(c)
        else:
            groups.append([c])
    zones = []
    for g in groups:
        level = sorted(g)[len(g) // 2]
        lo, hi = level * (1 - ZONE), level * (1 + ZONE)
        hits = sorted((t, d) for t, p, d in highs if lo <= p <= hi)
        touches, last_t = [], None
        for t, d in hits:
            if last_t is None or t - last_t >= 2 * H:
                touches.append(d)
            last_t = t
        z = {"price": level, "up": level / px_now - 1, "touches": len(touches),
             "reject": max(touches) if touches else 0.0, "last_touch": hits[-1][0] if hits else None,
             "ath": lo <= ath <= hi, "vol_share": sum(x for b, x in vol.items() if lo <= 1.01 ** b <= hi) / vol_total,
             "under": float(under[1][(under[0] >= lo) & (under[0] <= hi)].sum() / S) if under is not None else None,
             "wall": None, "buy_usd": None, "wall_ratio": None}
        u_mid = int(round(z["up"] * 100))
        if up and u_mid < len(up) - 1:
            u0, u1 = max(0, int((lo / px_now - 1) * 100)), min(len(up) - 1, int(math.ceil((hi / px_now - 1) * 100)))
            z["wall"] = depth["up_usd"][u1] - depth["up_usd"][u0]
            z["buy_usd"] = depth["up_usd"][min(len(up) - 1, max(0, u_mid))]
            z["wall_ratio"] = local_ratio(depth, u_mid, ZONE * 100, key="up_band")
        z["age_h"] = (now_t - z["last_touch"]) / H if z["last_touch"] else None
        zones.append(z)
    rate_resistances(zones)
    return zones


RES_UNDER = 0.03   # >= 3% supply dipegang holder yang rugi dengan harga beli di zona ini
TIERS = ("weak", "medium", "strong")


def res_tier(touches, under=None, wall_ratio=None):
    """Kekuatan zona resistance, dikalibrasi di riwayat 24 koin (resistance_test): 3+ penolakan 7 hari = kuat, 1-2 =
    sedang, tanpa penolakan = lemah; >= 3% supply dipegang holder yang rugi di zona itu naik satu tingkat (terbukti:
    menolak ±41% vs ±33% tanpa). Tembok jual LP >= 2,5x sekitarnya juga naik satu tingkat (mekanika pool; likuiditas
    historis tidak tersimpan, belum bisa diuji). ATH sendiri tidak dinaikkan (di koin sampel ATH justru sering tembus)."""
    tier = 2 if touches >= 3 else 1 if touches >= 1 else 0
    if (under or 0) >= RES_UNDER or (wall_ratio or 0) >= 2.5:
        tier = min(2, tier + 1)
    return tier


def rate_resistances(zones):
    for z in zones:
        z["score"] = res_tier(z["touches"], z.get("under"), z.get("wall_ratio"))
        z["strength"] = TIERS[z["score"]]


def momentum(x):
    """Harga datang dengan momentum: RSI 1 jam >= 70 atau volume 2 jam >= 2x normal. Di uji resistance, zona sedang &
    lemah jauh lebih sering tembus saat momentum (±16-27% menolak vs ±40% saat tenang); zona kuat tetap ±42%."""
    return (x.get("rsi1h") or 0) >= 70 or (x.get("vol_ratio2") or 0) >= 2


RES_SIGNALS = {
    "rsi70": lambda r: (r.get("rsi1h") or 0) >= 70,
    "volspike": lambda r: (r.get("vol_ratio2") or 0) >= 2,
    "bb": lambda r: (r.get("bb_pctb") or 0) >= 1.0,
    "stretch": lambda r: (r.get("ema_gap") or 0) >= 0.15,
    "sellers": lambda r: r.get("buy_share4") is not None and r["buy_share4"] < 0.5,
    "lower_highs": lambda r: (r.get("structure") or 0) == -1,
    "div": lambda r: (r.get("bear_div") or 0) >= 1,
}


def summarize_resistance_test(rows):
    """Ringkasan resistance_test untuk reference.json: peluang menolak per kekuatan x momentum, level biasa, dan efek
    tiap sinyal teknikal (menolak saat sinyal menyala vs mati)."""
    def stat(rs):
        n = len(rs)
        drops = sorted(r["drop"] for r in rs if r["rejected"])
        return {"n": n, "rate": sum(r["rejected"] for r in rs) / n if n else None,
                "drop": drops[len(drops) // 2] if drops else None}
    zones = [r for r in rows if r["kind"] == "zone"]
    out = {"zones": {}, "signals": {}}
    for i, name in enumerate(TIERS):
        rs = [r for r in zones if res_tier(r["touches"], r.get("under")) == i]
        out["zones"][name] = {"all": stat(rs), "calm": stat([r for r in rs if not momentum(r)]),
                              "momentum": stat([r for r in rs if momentum(r)])}
    plain = [r for r in rows if r["kind"] == "plain"]
    out["zones"]["plain"] = {"all": stat(plain), "calm": stat([r for r in plain if not momentum(r)]),
                             "momentum": stat([r for r in plain if momentum(r)])}
    for code, fn in RES_SIGNALS.items():
        on, off = [r for r in rows if fn(r)], [r for r in rows if not fn(r)]
        out["signals"][code] = {"on": stat(on), "off": stat(off)}
    return out


def resistance_test(tokens, step_h=2, window_h=24, signals=True):
    """Uji di riwayat koin sampel: tiap `step_h` jam, zona resistance (price action + holder rugi, tanpa peta likuiditas
    karena likuiditas historis tidak tersimpan) dibandingkan dengan level biasa +10/+20/+30/+40%. Zona yang disentuh
    dalam `window_h` jam dihitung 'menolak' kalau 6 jam sesudahnya harga tidak tembus >5% di atas zona dan sempat turun
    >=5% dari harga sentuh. Juga mencatat sinyal teknikal saat disentuh untuk uji sinyal exit."""
    from .holders import cost_basis
    from .technicals import tech_features
    rows = []
    for tok in tokens:
        pt, pv = tok._pt, [p[2] for p in tok.prices]
        if len(pt) < 10:
            continue

        def test(level, t):
            lo, hi = level * (1 - ZONE), level * (1 + ZONE)
            for i in range(bisect.bisect_right(pt, t), bisect.bisect_right(pt, t + window_h * H)):
                if pv[i] >= lo:
                    seg = pv[i:bisect.bisect_right(pt, pt[i] + 6 * H)]
                    return pt[i], max(seg) <= hi * 1.05 and min(seg) <= pv[i] * 0.95, 1 - min(seg) / pv[i]
            return None, False, 0.0

        times = list(range(pt[0] + 2 * 86400, pt[-1] - window_h * H, step_h * H))
        cbs = cost_basis(tok, times)
        tok_rows = []
        for t in times:
            px = pv[bisect.bisect_right(pt, t) - 1]
            zs = [z for z in resistance_zones(tok, t, px, None, cbs[t], max_up=0.6) if z["up"] >= 0.05]
            for z in zs:
                tt, rej, drop = test(z["price"], t)
                if tt is not None:
                    tok_rows.append({"tok": tok.symbol, "kind": "zone", "strength": z["strength"], "touches": z["touches"],
                                     "ath": z["ath"], "under": z["under"] or 0.0, "vol_share": z["vol_share"],
                                     "t": tt, "rejected": rej, "drop": drop})
            for d in (0.1, 0.2, 0.3, 0.4):
                lvl = px * (1 + d)
                if not any(abs(math.log(lvl / z["price"])) < 2 * ZONE for z in zs):
                    tt, rej, drop = test(lvl, t)
                    if tt is not None:
                        tok_rows.append({"tok": tok.symbol, "kind": "plain", "t": tt, "rejected": rej, "drop": drop})
        if signals and tok_rows:
            tf = tech_features(tok, sorted({r["t"] for r in tok_rows}))
            for r in tok_rows:
                r.update(tf.get(r["t"]) or {})
        rows += tok_rows
    return rows


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


def volume_bands(tok, now_t, px_now, band=2, up=200, down=80):
    """Volume transaksi LOOKBACK_D hari terakhir per pita `band`% relatif ke harga sekarang (dalam aset quote):
    {batas bawah pita dalam persen: volume}. Untuk profil volume (VPVR) di samping peta likuiditas."""
    pt, pv = tok._pt, [p[2] for p in tok.prices]
    k = bisect.bisect_right(pt, now_t)
    out = {}
    for b, v in volume_profile(tok.events, pt[:k], pv[:k], now_t).items():
        d = (1.01 ** b / px_now - 1) * 100
        if -down <= d < up:
            key = int(math.floor(d / band) * band)
            out[key] = out.get(key, 0.0) + v
    return out


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
    # kekuatan, dikalibrasi di riwayat 24 koin sampel (support_test, zona disentuh dalam 24 jam): yang membedakan
    # adalah jumlah pantulan 7 hari (3+ bertahan ±42%, 1-2 ±36%, zona tanpa pantulan ±19%; level biasa ±32%).
    # Seberapa baru & besar pantulan tidak konsisten. Likuiditas LP tebal (>= 2,5x pita sekitarnya) ikut
    # menaikkan satu tingkat, berdasarkan mekanika pool (likuiditas historis tidak tersimpan, belum bisa diuji).
    for z in zones:
        age = (now_t - z["last_touch"]) / H if z["last_touch"] else None
        tier = 2 if z["touches"] >= 3 else 1 if z["touches"] >= 1 else 0
        if depth:
            z["liq_ratio"] = r = local_ratio(depth, -z["drop"] * 100, ZONE * 100)
            tier = min(2, tier + (1 if r >= 2.5 else 0))
        z["age_h"], z["score"] = age, tier
        z["strength"] = ("weak", "medium", "strong")[tier]
    zones.sort(key=lambda z: -z["price"])
    return zones


def local_ratio(depth, d, half, around=15, key="band"):
    """Ketebalan likuiditas di pita d±half (persen) dibanding pita sekitarnya (±around, di luar zona).
    Pembanding lokal, karena posisi full range secara alami makin tebal per 1% saat harga makin turun.
    key="up_band" untuk sisi atas harga."""
    band = depth[key]
    n = len(band) - 1
    inner = [band[i] for i in range(max(1, int(d - half)), min(n, int(d + half)) + 1)]
    outer = [band[i] for i in range(max(1, int(d - around)), min(n, int(d + around)) + 1)
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
