"""
Pola price action "retest ATH lalu gagal lalu rug" (CRADLE 10-04, PULP 10-10).

Struktur: high sebelumnya (H1, dekat ATH) → pullback ≥25% ke low range (L) → harga balik
ke ≤10% dari H1 (boleh sedikit tembus sampai +5%) = retest → ditolak ≥15% dari high retest
= gagal → tembus L = breakdown. Hanya memakai harga sampai titik t.
"""
import bisect

H = 3600
PULLBACK = 0.25     # pullback minimal dari H1 sebelum retest
NEAR = 0.90         # retest = harga >= 90% dari H1
DEVIATION = 1.05    # high retest boleh tembus H1 sampai +5% (lebih dari itu = breakout); CRADLE & PULP +0,5–1%
REJECT = 0.85       # gagal = harga <= 85% dari high retest
ATH_FRAC = 0.80     # H1 harus >= 80% ATH (retest ATH, bukan sembarang high lokal)
RETEST_WINDOW = 6   # jam: high retest dicari di jendela ini
LOOKBACK = 48       # jam: H1 dicari di jendela ini

STATES = {
    "RETEST": "retesting the ATH",
    "GAGAL": "ATH retest failed",
    "BREAKDOWN": "broke range support after an ATH retest",
    "TURUN": "fading after an ATH retest",
}


def retest_state(pt, pv, t, ath=None):
    """Status pola di waktu t dari sampel harga (pt = waktu, pv = harga), atau None.
    `ath` = harga tertinggi s/d t kalau sudah dihitung (hemat waktu di loop)."""
    k = bisect.bisect_right(pt, t) - 1
    if k < 0:
        return None
    p = pv[k]
    a = bisect.bisect_left(pt, t - LOOKBACK * H)
    r0 = bisect.bisect_left(pt, t - RETEST_WINDOW * H)
    if r0 >= k:
        return None
    ir = max(range(r0, k + 1), key=lambda i: pv[i])
    e1 = bisect.bisect_left(pt, pt[ir] - H)  # H1 minimal 1 jam sebelum high retest
    if e1 <= a:
        return None
    i1 = max(range(a, e1), key=lambda i: pv[i])
    if i1 + 1 >= ir:
        return None
    il = min(range(i1 + 1, ir), key=lambda i: pv[i])
    h1, hr, low = pv[i1], pv[ir], pv[il]
    if 1 - low / h1 < PULLBACK or hr < NEAR * h1 or hr > DEVIATION * h1 or h1 < ATH_FRAC * (ath or max(pv[:k + 1])):
        return None
    if p <= low:
        state = "BREAKDOWN"
    elif p <= REJECT * hr:
        state = "GAGAL"
    elif p >= NEAR * h1:
        state = "RETEST"
    else:
        state = "TURUN"
    return {"state": state, "h1": h1, "h1_t": pt[i1], "low": low, "low_t": pt[il],
            "retest": hr, "retest_t": pt[ir]}
