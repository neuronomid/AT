#property strict
#property version   "1.00"
#property description "AT manual order pad for personal chart trading."
#property description "Standalone chart-side EA for market orders and draggable pending orders."

input bool InpEnableTrading = false;
input double InpVolumeLots = 0.10;
input int InpStopLossPoints = 0;
input int InpTakeProfitPoints = 0;
input int InpPendingOffsetPoints = 100;
input bool InpAutoSubmitPendingOnDrop = true;
input long InpMagicNumber = 6101001;
input string InpOrderComment = "ATManualPad";

input ENUM_BASE_CORNER InpPanelCorner = CORNER_RIGHT_UPPER;
input int InpXOffset = 14;
input int InpYOffset = 18;
input int InpPanelWidth = 214;
input int InpPanelHeight = 178;
input string InpUiFont = "Segoe UI";
input int InpTitleFontSize = 8;
input int InpMetaFontSize = 7;
input int InpButtonFontSize = 8;

input color InpPanelColor = C'17,24,39';
input color InpBorderColor = C'49,66,91';
input color InpAccentColor = C'36,184,240';
input color InpTextColor = clrWhite;
input color InpMetaColor = C'155,167,187';
input color InpWarnColor = C'255,184,77';
input color InpBuyColor = C'28,134,88';
input color InpSellColor = C'176,58,72';
input color InpPendingBuyColor = C'35,102,168';
input color InpPendingSellColor = C'170,105,33';
input color InpActiveButtonColor = C'36,184,240';
input color InpActiveButtonTextColor = C'17,24,39';

enum ATOrderPadMode
{
   PAD_MODE_NONE = 0,
   PAD_MODE_BUY_MARKET = 1,
   PAD_MODE_SELL_MARKET = 2,
   PAD_MODE_BUY_LIMIT = 3,
   PAD_MODE_SELL_LIMIT = 4,
   PAD_MODE_BUY_STOP = 5,
   PAD_MODE_SELL_STOP = 6
};

long g_chart_id = 0;
string g_prefix = "";
string g_panel_name = "";
string g_accent_name = "";
string g_title_name = "";
string g_meta_name = "";
string g_status_name = "";
string g_lots_label_name = "";
string g_lots_edit_name = "";
string g_sl_label_name = "";
string g_sl_edit_name = "";
string g_tp_label_name = "";
string g_tp_edit_name = "";
string g_buy_market_name = "";
string g_sell_market_name = "";
string g_place_name = "";
string g_cancel_name = "";
string g_buy_limit_name = "";
string g_sell_limit_name = "";
string g_buy_stop_name = "";
string g_sell_stop_name = "";
string g_staging_line_name = "";
ATOrderPadMode g_staged_mode = PAD_MODE_NONE;
double g_volume_lots = 0.0;
int g_stop_loss_points = 0;
int g_take_profit_points = 0;

int OnInit()
{
   g_chart_id = ChartID();
   g_prefix = "AT_ManualPad_" + IntegerToString((int)g_chart_id) + "_" + IntegerToString((int)GetTickCount());
   g_panel_name = BuildObjectName("panel");
   g_accent_name = BuildObjectName("accent");
   g_title_name = BuildObjectName("title");
   g_meta_name = BuildObjectName("meta");
   g_status_name = BuildObjectName("status");
   g_lots_label_name = BuildObjectName("lots_label");
   g_lots_edit_name = BuildObjectName("lots_edit");
   g_sl_label_name = BuildObjectName("sl_label");
   g_sl_edit_name = BuildObjectName("sl_edit");
   g_tp_label_name = BuildObjectName("tp_label");
   g_tp_edit_name = BuildObjectName("tp_edit");
   g_buy_market_name = BuildObjectName("buy_market");
   g_sell_market_name = BuildObjectName("sell_market");
   g_place_name = BuildObjectName("place");
   g_cancel_name = BuildObjectName("cancel");
   g_buy_limit_name = BuildObjectName("buy_limit");
   g_sell_limit_name = BuildObjectName("sell_limit");
   g_buy_stop_name = BuildObjectName("buy_stop");
   g_sell_stop_name = BuildObjectName("sell_stop");
   g_staging_line_name = BuildObjectName("staging_line");
   g_volume_lots = NormalizeVolume(InpVolumeLots);
   g_stop_loss_points = MathMax(InpStopLossPoints, 0);
   g_take_profit_points = MathMax(InpTakeProfitPoints, 0);

   ChartSetInteger(g_chart_id, CHART_EVENT_OBJECT_DELETE, true);

   if(!BuildInterface())
      return(INIT_FAILED);

   UpdateMetaText();
   UpdateButtonStates();
   SetStatus(DefaultStatusText(), InpMetaColor);
   ChartRedraw(g_chart_id);
   return(INIT_SUCCEEDED);
}

