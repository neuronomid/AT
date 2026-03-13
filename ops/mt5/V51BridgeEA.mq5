#property strict

input string BridgeBaseUrl = "http://127.0.0.1:8091";
input string RuntimeSymbol = "BTCUSD";
input int PollIntervalSeconds = 1;
input int HttpTimeoutMs = 3000;
input int BarsLookback1m = 120;
input int BarsLookback5m = 60;
input int BarsLookback15m = 40;

bool g_busy = false;

int OnInit()
{
   EventSetTimer(PollIntervalSeconds);
   return(INIT_SUCCEEDED);
}

void OnDeinit(const int reason)
{
   EventKillTimer();
}

void OnTimer()
{
   if(g_busy)
      return;

   g_busy = true;
   PublishSnapshot();
   PollCommands();
   g_busy = false;
}

void PublishSnapshot()
{
   string payload = BuildSnapshotJson();
   if(payload == "")
      return;

   string response = HttpRequest("POST", BridgeBaseUrl + "/bridge/snapshot", payload);
   if(response == "")
      Print("V51BridgeEA: snapshot publish failed.");
}

void PollCommands()
{
   string response = HttpRequest("GET", BridgeBaseUrl + "/bridge/commands?limit=5", "");
   if(response == "" || StringFind(response, "\"commands\":[]") >= 0)
      return;

   int cursor = 0;
   while(true)
   {
      int command_pos = StringFind(response, "\"command_id\":\"", cursor);
      if(command_pos < 0)
         break;

      string command_id = JsonGetString(response, "command_id", command_pos);
      string command_type = JsonGetString(response, "command_type", command_pos);
      string side = JsonGetString(response, "side", command_pos);
      string symbol = JsonGetString(response, "symbol", command_pos);
      string ticket_id = JsonGetString(response, "ticket_id", command_pos);
      string reason = JsonGetString(response, "reason", command_pos);
      string comment = JsonGetString(response, "comment", command_pos);
      double volume = JsonGetDouble(response, "volume_lots", command_pos);
      double stop_loss = JsonGetDouble(response, "stop_loss", command_pos);
      double take_profit = JsonGetDouble(response, "take_profit", command_pos);
      long magic_number = (long)JsonGetDouble(response, "magic_number", command_pos);

      bool ok = false;
      string message = "";
      long position_ticket = (ticket_id == "" ? 0 : (long)StringToInteger(ticket_id));
      double fill_price = 0.0;
      double fill_volume = 0.0;

      if(symbol != "" && symbol != RuntimeSymbol)
      {
         PostAck(command_id, "ignored", 0, "Symbol mismatch", 0.0, 0.0);
         cursor = command_pos + 12;
         continue;
      }

      if(command_type == "place_entry")
         ok = ExecuteEntry(side, volume, stop_loss, take_profit, comment, magic_number, position_ticket, fill_price, fill_volume, message);
      else if(command_type == "modify_ticket")
         ok = ExecuteModify(position_ticket, stop_loss, take_profit, message);
      else if(command_type == "close_ticket")
         ok = ExecuteClose(position_ticket, volume, fill_price, fill_volume, message);
      else
         message = "Unsupported command_type";

      PostAck(command_id, ok ? "applied" : "rejected", position_ticket, ok ? reason : message, fill_price, fill_volume);
      cursor = command_pos + 12;
   }
}

bool ExecuteEntry(
   string side,
   double volume,
   double stop_loss,
   double take_profit,
   string comment,
   long magic_number,
   long &position_ticket,
   double &fill_price,
   double &fill_volume,
   string &message
)
{
   MqlTradeRequest request;
   MqlTradeResult result;
   ZeroMemory(request);
   ZeroMemory(result);

   request.action = TRADE_ACTION_DEAL;
   request.symbol = RuntimeSymbol;
   request.type = (side == "short" ? ORDER_TYPE_SELL : ORDER_TYPE_BUY);
   request.volume = volume;
   request.price = (side == "short" ? SymbolInfoDouble(RuntimeSymbol, SYMBOL_BID) : SymbolInfoDouble(RuntimeSymbol, SYMBOL_ASK));
   request.sl = stop_loss;
   request.tp = take_profit;
   request.magic = magic_number;
   request.comment = comment;
   request.type_filling = ResolveFillingMode(RuntimeSymbol);

   bool sent = OrderSend(request, result);
   message = result.comment;
   fill_price = result.price;
   fill_volume = result.volume;
   if(!(sent && (result.retcode == TRADE_RETCODE_DONE || result.retcode == TRADE_RETCODE_PLACED)))
      return(false);

   Sleep(150);
   position_ticket = ResolvePositionTicket(RuntimeSymbol, magic_number, comment);
   return(true);
}

