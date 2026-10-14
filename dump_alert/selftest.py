"""
Uji logika dump_alert tanpa jaringan dengan dataset sintetis:
sniper di detik pertama, claim ke banyak wallet, whale masuk lewat router, retail naik,
pump terakhir yang dipakai whale untuk keluar, lalu harga jatuh.

  python -m dump_alert.selftest
"""
import json

from lp_analysis.chain import RPCError, v4_state_slot

from . import levels
from .fetch import Q96, Fetcher, PacedRPC, QuotaError, parse_keys, sqrt_to_price
from .lp_check import drop_bin, evaluate, features, hist_quantile, lp_value, signs_of, truncate
from .patterns import retest_state
from .signals import Token, find_dumps, launch_risk, net_events, timeline

U = 10 ** 18
ZERO = "0x" + "0" * 40
POOL, ROUTER, VAULT, DEPLOYER = "0x" + "a" * 40, "0x" + "b" * 40, "0x" + "c" * 40, "0x" + "d" * 40
W1, W2, S1, S2 = "0x" + "1" * 40, "0x" + "2" * 40, "0x" + "3" * 40, "0x" + "4" * 40
T0 = 1_760_000_000 - 1_760_000_000 % 300
H = 3600


def build():
    T, n = [], [0]

    def tx(t, *legs):
        n[0] += 1
        for f, to, amt in legs:
            T.append([n[0], t, f"0x{n[0]:064x}", f, to, int(amt * U)])

    tx(T0 - 60, (ZERO, DEPLOYER, 1e9))
    tx(T0, (DEPLOYER, POOL, 150e6))
    tx(T0, (DEPLOYER, VAULT, 850e6))
    tx(T0 + 1, (POOL, S1, 50e6))
    tx(T0 + 2, (POOL, S2, 40e6))
    for i in range(100):
        tx(T0 + 60 + i, (VAULT, "0x9" + f"{i:039x}", 1e6))
    tx(T0 + 2 * H, (POOL, ROUTER, 10e6), (ROUTER, W1, 10e6))  # beli lewat router
    tx(T0 + 2 * H + 30, (POOL, W2, 10e6))
    for k in range(int(30 * H / 300)):  # retail beli tiap 5 menit, makin ramai saat pump (jam 20-20.5)
        t = T0 + 3 * H + k * 300
        burst = 6 if T0 + 20 * H <= t < T0 + 20.5 * H else 1
        for j in range(burst):
            tx(t + j, (POOL, "0x8" + f"{k * 10 + j:039x}", 0.05e6))
    tx(T0 + 20 * H + 1500, (W1, ROUTER, 10e6), (ROUTER, POOL, 10e6))  # whale keluar lewat router
    tx(T0 + 20 * H + 1600, (W2, POOL, 10e6))
    T.sort(key=lambda x: (x[1], x[0]))  # urut waktu seperti data on-chain
    prices = []
    for t in range(T0 + 300, T0 + 33 * H, 300):
        h = (t - T0) / H
        if h < 20:
            p = 1e-7 + 1e-8 * h
        elif h < 20.5:
            p = 3e-7 * (1 + (h - 20))      # pump +50% dalam 30 menit
        else:
            p = max(0.8e-7, 4.5e-7 - 1.2e-7 * (h - 20.5))
        prices.append([t, 0, p])
    return {"token": "0x" + "e" * 40, "symbol": "TEST", "name": "Test", "decimals": 18, "supply": 1e9,
            "transfers": T, "prices": prices, "markets": [POOL],
            "contracts": {POOL: True, ROUTER: True, VAULT: True},
            "market": {"quote_symbol": "ETH"}}


class FakeHTTP:
    """Server Alchemy palsu: key A kuota bulanan habis, key B sekali kena batas per detik lalu lancar."""

    class R:
        def __init__(self, code, body):
            self.status_code, self.text = code, json.dumps(body)

        def json(self):
            return json.loads(self.text)

    def __init__(self):
        self.hits = {"A": 0, "B": 0}

    def post(self, url, json=None, timeout=None):
        key = url[-1]
        self.hits[key] += 1
        if key == "A":
            return self.R(429, {"error": {"code": 429, "message": "Monthly capacity limit exceeded."}})
        if self.hits[key] == 1:
            return self.R(429, {"error": {"code": 429, "message": "Your app has exceeded its compute units per "
                                                                 "second capacity."}})
        return self.R(200, {"jsonrpc": "2.0", "id": 1, "result": "0x10"})


