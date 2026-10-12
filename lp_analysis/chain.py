"""
Helper bersama: klien JSON-RPC (batch + retry), codec ABI minimal, dan
matematika Uniswap v3/v4 untuk menilai posisi LP. Tanpa dependensi web3.
"""
import time

import requests
from Crypto.Hash import keccak

CHAIN_ID = 4663  # Robinhood Chain mainnet

# Deployment Uniswap di Robinhood Chain (developers.uniswap.org).
# fetch.py mencocokkan alamat ini satu sama lain on-chain sebelum dipakai.
V3_NPM = "0x73991a25c818bf1f1128deaab1492d45638de0d3"
V3_FACTORY = "0x1f7d7550b1b028f7571e69a784071f0205fd2efa"
V4_POSM = "0x58daec3116aae6d93017baaea7749052e8a04fa7"

ZERO = "0x" + "0" * 40
Q96 = 2 ** 96
Q128 = 2 ** 128
MAX_U128 = 2 ** 128 - 1
FULL_RANGE_TICK = 880000  # |tick| di atas ini praktis full range


# ---------------------------------------------------------------- hashing / ABI

def keccak_bytes(data: bytes) -> bytes:
    h = keccak.new(digest_bits=256)
    h.update(data)
    return h.digest()


def selector(signature: str) -> str:
    return "0x" + keccak_bytes(signature.encode()).hex()[:8]


def topic(signature: str) -> str:
    return "0x" + keccak_bytes(signature.encode()).hex()


T_TRANSFER = topic("Transfer(address,address,uint256)")
T_INCREASE = topic("IncreaseLiquidity(uint256,uint128,uint256,uint256)")
T_DECREASE = topic("DecreaseLiquidity(uint256,uint128,uint256,uint256)")
T_COLLECT = topic("Collect(uint256,address,uint256,uint256)")
T_MODIFY = topic("ModifyLiquidity(bytes32,address,int24,int24,int256,bytes32)")


def enc_int(v: int) -> str:
    return f"{v % 2 ** 256:064x}"


def enc_addr(a: str) -> str:
    return a.lower().removeprefix("0x").rjust(64, "0")


def calldata(signature: str, *words_hex: str) -> str:
    return selector(signature) + "".join(words_hex)


def words(data: str) -> list[int]:
    d = data.removeprefix("0x")
    return [int(d[i:i + 64], 16) for i in range(0, len(d), 64)]


def signed(v: int, bits: int) -> int:
    v &= (1 << bits) - 1
    return v - (1 << bits) if v >> (bits - 1) else v


def word_addr(v: int) -> str:
    return "0x" + f"{v & (2 ** 160 - 1):040x}"


def dec_string(data: str) -> str:
    """string ABI biasa atau bytes32 (token lama seperti MKR)."""
    d = bytes.fromhex(data.removeprefix("0x"))
    if len(d) == 32:
        return d.rstrip(b"\0").decode(errors="replace")
    if len(d) >= 64:
        off = int.from_bytes(d[:32], "big")
        n = int.from_bytes(d[off:off + 32], "big")
        return d[off + 32:off + 32 + n].decode(errors="replace")
    return ""


# ---------------------------------------------------------------- Uniswap math

def tick_to_sqrt(tick: int) -> float:
    """sqrt(harga mentah token1/token0) pada sebuah tick."""
    return 1.0001 ** (tick / 2)


def sqrt_x96_to_tick(sqrt_x96: int) -> int:
    import math
    p = (sqrt_x96 / Q96) ** 2
    return math.floor(math.log(p) / math.log(1.0001)) if p > 0 else -FULL_RANGE_TICK


def amounts_for_liquidity(liq: float, sqrt_p: float, sqrt_a: float, sqrt_b: float):
    """Jumlah token0/token1 (unit mentah) untuk likuiditas `liq` pada harga sqrt_p."""
    if sqrt_a > sqrt_b:
        sqrt_a, sqrt_b = sqrt_b, sqrt_a
    if sqrt_p <= sqrt_a:
        return liq * (sqrt_b - sqrt_a) / (sqrt_a * sqrt_b), 0.0
    if sqrt_p >= sqrt_b:
        return 0.0, liq * (sqrt_b - sqrt_a)
    return liq * (sqrt_b - sqrt_p) / (sqrt_p * sqrt_b), liq * (sqrt_p - sqrt_a)


def v4_pool_id(c0: str, c1: str, fee: int, tick_spacing: int, hooks: str) -> str:
    raw = bytes.fromhex(enc_addr(c0) + enc_addr(c1) + enc_int(fee)
                        + enc_int(tick_spacing) + enc_addr(hooks))
    return "0x" + keccak_bytes(raw).hex()


# Layout storage PoolManager v4 (sama dengan StateLibrary resmi).
V4_POOLS_SLOT = 6


def _slot(key: bytes, base: int) -> int:
    return int.from_bytes(keccak_bytes(key + base.to_bytes(32, "big")), "big")