bool ExecuteModify(long ticket, double stop_loss, double take_profit, string &message)
{
   if(ticket <= 0 || !PositionSelectByTicket((ulong)ticket))
   {
      message = "Position not found for modify";
      return(false);
   }

   MqlTradeRequest request;
   MqlTradeResult result;
   ZeroMemory(request);
   ZeroMemory(result);

   request.action = TRADE_ACTION_SLTP;
   request.position = (ulong)ticket;
   request.symbol = RuntimeSymbol;
   request.sl = stop_loss;
   request.tp = take_profit;

   bool sent = OrderSend(request, result);
   message = result.comment;
   return(sent && result.retcode == TRADE_RETCODE_DONE);
}

bool ExecuteClose(long ticket, double volume, double &fill_price, double &fill_volume, string &message)
{
   if(ticket <= 0 || !PositionSelectByTicket((ulong)ticket))
   {
      message = "Position not found for close";
      return(false);
   }

   ENUM_POSITION_TYPE position_type = (ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE);
   string symbol = PositionGetString(POSITION_SYMBOL);
   double full_volume = PositionGetDouble(POSITION_VOLUME);
   double close_volume = (volume > 0.0 && volume < full_volume ? volume : full_volume);

   MqlTradeRequest request;
   MqlTradeResult result;
   ZeroMemory(request);
   ZeroMemory(result);

   request.action = TRADE_ACTION_DEAL;
   request.position = (ulong)ticket;
   request.symbol = symbol;
   request.volume = close_volume;
   request.type = (position_type == POSITION_TYPE_BUY ? ORDER_TYPE_SELL : ORDER_TYPE_BUY);
   request.price = (position_type == POSITION_TYPE_BUY ? SymbolInfoDouble(symbol, SYMBOL_BID) : SymbolInfoDouble(symbol, SYMBOL_ASK));
   request.type_filling = ResolveFillingMode(symbol);

   bool sent = OrderSend(request, result);
   message = result.comment;
   fill_price = result.price;
   fill_volume = result.volume;
   return(sent && (result.retcode == TRADE_RETCODE_DONE || result.retcode == TRADE_RETCODE_PLACED));
}

long ResolvePositionTicket(string symbol, long magic_number, string comment)
{
   for(int index = 0; index < PositionsTotal(); index++)
   {
      ulong ticket = PositionGetTicket(index);
      if(!PositionSelectByTicket(ticket))
         continue;
      if(PositionGetString(POSITION_SYMBOL) != symbol)
         continue;
      if((long)PositionGetInteger(POSITION_MAGIC) != magic_number)
         continue;
      if(PositionGetString(POSITION_COMMENT) != comment)
         continue;
      return((long)ticket);
   }
   return(0);
}

void PostAck(string command_id, string status, long ticket_id, string message, double fill_price, double fill_volume)
{
   string payload = "{";
   payload += "\"command_id\":\"" + EscapeJson(command_id) + "\",";
   payload += "\"status\":\"" + EscapeJson(status) + "\",";
   payload += "\"broker_time\":\"" + FormatDateTime(TimeTradeServer()) + "\",";
   payload += "\"ticket_id\":\"" + LongValueToString(ticket_id) + "\",";
   payload += "\"message\":\"" + EscapeJson(message) + "\",";
   payload += "\"fill_price\":" + JsonDoubleOrNull(fill_price, _Digits) + ",";
   payload += "\"fill_volume_lots\":" + JsonDoubleOrNull(fill_volume, 2) + ",";
   payload += "\"payload\":{}";
   payload += "}";
   HttpRequest("POST", BridgeBaseUrl + "/bridge/acks", payload);
}