class FakeRPC:
    """Storage pool v4 palsu: harga hanya berubah di blok yang ada swap-nya; di blok `empty` pool tanpa
    likuiditas dan harganya sampah (sqrtPrice minimal = harga raksasa)."""

    def __init__(self, swaps, pool_id, fail_at=None, empty=()):
        self.swaps, self.fail_at, self.empty, self.reads, self.liq_checks = swaps, fail_at, set(empty), [], []
        self.liq_slot = hex(v4_state_slot(pool_id) + 3)

    def word(self, slot, b):
        if slot == self.liq_slot:
            return hex(0 if b in self.empty else 10 ** 18)
        if b in self.empty:
            return hex(1)
        return hex(int((1 + sum(1 for s in self.swaps if s <= b)) ** -0.5 * Q96))

    def batch(self, calls):
        out = []
        for _, (_, slot, blk) in calls:
            b = int(blk, 16)
            if slot == self.liq_slot:
                self.liq_checks.append(b)
            else:
                self.reads.append(b)
            out.append(RPCError("rate limited") if b == self.fail_at else self.word(slot, b))
        return out


def prices_case(fail_at=None, swaps=(125, 160), empty=()):
    pm, pid = "0x" + "f" * 40, "0x" + "ab" * 32
    f = Fetcher.__new__(Fetcher)
    f.log, f.checkpoint = (lambda *a: None), (lambda *a: None)
    f.rpc = FakeRPC(swaps, pid, fail_at, empty)
    f.ds = {"decimals": 18, "markets": [pm], "prices": [],
            "transfers": [[b, b, "0x", pm, "0x" + "1" * 40, 1] for b in swaps],
            "market": {"kind": "v4", "manager": pm, "pool_id": pid, "token_is_c1": True,
                       "quote_decimals": 18, "bridge": None}}
    samples = [(b, b) for b in range(100, 201, 10)]
    try:
        f._read_prices(samples)
    except RPCError:
        assert fail_at, "tidak boleh gagal tanpa panggilan yang ditolak"
    return f


