#property strict
#property version   "1.00"
#property description "AT tester-safe manual replay EA for Strategy Tester visual mode."
#property description "Reads commands from FILE_COMMON and writes status/ack rows for local manual practice trading."

input bool InpEnableOrderExecution = false;
input string InpSessionId = "default";
input int InpPollIntervalSeconds = 1;
input double InpDefaultVolumeLots = 0.10;
input long InpMagicNumber = 6101101;
input string InpOrderComment = "ATManualReplay";

string g_commands_path = "";
string g_acks_path = "";
string g_status_path = "";
string g_processed_command_ids = "|";

int OnInit()
{
   EnsureCommonFolders();
   g_commands_path = SessionFolder() + "\\commands.tsv";
   g_acks_path = SessionFolder() + "\\acks.tsv";
   g_status_path = SessionFolder() + "\\status.tsv";

   EventSetTimer(MathMax(InpPollIntervalSeconds, 1));
   WriteAck("system", "ready", "init", 0, "Manual replay tester EA initialized.");
   WriteStatus();
   return(INIT_SUCCEEDED);
}

void OnDeinit(const int reason)
{
   EventKillTimer();
   WriteStatus();
}

void OnTick()
{
}

void OnTimer()
{
   ProcessCommands();
   WriteStatus();
}

void EnsureCommonFolders()
{
   FolderCreate("AT", FILE_COMMON);
   FolderCreate("AT\\manual_replay", FILE_COMMON);
   FolderCreate(SessionFolder(), FILE_COMMON);
}

string SessionFolder()
{
   return("AT\\manual_replay\\" + SafePathComponent(InpSessionId));
}

string SafePathComponent(string value)
{
   string trimmed = TrimString(value);
   if(trimmed == "")
      return("default");

   string output = "";
   for(int index = 0; index < StringLen(trimmed); index++)
   {
      string ch = StringSubstr(trimmed, index, 1);
      if(
         ch == "\\"
         || ch == "/"
         || ch == ":"
         || ch == "*"
         || ch == "?"
         || ch == "\""
         || ch == "<"
         || ch == ">"
         || ch == "|"
         || ch == "\t"
         || ch == "\r"
         || ch == "\n"
      )
      {
         output += "_";
         continue;
      }
      output += ch;
   }

   output = TrimString(output);
   if(output == "")
      return("default");
   return(output);
}

void ProcessCommands()
{
   int handle = FileOpen(
      g_commands_path,
      FILE_READ | FILE_CSV | FILE_COMMON | FILE_ANSI | FILE_SHARE_READ | FILE_SHARE_WRITE,
      '\t'
   );
   if(handle == INVALID_HANDLE)
      return;

   while(!FileIsEnding(handle))
   {
      string command_id = FileReadString(handle);
      string created_at = FileReadString(handle);
      string action = FileReadString(handle);
      string symbol = FileReadString(handle);
      string order_type = FileReadString(handle);
      string volume_text = FileReadString(handle);
      string entry_price_text = FileReadString(handle);
      string sl_price_text = FileReadString(handle);
      string tp_price_text = FileReadString(handle);
      string sl_points_text = FileReadString(handle);
      string tp_points_text = FileReadString(handle);
      string ticket_id_text = FileReadString(handle);
      string comment = FileReadString(handle);

      if(command_id == "" || action == "")
         continue;
      if(IsCommandProcessed(command_id))
         continue;

      MarkCommandProcessed(command_id);

      if(!CommandTargetsCurrentSymbol(symbol))
      {
         WriteAck(command_id, "ignored", action, 0, "Symbol mismatch for current tester chart.");
         continue;
      }

      if(!InpEnableOrderExecution)
      {
         WriteAck(command_id, "rejected", action, 0, "InpEnableOrderExecution=false");
         continue;
      }

      string can_trade_message = "";
      if(!CanTradeNow(can_trade_message))
      {
         WriteAck(command_id, "rejected", action, 0, can_trade_message);
         continue;
      }

      double volume_lots = ParseDouble(volume_text, 0.0);
      double entry_price = ParseDouble(entry_price_text, 0.0);
      double sl_price = ParseDouble(sl_price_text, 0.0);
      double tp_price = ParseDouble(tp_price_text, 0.0);
      int sl_points = (int)ParseLong(sl_points_text, 0);
      int tp_points = (int)ParseLong(tp_points_text, 0);
      long ticket_id = ParseLong(ticket_id_text, 0);

      string outcome_status = "rejected";
      string message = "";
      long outcome_ticket = 0;

      if(
         action == "place_order"
         && ExecutePlaceOrder(
            order_type,
            volume_lots,
            entry_price,
            sl_price,
            tp_price,
            sl_points,
            tp_points,
            comment,
            outcome_ticket,
            message
         )
      )
      {
         outcome_status = "applied";
      }
      else if(action == "close_ticket" && ExecuteCloseTicket(ticket_id, volume_lots, outcome_ticket, message))
      {
         outcome_status = "applied";
      }
      else if(action == "protect_ticket" && ExecuteProtectTicket(ticket_id, sl_price, tp_price, message))
      {
         outcome_status = "applied";
         outcome_ticket = ticket_id;
      }
      else if(action == "close_all" && ExecuteCloseAll(message))
      {
         outcome_status = "applied";
      }
      else if(action == "cancel_all" && ExecuteCancelAll(message))
      {
         outcome_status = "applied";
      }
      else if(action == "flatten" && ExecuteFlatten(message))
      {
         outcome_status = "applied";
      }
      else if(message == "")
      {
         message = "Unsupported or failed action.";
      }

      WriteAck(command_id, outcome_status, action, outcome_ticket, message);
   }

   FileClose(handle);
}

