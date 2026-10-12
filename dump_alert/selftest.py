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


def main():
    levels_case()
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