def levels_case():
    """Kurva kedalaman vs rumus pasti Uniswap, zona support dari swing low, saran batas bawah teknikal."""
    import math as m_
    L = 10 ** 18
    # token c0 (USDG c1), harga 1 USDG/token: P mentah = 1e6/1e18 -> sqrtP = 1e-6; full range (tanpa tick di jendela)
    full0 = {"sqrt": int(1e-6 * Q96), "tick": m_.floor(m_.log(1e-12) / m_.log(1.0001)), "liquidity": L, "ticks": [],
             "token_is_c1": False, "quote_decimals": 6, "usd_per_quote": 1.0, "pool_id": "0x01", "quote": "USDG",
             "fee": 10000}
    u, t, bad = levels.depth_curve(full0, 18)
    assert not bad and abs(u[50] / 292893.2 - 1) < 1e-4 and abs(t[50] / 414213.6 - 1) < 1e-4, (u[50], t[50])
    # orientasi sebaliknya (token c1, USDG c0): hasil harus sama
    full1 = {**full0, "sqrt": int(1e6 * Q96), "tick": m_.floor(m_.log(1e12) / m_.log(1.0001)), "token_is_c1": True}
    u1, t1, _ = levels.depth_curve(full1, 18)
    assert abs(u1[50] / u[50] - 1) < 1e-3 and abs(t1[50] / t[50] - 1) < 1e-3, (u1[50], u[50])
    # posisi terkonsentrasi sampai -30%: di bawah itu tidak ada likuiditas, kurva datar
    ta = full0["tick"] + m_.floor(m_.log(0.7) / m_.log(1.0001))
    conc = {**full0, "ticks": [[ta, L]]}
    uc, _, bad = levels.depth_curve(conc, 18)
    assert not bad and uc[31] == uc[60] and abs(uc[30] / u[30] - 1) < 1e-3, (uc[29], uc[30], uc[31], uc[60])
    dep = levels.book_depth({"ts": 0, "pools": [conc, full0]}, 18)
    assert abs(dep["usd"][20] - (uc[20] + u[20])) < 1e-6
    assert abs(levels.drop_for_tokens({"tok": t}, t[50]) - 0.5) < 1e-9
    assert levels.holes(levels.book_depth({"ts": 0, "pools": [conc]}, 18), max_drop=0.8)[0][0] == -0.31
    # swing low: tiga kali turun ke 0,8 lalu memantul ke 1,0; sekarang 1,1
    pt, pv = [], []
    for i in range(3 * 288):
        h = i / 12
        cyc = h % 20
        pv.append(0.8 + 0.2 * abs(cyc - 10) / 10 if h < 60 else 1.1)
        pt.append(T0 + i * 300)

    class Tok:
        _pt, prices, events = pt, [[a, 0, b] for a, b in zip(pt, pv)], []
    zs = levels.supports(Tok, pt[-1], pv[-1])
    z = min(zs, key=lambda z: abs(z["price"] - 0.8))
    assert abs(z["price"] - 0.8) < 0.02 and z["touches"] >= 2 and z["bounce"] >= 0.1, zs
    hint = levels.floor_hint([{**z, "strength": "strong"}], 30)
    assert hint["above"] and hint["alt"]["floor"] == 34, hint
    # resistance: tiga kali naik ke 1,0 lalu ditolak ke 0,8; sekarang 0,8 -> zona di ±1,0 dengan >= 2 penolakan;
    # holder rugi yang beli di 1,0 (5% supply) menaikkan kekuatan
    pv2 = [1.0 - 0.2 * abs((i / 12) % 20 - 10) / 10 if i / 12 < 60 else 0.8 for i in range(3 * 288)]

    class Tok2:
        _pt, prices, events, supply = pt, [[a, 0, b] for a, b in zip(pt, pv2)], [], 1000.0
    import numpy as np
    rz = levels.resistance_zones(Tok2, pt[-1], pv2[-1], cb=(np.array([1.0, 0.5]), np.array([50.0, 30.0])))
    r1 = min(rz, key=lambda r: abs(r["price"] - 1.0))
    assert abs(r1["price"] - 1.0) < 0.02 and r1["touches"] >= 2 and abs(r1["under"] - 0.05) < 1e-9, rz
    # holder rugi 5% di zona itu -> medium; penolakan saja tidak menaikkan tingkat (tidak terbukti di harga pasar)
    assert r1["strength"] == "medium" and levels.res_tier(3) == 0 and levels.res_tier(0, 0.04) == 1
    assert levels.res_tier(0, flips=2) == 1 and levels.res_tier(0, flips=3) == 2 and levels.res_tier(0, 0.04, flips=2) == 2
    assert levels.res_tier(5, flips=1) == 0 and levels.res_tier(0, wall_ratio=3.0) == 1
    assert levels.momentum({"rsi1h": 72}) and levels.momentum({"vol_ratio2": 2.5}) and not levels.momentum({"rsi1h": 60})
    rows_ = [{"kind": "zone", "touches": 0, "flips": 3, "under": 0.0, "rejected": True, "drop": 0.2, "rsi1h": 50},
             {"kind": "zone", "touches": 0, "flips": 3, "under": 0.0, "rejected": False, "drop": 0.0, "rsi1h": 75},
             {"kind": "plain", "rejected": False, "drop": 0.0, "rsi1h": 75}]
    sm = levels.summarize_resistance_test(rows_)
    assert sm["zones"]["strong"]["all"]["rate"] == 0.5 and sm["zones"]["strong"]["calm"]["rate"] == 1.0
    assert sm["signals"]["rsi70"]["on"]["n"] == 2 and sm["signals"]["rsi70"]["on"]["rate"] == 0.0