bool ExecutePlaceOrder(
   string order_type_label,
   double requested_volume,
   double requested_entry_price,
   double requested_sl_price,
   double requested_tp_price,
   int requested_sl_points,
   int requested_tp_points,
   string requested_comment,
   long &ticket_out,
   string &message
)
{
   ENUM_ORDER_TYPE order_type;
   if(!ParseOrderType(order_type_label, order_type))
   {
      message = "Unsupported order type.";
      return(false);
   }

   double volume = NormalizeVolume(requested_volume > 0.0 ? requested_volume : InpDefaultVolumeLots);
   if(volume <= 0.0)
   {
      message = "Volume must be greater than zero.";
      return(false);
   }

   MqlTradeRequest request;
   MqlTradeResult result;
   ZeroMemory(request);
   ZeroMemory(result);

   request.symbol = _Symbol;
   request.volume = volume;
   request.magic = InpMagicNumber;
   request.comment = BuildOrderComment(order_type_label, requested_comment);
   request.type = order_type;

   bool is_market_order = (order_type == ORDER_TYPE_BUY || order_type == ORDER_TYPE_SELL);
   if(is_market_order)
   {
      MqlTick tick;
      if(!SymbolInfoTick(_Symbol, tick))
      {
         message = "Current tick unavailable.";
         return(false);
      }

      request.action = TRADE_ACTION_DEAL;
      request.price = NormalizePrice(order_type == ORDER_TYPE_BUY ? tick.ask : tick.bid);
      request.deviation = 10;
      request.type_filling = ResolveFillingMode(_Symbol);
   }
   else
   {
      if(requested_entry_price <= 0.0)
      {
         message = "Pending orders require an entry price.";
         return(false);
      }

      request.action = TRADE_ACTION_PENDING;
      request.price = NormalizePrice(requested_entry_price);
      request.type_time = ORDER_TIME_GTC;
      request.type_filling = ResolveFillingMode(_Symbol);

      if(!ValidatePendingPrice(order_type, request.price, message))
         return(false);
   }

   ResolveProtectionPrices(
      order_type,
      request.price,
      requested_sl_price,
      requested_tp_price,
      requested_sl_points,
      requested_tp_points,
      request.sl,
      request.tp
   );

   ResetLastError();
   bool sent = OrderSend(request, result);
   message = BuildTradeMessage(result, GetLastError());
   ticket_out = (long)(result.order > 0 ? result.order : result.deal);
   return(sent && (result.retcode == TRADE_RETCODE_DONE || result.retcode == TRADE_RETCODE_PLACED));
}

bool ExecuteCloseTicket(long requested_ticket, double requested_volume, long &ticket_out, string &message)
{
   if(requested_ticket <= 0)
   {
      message = "Ticket id is required.";
      return(false);
   }

   if(PositionSelectByTicket((ulong)requested_ticket))
   {
      ticket_out = requested_ticket;
      return(ExecuteClosePositionTicket((ulong)requested_ticket, requested_volume, message));
   }

   if(OrderSelect((ulong)requested_ticket))
   {
      ENUM_ORDER_TYPE order_type = (ENUM_ORDER_TYPE)OrderGetInteger(ORDER_TYPE);
      if(!IsPendingOrderType(order_type))
      {
         message = "Ticket is not a pending order.";
         return(false);
      }
      ticket_out = requested_ticket;
      return(CancelPendingOrder((ulong)requested_ticket, message));
   }

   message = "Ticket was not found.";
   return(false);
}

