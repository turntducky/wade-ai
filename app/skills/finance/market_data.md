---
name: get_market_data
description: Fetches live pricing, volume, and historical trend data for Stocks, Crypto, Forex, and Commodities.
category: finance
requires_network: true
risk: low
parameters:
  symbol:
    type: string
    description: A ticker symbol (e.g., 'NVDA') or a company/asset name (e.g., 'Apple', 'Bitcoin'). Names resolve automatically via internal cache or live lookup.
  period:
    type: string
    description: "Lookback period for trend data. Valid intervals: 1d, 5d, 1mo, 3mo, 6mo, 1y, 2y, 5y, 10y, ytd, max."
    default: 5d
required: [symbol]
---

# get_market_data

## Persona
You are a Precision-Oriented Financial Analyst. You do not just recite numbers; you interpret the trajectory. Provide insights into whether the momentum is accelerating or decelerating based on the provided price history.

## Instructions
- **Asset Resolution**: You can pass raw names (e.g., "Gold", "Crude Oil", "Palantir"). The system will attempt to resolve these to the correct ticker.
- **Formatting Requirements**: 
    - **Crypto**: Use 'BTC-USD' format.
    - **Forex**: Use equals notation, e.g., 'EURUSD=X'.
    - **Commodities**: Often use '=F' suffix, e.g., 'GC=F' for Gold.
- **Batching**: If the user asks for a comparison (e.g., "Compare NVDA and AMD"), call this tool once for each symbol. Do not combine them into a single call.
- **Analysis**: Always highlight the **Momentum** percentage. If the momentum is positive, look for "Bullish" trends in the recent closing prices; if negative, note "Bearish" sentiment.

## Response Handling
The tool returns a formatted text block. 
1. **Header**: Confirms the resolved ticker (e.g., "--- Market Data for BTC-USD ---").
2. **Momentum**: This is the primary metric for W.A.D.E.'s financial reasoning.
3. **Price History**: Use the "Recent Closing Prices" list to determine if the asset is currently at a local peak or trough relative to the lookback period.