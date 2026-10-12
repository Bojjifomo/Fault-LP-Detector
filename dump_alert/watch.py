"""
Pantau token selama posisi LP terbuka dan kirim alert saat vonis lp_check memburuk
(mis. whale mulai net jual atau harga mulai jatuh tajam) — sinyal untuk bangun dan tarik LP.

  ALCHEMY_API_KEYS=k1,k2 python -m dump_alert.watch --token 0xA --token 0xB [--interval 300]
  python -m dump_alert.watch --token 0xA --once      # sekali cek (untuk cron)

Telegram (opsional): set TELEGRAM_BOT_TOKEN dan TELEGRAM_CHAT_ID.
Token baru pertama kali ditarik penuh (bisa beberapa menit); sesudahnya inkremental.
"""
import argparse
import json
import os
import time

import requests

from .fetch import Fetcher, data_path, log, make_rpc
from .lp_check import FILTERS, STEP, VERDICTS, History, evaluate, features
from .signals import Token, launch_risk


def notify(text: str):
    print(text, flush=True)
    bot, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if bot and chat:
        try:
            requests.post(f"https://api.telegram.org/bot{bot}/sendMessage",
                          json={"chat_id": chat, "text": text}, timeout=20)
        except requests.RequestException as e:
            log(f"gagal kirim Telegram: {type(e).__name__}")


def check(rpc, address: str, sleep_h: float, state: dict):
    path = data_path(address)
    new = not os.path.exists(path)
    tok = Token(Fetcher(rpc, address, path).run(STEP, liquidity=False))
    flags = launch_risk(tok).get("flags", [])
    if new and flags:
        notify(f"[RISIKO LAUNCH] {tok.symbol} {tok.address}\n" + "\n".join(f"• {f}" for f in flags))
    rows = features(tok, start=tok._pt[-1] - 2 * STEP) if tok._pt else []
    if not rows:
        log(f"{tok.symbol}: belum ada data harga")
        return
    f = rows[-1]
    level, hits, detail = evaluate(f, History(tok, sleep_h).rate(f["t"]), flags)
    keras = [c for c, k, _ in FILTERS if k and hits[c]]
    prev = state.get(address, {"level": 0, "keras": []})
    if level > prev["level"] or (level == 2 and not set(keras) <= set(prev["keras"])):
        fdv = f["px"] * tok.supply
        lines = [f"[{VERDICTS[level]}] {tok.symbol} {tok.address}",
                 time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(f["t"])) + f" · FDV {fdv:,.1f} {tok.quote}"]
        lines += [f"• {code} {desc}: {detail[code]}" for code, k, desc in FILTERS if hits[code] and code != "L5"]
        if level == 2:
            lines.append("→ pertimbangkan tarik LP sekarang")
        notify("\n".join(lines))
    elif level < prev["level"] and level == 0:
        notify(f"[REDA] {tok.symbol}: vonis kembali LAYAK")
    log(f"{tok.symbol}: {VERDICTS[level]} {','.join(c for c, _, _ in FILTERS if hits[c]) or '-'}")
    state[address] = {"level": level, "keras": keras}


def main():
    ap = argparse.ArgumentParser(description="Alert risiko dump untuk posisi LP token Robinhood Chain")
    ap.add_argument("--token", required=True, action="append", help="alamat kontrak token (boleh diulang)")
    ap.add_argument("--interval", type=int, default=300, help="detik antar cek (default 300)")
    ap.add_argument("--sleep", type=float, default=8, help="jendela risiko untuk statistik riwayat, jam (default 8)")
    ap.add_argument("--once", action="store_true", help="cek sekali lalu keluar")
    ap.add_argument("--rpc-url")
    args = ap.parse_args()
    rpc = make_rpc(args.rpc_url)
    state_path = os.path.join(os.path.dirname(data_path("x")), "watch_state.json")
    state = json.load(open(state_path)) if os.path.exists(state_path) else {}
    while True:
        for a in args.token:
            try:
                check(rpc, a.lower(), args.sleep, state)
            except Exception as e:  # satu token gagal jangan hentikan pemantauan token lain
                log(f"{a}: gagal cek ({type(e).__name__}: {rpc._redact(e)[:200]})")
        with open(state_path, "w") as f:
            json.dump(state, f)
        if args.once:
            break
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
