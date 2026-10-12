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
- **Liquidity & support**: the real Uniswap v4 tick map below the price (selling needed to push price down
  10/30/50%), liquidity walls and gaps, plus swing-low supports tested on the sample coins.
- **Recommended LP range**: lower bound from historical drops / just below a support, upper bound from historical
  rallies, and the chance price stays inside while you sleep.

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