bool ExecuteProtectTicket(long requested_ticket, double requested_sl_price, double requested_tp_price, string &message)
{
   if(requested_ticket <= 0)
   {
      message = "Ticket id is required.";
      return(false);
   }

   if(requested_sl_price <= 0.0 && requested_tp_price <= 0.0)
   {
      message = "Provide at least one of sl_price or tp_price.";
      return(false);
   }

   if(PositionSelectByTicket((ulong)requested_ticket))
      return(ModifyOpenPosition((ulong)requested_ticket, requested_sl_price, requested_tp_price, message));

   if(OrderSelect((ulong)requested_ticket))
      return(ModifyPendingOrder((ulong)requested_ticket, requested_sl_price, requested_tp_price, message));

   message = "Ticket was not found.";
   return(false);
}

bool ExecuteCloseAll(string &message)
{
   return(CloseAllPositions(message));
}

bool ExecuteCancelAll(string &message)
{
   return(CancelAllPendingOrders(message));
}

bool ExecuteFlatten(string &message)
{
   string cancel_message = "";
   string close_message = "";
   bool cancel_ok = CancelAllPendingOrders(cancel_message);
   bool close_ok = CloseAllPositions(close_message);

   message = "cancel_all=" + (cancel_ok ? "ok" : cancel_message) + "; close_all=" + (close_ok ? "ok" : close_message);
   return(cancel_ok && close_ok);
}

bool CloseAllPositions(string &message)
{
   int closed = 0;
   for(int index = PositionsTotal() - 1; index >= 0; index--)
   {
      ulong ticket = PositionGetTicket(index);
      if(ticket == 0 || !PositionSelectByTicket(ticket))
         continue;
      if(!SymbolsMatch(PositionGetString(POSITION_SYMBOL), _Symbol))
         continue;

      string close_message = "";
      if(!ExecuteClosePositionTicket(ticket, 0.0, close_message))
      {
         message = close_message;
         return(false);
      }
      closed++;
   }

   if(closed == 0)
      message = "No open positions were present.";
   else
      message = "Closed " + IntegerToString(closed) + " open position(s).";
   return(true);
}

bool CancelAllPendingOrders(string &message)
{
   int cancelled = 0;
   for(int index = OrdersTotal() - 1; index >= 0; index--)
   {
      ulong ticket = OrderGetTicket(index);
      if(ticket == 0 || !OrderSelect(ticket))
         continue;
      if(!SymbolsMatch(OrderGetString(ORDER_SYMBOL), _Symbol))
         continue;

      ENUM_ORDER_TYPE order_type = (ENUM_ORDER_TYPE)OrderGetInteger(ORDER_TYPE);
      if(!IsPendingOrderType(order_type))
         continue;

      string cancel_message = "";
      if(!CancelPendingOrder(ticket, cancel_message))
      {
         message = cancel_message;
         return(false);
      }
      cancelled++;
   }

   if(cancelled == 0)
      message = "No pending orders were present.";
   else
      message = "Cancelled " + IntegerToString(cancelled) + " pending order(s).";
   return(true);
}

bool ExecuteClosePositionTicket(ulong ticket, double requested_volume, string &message)
{
   if(!PositionSelectByTicket(ticket))
   {
      message = "Open position not found.";
      return(false);
   }

   ENUM_POSITION_TYPE position_type = (ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE);
   string symbol = PositionGetString(POSITION_SYMBOL);
   double position_volume = PositionGetDouble(POSITION_VOLUME);
   double close_volume = position_volume;
   if(requested_volume > 0.0 && requested_volume < position_volume)
      close_volume = NormalizeVolume(requested_volume);

   MqlTradeRequest request;
   MqlTradeResult result;
   ZeroMemory(request);
   ZeroMemory(result);

   request.action = TRADE_ACTION_DEAL;
   request.position = ticket;
   request.symbol = symbol;
   request.volume = close_volume;
   request.type = (position_type == POSITION_TYPE_BUY ? ORDER_TYPE_SELL : ORDER_TYPE_BUY);
   request.price = NormalizePrice(
      position_type == POSITION_TYPE_BUY
      ? SymbolInfoDouble(symbol, SYMBOL_BID)
      : SymbolInfoDouble(symbol, SYMBOL_ASK)
   );
   request.type_filling = ResolveFillingMode(symbol);

   ResetLastError();
   bool sent = OrderSend(request, result);
   message = BuildTradeMessage(result, GetLastError());
   return(sent && (result.retcode == TRADE_RETCODE_DONE || result.retcode == TRADE_RETCODE_PLACED));
}

