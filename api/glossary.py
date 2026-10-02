"""Plain-language help for people who know code but not markets.

Served at /v1/glossary and /v1/markets, and used by the web UI for its explanations.
"""

GLOSSARY = {
    "symbol": {
        "title": "Symbol",
        "plain": "A short code that identifies one thing you can trade, written EXCHANGE:TICKER. NSE:RELIANCE is Reliance Industries on India's NSE exchange. Search by name with /v1/symbols/search.",
    },
    "exchange": {
        "title": "Exchange",
        "plain": "The marketplace where something trades. India has NSE and BSE; the US has NASDAQ and NYSE. The same company can trade on more than one exchange.",
    },
    "price": {
        "title": "Price",
        "plain": "The most recent price one share (or one coin) traded at. When the market is closed it is the last price of the day.",
    },
    "change": {
        "title": "Change today",
        "plain": "How far the price has moved since the previous day's close, as an amount and as a percentage. Up is green, down is red.",
    },
    "volume": {
        "title": "Volume",
        "plain": "How many shares changed hands today. A big number means many people are trading it; a tiny number means it is hard to buy or sell without moving the price.",
    },
    "value_traded": {
        "title": "Value traded",
        "plain": "The total money that changed hands today (shares traded multiplied by price).",
    },
    "market_cap": {
        "title": "Market cap",
        "plain": "The total value of all of a company's shares: price times number of shares. A quick measure of how big a company is. Shown in the listing currency; for India that is rupees, written in lakh and crore.",
    },
    "pe": {
        "title": "P/E ratio",
        "plain": "Price divided by the company's yearly profit per share. It shows how many rupees (or dollars) investors pay for one rupee of yearly profit. A high number means high expectations. Only compare companies in the same line of business. Empty means the company has no profit to divide by.",
    },
    "eps": {
        "title": "EPS (earnings per share)",
        "plain": "The company's profit over the last 12 months divided by its number of shares.",
    },
    "range_52w": {
        "title": "52-week high and low",
        "plain": "The highest and lowest prices in the past year. Where today's price sits between them shows whether it is near a yearly peak or a yearly bottom.",
    },
    "day_range": {
        "title": "Day's range",
        "plain": "The lowest and highest prices so far today.",
    },
    "open_high_low_close": {
        "title": "Open, high, low, close",
        "plain": "The first price of the day (open), the highest (high), the lowest (low) and the latest or final one (close).",
    },
    "candle": {
        "title": "Candle",
        "plain": "One bar on a price chart that summarises a stretch of time (a minute, a day, a week). It records the open, high, low and close prices. Green means the price ended higher than it started; red means lower.",
    },
    "timeframe": {
        "title": "Timeframe",
        "plain": "How much time each candle covers: 1m is one minute, 1h one hour, 1d one day, 1w one week, 1M one month.",
    },
    "delayed": {
        "title": "Delayed prices",
        "plain": "Free market data is usually shown 15 minutes late because exchanges charge for real-time feeds. Every price here says whether it is real time or delayed, so never present a delayed price as live.",
    },
    "realtime": {
        "title": "Real-time prices",
        "plain": "Prices that update the moment trades happen. Crypto exchanges give this for free; stock exchanges usually do not.",
    },
    "session": {
        "title": "Market session",
        "plain": "Whether the exchange is open. Outside trading hours the price stays at the last close.",
    },
    "currency": {
        "title": "Currency",
        "plain": "Prices and company sizes are in the currency of the exchange: rupees for NSE and BSE, dollars for NASDAQ and NYSE. Nothing is converted for you.",
    },
    "lakh_crore": {
        "title": "Lakh and crore",
        "plain": "Indian number units. 1 lakh = 1,00,000 (a hundred thousand). 1 crore = 1,00,00,000 (ten million). 1 lakh crore = one trillion.",
    },
    "gainers": {
        "title": "Gainers",
        "plain": "Stocks whose price rose the most today. Only liquid stocks on the main exchange are listed, so tiny or hard-to-trade ones do not crowd the list.",
    },
    "losers": {
        "title": "Losers",
        "plain": "Stocks whose price fell the most today.",
    },
    "most_active": {
        "title": "Most active",
        "plain": "Stocks with the most shares traded today. Busy stocks are usually the ones in the news.",
    },
    "penny_stocks": {
        "title": "Penny stocks",
        "plain": "Very low-priced stocks (under 20 rupees in India, 5 dollars in the US). They can move fast and are often risky and thinly traded.",
    },
    "screener": {
        "title": "Screener",
        "plain": "A filter for the whole market. You set rules, such as price under 500 and rising today, and it returns every stock that matches.",
    },
    "dividend": {
        "title": "Dividend",
        "plain": "A share of profit a company pays to its shareholders. Dividend yield is the yearly dividend as a percentage of the price.",
    },
    "beta": {
        "title": "Beta",
        "plain": "How strongly a stock moves compared with the overall market. Around 1 moves with the market; above 1 swings more; below 1 swings less.",
    },
    "volatility": {
        "title": "Volatility",
        "plain": "How much the price typically moves up and down. Higher means a bumpier ride.",
    },
    "performance": {
        "title": "Performance",
        "plain": "How much the price has changed over a past period, such as a week, three months or a year.",
    },
    "sector": {
        "title": "Sector and industry",
        "plain": "The line of business the company is in, for example Energy or Banking.",
    },
    "bid_ask": {
        "title": "Bid and ask",
        "plain": "The highest price a buyer is offering (bid) and the lowest price a seller wants (ask). Often empty when the market is closed or for delayed data.",
    },
    "earnings": {
        "title": "Earnings",
        "plain": "A company's reported profit for a quarter. The earnings calendar lists when companies will announce them; prices often move sharply on the day.",
    },
}