void OnDeinit(const int reason)
{
   DeleteInterface();
   ChartRedraw(g_chart_id);
}

void OnTick()
{
}

void OnChartEvent(const int id, const long &lparam, const double &dparam, const string &sparam)
{
   if(id == CHARTEVENT_OBJECT_CLICK)
   {
      HandleObjectClick(sparam);
      return;
   }

   if(id == CHARTEVENT_OBJECT_DRAG && sparam == g_staging_line_name)
   {
      HandleStagingLineDrag();
      return;
   }

   if(id == CHARTEVENT_OBJECT_ENDEDIT)
   {
      HandleEditCommit(sparam);
      return;
   }

   if(id == CHARTEVENT_OBJECT_DELETE && sparam == g_staging_line_name)
   {
      g_staged_mode = PAD_MODE_NONE;
      UpdateButtonStates();
      SetStatus(DefaultStatusText(), InpMetaColor);
      return;
   }

   if(id == CHARTEVENT_CHART_CHANGE)
   {
      BuildInterface();
      UpdateMetaText();
      UpdateButtonStates();
      ChartRedraw(g_chart_id);
   }
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
   ok = EnsureRectangleLabel(g_accent_name, panel_x, panel_y, 4, InpPanelHeight, InpAccentColor, InpAccentColor, 0) && ok;
   ok = EnsureTextLabel(g_title_name, ResolveInnerX(12), ResolveInnerY(9), InpUiFont, InpTitleFontSize, InpTextColor) && ok;
   ok = EnsureTextLabel(g_meta_name, ResolveInnerX(12), ResolveInnerY(25), InpUiFont, InpMetaFontSize, InpMetaColor) && ok;
   ok = EnsureTextLabel(g_lots_label_name, ResolveInnerX(12), ResolveInnerY(44), InpUiFont, InpMetaFontSize, InpMetaColor) && ok;
   ok = EnsureEdit(g_lots_edit_name, ResolveInnerX(28), ResolveInnerY(39), 30, 18) && ok;
   ok = EnsureTextLabel(g_sl_label_name, ResolveInnerX(66), ResolveInnerY(44), InpUiFont, InpMetaFontSize, InpMetaColor) && ok;
   ok = EnsureEdit(g_sl_edit_name, ResolveInnerX(84), ResolveInnerY(39), 34, 18) && ok;
   ok = EnsureTextLabel(g_tp_label_name, ResolveInnerX(126), ResolveInnerY(44), InpUiFont, InpMetaFontSize, InpMetaColor) && ok;
   ok = EnsureEdit(g_tp_edit_name, ResolveInnerX(146), ResolveInnerY(39), 40, 18) && ok;
   ok = EnsureTextLabel(g_status_name, ResolveInnerX(12), ResolveInnerY(160), InpUiFont, InpMetaFontSize, InpMetaColor) && ok;

   ok = EnsureButton(g_buy_market_name, "BUY MKT", ResolveInnerX(12), ResolveInnerY(64), 62, 18) && ok;
   ok = EnsureButton(g_sell_market_name, "SELL MKT", ResolveInnerX(82), ResolveInnerY(64), 62, 18) && ok;
   ok = EnsureButton(g_place_name, "PLACE", ResolveInnerX(12), ResolveInnerY(88), 88, 18) && ok;
   ok = EnsureButton(g_cancel_name, "CANCEL", ResolveInnerX(110), ResolveInnerY(88), 88, 18) && ok;
   ok = EnsureButton(g_buy_limit_name, "BUY LMT", ResolveInnerX(12), ResolveInnerY(112), 88, 18) && ok;
   ok = EnsureButton(g_sell_limit_name, "SELL LMT", ResolveInnerX(110), ResolveInnerY(112), 88, 18) && ok;
   ok = EnsureButton(g_buy_stop_name, "BUY STP", ResolveInnerX(12), ResolveInnerY(136), 88, 18) && ok;
   ok = EnsureButton(g_sell_stop_name, "SELL STP", ResolveInnerX(110), ResolveInnerY(136), 88, 18) && ok;

   ObjectSetString(g_chart_id, g_title_name, OBJPROP_TEXT, "AT Manual Pad");
   ObjectSetString(g_chart_id, g_lots_label_name, OBJPROP_TEXT, "L");
   ObjectSetString(g_chart_id, g_sl_label_name, OBJPROP_TEXT, "SL");
   ObjectSetString(g_chart_id, g_tp_label_name, OBJPROP_TEXT, "TP");
   SyncEditFields();
   return(ok);
}

