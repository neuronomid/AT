# MT5 Chart Replay Workspace

`ATChartReplayWorkspaceEA.mq5` creates a TradingView-style replay workspace on a normal MT5 chart.

This is not the Strategy Tester visualizer.
It creates a custom symbol, seeds historical context before your chosen start point, and then replays forward on a regular chart window so MT5 drawing tools remain available.

## File

- EA: `ops/mt5/ATChartReplayWorkspaceEA.mq5`

## What It Does

- creates a custom replay symbol derived from a real source symbol
- jumps the current chart onto that replay symbol
- replays from `InpReplayStartTime` to `InpReplayEndTime`
- supports `BAR` stepping and `TICK` stepping
- supports `play`, `pause`, `step`, `reset`, and speed changes
- shows a compact corner control panel with replay, order, and position sections
- keeps the chart as a normal MT5 chart, so native drawing tools still work
- simulates manual market trades on the replay chart
- tracks closed trades for the current replay session and can open a separate session report window

## Current Limits

- simulated trading only, not broker execution
- market entries only in this first version
- switching between `BAR` and `TICK` mode resets the replay
- `TICK` mode depends on real historical ticks being available in MT5

## Install

Copy `ATChartReplayWorkspaceEA.mq5` into:

`/Users/omid/Library/Application Support/net.metaquotes.wine.metatrader5/drive_c/Program Files/MetaTrader 5/MQL5/Experts/Advisors/`

Then compile it in MT5 MetaEditor.

## Use

1. Open a normal chart for the source symbol you want to replay.
2. Attach `ATChartReplayWorkspaceEA`.
3. Set:
   - `InpSourceSymbol`
   - `InpReplayPeriod`
   - `InpReplayStartTime`
   - `InpReplayEndTime`
   - `InpWarmupMinutes`
4. Click `OK`.
5. The EA creates a replay symbol like `ATR_BTCUSD_replay1` and switches the current chart to it.
6. Use the panel buttons or hotkeys.

## Hotkeys

- `Space`: step once
- `Enter`: play or pause
- `M`: toggle mode and reset
- `R`: reset replay
- `B`: simulated buy
- `S`: simulated sell
- `C`: close simulated position
- `+`: faster
- `-`: slower

## Buttons

- `PLAY`: toggle playback
- `BACK`: move backward one replay unit in the current mode
- `STEP`: advance one unit in the current mode
- `RESET`: go back to the selected replay start point
- `MODE`: switch between `BAR` and `TICK` and reset
- `REPORT`: opens or refreshes a separate MT5 chart window with the current session report
- `VOL-`, `VOL+`: decrease or increase the staged lot size for the next trade
- `S-`, `S+`: slow down or speed up playback
- `BUY`, `SELL`, `CLOSE`: simulated trade actions
- drag the plotted `SL` and `TP` lines on the chart to adjust protection for the staged order or open simulated position
- the replay view expands to keep entry, SL, and TP levels visible while a simulated trade is active

## Drawing

Because replay runs on a normal chart:

- trendlines
- rectangles
- horizontal levels
- arrows
- text notes

all use MT5’s normal drawing tools.

## Recommended Workflow

1. Set your replay start and end times.
2. Let the EA switch the chart to the replay symbol.
3. Pause immediately if you want a clean starting state.
4. Draw your levels.
5. Use `Space` for manual stepping or `Enter` for low-speed playback.
6. Use `BUY`, `SELL`, and `CLOSE` for simulated practice trades.