bool CancelPendingOrder(ulong ticket, string &message)
{
   if(!OrderSelect(ticket))
   {
      message = "Pending order not found.";
      return(false);
   }

   MqlTradeRequest request;
   MqlTradeResult result;
   ZeroMemory(request);
   ZeroMemory(result);

   request.action = TRADE_ACTION_REMOVE;
   request.order = ticket;
   request.symbol = OrderGetString(ORDER_SYMBOL);

   ResetLastError();
   bool sent = OrderSend(request, result);
   message = BuildTradeMessage(result, GetLastError());
   return(sent && result.retcode == TRADE_RETCODE_DONE);
}

bool ModifyOpenPosition(ulong ticket, double requested_sl_price, double requested_tp_price, string &message)
{
   if(!PositionSelectByTicket(ticket))
   {
      message = "Open position not found.";
      return(false);
   }

   double current_sl = PositionGetDouble(POSITION_SL);
   double current_tp = PositionGetDouble(POSITION_TP);

   MqlTradeRequest request;
   MqlTradeResult result;
   ZeroMemory(request);
   ZeroMemory(result);

   request.action = TRADE_ACTION_SLTP;
   request.position = ticket;
   request.symbol = PositionGetString(POSITION_SYMBOL);
   request.sl = (requested_sl_price > 0.0 ? NormalizePrice(requested_sl_price) : current_sl);
   request.tp = (requested_tp_price > 0.0 ? NormalizePrice(requested_tp_price) : current_tp);

   ResetLastError();
   bool sent = OrderSend(request, result);
   message = BuildTradeMessage(result, GetLastError());
   return(sent && result.retcode == TRADE_RETCODE_DONE);
}

bool ModifyPendingOrder(ulong ticket, double requested_sl_price, double requested_tp_price, string &message)
{
   if(!OrderSelect(ticket))
   {
      message = "Pending order not found.";
      return(false);
   }

   ENUM_ORDER_TYPE order_type = (ENUM_ORDER_TYPE)OrderGetInteger(ORDER_TYPE);
   if(!IsPendingOrderType(order_type))
   {
      message = "Ticket is not a pending order.";
      return(false);
   }

   MqlTradeRequest request;
   MqlTradeResult result;
   ZeroMemory(request);
   ZeroMemory(result);

   request.action = TRADE_ACTION_MODIFY;
   request.order = ticket;
   request.symbol = OrderGetString(ORDER_SYMBOL);
   request.price = OrderGetDouble(ORDER_PRICE_OPEN);
   request.stoplimit = OrderGetDouble(ORDER_PRICE_STOPLIMIT);
   request.sl = (requested_sl_price > 0.0 ? NormalizePrice(requested_sl_price) : OrderGetDouble(ORDER_SL));
   request.tp = (requested_tp_price > 0.0 ? NormalizePrice(requested_tp_price) : OrderGetDouble(ORDER_TP));
   request.type_time = (ENUM_ORDER_TYPE_TIME)OrderGetInteger(ORDER_TYPE_TIME);
   request.expiration = (datetime)OrderGetInteger(ORDER_TIME_EXPIRATION);

   ResetLastError();
   bool sent = OrderSend(request, result);
   message = BuildTradeMessage(result, GetLastError());
   return(sent && result.retcode == TRADE_RETCODE_DONE);
}

bool ParseOrderType(string label, ENUM_ORDER_TYPE &order_type)
{
   string normalized = TrimString(label);
   StringToLower(normalized);
   StringReplace(normalized, "-", "_");

   if(normalized == "buy")
   {
      order_type = ORDER_TYPE_BUY;
      return(true);
   }
   if(normalized == "sell")
   {
      order_type = ORDER_TYPE_SELL;
      return(true);
   }
   if(normalized == "buy_limit")
   {
      order_type = ORDER_TYPE_BUY_LIMIT;
      return(true);
   }
   if(normalized == "sell_limit")
   {
      order_type = ORDER_TYPE_SELL_LIMIT;
      return(true);
   }
   if(normalized == "buy_stop")
   {
      order_type = ORDER_TYPE_BUY_STOP;
      return(true);
   }
   if(normalized == "sell_stop")
   {
      order_type = ORDER_TYPE_SELL_STOP;
      return(true);
   }
   return(false);
}

