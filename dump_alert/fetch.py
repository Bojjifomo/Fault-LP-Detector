"""
Tarik data on-chain sebuah token di Robinhood Chain untuk deteksi pola sebelum dump:
semua transfer ERC20 (lewat alchemy_getAssetTransfers, jadi tidak kena batas 10 blok
eth_getLogs di free tier), pool Uniswap v4/v3 utamanya, sampel harga historis dari
storage pool, dan klasifikasi alamat kontrak vs wallet (EOA).

  ALCHEMY_API_KEY=... python -m dump_alert.fetch --token 0xALAMAT [--token 0xLAIN]
  ALCHEMY_API_KEYS=key1,key2,key3 python -m dump_alert.fetch --token 0xALAMAT   # beberapa key
  python -m dump_alert.analyze dump_alert/data/0xalamat.json

Jalankan ulang perintah yang sama untuk update inkremental. Progres disimpan per tahap, jadi
kalau terputus (rate limit, kuota habis) perintah berikutnya melanjutkan dari titik terakhir.
"""
import argparse
import bisect
import calendar
import collections
import concurrent.futures
import itertools
import json
import math
import os
import random
import re
import sys
import threading
import time

import requests

from lp_analysis.chain import (Q96, RPC, RPCError, V3_NPM, V4_POSM, ZERO, calldata, dec_string,
                               decode_v4_slot0, enc_addr, enc_int, keccak_bytes, signed, topic, v4_pool_id,
                               v4_state_slot, v4_tick_slot, word_addr, words)

T_INIT = topic("Initialize(bytes32,address,address,uint24,int24,address,uint160,int24)")
T_SWAP = topic("Swap(bytes32,address,int128,int128,uint160,uint128,int24,uint24)")
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
# Sampel harga berjenjang: resolusi penuh (`step`, default 5 menit) hanya untuk hari-hari terakhir
# yang dipakai pola & grafik; riwayat lama cukup jarang (dipakai statistik riwayat & ATH).
FINE_DAYS, MID_DAYS = 3, 10
MID_STEP, COARSE_STEP = 1800, 7200
TRANSFER_WORKERS = 8   # per key
H = 3600
ADDR_CACHE = os.path.join(DATA_DIR, "_addresses.json")  # kontrak vs wallet, dipakai bersama semua token

# Analisa difokuskan ke pool token/USDG (tempat LP dibuka): harga dibaca dari pool USDG paling ramai
# dalam USD; riwayat sebelum pool USDG ada diambil dari pool utama yang dikonversi ke USDG per sampel.
USDG = "0x5fc5360d0400a0fd4f2af552add042d716f1d168"
ETH_USDG = (ZERO, USDG, 100, 1, ZERO)  # pool ETH/USDG yang paling sering dilalui rute swap (cadangan konversi)
MARKET_VERSION = 2       # versi format market; data versi lama dicari ulang poolnya & harganya disampel ulang
USDG_GRACE = 3600        # pool USDG baru dipakai 1 jam sesudah dibuat (harga awal sering belum di-arbitrase)
USDG_RECHECK = 6 * 3600  # koin tanpa pool USDG dicek ulang (pool USDG sering dibuat belakangan)
BOOK_MAX_DROP = 0.95     # peta likuiditas LP dibaca dari harga sekarang sampai -95%
BOOK_MAX_RISE = 2.0      # ... dan ke atas sampai +200% (likuiditas yang menahan pembelian)
BOOK_REFRESH = 900       # detik: peta likuiditas dibaca ulang paling sering tiap 15 menit
BOOK_POOLS = 8           # pool terbanyak yang dibaca peta likuiditasnya (USDG + ETH paling ramai)
LP_CHECK_H = 24          # transaksi besar dalam 24 jam terakhir dicek: swap atau tambah/tarik likuiditas?
LP_MIN_PCT = 0.1         # ... yang memindahkan >= 0,1% supply ke/dari pool
T_MODIFY = topic("ModifyLiquidity(bytes32,address,int24,int24,int256,bytes32)")

ALCHEMY_URL = "https://robinhood-mainnet.g.alchemy.com/v2"
# Perkiraan compute unit (CU) per method menurut tabel Alchemy; untuk mengatur kecepatan & melaporkan pemakaian.
CU = {"eth_blockNumber": 10, "eth_getBlockByNumber": 16, "eth_getTransactionReceipt": 15, "eth_getStorageAt": 17,
      "eth_call": 26, "eth_getCode": 26, "eth_getLogs": 75, "alchemy_getAssetTransfers": 150}
# CU/detik per key. Diukur di free tier: ±450 CU/detik bertahan tanpa ditolak, tapi di ±680 sebagian
# besar panggilan ditolak dan hasil bersihnya malah turun ke ±220. Jadi mulai sedikit di bawah batas,
# naik pelan saat lancar, turun sebanding porsi yang ditolak.
START_CUPS, MIN_CUPS, MAX_CUPS = 420, 60, 5000
BATCH_SECONDS = 0.5  # ukuran batch ≈ kuota 0,5 detik, supaya beban rata (batch besar sering ditolak sebagian)
GIVE_UP = 180      # detik: request yang terus kena 429/5xx/putus baru menyerah setelah ini
DEAD_RETRY = 3600  # detik: key yang kuotanya habis / ditolak dicoba lagi setelah ini


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def parse_ts(s: str) -> int:
    return calendar.timegm(time.strptime(s[:19], "%Y-%m-%dT%H:%M:%S"))


def data_path(token: str) -> str:
    return os.path.join(DATA_DIR, token.lower() + ".json")


def _monthly(err) -> bool:
    """Kuota bulanan / batas belanja habis: key ini tidak bisa dipakai lagi sampai kuota reset."""
    s = str(err).lower()
    return "monthly" in s or "spending" in s


def _limited(err) -> bool:
    """Batas compute-unit/detik: cukup pelankan lalu ulangi."""
    s = str(err).lower()
    return not _monthly(s) and ("429" in s or "rate limit" in s or "rate-limit" in s or "too many" in s
                                or "compute units" in s or "capacity" in s)


def _why(code, body, errors=()) -> str:
    """Alasan key dimatikan, dalam kalimat pendek."""
    if _monthly(body) or _monthly(errors):
        return "monthly quota used up"
    try:
        msg = json.loads(body)["error"]["message"]
    except (ValueError, KeyError, TypeError):
        msg = body[:100]
    hint = {401: "wrong / unknown key", 402: "payment limit reached",
            403: "rejected; make sure Robinhood Chain is enabled in that Alchemy app"}.get(code, "rejected")
    return f"{hint} (HTTP {code}: {msg})"


class QuotaError(RPCError):
    """Tidak ada key yang bisa dipakai (kuota bulanan habis, key salah, atau jaringan belum diaktifkan)."""


def parse_keys(*sources) -> list[str]:
    """Alchemy key dari beberapa sumber: string (dipisah koma/spasi/baris), list, atau URL
    .../v2/<key> hasil copy dari dashboard. Urutan dipertahankan, duplikat dibuang."""
    out = []
    for src in sources:
        for item in (src if isinstance(src, (list, tuple)) else [src]):
            for k in re.split(r"[\s,;]+", str(item or "")):
                k = k.strip().strip("\"'")
                if "/v2/" in k:
                    k = k.rsplit("/v2/", 1)[1].strip("/")
                if k and k not in out:
                    out.append(k)
    return out


def env_keys() -> list[str]:
    return parse_keys(os.environ.get("ALCHEMY_API_KEYS"), os.environ.get("ALCHEMY_API_KEY"))