void DeleteInterface()
{
   ObjectDelete(g_chart_id, g_panel_name);
   ObjectDelete(g_chart_id, g_accent_name);
   ObjectDelete(g_chart_id, g_title_name);
   ObjectDelete(g_chart_id, g_meta_name);
   ObjectDelete(g_chart_id, g_status_name);
   ObjectDelete(g_chart_id, g_lots_label_name);
   ObjectDelete(g_chart_id, g_lots_edit_name);
   ObjectDelete(g_chart_id, g_sl_label_name);
   ObjectDelete(g_chart_id, g_sl_edit_name);
   ObjectDelete(g_chart_id, g_tp_label_name);
   ObjectDelete(g_chart_id, g_tp_edit_name);
   ObjectDelete(g_chart_id, g_buy_market_name);
   ObjectDelete(g_chart_id, g_sell_market_name);
   ObjectDelete(g_chart_id, g_place_name);
   ObjectDelete(g_chart_id, g_cancel_name);
   ObjectDelete(g_chart_id, g_buy_limit_name);
   ObjectDelete(g_chart_id, g_sell_limit_name);
   ObjectDelete(g_chart_id, g_buy_stop_name);
   ObjectDelete(g_chart_id, g_sell_stop_name);
   ObjectDelete(g_chart_id, g_staging_line_name);
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
         Print(__FUNCTION__, ": failed to create ", name, " error=", GetLastError());
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
         Print(__FUNCTION__, ": failed to create ", name, " error=", GetLastError());
         return(false);
      }
   }

   bool ok = true;
   ok = ObjectSetInteger(g_chart_id, name, OBJPROP_CORNER, InpPanelCorner) && ok;
   ok = ObjectSetInteger(g_chart_id, name, OBJPROP_ANCHOR, ANCHOR_LEFT_UPPER) && ok;
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
      {
         Print(__FUNCTION__, ": failed to create ", name, " error=", GetLastError());
         return(false);
      }
   }

   bool ok = true;
   ok = ObjectSetInteger(g_chart_id, name, OBJPROP_CORNER, InpPanelCorner) && ok;
   ok = ObjectSetInteger(g_chart_id, name, OBJPROP_XDISTANCE, x) && ok;
   ok = ObjectSetInteger(g_chart_id, name, OBJPROP_YDISTANCE, y) && ok;
   ok = ObjectSetInteger(g_chart_id, name, OBJPROP_XSIZE, width) && ok;
   ok = ObjectSetInteger(g_chart_id, name, OBJPROP_YSIZE, height) && ok;
   ok = ObjectSetInteger(g_chart_id, name, OBJPROP_BGCOLOR, InpPanelColor) && ok;
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
         Print(__FUNCTION__, ": failed to create ", name, " error=", GetLastError());
         return(false);
      }
   }

   bool ok = true;
   ok = ObjectSetInteger(g_chart_id, name, OBJPROP_CORNER, InpPanelCorner) && ok;
   ok = ObjectSetInteger(g_chart_id, name, OBJPROP_XDISTANCE, x) && ok;
   ok = ObjectSetInteger(g_chart_id, name, OBJPROP_YDISTANCE, y) && ok;
   ok = ObjectSetInteger(g_chart_id, name, OBJPROP_XSIZE, width) && ok;
   ok = ObjectSetInteger(g_chart_id, name, OBJPROP_YSIZE, height) && ok;
   ok = ObjectSetInteger(g_chart_id, name, OBJPROP_BGCOLOR, InpBorderColor) && ok;
   ok = ObjectSetInteger(g_chart_id, name, OBJPROP_COLOR, InpTextColor) && ok;
   ok = ObjectSetInteger(g_chart_id, name, OBJPROP_FONTSIZE, InpMetaFontSize) && ok;
   ok = ObjectSetString(g_chart_id, name, OBJPROP_FONT, InpUiFont) && ok;
   ok = ObjectSetInteger(g_chart_id, name, OBJPROP_READONLY, false) && ok;
   ok = ObjectSetInteger(g_chart_id, name, OBJPROP_SELECTABLE, false) && ok;
   ok = ObjectSetInteger(g_chart_id, name, OBJPROP_SELECTED, false) && ok;
   ok = ObjectSetInteger(g_chart_id, name, OBJPROP_HIDDEN, true) && ok;
   ok = ObjectSetInteger(g_chart_id, name, OBJPROP_ZORDER, 3) && ok;
   return(ok);
}

