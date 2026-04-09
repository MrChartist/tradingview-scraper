# TradingView Intelligence Terminal

![Terminal Preview](https://via.placeholder.com/1200x600/0a0e1a/38bdf8?text=TradingView+Intelligence+Terminal) *(Add actual screenshot here)*

An enterprise-grade, real-time market data intelligence terminal. Built with a high-performance **FastAPI** backend and a sleek, fully responsive **Glassmorphic** frontend, this project operates as a powerful wrapper around the `tradingview-scraper` library.

**Repository:** [MrChartist/tradingview-scraper](https://github.com/MrChartist/tradingview-scraper)

---

## ✨ Key Features

### 1. Advanced Symbol Lookup 🔍
Instantly fetch comprehensive data for any ticker (Stocks, Crypto, Forex).
* **Overview:** General symbol information, current price, and basic performance metrics.
* **Indicators:** Wide array of technical indicators (RSI, MACD, Moving Averages, etc.).
* **Fundamentals:** Deep-dive into financial data and fundamental graphs.
* **Real-Time OHLCV Pricing:** Streams historical and real-time candle data directly via TradingView WebSockets. Adjustable timeframes (1m to 1M) and candle limits.

### 2. Market Movers Dashboard 📈
Track the heartbeat of global markets across multiple asset classes and regions (USA, India, UK, Crypto, Forex).
* **Categories tracked:** Gainers, Losers, Most Active, Penny Stocks, Pre-Market Gainers/Losers, After-Hours Gainers/Losers.
* Instantly view the biggest movers with percentage changes color-coded for quick visual parsing.

### 3. Dynamic Market Screener 🎯
Filter thousands of assets using custom parameters.
* **Available Filters:** Minimum/Maximum Price, Minimum Volume, Minimum Market Cap.
* **Global Support:** Screen markets in USA, India, UK, Canada, Germany, Crypto, and Global Forex.

### 4. Universal Data Export 💾
Every single module—Symbol Lookup, Market Movers, and Screener—supports one-click secure downloads in **CSV** or **JSON** formats for integration into your own data pipelines, backtesting engines, or spreadsheets.

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
```

### 2. Running the Server

Start the FastAPI application using Uvicorn:

```bash
uvicorn api.main:app --reload --port 8000
```

### 3. Accessing the Terminal

Once the server is running, simply open your browser and navigate to:
**http://localhost:8000/**

*The frontend files are served directly by the FastAPI backend.*

---

## 📡 API Reference

The FastAPI backend provides robust REST endpoints that power the frontend. You can access the interactive Swagger documentation at `http://localhost:8000/docs`.

### Selected Endpoints:
* `GET /api/overview/{exchange}/{ticker}`
* `GET /api/indicators/{exchange}/{ticker}`
* `GET /api/fundamentals/{exchange}/{ticker}`
* `GET /api/ohlcv/{exchange}/{ticker}?timeframe=1d&candles=100`
* `GET /api/movers?market=stocks-usa&category=gainers&limit=25`
* `GET /api/screener?market=america&min_price=10&min_volume=1000000`

---

## 🧪 Testing and Exported Data

The repository heavily tests its scraping capabilities to ensure reliability against TradingView updates.

### Running PyTests
You can run the full suite of scraper tests located in the `/tests/` directory:
```bash
pytest tests/
```

### Sample Data & Exports
When streaming OHLCV data or running independent scraper scripts, JSON outputs are frequently saved into the `/export/` and `/tradingview_scraper/data/` directories. 

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
* **Frontend:** Vanilla HTML/CSS/JS (Zero-dependency, high-performance Glassmorphism UI)
* **Data Processing:** Pandas, BeautifulSoup4

---

*Built by Mr. Chartist*
