//+------------------------------------------------------------------+
//|  AlgoBotBias.mqh — Read algo-bot morning bias from JSON file     |
//|                                                                  |
//|  The Python morning pipeline writes /tmp/algo-bot-bias.json      |
//|  (or BIAS_FILE_PATH) after each run. Copy or symlink that file   |
//|  into MT5's Common Files folder so the EA can read it.           |
//|                                                                  |
//|  MT5 Common Files folder (Windows):                              |
//|    %APPDATA%\MetaQuotes\Terminal\Common\Files\                   |
//|                                                                  |
//|  File format (UTF-8 JSON):                                       |
//|    {                                                             |
//|      "direction":  "bullish" | "bearish" | "neutral",           |
//|      "confidence": 0.0 – 1.0,                                   |
//|      "reasoning":  "...",                                        |
//|      "timestamp":  "2026-06-15T09:45:00+00:00"                  |
//|    }                                                             |
//|                                                                  |
//|  Usage:                                                          |
//|    #include <AlgoBotBias.mqh>                                    |
//|    string dir; double conf;                                      |
//|    if(!ReadAlgoBias("algo-bot-bias.json", dir, conf)) return;    |
//+------------------------------------------------------------------+
#ifndef ALGOBOT_BIAS_MQH
#define ALGOBOT_BIAS_MQH

#define BIAS_MAX_AGE_SECONDS 28800   // 8 hours — matches Python BIAS_MAX_AGE_HOURS

//--- Extract a string value from a JSON line: "key": "value"
string ExtractJsonString(const string line, const string key)
  {
   string search = "\"" + key + "\": \"";
   int pos = StringFind(line, search);
   if(pos < 0) return "";
   int start = pos + StringLen(search);
   int end   = StringFind(line, "\"", start);
   if(end < 0) return "";
   return StringSubstr(line, start, end - start);
  }

//--- Extract a numeric value from a JSON line: "key": 0.1234
double ExtractJsonDouble(const string line, const string key)
  {
   string search = "\"" + key + "\": ";
   int pos = StringFind(line, search);
   if(pos < 0) return -1.0;
   int start = pos + StringLen(search);
   // read until comma, newline, or closing brace
   string rest = StringSubstr(line, start, 20);
   return StringToDouble(rest);
  }

//--- Parse ISO 8601 UTC timestamp into a datetime (seconds since 1970 epoch)
//    Handles "2026-06-15T09:45:00+00:00" and "2026-06-15T09:45:00.123456+00:00"
datetime ParseISOTimestamp(const string ts)
  {
   if(StringLen(ts) < 19) return 0;
   // Slice: "2026-06-15T09:45:00"
   int yr  = (int)StringToInteger(StringSubstr(ts, 0, 4));
   int mo  = (int)StringToInteger(StringSubstr(ts, 5, 2));
   int day = (int)StringToInteger(StringSubstr(ts, 8, 2));
   int hr  = (int)StringToInteger(StringSubstr(ts, 11, 2));
   int min = (int)StringToInteger(StringSubstr(ts, 14, 2));
   int sec = (int)StringToInteger(StringSubstr(ts, 17, 2));

   MqlDateTime dt;
   dt.year  = yr; dt.mon  = mo; dt.day  = day;
   dt.hour  = hr; dt.min  = min; dt.sec = sec;
   dt.day_of_week = 0; dt.day_of_year = 0;
   return StructToTime(dt);
  }

//+------------------------------------------------------------------+
//| ReadAlgoBias                                                      |
//|                                                                   |
//| Reads the bias file from MT5 Common Files folder.                 |
//|                                                                   |
//| Parameters:                                                       |
//|   filename   — filename only, e.g. "algo-bot-bias.json"          |
//|   direction  — out: "bullish", "bearish", or "neutral"           |
//|   confidence — out: 0.0 – 1.0                                    |
//|                                                                   |
//| Returns true if file is present, fresh, and direction != neutral. |
//| Returns false (and caller should skip the trade) if:             |
//|   - file not found                                               |
//|   - timestamp missing or older than BIAS_MAX_AGE_SECONDS         |
//|   - direction is "neutral"                                        |
//+------------------------------------------------------------------+
bool ReadAlgoBias(const string filename, string &direction, double &confidence)
  {
   direction  = "neutral";
   confidence = 0.0;

   int fh = FileOpen(filename, FILE_READ|FILE_TXT|FILE_COMMON|FILE_ANSI);
   if(fh == INVALID_HANDLE)
     {
      Print("AlgoBotBias: file not found in Common Files: ", filename);
      return false;
     }

   string dir_val  = "";
   double conf_val = 0.0;
   string ts_val   = "";

   while(!FileIsEnding(fh))
     {
      string line = FileReadString(fh);

      if(dir_val  == "")  dir_val  = ExtractJsonString(line, "direction");
      if(ts_val   == "")  ts_val   = ExtractJsonString(line, "timestamp");
      if(conf_val == 0.0) conf_val = ExtractJsonDouble(line, "confidence");
     }
   FileClose(fh);

   // Validate timestamp freshness
   if(ts_val == "")
     {
      Print("AlgoBotBias: missing timestamp in ", filename);
      return false;
     }

   datetime written_at = ParseISOTimestamp(ts_val);
   datetime now_utc    = TimeGMT();
   long age_seconds    = (long)(now_utc - written_at);

   if(age_seconds < 0 || age_seconds > BIAS_MAX_AGE_SECONDS)
     {
      Print("AlgoBotBias: stale bias (age=", age_seconds, "s). Skipping trade.");
      return false;
     }

   if(dir_val == "" || dir_val == "neutral")
     {
      Print("AlgoBotBias: direction=", (dir_val == "" ? "missing" : "neutral"), ". Skipping trade.");
      return false;
     }

   direction  = dir_val;
   confidence = conf_val;
   Print("AlgoBotBias: direction=", direction, " confidence=", DoubleToString(confidence, 4),
         " age=", age_seconds, "s");
   return true;
  }

#endif // ALGOBOT_BIAS_MQH
//+------------------------------------------------------------------+