def ranges_case():
    """Range optimal: rumus nilai/konsentrasi, pilihan range di data sintetis, tingkat fee live, kedalaman sisi atas."""
    import json as js
    import math as m_
    import numpy as np
    from . import ranges as rg
    assert abs(float(rg.lp_values(np.array([0.5]), (20, 20))[0]) - lp_value(-0.5, 20)) < 1e-9
    assert abs(float(rg.lp_values(np.array([1.3]), (35, 70))[0]) - lp_value(0.3, 35, 70)) < 1e-9
    assert abs(float(rg.lp_values(np.array([0.25]), rg.FULL)[0]) - 0.5) < 1e-12
    assert rg.concentration(rg.FULL) == 1 and abs(rg.concentration((20, 20)) - 10.379) < 0.001
    assert rg.usdg_share(rg.FULL) == 0.5 and rg.usdg_share((90, 2)) > 0.95 and rg.usdg_share((5, 200)) < 0.2
    # 3 koin x 60 momen: harga datar & volume rata -> fee saja; range tersempit menang. Tanpa fee & ada yang
    # turun 50% -> tidak ada range yang untung -> skip.
    n, steps = 180, rg.STEPS
    flat = {"tokens": js.dumps([{"symbol": f"C{i}", "address": f"0x{i}", "n": 60} for i in range(3)]),
            "tok": np.repeat(np.arange(3), 60).astype(np.int16), "t": np.arange(n, dtype=np.int64),
            "level": np.zeros(n, np.int8), "vol6": np.ones(n, np.float32), "vol_q": np.array([0.5, 2.0]),
            "P": np.ones((n, steps), np.float16), "W": np.ones((n, steps), np.float16)}
    rec = rg.Ranges(flat).recommend(0, 1.0, 8, 0.02, 0.0, user=(20.0, 20.0))
    assert rec["best"]["range"] == (5, 2) and not rec["skip"] and rec["mine"]["range"] == (20.0, 20.0), rec["best"]
    assert abs(rec["full"]["fees"] - 0.02 / 3) < 1e-3, rec["full"]["fees"]   # 8 jam = 1/3 hari, full range
    diluted = rg.Ranges(flat).recommend(0, 1.0, 8, 0.02, 1.0)
    assert diluted["best"]["fees"] < rec["best"]["fees"] / 5, "posisi besar ikut membagi fee"
    # range aman: 100 jendela, jendela ke-i turun i% dan naik i% -> bertahan 90% butuh -90% / +90% (batas bawah lebih dalam tidak membantu)
    mins, maxs = 1 - np.arange(1, 101) / 100, 1 + np.arange(1, 101) / 100
    fr = dict(rg.frontier([(mins, maxs)], 0.9))
    assert fr[90] == 90 and 89 not in fr and fr[95] == 90, fr
    assert rg.coverage((mins, maxs), (50.0, 100.0)) == (0.5, 0.5) and rg.coverage((mins, maxs), rg.FULL) == (1.0, 0.0)
    rec = rg.Ranges(flat).recommend(0, 1.0, 8, 0.02, 0.0, user=(65.0, 65.0))
    sf = rec["safe"]
    assert sf["feasible"] and sf["pick"]["stay"] == 1.0 and sf["margin"] == 5, sf["pick"]
    assert sf["pick"]["range"] == (sf["optimal"]["range"][0] + 5, sf["optimal"]["range"][1] + 5), (sf["pick"], sf["optimal"])
    assert any(c["tag"] == "usual" and c["range"][0] == 60 for c in sf["rows"])
    # single-sided USDG: batas atas 0% -> 100% USDG saat buka; nilai = lp_value dengan upper 0
    assert abs(rg.usdg_share((65, 0)) - 1) < 1e-12 and rg.label((65.0, 0.0)) == "-65% to 0%"
    assert abs(float(rg.lp_values(np.array([0.5]), (65, 0))[0]) - lp_value(-0.5, 65, 0)) < 1e-9
    assert abs(lp_value(0.4, 65, 0) - 1.0) < 1e-12, "harga naik: tetap 100% USDG, nilai tidak berubah"
    # batas bawah: jendela ke-i turun i% -> 90% jendela tidak pernah di bawah -90% (-89% belum cukup); naik tidak dihitung
    assert rg.lower_frontier([(mins, maxs)], 0.9) == 90 and rg.lower_frontier([(mins * 0 + 0.01, maxs)], 0.9) is None
    rec = rg.Ranges(flat).recommend(0, 1.0, 8, 0.02, 0.0, user=(65.0, 0.0), single=True)
    sf = rec["safe"]
    assert sf["mode"] == "single" and sf["feasible"] and sf["optimal"]["range"] == (1.0, 0.0), sf["optimal"]
    assert sf["pick"]["range"] == (6.0, 0.0) and sf["pick"]["hit_low"] == 0 and sf["pick"]["earn"] == 1.0, sf["pick"]
    assert all(c["range"][1] == 0 for c in sf["rows"] + sf["ladder"]) and len(sf["ladder"]) == len(rg.SINGLE_LADDER)
    assert {60.0, 70.0} <= {c["range"][0] for c in sf["rows"] if c["tag"] == "usual"}
    drop = dict(flat, P=np.where(np.arange(n)[:, None] % 2 == 0, 1.0, 0.5).astype(np.float16).repeat(steps, 1))
    assert rg.Ranges(drop).recommend(0, 1.0, 8, 0.0, 0.0)["skip"]

    class Tk:
        quote, _pt, prices, events = "USDG", [T0, T0 + H], [[T0, 0, 1.0], [T0 + H, 0, 1.0]], []
    m = {"fee": 10000, "pool_id": "0xp", "usdg_pools": [{"pool_id": "0xp", "usdg_side": 5000.0}]}
    fy = rg.fee_yield(Tk, m, T0 + H, 1.0, 1000)
    assert fy["r"] == 0 and fy["tvl"] == 10000 and abs(fy["share"] - 0.1) < 1e-12, fy
    Tk.events = [(T0 + 60, "buy", "0xw", 20000.0)]
    assert abs(rg.fee_yield(Tk, m, T0 + H, 1.0, 1000)["r"] - 0.02) < 1e-12   # 1% x $20K / $10K
    fy = rg.fee_yield(Tk, {**m, "fee": 0x800000}, T0 + H, 1.0, 1000)
    assert fy["assumed"] and abs(fy["r"] - 0.02) < 1e-12, fy
    # kedalaman sisi atas, full range: menaikkan harga 50% butuh L*(sqrt(1.5)-1) USDG, dua orientasi sama
    L = 10 ** 18
    p0 = {"sqrt": int(1e-6 * Q96), "tick": m_.floor(m_.log(1e-12) / m_.log(1.0001)), "liquidity": L, "ticks": [],
          "token_is_c1": False, "quote_decimals": 6, "usd_per_quote": 1.0, "up": 2.0}
    p1 = {**p0, "sqrt": int(1e6 * Q96), "tick": m_.floor(m_.log(1e12) / m_.log(1.0001)), "token_is_c1": True}
    u0, u1 = levels.depth_curve_up(p0, 18), levels.depth_curve_up(p1, 18)
    assert abs(u0[50] / 224744.9 - 1) < 1e-4 and abs(u1[50] / u0[50] - 1) < 1e-3, (u0[50], u1[50])
    assert levels.depth_curve_up({k: v for k, v in p0.items() if k != "up"}, 18) is None


