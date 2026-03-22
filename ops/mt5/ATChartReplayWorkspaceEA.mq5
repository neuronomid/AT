#property strict
#property version   "1.00"
#property description "AT custom-symbol chart replay workspace for manual practice."
#property description "Uses a normal MT5 chart so native drawing tools remain available."

#include <Canvas\Canvas.mqh>

input string InpWorkspaceId = "replay1";
input string InpSourceSymbol = "";
input double InpReplayStartingBalance = 10000.0;
input ENUM_TIMEFRAMES InpReplayPeriod = PERIOD_M5;
input datetime InpReplayStartTime = D'2026.03.20 09:30:00';
input datetime InpReplayEndTime = D'2026.03.20 16:00:00';
input int InpWarmupMinutes = 240;
input bool InpUseRealTicks = false;
input bool InpStartPaused = true;
input int InpTimerIntervalSeconds = 1;
input int InpPlaybackUnitsPerTimer = 1;

input double InpTradeVolumeLots = 0.10;
input int InpTradeStopLossPoints = 0;
input int InpTradeTakeProfitPoints = 0;
input double InpDefaultProtectionPips = 4.0;
input int InpInteractiveProtectionPoints = 250;
input int InpPendingEntryOffsetPoints = 120;

input ENUM_BASE_CORNER InpPanelCorner = CORNER_RIGHT_LOWER;
input int InpXOffset = 14;
input int InpYOffset = 18;
input int InpPanelWidth = 572;
input int InpPanelHeight = 226;
input string InpUiFont = "Verdana";
input int InpTitleFontSize = 11;
input int InpMetaFontSize = 9;
input int InpButtonFontSize = 9;
input color InpPanelColor = C'21,30,45';
input color InpBorderColor = C'76,98,128';
input color InpAccentColor = C'36,184,240';
input color InpTextColor = clrWhite;
input color InpMetaColor = C'188,199,215';
input color InpWarnColor = C'255,184,77';
input color InpGoodColor = C'28,134,88';
input color InpBadColor = C'176,58,72';
input color InpNeutralButtonColor = C'45,61,84';

enum ATReplayStepMode
{
   REPLAY_MODE_BAR = 0,
   REPLAY_MODE_TICK = 1
};

enum ATSimPendingType
{
   PENDING_NONE = 0,
   PENDING_BUY_LIMIT = 1,
   PENDING_SELL_LIMIT = 2,
   PENDING_BUY_STOP = 3,
   PENDING_SELL_STOP = 4
};

struct ATSimPosition
{
   bool open;
   int side;
   double volume_lots;
   double entry_price;
   double stop_loss;
   double take_profit;
   datetime opened_at;
};

struct ATSimPendingOrder
{
   bool active;
   ATSimPendingType type;
   double volume_lots;
   double entry_price;
   double stop_loss;
   double take_profit;
   datetime created_at;
};

struct ATSimClosedTrade
{
   int side;
   double volume_lots;
   datetime opened_at;
   datetime closed_at;
   double entry_price;
   double exit_price;
   double stop_loss;
   double take_profit;
   double pnl;
   double balance_after;
   string exit_reason;
};

struct ATSessionReportStats
{
   int total_trades;
   int wins;
   int losses;
   int long_trades;
   int short_trades;
   double gross_profit;
   double gross_loss;
   double net_profit;
   double win_rate;
   double profit_factor;
   double avg_win;
   double avg_loss;
   double best_trade;
   double worst_trade;
   double ending_balance;
   double current_equity;
   double peak_balance;
   double max_drawdown;
};

struct ATReplaySnapshot
{
   ATReplayStepMode step_mode;
   int next_m1_index;
   int next_tick_index;
   datetime last_replay_time;
   double last_bid;
   double last_ask;
   double realized_pnl;
   int closed_trade_count;
   ATSimPosition position;
   ATSimPendingOrder pending_order;
};

long g_chart_id = 0;
string g_prefix = "";
string g_source_symbol = "";
string g_custom_symbol = "";
ENUM_BASE_CORNER g_panel_corner = CORNER_RIGHT_LOWER;
int g_panel_x_offset = 14;
int g_panel_y_offset = 18;
ATReplayStepMode g_step_mode = REPLAY_MODE_BAR;
bool g_is_ready = false;
bool g_is_playing = false;
int g_playback_units = 1;
bool g_has_real_ticks = false;
bool g_using_synthetic_ticks = false;
bool g_workspace_seeded = false;
datetime g_replay_start_minute = 0;
datetime g_last_replay_time = 0;
double g_last_bid = 0.0;
double g_last_ask = 0.0;
double g_realized_pnl = 0.0;
double g_simulated_balance_start = 0.0;
bool g_viewport_fixed = false;
double g_viewport_min = 0.0;
double g_viewport_max = 0.0;
ATSimPosition g_position = {false, 0, 0.0, 0.0, 0.0, 0.0, 0};
ATSimPendingOrder g_pending_order = {false, PENDING_NONE, 0.0, 0.0, 0.0, 0.0, 0};

MqlRates g_source_m1[];
MqlTick g_source_ticks[];
ATSimClosedTrade g_closed_trades[];
ATReplaySnapshot g_snapshots[];
int g_first_replay_m1_index = 0;
int g_next_m1_index = 0;
int g_next_tick_index = 0;
int g_snapshot_count = 0;
int g_snapshot_cursor = -1;
int g_closed_trade_count = 0;
double g_trade_volume_lots = 0.0;
long g_report_chart_id = 0;
CCanvas g_report_graph_canvas;

string g_panel_name = "";
string g_accent_name = "";
string g_orders_heading_name = "";
string g_stats_heading_name = "";
string g_replay_heading_name = "";
string g_balance_name = "";
string g_volume_edit_name = "";
string g_separator_left_name = "";
string g_separator_right_name = "";
string g_title_name = "";
string g_meta_name = "";
string g_status_name = "";
string g_quote_name = "";
string g_mode_name = "";
string g_position_name = "";
string g_volume_name = "";
string g_hotkeys_name = "";
string g_report_button_name = "";
string g_play_button_name = "";
string g_back_button_name = "";
string g_step_button_name = "";
string g_reset_button_name = "";
string g_mode_button_name = "";
string g_volume_down_button_name = "";
string g_volume_up_button_name = "";
string g_speed_down_button_name = "";
string g_speed_up_button_name = "";
string g_buy_button_name = "";
string g_sell_button_name = "";
string g_close_button_name = "";
string g_buy_limit_button_name = "";
string g_sell_limit_button_name = "";
string g_buy_stop_button_name = "";
string g_sell_stop_button_name = "";
string g_entry_line_name = "";
string g_stop_line_name = "";
string g_take_line_name = "";
string g_entry_tag_name = "";
string g_stop_tag_name = "";
string g_take_tag_name = "";
string g_floating_tag_name = "";

ENUM_TIMEFRAMES ReplayBarPeriod()
{
   if(InpReplayPeriod > PERIOD_CURRENT)
      return(InpReplayPeriod);

   ENUM_TIMEFRAMES chart_period = (ENUM_TIMEFRAMES)Period();
   if(chart_period > PERIOD_CURRENT)
      return(chart_period);

   return(PERIOD_M1);
}

bool WaitForVisibleCustomHistory(const int max_attempts, const int sleep_millis)
{
   if(g_first_replay_m1_index <= 0)
      return(true);

   MqlRates probe[];
   ArraySetAsSeries(probe, false);

   for(int attempt = 0; attempt < max_attempts; attempt++)
   {
      ResetLastError();
      if(CopyRates(g_custom_symbol, PERIOD_M1, 0, 2, probe) > 0)
         return(true);

      if((attempt + 1) < max_attempts)
         Sleep(MathMax(sleep_millis, 1));
   }

   return(false);
}

bool ReplayConfigurationMatchesCurrentInputs()
{
   string expected = BuildReplayDescription(g_source_symbol, InpWorkspaceId);
   string current = SymbolInfoString(g_custom_symbol, SYMBOL_DESCRIPTION);
   return(current == expected);
}

string ReplayPeriodText()
{
   ENUM_TIMEFRAMES replay_period = ReplayBarPeriod();
   ENUM_TIMEFRAMES chart_period = (ENUM_TIMEFRAMES)Period();
   string period_text = EnumToString(replay_period);

   if(chart_period > PERIOD_CURRENT && chart_period != replay_period)
      period_text += " | chart " + EnumToString(chart_period);

   return(period_text);
}

int OnInit()
{
   g_chart_id = ChartID();
   g_prefix = "ATReplay_" + IntegerToString((int)g_chart_id) + "_" + IntegerToString((int)GetTickCount());
   g_panel_corner = InpPanelCorner;
   g_panel_x_offset = MathMax(InpXOffset, 0);
   g_panel_y_offset = MathMax(InpYOffset, 0);
   AssignObjectNames();
   g_playback_units = MathMax(InpPlaybackUnitsPerTimer, 1);
   g_step_mode = (InpUseRealTicks ? REPLAY_MODE_TICK : REPLAY_MODE_BAR);
   g_is_playing = !InpStartPaused;

   if(!ResolveReplaySymbols())
      return(INIT_FAILED);

   g_trade_volume_lots = NormalizeVolumeLots(InpTradeVolumeLots);
   if(g_trade_volume_lots <= 0.0)
      g_trade_volume_lots = MathMax(InpTradeVolumeLots, 0.01);
   g_simulated_balance_start = ResolveReplayStartingBalance();

   if(!LoadReplaySourceData())
      return(INIT_FAILED);

   if(Symbol() != g_custom_symbol)
   {
      if(!ResetReplayWorkspace(true))
         return(INIT_FAILED);

      if(!ChartSetSymbolPeriod(g_chart_id, g_custom_symbol, ReplayBarPeriod()))
      {
         Print(__FUNCTION__, ": ChartSetSymbolPeriod failed. Error code = ", GetLastError());
         return(INIT_FAILED);
      }
      return(INIT_SUCCEEDED);
   }

   if(!ReplayConfigurationMatchesCurrentInputs() || !WaitForVisibleCustomHistory(6, 50))
   {
      if(!ResetReplayWorkspace(false))
         return(INIT_FAILED);
   }
   else
   {
      SynchronizeQuoteFromCustomHistory();
   }

   ConfigureChartBehavior();
   BuildInterface();
   InitializeReplaySnapshots();
   UpdateInterface();
   SynchronizeViewToLatest();
   ChartSetInteger(g_chart_id, CHART_EVENT_OBJECT_CREATE, true);
   ChartSetInteger(g_chart_id, CHART_EVENT_OBJECT_DELETE, true);
   EventSetTimer(MathMax(InpTimerIntervalSeconds, 1));
   g_is_ready = true;
   return(INIT_SUCCEEDED);
}

void OnDeinit(const int reason)
{
   EventKillTimer();
   CloseReportChart();
   DeleteInterface();
   DeleteTradeObjects();
   ChartRedraw(g_chart_id);
}

void OnTick()
{
}

void OnTimer()
{
   if(!g_is_ready)
      return;

   if(!g_is_playing)
      return;

   bool progressed = true;
   for(int unit = 0; unit < g_playback_units; unit++)
   {
      progressed = StepReplay();
      if(!progressed)
         break;
   }

   if(!progressed)
   {
      g_is_playing = false;
      SetStatus("Replay finished.", InpWarnColor);
   }

   UpdateInterface();
}

void OnChartEvent(const int id, const long &lparam, const double &dparam, const string &sparam)
{
   if(id == CHARTEVENT_OBJECT_CREATE)
   {
      PauseReplayForUserDrawing(sparam);
      return;
   }

   if(id == CHARTEVENT_OBJECT_CLICK)
   {
      HandleButtonClick(sparam);
      return;
   }

   if(id == CHARTEVENT_OBJECT_DRAG)
   {
      if(sparam == g_panel_name)
      {
         HandlePanelDrag();
         return;
      }
      HandleInteractiveObjectDrag(sparam);
      return;
   }

   if(id == CHARTEVENT_OBJECT_ENDEDIT)
   {
      HandleEditCommit(sparam);
      return;
   }

   if(id == CHARTEVENT_KEYDOWN)
   {
      HandleKeyPress((int)lparam);
      return;
   }

   if(id == CHARTEVENT_CHART_CHANGE)
   {
      if(g_is_playing)
         UpdateInterface();
   }
}

void ConfigureChartBehavior()
{
   // The replay workspace runs on a custom symbol chart, so restore the normal
   // MT5 mouse interactions explicitly instead of relying on whatever chart
   // input state happened to be active before the symbol switch.
   ChartSetInteger(g_chart_id, CHART_CONTEXT_MENU, true);
   ChartSetInteger(g_chart_id, CHART_CROSSHAIR_TOOL, true);
   ChartSetInteger(g_chart_id, CHART_MOUSE_SCROLL, true);
   ChartSetInteger(g_chart_id, CHART_DRAG_TRADE_LEVELS, true);
   ChartSetInteger(g_chart_id, CHART_QUICK_NAVIGATION, false);
   ChartSetInteger(g_chart_id, CHART_KEYBOARD_CONTROL, true);
   ChartSetInteger(g_chart_id, CHART_AUTOSCROLL, true);
   ChartSetInteger(g_chart_id, CHART_SHIFT, true);
   ChartSetDouble(g_chart_id, CHART_SHIFT_SIZE, 20.0);
}

void AssignObjectNames()
{
   g_panel_name = BuildObjectName("panel");
   g_accent_name = BuildObjectName("accent");
   g_orders_heading_name = BuildObjectName("orders_heading");
   g_stats_heading_name = BuildObjectName("stats_heading");
   g_replay_heading_name = BuildObjectName("replay_heading");
   g_balance_name = BuildObjectName("balance");
   g_volume_edit_name = BuildObjectName("volume_edit");
   g_separator_left_name = BuildObjectName("separator_left");
   g_separator_right_name = BuildObjectName("separator_right");
   g_title_name = BuildObjectName("title");
   g_meta_name = BuildObjectName("meta");
   g_status_name = BuildObjectName("status");
   g_quote_name = BuildObjectName("quote");
   g_mode_name = BuildObjectName("mode");
   g_position_name = BuildObjectName("position");
   g_volume_name = BuildObjectName("volume");
   g_hotkeys_name = BuildObjectName("hotkeys");
   g_report_button_name = BuildObjectName("report_button");
   g_play_button_name = BuildObjectName("play");
   g_back_button_name = BuildObjectName("back");
   g_step_button_name = BuildObjectName("step");
   g_reset_button_name = BuildObjectName("reset");
   g_mode_button_name = BuildObjectName("mode_toggle");
   g_volume_down_button_name = BuildObjectName("volume_down");
   g_volume_up_button_name = BuildObjectName("volume_up");
   g_speed_down_button_name = BuildObjectName("speed_down");
   g_speed_up_button_name = BuildObjectName("speed_up");
   g_buy_button_name = BuildObjectName("buy");
   g_sell_button_name = BuildObjectName("sell");
   g_close_button_name = BuildObjectName("close");
   g_buy_limit_button_name = BuildObjectName("buy_limit");
   g_sell_limit_button_name = BuildObjectName("sell_limit");
   g_buy_stop_button_name = BuildObjectName("buy_stop");
   g_sell_stop_button_name = BuildObjectName("sell_stop");
   g_entry_line_name = BuildObjectName("entry_line");
   g_stop_line_name = BuildObjectName("stop_line");
   g_take_line_name = BuildObjectName("take_line");
   g_entry_tag_name = BuildObjectName("entry_tag");
   g_stop_tag_name = BuildObjectName("stop_tag");
   g_take_tag_name = BuildObjectName("take_tag");
   g_floating_tag_name = BuildObjectName("floating_tag");
}

string BuildObjectName(const string suffix)
{
   return(g_prefix + "_" + suffix);
}

bool IsManagedChartObject(const string name)
{
   if(name == "")
      return(false);
   return(StringFind(name, g_prefix + "_") == 0);
}

void PauseReplayForUserDrawing(const string object_name)
{
   if(object_name == "" || IsManagedChartObject(object_name) || !g_is_playing)
      return;

   g_is_playing = false;
   SetStatus("Replay paused while drawing " + object_name + ".", InpAccentColor);
   UpdateInterface();
}