string BuildSnapshotJson()
{
   double bid = SymbolInfoDouble(RuntimeSymbol, SYMBOL_BID);
   double ask = SymbolInfoDouble(RuntimeSymbol, SYMBOL_ASK);
   if(bid <= 0.0 || ask <= 0.0)
      return("");

   string payload = "{";
   payload += "\"bridge_id\":\"mt5-v51-local\",";
   payload += "\"server_time\":\"" + FormatDateTime(TimeTradeServer()) + "\",";
   payload += "\"symbol\":\"" + EscapeJson(RuntimeSymbol) + "\",";
   payload += "\"bid\":" + DoubleToString(bid, _Digits) + ",";
   payload += "\"ask\":" + DoubleToString(ask, _Digits) + ",";
   payload += "\"spread_bps\":" + DoubleToString(SpreadBps(bid, ask), 4) + ",";
   payload += "\"symbol_spec\":" + BuildSymbolSpecJson() + ",";
   payload += "\"bars_1m\":" + BuildBarsJson(PERIOD_M1, BarsLookback1m, "1m") + ",";
   payload += "\"bars_5m\":" + BuildBarsJson(PERIOD_M5, BarsLookback5m, "5m") + ",";
   payload += "\"bars_15m\":" + BuildBarsJson(PERIOD_M15, BarsLookback15m, "15m") + ",";
   payload += "\"account\":" + BuildAccountJson() + ",";
   payload += "\"open_tickets\":" + BuildOpenTicketsJson() + ",";
   payload += "\"pending_command_ids\":[],";
   payload += "\"event_reasons\":[],";
   payload += "\"health\":{\"bridge_id\":\"mt5-v51-local\",\"connected\":true,\"pending_command_count\":0}";
   payload += "}";
   return(payload);
}

string BuildSymbolSpecJson()
{
   string payload = "{";
   payload += "\"digits\":" + IntegerToString((int)SymbolInfoInteger(RuntimeSymbol, SYMBOL_DIGITS)) + ",";
   payload += "\"point\":" + DoubleToString(SymbolInfoDouble(RuntimeSymbol, SYMBOL_POINT), 8) + ",";
   payload += "\"tick_size\":" + DoubleToString(SymbolInfoDouble(RuntimeSymbol, SYMBOL_TRADE_TICK_SIZE), 8) + ",";
   payload += "\"tick_value\":" + DoubleToString(SymbolInfoDouble(RuntimeSymbol, SYMBOL_TRADE_TICK_VALUE), 8) + ",";
   payload += "\"volume_min\":" + DoubleToString(SymbolInfoDouble(RuntimeSymbol, SYMBOL_VOLUME_MIN), 2) + ",";
   payload += "\"volume_step\":" + DoubleToString(SymbolInfoDouble(RuntimeSymbol, SYMBOL_VOLUME_STEP), 2) + ",";
   payload += "\"volume_max\":" + DoubleToString(SymbolInfoDouble(RuntimeSymbol, SYMBOL_VOLUME_MAX), 2) + ",";
   payload += "\"stops_level_points\":" + IntegerToString((int)SymbolInfoInteger(RuntimeSymbol, SYMBOL_TRADE_STOPS_LEVEL));
   payload += "}";
   return(payload);
}

string BuildBarsJson(ENUM_TIMEFRAMES timeframe, int lookback, string label)
{
   MqlRates rates[];
   ArraySetAsSeries(rates, true);
   int copied = CopyRates(RuntimeSymbol, timeframe, 0, lookback, rates);
   if(copied <= 0)
      return("[]");

   string payload = "[";
   for(int index = copied - 1; index >= 0; index--)
   {
      MqlRates rate = rates[index];
      payload += "{";
      payload += "\"timeframe\":\"" + label + "\",";
      payload += "\"start_at\":\"" + FormatDateTime(rate.time) + "\",";
      payload += "\"end_at\":\"" + FormatDateTime(rate.time + PeriodSeconds(timeframe)) + "\",";
      payload += "\"open_price\":" + DoubleToString(rate.open, _Digits) + ",";
      payload += "\"high_price\":" + DoubleToString(rate.high, _Digits) + ",";
      payload += "\"low_price\":" + DoubleToString(rate.low, _Digits) + ",";
      payload += "\"close_price\":" + DoubleToString(rate.close, _Digits) + ",";
      payload += "\"volume\":" + IntegerToString((int)rate.tick_volume) + ",";
      payload += "\"tick_volume\":" + IntegerToString((int)rate.tick_volume) + ",";
      payload += "\"spread_bps\":null,";
      payload += "\"complete\":true";
      payload += "}";
      if(index > 0)
         payload += ",";
   }
   payload += "]";
   return(payload);
}