# Which glossary entry explains which data field (used by the UI and the docs).
FIELD_TERMS = {
    "close": "price", "price": "price", "change": "change", "change_abs": "change", "change_percent": "change",
    "change_from_open": "change", "volume": "volume", "Value.Traded": "value_traded",
    "market_cap_basic": "market_cap", "market_cap_calc": "market_cap", "market_cap_diluted_calc": "market_cap",
    "market_cap": "market_cap", "price_earnings_ttm": "pe", "earnings_per_share_basic_ttm": "eps",
    "earnings_per_share_diluted_ttm": "eps", "price_52_week_high": "range_52w", "price_52_week_low": "range_52w",
    "high_52w": "range_52w", "low_52w": "range_52w", "open": "open_high_low_close", "high": "open_high_low_close",
    "low": "open_high_low_close", "prev_close": "open_high_low_close", "dividends_yield": "dividend",
    "beta_1_year": "beta", "bid": "bid_ask", "ask": "bid_ask", "sector": "sector", "industry": "sector",
    "currency": "currency", "update_mode": "delayed", "delay_seconds": "delayed", "realtime": "realtime",
    "Volatility.D": "volatility", "Volatility.W": "volatility", "Volatility.M": "volatility",
    "Perf.W": "performance", "Perf.1M": "performance", "Perf.3M": "performance", "Perf.6M": "performance",
    "Perf.Y": "performance", "Perf.YTD": "performance", "session": "session",
}

CATALOG = {
    "markets": [
        {"id": "stocks-india", "name": "India (NSE)", "currency": "INR", "exchanges": ["NSE"], "screener_market": "india",
         "data": "Stocks are delayed about 15 minutes.", "categories": ["gainers", "losers", "most-active", "penny-stocks"]},
        {"id": "stocks-usa", "name": "United States", "currency": "USD", "exchanges": ["NASDAQ", "NYSE", "AMEX"], "screener_market": "america",
         "data": "Stocks are delayed about 15 minutes.",
         "categories": ["gainers", "losers", "most-active", "penny-stocks", "pre-market-gainers", "pre-market-losers", "after-hours-gainers", "after-hours-losers"]},
        {"id": "stocks-uk", "name": "United Kingdom", "currency": "GBP", "exchanges": ["LSE"], "screener_market": "uk",
         "data": "Prices may be in pence for some stocks.", "categories": ["gainers", "losers", "most-active", "penny-stocks"]},
        {"id": "stocks-canada", "name": "Canada", "currency": "CAD", "exchanges": ["TSX"], "screener_market": "canada",
         "data": "Stocks are delayed.", "categories": ["gainers", "losers", "most-active", "penny-stocks"]},
        {"id": "stocks-australia", "name": "Australia", "currency": "AUD", "exchanges": ["ASX"], "screener_market": "australia",
         "data": "Stocks are delayed.", "categories": ["gainers", "losers", "most-active", "penny-stocks"]},
        {"id": "crypto", "name": "Crypto", "currency": "USDT/USD", "exchanges": ["BINANCE", "COINBASE", "BYBIT", "KRAKEN", "OKX"], "screener_market": "crypto",
         "data": "Real time, trades around the clock.", "categories": ["gainers", "losers", "most-active"]},
        {"id": "forex", "name": "Forex", "currency": "varies", "exchanges": [], "screener_market": "forex",
         "data": "Currency pairs, real time on weekdays.", "categories": ["gainers", "losers", "most-active"]},
    ],
    "categories": {
        "gainers": "Rose the most today", "losers": "Fell the most today", "most-active": "Most shares traded today",
        "penny-stocks": "Very low-priced stocks", "pre-market-gainers": "Rising before the market opens (US)",
        "pre-market-losers": "Falling before the market opens (US)", "after-hours-gainers": "Rising after the market closes (US)",
        "after-hours-losers": "Falling after the market closes (US)",
    },
    "timeframes": [
        {"id": "1m", "name": "1 minute"}, {"id": "5m", "name": "5 minutes"}, {"id": "15m", "name": "15 minutes"},
        {"id": "30m", "name": "30 minutes"}, {"id": "1h", "name": "1 hour"}, {"id": "2h", "name": "2 hours"},
        {"id": "4h", "name": "4 hours"}, {"id": "1d", "name": "1 day"}, {"id": "1w", "name": "1 week"}, {"id": "1M", "name": "1 month"},
    ],
    "screener_fields": [
        {"field": "close", "name": "Price"}, {"field": "change", "name": "Change today (%)"},
        {"field": "volume", "name": "Volume (shares traded)"}, {"field": "market_cap_basic", "name": "Market cap (company size)"},
        {"field": "price_earnings_ttm", "name": "P/E ratio"}, {"field": "earnings_per_share_basic_ttm", "name": "Earnings per share"},
        {"field": "dividends_yield", "name": "Dividend yield (%)"}, {"field": "price_52_week_high", "name": "52-week high"},
        {"field": "price_52_week_low", "name": "52-week low"},
    ],
    "operators": [
        {"op": "gt", "name": "greater than"}, {"op": "gte", "name": "at least"}, {"op": "lt", "name": "less than"},
        {"op": "lte", "name": "at most"}, {"op": "eq", "name": "equal to"}, {"op": "neq", "name": "not equal to"},
        {"op": "in", "name": "one of a list"}, {"op": "between", "name": "between two values"},
    ],
    "freshness": {
        "real time": "Updates the moment trades happen.",
        "15 min delayed": "Shown about 15 minutes after the trade (normal for free stock data).",
        "end of day": "Only updated once per day.",
        "unknown": "The source did not say how fresh this is.",
    },
}
