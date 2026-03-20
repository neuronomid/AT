#property strict
#property version   "1.00"
#property description "AT candle countdown indicator for MetaTrader 5."
#property description "Displays the remaining lifetime of the active chart candle."
#property indicator_chart_window
#property indicator_plots 0

input ENUM_BASE_CORNER InpPanelCorner = CORNER_RIGHT_UPPER;
input int InpXOffset = 18;
input int InpYOffset = 24;
input int InpPanelWidth = 185;
input int InpPanelHeight = 71;
input string InpUiFont = "Segoe UI";
input string InpCountdownFont = "Consolas";
input int InpTitleFontSize = 5;
input int InpCountdownFontSize = 16;
input int InpDetailFontSize = 5;
input color InpPanelColor = C'17,24,39';
input color InpBorderColor = C'49,66,91';
input color InpAccentColor = C'36,184,240';
input color InpTitleColor = C'155,167,187';
input color InpCountdownColor = clrWhite;
input color InpDetailColor = C'155,167,187';
input color InpWarningColor = C'255,184,77';

long g_chart_id = 0;
string g_prefix = "";
string g_panel_name = "";
string g_accent_name = "";
string g_title_name = "";
string g_value_name = "";
string g_window_name = "";
string g_status_name = "";
datetime g_current_bar_open = 0;
int g_period_seconds = 0;

int ScaleUiMetric(const int value)
{
   return((int)MathRound(value * 0.75));
}

int OnInit()
{
   g_chart_id = ChartID();
   g_prefix = "AT_CandleCountdown_" + IntegerToString((int)g_chart_id) + "_" + IntegerToString((int)GetTickCount());
   g_panel_name = BuildObjectName("panel");
   g_accent_name = BuildObjectName("accent");
   g_title_name = BuildObjectName("title");
   g_value_name = BuildObjectName("value");
   g_window_name = BuildObjectName("window");
   g_status_name = BuildObjectName("status");

   IndicatorSetString(INDICATOR_SHORTNAME, "AT Candle Countdown");

   if(!BuildInterface())
      return(INIT_FAILED);

   RefreshBarStateFromSeries();
   UpdateCountdownDisplay();

   ResetLastError();
   if(!EventSetTimer(1))
   {
      Print(__FUNCTION__, ": EventSetTimer failed. Error code = ", GetLastError());
      DeleteInterface();
      return(INIT_FAILED);
   }

   ChartRedraw(g_chart_id);
   return(INIT_SUCCEEDED);
}

void OnDeinit(const int reason)
{
   EventKillTimer();
   DeleteInterface();
   ChartRedraw(g_chart_id);
}

int OnCalculate(
   const int rates_total,
   const int prev_calculated,
   const datetime &time[],
   const double &open[],
   const double &high[],
   const double &low[],
   const double &close[],
   const long &tick_volume[],
   const long &volume[],
   const int &spread[]
)
{
   return(rates_total);
}

void OnTimer()
{
   RefreshBarStateFromSeries();
   UpdateCountdownDisplay();
}

void OnChartEvent(const int id, const long &lparam, const double &dparam, const string &sparam)
{
   if(id != CHARTEVENT_CHART_CHANGE)
      return;

   RefreshBarStateFromSeries();
   BuildInterface();
   UpdateCountdownDisplay();
}

string BuildObjectName(const string suffix)
{
   return(g_prefix + "_" + suffix);
}

bool BuildInterface()
{
   int panel_x = ResolvePanelXDistance(InpPanelWidth);
   int panel_y = ResolvePanelYDistance(InpPanelHeight);
   bool ok = true;
   ok = EnsureRectangleLabel(g_panel_name, panel_x, panel_y, InpPanelWidth, InpPanelHeight, InpPanelColor, InpBorderColor, 1) && ok;
   ok = EnsureRectangleLabel(g_accent_name, panel_x, panel_y, ScaleUiMetric(5), InpPanelHeight, InpAccentColor, InpAccentColor, 0) && ok;
   ok = EnsureTextLabel(g_title_name, ResolveTextXDistance(ScaleUiMetric(14)), ResolveTextYDistance(ScaleUiMetric(10)), InpUiFont, InpTitleFontSize, InpTitleColor) && ok;
   ok = EnsureTextLabel(g_value_name, ResolveTextXDistance(ScaleUiMetric(14)), ResolveTextYDistance(ScaleUiMetric(25)), InpCountdownFont, InpCountdownFontSize, InpCountdownColor) && ok;
   ok = EnsureTextLabel(g_window_name, ResolveTextXDistance(ScaleUiMetric(14)), ResolveTextYDistance(ScaleUiMetric(58)), InpUiFont, InpDetailFontSize, InpDetailColor) && ok;
   ok = EnsureTextLabel(g_status_name, ResolveTextXDistance(ScaleUiMetric(14)), ResolveTextYDistance(ScaleUiMetric(74)), InpUiFont, InpDetailFontSize, InpAccentColor) && ok;
   return(ok);
}