class Lane:
    """Satu endpoint (satu API key). Pembatas compute-unit/detik yang menyesuaikan diri: naik pelan
    selama lancar, turun tajam saat kena 429, jadi berapa pun thread-nya kecepatannya tetap di bawah
    batas key. Key yang kuota bulanannya habis atau ditolak dimatikan sementara."""

    def __init__(self, url):
        self.url = url
        self.rate, self.tokens = float(START_CUPS), START_CUPS * BATCH_SECONDS
        self.t = time.monotonic()
        self.dead_until, self.why, self.cu = 0.0, "", 0
        self.lock = threading.Lock()

    def alive(self):
        return time.time() >= self.dead_until

    def ready(self):
        """Perkiraan token CU yang tersedia sekarang (untuk memilih key yang paling longgar)."""
        return min(self.rate * BATCH_SECONDS, self.tokens + (time.monotonic() - self.t) * self.rate) / self.rate

    def take(self, cu):
        # simpanan token maksimal setara BATCH_SECONDS: beberapa thread tidak bisa menembak sekaligus
        # (lonjakan sesaat langsung ditolak Alchemy walau rata-rata per detiknya masih di bawah batas)
        with self.lock:
            now = time.monotonic()
            self.tokens = min(self.rate * BATCH_SECONDS, self.tokens + (now - self.t) * self.rate) - cu
            self.t = now
            wait = -self.tokens / self.rate if self.tokens < 0 else 0.0
        if wait:
            time.sleep(wait)

    def ok(self, cu):
        with self.lock:
            self.cu += cu
            # di bawah START_CUPS pulih cepat (sesudah dipelankan); di atasnya naik pelan, karena melewati
            # batas key membuat banyak panggilan ditolak dan hasil bersihnya malah ambruk
            self.rate = min(MAX_CUPS, self.rate * 1.05 if self.rate < START_CUPS else self.rate + cu / 100)

    def throttle(self, frac=1.0):
        """Kena 429; `frac` = porsi panggilan yang ditolak (batch bisa ditolak sebagian)."""
        with self.lock:
            self.rate = max(MIN_CUPS, self.rate * (1 - 0.25 * frac))
            self.tokens = min(self.tokens, 0.0) - 0.5 * self.rate * frac  # jeda sebanding porsi yang ditolak

    def kill(self, why):
        self.dead_until, self.why = time.time() + DEAD_RETRY, why


_LANES, _LANES_LOCK = {}, threading.Lock()


def lane_for(url) -> Lane:
    """Status key disimpan per proses (dipakai ulang antar pengecekan di Streamlit)."""
    with _LANES_LOCK:
        if url not in _LANES:
            _LANES[url] = Lane(url)
        return _LANES[url]