int ResolvePanelOffsetXFromDistance(const int xdistance, const int object_width)
{
   if(IsRightCorner())
      return(MathMax(xdistance - object_width, 0));
   return(MathMax(xdistance, 0));
}

int ResolvePanelOffsetYFromDistance(const int ydistance, const int object_height)
{
   if(IsLowerCorner())
      return(MathMax(ydistance - object_height, 0));
   return(MathMax(ydistance, 0));
}

void HandlePanelDrag()
{
   if(ObjectFind(g_chart_id, g_panel_name) < 0)
      return;

   long dragged_corner = ObjectGetInteger(g_chart_id, g_panel_name, OBJPROP_CORNER);
   if(dragged_corner < CORNER_LEFT_UPPER || dragged_corner > CORNER_RIGHT_LOWER)
      dragged_corner = g_panel_corner;

   g_panel_corner = (ENUM_BASE_CORNER)dragged_corner;
   g_panel_x_offset = ResolvePanelOffsetXFromDistance((int)ObjectGetInteger(g_chart_id, g_panel_name, OBJPROP_XDISTANCE), InpPanelWidth);
   g_panel_y_offset = ResolvePanelOffsetYFromDistance((int)ObjectGetInteger(g_chart_id, g_panel_name, OBJPROP_YDISTANCE), InpPanelHeight);

   ObjectSetInteger(g_chart_id, g_panel_name, OBJPROP_SELECTED, false);
   BuildInterface();
   SetStatus("Control panel moved.", InpAccentColor);
   UpdateInterface();
}

double ResolveReplayStartingBalance()
{
   if(InpReplayStartingBalance > 0.0)
      return(InpReplayStartingBalance);

   double account_balance = AccountInfoDouble(ACCOUNT_BALANCE);
   if(account_balance > 0.0)
      return(account_balance);

   return(10000.0);
}

void RefreshReplayChart()
{
   string chart_symbol = Symbol();
   if(chart_symbol == "")
      chart_symbol = g_custom_symbol;

   ENUM_TIMEFRAMES chart_period = ReplayBarPeriod();

   if(Symbol() == chart_symbol && (ENUM_TIMEFRAMES)Period() == chart_period)
   {
      ChartRedraw(g_chart_id);
      return;
   }

   ResetLastError();
   if(!ChartSetSymbolPeriod(g_chart_id, chart_symbol, chart_period))
      Print(__FUNCTION__, ": ChartSetSymbolPeriod refresh failed. Error code = ", GetLastError());
}

bool ResolveReplaySymbols()
{
   string candidate = TrimString(InpSourceSymbol);
   if(candidate == "")
   {
      if(StringFind(Symbol(), "ATR_") == 0)
      {
         candidate = ResolveSourceSymbolFromReplaySymbol(Symbol());
         if(candidate == "")
         {
            Print("Could not infer the source symbol from replay symbol ", Symbol(), ". Set InpSourceSymbol explicitly.");
            return(false);
         }
      }
      else
      {
         candidate = Symbol();
      }
   }

   g_source_symbol = candidate;
   ResetLastError();
   if(!SymbolSelect(g_source_symbol, true))
   {
      string chart_symbol = Symbol();
      if(StringFind(chart_symbol, "ATR_") != 0 && chart_symbol != "" && StringFind(chart_symbol, g_source_symbol) == 0)
      {
         ResetLastError();
         if(SymbolSelect(chart_symbol, true))
         {
            Print(__FUNCTION__, ": using current chart symbol ", chart_symbol, " instead of requested source symbol ", g_source_symbol, ".");
            g_source_symbol = chart_symbol;
         }
      }

      if(!SymbolSelect(g_source_symbol, true))
      {
         Print(__FUNCTION__, ": failed to select source symbol ", g_source_symbol, ". Error code = ", GetLastError());
         return(false);
      }
   }

   g_custom_symbol = BuildCustomSymbolName(g_source_symbol, InpWorkspaceId);
   return(true);
}

string ResolveSourceSymbolFromReplaySymbol(const string replay_symbol)
{
   string description = SymbolInfoString(replay_symbol, SYMBOL_DESCRIPTION);
   string prefix = "ATReplay|source=";
   int prefix_index = StringFind(description, prefix);
   if(prefix_index < 0)
      return("");

   int value_start = prefix_index + StringLen(prefix);
   int workspace_index = StringFind(description, "|workspace=", value_start);
   if(workspace_index < 0)
      return(StringSubstr(description, value_start));

   return(StringSubstr(description, value_start, workspace_index - value_start));
}

string BuildCustomSymbolName(const string source_symbol, const string workspace_id)
{
   string source_clean = SanitizeSymbolToken(source_symbol);
   string workspace_clean = SanitizeSymbolToken(workspace_id);
   string name = "ATR_" + source_clean + "_" + workspace_clean;
   if(StringLen(name) > 31)
      name = StringSubstr(name, 0, 31);
   return(name);
}

string SanitizeSymbolToken(string value)
{
   string trimmed = TrimString(value);
   string output = "";
   for(int index = 0; index < StringLen(trimmed); index++)
   {
      string ch = StringSubstr(trimmed, index, 1);
      bool allowed =
         (ch >= "A" && ch <= "Z")
         || (ch >= "a" && ch <= "z")
         || (ch >= "0" && ch <= "9")
         || ch == "."
         || ch == "_"
         || ch == "&"
         || ch == "#";
      output += (allowed ? ch : "_");
   }
   if(output == "")
      output = "Replay";
   return(output);
}

bool LoadReplaySourceData()
{
   if(InpReplayEndTime <= InpReplayStartTime)
   {
      Print("InpReplayEndTime must be later than InpReplayStartTime.");
      return(false);
   }

   int warmup_minutes = MathMax(InpWarmupMinutes, 0);
   datetime warmup_start = InpReplayStartTime - (warmup_minutes * 60);
   if(warmup_start < 0)
      warmup_start = 0;

   ArrayFree(g_source_m1);
   ArraySetAsSeries(g_source_m1, false);

   ResetLastError();
   int copied_bars = CopyRates(g_source_symbol, PERIOD_M1, warmup_start, InpReplayEndTime, g_source_m1);
   if(copied_bars <= 0)
   {
      Print(__FUNCTION__, ": CopyRates failed for ", g_source_symbol, " in range ", TimeToString(warmup_start, TIME_DATE | TIME_MINUTES), " -> ", TimeToString(InpReplayEndTime, TIME_DATE | TIME_MINUTES), ". Error code = ", GetLastError());
      return(false);
   }

   g_replay_start_minute = FloorToMinute(InpReplayStartTime);
   g_first_replay_m1_index = FindFirstBarAtOrAfter(g_replay_start_minute);
   datetime first_available = g_source_m1[0].time;
   datetime last_available = g_source_m1[copied_bars - 1].time;
   Print(__FUNCTION__, ": loaded ", copied_bars, " M1 bars for ", g_source_symbol, ". Available range ", TimeToString(first_available, TIME_DATE | TIME_MINUTES), " -> ", TimeToString(last_available, TIME_DATE | TIME_MINUTES), ".");

   if(first_available > g_replay_start_minute)
   {
      g_first_replay_m1_index = 0;
      g_replay_start_minute = first_available;
      Print("Requested replay start ", TimeToString(InpReplayStartTime, TIME_DATE | TIME_MINUTES), " is earlier than the loaded M1 range. Starting from ", TimeToString(g_replay_start_minute, TIME_DATE | TIME_MINUTES), ".");
   }

   if(g_first_replay_m1_index < 0)
   {
      g_first_replay_m1_index = copied_bars - 1;
      g_replay_start_minute = g_source_m1[g_first_replay_m1_index].time;
      Print("Requested replay start ", TimeToString(InpReplayStartTime, TIME_DATE | TIME_MINUTES), " is outside the loaded M1 range. Falling back to ", TimeToString(g_replay_start_minute, TIME_DATE | TIME_MINUTES), ".");
   }

   g_next_m1_index = g_first_replay_m1_index;
   g_last_replay_time = (g_first_replay_m1_index > 0 ? g_source_m1[g_first_replay_m1_index - 1].time : g_replay_start_minute);

   ArrayFree(g_source_ticks);
   ArraySetAsSeries(g_source_ticks, false);
   g_has_real_ticks = false;
   g_using_synthetic_ticks = false;
   g_next_tick_index = 0;

   if(!PrepareTickReplayData())
      Print("Tick replay data is unavailable for the requested range. BAR mode will still work.");

   return(true);
}

int FindFirstBarAtOrAfter(datetime value)
{
   int total = ArraySize(g_source_m1);
   for(int index = 0; index < total; index++)
   {
      if(g_source_m1[index].time >= value)
         return(index);
   }
   return(-1);
}

bool EnsureCustomReplaySymbol()
{
   ResetLastError();
   bool created = CustomSymbolCreate(g_custom_symbol, "ATReplay", g_source_symbol);
   int error = GetLastError();
   if(!created && error != 5304)
   {
      Print(__FUNCTION__, ": CustomSymbolCreate failed for ", g_custom_symbol, ". Error code = ", error);
      return(false);
   }

   if(!SymbolSelect(g_custom_symbol, true))
   {
      Print(__FUNCTION__, ": SymbolSelect failed for ", g_custom_symbol, ". Error code = ", GetLastError());
      return(false);
   }

   ResetLastError();
   if(!CustomSymbolSetString(g_custom_symbol, SYMBOL_DESCRIPTION, BuildReplayDescription(g_source_symbol, InpWorkspaceId)))
      Print(__FUNCTION__, ": could not store replay metadata on ", g_custom_symbol, ". Error code = ", GetLastError());

   return(true);
}

string BuildReplayDescription(const string source_symbol, const string workspace_id)
{
   return(
      "ATReplay|source=" + source_symbol
      + "|workspace=" + workspace_id
      + "|period=" + IntegerToString((int)ReplayBarPeriod())
      + "|start=" + TimeToString(InpReplayStartTime, TIME_DATE | TIME_MINUTES)
      + "|end=" + TimeToString(InpReplayEndTime, TIME_DATE | TIME_MINUTES)
      + "|warmup=" + IntegerToString(InpWarmupMinutes)
   );
}

bool PrepareTickReplayData()
{
   ArrayFree(g_source_ticks);
   ArraySetAsSeries(g_source_ticks, false);
   g_next_tick_index = 0;
   g_has_real_ticks = false;
   g_using_synthetic_ticks = false;

   long from_msc = ((long)g_replay_start_minute) * 1000;
   long to_msc = (((long)InpReplayEndTime) * 1000) - 1;
   if(to_msc <= from_msc)
      to_msc = from_msc + 1000;

   ResetLastError();
   int copied_ticks = CopyTicksRange(g_source_symbol, g_source_ticks, COPY_TICKS_ALL, from_msc, to_msc);
   if(copied_ticks > 0)
   {
      g_has_real_ticks = true;
      return(true);
   }

   return(BuildSyntheticTicksFromM1());
}

bool BuildSyntheticTicksFromM1()
{
   int total_bars = ArraySize(g_source_m1);
   if(total_bars <= 0 || g_first_replay_m1_index >= total_bars)
      return(false);

   double point = SymbolInfoDouble(g_source_symbol, SYMBOL_POINT);
   if(point <= 0.0)
      point = 0.00001;

   int estimated_ticks = (total_bars - g_first_replay_m1_index) * 4;
   ArrayResize(g_source_ticks, estimated_ticks);
   int out_index = 0;

   for(int index = g_first_replay_m1_index; index < total_bars; index++)
   {
      MqlRates bar = g_source_m1[index];
      if(bar.time > InpReplayEndTime)
         break;

      double spread = (bar.spread > 0 ? bar.spread * point : point);
      double path[];
      ArrayResize(path, 4);
      path[0] = bar.open;
      if(bar.close >= bar.open)
      {
         path[1] = bar.low;
         path[2] = bar.high;
      }
      else
      {
         path[1] = bar.high;
         path[2] = bar.low;
      }
      path[3] = bar.close;

      for(int step = 0; step < 4; step++)
      {
         MqlTick tick;
         ZeroMemory(tick);

         double mid = path[step];
         tick.time = bar.time;
         tick.time_msc = ((long)bar.time) * 1000 + (step * 200);
         tick.last = NormalizePriceForSymbol(mid, g_source_symbol);
         tick.bid = NormalizePriceForSymbol(mid - (spread / 2.0), g_source_symbol);
         tick.ask = NormalizePriceForSymbol(mid + (spread / 2.0), g_source_symbol);
         tick.volume = (ulong)MathMax(1, (int)bar.tick_volume / 4);
         tick.flags = TICK_FLAG_BID | TICK_FLAG_ASK | TICK_FLAG_LAST | TICK_FLAG_VOLUME;
         g_source_ticks[out_index] = tick;
         out_index++;
      }
   }

   ArrayResize(g_source_ticks, out_index);
   g_using_synthetic_ticks = (out_index > 0);
   return(g_using_synthetic_ticks);
}

long CurrentReplayCursorMsc()
{
   if(g_step_mode == REPLAY_MODE_TICK && g_next_tick_index > 0 && (g_next_tick_index - 1) < ArraySize(g_source_ticks))
      return(g_source_ticks[g_next_tick_index - 1].time_msc);
   return(((long)g_last_replay_time) * 1000);
}

int FindFirstTickAfterCursor(const long cursor_msc)
{
   int total = ArraySize(g_source_ticks);
   for(int index = 0; index < total; index++)
   {
      if(g_source_ticks[index].time_msc > cursor_msc)
         return(index);
   }
   return(total);
}

int FindFirstBarAfterMinute(const datetime minute_time)
{
   int total = ArraySize(g_source_m1);
   for(int index = g_first_replay_m1_index; index < total; index++)
   {
      if(g_source_m1[index].time > minute_time)
         return(index);
   }
   return(total);
}

void ResetSessionTrades()
{
   g_closed_trade_count = 0;
   ArrayResize(g_closed_trades, 0);
}

bool ResetReplayWorkspace(const bool preserve_play_state)
{
   if(!EnsureCustomReplaySymbol())
      return(false);

   ResetLastError();
   CustomRatesDelete(g_custom_symbol, 0, LONG_MAX);
   CustomTicksDelete(g_custom_symbol, 0, LONG_MAX);

   if(g_first_replay_m1_index > 0)
   {
      MqlRates seed_rates[];
      ArrayResize(seed_rates, g_first_replay_m1_index);
      for(int index = 0; index < g_first_replay_m1_index; index++)
         seed_rates[index] = g_source_m1[index];

      ResetLastError();
      int updated = CustomRatesUpdate(g_custom_symbol, seed_rates);
      if(updated < 0)
      {
         Print(__FUNCTION__, ": CustomRatesUpdate failed while seeding history. Error code = ", GetLastError());
         return(false);
      }
   }

   g_next_m1_index = g_first_replay_m1_index;
   g_next_tick_index = 0;
   g_last_replay_time = (g_first_replay_m1_index > 0 ? g_source_m1[g_first_replay_m1_index - 1].time : g_replay_start_minute);
   g_workspace_seeded = true;
   g_realized_pnl = 0.0;
   ResetSimPosition();
   ResetPendingOrder();
   ResetSessionTrades();
   DeleteTradeObjects();
   SynchronizeQuoteFromCustomHistory();

   if(!preserve_play_state)
      g_is_playing = !InpStartPaused;

   if(Symbol() == g_custom_symbol)
      RefreshReplayChart();
   else
      ChartRedraw(g_chart_id);
   ConfigureChartBehavior();
   InitializeReplaySnapshots();
   SynchronizeViewToLatest();
   ChartRedraw(g_chart_id);
   SetStatus("Replay reset to the chosen start point.", InpAccentColor);
   return(true);
}

void SynchronizeQuoteFromCustomHistory()
{
   int bars = Bars(g_custom_symbol, PERIOD_M1);
   if(bars <= 0)
   {
      g_last_bid = 0.0;
      g_last_ask = 0.0;
      return;
   }

   MqlRates rates[];
   ArraySetAsSeries(rates, false);
   if(CopyRates(g_custom_symbol, PERIOD_M1, 0, bars, rates) <= 0)
      return;

   MqlRates last_bar = rates[bars - 1];
   UpdateQuoteFromBar(last_bar);
}