void HandleObjectClick(const string name)
{
   ResetButtonState(name);

   if(name == g_buy_market_name)
   {
      HandleMarketOrder(PAD_MODE_BUY_MARKET);
      return;
   }
   if(name == g_sell_market_name)
   {
      HandleMarketOrder(PAD_MODE_SELL_MARKET);
      return;
   }
   if(name == g_place_name)
   {
      SubmitStagedPending();
      return;
   }
   if(name == g_cancel_name)
   {
      CancelStaging("Pending order staging cancelled.");
      return;
   }
   if(name == g_buy_limit_name)
   {
      HandlePendingButton(PAD_MODE_BUY_LIMIT);
      return;
   }
   if(name == g_sell_limit_name)
   {
      HandlePendingButton(PAD_MODE_SELL_LIMIT);
      return;
   }
   if(name == g_buy_stop_name)
   {
      HandlePendingButton(PAD_MODE_BUY_STOP);
      return;
   }
   if(name == g_sell_stop_name)
   {
      HandlePendingButton(PAD_MODE_SELL_STOP);
      return;
   }
}

void HandleEditCommit(const string name)
{
   if(name == g_lots_edit_name)
   {
      string raw = TrimString(ObjectGetString(g_chart_id, g_lots_edit_name, OBJPROP_TEXT));
      double parsed = StringToDouble(raw);
      if(raw == "" || parsed <= 0.0)
      {
         SyncEditFields();
         SetStatus("Lots must be greater than zero.", InpWarnColor);
         return;
      }
      g_volume_lots = NormalizeVolume(parsed);
      SyncEditFields();
      UpdateMetaText();
      SetStatus("Lots updated to " + DoubleToString(g_volume_lots, VolumeDigits()) + ".", InpMetaColor);
      return;
   }

   if(name == g_sl_edit_name)
   {
      string raw = TrimString(ObjectGetString(g_chart_id, g_sl_edit_name, OBJPROP_TEXT));
      if(raw == "")
         raw = "0";
      int parsed = (int)StringToInteger(raw);
      if(parsed < 0)
      {
         SyncEditFields();
         SetStatus("SL points cannot be negative.", InpWarnColor);
         return;
      }
      g_stop_loss_points = parsed;
      SyncEditFields();
      UpdateMetaText();
      SetStatus("SL updated to " + IntegerToString(g_stop_loss_points) + " points.", InpMetaColor);
      return;
   }

   if(name == g_tp_edit_name)
   {
      string raw = TrimString(ObjectGetString(g_chart_id, g_tp_edit_name, OBJPROP_TEXT));
      if(raw == "")
         raw = "0";
      int parsed = (int)StringToInteger(raw);
      if(parsed < 0)
      {
         SyncEditFields();
         SetStatus("TP points cannot be negative.", InpWarnColor);
         return;
      }
      g_take_profit_points = parsed;
      SyncEditFields();
      UpdateMetaText();
      SetStatus("TP updated to " + IntegerToString(g_take_profit_points) + " points.", InpMetaColor);
      return;
   }
}

void HandleMarketOrder(const ATOrderPadMode mode)
{
   CancelStaging("");

   if(!InpEnableTrading)
   {
      SetStatus(ModeLabel(mode) + " preview only. Set EnableTrading=true to send.", InpWarnColor);
      return;
   }

   string can_trade_message = "";
   if(!CanTradeNow(can_trade_message))
   {
      SetStatus(can_trade_message, InpWarnColor);
      return;
   }

   ulong ticket = 0;
   string message = "";
   if(ExecuteMarketOrder(mode, ticket, message))
      SetStatus(ModeLabel(mode) + " sent. ticket=" + UnsignedLongToString(ticket), ModeIsBuy(mode) ? InpBuyColor : InpSellColor);
   else
      SetStatus(ModeLabel(mode) + " failed: " + message, InpWarnColor);
}

void HandlePendingButton(const ATOrderPadMode mode)
{
   if(g_staged_mode == mode && StagingLineExists())
   {
      SubmitStagedPending();
      return;
   }

   g_staged_mode = mode;
   double suggested_price = SuggestedPendingPrice(mode);
   if(suggested_price <= 0.0)
   {
      g_staged_mode = PAD_MODE_NONE;
      UpdateButtonStates();
      SetStatus("Current tick unavailable. Wait for live prices and try again.", InpWarnColor);
      return;
   }
   CreateOrUpdateStagingLine(suggested_price, mode);
   UpdateButtonStates();
   SetStatus(StagingInstruction(mode), InpAccentColor);
}

void HandleStagingLineDrag()
{
   if(!IsPendingMode(g_staged_mode) || !StagingLineExists())
      return;

   double price = StagingLinePrice();
   string validation_message = "";
   if(!ValidatePendingPrice(g_staged_mode, price, validation_message))
   {
      SetStatus(validation_message, InpWarnColor);
      return;
   }

   if(!InpAutoSubmitPendingOnDrop)
   {
      SetStatus(ModeLabel(g_staged_mode) + " staged @ " + PriceToText(price) + ". Press PLACE to send.", InpAccentColor);
      return;
   }

   SubmitStagedPending();
}