def position_case():
    """Rencana exit posisi terbuka: parse market cap, isi posisi, status range, good exit, batas keluar."""
    import numpy as np
    from .lp_check import lp_split, parse_mcap, position_plan
    assert parse_mcap("8.38M") == 8.38e6 * 1 or abs(parse_mcap("8.38M") - 8.38e6) < 1e-3
    assert abs(parse_mcap("$8,38m") - 8.38e6) < 1e-3 and parse_mcap("850k") == 850e3 and parse_mcap("8,380,000") == 8.38e6
    assert parse_mcap("") is None and parse_mcap("abc") is None and parse_mcap("-5") is None
    for r in (1.2, 1.0, 0.8, 0.5):
        for lo, up in ((65, 0), (50, 60)):
            c, u = lp_split(r, lo, up)
            assert abs(c + u - lp_value(r - 1, lo, up)) < 1e-9 and c >= 0 and u >= 0, (r, lo, up)
    # single-sided -65% dibuka di harga 1,0; sekarang 0,8 (di dalam range). Resistance di +3% (terlalu dekat), +10%
    # (kuat), +18% (lemah) dan +20% (sedang); floor kuat di -25%. Good exit = +10%; sesudahnya langsung batas atas
    # (hanya zona strong yang dipakai: medium/weak tidak lebih sering menahan daripada harga acak).
    def z(up, strength, hold):
        return {"price": 0.8 * (1 + up) / (1 - levels.ZONE), "up": up, "strength": strength, "hold": hold}
    lv = {"res": [z(0.03, "strong", 0.44), z(0.10, "strong", 0.44), z(0.18, "weak", 0.14), z(0.20, "medium", 0.42)],
          "zones": [{"price": 0.6, "drop": -0.25, "strength": "strong"}]}
    rec = {"peer_max": np.linspace(1.0, 1.4, 101), "peer_min": np.linspace(0.6, 1.0, 101)}
    o = position_plan({"px": 0.8}, 1.0, 65, 0, lv, rec, None, False)
    assert o["state"] == "in" and abs(o["value"] - lp_value(-0.2, 65, 0)) < 1e-12 and o["break_even"] == 1.0
    assert o["good"]["kind"] == "resistance" and abs(o["good"]["from_now"] - 0.10) < 1e-9, o["good"]
    assert o["after"]["kind"] == "top" and o["targets"][-1]["kind"] == "top", o["after"]
    assert abs(o["stop"]["price"] - 0.6 * (1 - levels.ZONE) * 0.95) < 1e-12 and o["bottom"]["price"] == 0.35
    # floor yang sering tersentuh (peer_min sering <= level) dilewati -> floor lebih dalam yang jarang
    lv2 = dict(lv, zones=[{"price": 0.78, "drop": -0.025, "strength": "strong"}, {"price": 0.6, "drop": -0.25, "strength": "medium"}])
    o2 = position_plan({"px": 0.8}, 1.0, 65, 0, lv2, rec, None, False)
    assert o2["stop"]["zone"]["price"] == 0.6 and o2["stop"]["reach"] <= 0.25, o2["stop"]
    assert abs(o["targets"][-1]["reach"] - np.mean(rec["peer_max"] >= 1.25)) < 1e-12
    # tanpa resistance strong -> batas atas
    o = position_plan({"px": 0.8}, 1.0, 65, 0, {"res": [z(0.18, "weak", 0.14)], "zones": []}, rec, None, True)
    assert o["good"]["kind"] == "top" and o["stop"] is None
    assert position_plan({"px": 1.3}, 1.0, 65, 0, lv, rec, None, False)["state"] == "above"
    b = position_plan({"px": 0.3}, 1.0, 65, 0, {"res": [], "zones": []}, rec, None, False)
    assert b["state"] == "below" and b["usdg"] == 0 and abs(b["value"] - 0.3 / 0.35 ** 0.5) < 1e-9