void CaptureReplaySnapshot()
{
   if(g_snapshot_cursor < (g_snapshot_count - 1))
      g_snapshot_count = g_snapshot_cursor + 1;

   ArrayResize(g_snapshots, g_snapshot_count + 1);
   ATReplaySnapshot snapshot;
   snapshot.step_mode = g_step_mode;
   snapshot.next_m1_index = g_next_m1_index;
   snapshot.next_tick_index = g_next_tick_index;
   snapshot.last_replay_time = g_last_replay_time;
   snapshot.last_bid = g_last_bid;
   snapshot.last_ask = g_last_ask;
   snapshot.realized_pnl = g_realized_pnl;
    snapshot.closed_trade_count = g_closed_trade_count;
   snapshot.position = g_position;
   snapshot.pending_order = g_pending_order;
   g_snapshots[g_snapshot_count] = snapshot;
   g_snapshot_count++;
   g_snapshot_cursor = g_snapshot_count - 1;
}

void InitializeReplaySnapshots()
{
   ArrayResize(g_snapshots, 0);
   g_snapshot_count = 0;
   g_snapshot_cursor = -1;
   CaptureReplaySnapshot();
}

bool RebuildCustomHistoryToSnapshot(const ATReplaySnapshot &snapshot)
{
   if(!EnsureCustomReplaySymbol())
      return(false);

   CustomRatesDelete(g_custom_symbol, 0, LONG_MAX);
   CustomTicksDelete(g_custom_symbol, 0, LONG_MAX);

   if(g_first_replay_m1_index > 0)
   {
      MqlRates seed_rates[];
      ArrayResize(seed_rates, g_first_replay_m1_index);
      for(int index = 0; index < g_first_replay_m1_index; index++)
         seed_rates[index] = g_source_m1[index];

      if(CustomRatesUpdate(g_custom_symbol, seed_rates) < 0)
      {
         Print(__FUNCTION__, ": failed to seed custom history. Error code = ", GetLastError());
         return(false);
      }
   }

   if(snapshot.step_mode == REPLAY_MODE_BAR && snapshot.next_m1_index > g_first_replay_m1_index)
   {
      int replay_count = snapshot.next_m1_index - g_first_replay_m1_index;
      MqlRates replay_rates[];
      ArrayResize(replay_rates, replay_count);
      for(int index = 0; index < replay_count; index++)
         replay_rates[index] = g_source_m1[g_first_replay_m1_index + index];

      if(CustomRatesUpdate(g_custom_symbol, replay_rates) < 0)
      {
         Print(__FUNCTION__, ": failed to rebuild bar replay history. Error code = ", GetLastError());
         return(false);
      }
   }

   if(snapshot.step_mode == REPLAY_MODE_TICK && snapshot.next_tick_index > 0)
   {
      MqlTick replay_ticks[];
      ArrayResize(replay_ticks, snapshot.next_tick_index);
      for(int index = 0; index < snapshot.next_tick_index; index++)
         replay_ticks[index] = g_source_ticks[index];

      if(CustomTicksAdd(g_custom_symbol, replay_ticks) < 0)
      {
         Print(__FUNCTION__, ": failed to rebuild tick replay history. Error code = ", GetLastError());
         return(false);
      }
   }

   return(true);
}

bool RestoreReplaySnapshot(const ATReplaySnapshot &snapshot)
{
   if(!RebuildCustomHistoryToSnapshot(snapshot))
      return(false);

   g_step_mode = snapshot.step_mode;
   g_next_m1_index = snapshot.next_m1_index;
   g_next_tick_index = snapshot.next_tick_index;
   g_last_replay_time = snapshot.last_replay_time;
   g_last_bid = snapshot.last_bid;
   g_last_ask = snapshot.last_ask;
   g_realized_pnl = snapshot.realized_pnl;
   g_closed_trade_count = snapshot.closed_trade_count;
   g_position = snapshot.position;
   g_pending_order = snapshot.pending_order;
   g_workspace_seeded = true;
   g_is_playing = false;

   ConfigureChartBehavior();
   DrawTradeObjects();
   SynchronizeViewToLatest();
   ChartRedraw(g_chart_id);
   return(true);
}

void StepBackwardOneUnit()
{
   if(g_is_playing)
   {
      SetStatus("Pause playback before stepping backward.", InpWarnColor);
      UpdateInterface();
      return;
   }

   if(g_snapshot_cursor <= 0)
   {
      SetStatus("Replay is already at the earliest step.", InpWarnColor);
      UpdateInterface();
      return;
   }

   g_snapshot_cursor--;
   if(!RestoreReplaySnapshot(g_snapshots[g_snapshot_cursor]))
   {
      SetStatus("Could not step backward.", InpWarnColor);
      UpdateInterface();
      return;
   }

   SetStatus("Moved back one replay unit.", InpAccentColor);
   UpdateInterface();
}

bool StepReplay()
{
   if(g_step_mode == REPLAY_MODE_TICK)
   {
      if(ArraySize(g_source_ticks) <= 0)
      {
         SetStatus("No tick stream is available for this range. Switch to BAR mode.", InpWarnColor);
         return(false);
      }
      return(AdvanceOneTick());
   }
   return(AdvanceOneChartBar());
}

bool AdvanceOneTick()
{
   if(g_next_tick_index >= ArraySize(g_source_ticks))
      return(false);

   MqlTick add_tick[];
   ArrayResize(add_tick, 1);
   add_tick[0] = g_source_ticks[g_next_tick_index];

   ResetLastError();
   int updated = CustomTicksAdd(g_custom_symbol, add_tick);
   if(updated < 0)
   {
      Print(__FUNCTION__, ": CustomTicksAdd failed. Error code = ", GetLastError());
      return(false);
   }

   g_next_tick_index++;
   g_last_replay_time = (datetime)(add_tick[0].time_msc / 1000);
   UpdateQuoteFromTick(add_tick[0]);
   EvaluatePendingOrderAgainstTick(add_tick[0]);
   EvaluatePositionAgainstTick(add_tick[0]);
   CaptureReplaySnapshot();
   SynchronizeViewToLatest();
   ChartRedraw(g_chart_id);
   return(true);
}

bool AdvanceOneChartBar()
{
   if(g_next_m1_index >= ArraySize(g_source_m1))
      return(false);

   int timeframe_seconds = PeriodSeconds(ReplayBarPeriod());
   if(timeframe_seconds <= 0)
      timeframe_seconds = 60;

   datetime first_bar_time = g_source_m1[g_next_m1_index].time;
   datetime target_end = FloorToTimeframe(first_bar_time, timeframe_seconds) + timeframe_seconds;

   int start_index = g_next_m1_index;
   int count = 0;
   while(g_next_m1_index < ArraySize(g_source_m1))
   {
      datetime bar_time = g_source_m1[g_next_m1_index].time;
      if(bar_time >= target_end)
         break;
      g_next_m1_index++;
      count++;
   }

   if(count <= 0)
      return(false);

   MqlRates batch[];
   ArrayResize(batch, count);
   for(int index = 0; index < count; index++)
      batch[index] = g_source_m1[start_index + index];

   ResetLastError();
   int updated = CustomRatesUpdate(g_custom_symbol, batch);
   if(updated < 0)
   {
      Print(__FUNCTION__, ": CustomRatesUpdate failed during replay. Error code = ", GetLastError());
      return(false);
   }

   for(int index = 0; index < count; index++)
   {
      EvaluatePendingOrderAgainstBar(batch[index]);
      EvaluatePositionAgainstBar(batch[index]);
   }

   g_last_replay_time = batch[count - 1].time + 59;
   UpdateQuoteFromBar(batch[count - 1]);
   CaptureReplaySnapshot();
   SynchronizeViewToLatest();
   ChartRedraw(g_chart_id);
   return(true);
}

datetime FloorToMinute(datetime value)
{
   return(value - (value % 60));
}

datetime FloorToTimeframe(datetime value, const int timeframe_seconds)
{
   if(timeframe_seconds <= 0)
      return(FloorToMinute(value));
   return(value - (value % timeframe_seconds));
}

void UpdateQuoteFromBar(const MqlRates &bar)
{
   double point = SymbolInfoDouble(g_custom_symbol, SYMBOL_POINT);
   if(point <= 0.0)
      point = 0.00001;

   double spread = (bar.spread > 0 ? bar.spread * point : point);
   double mid = bar.close;
   g_last_bid = mid - (spread / 2.0);
   g_last_ask = mid + (spread / 2.0);
}

void UpdateQuoteFromTick(const MqlTick &tick)
{
   double bid = tick.bid;
   double ask = tick.ask;
   if(bid <= 0.0 && tick.last > 0.0)
      bid = tick.last;
   if(ask <= 0.0 && tick.last > 0.0)
      ask = tick.last;
   if(ask <= 0.0)
      ask = bid;
   if(bid <= 0.0)
      bid = ask;
   g_last_bid = bid;
   g_last_ask = ask;
}

void HandleButtonClick(const string name)
{
   if(name == g_report_button_name)
   {
      OpenSessionReportWindow();
      return;
   }
   if(name == g_play_button_name)
   {
      TogglePlayPause();
      return;
   }
   if(name == g_back_button_name)
   {
      StepBackwardOneUnit();
      return;
   }
   if(name == g_step_button_name)
   {
      StepManually();
      return;
   }
   if(name == g_reset_button_name)
   {
      ResetReplayWorkspace(false);
      UpdateInterface();
      return;
   }
   if(name == g_mode_button_name)
   {
      ToggleModeAndReset();
      return;
   }
   if(name == g_volume_down_button_name)
   {
      AdjustTradeVolume(-1);
      UpdateInterface();
      return;
   }
   if(name == g_volume_up_button_name)
   {
      AdjustTradeVolume(1);
      UpdateInterface();
      return;
   }
   if(name == g_speed_down_button_name)
   {
      g_playback_units = MathMax(g_playback_units - 1, 1);
      SetStatus("Playback speed decreased.", InpAccentColor);
      UpdateInterface();
      return;
   }
   if(name == g_speed_up_button_name)
   {
      g_playback_units = MathMin(g_playback_units + 1, 32);
      SetStatus("Playback speed increased.", InpAccentColor);
      UpdateInterface();
      return;
   }
   if(name == g_buy_button_name)
   {
      OpenSimPosition(1);
      UpdateInterface();
      return;
   }
   if(name == g_sell_button_name)
   {
      OpenSimPosition(-1);
      UpdateInterface();
      return;
   }
   if(name == g_close_button_name)
   {
      ManualClosePosition();
      UpdateInterface();
      return;
   }
   if(name == g_buy_limit_button_name)
   {
      CreatePendingOrder(PENDING_BUY_LIMIT);
      UpdateInterface();
      return;
   }
   if(name == g_sell_limit_button_name)
   {
      CreatePendingOrder(PENDING_SELL_LIMIT);
      UpdateInterface();
      return;
   }
   if(name == g_buy_stop_button_name)
   {
      CreatePendingOrder(PENDING_BUY_STOP);
      UpdateInterface();
      return;
   }
   if(name == g_sell_stop_button_name)
   {
      CreatePendingOrder(PENDING_SELL_STOP);
      UpdateInterface();
      return;
   }
}

void HandleEditCommit(const string name)
{
   if(name != g_volume_edit_name)
      return;

   string raw = TrimString(ObjectGetString(g_chart_id, g_volume_edit_name, OBJPROP_TEXT));
   StringReplace(raw, ",", ".");
   double parsed = StringToDouble(raw);
   if(raw == "" || parsed <= 0.0)
   {
      SyncVolumeEditField();
      SetStatus("Order size must be greater than zero.", InpWarnColor);
      return;
   }

   g_trade_volume_lots = NormalizeVolumeLots(parsed);
   if(g_pending_order.active)
      g_pending_order.volume_lots = g_trade_volume_lots;

   SyncVolumeEditField();
   DrawTradeObjects();
   UpdateInterface();
   SetStatus("Order size set to " + VolumeToText(g_trade_volume_lots) + " lots.", InpAccentColor);
}

void HandleKeyPress(const int key_code)
{
   if(IsVolumeEditActive())
      return;

   if(key_code == 32)
   {
      StepManually();
      return;
   }
   if(key_code == 13)
   {
      TogglePlayPause();
      return;
   }
   if(key_code == 82)
   {
      ResetReplayWorkspace(false);
      UpdateInterface();
      return;
   }
   if(key_code == 77)
   {
      ToggleModeAndReset();
      return;
   }
   if(key_code == 66)
   {
      OpenSimPosition(1);
      UpdateInterface();
      return;
   }
   if(key_code == 83)
   {
      OpenSimPosition(-1);
      UpdateInterface();
      return;
   }
   if(key_code == 67)
   {
      ManualClosePosition();
      UpdateInterface();
      return;
   }
   if(key_code == 187 || key_code == 107)
   {
      g_playback_units = MathMin(g_playback_units + 1, 32);
      UpdateInterface();
      return;
   }
   if(key_code == 189 || key_code == 109)
   {
      g_playback_units = MathMax(g_playback_units - 1, 1);
      UpdateInterface();
      return;
   }
}

void TogglePlayPause()
{
   g_is_playing = !g_is_playing;
   SetStatus(g_is_playing ? "Playback running." : "Playback paused.", g_is_playing ? InpGoodColor : InpAccentColor);
   UpdateInterface();
}

void StepManually()
{
   if(g_is_playing)
   {
      SetStatus("Pause playback before stepping manually.", InpWarnColor);
      UpdateInterface();
      return;
   }

   if(!StepReplay())
      SetStatus("Replay finished or no more data available.", InpWarnColor);
   else
      SetStatus("Advanced one replay unit.", InpAccentColor);
   UpdateInterface();
}

void ToggleModeAndReset()
{
   ATReplayStepMode next_mode = (g_step_mode == REPLAY_MODE_TICK ? REPLAY_MODE_BAR : REPLAY_MODE_TICK);
   if(next_mode == REPLAY_MODE_TICK && ArraySize(g_source_ticks) <= 0)
   {
      SetStatus("No tick stream is available for this range. Staying in BAR mode.", InpWarnColor);
      UpdateInterface();
      return;
   }

   if(next_mode == REPLAY_MODE_TICK)
   {
      if(g_next_m1_index <= g_first_replay_m1_index)
         g_next_tick_index = 0;
      else
         g_next_tick_index = FindFirstTickAfterCursor(CurrentReplayCursorMsc());
   }
   else
   {
      if(g_next_tick_index <= 0)
         g_next_m1_index = g_first_replay_m1_index;
      else
         g_next_m1_index = FindFirstBarAfterMinute(FloorToMinute(g_last_replay_time));
   }

   g_step_mode = next_mode;
   g_is_playing = false;
   CaptureReplaySnapshot();
   ConfigureChartBehavior();
   DrawTradeObjects();
   SynchronizeViewToLatest();

   string mode_message = (g_step_mode == REPLAY_MODE_TICK ? "Mode set to TICK at the current replay point." : "Mode set to BAR at the current replay point.");
   if(g_step_mode == REPLAY_MODE_TICK && g_has_real_ticks)
      mode_message += " Using real ticks.";
   else if(g_step_mode == REPLAY_MODE_TICK && g_using_synthetic_ticks)
      mode_message += " Using synthetic ticks.";
   SetStatus(mode_message, InpAccentColor);
   UpdateInterface();
}

void ResetSimPosition()
{
   g_position.open = false;
   g_position.side = 0;
   g_position.volume_lots = 0.0;
   g_position.entry_price = 0.0;
   g_position.stop_loss = 0.0;
   g_position.take_profit = 0.0;
   g_position.opened_at = 0;
}

void ResetPendingOrder()
{
   g_pending_order.active = false;
   g_pending_order.type = PENDING_NONE;
   g_pending_order.volume_lots = 0.0;
   g_pending_order.entry_price = 0.0;
   g_pending_order.stop_loss = 0.0;
   g_pending_order.take_profit = 0.0;
   g_pending_order.created_at = 0;
}