void SubmitStagedPending()
{
   if(!IsPendingMode(g_staged_mode) || !StagingLineExists())
   {
      SetStatus("No staged pending order. Choose a limit/stop button first.", InpWarnColor);
      return;
   }

   double price = StagingLinePrice();
   string validation_message = "";
   if(!ValidatePendingPrice(g_staged_mode, price, validation_message))
   {
      SetStatus(validation_message, InpWarnColor);
      return;
   }

   if(!InpEnableTrading)
   {
      SetStatus(ModeLabel(g_staged_mode) + " preview @ " + PriceToText(price) + ". Set EnableTrading=true to send.", InpWarnColor);
      return;
   }

   string can_trade_message = "";
   if(!CanTradeNow(can_trade_message))
   {
      SetStatus(can_trade_message, InpWarnColor);
      return;
   }

   ulong ticket = 0;
   string message = "";
   ATOrderPadMode mode = g_staged_mode;
   if(ExecutePendingOrder(mode, price, ticket, message))
   {
      CancelStaging("");
      SetStatus(ModeLabel(mode) + " placed @ " + PriceToText(price) + " ticket=" + UnsignedLongToString(ticket), ModeIsBuy(mode) ? InpBuyColor : InpSellColor);
   }
   else
   {
      SetStatus(ModeLabel(mode) + " failed: " + message, InpWarnColor);
   }
}

bool ExecuteMarketOrder(const ATOrderPadMode mode, ulong &ticket, string &message)
{
   MqlTick tick;
   if(!SymbolInfoTick(_Symbol, tick))
   {
      message = "Current tick unavailable";
      return(false);
   }

   MqlTradeRequest request;
   MqlTradeResult result;
   ZeroMemory(request);
   ZeroMemory(result);

   request.action = TRADE_ACTION_DEAL;
   request.symbol = _Symbol;
   request.volume = NormalizeVolume(g_volume_lots);
   request.magic = InpMagicNumber;
   request.comment = BuildOrderComment(mode);
   request.type = (mode == PAD_MODE_BUY_MARKET ? ORDER_TYPE_BUY : ORDER_TYPE_SELL);
   request.price = NormalizePrice(mode == PAD_MODE_BUY_MARKET ? tick.ask : tick.bid);
   request.deviation = 10;
   request.type_filling = ResolveFillingMode(_Symbol);
   ApplyProtectionPrices(request.type, request.price, request.sl, request.tp);

   ResetLastError();
   bool sent = OrderSend(request, result);
   message = BuildTradeMessage(result, GetLastError());
   ticket = (result.order > 0 ? result.order : result.deal);
   return(sent && (result.retcode == TRADE_RETCODE_DONE || result.retcode == TRADE_RETCODE_PLACED));
}

bool ExecutePendingOrder(const ATOrderPadMode mode, const double price, ulong &ticket, string &message)
{
   MqlTradeRequest request;
   MqlTradeResult result;
   ZeroMemory(request);
   ZeroMemory(result);

   request.action = TRADE_ACTION_PENDING;
   request.symbol = _Symbol;
   request.volume = NormalizeVolume(g_volume_lots);
   request.magic = InpMagicNumber;
   request.comment = BuildOrderComment(mode);
   request.type = PendingOrderType(mode);
   request.price = NormalizePrice(price);
   request.type_time = ORDER_TIME_GTC;
   ApplyProtectionPrices(request.type, request.price, request.sl, request.tp);

   ResetLastError();
   bool sent = OrderSend(request, result);
   message = BuildTradeMessage(result, GetLastError());
   ticket = result.order;
   return(sent && (result.retcode == TRADE_RETCODE_DONE || result.retcode == TRADE_RETCODE_PLACED));
}

void ApplyProtectionPrices(const ENUM_ORDER_TYPE order_type, const double entry_price, double &stop_loss, double &take_profit)
{
   stop_loss = 0.0;
   take_profit = 0.0;

   bool is_buy =
      order_type == ORDER_TYPE_BUY
      || order_type == ORDER_TYPE_BUY_LIMIT
      || order_type == ORDER_TYPE_BUY_STOP;

   if(g_stop_loss_points > 0)
   {
      double offset = g_stop_loss_points * SymbolInfoDouble(_Symbol, SYMBOL_POINT);
      stop_loss = NormalizePrice(is_buy ? entry_price - offset : entry_price + offset);
   }

   if(g_take_profit_points > 0)
   {
      double offset = g_take_profit_points * SymbolInfoDouble(_Symbol, SYMBOL_POINT);
      take_profit = NormalizePrice(is_buy ? entry_price + offset : entry_price - offset);
   }
}

