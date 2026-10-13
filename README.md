# Fault LP Detector

Check a Robinhood Chain memecoin **before you open a Uniswap LP and go to sleep**. Paste a contract address and
the tool pulls the coin's on-chain history, then tells you whether it looks safe to LP (**OK / CAUTION / AVOID**),
how much selling the pools can absorb, where the supports are, and which LP range makes sense from the current
price.

What it looks at:
- **USDG pool focus**: prices and ranges come from the coin's deepest token/USDG pool (where you LP).
- **Pre-crash fingerprints**: the coin is compared with how the reference coins looked right before their crashes.
- **Similar past moments**: every hour of history in the sample coins is matched on pattern, price action, whales,
  technicals and coin profile, and you see what happened in the next 8 hours.
- **Whales & holders**: whale net selling/buying (LP adds/removals are filtered out), new big wallets, supply held
  by holders in big profit, sniper/insider bags.
- **Liquidity & support**: the real Uniswap v4 tick map from +200% to −95% of the price, shown as a vertical
  liquidity profile (like VPVR) next to the volume traded at each price, plus liquidity walls, gaps and swing-low
  supports tested on the sample coins.
- **Single-sided USDG by default**: most people here LP with only USDG, from today's price (0%) down to a lower
  bound (e.g. 0% to -65%). The upper bound is then simply today's price, so the tool picks only the lower bound: the
  closest one the price did not fall to during the night in at least 90% of similar nights, plus 5–10% deeper, and
  compares it with -60/-65/-70% and a ladder from -20% to -90%. The exit is automatic (the LP sells coin bought on a
  dip back into USDG when the price returns); floors inside the range show where a bounce may stall. Two-sided
  (USDG + coin) is one click away.
- **Exit plan for an open position**: enter the market cap you opened at (plus your lower bound) and get where the
  position stands and one answer — hold (it exits by itself at the top of your range), take profit by hand just under
  a strong resistance, or close — plus a price ladder (top of range, take-profit level, now, close level, bottom) with
  your LP's dollar value at each level and how often the price got there in a night like this. The close level is a
  strong floor that breaks on 1 in 4 nights or fewer, so a normal dip does not trigger it. Resistance now includes old floors the price fell through (tested: 2+ broken floors held ~45% vs 28% random).
- **Safe LP range (two-sided)**: the tightest range that stayed in range for the whole night in at least 90% (80/95% selectable)
  of similar nights — from the coin's own history and similar moments in the sample coins — plus a 5–10% safety
  margin, compared with your usual lower bounds (-60/-65/-70%). Results in dollars include fees from real volume ×
  the pool's live fee level, diluted by your position size; it tells you to wait when the fees don't pay.
- **Where to take profit (two-sided)**: strong resistance above the price (past rejections, holders at break-even, sell walls in
  the pools, busy prices), tested on the sample coins, with a suggested upper bound for an automatic exit and a
  momentum check (overbought signals tend to break resistance, not reverse it).
- **Top holders & money flow**: the 10 biggest wallets (share, average buy price, profit multiple, last move) and the
  buys/sells of the last 1/4/24 hours, plus how far the price usually moves while you sleep.
- **One clear answer at the top** (open / risky / don't open / low risk but fees too low) in plain English, results in
  dollars for your position size, and a conclusion under every analysis.

## Run it

```bash
pip install -r dump_alert/requirements.txt
export ALCHEMY_API_KEYS=key1,key2      # one or more Alchemy keys (or paste them in the sidebar)
streamlit run dump_alert/streamlit_app.py
```

Streamlit Community Cloud: main file `dump_alert/streamlit_app.py`, secrets
`ALCHEMY_API_KEYS = ["key-1", "key-2"]`. Keep the app private, otherwise visitors spend your Alchemy quota.

Command line: `python -m dump_alert.lp_check 0xCA [--range 20] [--sleep 8]`, alarm via Telegram:
`python -m dump_alert.watch --token 0xCA`. Offline logic check: `python -m dump_alert.selftest`.

Full documentation (Indonesian): [dump_alert/README.md](dump_alert/README.md).

Not financial advice: an OK verdict does not mean safe — coins can crash without warning.