int DefaultStopLossPoints()
{
   if(InpTradeStopLossPoints > 0)
      return(InpTradeStopLossPoints);
   return(DefaultProtectionPoints());
}

int DefaultTakeProfitPoints()
{
   if(InpTradeTakeProfitPoints > 0)
      return(InpTradeTakeProfitPoints);
   return(DefaultProtectionPoints());
}

double SymbolPointValue()
{
   double point = SymbolInfoDouble(g_custom_symbol, SYMBOL_POINT);
   if(point <= 0.0)
      point = 0.00001;
   return(point);
}

double SymbolPipSize()
{
   double point = SymbolPointValue();
   int digits = (int)SymbolInfoInteger(g_custom_symbol, SYMBOL_DIGITS);
   if(digits == 3 || digits == 5)
      return(point * 10.0);
   return(point);
}

int DefaultProtectionPoints()
{
   double point = SymbolPointValue();
   double spread = 0.0;
   if(g_last_ask > 0.0 && g_last_bid > 0.0)
      spread = MathAbs(g_last_ask - g_last_bid);

   if(spread <= 0.0)
   {
      double symbol_bid = SymbolInfoDouble(g_custom_symbol, SYMBOL_BID);
      double symbol_ask = SymbolInfoDouble(g_custom_symbol, SYMBOL_ASK);
      if(symbol_ask > 0.0 && symbol_bid > 0.0)
         spread = MathAbs(symbol_ask - symbol_bid);
   }

   double distance = 0.0;
   if(spread > 0.0)
      distance = spread * 2.0;
   else
      distance = MathMax(InpDefaultProtectionPips, 0.1) * SymbolPipSize();

   return((int)MathMax(MathRound(distance / point), 1));
}

double DefaultStopLossPriceForEntry(const int side, const double entry_price)
{
   double distance = DefaultStopLossPoints() * SymbolPointValue();
   return(NormalizePrice(side > 0 ? entry_price - distance : entry_price + distance));
}

double DefaultTakeProfitPriceForEntry(const int side, const double entry_price)
{
   double distance = DefaultTakeProfitPoints() * SymbolPointValue();
   return(NormalizePrice(side > 0 ? entry_price + distance : entry_price - distance));
}

double NormalizeStopLossPrice(const int side, const double entry_price, const double current_price)
{
   double point = SymbolPointValue();
   if(current_price <= 0.0)
      return(DefaultStopLossPriceForEntry(side, entry_price));
   if(side > 0 && current_price >= entry_price)
      return(NormalizePrice(entry_price - point));
   if(side < 0 && current_price <= entry_price)
      return(NormalizePrice(entry_price + point));
   return(NormalizePrice(current_price));
}

double NormalizeTakeProfitPrice(const int side, const double entry_price, const double current_price)
{
   double point = SymbolPointValue();
   if(current_price <= 0.0)
      return(DefaultTakeProfitPriceForEntry(side, entry_price));
   if(side > 0 && current_price <= entry_price)
      return(NormalizePrice(entry_price + point));
   if(side < 0 && current_price >= entry_price)
      return(NormalizePrice(entry_price - point));
   return(NormalizePrice(current_price));
}

int PendingOrderSide(const ATSimPendingType type)
{
   if(type == PENDING_BUY_LIMIT || type == PENDING_BUY_STOP)
      return(1);
   if(type == PENDING_SELL_LIMIT || type == PENDING_SELL_STOP)
      return(-1);
   return(0);
}

string PendingOrderTypeText(const ATSimPendingType type)
{
   if(type == PENDING_BUY_LIMIT)
      return("BUY LIMIT");
   if(type == PENDING_SELL_LIMIT)
      return("SELL LIMIT");
   if(type == PENDING_BUY_STOP)
      return("BUY STOP");
   if(type == PENDING_SELL_STOP)
      return("SELL STOP");
   return("NONE");
}

void NormalizePendingOrderLevels()
{
   if(!g_pending_order.active)
      return;

   int side = PendingOrderSide(g_pending_order.type);
   g_pending_order.stop_loss = NormalizeStopLossPrice(side, g_pending_order.entry_price, g_pending_order.stop_loss);
   g_pending_order.take_profit = NormalizeTakeProfitPrice(side, g_pending_order.entry_price, g_pending_order.take_profit);
}

void NormalizePositionProtectionLevels()
{
   if(!g_position.open)
      return;
   g_position.stop_loss = NormalizeStopLossPrice(g_position.side, g_position.entry_price, g_position.stop_loss);
   g_position.take_profit = NormalizeTakeProfitPrice(g_position.side, g_position.entry_price, g_position.take_profit);
}

void CreatePendingOrder(const ATSimPendingType type)
{
   if(g_position.open)
   {
      SetStatus("Close the open position before creating a pending order.", InpWarnColor);
      return;
   }

   if(g_pending_order.active)
   {
      SetStatus("Only one pending order is supported right now.", InpWarnColor);
      return;
   }

   if(g_last_bid <= 0.0 || g_last_ask <= 0.0)
   {
      SetStatus("Replay has not produced a valid quote yet.", InpWarnColor);
      return;
   }

   double point = SymbolInfoDouble(g_custom_symbol, SYMBOL_POINT);
   if(point <= 0.0)
      point = 0.00001;

   double offset = MathMax(InpPendingEntryOffsetPoints, 1) * point;
   g_pending_order.active = true;
   g_pending_order.type = type;
   g_pending_order.volume_lots = g_trade_volume_lots;
   g_pending_order.created_at = g_last_replay_time;

   if(type == PENDING_BUY_LIMIT)
      g_pending_order.entry_price = NormalizePrice(g_last_ask - offset);
   else if(type == PENDING_SELL_LIMIT)
      g_pending_order.entry_price = NormalizePrice(g_last_bid + offset);
   else if(type == PENDING_BUY_STOP)
      g_pending_order.entry_price = NormalizePrice(g_last_ask + offset);
   else if(type == PENDING_SELL_STOP)
      g_pending_order.entry_price = NormalizePrice(g_last_bid - offset);

   g_pending_order.stop_loss = 0.0;
   g_pending_order.take_profit = 0.0;
   NormalizePendingOrderLevels();
   DrawTradeObjects();
   SetStatus(PendingOrderTypeText(type) + " staged @ " + PriceToText(g_pending_order.entry_price), InpAccentColor);
}

void CancelPendingOrder(const string reason)
{
   if(!g_pending_order.active)
      return;

   string label = PendingOrderTypeText(g_pending_order.type);
   ResetPendingOrder();
   DeleteTradeObjects();
   SetStatus(label + " cancelled via " + reason + ".", InpWarnColor);
}

void ActivatePendingOrder()
{
   if(!g_pending_order.active || g_position.open)
      return;

   int side = PendingOrderSide(g_pending_order.type);
   string label = PendingOrderTypeText(g_pending_order.type);

   g_position.open = true;
   g_position.side = side;
   g_position.volume_lots = g_pending_order.volume_lots;
   g_position.entry_price = g_pending_order.entry_price;
   g_position.stop_loss = g_pending_order.stop_loss;
   g_position.take_profit = g_pending_order.take_profit;
   g_position.opened_at = g_last_replay_time;
   NormalizePositionProtectionLevels();

   ResetPendingOrder();
   DrawTradeObjects();
   SetStatus(label + " filled @ " + PriceToText(g_position.entry_price), side > 0 ? InpGoodColor : InpBadColor);
}

void EvaluatePendingOrderAgainstTick(const MqlTick &tick)
{
   if(!g_pending_order.active || g_position.open)
      return;

   bool triggered = false;
   if(g_pending_order.type == PENDING_BUY_LIMIT)
      triggered = (g_last_ask <= g_pending_order.entry_price);
   else if(g_pending_order.type == PENDING_SELL_LIMIT)
      triggered = (g_last_bid >= g_pending_order.entry_price);
   else if(g_pending_order.type == PENDING_BUY_STOP)
      triggered = (g_last_ask >= g_pending_order.entry_price);
   else if(g_pending_order.type == PENDING_SELL_STOP)
      triggered = (g_last_bid <= g_pending_order.entry_price);

   if(triggered)
      ActivatePendingOrder();
}

void EvaluatePendingOrderAgainstBar(const MqlRates &bar)
{
   if(!g_pending_order.active || g_position.open)
      return;

   bool triggered = false;
   if(g_pending_order.type == PENDING_BUY_LIMIT)
      triggered = (bar.low <= g_pending_order.entry_price);
   else if(g_pending_order.type == PENDING_SELL_LIMIT)
      triggered = (bar.high >= g_pending_order.entry_price);
   else if(g_pending_order.type == PENDING_BUY_STOP)
      triggered = (bar.high >= g_pending_order.entry_price);
   else if(g_pending_order.type == PENDING_SELL_STOP)
      triggered = (bar.low <= g_pending_order.entry_price);

   if(triggered)
      ActivatePendingOrder();
}

double ReadObjectPrice(const string name)
{
   return(ObjectGetDouble(g_chart_id, name, OBJPROP_PRICE, 0));
}

void HandleInteractiveObjectDrag(const string name)
{
   if(name != g_entry_line_name && name != g_stop_line_name && name != g_take_line_name)
      return;

   double price = NormalizePrice(ReadObjectPrice(name));
   if(price <= 0.0)
      return;

   if(g_pending_order.active)
   {
      if(name == g_entry_line_name)
         g_pending_order.entry_price = price;
      else if(name == g_stop_line_name)
         g_pending_order.stop_loss = price;
      else if(name == g_take_line_name)
         g_pending_order.take_profit = price;

      NormalizePendingOrderLevels();
      SetTradeObjectSelection(name, false);
      DrawTradeObjects();
      SetStatus(PendingOrderTypeText(g_pending_order.type) + " updated.", InpAccentColor);
      UpdateInterface();
      return;
   }

   if(g_position.open)
   {
      if(name == g_stop_line_name)
      {
         g_position.stop_loss = price;
         SetStatus("Stop loss updated.", InpAccentColor);
      }
      else if(name == g_take_line_name)
      {
         g_position.take_profit = price;
         SetStatus("Take profit updated.", InpAccentColor);
      }
      else
      {
         SetTradeObjectSelection(name, false);
         SetStatus("Drag the SL or TP line to manage the open position.", InpWarnColor);
         DrawTradeObjects();
         UpdateInterface();
         return;
      }

      NormalizePositionProtectionLevels();
      SetTradeObjectSelection(name, false);
      DrawTradeObjects();
      UpdateInterface();
   }
}

void OpenSimPosition(const int side)
{
   if(g_position.open)
   {
      SetStatus("A simulated position is already open.", InpWarnColor);
      return;
   }

   if(g_pending_order.active)
   {
      SetStatus("Cancel the pending order before opening a market position.", InpWarnColor);
      return;
   }

   if(g_last_bid <= 0.0 || g_last_ask <= 0.0)
   {
      SetStatus("Replay has not produced a valid quote yet.", InpWarnColor);
      return;
   }

   double point = SymbolInfoDouble(g_custom_symbol, SYMBOL_POINT);
   if(point <= 0.0)
      point = 0.00001;

   g_position.open = true;
   g_position.side = side;
   g_position.volume_lots = g_trade_volume_lots;
   g_position.entry_price = (side > 0 ? g_last_ask : g_last_bid);
   g_position.opened_at = g_last_replay_time;
   g_position.stop_loss = NormalizePrice(
      side > 0
      ? g_position.entry_price - (DefaultStopLossPoints() * point)
      : g_position.entry_price + (DefaultStopLossPoints() * point)
   );
   g_position.take_profit = NormalizePrice(
      side > 0
      ? g_position.entry_price + (DefaultTakeProfitPoints() * point)
      : g_position.entry_price - (DefaultTakeProfitPoints() * point)
   );
   NormalizePositionProtectionLevels();

   DrawTradeObjects();
   SetStatus(
      (side > 0 ? "Opened LONG" : "Opened SHORT")
      + " @ "
      + PriceToText(g_position.entry_price),
      side > 0 ? InpGoodColor : InpBadColor
   );
}

void ManualClosePosition()
{
   if(g_position.open)
   {
      double exit_price = (g_position.side > 0 ? g_last_bid : g_last_ask);
      CloseSimPosition("manual", exit_price);
      return;
   }

   if(g_pending_order.active)
   {
      CancelPendingOrder("manual");
      return;
   }

   SetStatus("No simulated position or pending order is active.", InpWarnColor);
}

void CloseSimPosition(const string reason, const double exit_price)
{
   if(!g_position.open)
      return;

   double pnl = CalculatePositionPnl(exit_price);
   g_realized_pnl += pnl;
   RecordClosedTrade(reason, exit_price, pnl);

   string direction = (g_position.side > 0 ? "LONG" : "SHORT");
   string message = direction + " closed via " + reason + ". pnl=" + DoubleToString(pnl, 2);
   SetStatus(message, pnl >= 0.0 ? InpGoodColor : InpBadColor);

   ResetSimPosition();
   DeleteTradeObjects();
}

double CalculatePositionPnl(const double exit_price)
{
   double tick_size = SymbolInfoDouble(g_custom_symbol, SYMBOL_TRADE_TICK_SIZE);
   double tick_value = SymbolInfoDouble(g_custom_symbol, SYMBOL_TRADE_TICK_VALUE_PROFIT);
   if(tick_size <= 0.0)
      tick_size = SymbolInfoDouble(g_custom_symbol, SYMBOL_POINT);
   if(tick_size <= 0.0)
      tick_size = 0.00001;
   if(tick_value <= 0.0)
      tick_value = 1.0;

   double move = (exit_price - g_position.entry_price) * g_position.side;
   return((move / tick_size) * tick_value * g_position.volume_lots);
}

void EvaluatePositionAgainstTick(const MqlTick &tick)
{
   if(!g_position.open)
      return;

   double bid = g_last_bid;
   double ask = g_last_ask;

   if(g_position.side > 0)
   {
      if(g_position.stop_loss > 0.0 && bid <= g_position.stop_loss)
      {
         CloseSimPosition("stop", g_position.stop_loss);
         return;
      }
      if(g_position.take_profit > 0.0 && bid >= g_position.take_profit)
      {
         CloseSimPosition("target", g_position.take_profit);
         return;
      }
   }
   else
   {
      if(g_position.stop_loss > 0.0 && ask >= g_position.stop_loss)
      {
         CloseSimPosition("stop", g_position.stop_loss);
         return;
      }
      if(g_position.take_profit > 0.0 && ask <= g_position.take_profit)
      {
         CloseSimPosition("target", g_position.take_profit);
         return;
      }
   }

   DrawTradeObjects();
}

void EvaluatePositionAgainstBar(const MqlRates &bar)
{
   if(!g_position.open)
      return;

   if(g_position.side > 0)
   {
      bool hit_stop = (g_position.stop_loss > 0.0 && bar.low <= g_position.stop_loss);
      bool hit_target = (g_position.take_profit > 0.0 && bar.high >= g_position.take_profit);

      if(hit_stop && hit_target)
      {
         CloseSimPosition("bar_conflict_stop_first", g_position.stop_loss);
         return;
      }
      if(hit_stop)
      {
         CloseSimPosition("stop", g_position.stop_loss);
         return;
      }
      if(hit_target)
      {
         CloseSimPosition("target", g_position.take_profit);
         return;
      }
   }
   else
   {
      bool hit_stop = (g_position.stop_loss > 0.0 && bar.high >= g_position.stop_loss);
      bool hit_target = (g_position.take_profit > 0.0 && bar.low <= g_position.take_profit);

      if(hit_stop && hit_target)
      {
         CloseSimPosition("bar_conflict_stop_first", g_position.stop_loss);
         return;
      }
      if(hit_stop)
      {
         CloseSimPosition("stop", g_position.stop_loss);
         return;
      }
      if(hit_target)
      {
         CloseSimPosition("target", g_position.take_profit);
         return;
      }
   }

   DrawTradeObjects();
}