bool ValidatePendingPrice(const ATOrderPadMode mode, const double price, string &message)
{
   MqlTick tick;
   if(!SymbolInfoTick(_Symbol, tick))
   {
      message = "Current tick unavailable";
      return(false);
   }

   double point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   double min_distance = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL) * point;
   double normalized_price = NormalizePrice(price);

   if(mode == PAD_MODE_BUY_LIMIT && normalized_price >= tick.ask - min_distance)
   {
      message = "BUY LIMIT must sit below ask by at least the stop distance.";
      return(false);
   }
   if(mode == PAD_MODE_BUY_STOP && normalized_price <= tick.ask + min_distance)
   {
      message = "BUY STOP must sit above ask by at least the stop distance.";
      return(false);
   }
   if(mode == PAD_MODE_SELL_LIMIT && normalized_price <= tick.bid + min_distance)
   {
      message = "SELL LIMIT must sit above bid by at least the stop distance.";
      return(false);
   }
   if(mode == PAD_MODE_SELL_STOP && normalized_price >= tick.bid - min_distance)
   {
      message = "SELL STOP must sit below bid by at least the stop distance.";
      return(false);
   }

   return(true);
}

bool CanTradeNow(string &message)
{
   if(!TerminalInfoInteger(TERMINAL_TRADE_ALLOWED))
   {
      message = "Terminal trading is disabled.";
      return(false);
   }

   if(!MQLInfoInteger(MQL_TRADE_ALLOWED))
   {
      message = "Enable Algo Trading for this EA to send orders.";
      return(false);
   }

   if(!AccountInfoInteger(ACCOUNT_TRADE_ALLOWED))
   {
      message = "Account trade permission is disabled.";
      return(false);
   }

   long trade_mode = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_MODE);
   if(trade_mode == SYMBOL_TRADE_MODE_DISABLED)
   {
      message = "Trading is disabled for " + _Symbol + ".";
      return(false);
   }

   return(true);
}

void CreateOrUpdateStagingLine(const double price, const ATOrderPadMode mode)
{
   double line_price = NormalizePrice(price);

   if(ObjectFind(g_chart_id, g_staging_line_name) < 0)
   {
      ResetLastError();
      if(!ObjectCreate(g_chart_id, g_staging_line_name, OBJ_HLINE, 0, 0, line_price))
      {
         Print(__FUNCTION__, ": failed to create staging line error=", GetLastError());
         return;
      }
   }

   ObjectSetDouble(g_chart_id, g_staging_line_name, OBJPROP_PRICE, line_price);
   ObjectSetInteger(g_chart_id, g_staging_line_name, OBJPROP_COLOR, ModeLineColor(mode));
   ObjectSetInteger(g_chart_id, g_staging_line_name, OBJPROP_STYLE, STYLE_DASHDOTDOT);
   ObjectSetInteger(g_chart_id, g_staging_line_name, OBJPROP_WIDTH, 2);
   ObjectSetInteger(g_chart_id, g_staging_line_name, OBJPROP_BACK, false);
   ObjectSetInteger(g_chart_id, g_staging_line_name, OBJPROP_SELECTABLE, true);
   ObjectSetInteger(g_chart_id, g_staging_line_name, OBJPROP_SELECTED, true);
   ObjectSetInteger(g_chart_id, g_staging_line_name, OBJPROP_HIDDEN, false);
   ObjectSetInteger(g_chart_id, g_staging_line_name, OBJPROP_ZORDER, 4);
   ChartRedraw(g_chart_id);
}

void CancelStaging(const string status_text)
{
   ObjectDelete(g_chart_id, g_staging_line_name);
   g_staged_mode = PAD_MODE_NONE;
   UpdateButtonStates();
   if(status_text != "")
      SetStatus(status_text, InpMetaColor);
}

bool StagingLineExists()
{
   return(ObjectFind(g_chart_id, g_staging_line_name) >= 0);
}

double StagingLinePrice()
{
   if(!StagingLineExists())
      return(0.0);
   return(NormalizePrice(ObjectGetDouble(g_chart_id, g_staging_line_name, OBJPROP_PRICE)));
}

