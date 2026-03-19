# MT5 Indicators

`ATCandleCountdown.mq5` is a chart-window MT5 indicator that shows the remaining lifetime of the active candle for the chart's current timeframe.

Design choices:

- refreshes once per second with `EventSetTimer(1)` so the countdown keeps moving between ticks
- reads the active bar's open time from the chart series with `CopyTime(..., 0, 1, ...)`
- counts down against `TimeTradeServer()` first, with `TimeCurrent()` as a fallback when the trade-server clock is unavailable
- uses chart objects instead of `Comment()` so the UI stays self-contained and does not fight with other tools on the chart
- handles `PERIOD_MN1` with a calendar-month rollover instead of assuming every month is exactly 30 days

Suggested install target on your MT5 setup:

`/Users/omid/Library/Application Support/net.metaquotes.wine.metatrader5/drive_c/Program Files/MetaTrader 5/MQL5/Indicators/AT/ATCandleCountdown.mq5`