void DrawTradeObjects()
{
   if(g_position.open)
   {
      NormalizePositionProtectionLevels();
      color entry_color = (g_position.side > 0 ? InpGoodColor : InpBadColor);
      EnsureHLine(g_entry_line_name, g_position.entry_price, entry_color, STYLE_SOLID);
      if(g_position.stop_loss > 0.0)
      {
         EnsureHLine(g_stop_line_name, g_position.stop_loss, InpBadColor, STYLE_DOT);
         EnsureTradeText(g_stop_tag_name, g_last_replay_time, g_position.stop_loss, "SL", InpBadColor);
      }
      else
      {
         ObjectDelete(g_chart_id, g_stop_line_name);
         ObjectDelete(g_chart_id, g_stop_tag_name);
      }
      if(g_position.take_profit > 0.0)
      {
         EnsureHLine(g_take_line_name, g_position.take_profit, InpGoodColor, STYLE_DOT);
         EnsureTradeText(g_take_tag_name, g_last_replay_time, g_position.take_profit, "TP", InpGoodColor);
      }
      else
      {
         ObjectDelete(g_chart_id, g_take_line_name);
         ObjectDelete(g_chart_id, g_take_tag_name);
      }
      EnsureTradeText(g_entry_tag_name, g_position.opened_at, g_position.entry_price, (g_position.side > 0 ? "BUY " : "SELL ") + VolumeToText(g_position.volume_lots), entry_color);
      double floating_price = (g_position.side > 0 ? g_last_bid : g_last_ask);
      double floating_pnl = CalculatePositionPnl(floating_price);
      EnsureTradeText(g_floating_tag_name, g_last_replay_time, floating_price, "P/L " + DoubleToString(floating_pnl, 2), floating_pnl >= 0.0 ? InpGoodColor : InpBadColor);
      UpdateTradeViewport();
      return;
   }

   if(g_pending_order.active)
   {
      NormalizePendingOrderLevels();
      int side = PendingOrderSide(g_pending_order.type);
      color entry_color = (side > 0 ? InpGoodColor : InpBadColor);
      EnsureHLine(g_entry_line_name, g_pending_order.entry_price, entry_color, STYLE_SOLID);
      if(g_pending_order.stop_loss > 0.0)
      {
         EnsureHLine(g_stop_line_name, g_pending_order.stop_loss, InpBadColor, STYLE_DOT);
         EnsureTradeText(g_stop_tag_name, g_last_replay_time, g_pending_order.stop_loss, "SL", InpBadColor);
      }
      else
      {
         ObjectDelete(g_chart_id, g_stop_line_name);
         ObjectDelete(g_chart_id, g_stop_tag_name);
      }
      if(g_pending_order.take_profit > 0.0)
      {
         EnsureHLine(g_take_line_name, g_pending_order.take_profit, InpGoodColor, STYLE_DOT);
         EnsureTradeText(g_take_tag_name, g_last_replay_time, g_pending_order.take_profit, "TP", InpGoodColor);
      }
      else
      {
         ObjectDelete(g_chart_id, g_take_line_name);
         ObjectDelete(g_chart_id, g_take_tag_name);
      }
      EnsureTradeText(g_entry_tag_name, g_pending_order.created_at, g_pending_order.entry_price, PendingOrderTypeText(g_pending_order.type) + " " + VolumeToText(g_pending_order.volume_lots), entry_color);
      EnsureTradeText(g_floating_tag_name, g_last_replay_time, g_pending_order.entry_price, "Drag entry / SL / TP", InpMetaColor);
      UpdateTradeViewport();
      return;
   }

   DeleteTradeObjects();
   UpdateTradeViewport();
}

void IncludePriceInViewportRange(const double price, double &min_price, double &max_price)
{
   if(price <= 0.0)
      return;
   if(price < min_price)
      min_price = price;
   if(price > max_price)
      max_price = price;
}

void UpdateTradeViewport()
{
   if(!g_position.open && !g_pending_order.active)
   {
      if(g_viewport_fixed)
      {
         ChartSetInteger(g_chart_id, CHART_SCALEFIX, false);
         g_viewport_fixed = false;
         g_viewport_min = 0.0;
         g_viewport_max = 0.0;
      }
      return;
   }

   double min_price = 1.0e100;
   double max_price = -1.0e100;
   MqlRates recent_rates[];
   int bars_to_scan = MathMin(Bars(g_custom_symbol, (ENUM_TIMEFRAMES)Period()), 160);
   if(bars_to_scan > 0 && CopyRates(g_custom_symbol, (ENUM_TIMEFRAMES)Period(), 0, bars_to_scan, recent_rates) > 0)
   {
      ArraySetAsSeries(recent_rates, false);
      for(int index = 0; index < ArraySize(recent_rates); index++)
      {
         IncludePriceInViewportRange(recent_rates[index].low, min_price, max_price);
         IncludePriceInViewportRange(recent_rates[index].high, min_price, max_price);
      }
   }

   IncludePriceInViewportRange(g_last_bid, min_price, max_price);
   IncludePriceInViewportRange(g_last_ask, min_price, max_price);

   if(g_position.open)
   {
      IncludePriceInViewportRange(g_position.entry_price, min_price, max_price);
      IncludePriceInViewportRange(g_position.stop_loss, min_price, max_price);
      IncludePriceInViewportRange(g_position.take_profit, min_price, max_price);
   }

   if(g_pending_order.active)
   {
      IncludePriceInViewportRange(g_pending_order.entry_price, min_price, max_price);
      IncludePriceInViewportRange(g_pending_order.stop_loss, min_price, max_price);
      IncludePriceInViewportRange(g_pending_order.take_profit, min_price, max_price);
   }

   if(min_price >= max_price)
      return;

   double point = SymbolInfoDouble(g_custom_symbol, SYMBOL_POINT);
   if(point <= 0.0)
      point = 0.00001;

   double padding = MathMax((max_price - min_price) * 0.18, point * 120.0);
   double next_min = NormalizePrice(min_price - padding);
   double next_max = NormalizePrice(max_price + padding);
   bool changed =
      !g_viewport_fixed
      || MathAbs(next_min - g_viewport_min) > (point * 2.0)
      || MathAbs(next_max - g_viewport_max) > (point * 2.0);

   if(!changed)
      return;

   ChartSetInteger(g_chart_id, CHART_SCALEFIX, true);
   ChartSetDouble(g_chart_id, CHART_FIXED_MIN, next_min);
   ChartSetDouble(g_chart_id, CHART_FIXED_MAX, next_max);
   g_viewport_fixed = true;
   g_viewport_min = next_min;
   g_viewport_max = next_max;
}

void DeleteTradeObjects()
{
   ObjectDelete(g_chart_id, g_entry_line_name);
   ObjectDelete(g_chart_id, g_stop_line_name);
   ObjectDelete(g_chart_id, g_take_line_name);
   ObjectDelete(g_chart_id, g_entry_tag_name);
   ObjectDelete(g_chart_id, g_stop_tag_name);
   ObjectDelete(g_chart_id, g_take_tag_name);
   ObjectDelete(g_chart_id, g_floating_tag_name);
}

bool EnsureHLine(const string name, const double price, const color line_color, const ENUM_LINE_STYLE style)
{
   bool exists = (ObjectFind(g_chart_id, name) >= 0);
   bool is_selected = false;
   if(exists)
      is_selected = (ObjectGetInteger(g_chart_id, name, OBJPROP_SELECTED) != 0);

   if(!exists)
   {
      ResetLastError();
      if(!ObjectCreate(g_chart_id, name, OBJ_HLINE, 0, 0, price))
      {
         Print(__FUNCTION__, ": failed to create line ", name, ". Error code = ", GetLastError());
         return(false);
      }
   }

   bool ok = true;
   if(!is_selected)
      ok = ObjectSetDouble(g_chart_id, name, OBJPROP_PRICE, price) && ok;
   ok = ObjectSetInteger(g_chart_id, name, OBJPROP_COLOR, line_color) && ok;
   ok = ObjectSetInteger(g_chart_id, name, OBJPROP_STYLE, style) && ok;
   ok = ObjectSetInteger(g_chart_id, name, OBJPROP_WIDTH, 2) && ok;
   ok = ObjectSetInteger(g_chart_id, name, OBJPROP_BACK, false) && ok;
   ok = ObjectSetInteger(g_chart_id, name, OBJPROP_SELECTABLE, true) && ok;
   ok = ObjectSetInteger(g_chart_id, name, OBJPROP_SELECTED, is_selected) && ok;
   ok = ObjectSetInteger(g_chart_id, name, OBJPROP_HIDDEN, false) && ok;
   return(ok);
}

bool EnsureTradeText(const string name, const datetime when, const double price, const string text, const color text_color)
{
   if(ObjectFind(g_chart_id, name) < 0)
   {
      ResetLastError();
      if(!ObjectCreate(g_chart_id, name, OBJ_TEXT, 0, when, price))
      {
         Print(__FUNCTION__, ": failed to create text object ", name, ". Error code = ", GetLastError());
         return(false);
      }
   }

   bool ok = true;
   ok = ObjectMove(g_chart_id, name, 0, when, price) && ok;
   ok = ObjectSetString(g_chart_id, name, OBJPROP_TEXT, text) && ok;
   ok = ObjectSetString(g_chart_id, name, OBJPROP_FONT, InpUiFont) && ok;
   ok = ObjectSetInteger(g_chart_id, name, OBJPROP_FONTSIZE, InpMetaFontSize) && ok;
   ok = ObjectSetInteger(g_chart_id, name, OBJPROP_COLOR, text_color) && ok;
   ok = ObjectSetInteger(g_chart_id, name, OBJPROP_BACK, false) && ok;
   ok = ObjectSetInteger(g_chart_id, name, OBJPROP_SELECTABLE, false) && ok;
   ok = ObjectSetInteger(g_chart_id, name, OBJPROP_HIDDEN, false) && ok;
   return(ok);
}

bool BuildInterface()
{
   int panel_x = ResolvePanelXDistance(InpPanelWidth);
   int panel_y = ResolvePanelYDistance(InpPanelHeight);
   int padding_x = 14;
   int header_y = 10;
   int meta_y = 28;
   int quote_y = 46;
   int section_y = 68;
   int row1_y = 88;
   int row2_y = 116;
   int row3_y = 144;
   int footer_y = 178;
   int report_y = 202;
   int section_gap = 14;
   int orders_width = 160;
   int stats_width = 182;
   int replay_width = InpPanelWidth - (padding_x * 2) - (section_gap * 2) - orders_width - stats_width;
   int orders_x = padding_x;
   int stats_x = orders_x + orders_width + section_gap;
   int replay_x = stats_x + stats_width + section_gap;
   int divider_y = 64;
   int divider_height = 110;
   int divider_left_x = stats_x - 7;
   int divider_right_x = replay_x - 7;
   int order_gap = 6;
   int order_top_width = (orders_width - (order_gap * 2)) / 3;
   int order_small_width = (orders_width - order_gap) / 2;
   int replay_gap = 6;
   int replay_top_width = (replay_width - (replay_gap * 2)) / 3;
   int replay_wide_width = (replay_width - replay_gap) / 2;
   int volume_button_width = 44;
   bool ok = true;

   ok = EnsureRectangleLabel(g_panel_name, panel_x, panel_y, InpPanelWidth, InpPanelHeight, InpPanelColor, InpBorderColor, 1) && ok;
   ok = EnsureRectangleLabel(g_accent_name, ResolveInnerX(0), ResolveInnerY(0), InpPanelWidth, 3, InpAccentColor, InpAccentColor, 0) && ok;
   ok = EnsureRectangleLabel(g_separator_left_name, ResolveInnerX(divider_left_x), ResolveInnerY(divider_y), 1, divider_height, InpBorderColor, InpBorderColor, 0) && ok;
   ok = EnsureRectangleLabel(g_separator_right_name, ResolveInnerX(divider_right_x), ResolveInnerY(divider_y), 1, divider_height, InpBorderColor, InpBorderColor, 0) && ok;

   ok = EnsureTextLabel(g_title_name, ResolveInnerX(14), ResolveInnerY(header_y), InpUiFont, InpTitleFontSize, InpTextColor) && ok;
   ok = EnsureTextLabel(g_meta_name, ResolveInnerX(14), ResolveInnerY(meta_y), InpUiFont, InpMetaFontSize, InpMetaColor) && ok;
   ok = EnsureTextLabel(g_quote_name, ResolveInnerX(14), ResolveInnerY(quote_y), InpUiFont, InpMetaFontSize, InpTextColor) && ok;

   ok = EnsureTextLabel(g_orders_heading_name, ResolveInnerX(orders_x), ResolveInnerY(section_y), InpUiFont, InpMetaFontSize, InpMetaColor) && ok;
   ok = EnsureTextLabel(g_stats_heading_name, ResolveInnerX(stats_x), ResolveInnerY(section_y), InpUiFont, InpMetaFontSize, InpMetaColor) && ok;
   ok = EnsureTextLabel(g_replay_heading_name, ResolveInnerX(replay_x), ResolveInnerY(section_y), InpUiFont, InpMetaFontSize, InpMetaColor) && ok;
   ok = EnsureTextLabel(g_mode_name, ResolveInnerX(replay_x), ResolveInnerY(footer_y), InpUiFont, InpMetaFontSize, InpTextColor) && ok;

   ok = EnsureButton(g_buy_button_name, "BUY", ResolveInnerX(orders_x), ResolveInnerY(row1_y), order_top_width, 22) && ok;
   ok = EnsureButton(g_sell_button_name, "SELL", ResolveInnerX(orders_x + order_top_width + order_gap), ResolveInnerY(row1_y), order_top_width, 22) && ok;
   ok = EnsureButton(g_close_button_name, "CLOSE", ResolveInnerX(orders_x + ((order_top_width + order_gap) * 2)), ResolveInnerY(row1_y), order_top_width, 22) && ok;
   ok = EnsureButton(g_buy_limit_button_name, "B-LMT", ResolveInnerX(orders_x), ResolveInnerY(row2_y), order_small_width, 22) && ok;
   ok = EnsureButton(g_sell_limit_button_name, "S-LMT", ResolveInnerX(orders_x + order_small_width + order_gap), ResolveInnerY(row2_y), order_small_width, 22) && ok;
   ok = EnsureButton(g_buy_stop_button_name, "B-STP", ResolveInnerX(orders_x), ResolveInnerY(row3_y), order_small_width, 22) && ok;
   ok = EnsureButton(g_sell_stop_button_name, "S-STP", ResolveInnerX(orders_x + order_small_width + order_gap), ResolveInnerY(row3_y), order_small_width, 22) && ok;

   ok = EnsureTextLabel(g_volume_name, ResolveInnerX(stats_x), ResolveInnerY(row1_y + 3), InpUiFont, InpMetaFontSize, InpMetaColor) && ok;
   ok = EnsureButton(g_volume_down_button_name, "VOL-", ResolveInnerX(stats_x + stats_width - ((volume_button_width * 2) + replay_gap)), ResolveInnerY(row1_y), volume_button_width, 22) && ok;
   ok = EnsureButton(g_volume_up_button_name, "VOL+", ResolveInnerX(stats_x + stats_width - volume_button_width), ResolveInnerY(row1_y), volume_button_width, 22) && ok;
   ok = EnsureTextLabel(g_position_name, ResolveInnerX(stats_x), ResolveInnerY(row2_y + 3), InpUiFont, InpMetaFontSize, InpTextColor) && ok;
   ok = EnsureTextLabel(g_balance_name, ResolveInnerX(stats_x), ResolveInnerY(row3_y + 3), InpUiFont, InpMetaFontSize, InpMetaColor) && ok;

   ok = EnsureButton(g_back_button_name, "BACK", ResolveInnerX(replay_x), ResolveInnerY(row1_y), replay_top_width, 22) && ok;
   ok = EnsureButton(g_play_button_name, "PLAY", ResolveInnerX(replay_x + replay_top_width + replay_gap), ResolveInnerY(row1_y), replay_top_width, 22) && ok;
   ok = EnsureButton(g_step_button_name, "STEP", ResolveInnerX(replay_x + ((replay_top_width + replay_gap) * 2)), ResolveInnerY(row1_y), replay_top_width, 22) && ok;
   ok = EnsureButton(g_reset_button_name, "RESET", ResolveInnerX(replay_x), ResolveInnerY(row2_y), replay_wide_width, 22) && ok;
   ok = EnsureButton(g_mode_button_name, "MODE", ResolveInnerX(replay_x + replay_wide_width + replay_gap), ResolveInnerY(row2_y), replay_wide_width, 22) && ok;
   ok = EnsureButton(g_speed_down_button_name, "S-", ResolveInnerX(replay_x), ResolveInnerY(row3_y), replay_wide_width, 22) && ok;
   ok = EnsureButton(g_speed_up_button_name, "S+", ResolveInnerX(replay_x + replay_wide_width + replay_gap), ResolveInnerY(row3_y), replay_wide_width, 22) && ok;

   ok = EnsureTextLabel(g_status_name, ResolveInnerX(14), ResolveInnerY(footer_y), InpUiFont, InpMetaFontSize, InpAccentColor) && ok;
   ok = EnsureButton(g_report_button_name, "REPORT", ResolveInnerX(14), ResolveInnerY(report_y), InpPanelWidth - 28, 18) && ok;

   return(ok);
}