string BuildAccountJson()
{
   string mode = "netting";
   long margin_mode = AccountInfoInteger(ACCOUNT_MARGIN_MODE);
   if(margin_mode == ACCOUNT_MARGIN_MODE_RETAIL_HEDGING)
      mode = "hedging";

   string payload = "{";
   payload += "\"login\":\"" + IntegerToString((int)AccountInfoInteger(ACCOUNT_LOGIN)) + "\",";
   payload += "\"balance\":" + DoubleToString(AccountInfoDouble(ACCOUNT_BALANCE), 2) + ",";
   payload += "\"equity\":" + DoubleToString(AccountInfoDouble(ACCOUNT_EQUITY), 2) + ",";
   payload += "\"free_margin\":" + DoubleToString(AccountInfoDouble(ACCOUNT_MARGIN_FREE), 2) + ",";
   payload += "\"margin\":" + DoubleToString(AccountInfoDouble(ACCOUNT_MARGIN), 2) + ",";
   payload += "\"margin_level\":" + DoubleToString(AccountInfoDouble(ACCOUNT_MARGIN_LEVEL), 2) + ",";
   payload += "\"currency\":\"" + EscapeJson(AccountInfoString(ACCOUNT_CURRENCY)) + "\",";
   payload += "\"leverage\":" + IntegerToString((int)AccountInfoInteger(ACCOUNT_LEVERAGE)) + ",";
   payload += "\"demo\":" + (AccountInfoInteger(ACCOUNT_TRADE_MODE) == ACCOUNT_TRADE_MODE_DEMO ? "true" : "false") + ",";
   payload += "\"account_mode\":\"" + mode + "\",";
   payload += "\"trade_allowed\":" + (AccountInfoInteger(ACCOUNT_TRADE_ALLOWED) > 0 ? "true" : "false") + ",";
   payload += "\"open_profit\":" + DoubleToString(AccountInfoDouble(ACCOUNT_PROFIT), 2) + ",";
   payload += "\"broker\":\"" + EscapeJson(AccountInfoString(ACCOUNT_COMPANY)) + "\"";
   payload += "}";
   return(payload);
}

string BuildOpenTicketsJson()
{
   string payload = "[";
   int written = 0;
   for(int index = 0; index < PositionsTotal(); index++)
   {
      ulong ticket = PositionGetTicket(index);
      if(!PositionSelectByTicket(ticket))
         continue;

      string symbol = PositionGetString(POSITION_SYMBOL);
      if(symbol != RuntimeSymbol)
         continue;

      if(written > 0)
         payload += ",";

      ENUM_POSITION_TYPE side = (ENUM_POSITION_TYPE)PositionGetInteger(POSITION_TYPE);
      double volume = PositionGetDouble(POSITION_VOLUME);
      double open_price = PositionGetDouble(POSITION_PRICE_OPEN);
      double current_price = PositionGetDouble(POSITION_PRICE_CURRENT);
      double stop_loss = PositionGetDouble(POSITION_SL);
      double take_profit = PositionGetDouble(POSITION_TP);
      double profit = PositionGetDouble(POSITION_PROFIT);
      string comment = PositionGetString(POSITION_COMMENT);

      payload += "{";
      payload += "\"ticket_id\":\"" + LongValueToString((long)ticket) + "\",";
      payload += "\"symbol\":\"" + EscapeJson(symbol) + "\",";
      payload += "\"side\":\"" + (side == POSITION_TYPE_BUY ? "long" : "short") + "\",";
      payload += "\"volume_lots\":" + DoubleToString(volume, 2) + ",";
      payload += "\"open_price\":" + DoubleToString(open_price, _Digits) + ",";
      payload += "\"current_price\":" + DoubleToString(current_price, _Digits) + ",";
      payload += "\"stop_loss\":" + JsonDoubleOrNull(stop_loss, _Digits) + ",";
      payload += "\"take_profit\":" + JsonDoubleOrNull(take_profit, _Digits) + ",";
      payload += "\"unrealized_pnl_usd\":" + DoubleToString(profit, 2) + ",";
      payload += "\"protected\":" + (IsProtected(side, open_price, stop_loss) ? "true" : "false") + ",";
      payload += "\"opened_at\":\"" + FormatDateTime((datetime)PositionGetInteger(POSITION_TIME)) + "\",";
      payload += "\"magic_number\":" + IntegerToString((int)PositionGetInteger(POSITION_MAGIC)) + ",";
      payload += "\"comment\":\"" + EscapeJson(comment) + "\",";
      payload += "\"basket_id\":\"" + EscapeJson(ExtractBasketId(comment)) + "\",";
      payload += "\"metadata\":{}";
      payload += "}";
      written++;
   }
   payload += "]";
   return(payload);
}