bool ValidatePendingPrice(ENUM_ORDER_TYPE order_type, double price, string &message)
{
   MqlTick tick;
   if(!SymbolInfoTick(_Symbol, tick))
   {
      message = "Current tick unavailable.";
      return(false);
   }

   double point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   double minimum_distance = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL) * point;
   double normalized_price = NormalizePrice(price);

   if(order_type == ORDER_TYPE_BUY_LIMIT && normalized_price >= tick.ask - minimum_distance)
   {
      message = "BUY LIMIT must stay below ask by at least the stop distance.";
      return(false);
   }
   if(order_type == ORDER_TYPE_BUY_STOP && normalized_price <= tick.ask + minimum_distance)
   {
      message = "BUY STOP must stay above ask by at least the stop distance.";
      return(false);
   }
   if(order_type == ORDER_TYPE_SELL_LIMIT && normalized_price <= tick.bid + minimum_distance)
   {
      message = "SELL LIMIT must stay above bid by at least the stop distance.";
      return(false);
   }
   if(order_type == ORDER_TYPE_SELL_STOP && normalized_price >= tick.bid - minimum_distance)
   {
      message = "SELL STOP must stay below bid by at least the stop distance.";
      return(false);
   }

   return(true);
}

void ResolveProtectionPrices(
   ENUM_ORDER_TYPE order_type,
   double entry_price,
   double requested_sl_price,
   double requested_tp_price,
   int requested_sl_points,
   int requested_tp_points,
   double &sl_out,
   double &tp_out
)
{
   sl_out = 0.0;
   tp_out = 0.0;

   bool is_buy =
      order_type == ORDER_TYPE_BUY
      || order_type == ORDER_TYPE_BUY_LIMIT
      || order_type == ORDER_TYPE_BUY_STOP;

   double point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);

   if(requested_sl_price > 0.0)
      sl_out = NormalizePrice(requested_sl_price);
   else if(requested_sl_points > 0 && point > 0.0)
      sl_out = NormalizePrice(is_buy ? entry_price - (requested_sl_points * point) : entry_price + (requested_sl_points * point));

   if(requested_tp_price > 0.0)
      tp_out = NormalizePrice(requested_tp_price);
   else if(requested_tp_points > 0 && point > 0.0)
      tp_out = NormalizePrice(is_buy ? entry_price + (requested_tp_points * point) : entry_price - (requested_tp_points * point));
}

void WriteAck(string command_id, string status, string action, long ticket_id, string message)
{
   int handle = FileOpen(
      g_acks_path,
      FILE_READ | FILE_WRITE | FILE_CSV | FILE_COMMON | FILE_ANSI | FILE_SHARE_READ | FILE_SHARE_WRITE,
      '\t'
   );
   if(handle == INVALID_HANDLE)
      return;

   FileSeek(handle, 0, SEEK_END);
   FileWrite(
      handle,
      SanitizeField(command_id),
      FormatDateTime(TimeTradeServer()),
      SanitizeField(status),
      SanitizeField(action),
      LongValueToString(ticket_id),
      SanitizeField(message)
   );
   FileFlush(handle);
   FileClose(handle);
}

void WriteStatus()
{
   MqlTick tick;
   bool has_tick = SymbolInfoTick(_Symbol, tick);
   int open_positions = 0;
   int pending_orders = 0;
   double volume_lots = 0.0;
   double floating_pnl = 0.0;
   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   double equity = AccountInfoDouble(ACCOUNT_EQUITY);

   CountOpenPositions(open_positions, volume_lots, floating_pnl);
   pending_orders = CountPendingOrders();

   double spread_points = 0.0;
   double point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   if(has_tick && point > 0.0)
      spread_points = (tick.ask - tick.bid) / point;

   int handle = FileOpen(
      g_status_path,
      FILE_WRITE | FILE_CSV | FILE_COMMON | FILE_ANSI | FILE_SHARE_READ | FILE_SHARE_WRITE,
      '\t'
   );
   if(handle == INVALID_HANDLE)
      return;

   FileWrite(
      handle,
      FormatDateTime(TimeTradeServer()),
      _Symbol,
      has_tick ? DoubleToString(tick.bid, (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS)) : "",
      has_tick ? DoubleToString(tick.ask, (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS)) : "",
      DoubleToString(spread_points, 2),
      IntegerToString(open_positions),
      IntegerToString(pending_orders),
      DoubleToString(volume_lots, VolumeDigits()),
      DoubleToString(floating_pnl, 2),
      DoubleToString(balance, 2),
      DoubleToString(equity, 2)
   );
   FileFlush(handle);
   FileClose(handle);
}