void DeleteInterface()
{
   ObjectDelete(g_chart_id, g_panel_name);
   ObjectDelete(g_chart_id, g_accent_name);
   ObjectDelete(g_chart_id, g_title_name);
   ObjectDelete(g_chart_id, g_value_name);
   ObjectDelete(g_chart_id, g_window_name);
   ObjectDelete(g_chart_id, g_status_name);
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
      {
         Print(__FUNCTION__, ": failed to create rectangle label ", name, ". Error code = ", GetLastError());
         return(false);
      }
   }

   bool ok = true;
   ok = ObjectSetInteger(g_chart_id, name, OBJPROP_CORNER, InpPanelCorner) && ok;
   ok = ObjectSetInteger(g_chart_id, name, OBJPROP_XDISTANCE, x) && ok;
   ok = ObjectSetInteger(g_chart_id, name, OBJPROP_YDISTANCE, y) && ok;
   ok = ObjectSetInteger(g_chart_id, name, OBJPROP_XSIZE, width) && ok;
   ok = ObjectSetInteger(g_chart_id, name, OBJPROP_YSIZE, height) && ok;
   ok = ObjectSetInteger(g_chart_id, name, OBJPROP_BGCOLOR, background_color) && ok;
   ok = ObjectSetInteger(g_chart_id, name, OBJPROP_BORDER_TYPE, BORDER_FLAT) && ok;
   ok = ObjectSetInteger(g_chart_id, name, OBJPROP_COLOR, border_color) && ok;
   ok = ObjectSetInteger(g_chart_id, name, OBJPROP_WIDTH, border_width) && ok;
   ok = ObjectSetInteger(g_chart_id, name, OBJPROP_BACK, false) && ok;
   ok = ObjectSetInteger(g_chart_id, name, OBJPROP_SELECTABLE, false) && ok;
   ok = ObjectSetInteger(g_chart_id, name, OBJPROP_SELECTED, false) && ok;
   ok = ObjectSetInteger(g_chart_id, name, OBJPROP_HIDDEN, true) && ok;
   ok = ObjectSetInteger(g_chart_id, name, OBJPROP_ZORDER, 0) && ok;

   if(!ok)
      Print(__FUNCTION__, ": failed to configure rectangle label ", name, ". Error code = ", GetLastError());

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
      {
         Print(__FUNCTION__, ": failed to create text label ", name, ". Error code = ", GetLastError());
         return(false);
      }
   }

   bool ok = true;
   ok = ObjectSetInteger(g_chart_id, name, OBJPROP_CORNER, InpPanelCorner) && ok;
   ok = ObjectSetInteger(g_chart_id, name, OBJPROP_ANCHOR, ResolveTextAnchor()) && ok;
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

   if(!ok)
      Print(__FUNCTION__, ": failed to configure text label ", name, ". Error code = ", GetLastError());

   return(ok);
}

bool IsRightCorner()
{
   return(InpPanelCorner == CORNER_RIGHT_UPPER || InpPanelCorner == CORNER_RIGHT_LOWER);
}

bool IsLowerCorner()
{
   return(InpPanelCorner == CORNER_LEFT_LOWER || InpPanelCorner == CORNER_RIGHT_LOWER);
}

int ResolvePanelXDistance(const int object_width)
{
   if(IsRightCorner())
      return(InpXOffset + object_width);

   return(InpXOffset);
}

int ResolvePanelYDistance(const int object_height)
{
   if(IsLowerCorner())
      return(InpYOffset + object_height);

   return(InpYOffset);
}

int ResolveTextXDistance(const int left_padding)
{
   if(IsRightCorner())
      return(InpXOffset + InpPanelWidth - left_padding);

   return(InpXOffset + left_padding);
}

int ResolveTextYDistance(const int top_padding)
{
   if(IsLowerCorner())
      return(InpYOffset + InpPanelHeight - top_padding);

   return(InpYOffset + top_padding);
}

ENUM_ANCHOR_POINT ResolveTextAnchor()
{
   return(ANCHOR_LEFT_UPPER);
}

bool RefreshBarStateFromSeries()
{
   g_period_seconds = PeriodSeconds(PERIOD_CURRENT);

   datetime current_bar[];
   ArrayResize(current_bar, 1);

   ResetLastError();
   int copied = CopyTime(_Symbol, PERIOD_CURRENT, 0, 1, current_bar);
   if(copied <= 0 || ArraySize(current_bar) == 0 || current_bar[0] <= 0)
      return(false);

   g_current_bar_open = current_bar[0];
   return(true);
}