def supply_case():
    """Market cap seperti DexScreener: supply dikurangi koin di alamat burn (0x…dead) dan yang di-burn ke 0x0."""
    from .holders import BURN
    from .lp_check import circulating_supply
    from .signals import ZERO

    class Tk:
        supply = 1000.0
        ds = {"decimals": 0, "transfers": [[1, 1, "h", ZERO, "0xa", 1000], [2, 2, "h", "0xa", sorted(BURN)[0], 50],
                                           [3, 3, "h", "0xa", ZERO, 10]]}
    assert abs(circulating_supply(Tk) - 940.0) < 1e-9, circulating_supply(Tk)
    Tk.ds = {"decimals": 0, "transfers": []}   # mint tidak lewat Transfer: pakai totalSupply
    assert circulating_supply(Tk) == 1000.0


def main():
    levels_case()
    ranges_case()
    position_case()
    supply_case()
    # banyak key: key kuota habis dilewati, key yang kena batas per detik dipelankan lalu dipakai lagi
    assert parse_keys("k1, k2\nk3", ["k2", "https://robinhood-mainnet.g.alchemy.com/v2/k4"], None) == ["k1", "k2", "k3", "k4"]
    rpc = PacedRPC(["https://selftest.invalid/v2/SECRETKEYA", "https://selftest.invalid/v2/SECRETKEYB"])
    http = FakeHTTP()
    rpc._session = lambda: http
    assert rpc.call("eth_blockNumber", []) == "0x10" and rpc.cu == 10
    assert [st for _, st, _ in rpc.status()] == ["monthly quota used up", "active"], rpc.status()
    rpc.lanes[1].kill("ditolak (HTTP 403)")
    try:
        rpc.call("eth_blockNumber", [])
        raise AssertionError("semua key mati harus QuotaError")
    except QuotaError as e:
        assert "key #1: monthly quota used up" in str(e) and "SECRETKEY" not in str(e), e
    for ln in rpc.lanes:
        ln.dead_until = 0

    # harga: sampel tanpa swap sejak sampel sebelumnya tidak dibaca, harganya sama dengan hasil baca
    f = prices_case()
    assert f.rpc.reads == [100, 130, 160], f.rpc.reads
    true = lambda b: 1 + sum(1 for s in (125, 160) if s <= b)
    assert [round(p[2], 9) for p in f.ds["prices"]] == [true(b) for b in range(100, 201, 10)], f.ds["prices"]
    # gagal di tengah: yang sudah dibaca tetap tersimpan, berurutan, siap dilanjutkan
    f = prices_case(fail_at=160)
    assert [p[0] for p in f.ds["prices"]] == [100, 110, 120, 130, 140, 150], f.ds["prices"]
    # pool sempat kosong (likuiditas 0, harga sampah 1e50-an): sampel itu tidak dipakai
    f = prices_case(swaps=(125, 160, 175), empty=(180,))
    assert f.rpc.liq_checks == [100, 180], f.rpc.liq_checks
    assert 180 not in [p[0] for p in f.ds["prices"]] and max(p[2] for p in f.ds["prices"]) < 10, f.ds["prices"]

    # harga dari sqrtPriceX96: ETH = currency0, token = currency1, 7e-8 ETH per token
    sq = int((1 / 7e-8) ** 0.5 * Q96)
    assert abs(sqrt_to_price(sq, True, 18, 18) / 7e-8 - 1) < 1e-9
    assert abs(sqrt_to_price(sq, False, 18, 6) / ((sq / Q96) ** 2 * 1e12) - 1) < 1e-9

    ds = build()
    ev = net_events(ds["transfers"], {POOL}, {POOL, ROUTER, VAULT}, U)
    kinds = {(a, k) for _, k, a, _ in ev}
    assert (W1, "buy") in kinds and (W1, "sell") in kinds, "router harus ter-netting jadi buy/sell"
    assert ("0x9" + "0" * 39, "recv") in kinds, "claim dari vault = recv"
    assert (DEPLOYER, "xout") in kinds
    # transaksi likuiditas (ModifyLiquidity): tambah LP bukan jual, tarik LP bukan beli
    lp_add = [[9, T0 + 9 * H, "0xlpadd", W2, POOL, 5 * U]]
    lp_rem = [[9, T0 + 9 * H, "0xlprem", POOL, W2, 5 * U]]
    assert [e[1] for e in net_events(lp_add, {POOL}, set(), U, lp_txs={"0xlpadd"})] == ["lp"]
    assert [e[1] for e in net_events(lp_rem, {POOL}, set(), U, lp_txs={"0xlprem"})] == ["lpout"]
    assert [e[1] for e in net_events(lp_rem, {POOL}, set(), U)] == ["buy"], "tanpa tanda LP tetap terbaca beli"

    tok = Token(ds)
    # holder terbesar & aliran uang: urut dari yang terbesar, penerima claim = 'free', dua whale keluar = -2% supply
    from . import holders
    top = holders.top_holders(tok, tok._pt[-1], 5)
    assert all(a_["share"] >= b_["share"] for a_, b_ in zip(top, top[1:])) and top[-1]["free"], top
    fl = {w["hours"]: w for w in holders.money_flow(tok, tok._pt[-1])}
    assert abs(fl[24]["whale_net"] + 0.02) < 1e-9 and abs(fl[24]["net"] - (fl[24]["buy"] - fl[24]["sell"])) < 1e-12, fl[24]
    assert fl[1]["sellers"] == 0 and fl[24]["sellers"] == 2, fl
    risk = launch_risk(tok)
    assert abs(risk["snipe_60s_pct_pool"] - 60) < 1e-6, risk
    assert abs(risk["recv_24h_pct_supply"] - 10) < 1e-6, risk
    assert any("sniper" in f for f in risk["flags"])

    rows = timeline(tok)
    by_t = {r["t"]: r for r in rows}
    exit_t = T0 + 20 * H + 1800  # baris pertama sesudah kedua whale jual
    r = by_t[exit_t]
    assert abs(r["whale_net"] - 2.0) < 1e-6 and r["whale_sellers"] == 2, r
    assert {"A", "B", "C"} <= set(r["rules"]) and r["level"] == 2, r
    assert not any(x["rules"] for x in rows if x["t"] < T0 + 20 * H), "tidak boleh ada alert sebelum whale jual"
    assert by_t[exit_t - 300]["whale_net"] == 0, "sinyal tidak boleh mengintip transaksi sesudah t"

    dumps = find_dumps(tok.prices)
    assert len(dumps) == 1, dumps
    d = dumps[0]
    assert T0 + 20 * H <= d["peak_t"] <= T0 + 21 * H and d["trough"] <= 0.8e-7 + 1e-12, d
    assert d["peak_t"] - 6 * H <= exit_t <= d["cross_t"]

    # nilai LP: full range ~ sqrt(harga); range ±20% di harga -50% sudah 100% token
    assert abs(lp_value(-0.5, 0) - 0.5 ** 0.5) < 1e-12
    assert abs(lp_value(0, 20) - 1) < 1e-12
    assert abs(lp_value(-0.5, 20) - 0.5324) < 1e-3
    assert abs(lp_value(0.5, 20) - lp_value(1.0, 20)) < 1e-12, "di atas range nilai tetap (semua ETH)"
    assert abs(lp_value(-0.2, 20, 20) - lp_value(-0.2, 20)) < 1e-12 and lp_value(-0.35, 35, 70) < lp_value(-0.35, 35, 35)

    by = {f["t"]: f for f in features(tok)}
    calm = by[T0 + 8 * H]
    assert calm["ath4h_break"], "harga sintetis naik terus = ATH 4 jam baru"
    lvl, hits, _ = evaluate(calm, None, [])
    assert lvl == 1 and hits["L7"] and not hits["K5"], "tembus ATH 4 jam = minimal HATI-HATI"
    assert evaluate({**calm, "ath4h_break": False}, None, [])[0] == 0, "periode tenang tanpa tembus ATH = LAYAK"
    whale_exit = by[T0 + 21 * H]
    assert abs(whale_exit["wn6"] - 2.0) < 1e-6 and evaluate(whale_exit, None, [])[1]["K1"], "whale keluar = K1"
    lvl, hits, _ = evaluate(by[T0 + 23 * H], None, [])
    assert lvl == 2 and hits["K2"], "harga -48% dalam 4 jam = falling knife"
    cut = truncate(ds, T0 + 10 * H)
    assert len(cut["prices"]) == 120 and max(x[1] for x in cut["transfers"]) < T0 + 10 * H, "truncate memotong data sesudah t"

    # pola retest ATH: naik ke 1.0 (jam 10) → pullback ke 0.6 (jam 14) → retest 0.98 (jam 18) → gagal → breakdown
    path = [(0, 0.2), (10, 1.0), (14, 0.6), (18, 0.98), (19, 0.8), (20, 0.55)]
    pt, pv = [], []
    for (h0, p0), (h1, p1) in zip(path, path[1:]):
        for m in range(int((h1 - h0) * 12)):
            pt.append(T0 + int((h0 + m / 12) * H))
            pv.append(p0 + (p1 - p0) * m / ((h1 - h0) * 12))
    pt.append(T0 + 20 * H)
    pv.append(0.55)
    state = lambda h: (retest_state(pt, pv, T0 + int(h * H)) or {}).get("state")
    assert state(12) is None, "belum retest"
    assert state(18) == "RETEST" and state(19) == "GAGAL" and state(20) == "BREAKDOWN", [state(h) for h in (18, 19, 20)]
    pat = retest_state(pt, pv, T0 + 18 * H)
    assert abs(pat["h1"] - 1.0) < 1e-9 and abs(pat["low"] - 0.6) < 1e-9
    breakout = [p * (1.4 if t > T0 + 15 * H else 1) for t, p in zip(pt, pv)]  # tembus jauh di atas high = breakout
    assert retest_state(pt, breakout, T0 + 18 * H) is None
    # tanda sebelum crash: pump 4 jam, sudah 2x dari low 24 jam, di puncak, whale memborong
    row = {"r4h": 0.4, "ext24": 1.0, "off24": -0.02, "pattern": None, "wn6": -2.0, "fresh": 0.5}
    assert signs_of(row) == {"pump", "lipat", "pucuk", "borong"}, signs_of(row)
    assert signs_of({**row, "r4h": -0.1, "ext24": 0.2, "off24": -0.5, "wn6": 2.0}) == {"jual"}
    # saran range: histogram penurunan -> kuantil
    assert drop_bin(-0.301) == 31 and drop_bin(0.05) == 0 and drop_bin(-1.5) == 100
    h = [0] * 101
    for d in [5] * 70 + [20] * 15 + [60] * 15:  # 70% kasus turun 5%, 15% turun 20%, 15% turun 60%
        h[d] += 1
    assert hist_quantile(h, 0.8) == 20 and hist_quantile(h, 0.9) == 60 and hist_quantile(h, 0.5) == 5
    print("selftest OK")


if __name__ == "__main__":
    main()
