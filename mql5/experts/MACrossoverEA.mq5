//+------------------------------------------------------------------+
//|                                            MA_Crossover_EA.mq5   |
//|                          MA Crossover + ADX Filter - USD/JPY     |
//+------------------------------------------------------------------+
#property copyright "Madanw"
#property version   "2.01"

#include <Trade\Trade.mqh>
#include <AlgoBotBias.mqh>

//--- Input Parameters
input int      FastMA_Period     = 9;          // Fast MA Period
input int      SlowMA_Period     = 21;         // Slow MA Period
input int      ADX_Period        = 14;         // ADX Period
input double   ADX_Minimum       = 25.0;       // Minimum ADX strength
input double   LotSize           = 0.01;       // Lot Size
input int      StopLoss          = 200;        // Stop Loss in points
input int      TakeProfit        = 400;        // Take Profit in points
input int      MagicNumber       = 12345;      // Unique EA identifier

//--- Bias bridge
input string   BiasFile          = "algo-bot-bias.json";  // Bias file name in MT5 Common Files
input double   MinBiasConfidence = 0.60;                  // Min confidence to trade

//--- Global Variables
CTrade trade;
int fastMAHandle;
int slowMAHandle;
int adxHandle;

//+------------------------------------------------------------------+
//| Expert initialization function                                     |
//+------------------------------------------------------------------+
int OnInit()
  {
   fastMAHandle = iMA(_Symbol, _Period, FastMA_Period, 0, MODE_EMA, PRICE_CLOSE);
   slowMAHandle = iMA(_Symbol, _Period, SlowMA_Period, 0, MODE_EMA, PRICE_CLOSE);
   adxHandle    = iADX(_Symbol, _Period, ADX_Period);

   if(fastMAHandle == INVALID_HANDLE ||
      slowMAHandle == INVALID_HANDLE ||
      adxHandle    == INVALID_HANDLE)
     {
      Print("Error creating indicators!");
      return INIT_FAILED;
     }

   trade.SetExpertMagicNumber(MagicNumber);
   Print("MA Crossover EA v2.01 | Fast:", FastMA_Period, " Slow:", SlowMA_Period,
         " ADX:", ADX_Period, " | BiasFile:", BiasFile);
   return INIT_SUCCEEDED;
  }

//+------------------------------------------------------------------+
//| Expert deinitialization function                                   |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
  {
   IndicatorRelease(fastMAHandle);
   IndicatorRelease(slowMAHandle);
   IndicatorRelease(adxHandle);
  }

//+------------------------------------------------------------------+
//| Expert tick function                                               |
//+------------------------------------------------------------------+
void OnTick()
  {
   if(!IsNewBar()) return;

   // --- Bias gate: read algo-bot morning bias before any trade ---
   string biasDir; double biasConf;
   if(!ReadAlgoBias(BiasFile, biasDir, biasConf)) return;
   if(biasConf < MinBiasConfidence)
     {
      Print("MA Crossover: bias confidence ", DoubleToString(biasConf, 4),
            " < ", DoubleToString(MinBiasConfidence, 2), " — skip.");
      return;
     }

   // Get MA values
   double fastMA[], slowMA[];
   ArraySetAsSeries(fastMA, true);
   ArraySetAsSeries(slowMA, true);

   if(CopyBuffer(fastMAHandle, 0, 0, 3, fastMA) < 3) return;
   if(CopyBuffer(slowMAHandle, 0, 0, 3, slowMA) < 3) return;

   // Get ADX value
   double adxVal[];
   ArraySetAsSeries(adxVal, true);
   if(CopyBuffer(adxHandle, 0, 0, 3, adxVal) < 3) return;

   // ADX filter — only trade in strong trends
   bool trendIsStrong = (adxVal[1] >= ADX_Minimum);

   // Crossover signals — bias gate narrows direction
   bool bullishCross = (fastMA[1] > slowMA[1]) && (fastMA[2] <= slowMA[2]) && (biasDir == "bullish");
   bool bearishCross = (fastMA[1] < slowMA[1]) && (fastMA[2] >= slowMA[2]) && (biasDir == "bearish");

   int openPositions = CountPositions();

   // BUY — crossover + strong trend + bullish bias
   if(bullishCross && trendIsStrong && openPositions == 0)
     {
      double sl  = _Point * StopLoss;
      double tp  = _Point * TakeProfit;
      double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
      trade.Buy(LotSize, _Symbol, ask, ask - sl, ask + tp, "MA+ADX Buy");
      Print("BUY | ADX: ", adxVal[1], " | Fast MA: ", fastMA[1], " | Slow MA: ", slowMA[1],
            " | Bias: ", biasDir, " (", DoubleToString(biasConf, 2), ")");
     }

   // SELL — crossover + strong trend + bearish bias
   if(bearishCross && trendIsStrong && openPositions == 0)
     {
      double sl  = _Point * StopLoss;
      double tp  = _Point * TakeProfit;
      double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
      trade.Sell(LotSize, _Symbol, bid, bid + sl, bid - tp, "MA+ADX Sell");
      Print("SELL | ADX: ", adxVal[1], " | Fast MA: ", fastMA[1], " | Slow MA: ", slowMA[1],
            " | Bias: ", biasDir, " (", DoubleToString(biasConf, 2), ")");
     }
  }

//+------------------------------------------------------------------+
//| Check if a new bar has opened                                      |
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
//| Count open positions for this EA                                   |
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