void DeleteInterface()
{
   ObjectDelete(g_chart_id, g_panel_name);
   ObjectDelete(g_chart_id, g_accent_name);
   ObjectDelete(g_chart_id, g_orders_heading_name);
   ObjectDelete(g_chart_id, g_stats_heading_name);
   ObjectDelete(g_chart_id, g_replay_heading_name);
   ObjectDelete(g_chart_id, g_balance_name);
   ObjectDelete(g_chart_id, g_volume_edit_name);
   ObjectDelete(g_chart_id, g_separator_left_name);
   ObjectDelete(g_chart_id, g_separator_right_name);
   ObjectDelete(g_chart_id, g_title_name);
   ObjectDelete(g_chart_id, g_meta_name);
   ObjectDelete(g_chart_id, g_quote_name);
   ObjectDelete(g_chart_id, g_mode_name);
   ObjectDelete(g_chart_id, g_position_name);
   ObjectDelete(g_chart_id, g_volume_name);
   ObjectDelete(g_chart_id, g_hotkeys_name);
   ObjectDelete(g_chart_id, g_report_button_name);
   ObjectDelete(g_chart_id, g_status_name);
   ObjectDelete(g_chart_id, g_play_button_name);
   ObjectDelete(g_chart_id, g_back_button_name);
   ObjectDelete(g_chart_id, g_step_button_name);
   ObjectDelete(g_chart_id, g_reset_button_name);
   ObjectDelete(g_chart_id, g_mode_button_name);
   ObjectDelete(g_chart_id, g_volume_down_button_name);
   ObjectDelete(g_chart_id, g_volume_up_button_name);
   ObjectDelete(g_chart_id, g_speed_down_button_name);
   ObjectDelete(g_chart_id, g_speed_up_button_name);
   ObjectDelete(g_chart_id, g_buy_button_name);
   ObjectDelete(g_chart_id, g_sell_button_name);
   ObjectDelete(g_chart_id, g_close_button_name);
   ObjectDelete(g_chart_id, g_buy_limit_button_name);
   ObjectDelete(g_chart_id, g_sell_limit_button_name);
   ObjectDelete(g_chart_id, g_buy_stop_button_name);
   ObjectDelete(g_chart_id, g_sell_stop_button_name);
}

void UpdateInterface()
{
   ObjectSetString(g_chart_id, g_title_name, OBJPROP_TEXT, "AT Chart Replay");
   ObjectSetString(g_chart_id, g_meta_name, OBJPROP_TEXT, g_source_symbol + " -> " + g_custom_symbol + " | " + ReplayPeriodText());

   string quote_text = "Bid " + PriceToText(g_last_bid) + " | Ask " + PriceToText(g_last_ask) + " | " + TimeToString(g_last_replay_time, TIME_DATE | TIME_MINUTES);
   ObjectSetString(g_chart_id, g_quote_name, OBJPROP_TEXT, quote_text);

   string mode_text = (g_step_mode == REPLAY_MODE_TICK ? "TICK" : "BAR");
   mode_text += " | x" + IntegerToString(g_playback_units);
   mode_text += " | " + (g_is_playing ? "playing" : "paused");
   if(g_step_mode == REPLAY_MODE_TICK)
   {
      if(g_has_real_ticks)
         mode_text += " | real";
      else if(g_using_synthetic_ticks)
         mode_text += " | synth";
      else
         mode_text += " | no ticks";
   }
   ObjectSetString(g_chart_id, g_orders_heading_name, OBJPROP_TEXT, "ORDERS");
   ObjectSetString(g_chart_id, g_stats_heading_name, OBJPROP_TEXT, "POSITION");
   ObjectSetString(g_chart_id, g_replay_heading_name, OBJPROP_TEXT, "REPLAY");
   ObjectSetString(g_chart_id, g_mode_name, OBJPROP_TEXT, mode_text);
   ObjectSetString(g_chart_id, g_position_name, OBJPROP_TEXT, PositionSummaryText());
   ObjectSetString(g_chart_id, g_balance_name, OBJPROP_TEXT, BalanceSummaryText());
   ObjectSetString(g_chart_id, g_volume_name, OBJPROP_TEXT, "Lots " + VolumeToText(g_trade_volume_lots));
   ObjectSetString(g_chart_id, g_report_button_name, OBJPROP_TEXT, ReportSummaryText());

   ColorizeButton(g_report_button_name, ReportSummaryButtonColor(), InpTextColor);
   ColorizeButton(g_back_button_name, InpNeutralButtonColor, InpTextColor);
   ColorizeButton(g_play_button_name, g_is_playing ? InpAccentColor : InpNeutralButtonColor, g_is_playing ? InpPanelColor : InpTextColor);
   ColorizeButton(g_step_button_name, InpNeutralButtonColor, InpTextColor);
   ColorizeButton(g_reset_button_name, InpNeutralButtonColor, InpTextColor);
   ColorizeButton(g_mode_button_name, InpNeutralButtonColor, InpTextColor);
   ColorizeButton(g_volume_down_button_name, InpNeutralButtonColor, InpTextColor);
   ColorizeButton(g_volume_up_button_name, InpNeutralButtonColor, InpTextColor);
   ColorizeButton(g_speed_down_button_name, InpNeutralButtonColor, InpTextColor);
   ColorizeButton(g_speed_up_button_name, InpNeutralButtonColor, InpTextColor);
   ColorizeButton(g_buy_button_name, InpGoodColor, InpTextColor);
   ColorizeButton(g_sell_button_name, InpBadColor, InpTextColor);
   ColorizeButton(g_close_button_name, InpNeutralButtonColor, InpTextColor);
   ColorizeButton(g_buy_limit_button_name, InpGoodColor, InpTextColor);
   ColorizeButton(g_sell_limit_button_name, InpBadColor, InpTextColor);
   ColorizeButton(g_buy_stop_button_name, InpGoodColor, InpTextColor);
   ColorizeButton(g_sell_stop_button_name, InpBadColor, InpTextColor);

   DrawTradeObjects();
   RefreshSessionReportWindow(false);
   ChartRedraw(g_chart_id);
}

string PositionSummaryText()
{
   if(!g_position.open && !g_pending_order.active)
      return("Flat | next " + VolumeToText(g_trade_volume_lots));

   if(g_pending_order.active)
   {
      string pending_text =
         PendingOrderTypeText(g_pending_order.type)
         + " " + VolumeToText(g_pending_order.volume_lots)
         + " @ " + PriceToText(g_pending_order.entry_price);
      if(g_pending_order.stop_loss > 0.0)
         pending_text += " | SL " + PriceToText(g_pending_order.stop_loss);
      if(g_pending_order.take_profit > 0.0)
         pending_text += " | TP " + PriceToText(g_pending_order.take_profit);
      return(pending_text);
   }

   double floating = CalculatePositionPnl(g_position.side > 0 ? g_last_bid : g_last_ask);
   string side = (g_position.side > 0 ? "LONG" : "SHORT");
   string text =
      side
      + " " + VolumeToText(g_position.volume_lots)
      + " @ " + PriceToText(g_position.entry_price)
      + " | F " + DoubleToString(floating, 2);
   if(g_position.stop_loss > 0.0)
      text += " | SL " + PriceToText(g_position.stop_loss);
   if(g_position.take_profit > 0.0)
      text += " | TP " + PriceToText(g_position.take_profit);
   return(text);
}

double FloatingPnlValue()
{
   if(!g_position.open)
      return(0.0);
   return(CalculatePositionPnl(g_position.side > 0 ? g_last_bid : g_last_ask));
}

double SimulatedBalanceValue()
{
   return(g_simulated_balance_start + g_realized_pnl);
}

string BalanceSummaryText()
{
   return(
      "Bal " + DoubleToString(SimulatedBalanceValue(), 2)
      + " | P/L " + DoubleToString(g_realized_pnl + FloatingPnlValue(), 2)
   );
}

void RecordClosedTrade(const string reason, const double exit_price, const double pnl)
{
   ATSimClosedTrade trade;
   trade.side = g_position.side;
   trade.volume_lots = g_position.volume_lots;
   trade.opened_at = g_position.opened_at;
   trade.closed_at = g_last_replay_time;
   trade.entry_price = g_position.entry_price;
   trade.exit_price = exit_price;
   trade.stop_loss = g_position.stop_loss;
   trade.take_profit = g_position.take_profit;
   trade.pnl = pnl;
   trade.balance_after = SimulatedBalanceValue();
   trade.exit_reason = reason;

   if(g_closed_trade_count >= ArraySize(g_closed_trades))
      ArrayResize(g_closed_trades, g_closed_trade_count + 1);
   g_closed_trades[g_closed_trade_count] = trade;
   g_closed_trade_count++;
}

void ComputeSessionReportStats(ATSessionReportStats &stats)
{
   ZeroMemory(stats);
   stats.ending_balance = SimulatedBalanceValue();
   stats.current_equity = stats.ending_balance + FloatingPnlValue();
   stats.peak_balance = g_simulated_balance_start;

   bool has_trade = false;
   for(int index = 0; index < g_closed_trade_count; index++)
   {
      ATSimClosedTrade trade = g_closed_trades[index];
      stats.total_trades++;
      if(trade.side > 0)
         stats.long_trades++;
      else if(trade.side < 0)
         stats.short_trades++;

      if(trade.pnl > 0.0)
      {
         stats.wins++;
         stats.gross_profit += trade.pnl;
      }
      else if(trade.pnl < 0.0)
      {
         stats.losses++;
         stats.gross_loss += -trade.pnl;
      }

      if(!has_trade || trade.pnl > stats.best_trade)
         stats.best_trade = trade.pnl;
      if(!has_trade || trade.pnl < stats.worst_trade)
         stats.worst_trade = trade.pnl;
      has_trade = true;

      if(trade.balance_after > stats.peak_balance)
         stats.peak_balance = trade.balance_after;
      double drawdown = stats.peak_balance - trade.balance_after;
      if(drawdown > stats.max_drawdown)
         stats.max_drawdown = drawdown;
   }

   stats.net_profit = g_realized_pnl;
   if(stats.total_trades > 0)
      stats.win_rate = (100.0 * stats.wins) / stats.total_trades;
   if(stats.wins > 0)
      stats.avg_win = stats.gross_profit / stats.wins;
   if(stats.losses > 0)
      stats.avg_loss = stats.gross_loss / stats.losses;
   if(stats.gross_loss > 0.0)
      stats.profit_factor = stats.gross_profit / stats.gross_loss;
}

string SignedMoneyText(const double value)
{
   if(value > 0.0)
      return("+" + DoubleToString(value, 2));
   return(DoubleToString(value, 2));
}

string ProfitFactorText(const ATSessionReportStats &stats)
{
   if(stats.gross_loss <= 0.0)
   {
      if(stats.gross_profit > 0.0)
         return("INF");
      return("0.00");
   }
   return(DoubleToString(stats.profit_factor, 2));
}

string ReportSummaryText()
{
   ATSessionReportStats stats;
   ComputeSessionReportStats(stats);

   if(stats.total_trades <= 0)
      return("REPORT | 0 trades | Net 0.00");

   return(
      "REPORT | "
      + IntegerToString(stats.total_trades)
      + " trades | Win "
      + DoubleToString(stats.win_rate, 1)
      + "% | Net "
      + SignedMoneyText(stats.net_profit)
   );
}

color ReportSummaryButtonColor()
{
   ATSessionReportStats stats;
   ComputeSessionReportStats(stats);
   if(stats.total_trades <= 0)
      return(InpNeutralButtonColor);
   return(stats.net_profit >= 0.0 ? InpGoodColor : InpBadColor);
}

string PadRightText(const string value, const int width)
{
   string output = value;
   while(StringLen(output) < width)
      output += " ";
   return(output);
}

string PadLeftText(const string value, const int width)
{
   string output = value;
   while(StringLen(output) < width)
      output = " " + output;
   return(output);
}

string ShortReportTimeText(const datetime value)
{
   return(TimeToString(value, TIME_MINUTES));
}

string ReportReasonText(const string reason)
{
   string label = reason;
   StringReplace(label, "bar_conflict_stop_first", "bar stop");
   StringReplace(label, "_", " ");
   return(label);
}

bool SessionReportChartIsOpen()
{
   if(g_report_chart_id <= 0)
      return(false);

   string symbol_name = ChartSymbol(g_report_chart_id);
   if(symbol_name == "")
   {
      g_report_chart_id = 0;
      return(false);
   }

   return(true);
}

string ReportObjectName(const string suffix)
{
   return(g_prefix + "_report_" + suffix);
}

void DeleteReportObjects(const long chart_id)
{
   if(chart_id <= 0)
      return;

   g_report_graph_canvas.Destroy();
   ObjectDelete(chart_id, ReportObjectName("graph_canvas"));
   ObjectDelete(chart_id, ReportObjectName("background"));
   ObjectDelete(chart_id, ReportObjectName("accent"));
   ObjectDelete(chart_id, ReportObjectName("title"));
   ObjectDelete(chart_id, ReportObjectName("meta"));
    ObjectDelete(chart_id, ReportObjectName("section"));
   ObjectDelete(chart_id, ReportObjectName("stats_box"));
   ObjectDelete(chart_id, ReportObjectName("graph_box"));
   ObjectDelete(chart_id, ReportObjectName("graph_title"));
   ObjectDelete(chart_id, ReportObjectName("graph_note"));
   ObjectDelete(chart_id, ReportObjectName("graph_min"));
   ObjectDelete(chart_id, ReportObjectName("graph_max"));
   ObjectDelete(chart_id, ReportObjectName("graph_current"));
   ObjectDelete(chart_id, ReportObjectName("trades_box"));
   ObjectDelete(chart_id, ReportObjectName("trade_header"));
   ObjectDelete(chart_id, ReportObjectName("footer"));

   for(int index = 0; index < 8; index++)
   {
      ObjectDelete(chart_id, ReportObjectName("summary_left_" + IntegerToString(index)));
      ObjectDelete(chart_id, ReportObjectName("summary_right_" + IntegerToString(index)));
   }
   for(int index = 0; index < 512; index++)
   {
      ObjectDelete(chart_id, ReportObjectName("trade_row_" + IntegerToString(index)));
      ObjectDelete(chart_id, ReportObjectName("graph_s_" + IntegerToString(index)));
      ObjectDelete(chart_id, ReportObjectName("graph_h_" + IntegerToString(index)));
      ObjectDelete(chart_id, ReportObjectName("graph_v_" + IntegerToString(index)));
      ObjectDelete(chart_id, ReportObjectName("graph_p_" + IntegerToString(index)));
   }
}