def v4_state_slot(pool_id: str) -> int:
    return _slot(bytes.fromhex(pool_id.removeprefix("0x")), V4_POOLS_SLOT)


def v4_tick_slot(state: int, tick: int) -> int:
    return _slot(bytes.fromhex(enc_int(tick)), state + 4)


def v4_position_slot(state: int, owner: str, tick_lower: int, tick_upper: int, salt: int) -> int:
    key = (bytes.fromhex(owner.removeprefix("0x"))
           + (tick_lower % 2 ** 24).to_bytes(3, "big")
           + (tick_upper % 2 ** 24).to_bytes(3, "big")
           + salt.to_bytes(32, "big"))
    return _slot(keccak_bytes(key), state + 6)


def decode_v4_slot0(word: int):
    return word & (2 ** 160 - 1), signed((word >> 160) & 0xFFFFFF, 24)


def fee_growth_inside(tick, lower, upper, g, out_lower, out_upper) -> int:
    below = out_lower if tick >= lower else (g - out_lower)
    above = out_upper if tick < upper else (g - out_upper)
    return (g - below - above) % 2 ** 256


# ---------------------------------------------------------------- JSON-RPC

class RPCError(Exception):
    pass


class LogsUnavailable(Exception):
    pass


def _rate_limited(err) -> bool:
    s = str(err).lower()
    return "429" in s or "rate" in s or "compute units" in s or "capacity" in s


class RPC:
    def __init__(self, url: str, batch_size: int = 40, headers: dict | None = None):
        self.url = url
        self.batch_size = batch_size
        self.session = requests.Session()
        if headers:
            self.session.headers.update(headers)
        self.requests = 0
        # API key Alchemy ada di path URL; jangan sampai bocor lewat pesan error
        self._secret = url.rsplit("/v2/", 1)[1].strip("/") if "/v2/" in url else ""

    def _redact(self, msg) -> str:
        msg = str(msg)
        return msg.replace(self._secret, "***") if self._secret else msg

    def _post(self, payload):
        delay, err = 1.0, None
        for _ in range(8):
            self.requests += 1
            try:
                r = self.session.post(self.url, json=payload, timeout=90)
            except requests.RequestException as e:
                err = e
            else:
                if r.status_code == 429 or r.status_code >= 500:
                    err = RPCError(f"HTTP {r.status_code}: {r.text[:200]}")
                elif r.status_code != 200:
                    raise RPCError(self._redact(f"HTTP {r.status_code}: {r.text[:300]}"))
                else:
                    return r.json()
            time.sleep(delay)
            delay = min(delay * 2, 30)
        raise RPCError(self._redact(f"gagal setelah retry: {err}"))

    def call(self, method: str, params: list):
        resp = self._post({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        if "error" in resp:
            raise RPCError(f"{method}: {resp['error']}")
        return resp["result"]

    def batch(self, calls: list[tuple[str, list]]) -> list:
        """Hasil per call; call yang gagal menjadi instance RPCError (tidak di-raise)."""
        results: list = [None] * len(calls)
        pending = list(range(len(calls)))
        delay = 1.0
        for _ in range(6):
            retry = []
            for i in range(0, len(pending), self.batch_size):
                idx = pending[i:i + self.batch_size]
                payload = [{"jsonrpc": "2.0", "id": k, "method": calls[k][0],
                            "params": calls[k][1]} for k in idx]
                resp = self._post(payload)
                if isinstance(resp, dict):
                    if _rate_limited(resp.get("error")):
                        retry += idx
                        continue
                    raise RPCError(str(resp.get("error", resp)))
                by_id = {r.get("id"): r for r in resp}
                for k in idx:
                    r = by_id.get(k)
                    if r is None or ("error" in r and _rate_limited(r["error"])):
                        retry.append(k)
                    elif "error" in r:
                        results[k] = RPCError(str(r["error"]))
                    else:
                        results[k] = r["result"]
            if not retry:
                return results
            pending = retry
            time.sleep(delay)
            delay = min(delay * 2, 30)
        for k in pending:
            results[k] = RPCError("rate limited")
        return results

    def get_logs(self, flt: dict, start: int, end: int, max_requests: int = 400) -> list:
        """eth_getLogs dengan pemecahan rentang blok otomatis kalau provider menolak."""
        out, stack, used = [], [(start, end)], 0
        while stack:
            a, b = stack.pop()
            used += 1
            if used > max_requests:
                raise LogsUnavailable("terlalu banyak pemecahan eth_getLogs")
            try:
                out += self.call("eth_getLogs", [{**flt, "fromBlock": hex(a), "toBlock": hex(b)}])
            except RPCError as e:
                if "free tier" in str(e).lower() or b - a < 1000:
                    raise LogsUnavailable(str(e)) from e
                mid = (a + b) // 2
                stack += [(mid + 1, b), (a, mid)]
        return out
