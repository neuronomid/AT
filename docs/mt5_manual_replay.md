# MT5 Manual Replay

This path is for personal backtest practice in MT5 Strategy Tester visual mode.

It does not use the HTTP bridge EAs.
It uses a tester-safe shared-file channel because MT5 Strategy Tester does not expose the same in-chart interaction hooks as a live chart.

## Files

- tester EA: `ops/mt5/ATManualReplayTesterEA.mq5`
- controller CLI: `src/app/mt5_manual_replay.py`
- helper launcher: `scripts/run_mt5_manual_replay.sh`

## Workflow

1. Copy `ops/mt5/ATManualReplayTesterEA.mq5` into your MT5 Experts folder and compile it.
2. Open MT5 Strategy Tester in visual mode for the symbol and date range you want to practice.
3. Attach `ATManualReplayTesterEA` to the tester run.
4. Set `InpEnableOrderExecution=true` and choose a session id such as `btc-practice-1`.
5. In the repo terminal, initialize the same session:

```bash
scripts/run_mt5_manual_replay.sh --session btc-practice-1 init --reset
```

6. Send practice orders from the terminal while the tester is running:

```bash
scripts/run_mt5_manual_replay.sh --session btc-practice-1 buy 0.10 --sl-points 300 --tp-points 600
scripts/run_mt5_manual_replay.sh --session btc-practice-1 sell 0.10
scripts/run_mt5_manual_replay.sh --session btc-practice-1 buy-limit 0.10 84000 --sl-points 250 --tp-points 500
scripts/run_mt5_manual_replay.sh --session btc-practice-1 protect-ticket 123456 --sl-price 83820 --tp-price 84550
scripts/run_mt5_manual_replay.sh --session btc-practice-1 close-ticket 123456
scripts/run_mt5_manual_replay.sh --session btc-practice-1 flatten
```

7. Watch execution feedback:

```bash
scripts/run_mt5_manual_replay.sh --session btc-practice-1 tail-acks --follow
scripts/run_mt5_manual_replay.sh --session btc-practice-1 status
```

## Notes

- Leave `--symbol` blank unless you want an explicit symbol filter.
- The controller auto-detects the MT5 `Common/Files` folder on the current macOS/Wine setup.
- If auto-detection misses your terminal, pass `--common-dir`.
- `ATManualOrderPadEA.mq5` is still the live/demo chart-side order pad.
  `ATManualReplayTesterEA.mq5` is the tester-safe variant for historical replay practice.