bool EnsureReportRectangle(
   const long chart_id,
   const string name,
   const int x,
   const int y,
   const int width,
   const int height,
   const color background_color,
   const color border_color,
   const int border_width
)
{
   if(ObjectFind(chart_id, name) < 0)
   {
      ResetLastError();
      if(!ObjectCreate(chart_id, name, OBJ_RECTANGLE_LABEL, 0, 0, 0))
         return(false);
   }

   bool ok = true;
   ok = ObjectSetInteger(chart_id, name, OBJPROP_CORNER, CORNER_LEFT_UPPER) && ok;
   ok = ObjectSetInteger(chart_id, name, OBJPROP_XDISTANCE, x) && ok;
   ok = ObjectSetInteger(chart_id, name, OBJPROP_YDISTANCE, y) && ok;
   ok = ObjectSetInteger(chart_id, name, OBJPROP_XSIZE, width) && ok;
   ok = ObjectSetInteger(chart_id, name, OBJPROP_YSIZE, height) && ok;
   ok = ObjectSetInteger(chart_id, name, OBJPROP_BGCOLOR, background_color) && ok;
   ok = ObjectSetInteger(chart_id, name, OBJPROP_BORDER_TYPE, BORDER_FLAT) && ok;
   ok = ObjectSetInteger(chart_id, name, OBJPROP_COLOR, border_color) && ok;
   ok = ObjectSetInteger(chart_id, name, OBJPROP_WIDTH, border_width) && ok;
   ok = ObjectSetInteger(chart_id, name, OBJPROP_BACK, false) && ok;
   ok = ObjectSetInteger(chart_id, name, OBJPROP_SELECTABLE, false) && ok;
   ok = ObjectSetInteger(chart_id, name, OBJPROP_SELECTED, false) && ok;
   ok = ObjectSetInteger(chart_id, name, OBJPROP_HIDDEN, false) && ok;
   return(ok);
}

bool EnsureReportLabel(
   const long chart_id,
   const string name,
   const string text,
   const int x,
   const int y,
   const string font_name,
   const int font_size,
   const color text_color
)
{
   if(ObjectFind(chart_id, name) < 0)
   {
      ResetLastError();
      if(!ObjectCreate(chart_id, name, OBJ_LABEL, 0, 0, 0))
         return(false);
   }

   bool ok = true;
   ok = ObjectSetInteger(chart_id, name, OBJPROP_CORNER, CORNER_LEFT_UPPER) && ok;
   ok = ObjectSetInteger(chart_id, name, OBJPROP_XDISTANCE, x) && ok;
   ok = ObjectSetInteger(chart_id, name, OBJPROP_YDISTANCE, y) && ok;
   ok = ObjectSetInteger(chart_id, name, OBJPROP_COLOR, text_color) && ok;
   ok = ObjectSetInteger(chart_id, name, OBJPROP_FONTSIZE, font_size) && ok;
   ok = ObjectSetString(chart_id, name, OBJPROP_FONT, font_name) && ok;
   ok = ObjectSetString(chart_id, name, OBJPROP_TEXT, text) && ok;
   ok = ObjectSetInteger(chart_id, name, OBJPROP_BACK, false) && ok;
   ok = ObjectSetInteger(chart_id, name, OBJPROP_SELECTABLE, false) && ok;
   ok = ObjectSetInteger(chart_id, name, OBJPROP_SELECTED, false) && ok;
   ok = ObjectSetInteger(chart_id, name, OBJPROP_HIDDEN, false) && ok;
   return(ok);
}

bool EnsureSessionReportChart()
{
   if(SessionReportChartIsOpen())
      return(true);

   g_report_chart_id = ChartOpen(g_custom_symbol, ReplayBarPeriod());
   if(g_report_chart_id <= 0)
   {
      SetStatus("Could not open the session report window.", InpWarnColor);
      return(false);
   }

   for(int attempt = 0; attempt < 20; attempt++)
   {
      if(ChartSymbol(g_report_chart_id) != "")
         break;
      Sleep(50);
   }

   ChartSetInteger(g_report_chart_id, CHART_AUTOSCROLL, false);
   ChartSetInteger(g_report_chart_id, CHART_SHIFT, false);
   return(true);
}

int ReportChartWidthPixels()
{
   if(!SessionReportChartIsOpen())
      return(1280);

   long width = ChartGetInteger(g_report_chart_id, CHART_WIDTH_IN_PIXELS, 0);
   if(width <= 0)
      return(1280);
   return((int)width);
}

int ReportChartHeightPixels()
{
   if(!SessionReportChartIsOpen())
      return(720);

   long height = ChartGetInteger(g_report_chart_id, CHART_HEIGHT_IN_PIXELS, 0);
   if(height <= 0)
      return(720);
   return((int)height);
}

void RenderBalanceGraph(
   const int graph_x,
   const int graph_y,
   const int graph_width,
   const int graph_height,
   const color line_color,
   const color point_color,
   const color text_color,
   const color sub_color
)
{
   datetime point_times[];
   double point_balances[];
   ArrayResize(point_times, g_closed_trade_count + 2);
   ArrayResize(point_balances, g_closed_trade_count + 2);

   int point_count = 0;
   point_times[point_count] = g_replay_start_minute;
   point_balances[point_count] = g_simulated_balance_start;
   point_count++;

   for(int index = 0; index < g_closed_trade_count; index++)
   {
      point_times[point_count] = g_closed_trades[index].closed_at;
      point_balances[point_count] = g_closed_trades[index].balance_after;
      point_count++;
   }

   double current_balance = g_simulated_balance_start + g_realized_pnl + FloatingPnlValue();
   if(point_count <= 1 || point_times[point_count - 1] != g_last_replay_time || MathAbs(point_balances[point_count - 1] - current_balance) > 0.0001)
   {
      point_times[point_count] = g_last_replay_time;
      point_balances[point_count] = current_balance;
      point_count++;
   }

   if(point_count <= 0)
      return;

   datetime min_time = point_times[0];
   datetime max_time = point_times[point_count - 1];
   double min_balance = point_balances[0];
   double max_balance = point_balances[0];
   for(int index = 1; index < point_count; index++)
   {
      if(point_times[index] < min_time)
         min_time = point_times[index];
      if(point_times[index] > max_time)
         max_time = point_times[index];
      if(point_balances[index] < min_balance)
         min_balance = point_balances[index];
      if(point_balances[index] > max_balance)
         max_balance = point_balances[index];
   }

   if(max_time <= min_time)
      max_time = min_time + 60;
   double balance_span = max_balance - min_balance;
   if(balance_span <= 0.0)
      balance_span = MathMax(MathAbs(max_balance) * 0.01, 1.0);

   double balance_padding = MathMax(balance_span * 0.12, 1.0);
   min_balance -= balance_padding;
   max_balance += balance_padding;
   balance_span = max_balance - min_balance;

   int inner_left = graph_x + 18;
   int inner_right = graph_x + graph_width - 18;
   int inner_top = graph_y + 44;
   int inner_bottom = graph_y + graph_height - 34;
   int inner_width = MathMax(inner_right - inner_left, 1);
   int inner_height = MathMax(inner_bottom - inner_top, 1);
   int graph_title_y = graph_y + 10;
   int graph_scale_top_y = graph_y + 26;
   int graph_footer_y = graph_y + graph_height - 18;

   EnsureReportLabel(g_report_chart_id, ReportObjectName("graph_title"), "Balance Curve", graph_x + 14, graph_title_y, InpUiFont, 11, text_color);
   EnsureReportLabel(g_report_chart_id, ReportObjectName("graph_note"), "Closed trades plus current equity", graph_x + graph_width - 220, graph_title_y, "Consolas", 9, sub_color);
   EnsureReportLabel(g_report_chart_id, ReportObjectName("graph_max"), DoubleToString(max_balance, 2), graph_x + 14, graph_scale_top_y, "Consolas", 8, sub_color);
   EnsureReportLabel(g_report_chart_id, ReportObjectName("graph_min"), DoubleToString(min_balance, 2), graph_x + 14, graph_footer_y, "Consolas", 8, sub_color);
   EnsureReportLabel(g_report_chart_id, ReportObjectName("graph_current"), "Current " + DoubleToString(current_balance, 2), graph_x + graph_width - 150, graph_footer_y, "Consolas", 8, sub_color);

   int point_xs[];
   int point_ys[];
   ArrayResize(point_xs, point_count);
   ArrayResize(point_ys, point_count);

   for(int index = 0; index < point_count; index++)
   {
      double time_ratio = (double)(point_times[index] - min_time) / (double)(max_time - min_time);
      double balance_ratio = (point_balances[index] - min_balance) / balance_span;

      point_xs[index] = inner_left + (int)MathRound(time_ratio * inner_width);
      point_ys[index] = inner_bottom - (int)MathRound(balance_ratio * inner_height);
   }

   for(int index = 0; index < 2048; index++)
   {
      ObjectDelete(g_report_chart_id, ReportObjectName("graph_s_" + IntegerToString(index)));
      ObjectDelete(g_report_chart_id, ReportObjectName("graph_h_" + IntegerToString(index)));
      ObjectDelete(g_report_chart_id, ReportObjectName("graph_v_" + IntegerToString(index)));
      ObjectDelete(g_report_chart_id, ReportObjectName("graph_p_" + IntegerToString(index)));
   }

   string canvas_name = ReportObjectName("graph_canvas");
   g_report_graph_canvas.Destroy();
   ObjectDelete(g_report_chart_id, canvas_name);
   if(!g_report_graph_canvas.CreateBitmapLabel(
      g_report_chart_id,
      0,
      canvas_name,
      inner_left,
      inner_top,
      inner_width + 1,
      inner_height + 1,
      COLOR_FORMAT_XRGB_NOALPHA
   ))
      return;

   g_report_graph_canvas.Erase(COLOR2RGB(C'246,248,252'));

   for(int index = 1; index < point_count; index++)
   {
      int previous_x = point_xs[index - 1] - inner_left;
      int previous_y = point_ys[index - 1] - inner_top;
      int current_x = point_xs[index] - inner_left;
      int current_y = point_ys[index] - inner_top;
      g_report_graph_canvas.LineThick(previous_x, previous_y, current_x, current_y, COLOR2RGB(line_color), 2, UINT_MAX, LINE_END_ROUND);
   }

   for(int index = 0; index < point_count; index++)
   {
      int marker_x = point_xs[index] - inner_left;
      int marker_y = point_ys[index] - inner_top;
      g_report_graph_canvas.FillRectangle(marker_x - 2, marker_y - 2, marker_x + 2, marker_y + 2, COLOR2RGB(point_color));
   }

   g_report_graph_canvas.Update(false);
}

string FormatTradeRowText(const int ordinal, const ATSimClosedTrade &trade)
{
   string row = "";
   row += PadLeftText(IntegerToString(ordinal), 2) + "  ";
   row += PadRightText(trade.side > 0 ? "LONG" : "SHORT", 5) + "  ";
   row += PadLeftText(VolumeToText(trade.volume_lots), 4) + "  ";
   row += PadRightText(ShortReportTimeText(trade.opened_at), 5) + "  ";
   row += PadRightText(ShortReportTimeText(trade.closed_at), 5) + "  ";
   row += PadLeftText(PriceToText(trade.entry_price), 10) + "  ";
   row += PadLeftText(PriceToText(trade.exit_price), 10) + "  ";
   row += PadLeftText(SignedMoneyText(trade.pnl), 9) + "  ";
   row += ReportReasonText(trade.exit_reason);
   return(row);
}

void RenderSessionReportWindow()
{
   if(!SessionReportChartIsOpen())
      return;

   ATSessionReportStats stats;
   ComputeSessionReportStats(stats);

   const string title_font = InpUiFont;
   const string mono_font = "Consolas";
   int panel_x = 0;
   int panel_y = 0;
   int panel_width = ReportChartWidthPixels();
   int panel_height = ReportChartHeightPixels();
   if(panel_width < 900)
      panel_width = 900;
   if(panel_height < 640)
      panel_height = 640;

   const int outer_padding = 18;
   const int inner_padding = 16;
   const int header_height = 66;
   const int gap = 16;
   int stats_width = MathMax((int)(panel_width * 0.27), 320);
   int content_width = panel_width - (outer_padding * 2);
   if(stats_width > content_width - 260)
      stats_width = MathMax(280, content_width / 3);
   int top_y = outer_padding + header_height;
   int graph_height = MathMax((int)(panel_height * 0.32), 210);
   int graph_x = outer_padding + stats_width + gap;
   int graph_width = panel_width - graph_x - outer_padding;
   int stats_height = graph_height;
   int stats_left_x = outer_padding + inner_padding;
   int stats_right_x = outer_padding + (stats_width / 2) + 12;
   int trades_y = top_y + graph_height + gap;
   int trades_height = panel_height - trades_y - outer_padding - 24;
   const color panel_bg = clrWhite;
   const color panel_border = C'198,205,214';
   const color section_bg = C'246,248,252';
   const color text_color = C'36,43,52';
   const color sub_color = C'92,105,122';
   const color gain_color = C'28,134,88';
   const color loss_color = C'176,58,72';

   EnsureReportRectangle(g_report_chart_id, ReportObjectName("background"), panel_x, panel_y, panel_width, panel_height, panel_bg, panel_bg, 0);
   EnsureReportRectangle(g_report_chart_id, ReportObjectName("accent"), panel_x, panel_y, panel_width, 4, InpAccentColor, InpAccentColor, 0);
   EnsureReportRectangle(g_report_chart_id, ReportObjectName("stats_box"), outer_padding, top_y, stats_width, stats_height, section_bg, panel_border, 1);
   EnsureReportRectangle(g_report_chart_id, ReportObjectName("graph_box"), graph_x, top_y, graph_width, graph_height, section_bg, panel_border, 1);
   EnsureReportRectangle(g_report_chart_id, ReportObjectName("trades_box"), outer_padding, trades_y, panel_width - (outer_padding * 2), trades_height, section_bg, panel_border, 1);

   EnsureReportLabel(g_report_chart_id, ReportObjectName("title"), "AT Replay Session Report", outer_padding, outer_padding + 16, title_font, 16, text_color);
   EnsureReportLabel(
      g_report_chart_id,
      ReportObjectName("meta"),
      g_source_symbol + " -> " + g_custom_symbol + " | " + ReplayPeriodText()
      + " | " + TimeToString(InpReplayStartTime, TIME_DATE | TIME_MINUTES)
      + " -> " + TimeToString(g_last_replay_time, TIME_DATE | TIME_MINUTES),
      outer_padding,
      outer_padding + 42,
      mono_font,
      9,
      sub_color
   );

   string left_rows[8];
   left_rows[0] = "Start Balance  " + DoubleToString(g_simulated_balance_start, 2);
   left_rows[1] = "End Balance    " + DoubleToString(stats.ending_balance, 2);
   left_rows[2] = "Current Equity " + DoubleToString(stats.current_equity, 2);
   left_rows[3] = "Net Profit     " + SignedMoneyText(stats.net_profit);
   left_rows[4] = "Gross Profit   " + DoubleToString(stats.gross_profit, 2);
   left_rows[5] = "Gross Loss     " + DoubleToString(stats.gross_loss, 2);
   left_rows[6] = "Profit Factor  " + ProfitFactorText(stats);
   left_rows[7] = "Max Drawdown   " + DoubleToString(stats.max_drawdown, 2);

   string right_rows[8];
   right_rows[0] = "Trades         " + IntegerToString(stats.total_trades);
   right_rows[1] = "Wins / Losses  " + IntegerToString(stats.wins) + " / " + IntegerToString(stats.losses);
   right_rows[2] = "Win Rate       " + DoubleToString(stats.win_rate, 1) + "%";
   right_rows[3] = "Long / Short   " + IntegerToString(stats.long_trades) + " / " + IntegerToString(stats.short_trades);
   right_rows[4] = "Best Trade     " + SignedMoneyText(stats.best_trade);
   right_rows[5] = "Worst Trade    " + SignedMoneyText(stats.worst_trade);
   right_rows[6] = "Avg Win        " + DoubleToString(stats.avg_win, 2);
   right_rows[7] = "Avg Loss       " + DoubleToString(stats.avg_loss, 2);

   for(int row = 0; row < 8; row++)
   {
      EnsureReportLabel(
         g_report_chart_id,
         ReportObjectName("summary_left_" + IntegerToString(row)),
         left_rows[row],
         stats_left_x,
         top_y + 18 + (row * 20),
         mono_font,
         10,
         (row == 3 && stats.net_profit < 0.0) ? loss_color : text_color
      );
      EnsureReportLabel(
         g_report_chart_id,
         ReportObjectName("summary_right_" + IntegerToString(row)),
         right_rows[row],
         stats_right_x,
         top_y + 18 + (row * 20),
         mono_font,
         10,
         text_color
      );
   }

   RenderBalanceGraph(graph_x, top_y, graph_width, graph_height, InpAccentColor, InpAccentColor, text_color, sub_color);

   EnsureReportLabel(g_report_chart_id, ReportObjectName("section"), "Recent Trades", outer_padding + 16, trades_y + 12, title_font, 11, text_color);
   EnsureReportLabel(
      g_report_chart_id,
      ReportObjectName("trade_header"),
      "#  Side   Lot  Open  Close      Entry       Exit       PnL  Reason",
      outer_padding + 16,
      trades_y + 38,
      mono_font,
      9,
      sub_color
   );

   int max_rows = MathMin(64, MathMax((trades_height - 64) / 18, 1));
   for(int row = 0; row < max_rows; row++)
   {
      string object_name = ReportObjectName("trade_row_" + IntegerToString(row));
      int trade_index = g_closed_trade_count - 1 - row;
      if(trade_index < 0)
      {
         ObjectDelete(g_report_chart_id, object_name);
         continue;
      }

      ATSimClosedTrade trade = g_closed_trades[trade_index];
      EnsureReportLabel(
         g_report_chart_id,
         object_name,
         FormatTradeRowText(trade_index + 1, trade),
         outer_padding + 16,
         trades_y + 62 + (row * 18),
         mono_font,
         9,
         trade.pnl >= 0.0 ? gain_color : loss_color
      );
   }

   string footer =
      "Open Position: "
      + (!g_position.open ? "Flat" : PositionSummaryText())
      + " | Pending: "
      + (!g_pending_order.active ? "None" : PositionSummaryText());
   EnsureReportLabel(g_report_chart_id, ReportObjectName("footer"), footer, outer_padding, panel_height - 22, mono_font, 9, sub_color);

   ChartRedraw(g_report_chart_id);
}

