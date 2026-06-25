//+------------------------------------------------------------------+
//|                                          BB_Scalper_EA.mq5       |
//|                  Bollinger Bands + Volume + Session Filter       |
//|                              Optimized Parameters v4.01          |
//+------------------------------------------------------------------+
#property copyright "madanw"
#property version   "4.01"

#include <Trade\Trade.mqh>
#include <SentinelBias.mqh>

//--- Input Parameters (Optimized from 3,625 pass optimization)
input int      BB_Period       = 15;       // Bollinger Bands Period
input double   BB_Deviation    = 2.4;      // Bollinger Bands Deviation
input int      Vol_MA_Period   = 20;       // Volume MA Period
input double   Vol_Multiplier  = 2.0;      // Volume must be X times above average
input double   LotSize         = 0.01;     // Lot Size
input int      StopLoss        = 150;      // Stop Loss in points
input int      TakeProfit      = 150;      // Take Profit in points
input int      MagicNumber     = 54321;    // Unique EA identifier

//--- Session Filter Inputs (GMT)
input int      London_Start    = 8;        // London Open Hour (GMT)
input int      London_End      = 12;       // London/NY Overlap End Hour (GMT)
input int      NY_Start        = 13;       // NY Session Hour (GMT)
input int      NY_End          = 17;       // NY Session End Hour (GMT)

//--- Bias bridge
input string   BiasFile        = "sentinel-bias.json";  // Bias file name in MT5 Common Files
input double   MinBiasConfidence = 0.60;                // Min confidence to trade

//--- Global Variables
CTrade trade;
int bbHandle;
int volMAHandle;

//+------------------------------------------------------------------+
//| Session Filter Function                                           |
//+------------------------------------------------------------------+
bool IsValidSession()
  {
   MqlDateTime gmtStruct;
   TimeToStruct(TimeGMT(), gmtStruct);
   int hour = gmtStruct.hour;

   bool londonSession = (hour >= London_Start && hour < London_End);
   bool nySession     = (hour >= NY_Start     && hour < NY_End);

   return (londonSession || nySession);
  }

//+------------------------------------------------------------------+
//| Expert initialization function                                    |
//+------------------------------------------------------------------+
int OnInit()
  {
   bbHandle    = iBands(_Symbol, _Period, BB_Period, 0, BB_Deviation, PRICE_CLOSE);
   volMAHandle = iMA(_Symbol, _Period, Vol_MA_Period, 0, MODE_SMA, VOLUME_TICK);

   if(bbHandle == INVALID_HANDLE || volMAHandle == INVALID_HANDLE)
     {
      Print("Error creating indicators!");
      return INIT_FAILED;
     }

   trade.SetExpertMagicNumber(MagicNumber);
   Print("BB Scalper EA v4.01 | BB:", BB_Period, " Dev:", BB_Deviation,
         " Vol:", Vol_Multiplier, " SL:", StopLoss, " TP:", TakeProfit,
         " | BiasFile:", BiasFile);
   return INIT_SUCCEEDED;
  }

//+------------------------------------------------------------------+
//| Expert deinitialization function                                  |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
  {
   IndicatorRelease(bbHandle);
   IndicatorRelease(volMAHandle);
  }

//+------------------------------------------------------------------+
//| Expert tick function                                              |
//+------------------------------------------------------------------+
void OnTick()
  {
   if(!IsNewBar())       return;
   if(!IsValidSession()) return;

   // --- Bias gate: read sentinel morning bias before any trade ---
   string biasDir; double biasConf;
   if(!ReadAlgoBias(BiasFile, biasDir, biasConf)) return;
   if(biasConf < MinBiasConfidence)
     {
      Print("BB Scalper: bias confidence ", DoubleToString(biasConf, 4),
            " < ", DoubleToString(MinBiasConfidence, 2), " — skip.");
      return;
     }

   // Get Bollinger Bands values
   double bbUpper[], bbLower[], bbMiddle[];
   ArraySetAsSeries(bbUpper,  true);
   ArraySetAsSeries(bbLower,  true);
   ArraySetAsSeries(bbMiddle, true);

   if(CopyBuffer(bbHandle, 1, 0, 3, bbUpper)  < 3) return;
   if(CopyBuffer(bbHandle, 2, 0, 3, bbLower)  < 3) return;
   if(CopyBuffer(bbHandle, 0, 0, 3, bbMiddle) < 3) return;

   // Get volume and volume MA
   double volMA[];
   long   currentVol[];
   ArraySetAsSeries(volMA,      true);
   ArraySetAsSeries(currentVol, true);

   if(CopyBuffer(volMAHandle, 0, 0, 3, volMA) < 3) return;
   if(CopyTickVolume(_Symbol, _Period, 0, 3, currentVol) < 3) return;

   // Get close prices
   double close[];
   ArraySetAsSeries(close, true);
   if(CopyClose(_Symbol, _Period, 0, 3, close) < 3) return;

   // Volume confirmation
   bool volumeConfirmed = (currentVol[1] >= volMA[1] * Vol_Multiplier);

   // Price touching bands
   bool touchedLower = (close[1] <= bbLower[1]);
   bool touchedUpper = (close[1] >= bbUpper[1]);

   // Signals — bias gate narrows direction
   bool buySignal  = touchedLower && volumeConfirmed && (biasDir == "bullish");
   bool sellSignal = touchedUpper && volumeConfirmed && (biasDir == "bearish");

   int openPositions = CountPositions();

   // BUY
   if(buySignal && openPositions == 0)
     {
      double sl  = _Point * StopLoss;
      double tp  = _Point * TakeProfit;
      double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
      trade.Buy(LotSize, _Symbol, ask, ask - sl, ask + tp, "BB v4 Buy");
      Print("BUY | Close: ", close[1], " | Lower BB: ", bbLower[1],
            " | Vol: ", currentVol[1], " | Vol MA: ", volMA[1],
            " | Bias: ", biasDir, " (", DoubleToString(biasConf, 2), ")");
     }

   // SELL
   if(sellSignal && openPositions == 0)
     {
      double sl  = _Point * StopLoss;
      double tp  = _Point * TakeProfit;
      double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
      trade.Sell(LotSize, _Symbol, bid, bid + sl, bid - tp, "BB v4 Sell");
      Print("SELL | Close: ", close[1], " | Upper BB: ", bbUpper[1],
            " | Vol: ", currentVol[1], " | Vol MA: ", volMA[1],
            " | Bias: ", biasDir, " (", DoubleToString(biasConf, 2), ")");
     }
  }

//+------------------------------------------------------------------+
//| Check if a new bar has opened                                     |
//+------------------------------------------------------------------+
bool IsNewBar()
  {
   static datetime lastBarTime = 0;
   datetime currentBarTime = iTime(_Symbol, _Period, 0);
   if(currentBarTime != lastBarTime)
     {
      lastBarTime = currentBarTime;
      return true;
     }
   return false;
  }

//+------------------------------------------------------------------+
//| Count open positions for this EA                                  |
//+------------------------------------------------------------------+
int CountPositions()
  {
   int count = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      if(PositionGetSymbol(i) == _Symbol &&
         PositionGetInteger(POSITION_MAGIC) == MagicNumber)
         count++;
     }
   return count;
  }
//+------------------------------------------------------------------+