void CountOpenPositions(int &count_out, double &volume_out, double &pnl_out)
{
   count_out = 0;
   volume_out = 0.0;
   pnl_out = 0.0;

   for(int index = 0; index < PositionsTotal(); index++)
   {
      ulong ticket = PositionGetTicket(index);
      if(ticket == 0 || !PositionSelectByTicket(ticket))
         continue;
      if(!SymbolsMatch(PositionGetString(POSITION_SYMBOL), _Symbol))
         continue;

      count_out++;
      volume_out += PositionGetDouble(POSITION_VOLUME);
      pnl_out += PositionGetDouble(POSITION_PROFIT);
   }
}

int CountPendingOrders()
{
   int count = 0;
   for(int index = 0; index < OrdersTotal(); index++)
   {
      ulong ticket = OrderGetTicket(index);
      if(ticket == 0 || !OrderSelect(ticket))
         continue;
      if(!SymbolsMatch(OrderGetString(ORDER_SYMBOL), _Symbol))
         continue;

      ENUM_ORDER_TYPE order_type = (ENUM_ORDER_TYPE)OrderGetInteger(ORDER_TYPE);
      if(IsPendingOrderType(order_type))
         count++;
   }
   return(count);
}

bool IsPendingOrderType(ENUM_ORDER_TYPE order_type)
{
   return(
      order_type == ORDER_TYPE_BUY_LIMIT
      || order_type == ORDER_TYPE_SELL_LIMIT
      || order_type == ORDER_TYPE_BUY_STOP
      || order_type == ORDER_TYPE_SELL_STOP
      || order_type == ORDER_TYPE_BUY_STOP_LIMIT
      || order_type == ORDER_TYPE_SELL_STOP_LIMIT
   );
}

bool IsCommandProcessed(string command_id)
{
   return(StringFind(g_processed_command_ids, "|" + command_id + "|") >= 0);
}

void MarkCommandProcessed(string command_id)
{
   g_processed_command_ids += command_id + "|";
}

bool CommandTargetsCurrentSymbol(string requested_symbol)
{
   string normalized = TrimString(requested_symbol);
   if(normalized == "")
      return(true);
   return(SymbolsMatch(normalized, _Symbol));
}

bool SymbolsMatch(string left, string right)
{
   string normalized_left = TrimString(left);
   string normalized_right = TrimString(right);
   StringToUpper(normalized_left);
   StringToUpper(normalized_right);
   return(normalized_left == normalized_right);
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

double ParseDouble(string value, double fallback)
{
   string trimmed = TrimString(value);
   if(trimmed == "")
      return(fallback);
   return(StringToDouble(trimmed));
}

long ParseLong(string value, long fallback)
{
   string trimmed = TrimString(value);
   if(trimmed == "")
      return(fallback);
   return((long)StringToInteger(trimmed));
}

string BuildOrderComment(string order_type_label, string requested_comment)
{
   string suffix = SanitizeField(requested_comment);
   string label = SanitizeField(order_type_label);
   if(suffix == "")
      return(InpOrderComment + "|" + label);
   return(InpOrderComment + "|" + label + "|" + suffix);
}

string SanitizeField(string value)
{
   string output = TrimString(value);
   StringReplace(output, "\t", " ");
   StringReplace(output, "\r", " ");
   StringReplace(output, "\n", " ");
   return(output);
}

string FormatDateTime(datetime value)
{
   return(TimeToString(value, TIME_DATE | TIME_SECONDS));
}

string LongValueToString(long value)
{
   return(StringFormat("%I64d", value));
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

double NormalizePrice(double value)
{
   return(NormalizeDouble(value, (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS)));
}

double NormalizeVolume(double value)
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

string TrimString(string value)
{
   string result = value;
   StringTrimLeft(result);
   StringTrimRight(result);
   return(result);
}

ENUM_ORDER_TYPE_FILLING ResolveFillingMode(string symbol)
{
   long filling_mode = SymbolInfoInteger(symbol, SYMBOL_FILLING_MODE);

   if((filling_mode & SYMBOL_FILLING_IOC) == SYMBOL_FILLING_IOC)
      return(ORDER_FILLING_IOC);
   if((filling_mode & SYMBOL_FILLING_FOK) == SYMBOL_FILLING_FOK)
      return(ORDER_FILLING_FOK);
   return(ORDER_FILLING_RETURN);
}
