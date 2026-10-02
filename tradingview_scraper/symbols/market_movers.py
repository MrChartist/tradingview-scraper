"""Module providing a function to scrape market movers data (gainers, losers, penny stocks, etc.)."""

from typing import List, Optional, Dict

import requests

from tradingview_scraper.symbols.utils import (
    save_csv_file,
    save_json_file,
    generate_user_agent,
)


class MarketMovers:
    """
    A class to scrape market movers data from TradingView.

    This class provides functionality to retrieve data for various market categories
    such as gainers, losers, most active stocks, penny stocks, pre-market and
    after-hours gainers.

    Attributes:
        export_result (bool): Flag to determine if results should be exported to file.
        export_type (str): Type of export format ('json' or 'csv').
        headers (dict): HTTP headers for requests.

    Example:
        >>> scraper = MarketMovers(export_result=True, export_type='json')
        >>> gainers = scraper.scrape(market='stocks-usa', category='gainers')
        >>> print(gainers['data'])
    """

    # Supported markets
    SUPPORTED_MARKETS = [
        'stocks-usa',
        'stocks-uk',
        'stocks-india',
        'stocks-australia',
        'stocks-canada',
        'crypto',
        'forex',
        'bonds',
        'futures',
    ]

    # Supported categories for stock markets
    STOCK_CATEGORIES = [
        'gainers',
        'losers',
        'most-active',
        'penny-stocks',
        'pre-market-gainers',
        'pre-market-losers',
        'after-hours-gainers',
        'after-hours-losers',
    ]

    # Categories available for crypto, forex, futures and bonds
    OTHER_CATEGORIES = ['gainers', 'losers', 'most-active']

    # Default fields to fetch
    DEFAULT_FIELDS = [
        'name',
        'close',
        'change',
        'change_abs',
        'volume',
        'market_cap_basic',
        'price_earnings_ttm',
        'earnings_per_share_basic_ttm',
        'logoid',
        'description',
    ]

    def __init__(self, export_result: bool = False, export_type: str = 'json'):
        """
        Initialize the MarketMovers scraper.

        Args:
            export_result (bool): Whether to export results to a file. Defaults to False.
            export_type (str): Export format ('json' or 'csv'). Defaults to 'json'.
        """
        self.export_result = export_result
        self.export_type = export_type
        self.headers = {"User-Agent": generate_user_agent()}

    def _validate_market(self, market: str) -> None:
        """
        Validate if the market is supported.

        Args:
            market (str): The market to validate.

        Raises:
            ValueError: If the market is not supported.
        """
        if market not in self.SUPPORTED_MARKETS:
            raise ValueError(
                f"Unsupported market: {market}. "
                f"Supported markets: {', '.join(self.SUPPORTED_MARKETS)}"
            )

    def _validate_category(self, category: str, market: str) -> None:
        """
        Validate if the category is supported for the given market.

        Args:
            category (str): The category to validate.
            market (str): The market context.

        Raises:
            ValueError: If the category is not supported.
        """
        if self._extended_session(category) and market != 'stocks-usa':
            raise ValueError(
                f"Category '{category}' is only available for stocks-usa "
                "(no reliable extended-hours data for other markets)."
            )
        if market.startswith('stocks'):
            if category not in self.STOCK_CATEGORIES:
                raise ValueError(
                    f"Unsupported category: {category}. "
                    f"Supported categories for stocks: {', '.join(self.STOCK_CATEGORIES)}"
                )
            return
        if category not in self.OTHER_CATEGORIES:
            raise ValueError(
                f"Unsupported category for {market}: {category}. "
                f"Supported categories: {', '.join(self.OTHER_CATEGORIES)}"
            )

    def _build_scanner_payload(
        self,
        market: str,
        category: str,
        fields: Optional[List[str]] = None,
        limit: int = 50
    ) -> Dict:
        """
        Build the payload for the TradingView scanner API.

        Args:
            market (str): The market to scan.
            category (str): The category of market movers.
            fields (List[str], optional): Fields to retrieve.
            limit (int): Maximum number of results. Defaults to 50.

        Returns:
            dict: The scanner API payload.
        """
        if fields is None:
            fields = self.DEFAULT_FIELDS

        # Map category to filter conditions
        filter_conditions = self._get_filter_conditions(market, category)

        # Build sort configuration
        sort_config = self._get_sort_config(category)

        payload = {
            "columns": self._resolve_columns(category, fields),
            "filter": filter_conditions,
            "options": {
                "lang": "en"
            },
            "range": [0, limit],
            "sort": sort_config
        }

        return payload

    # Scanner region used for each market (one scanner endpoint per country).
    SCANNER_REGIONS = {
        'stocks-usa': 'america',
        'stocks-uk': 'uk',
        'stocks-india': 'india',
        'stocks-australia': 'australia',
        'stocks-canada': 'canada',
        'crypto': 'crypto',
        'forex': 'forex',
        'bonds': 'bonds',
        'futures': 'futures',
    }

    # Main exchanges per stock market. Keeps out OTC / illiquid listings and
    # duplicate cross-listings (e.g. NSE + BSE) that otherwise flood the lists.
    MAIN_EXCHANGES = {
        'stocks-usa': ['NASDAQ', 'NYSE', 'AMEX'],
        'stocks-uk': ['LSE'],
        'stocks-india': ['NSE'],
        'stocks-australia': ['ASX'],
        'stocks-canada': ['TSX'],
        'crypto': ['BINANCE', 'COINBASE', 'BYBIT', 'KRAKEN', 'OKX'],
    }

    # Minimum traded volume (shares / units) so results are actually tradable.
    MIN_VOLUME = {
        'stocks-usa': 100000,
        'stocks-uk': 100000,
        'stocks-india': 100000,
        'stocks-australia': 100000,
        'stocks-canada': 100000,
        'crypto': 1000000,
    }

    # Upper price bound for the "penny-stocks" category, in local currency.
    PENNY_PRICE = {
        'stocks-usa': 5,
        'stocks-india': 20,
        'stocks-uk': 100,
        'stocks-australia': 1,
        'stocks-canada': 5,
    }

    # Extended-hours categories read from dedicated scanner columns.
    EXTENDED_COLUMNS = {
        'pre-market': {
            'close': 'premarket_close',
            'change': 'premarket_change',
            'change_abs': 'premarket_change_abs',
            'volume': 'premarket_volume',
        },
        'after-hours': {
            'close': 'postmarket_close',
            'change': 'postmarket_change',
            'change_abs': 'postmarket_change_abs',
            'volume': 'postmarket_volume',
        },
    }

    @staticmethod
    def _extended_session(category: str) -> Optional[str]:
        """Return 'pre-market' / 'after-hours' for extended-hours categories."""
        if category.startswith('pre-market'):
            return 'pre-market'
        if category.startswith('after-hours'):
            return 'after-hours'
        return None

    def _resolve_columns(self, category: str, fields: List[str]) -> List[str]:
        """Map output field names to the scanner columns to request."""
        session = self._extended_session(category)
        mapping = self.EXTENDED_COLUMNS.get(session, {})
        return [mapping.get(f, f) for f in fields]

    def _get_filter_conditions(self, market: str, category: str) -> List[Dict]:
        """
        Get filter conditions based on market and category.

        Args:
            market (str): The market.
            category (str): The category.

        Returns:
            List[Dict]: Filter conditions for the scanner API.
        """
        filters = []
        session = self._extended_session(category)
        columns = self.EXTENDED_COLUMNS.get(session, {})
        change_col = columns.get('change', 'change')
        volume_col = columns.get('volume', 'volume')

        if market.startswith('stocks'):
            filters.append({"left": "type", "operation": "equal", "right": "stock"})

        exchanges = self.MAIN_EXCHANGES.get(market)
        if exchanges:
            filters.append({"left": "exchange", "operation": "in_range", "right": exchanges})

        min_volume = self.MIN_VOLUME.get(market)
        if min_volume:
            if session:
                min_volume = 10000  # extended hours trade far thinner
            filters.append({"left": volume_col, "operation": "greater", "right": min_volume})

        if category == 'penny-stocks':
            filters.append({
                "left": "close",
                "operation": "less",
                "right": self.PENNY_PRICE.get(market, 5),
            })
            filters.append({"left": "close", "operation": "greater", "right": 0})
        elif category.endswith('gainers'):
            filters.append({"left": change_col, "operation": "greater", "right": 0})
        elif category.endswith('losers'):
            filters.append({"left": change_col, "operation": "less", "right": 0})

        return filters

    def _get_sort_config(self, category: str) -> Dict:
        """
        Get sort configuration based on category.

        Args:
            category (str): The category.

        Returns:
            Dict: Sort configuration for the scanner API.
        """
        session = self._extended_session(category)
        columns = self.EXTENDED_COLUMNS.get(session, {})
        change_col = columns.get('change', 'change')

        if category.endswith('gainers'):
            return {"sortBy": change_col, "sortOrder": "desc"}
        if category.endswith('losers'):
            return {"sortBy": change_col, "sortOrder": "asc"}
        if category in ('most-active', 'penny-stocks'):
            return {"sortBy": "volume", "sortOrder": "desc"}
        return {"sortBy": change_col, "sortOrder": "desc"}

    def _get_scanner_url(self, market: str) -> str:
        """
        Get the appropriate scanner URL for the market.

        Args:
            market (str): The market.

        Returns:
            str: The scanner API URL.
        """
        region = self.SCANNER_REGIONS.get(market, 'america')
        return f"https://scanner.tradingview.com/{region}/scan"

    def scrape(
        self,
        market: str = 'stocks-usa',
        category: str = 'gainers',
        fields: Optional[List[str]] = None,
        limit: int = 50
    ) -> Dict:
        """
        Scrape market movers data from TradingView.

        Args:
            market (str): The market to scrape. Defaults to 'stocks-usa'.
            category (str): The category of market movers. Defaults to 'gainers'.
            fields (List[str], optional): Specific fields to retrieve. If None, uses default fields.
            limit (int): Maximum number of results to return. Defaults to 50.

        Returns:
            dict: A dictionary containing:
                - status (str): 'success' or 'failed'
                - data (List[Dict]): List of market mover data if successful
                - error (str): Error message if failed

        Raises:
            ValueError: If market or category is not supported.

        Example:
            >>> scraper = MarketMovers()
            >>> # Get top gainers
            >>> gainers = scraper.scrape(market='stocks-usa', category='gainers', limit=20)
            >>>
            >>> # Get penny stocks
            >>> penny_stocks = scraper.scrape(market='stocks-usa', category='penny-stocks', limit=100)
            >>>
            >>> # Get pre-market gainers with custom fields
            >>> premarket = scraper.scrape(
            ...     market='stocks-usa',
            ...     category='pre-market-gainers',
            ...     fields=['name', 'close', 'change', 'volume'],
            ...     limit=30
            ... )
        """
        # Validate inputs
        self._validate_market(market)
        self._validate_category(category, market)

        # Build payload
        payload = self._build_scanner_payload(market, category, fields, limit)

        # Get scanner URL
        url = self._get_scanner_url(market)

        try:
            # Make request
            response = requests.post(
                url,
                json=payload,
                headers=self.headers,
                timeout=10
            )

            if response.status_code == 200:
                json_response = response.json()

                # Extract data from response
                data = json_response.get('data', [])

                # Format the data
                formatted_data = []
                for item in data:
                    symbol_data = item.get('d', [])
                    if len(symbol_data) > 0:
                        # Map data to field names
                        formatted_item = {
                            'symbol': item.get('s', ''),
                        }

                        # Map each field value
                        field_list = fields if fields else self.DEFAULT_FIELDS
                        for idx, field in enumerate(field_list):
                            if idx < len(symbol_data):
                                formatted_item[field] = symbol_data[idx]

                        formatted_data.append(formatted_item)

                # Export if requested
                if self.export_result:
                    self._export(
                        data=formatted_data,
                        symbol=f"{market}_{category}",
                        data_category='market_movers'
                    )

                return {
                    'status': 'success',
                    'data': formatted_data,
                    'total': len(formatted_data)
                }
            else:
                return {
                    'status': 'failed',
                    'error': f'HTTP {response.status_code}: {response.text}'
                }

        except requests.RequestException as e:
            return {
                'status': 'failed',
                'error': f'Request failed: {str(e)}'
            }
        except Exception as e:
            return {
                'status': 'failed',
                'error': f'Request failed: {str(e)}'
            }

    def _export(
        self,
        data: List[Dict],
        symbol: Optional[str] = None,
        data_category: Optional[str] = None
    ) -> None:
        """
        Export scraped data to file.

        Args:
            data (List[Dict]): The data to export.
            symbol (str, optional): Symbol identifier for the filename.
            data_category (str, optional): Data category for the filename.
        """
        if self.export_type == 'json':
            save_json_file(data=data, symbol=symbol, data_category=data_category)
        elif self.export_type == 'csv':
            save_csv_file(data=data, symbol=symbol, data_category=data_category)