bool IsProtected(ENUM_POSITION_TYPE side, double open_price, double stop_loss)
{
   if(stop_loss <= 0.0)
      return(false);
   if(side == POSITION_TYPE_BUY)
      return(stop_loss >= open_price);
   return(stop_loss <= open_price);
}

string ExtractBasketId(string comment)
{
   int first = StringFind(comment, "|");
   if(first < 0)
      return("");
   int second = StringFind(comment, "|", first + 1);
   if(second < 0)
      return("");
   return(StringSubstr(comment, first + 1, second - first - 1));
}

double SpreadBps(double bid, double ask)
{
   double midpoint = (bid + ask) / 2.0;
   if(midpoint <= 0.0)
      return(0.0);
   return(((ask - bid) / midpoint) * 10000.0);
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

string FormatDateTime(datetime value)
{
   MqlDateTime parts;
   TimeToStruct(value, parts);
   return(
      StringFormat(
         "%04d-%02d-%02dT%02d:%02d:%02d",
         parts.year,
         parts.mon,
         parts.day,
         parts.hour,
         parts.min,
         parts.sec
      )
   );
}

string JsonDoubleOrNull(double value, int digits)
{
   if(value <= 0.0)
      return("null");
   return(DoubleToString(value, digits));
}

string LongValueToString(long value)
{
   return(StringFormat("%I64d", value));
}

string HttpRequest(string method, string url, string body)
{
   uchar request_body[];
   uchar response_body[];
   string response_headers;
   string request_headers = "Content-Type: application/json\r\n";
   int request_size = StringToCharArray(body, request_body, 0, WHOLE_ARRAY, CP_UTF8);
   if(request_size > 0 && request_body[request_size - 1] == 0)
      request_size--;
   ArrayResize(request_body, MathMax(request_size, 0));

   int code = WebRequest(method, url, request_headers, HttpTimeoutMs, request_body, response_body, response_headers);
   if(code < 200 || code >= 300)
   {
      Print("V51BridgeEA: WebRequest failed. code=", code, " url=", url);
      return("");
   }
   return(CharArrayToString(response_body, 0, -1, CP_UTF8));
}

string JsonGetString(string source, string key, int from)
{
   string token = "\"" + key + "\":\"";
   int start = StringFind(source, token, from);
   if(start < 0)
      return("");
   start += StringLen(token);
   int end = StringFind(source, "\"", start);
   if(end < 0)
      return("");
   return(StringSubstr(source, start, end - start));
}

double JsonGetDouble(string source, string key, int from)
{
   string token = "\"" + key + "\":";
   int start = StringFind(source, token, from);
   if(start < 0)
      return(0.0);
   start += StringLen(token);
   int end = start;
   while(end < StringLen(source))
   {
      ushort ch = StringGetCharacter(source, end);
      if(ch == ',' || ch == '}' || ch == ']')
         break;
      end++;
   }
   string raw = StringSubstr(source, start, end - start);
   StringReplace(raw, "\"", "");
   return(StringToDouble(raw));
}

string EscapeJson(string value)
{
   string escaped = value;
   StringReplace(escaped, "\\", "\\\\");
   StringReplace(escaped, "\"", "\\\"");
   return(escaped);
}