double SuggestedPendingPrice(const ATOrderPadMode mode)
{
   MqlTick tick;
   if(!SymbolInfoTick(_Symbol, tick))
      return(0.0);

   double point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   double stop_distance = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL) * point;
   double base_offset = MathMax(stop_distance + point, InpPendingOffsetPoints * point);

   if(mode == PAD_MODE_BUY_LIMIT)
      return(tick.ask - base_offset);
   if(mode == PAD_MODE_BUY_STOP)
      return(tick.ask + base_offset);
   if(mode == PAD_MODE_SELL_LIMIT)
      return(tick.bid + base_offset);
   if(mode == PAD_MODE_SELL_STOP)
      return(tick.bid - base_offset);
   return((tick.ask + tick.bid) / 2.0);
}

void UpdateMetaText()
{
   string mode = (InpEnableTrading ? "live" : "preview");
   string meta =
      _Symbol
      + " | lots " + DoubleToString(g_volume_lots, VolumeDigits())
      + " | SL " + IntegerToString(g_stop_loss_points)
      + " | TP " + IntegerToString(g_take_profit_points)
      + " | " + mode;
   ObjectSetString(g_chart_id, g_meta_name, OBJPROP_TEXT, meta);
}

void UpdateButtonStates()
{
   ColorizeButton(g_buy_market_name, InpBuyColor, InpTextColor);
   ColorizeButton(g_sell_market_name, InpSellColor, InpTextColor);
   ColorizeButton(g_place_name, InpBorderColor, InpTextColor);
   ColorizeButton(g_cancel_name, InpBorderColor, InpTextColor);
   ColorizeButton(g_buy_limit_name, InpPendingBuyColor, InpTextColor);
   ColorizeButton(g_sell_limit_name, InpPendingSellColor, InpTextColor);
   ColorizeButton(g_buy_stop_name, InpPendingBuyColor, InpTextColor);
   ColorizeButton(g_sell_stop_name, InpPendingSellColor, InpTextColor);

   string active_button = ButtonNameForMode(g_staged_mode);
   if(active_button != "")
      ColorizeButton(active_button, InpActiveButtonColor, InpActiveButtonTextColor);
   if(g_staged_mode != PAD_MODE_NONE)
      ColorizeButton(g_place_name, InpAccentColor, InpActiveButtonTextColor);
}

void ColorizeButton(const string name, const color background_color, const color text_color)
{
   if(ObjectFind(g_chart_id, name) < 0)
      return;
   ObjectSetInteger(g_chart_id, name, OBJPROP_BGCOLOR, background_color);
   ObjectSetInteger(g_chart_id, name, OBJPROP_COLOR, text_color);
}

void ResetButtonState(const string name)
{
   if(ObjectFind(g_chart_id, name) >= 0)
      ObjectSetInteger(g_chart_id, name, OBJPROP_STATE, false);
}

void SetStatus(const string text, const color text_color)
{
   ObjectSetString(g_chart_id, g_status_name, OBJPROP_TEXT, text);
   ObjectSetInteger(g_chart_id, g_status_name, OBJPROP_COLOR, text_color);
   ChartRedraw(g_chart_id);
}

string DefaultStatusText()
{
   if(InpEnableTrading)
      return("Choose an order type. Pending orders can be sent with PLACE.");
   return("Preview only. Set EnableTrading=true when you want live sends.");
}

string ButtonNameForMode(const ATOrderPadMode mode)
{
   if(mode == PAD_MODE_BUY_LIMIT)
      return(g_buy_limit_name);
   if(mode == PAD_MODE_SELL_LIMIT)
      return(g_sell_limit_name);
   if(mode == PAD_MODE_BUY_STOP)
      return(g_buy_stop_name);
   if(mode == PAD_MODE_SELL_STOP)
      return(g_sell_stop_name);
   return("");
}

string ModeLabel(const ATOrderPadMode mode)
{
   if(mode == PAD_MODE_BUY_MARKET)
      return("BUY MKT");
   if(mode == PAD_MODE_SELL_MARKET)
      return("SELL MKT");
   if(mode == PAD_MODE_BUY_LIMIT)
      return("BUY LIMIT");
   if(mode == PAD_MODE_SELL_LIMIT)
      return("SELL LIMIT");
   if(mode == PAD_MODE_BUY_STOP)
      return("BUY STOP");
   if(mode == PAD_MODE_SELL_STOP)
      return("SELL STOP");
   return("");
}

bool IsPendingMode(const ATOrderPadMode mode)
{
   return(mode == PAD_MODE_BUY_LIMIT || mode == PAD_MODE_SELL_LIMIT || mode == PAD_MODE_BUY_STOP || mode == PAD_MODE_SELL_STOP);
}

bool ModeIsBuy(const ATOrderPadMode mode)
{
   return(mode == PAD_MODE_BUY_MARKET || mode == PAD_MODE_BUY_LIMIT || mode == PAD_MODE_BUY_STOP);
}

