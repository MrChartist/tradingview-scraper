# TradingView Intelligence Terminal

An enterprise-grade, real-time market data intelligence terminal. Built with a high-performance **FastAPI** backend and a clean, responsive frontend in the **@MrChartist look** (deep navy, teal and orange, with dark and light themes), this project operates as a powerful wrapper around the `tradingview-scraper` library.

**Repository:** [MrChartist/tradingview-scraper](https://github.com/MrChartist/tradingview-scraper)

---

## ✨ Key Features

### 1. Advanced Symbol Lookup 🔍
Type a company name or ticker (for example `Reliance` or `AAPL`) and pick from the suggestions. No need to remember the exchange prefix.
* **Overview:** General symbol information, current price, and basic performance metrics.
* **Price & Candles:** Interactive candlestick chart with volume, plus a sortable candle table.
* **Indicators:** Wide array of technical indicator values, kept in their own tab.
* **Fundamentals:** Deep-dive into financial data and fundamental graphs.
* **Real-Time OHLCV Pricing:** Streams historical and real-time candle data directly via TradingView WebSockets. Adjustable timeframes (1m to 1M) and candle limits.

### 2. Market Movers Dashboard 📈
Track the heartbeat of global markets across multiple asset classes and regions (USA, India, UK, Crypto, Forex).
* **Categories tracked:** Gainers, Losers, Most Active, Penny Stocks. Pre-Market and After-Hours Gainers/Losers are available for USA.
* **Clean lists:** Only liquid listings on the main exchange (for example NSE for India) are shown, so OTC and thinly traded names do not flood the results.
* Click any row to open that symbol in Symbol Lookup.
* Instantly view the biggest movers with percentage changes color-coded for quick visual parsing.

### 3. Dynamic Market Screener 🎯
Filter thousands of assets using custom parameters.
* **Available Filters:** Min/Max Price, Min/Max Change %, Minimum Volume, Minimum Market Cap, with sort field and order. Market cap is typed in the unit people use: ₹ Crore for India, millions elsewhere.
* **Presets:** Active gainers, active losers, volume leaders, large caps and more in one click.
* **Global Support:** Screen markets in USA, India, UK, Canada, Germany, Crypto, and Global Forex.

### 4. Native-currency numbers 🇮🇳
Monetary values are shown in the listing currency, not USD. Indian stocks read in rupees using Lakh and Crore (for example Reliance market cap `₹15.78 L Cr`, volume `1.68 Cr`). US stocks read in `$` with T/B/M. For symbols on NSE, BSE, NASDAQ, NYSE, AMEX, LSE, TSX, ASX and XETR the API replaces TradingView's USD fundamentals with the regional scanner's native figures. Other exchanges fall back to USD and are labelled `currency: USD` in the JSON.

### 5. Universal Data Export 💾
Every single module—Symbol Lookup, Market Movers, and Screener—supports one-click downloads in **CSV** or **JSON** formats (your current filters and timeframe are respected) for integration into your own data pipelines, backtesting engines, or spreadsheets.

---

## 🚀 Quick Start Guide

### Prerequisites
* Python 3.8+
* Modern Web Browser

### 1. Installation

Clone the repository and install the backend dependencies:

```bash
git clone https://github.com/MrChartist/tradingview-scraper.git
cd tradingview-scraper

# It is recommended to use a virtual environment
python -m venv .venv
source .venv/bin/activate  # On Windows use: .venv\Scripts\activate

# Install main requirements and API requirements
pip install -r requirements.txt
pip install -r api/requirements.txt

# Optional, only to run the tests
pip install pytest httpx
```

### 2. Running the Server

Start the FastAPI application using Uvicorn:

```bash
uvicorn api.main:app --reload --port 8000
```

### 3. Accessing the Terminal

Once the server is running, simply open your browser and navigate to:
**http://localhost:8000/**

*The frontend files are served directly by the FastAPI backend, so you can start the server from any directory.*

**Tips:** press `/` to focus the symbol box, `1` `2` `3` to switch sections, and use the moon/sun button to change theme (your choice is remembered).

---

## 📡 API Reference

The FastAPI backend provides robust REST endpoints that power the frontend. You can access the interactive Swagger documentation at `http://localhost:8000/docs`.

### Selected Endpoints:
* `GET /api/search?q=reliance` (symbol autocomplete)
* `GET /api/overview/{exchange}/{ticker}`
* `GET /api/indicators/{exchange}/{ticker}`
* `GET /api/fundamentals/{exchange}/{ticker}`
* `GET /api/ohlcv/{exchange}/{ticker}?timeframe=1d&candles=100`
* `GET /api/movers?market=stocks-usa&category=gainers&limit=25`
* `GET /api/screener?market=india&min_price=100&min_volume=1000000&sort_by=change`

Responses are cached for 60 seconds. Invalid input returns a clear `400`/`422` message instead of a server error.

---

## 🧪 Testing and Exported Data

The repository heavily tests its scraping capabilities to ensure reliability against TradingView updates.

### Running PyTests
You can run the full suite of scraper tests located in the `/tests/` directory:
```bash
pytest tests/
```

### Sample Data & Exports
When you use the scraper classes directly with `export_result=True`, JSON/CSV outputs are saved into the `/export/` directory. The web API does not write export files; use the download buttons instead.

**Example OHLCV Output Structure (`export/ohlc_...json`):**
```json
[
  {
    "index": 0,
    "timestamp": 1712534400,
    "open": 169.59,
    "high": 170.15,
    "low": 168.32,
    "close": 169.60,
    "volume": 42051200
  }
]
```

---

## 🛠 Tech Stack
* **Core:** Python, TradingView WebSocket protocol
* **Backend:** FastAPI, Uvicorn, Pydantic
* **Frontend:** Vanilla HTML/CSS/JS (no build step, no dependencies)
* **Data Processing:** Pandas, BeautifulSoup4

---

*Built by Mr. Chartist*