class PacedRPC(RPC):
    """RPC untuk satu atau beberapa API key. Tiap request memakai key yang paling longgar; key yang
    kena 429 dipelankan, key yang kuota bulanannya habis / ditolak dilewati. Batch dikirim paralel
    dan hanya panggilan yang ditolak yang diulang. Aman dipakai dari beberapa thread."""

    def __init__(self, urls, batch_size=25, workers=None):
        urls = [urls] if isinstance(urls, str) else list(urls)
        super().__init__(urls[0], batch_size=batch_size)
        self.lanes = [lane_for(u) for u in urls]
        self.workers = workers or min(16, 4 * len(urls))
        self.cu = 0
        self._secrets = [u.rsplit("/v2/", 1)[1].strip("/") for u in urls if "/v2/" in u]
        self._local = threading.local()
        self._lock = threading.Lock()

    def _redact(self, msg) -> str:
        msg = str(msg)
        for sec in self._secrets:
            if sec:
                msg = msg.replace(sec, "***")
        return msg

    def _session(self):
        if not hasattr(self._local, "s"):
            self._local.s = requests.Session()
        return self._local.s

    def status(self):
        """[(nomor key, keterangan, CU terpakai sejak app jalan)]"""
        return [(i + 1, "active" if ln.alive() else ln.why, ln.cu) for i, ln in enumerate(self.lanes)]

    def _pick(self):
        live = [ln for ln in self.lanes if ln.alive()]
        if not live:
            why = "; ".join(f"key #{i}: {w}" for i, w, _ in self.status())
            raise QuotaError(f"no Alchemy key is usable ({why})")
        return max(live, key=lambda ln: (ln.ready(), random.random()))

    def _post(self, payload):
        items = payload if isinstance(payload, list) else [payload]
        cost = sum(CU.get(c["method"], 26) for c in items)
        deadline, delay, err = time.monotonic() + GIVE_UP, 0.5, None
        while True:
            ln = self._pick()
            ln.take(cost)
            self.requests += 1
            try:
                r = self._session().post(ln.url, json=payload, timeout=90)
            except requests.RequestException as e:
                err = e
            else:
                body = r.text[:300]
                try:
                    resp = r.json() if r.status_code == 200 else None
                except ValueError:  # 200 tapi bukan JSON (gangguan proxy/server): anggap error sementara
                    resp, r.status_code = None, 502
                errors = [x.get("error") for x in (resp if isinstance(resp, list) else [resp]) if isinstance(x, dict)]
                if r.status_code in (401, 402, 403) or _monthly(body if resp is None else errors):
                    ln.kill(self._redact(_why(r.status_code, body, errors)))
                    continue  # langsung coba key lain; QuotaError kalau tidak ada lagi
                if resp is not None:
                    self._local.lane = ln
                    ln.ok(cost)
                    with self._lock:
                        self.cu += cost
                    return resp
                if r.status_code == 429:
                    ln.throttle()
                    err = f"HTTP 429: {body}"
                elif r.status_code >= 500:
                    err = f"HTTP {r.status_code}: {body}"
                else:
                    raise RPCError(self._redact(f"HTTP {r.status_code}: {body}"))
            if time.monotonic() > deadline:
                raise RPCError(self._redact(f"failed after retrying for {GIVE_UP}s: {err}"))
            time.sleep(delay * (1 + random.random()))
            delay = min(delay * 2, 8)

    def call(self, method, params):
        deadline = time.monotonic() + GIVE_UP
        while True:
            resp = self._post({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
            if "error" not in resp:
                return resp["result"]
            if not _limited(resp["error"]) or time.monotonic() > deadline:
                raise RPCError(self._redact(f"{method}: {resp['error']}"))
            self._local.lane.throttle()

    def batch(self, calls):
        """Hasil per call (call gagal = instance RPCError). Batch dikirim paralel: latensi
        server per batch (~1 detik untuk state historis) lebih membatasi daripada kuota."""
        results = [None] * len(calls)
        avg = sum(CU.get(c[0], 26) for c in calls) / len(calls) if calls else 26
        rate = max((ln.rate for ln in self.lanes if ln.alive()), default=START_CUPS)
        size = max(4, min(self.batch_size, int(rate * BATCH_SECONDS / avg)))
        chunks = [list(range(i, min(i + size, len(calls)))) for i in range(0, len(calls), size)]

        def run(idx):
            deadline = time.monotonic() + GIVE_UP
            while idx:
                resp = self._post([{"jsonrpc": "2.0", "id": k, "method": calls[k][0], "params": calls[k][1]}
                                   for k in idx])
                if isinstance(resp, dict):  # error untuk seluruh batch
                    if not _limited(resp.get("error")) or time.monotonic() > deadline:
                        raise RPCError(self._redact(str(resp.get("error", resp))))
                    self._local.lane.throttle()
                    continue
                by_id, retry = {r.get("id"): r for r in resp}, []
                for k in idx:
                    r = by_id.get(k)
                    if r is None or ("error" in r and _limited(r["error"])):
                        retry.append(k)
                    elif "error" in r:
                        results[k] = RPCError(str(r["error"]))
                    else:
                        results[k] = r["result"]
                if retry and time.monotonic() > deadline:
                    for k in retry:
                        results[k] = RPCError("rate limited")
                    return
                if retry:
                    self._local.lane.throttle(len(retry) / len(idx))
                idx = retry

        with concurrent.futures.ThreadPoolExecutor(min(self.workers, len(chunks)) or 1) as ex:
            for fut in [ex.submit(run, c) for c in chunks]:
                fut.result()
        return results


def make_rpc(url: str | None = None, keys=None) -> PacedRPC:
    """`keys` = daftar Alchemy key (default: ALCHEMY_API_KEYS / ALCHEMY_API_KEY dari environment)."""
    url = url or os.environ.get("RH_RPC_URL")
    if url:
        return PacedRPC([url])
    keys = env_keys() if keys is None else parse_keys(keys)
    return PacedRPC([f"{ALCHEMY_URL}/{k}" for k in keys] or [ALCHEMY_URL])


def eth_usd(keys=None):
    """Harga ETH dalam USD: Alchemy Prices API, cadangan CoinGecko; None kalau semuanya gagal."""
    keys = env_keys() if keys is None else parse_keys(keys)
    sources = [(f"https://api.g.alchemy.com/prices/v1/{k}/tokens/by-symbol", {"symbols": "ETH"},
                lambda j: j["data"][0]["prices"][0]["value"]) for k in keys[:2]]
    sources.append(("https://api.coingecko.com/api/v3/simple/price", {"ids": "ethereum", "vs_currencies": "usd"},
                    lambda j: j["ethereum"]["usd"]))
    for url, params, pick in sources:
        try:
            return float(pick(requests.get(url, params=params, timeout=15).json()))
        except (requests.RequestException, KeyError, IndexError, ValueError, TypeError):
            continue
    return None


_PATH_LOCKS, _PATH_LOCKS_LOCK = {}, threading.Lock()


def path_lock(path) -> threading.Lock:
    """Satu penarik data per file: dua pengecekan token yang sama tidak menarik data dua kali."""
    with _PATH_LOCKS_LOCK:
        return _PATH_LOCKS.setdefault(os.path.abspath(path), threading.Lock())


def load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{threading.get_ident()}.tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f)
    os.replace(tmp, path)


def sqrt_to_price(sqrt_x96: int, token_is_c1: bool, dec_token: int, dec_quote: int):
    """Harga 1 token dalam unit quote dari sqrtPriceX96 (= sqrt(c1_mentah / c0_mentah))."""
    if not sqrt_x96:
        return None
    p = (sqrt_x96 / Q96) ** 2
    return (1 / p if token_is_c1 else p) * 10 ** (dec_token - dec_quote)


def is_contract_code(code) -> bool:
    # 0xef0100… = EOA dengan delegasi EIP-7702 (smart wallet), tetap dihitung wallet
    return isinstance(code, str) and len(code) > 2 and not code.startswith("0xef0100")


class Fetcher:
    def __init__(self, rpc: RPC, token: str, path: str, progress=log):
        self.rpc, self.token, self.path, self.log = rpc, token.lower(), path, progress
        self.ds = {"token": self.token}  # dimuat di run(), di dalam kunci file
        self._saved = time.time()
        self._stop = threading.Event()

    def call(self, to, sig, *args, block="latest"):
        return self.rpc.call("eth_call", [{"to": to, "data": calldata(sig, *args)}, block])

    def run(self, step: int, extra_markets=(), full=False, liquidity=True):
        """Tarik/update data. Progres disimpan per tahap (dan juga saat gagal), jadi pengecekan
        berikutnya melanjutkan dari titik terakhir tanpa mengulang panggilan yang sudah dibayar.
        liquidity=False: lewati likuiditas pool & peta likuiditas (untuk alarm yang sering polling)."""
        with path_lock(self.path):
            self.ds = load_json(self.path, {"token": self.token})  # bisa sudah diperbarui pengecekan lain
            try:
                return self._run(step, extra_markets, full, liquidity)
            except BaseException:  # termasuk Streamlit menghentikan script: simpan yang sudah ditarik
                if "symbol" in self.ds:
                    self.save()
                raise

    def _run(self, step, extra_markets, full, liquidity=True):
        t0, cu0 = time.time(), getattr(self.rpc, "cu", 0)
        self.latest = int(self.rpc.call("eth_blockNumber", []), 16)
        self.latest_ts = int(self.rpc.call("eth_getBlockByNumber", [hex(self.latest), False])["timestamp"], 16)
        if "symbol" not in self.ds:
            self.metadata()
        stage = time.time()

        def done(name):
            nonlocal stage
            self.log(f"✓ {name} ({time.time() - stage:.0f}s)")
            stage = time.time()
            self.save()

        self.transfers()
        done("transfers")
        self.ensure_market()
        self.mark_lp_txs()
        for m in extra_markets:
            if m.lower() not in self.ds["markets"]:
                self.ds["markets"].append(m.lower())
        self.classify()
        done("wallet vs contract check")
        self.sample_prices(step, full)
        if liquidity:  # likuiditas pool & peta likuiditas (watch tiap 5 menit tidak butuh: hemat CU)
            self.refresh_depths()
            self.liquidity_book()
        done("prices & liquidity")
        cu = getattr(self.rpc, "cu", 0) - cu0
        self.log(f"data ready in {time.time() - t0:.0f}s" + (f" (±{cu:,} CU)" if cu else ""))
        return self.ds

    def checkpoint(self, every=30):
        """Simpan progres di tengah tahap panjang, paling sering tiap `every` detik."""
        if time.time() - self._saved >= every:
            self.save()

    # ------------------------------------------------------------ metadata & transfer

    def metadata(self):
        t = self.token
        dec = words(self.call(t, "decimals()"))[0]
        # transfer pertama (mint) = awal riwayat; 1 panggilan, bukan binary search eth_getCode
        first = self.rpc.call("alchemy_getAssetTransfers", [{
            "fromBlock": "0x0", "toBlock": hex(self.latest), "contractAddresses": [t], "category": ["erc20"],
            "withMetadata": True, "excludeZeroValue": False, "maxCount": "0x1", "order": "asc"}])["transfers"]
        if first:
            lo = int(first[0]["blockNum"], 16)
        else:
            lo, hi = 0, self.latest
            while lo < hi:  # blok pembuatan kontrak
                mid = (lo + hi) // 2
                if self.rpc.call("eth_getCode", [t, hex(mid)]) not in ("0x", ""):
                    hi = mid
                else:
                    lo = mid + 1
        blk = self.rpc.call("eth_getBlockByNumber", [hex(lo), False])
        self.ds.update(
            name=dec_string(self.call(t, "name()")), symbol=dec_string(self.call(t, "symbol()")),
            decimals=dec, supply=words(self.call(t, "totalSupply()"))[0] / 10 ** dec,
            created_block=lo, created_ts=int(blk["timestamp"], 16),
            transfers=[], prices=[], contracts={}, markets=[], market=None)
        self.log(f"{self.ds['name']} ({self.ds['symbol']}) supply {self.ds['supply']:,.0f}, created at block {lo}")

    def _transfer_segment(self, a, b):
        out, page = [], None
        while True:
            if self._stop.is_set():  # segmen lain gagal: jangan buang kuota untuk hasil yang tidak dipakai
                raise RPCError("dibatalkan")
            p = {"fromBlock": hex(a), "toBlock": hex(b), "contractAddresses": [self.token],
                 "category": ["erc20"], "withMetadata": True, "excludeZeroValue": False,
                 "maxCount": "0x3e8", "order": "asc"}
            if page:
                p["pageKey"] = page
            r = self.rpc.call("alchemy_getAssetTransfers", [p])
            for x in r["transfers"]:
                out.append([int(x["blockNum"], 16), parse_ts(x["metadata"]["blockTimestamp"]), x["hash"],
                            x["from"].lower(), (x["to"] or ZERO).lower(),
                            int(x["rawContract"]["value"] or "0x0", 16)])
            page = r.get("pageKey")
            if not page:
                return out

    def transfers(self):
        T = self.ds["transfers"]
        start = T[-1][0] if T else self.ds["created_block"]
        while T and T[-1][0] == start:  # blok terakhir ditarik ulang utuh
            T.pop()
        n0, span = len(T), self.latest - start
        # rentang blok dipecah jadi segmen yang ditarik paralel (pageKey hanya bisa berurutan per segmen)
        nseg = 32 if span > 2_000_000 else 8 if span > 200_000 else 1
        edges = [start + span * i // nseg for i in range(nseg)] + [self.latest + 1]
        segs = [(edges[i], edges[i + 1] - 1) for i in range(nseg) if edges[i] <= edges[i + 1] - 1]
        parts, done = [None] * len(segs), 0
        workers = min(len(segs), TRANSFER_WORKERS * len(getattr(self.rpc, "lanes", [0])))
        ex = concurrent.futures.ThreadPoolExecutor(workers)
        self._stop.clear()
        try:
            futs = {ex.submit(self._transfer_segment, a, b): i for i, (a, b) in enumerate(segs)}
            for fut in concurrent.futures.as_completed(futs):
                parts[futs[fut]] = fut.result()
                done += 1
                if len(segs) > 1 and (done % 4 == 0 or done == len(segs)):
                    self.log(f"  transfers: segment {done}/{len(segs)}, {sum(len(x) for x in parts if x):,} so far")
        finally:
            self._stop.set()
            ex.shutdown(wait=False, cancel_futures=True)
            # hanya segmen berurutan dari awal yang disimpan, supaya lanjutan dari blok terakhir tetap utuh
            for part in itertools.takewhile(lambda x: x is not None, parts):
                T.extend(part)
        self.log(f"transfers: {len(T):,} (+{len(T) - n0:,})")

    # ------------------------------------------------------------ market (pool)

    def ensure_market(self):
        """Cari pool kalau belum ada, kalau format lama (harga dalam ETH), atau kalau dulu belum ada
        pool USDG dan sudah lewat USDG_RECHECK. Satuan harga berubah = sampel harga diulang."""
        m = self.ds.get("market")
        if m and m.get("kind") == "v4":
            if m.get("v") != MARKET_VERSION:
                self.log("old data format: looking up pools again (USDG focus) and re-sampling prices")
                m = self.ds["market"] = None
                self.ds["prices"] = []
            elif "pools" not in m:  # data sebelum ada peta likuiditas: lengkapi daftar pool
                self.discover_market()
                new = self.ds["market"]
                same = new and new.get("pool_id") == m.get("pool_id") and \
                    (new.get("pre") or {}).get("pool_id") == (m.get("pre") or {}).get("pool_id")
                if same:  # sumber harga sama: harga lama tetap dipakai
                    new["usdg_from_block"] = m.get("usdg_from_block")
                else:
                    self.ds["prices"] = []
                return
            elif not m.get("usdg_pools") and self.latest_ts - m.get("checked_ts", 0) > USDG_RECHECK:
                old = m
                self.discover_market()
                if self.ds["market"] and self.ds["market"].get("usdg_pools"):
                    self.log("a new USDG pool was found: re-sampling prices in USDG")
                    self.ds["prices"] = []
                else:  # tetap tanpa pool USDG: pertahankan market & harga lama
                    old["checked_ts"] = self.latest_ts
                    self.ds["market"] = old
                return
        if not m:
            self.discover_market()

    def mark_lp_txs(self):
        """Di Uniswap v4, tambah likuiditas terlihat seperti jual dan tarik likuiditas seperti beli di transfer
        token (di HYDX 22% transaksi besar ternyata transaksi LP). Receipt transaksi besar dalam LP_CHECK_H
        jam terakhir dicek sekali: yang berisi ModifyLiquidity tanpa Swap ditandai, supaya tidak dihitung
        sebagai whale jual/beli. Biasanya beberapa ratus panggilan (±15 CU) per koin, lalu inkremental."""
        m = self.ds.get("market") or {}
        if m.get("kind") != "v4":
            return
        pm, T = m["manager"], self.ds["transfers"]
        markets = set(self.ds["markets"])
        thr = self.ds["supply"] * 10 ** self.ds["decimals"] * LP_MIN_PCT / 100
        checked = set(self.ds.setdefault("lp_checked", []))
        start = self.latest_ts - LP_CHECK_H * H if hasattr(self, "latest_ts") else 0
        net = collections.Counter()
        for x in T[bisect.bisect_left([y[1] for y in T], start):]:
            net[x[2]] += (x[5] if x[4] in markets else 0) - (x[5] if x[3] in markets else 0)
        todo = [h for h, d in net.items() if abs(d) >= thr and h not in checked]
        if not todo:
            return
        lp = self.ds.setdefault("lp_txs", [])
        for h, r in zip(todo, self.rpc.batch([("eth_getTransactionReceipt", [h]) for h in todo])):
            if not isinstance(r, dict):
                continue
            tops = {lg["topics"][0] for lg in r["logs"] if lg["address"].lower() == pm and lg["topics"]}
            if T_MODIFY in tops and T_SWAP not in tops:
                lp.append(h)
            self.ds["lp_checked"].append(h)
        lps = set(lp)
        self.log(f"  large trades in the last 24h: {len(todo)} checked, {sum(1 for h in todo if h in lps)} were LP adds/removals")

    def _currency(self, c):
        """(simbol, desimal) sebuah currency; ETH native = alamat nol."""
        cache = self.__dict__.setdefault("_cur", {ZERO: ("ETH", 18), USDG: ("USDG", 6)})
        if c not in cache:
            cache[c] = (dec_string(self.call(c, "symbol()")), words(self.call(c, "decimals()"))[0])
        return cache[c]

    def discover_market(self):
        tok, T = self.token, self.ds["transfers"]
        pm = word_addr(words(self.call(V4_POSM, "poolManager()"))[0])
        weth = word_addr(words(self.call(V3_NPM, "WETH9()"))[0])
        market, markets = None, set()
        hashes = list(dict.fromkeys(x[2] for x in T if pm in (x[3], x[4])))
        if hashes:
            markets.add(pm)
            market = self._find_v4_pool(pm, weth, hashes)
        if market is None:
            market = self._find_v3_pool()
            if market:
                markets.add(market["pool"])
                quote = market["currency0"] if market["currency1"] == tok else market["currency1"]
                qs, qd = self._currency(quote)
                market.update(token_is_c1=market["currency1"] == tok, quote=quote, quote_symbol=qs,
                              quote_decimals=qd, usd=qs == "USDG")
        # penerima mint yang berupa kontrak dan jadi lawan transaksi banyak wallet = bonding curve (mis. Pons)
        mint_to = next((x[4] for x in T if x[3] == ZERO), None)
        if mint_to and mint_to not in markets and is_contract_code(self.rpc.call("eth_getCode", [mint_to, "latest"])):
            senders = {x[3] for x in T if x[4] == mint_to}
            receivers = {x[4] for x in T if x[3] == mint_to}
            if len(senders) >= 20 and len(receivers) >= 20:
                markets.add(mint_to)
        self.ds["market"], self.ds["markets"] = market, sorted(markets)
        if not market:
            self.log("market: no Uniswap pool found; price signals disabled")
        elif market.get("usdg_pools"):
            n = len(market["usdg_pools"])
            self.log(f"market: USDG pool {market['pool_id'][:10]}… fee {market['fee'] / 1e4:g}%"
                     + (f" (deepest of {n} USDG pools)" if n > 1 else ""))
        else:
            pair = market.get("pair_symbol") or market["quote_symbol"]
            self.log(f"market: no active USDG pool; using the main pool {market.get('pool_id') or market.get('pool')} "
                     f"vs {pair}")

    def _find_v4_pool(self, pm, weth, hashes):
        tok = self.token
        # receipt dari awal, tersebar di riwayat, dan banyak dari yang terbaru: pool USDG biasanya dibuat
        # belakangan oleh LP, jadi swap-nya hanya terlihat di transaksi baru
        hist = set(hashes[:5] + hashes[5::max(1, len(hashes) // 40)][:40])
        sample = list(dict.fromkeys(hashes[:5] + hashes[5::max(1, len(hashes) // 40)][:40] + hashes[-120:]))
        # freq_hist = keramaian sepanjang riwayat (pool untuk riwayat lama), freq = keramaian terbaru
        freq, freq_hist, keys = collections.Counter(), collections.Counter(), {}
        for h, r in zip(sample, self.rpc.batch([("eth_getTransactionReceipt", [h]) for h in sample])):
            if not isinstance(r, dict):
                continue
            for lg in r["logs"]:
                if lg["address"].lower() != pm or not lg["topics"]:
                    continue
                if lg["topics"][0] == T_SWAP:
                    freq[lg["topics"][1]] += 1
                    freq_hist[lg["topics"][1]] += h in hist
                elif lg["topics"][0] == T_INIT:
                    keys[lg["topics"][1]] = self._init_key(lg)
        missing = [pid for pid in freq if pid not in keys]  # PoolKey dari PositionManager.poolKeys(bytes25)
        res = self.rpc.batch([("eth_call", [{"to": V4_POSM, "data": calldata("poolKeys(bytes25)", pid[2:52].ljust(64, "0"))},
                                            "latest"]) for pid in missing])
        for pid, r in zip(missing, res):
            w = words(r) if isinstance(r, str) else []
            if len(w) >= 5 and (w[0] or w[1]):
                keys[pid] = (word_addr(w[0]), word_addr(w[1]), w[2], signed(w[3], 24), word_addr(w[4]))
        if not any(tok in k[:2] for k in keys.values()):
            keys.update(self._scan_initialize(pm, next(x[0] for x in self.ds["transfers"] if pm in (x[3], x[4]))))
        # fee statis > 10% = pool umpan/sampah; fee dinamis (flag 0x800000) tetap boleh
        self._sane = lambda k: k[2] <= 100_000 or k[2] == 0x800000
        self._eth = (ZERO, weth)
        cands = {pid: k for pid, k in keys.items() if tok in k[:2] and self._sane(k)}
        if not cands:
            return None
        is_eth = lambda k: k[0] in self._eth or k[1] in self._eth
        # pool utama = paling ramai sepanjang riwayat (harus ada sejak awal untuk riwayat harga);
        # keramaian terbaru & pasangan ETH hanya penentu kalau seri
        best = max(cands, key=lambda p: (freq_hist[p], freq[p], is_eth(cands[p])))
        # pool USDG yang masih punya likuiditas, terdalam dulu: harganya paling stabil (pool tipis gampang
        # melenceng/kosong), dan harga antar pool USDG tetap sejalan karena di-arbitrase
        usdg = [p for p in cands if USDG in cands[p][:2]]
        depth = self._depths(pm, {p: cands[p] for p in usdg})
        usdg = sorted((p for p in usdg if depth[p]["liquidity"] > 0), key=lambda p: (-depth[p]["usdg_side"], -freq[p]))
        market = {"kind": "v4", "v": MARKET_VERSION, "manager": pm, "checked_ts": self.latest_ts,
                  "usdg_pools": [{"pool_id": p, "fee": cands[p][2], "tick_spacing": cands[p][3], "hooks": cands[p][4],
                                  "swaps": freq[p], **depth[p]} for p in usdg],
                  # semua pool token yang terlihat dipakai swap (untuk peta likuiditas/support)
                  "pools": [{"pool_id": p, "currency0": cands[p][0], "currency1": cands[p][1], "fee": cands[p][2],
                             "tick_spacing": cands[p][3], "hooks": cands[p][4], "swaps": freq[p]}
                            for p in sorted(cands, key=lambda p: -freq[p]) if freq[p] or p == best]}
        if usdg:
            market.update(self._source(usdg[0], cands[usdg[0]], keys, freq, USDG), usd=True, usdg_from_block=None)
            market["pre"] = None if best == usdg[0] else self._source(best, cands[best], keys, freq, USDG)
        else:
            market.update(self._source(best, cands[best], keys, freq, ZERO), usd=False, pre=None)
        return market

    def _source(self, pid, k, keys, freq, out):
        """Sumber harga = pool token (pid, k). Kalau pasangannya bukan `out` (USDG, atau ETH untuk koin
        tanpa pool USDG), harga dikonversi per sampel lewat pool pasangan/`out` (bridge)."""
        tok = self.token
        quote = k[0] if k[1] == tok else k[1]
        qs, qd = self._currency(quote)
        src = {"pool_id": pid, "currency0": k[0], "currency1": k[1], "fee": k[2], "tick_spacing": k[3],
               "hooks": k[4], "token_is_c1": k[1] == tok, "quote": quote, "quote_symbol": qs, "quote_decimals": qd,
               "bridge": None}
        same = (lambda c: c in self._eth) if quote in self._eth else (lambda c: c == quote)
        outs = self._eth if out == ZERO else (out,)
        if same(outs[0]) or quote in outs:
            return src
        bridges = [(p, b) for p, b in keys.items() if any(map(same, b[:2])) and any(c in outs for c in b[:2])
                   and tok not in b[:2] and self._sane(b)]
        if not bridges and out == USDG and quote in self._eth:
            bridges = [(v4_pool_id(*ETH_USDG), ETH_USDG)]
        if not bridges:
            return src if out == ZERO else None  # tidak bisa dikonversi ke USDG
        bp, b = max(bridges, key=lambda c: freq[c[0]])
        os_, od = self._currency(out)
        src["bridge"] = {"pool_id": bp, "currency0": b[0], "currency1": b[1], "fee": b[2], "hooks": b[4],
                         "quote_is_c1": same(b[1]), "pair_decimals": qd, "out_decimals": od}
        src.update(pair_symbol=qs, quote_symbol=os_)
        return src

    def _depths(self, pm, pools):
        """Likuiditas aktif tiap pool di blok terbaru: L dan cadangan virtual sisi USDG (setara full range)."""
        out, calls = {}, []
        for pid in pools:
            st = v4_state_slot(pid)
            calls += [("eth_getStorageAt", [pm, hex(st), "latest"]), ("eth_getStorageAt", [pm, hex(st + 3), "latest"])]
        res = self.rpc.batch(calls)
        for n, (pid, k) in enumerate(pools.items()):
            r0, r3 = res[2 * n], res[2 * n + 1]
            sq = decode_v4_slot0(int(r0, 16))[0] if isinstance(r0, str) and r0 not in ("0x", "") else 0
            liq = int(r3, 16) & (2 ** 128 - 1) if isinstance(r3, str) and r3 not in ("0x", "") else 0
            usdg = 0.0
            if sq and liq:
                sp = sq / Q96  # sqrt(c1 mentah / c0 mentah)
                usdg = (liq / sp if k[0] == USDG else liq * sp) / 1e6
            out[pid] = {"liquidity": liq, "usdg_side": usdg}
        return out

    def _eth_usdg(self, pm):
        """Harga ETH dalam USDG dari pool ETH/USDG di blok terbaru."""
        pre = (self.ds["market"].get("pre") or {}).get("bridge")
        if pre and pre.get("out_decimals") == 6:
            pid, eth_c1 = pre["pool_id"], pre["quote_is_c1"]
        else:
            pid, eth_c1 = v4_pool_id(*ETH_USDG), False
        r = self.rpc.call("eth_getStorageAt", [pm, hex(v4_state_slot(pid)), "latest"])
        sq = decode_v4_slot0(int(r, 16))[0] if r not in ("0x", "") else 0
        return sqrt_to_price(sq, eth_c1, 18, 6)

    def liquidity_book(self, force=False):
        """Peta likuiditas LP di sekitar harga sekarang untuk pool-pool utama token: likuiditas aktif,
        harga, dan tiap tick yang terinisialisasi beserta liquidityNet-nya, dari +200% sampai -95% harga
        sekarang. levels.py menghitung dari sini berapa tekanan jual yang dibutuhkan untuk menurunkan harga
        ke tiap level (support likuiditas). Biasanya < 2 ribu CU; dibaca ulang paling sering tiap 15 menit."""
        m = self.ds.get("market") or {}
        fresh = m.get("book") and self.latest_ts - m["book"]["ts"] < BOOK_REFRESH \
            and all("up" in p for p in m["book"]["pools"])  # peta lama tanpa sisi atas dibaca ulang
        if m.get("kind") != "v4" or (not force and fresh):
            return
        pm, tok = m["manager"], self.token
        eth = (ZERO, word_addr(words(self.call(V3_NPM, "WETH9()"))[0]))
        pools = {}
        for p in (m.get("usdg_pools") or []):
            pools[p["pool_id"]] = {**p, "currency0": min(USDG, tok), "currency1": max(USDG, tok)}
        for p in (m.get("pools") or []):
            pools.setdefault(p["pool_id"], p)
        for src in (m, m.get("pre")):
            if src and src.get("pool_id"):
                pools.setdefault(src["pool_id"], {k: src[k] for k in ("pool_id", "currency0", "currency1", "fee",
                                                                         "tick_spacing", "hooks")})
        # hanya pool vs USDG atau ETH (bisa dinilai dalam USD), maksimal BOOK_POOLS
        quote_of = lambda p: p["currency0"] if p["currency1"] == tok else p["currency1"]
        pools = [p for p in pools.values() if quote_of(p) == USDG or quote_of(p) in eth][:BOOK_POOLS]
        if not pools:
            return
        eth_usd = self._eth_usdg(pm)
        st = {p["pool_id"]: v4_state_slot(p["pool_id"]) for p in pools}
        res = self.rpc.batch([c for p in pools for c in (("eth_getStorageAt", [pm, hex(st[p["pool_id"]]), "latest"]),
                                                         ("eth_getStorageAt", [pm, hex(st[p["pool_id"]] + 3), "latest"]))])
        span = math.ceil(math.log(1 / (1 - BOOK_MAX_DROP)) / math.log(1.0001))
        span_up = math.ceil(math.log(1 + BOOK_MAX_RISE) / math.log(1.0001))
        out, wcalls = [], []
        for n, p in enumerate(pools):
            r0, r3 = res[2 * n], res[2 * n + 1]
            if not isinstance(r0, str) or not isinstance(r3, str) or r0 in ("0x", ""):
                continue
            sq, tick = decode_v4_slot0(int(r0, 16))
            if not sq:
                continue
            q = quote_of(p)
            c1 = p["currency1"] == tok
            ts = p["tick_spacing"]
            # dari +BOOK_MAX_RISE sampai -BOOK_MAX_DROP harga token (token c1: harga token turun = tick naik)
            lo, hi = (tick - span_up, tick + span) if c1 else (tick - span, tick + span_up)
            words_ = list(range((lo // ts) >> 8, ((hi // ts) >> 8) + 1))
            rec = {"pool_id": p["pool_id"], "fee": p["fee"], "tick_spacing": ts, "hooks": p["hooks"],
                   "quote": "USDG" if q == USDG else "ETH", "quote_decimals": 6 if q == USDG else 18,
                   "usd_per_quote": 1.0 if q == USDG else eth_usd, "token_is_c1": c1, "sqrt": sq, "tick": tick,
                   "liquidity": int(r3, 16) & (2 ** 128 - 1), "window": [lo, hi], "up": BOOK_MAX_RISE, "ticks": [], "_words": words_}
            out.append(rec)
            wcalls += [("eth_getStorageAt", [pm, hex(int.from_bytes(keccak_bytes(bytes.fromhex(enc_int(w))
                        + (st[p["pool_id"]] + 5).to_bytes(32, "big")), "big")), "latest"]) for w in words_]
        wres = self.rpc.batch(wcalls)
        tcalls, owner, k = [], [], 0
        for rec in out:
            ts, (lo, hi) = rec["tick_spacing"], rec["window"]
            for w in rec.pop("_words"):
                r = wres[k]
                k += 1
                bits = int(r, 16) if isinstance(r, str) and r not in ("0x", "") else 0
                while bits:
                    b = (bits & -bits).bit_length() - 1
                    bits &= bits - 1
                    t = (w * 256 + b) * ts
                    if lo <= t <= hi:
                        tcalls.append(("eth_getStorageAt", [pm, hex(v4_tick_slot(v4_state_slot(rec["pool_id"]), t)),
                                                            "latest"]))
                        owner.append((rec, t))
        for (rec, t), r in zip(owner, self.rpc.batch(tcalls)):
            if isinstance(r, str) and r not in ("0x", ""):
                rec["ticks"].append([t, signed(int(r, 16) >> 128, 128)])
        m["book"] = {"ts": self.latest_ts, "block": self.latest, "eth_usd": eth_usd, "pools": out}
        self.log(f"  liquidity map: {len(out)} pools, {len(owner)} ticks")

    def refresh_depths(self):
        """Perbarui likuiditas pool USDG tiap kali data ditarik (2 panggilan per pool)."""
        m = self.ds.get("market") or {}
        pools = m.get("usdg_pools") or []
        if not pools:
            return
        keys = {p["pool_id"]: (USDG, self.token) if USDG < self.token else (self.token, USDG) for p in pools}
        for p, d in zip(pools, self._depths(m["manager"], keys).values()):
            p.update(d)

    @staticmethod
    def _init_key(lg):
        w = words(lg["data"])
        return (word_addr(int(lg["topics"][2], 16)), word_addr(int(lg["topics"][3], 16)),
                w[0], signed(w[1], 24), word_addr(w[2]))

    def _scan_initialize(self, pm, block, windows=20):
        """Cari event Initialize pool token ini mundur dari transfer pertama ke PoolManager (jendela 10 blok)."""
        found = {}
        for k in range(windows):
            a, b = block - 10 * k - 9, block - 10 * k
            for pos in (2, 3):
                tp = [T_INIT, None, None, None]
                tp[pos] = "0x" + enc_addr(self.token)
                try:
                    logs = self.rpc.call("eth_getLogs", [{"address": pm, "topics": tp,
                                                          "fromBlock": hex(a), "toBlock": hex(b)}])
                except RPCError:
                    return found
                for lg in logs:
                    found[lg["topics"][1]] = self._init_key(lg)
            if found:
                break
        return found

    def _find_v3_pool(self):
        tok, cnt = self.token, collections.Counter()
        for x in self.ds["transfers"]:
            cnt[x[3]] += 1
            cnt[x[4]] += 1
        for a, _ in cnt.most_common(15):
            if a in (ZERO, tok):
                continue
            try:
                t0 = word_addr(words(self.call(a, "token0()"))[0])
                t1 = word_addr(words(self.call(a, "token1()"))[0])
                fee = words(self.call(a, "fee()"))[0]
            except (RPCError, IndexError):
                continue
            if tok in (t0, t1):
                return {"kind": "v3", "pool": a, "currency0": t0, "currency1": t1, "fee": fee}
        return None

    # ------------------------------------------------------------ kontrak vs wallet

    def classify(self):
        T, C = self.ds["transfers"], self.ds["contracts"]
        bal, peak, cnt = collections.Counter(), collections.Counter(), collections.Counter()
        for _, _, _, f, to, raw in T:
            bal[f] -= raw
            bal[to] += raw
            peak[to] = max(peak[to], bal[to])
            cnt[f] += 1
            cnt[to] += 1
        # Cukup alamat yang pernah >= 0.3% supply (batas whale) atau sangat aktif (router/bot): alamat
        # di bawah itu tidak pernah jadi whale, dan router yang hanya meneruskan token sudah ter-netting
        # dalam satu transaksi. Hasil eth_getCode disimpan bersama untuk semua token (router, bot MEV,
        # dan whale yang sama sering muncul lagi), jadi token berikutnya tidak membayar ulang.
        thr = self.ds["supply"] * 10 ** self.ds["decimals"] * 0.003
        cand = sorted((a for a in cnt if a not in C and a != ZERO and (peak[a] >= thr or cnt[a] >= 1000)),
                      key=lambda a: -peak[a])[:2000]
        shared = load_json(ADDR_CACHE, {})
        for a in cand:
            if a in shared:
                C[a] = shared[a]
        todo = [a for a in cand if a not in C]
        if cand:
            self.log(f"  wallet check: {len(cand) - len(todo)} from cache, {len(todo)} looked up")
        try:
            for i in range(0, len(todo), 400):
                chunk = todo[i:i + 400]
                for a, code in zip(chunk, self.rpc.batch([("eth_getCode", [a, "latest"]) for a in chunk])):
                    if isinstance(code, str):
                        C[a] = shared[a] = is_contract_code(code)
                self.log(f"  wallet check {min(i + 400, len(todo))}/{len(todo)}")
                self.checkpoint()
        finally:
            if todo:
                with _PATH_LOCKS_LOCK:
                    save_json(ADDR_CACHE, {**load_json(ADDR_CACHE, {}), **shared})
        for m in self.ds["markets"]:
            C[m] = True

    # ------------------------------------------------------------ harga

    def _block_at(self, t, T, tss):
        """Perkiraan nomor blok pada waktu t (interpolasi antar transfer)."""
        i = bisect.bisect_left(tss, t)
        if i == 0:
            return None
        if i >= len(tss):  # sesudah transfer terakhir: interpolasi ke blok terbaru
            b0, t0, b1, t1 = T[-1][0], tss[-1], self.latest, self.latest_ts
        else:
            b0, t0, b1, t1 = T[i - 1][0], tss[i - 1], T[i][0], tss[i]
        return int(b0 + (b1 - b0) * (t - t0) / max(1, t1 - t0))

    def _src_at(self, b):
        """Sumber harga untuk blok b: pool USDG sejak usdg_from_block, sebelumnya pool utama (`pre`)."""
        m = self.ds["market"]
        pre = m.get("pre")
        return pre if pre and b < (m.get("usdg_from_block") or 0) else m

    def _calls(self, src, b):
        m = self.ds["market"]
        if m["kind"] != "v4":
            return [("eth_call", [{"to": m["pool"], "data": calldata("slot0()")}, hex(b)])]
        slots = self.__dict__.setdefault("_slots", {})
        pools = [src["pool_id"]] + ([src["bridge"]["pool_id"]] if src.get("bridge") else [])
        return [("eth_getStorageAt", [m["manager"], slots.setdefault(p, hex(v4_state_slot(p))), hex(b)]) for p in pools]

    def _decode_price(self, src, r, rb=None):
        """Hasil storage/slot0 pool (dan pool konversi kalau ada) -> harga dalam USDG / unit quote."""
        if not isinstance(r, str) or r in ("0x", ""):
            return None
        v4 = self.ds["market"]["kind"] == "v4"
        sq = decode_v4_slot0(int(r, 16))[0] if v4 else words(r)[0]
        px = sqrt_to_price(sq, src["token_is_c1"], self.ds["decimals"], src["quote_decimals"])
        bridge = src.get("bridge")
        if px and bridge:
            sqb = decode_v4_slot0(int(rb, 16))[0] if isinstance(rb, str) and rb not in ("0x", "") else 0
            out_per_pair = sqrt_to_price(sqb, bridge["quote_is_c1"], bridge["pair_decimals"],
                                         bridge.get("out_decimals", 18))
            px = px * out_per_pair if out_per_pair else None
        return px

    def _read_prices(self, samples, prev=None):
        """[(t, blok)] urut waktu -> harga di storage pool pada blok itu, langsung digabung ke data.
        Sampel yang sejak sampel sebelumnya tidak ada transfer token ke/dari market dilewati (harga
        pool tidak berubah tanpa swap) dan memakai harga sebelumnya. Dibaca per potongan dan tiap
        potongan langsung disimpan, jadi kalau gagal di tengah tidak ada panggilan yang perlu diulang."""
        m = self.ds["market"]
        mk = set(self.ds["markets"]) | {m.get("pool") or m.get("manager")}
        mblocks = sorted(x[0] for x in self.ds["transfers"] if x[3] in mk or x[4] in mk)
        srcs = [self._src_at(b) for _, b in samples]
        quiet, prev_b = [], prev[1] if prev else None
        prev_s = self._src_at(prev_b) if prev else None
        for (_, b), src in zip(samples, srcs):
            # sumber dengan konversi: harga pasangan bisa berubah sendiri, jadi selalu dibaca
            quiet.append(not src.get("bridge") and src is prev_s and prev_b is not None
                         and bisect.bisect_right(mblocks, b) == bisect.bisect_right(mblocks, prev_b))
            prev_b, prev_s = b, src
        todo = [j for j, q in enumerate(quiet) if not q]
        if len(todo) < len(samples):
            self.log(f"  prices: {len(samples) - len(todo):,} samples without swaps skipped, {len(todo):,} read")
        per = 200
        parts = [todo[i:i + per] for i in range(0, len(todo), per)] or [[]]
        last, pos, added = (prev[2] if prev else None), 0, 0
        for n, part in enumerate(parts):
            if len(todo) > per:
                self.log(f"  prices {n * per:,}/{len(todo):,}")
            calls, spans = [], []
            for j in part:
                c = self._calls(srcs[j], samples[j][1])
                spans.append((len(calls), len(c)))
                calls += c
            res = self.rpc.batch(calls)
            got, failed = {}, None
            for (o, k), j in zip(spans, part):
                r = res[o:o + k]
                if any(isinstance(x, RPCError) and str(x) == "rate limited" for x in r):
                    failed = j  # masih kena rate limit sesudah GIVE_UP: berhenti, lanjut di pengecekan berikutnya
                    break
                # error lain (mis. state historis tidak tersedia): sampel ini dilewati saja
                got[j] = None if any(isinstance(x, RPCError) for x in r) else self._decode_price(srcs[j], *r)
            self._check_jumps(samples, srcs, got, last)
            end = failed if failed is not None else parts[n + 1][0] if n + 1 < len(parts) else len(samples)
            pts = []
            for j in range(pos, end):
                if not quiet[j]:
                    last = got[j]  # None kalau pool belum ada di blok itu
                if last:
                    pts.append([samples[j][0], samples[j][1], last])
            pos, added = end, added + len(pts)
            self._merge_prices(pts)
            self.checkpoint()
            if failed is not None:
                raise RPCError("prices: some calls kept getting rejected (rate limit); progress saved")
        return added

    def _check_jumps(self, samples, srcs, got, last):
        """Harga pertama, dan harga yang melompat >3x (atau jatuh <1/3) dari harga sebelumnya, dicek
        likuiditas pool-nya di blok itu. Pool kosong (likuiditas 0) harganya bisa di mana saja (mis. 1e50):
        diganti pool utama kalau ada, kalau tidak dilewati. Lompatan asli (likuiditas ada) tetap dipakai."""
        m = self.ds["market"]
        if m["kind"] != "v4":
            return
        ref, junk, empty = last, None, []
        for j in sorted(got):
            v = got[j]
            if not v or (ref and 1 / 3 <= v / ref <= 3):
                ref = v or ref
                continue
            if v != junk:  # nilai sampah yang sama persis = pool kosong yang belum berubah, tidak perlu dicek lagi
                src = srcs[j]
                pools = [src["pool_id"]] + ([src["bridge"]["pool_id"]] if src.get("bridge") else [])
                res = self.rpc.batch([("eth_getStorageAt", [m["manager"], hex(v4_state_slot(p) + 3),
                                                            hex(samples[j][1])]) for p in pools])
                if all(not isinstance(r, str) or r in ("0x", "") or int(r, 16) & (2 ** 128 - 1) for r in res):
                    ref = v  # semua pool yang dipakai punya likuiditas: lompatan asli
                    continue
            junk = v
            empty.append(j)
        if not empty:
            return
        alt = [j for j in empty if srcs[j] is m and m.get("pre")]
        calls, spans = [], []
        for j in alt:
            c = self._calls(m["pre"], samples[j][1])
            spans.append((j, len(calls), len(c)))
            calls += c
        res = self.rpc.batch(calls) if calls else []
        for j in empty:
            got[j] = None
        for j, o, k in spans:
            r = res[o:o + k]
            if not any(isinstance(x, RPCError) for x in r):
                got[j] = self._decode_price(m["pre"], *r)
        self.log(f"  prices: {len(empty)} samples from an empty pool replaced by the main pool / skipped")

    def _usdg_start(self, samples):
        """Blok mulai memakai pool USDG: sampel pertama di mana pool sudah ada (binary search, ±11
        panggilan) + USDG_GRACE supaya harga awal pool yang belum di-arbitrase tidak ikut."""
        m = self.ds["market"]
        slot = hex(v4_state_slot(m["pool_id"]))
        def exists(b):
            r = self.rpc.call("eth_getStorageAt", [m["manager"], slot, hex(b)])
            return r not in ("0x", "") and decode_v4_slot0(int(r, 16))[0]

        lo, hi = 0, len(samples) - 1
        while lo < hi:
            mid = (lo + hi) // 2
            if exists(samples[mid][1]):
                hi = mid
            else:
                lo = mid + 1
        if lo == 0:
            return 0  # pool USDG sudah ada sejak awal riwayat
        t = samples[lo][0] + USDG_GRACE
        k = bisect.bisect_left([x[0] for x in samples], t)
        return samples[k][1] if k < len(samples) else samples[-1][1]

    def _merge_prices(self, pts):
        P = self.ds["prices"]
        if not pts:
            return
        if not P or pts[0][0] > P[-1][0]:
            P.extend(pts)  # lanjutan di ujung (kasus biasa)
            return
        merged = {p[0]: p for p in P}
        merged.update({p[0]: p for p in pts})
        P[:] = [merged[t] for t in sorted(merged)]

    def sample_prices(self, step: int, full=False):
        """full=True: resolusi `step` di seluruh riwayat (untuk token pembanding/referensi)."""
        m, T, P = self.ds["market"], self.ds["transfers"], self.ds["prices"]
        if not m or not T:
            return
        tss = [x[1] for x in T]
        t = P[-1][0] + step if P else (tss[0] // step + 1) * step
        fine_from, mid_from = self.latest_ts - FINE_DAYS * 86400, self.latest_ts - MID_DAYS * 86400
        if full:
            fine_from = 0
        samples = []
        while t < self.latest_ts:
            b = self._block_at(t, T, tss)
            if b is not None:
                samples.append((t, b))
            t += step if t >= fine_from else max(step, MID_STEP if t >= mid_from else COARSE_STEP)
        samples.append((self.latest_ts, self.latest))
        if m.get("usd") and m.get("usdg_from_block") is None:
            m["usdg_from_block"] = self._usdg_start(samples) if m.get("pre") else 0
            if m["usdg_from_block"]:
                self.log(f"  USDG pool used from block {m['usdg_from_block']:,}; before that the main pool "
                         f"(converted to USDG)")
        added = self._read_prices(samples, P[-1] if P else None)
        # sampel jarang bisa melewatkan spike (ATH): sampel ulang rapat di sekitar 3 puncak tertinggi
        # riwayat lama yang belum rapat. Puncak yang sudah dirapatkan tidak dibaca ulang di cek berikutnya.
        ts = [p[0] for p in P]
        have, extra = set(ts), set()
        for i in sorted((i for i in range(len(P)) if ts[i] < fine_from), key=lambda i: -P[i][2])[:3]:
            gap = max(ts[i] - ts[i - 1] if i else 0, ts[i + 1] - ts[i] if i + 1 < len(ts) else 0)
            if gap <= step:
                continue
            for tt in range(ts[i] - gap + step, ts[i] + gap, step):
                b = self._block_at(tt, T, tss) if tt not in have else None
                if b is not None:
                    extra.add((tt, b))
        if extra:
            added += self._read_prices(sorted(extra))
        self.log(f"prices: {len(P)} samples (+{added})")

    def save(self):
        save_json(self.path, self.ds)
        self._saved = time.time()


def main():
    ap = argparse.ArgumentParser(description="Tarik data token Robinhood Chain untuk deteksi pola pra-dump")
    ap.add_argument("--token", required=True, action="append", help="alamat kontrak token (boleh diulang)")
    ap.add_argument("--rpc-url", help="default: Alchemy robinhood-mainnet dengan ALCHEMY_API_KEY")
    ap.add_argument("--step", type=int, default=300, help="interval sampel harga, detik (default 300)")
    ap.add_argument("--full", action="store_true",
                    help="sampel harga resolusi penuh di seluruh riwayat (untuk token pembanding)")
    ap.add_argument("--market", action="append", default=[],
                    help="alamat market tambahan, mis. kontrak bonding curve (boleh diulang)")
    args = ap.parse_args()
    rpc = make_rpc(args.rpc_url)
    for tok in args.token:
        path = data_path(tok)
        Fetcher(rpc, tok, path).run(args.step, args.market, args.full)
        log(f"saved: {path} (RPC requests: {rpc.requests}, ±{rpc.cu:,} CU)")
    if len(rpc.lanes) > 1:
        log("key: " + ", ".join(f"#{i} {st} ({cu:,} CU)" for i, st, cu in rpc.status()))


if __name__ == "__main__":
    main()