void UpdateCountdownDisplay()
{
   string title = _Symbol + " " + TimeframeToString((ENUM_TIMEFRAMES)_Period) + " candle";
   string countdown_text = "--:--";
   string window_text = "Waiting for current bar data";
   string status_text = "Clock source unavailable";
   color value_color = InpCountdownColor;
   color status_color = InpDetailColor;

   bool used_fallback_clock = false;
   datetime now = GetClockNow(used_fallback_clock);

   if(g_current_bar_open > 0 && g_period_seconds > 0 && now > 0)
   {
      datetime next_bar_open = CalculateNextBarOpen(g_current_bar_open, (ENUM_TIMEFRAMES)_Period);
      int remaining_seconds = (int)(next_bar_open - now);
      bool waiting_for_tick = (remaining_seconds < 0);
      int display_seconds = remaining_seconds;
      if(display_seconds < 0)
         display_seconds = 0;

      countdown_text = FormatRemaining(display_seconds);
      window_text = "Open " + FormatBarTimestamp(g_current_bar_open) + "  Close " + FormatBarTimestamp(next_bar_open);

      if(waiting_for_tick)
      {
         value_color = InpWarningColor;
         status_color = InpWarningColor;
         status_text = "Waiting for the next tick to print the new candle";
      }
      else if(used_fallback_clock)
      {
         status_color = InpDetailColor;
         status_text = "Clock source: TimeCurrent fallback";
      }
      else
      {
         status_color = InpAccentColor;
         status_text = "Clock source: trade server time";
      }
   }

   SetText(g_title_name, title, InpTitleColor);
   SetText(g_value_name, countdown_text, value_color);
   SetText(g_window_name, window_text, InpDetailColor);
   SetText(g_status_name, status_text, status_color);
   ChartRedraw(g_chart_id);
}

void SetText(const string name, const string text, const color text_color)
{
   ObjectSetString(g_chart_id, name, OBJPROP_TEXT, text);
   ObjectSetInteger(g_chart_id, name, OBJPROP_COLOR, text_color);
}

datetime GetClockNow(bool &used_fallback_clock)
{
   used_fallback_clock = false;
   datetime server_time = TimeTradeServer();
   if(server_time > 0)
      return(server_time);

   used_fallback_clock = true;
   return(TimeCurrent());
}

datetime CalculateNextBarOpen(const datetime current_bar_open, const ENUM_TIMEFRAMES timeframe)
{
   if(timeframe == PERIOD_MN1)
   {
      MqlDateTime stamp = {};
      TimeToStruct(current_bar_open, stamp);
      stamp.mon++;
      if(stamp.mon > 12)
      {
         stamp.mon = 1;
         stamp.year++;
      }
      stamp.day = 1;
      return(StructToTime(stamp));
   }

   int seconds = PeriodSeconds(timeframe);
   if(seconds <= 0)
      seconds = g_period_seconds;

   if(seconds <= 0)
      return(current_bar_open);

   return(current_bar_open + seconds);
}

string FormatRemaining(int total_seconds)
{
   if(total_seconds < 0)
      total_seconds = 0;

   int days = total_seconds / 86400;
   int hours = (total_seconds % 86400) / 3600;
   int minutes = (total_seconds % 3600) / 60;
   int seconds = total_seconds % 60;

   if(days > 0)
      return(StringFormat("%dd %02d:%02d:%02d", days, hours, minutes, seconds));
   if(hours > 0)
      return(StringFormat("%02d:%02d:%02d", hours, minutes, seconds));
   return(StringFormat("%02d:%02d", minutes, seconds));
}

string FormatBarTimestamp(const datetime value)
{
   if(g_period_seconds >= 86400)
      return(TimeToString(value, TIME_DATE | TIME_MINUTES));

   return(TimeToString(value, TIME_MINUTES | TIME_SECONDS));
}

string TimeframeToString(const ENUM_TIMEFRAMES timeframe)
{
   switch(timeframe)
   {
      case PERIOD_M1: return("M1");
      case PERIOD_M2: return("M2");
      case PERIOD_M3: return("M3");
      case PERIOD_M4: return("M4");
      case PERIOD_M5: return("M5");
      case PERIOD_M6: return("M6");
      case PERIOD_M10: return("M10");
      case PERIOD_M12: return("M12");
      case PERIOD_M15: return("M15");
      case PERIOD_M20: return("M20");
      case PERIOD_M30: return("M30");
      case PERIOD_H1: return("H1");
      case PERIOD_H2: return("H2");
      case PERIOD_H3: return("H3");
      case PERIOD_H4: return("H4");
      case PERIOD_H6: return("H6");
      case PERIOD_H8: return("H8");
      case PERIOD_H12: return("H12");
      case PERIOD_D1: return("D1");
      case PERIOD_W1: return("W1");
      case PERIOD_MN1: return("MN1");
      default: return("CURRENT");
   }
}