color ModeLineColor(const ATOrderPadMode mode)
{
   return(ModeIsBuy(mode) ? InpPendingBuyColor : InpPendingSellColor);
}

ENUM_ORDER_TYPE PendingOrderType(const ATOrderPadMode mode)
{
   if(mode == PAD_MODE_BUY_LIMIT)
      return(ORDER_TYPE_BUY_LIMIT);
   if(mode == PAD_MODE_SELL_LIMIT)
      return(ORDER_TYPE_SELL_LIMIT);
   if(mode == PAD_MODE_BUY_STOP)
      return(ORDER_TYPE_BUY_STOP);
   return(ORDER_TYPE_SELL_STOP);
}

string BuildOrderComment(const ATOrderPadMode mode)
{
   string label = StringReplaceCopy(ModeLabel(mode), " ", "_");
   return(InpOrderComment + "|" + label);
}

string StagingInstruction(const ATOrderPadMode mode)
{
   if(InpAutoSubmitPendingOnDrop)
      return("Drag " + ModeLabel(mode) + " line, or press PLACE to send.");
   return("Drag " + ModeLabel(mode) + " line, then press PLACE to send.");
}

void SyncEditFields()
{
   if(ObjectFind(g_chart_id, g_lots_edit_name) >= 0)
      ObjectSetString(g_chart_id, g_lots_edit_name, OBJPROP_TEXT, DoubleToString(g_volume_lots, VolumeDigits()));
   if(ObjectFind(g_chart_id, g_sl_edit_name) >= 0)
      ObjectSetString(g_chart_id, g_sl_edit_name, OBJPROP_TEXT, IntegerToString(g_stop_loss_points));
   if(ObjectFind(g_chart_id, g_tp_edit_name) >= 0)
      ObjectSetString(g_chart_id, g_tp_edit_name, OBJPROP_TEXT, IntegerToString(g_take_profit_points));
}

string BuildTradeMessage(const MqlTradeResult &result, const int last_error)
{
   string comment = result.comment;
   if(comment == "")
      comment = "retcode=" + IntegerToString((int)result.retcode);
   if(last_error != 0)
      comment += " err=" + IntegerToString(last_error);
   return(comment);
}

double NormalizePrice(const double value)
{
   return(NormalizeDouble(value, (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS)));
}

double NormalizeVolume(const double value)
{
   double volume_min = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double volume_max = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   double volume_step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);

   if(volume_min <= 0.0)
      volume_min = value;
   if(volume_max <= 0.0)
      volume_max = value;
   if(volume_step <= 0.0)
      volume_step = 0.01;

   double clamped = MathMax(volume_min, MathMin(volume_max, value));
   double steps = MathRound((clamped - volume_min) / volume_step);
   double normalized = volume_min + (steps * volume_step);
   normalized = MathMax(volume_min, MathMin(volume_max, normalized));
   return(NormalizeDouble(normalized, VolumeDigits()));
}

int VolumeDigits()
{
   double volume_step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   if(volume_step <= 0.0)
      return(2);
   int digits = 0;
   while(digits < 8)
   {
      double scaled = volume_step * MathPow(10.0, digits);
      if(MathAbs(scaled - MathRound(scaled)) < 0.0000001)
         return(digits);
      digits++;
   }
   return(2);
}

string PriceToText(const double price)
{
   return(DoubleToString(NormalizePrice(price), (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS)));
}

string UnsignedLongToString(const ulong value)
{
   return(StringFormat("%I64u", value));
}

string StringReplaceCopy(const string source, const string search, const string replacement)
{
   string output = source;
   StringReplace(output, search, replacement);
   return(output);
}

string TrimString(string value)
{
   string result = value;
   StringTrimLeft(result);
   StringTrimRight(result);
   return(result);
}

ENUM_ORDER_TYPE_FILLING ResolveFillingMode(const string symbol)
{
   long filling_mode = SymbolInfoInteger(symbol, SYMBOL_FILLING_MODE);

   if((filling_mode & SYMBOL_FILLING_IOC) == SYMBOL_FILLING_IOC)
      return(ORDER_FILLING_IOC);
   if((filling_mode & SYMBOL_FILLING_FOK) == SYMBOL_FILLING_FOK)
      return(ORDER_FILLING_FOK);
   return(ORDER_FILLING_RETURN);
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

int ResolveInnerX(const int left_padding)
{
   if(IsRightCorner())
      return(InpXOffset + InpPanelWidth - left_padding);
   return(InpXOffset + left_padding);
}

int ResolveInnerY(const int top_padding)
{
   if(IsLowerCorner())
      return(InpYOffset + InpPanelHeight - top_padding);
   return(InpYOffset + top_padding);
}