void RefreshSessionReportWindow(const bool allow_open)
{
   if(allow_open)
   {
      if(!EnsureSessionReportChart())
         return;
   }
   else if(!SessionReportChartIsOpen())
   {
      return;
   }

   RenderSessionReportWindow();
}

void OpenSessionReportWindow()
{
   RefreshSessionReportWindow(true);
   SetStatus("Session report updated.", InpAccentColor);
   ChartRedraw(g_chart_id);
}

void CloseReportChart()
{
   if(!SessionReportChartIsOpen())
      return;

   DeleteReportObjects(g_report_chart_id);
   ChartRedraw(g_report_chart_id);
   ChartClose(g_report_chart_id);
   g_report_chart_id = 0;
}

string PriceToText(const double value)
{
   int digits = (int)SymbolInfoInteger(g_custom_symbol, SYMBOL_DIGITS);
   if(digits <= 0)
      digits = _Digits;
   return(DoubleToString(value, digits));
}

string VolumeToText(const double value)
{
   return(DoubleToString(value, VolumeDigits()));
}

void SetStatus(const string text, const color text_color)
{
   ObjectSetString(g_chart_id, g_status_name, OBJPROP_TEXT, text);
   ObjectSetInteger(g_chart_id, g_status_name, OBJPROP_COLOR, text_color);
}

bool IsVolumeEditActive()
{
   if(ObjectFind(g_chart_id, g_volume_edit_name) < 0)
      return(false);
   return(ObjectGetInteger(g_chart_id, g_volume_edit_name, OBJPROP_SELECTED) != 0);
}

void SyncVolumeEditField()
{
   if(ObjectFind(g_chart_id, g_volume_edit_name) < 0)
      return;
   if(IsVolumeEditActive())
      return;
   ObjectSetString(g_chart_id, g_volume_edit_name, OBJPROP_TEXT, VolumeToText(g_trade_volume_lots));
}

void SetTradeObjectSelection(const string name, const bool selected)
{
   if(ObjectFind(g_chart_id, name) < 0)
      return;
   ObjectSetInteger(g_chart_id, name, OBJPROP_SELECTED, selected);
}

string TradeSizingSymbol()
{
   if(g_custom_symbol != "")
      return(g_custom_symbol);
   if(g_source_symbol != "")
      return(g_source_symbol);
   return(Symbol());
}

double VolumeStep()
{
   string symbol_name = TradeSizingSymbol();
   double step = SymbolInfoDouble(symbol_name, SYMBOL_VOLUME_STEP);
   if(step <= 0.0)
      step = 0.01;
   return(step);
}

int VolumeDigits()
{
   double volume_step = VolumeStep();
   if(volume_step <= 0.0)
      return(2);

   for(int digits = 0; digits < 8; digits++)
   {
      double scaled = volume_step * MathPow(10.0, digits);
      if(MathAbs(scaled - MathRound(scaled)) < 1e-8)
         return(digits);
   }
   return(2);
}

double NormalizeVolumeLots(const double value)
{
   string symbol_name = TradeSizingSymbol();
   double volume_min = SymbolInfoDouble(symbol_name, SYMBOL_VOLUME_MIN);
   double volume_max = SymbolInfoDouble(symbol_name, SYMBOL_VOLUME_MAX);
   double volume_step = SymbolInfoDouble(symbol_name, SYMBOL_VOLUME_STEP);

   if(volume_step <= 0.0)
      volume_step = 0.01;
   if(volume_min <= 0.0)
      volume_min = volume_step;
   if(volume_max <= 0.0)
      volume_max = MathMax(volume_min, value);

   double base_value = value;
   if(base_value <= 0.0)
      base_value = volume_min;

   double clamped = MathMax(volume_min, MathMin(volume_max, base_value));
   double steps = MathRound((clamped - volume_min) / volume_step);
   double normalized = volume_min + (steps * volume_step);
   normalized = MathMax(volume_min, MathMin(volume_max, normalized));
   return(NormalizeDouble(normalized, VolumeDigits()));
}

void AdjustTradeVolume(const int direction)
{
   double next_volume = g_trade_volume_lots + (direction * VolumeStep());
   g_trade_volume_lots = NormalizeVolumeLots(next_volume);

   if(g_pending_order.active)
      g_pending_order.volume_lots = g_trade_volume_lots;

   SyncVolumeEditField();
   SetStatus("Order size set to " + VolumeToText(g_trade_volume_lots) + " lots.", InpAccentColor);
}

bool EnsureRectangleLabel(
   const string name,
   const int x,
   const int y,
   const int width,
   const int height,
   const color background_color,
   const color border_color,
   const int border_width
)
{
   if(ObjectFind(g_chart_id, name) < 0)
   {
      ResetLastError();
      if(!ObjectCreate(g_chart_id, name, OBJ_RECTANGLE_LABEL, 0, 0, 0))
         return(false);
   }

   bool ok = true;
   ok = ObjectSetInteger(g_chart_id, name, OBJPROP_CORNER, g_panel_corner) && ok;
   ok = ObjectSetInteger(g_chart_id, name, OBJPROP_XDISTANCE, x) && ok;
   ok = ObjectSetInteger(g_chart_id, name, OBJPROP_YDISTANCE, y) && ok;
   ok = ObjectSetInteger(g_chart_id, name, OBJPROP_XSIZE, width) && ok;
   ok = ObjectSetInteger(g_chart_id, name, OBJPROP_YSIZE, height) && ok;
   ok = ObjectSetInteger(g_chart_id, name, OBJPROP_BGCOLOR, background_color) && ok;
   ok = ObjectSetInteger(g_chart_id, name, OBJPROP_BORDER_TYPE, BORDER_FLAT) && ok;
   ok = ObjectSetInteger(g_chart_id, name, OBJPROP_COLOR, border_color) && ok;
   ok = ObjectSetInteger(g_chart_id, name, OBJPROP_WIDTH, border_width) && ok;
   ok = ObjectSetInteger(g_chart_id, name, OBJPROP_BACK, false) && ok;
   ok = ObjectSetInteger(g_chart_id, name, OBJPROP_SELECTABLE, name == g_panel_name) && ok;
   ok = ObjectSetInteger(g_chart_id, name, OBJPROP_SELECTED, false) && ok;
   ok = ObjectSetInteger(g_chart_id, name, OBJPROP_HIDDEN, true) && ok;
   ok = ObjectSetInteger(g_chart_id, name, OBJPROP_ZORDER, 0) && ok;
   return(ok);
}

bool EnsureTextLabel(
   const string name,
   const int x,
   const int y,
   const string font_name,
   const int font_size,
   const color text_color
)
{
   if(ObjectFind(g_chart_id, name) < 0)
   {
      ResetLastError();
      if(!ObjectCreate(g_chart_id, name, OBJ_LABEL, 0, 0, 0))
         return(false);
   }

   bool ok = true;
   ok = ObjectSetInteger(g_chart_id, name, OBJPROP_CORNER, g_panel_corner) && ok;
   ok = ObjectSetInteger(g_chart_id, name, OBJPROP_XDISTANCE, x) && ok;
   ok = ObjectSetInteger(g_chart_id, name, OBJPROP_YDISTANCE, y) && ok;
   ok = ObjectSetInteger(g_chart_id, name, OBJPROP_COLOR, text_color) && ok;
   ok = ObjectSetInteger(g_chart_id, name, OBJPROP_FONTSIZE, font_size) && ok;
   ok = ObjectSetString(g_chart_id, name, OBJPROP_FONT, font_name) && ok;
   ok = ObjectSetInteger(g_chart_id, name, OBJPROP_BACK, false) && ok;
   ok = ObjectSetInteger(g_chart_id, name, OBJPROP_SELECTABLE, false) && ok;
   ok = ObjectSetInteger(g_chart_id, name, OBJPROP_SELECTED, false) && ok;
   ok = ObjectSetInteger(g_chart_id, name, OBJPROP_HIDDEN, true) && ok;
   ok = ObjectSetInteger(g_chart_id, name, OBJPROP_ZORDER, 1) && ok;
   return(ok);
}

bool EnsureButton(
   const string name,
   const string text,
   const int x,
   const int y,
   const int width,
   const int height
)
{
   if(ObjectFind(g_chart_id, name) < 0)
   {
      ResetLastError();
      if(!ObjectCreate(g_chart_id, name, OBJ_BUTTON, 0, 0, 0))
         return(false);
   }

   bool ok = true;
   ok = ObjectSetInteger(g_chart_id, name, OBJPROP_CORNER, g_panel_corner) && ok;
   ok = ObjectSetInteger(g_chart_id, name, OBJPROP_XDISTANCE, x) && ok;
   ok = ObjectSetInteger(g_chart_id, name, OBJPROP_YDISTANCE, y) && ok;
   ok = ObjectSetInteger(g_chart_id, name, OBJPROP_XSIZE, width) && ok;
   ok = ObjectSetInteger(g_chart_id, name, OBJPROP_YSIZE, height) && ok;
   ok = ObjectSetInteger(g_chart_id, name, OBJPROP_BGCOLOR, InpNeutralButtonColor) && ok;
   ok = ObjectSetInteger(g_chart_id, name, OBJPROP_COLOR, InpTextColor) && ok;
   ok = ObjectSetInteger(g_chart_id, name, OBJPROP_STATE, false) && ok;
   ok = ObjectSetInteger(g_chart_id, name, OBJPROP_SELECTABLE, false) && ok;
   ok = ObjectSetInteger(g_chart_id, name, OBJPROP_SELECTED, false) && ok;
   ok = ObjectSetInteger(g_chart_id, name, OBJPROP_HIDDEN, true) && ok;
   ok = ObjectSetInteger(g_chart_id, name, OBJPROP_ZORDER, 2) && ok;
   ok = ObjectSetInteger(g_chart_id, name, OBJPROP_FONTSIZE, InpButtonFontSize) && ok;
   ok = ObjectSetString(g_chart_id, name, OBJPROP_FONT, InpUiFont) && ok;
   ok = ObjectSetString(g_chart_id, name, OBJPROP_TEXT, text) && ok;
   return(ok);
}

bool EnsureEdit(
   const string name,
   const int x,
   const int y,
   const int width,
   const int height
)
{
   if(ObjectFind(g_chart_id, name) < 0)
   {
      ResetLastError();
      if(!ObjectCreate(g_chart_id, name, OBJ_EDIT, 0, 0, 0))
      {
         Print(__FUNCTION__, ": failed to create edit ", name, ". Error code = ", GetLastError());
         return(false);
      }
   }

   bool ok = true;
   ok = ObjectSetInteger(g_chart_id, name, OBJPROP_CORNER, g_panel_corner) && ok;
   ok = ObjectSetInteger(g_chart_id, name, OBJPROP_XDISTANCE, x) && ok;
   ok = ObjectSetInteger(g_chart_id, name, OBJPROP_YDISTANCE, y) && ok;
   ok = ObjectSetInteger(g_chart_id, name, OBJPROP_XSIZE, width) && ok;
   ok = ObjectSetInteger(g_chart_id, name, OBJPROP_YSIZE, height) && ok;
   ok = ObjectSetInteger(g_chart_id, name, OBJPROP_BGCOLOR, InpNeutralButtonColor) && ok;
   ok = ObjectSetInteger(g_chart_id, name, OBJPROP_COLOR, InpTextColor) && ok;
   ok = ObjectSetInteger(g_chart_id, name, OBJPROP_BORDER_COLOR, InpBorderColor) && ok;
   ok = ObjectSetInteger(g_chart_id, name, OBJPROP_FONTSIZE, InpMetaFontSize) && ok;
   ok = ObjectSetString(g_chart_id, name, OBJPROP_FONT, InpUiFont) && ok;
   ok = ObjectSetInteger(g_chart_id, name, OBJPROP_READONLY, false) && ok;
   ok = ObjectSetInteger(g_chart_id, name, OBJPROP_SELECTABLE, true) && ok;
   ok = ObjectSetInteger(g_chart_id, name, OBJPROP_SELECTED, false) && ok;
   ok = ObjectSetInteger(g_chart_id, name, OBJPROP_HIDDEN, true) && ok;
   ok = ObjectSetInteger(g_chart_id, name, OBJPROP_ZORDER, 3) && ok;
   return(ok);
}

void ColorizeButton(const string name, const color background_color, const color text_color)
{
   if(ObjectFind(g_chart_id, name) < 0)
      return;
   ObjectSetInteger(g_chart_id, name, OBJPROP_BGCOLOR, background_color);
   ObjectSetInteger(g_chart_id, name, OBJPROP_COLOR, text_color);
   ObjectSetInteger(g_chart_id, name, OBJPROP_STATE, false);
}

bool IsRightCorner()
{
   return(g_panel_corner == CORNER_RIGHT_UPPER || g_panel_corner == CORNER_RIGHT_LOWER);
}

bool IsLowerCorner()
{
   return(g_panel_corner == CORNER_LEFT_LOWER || g_panel_corner == CORNER_RIGHT_LOWER);
}

int ResolvePanelXDistance(const int object_width)
{
   if(IsRightCorner())
      return(g_panel_x_offset + object_width);
   return(g_panel_x_offset);
}

int ResolvePanelYDistance(const int object_height)
{
   if(IsLowerCorner())
      return(g_panel_y_offset + object_height);
   return(g_panel_y_offset);
}

int ResolveInnerX(const int offset)
{
   if(IsRightCorner())
      return(g_panel_x_offset + InpPanelWidth - offset);
   return(g_panel_x_offset + offset);
}

int ResolveInnerY(const int offset)
{
   if(IsLowerCorner())
      return(g_panel_y_offset + InpPanelHeight - offset);
   return(g_panel_y_offset + offset);
}

void SynchronizeViewToLatest()
{
   ChartNavigate(g_chart_id, CHART_END, 0);
}

double NormalizePrice(const double value)
{
   int digits = (int)SymbolInfoInteger(g_custom_symbol, SYMBOL_DIGITS);
   if(digits <= 0)
      digits = _Digits;
   return(NormalizeDouble(value, digits));
}

double NormalizePriceForSymbol(const double value, const string symbol_name)
{
   int digits = (int)SymbolInfoInteger(symbol_name, SYMBOL_DIGITS);
   if(digits <= 0)
      digits = _Digits;
   return(NormalizeDouble(value, digits));
}

string TrimString(string value)
{
   string result = value;
   StringTrimLeft(result);
   StringTrimRight(result);
   return(result);
}
