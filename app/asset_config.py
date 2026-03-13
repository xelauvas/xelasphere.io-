"""
Centralized Asset Configuration
================================
Minimal hardcoded mappings for fast-path detection.
Most crypto lookups use CoinGecko's dynamic coin list.
Used by views.py, crypto_api.py, and served to frontend via API.
"""

# Top 10 crypto for fast-path detection (rest use dynamic CoinGecko lookup)
CRYPTO_SYMBOLS = {
    'btc': 'bitcoin', 'bitcoin': 'bitcoin',
    'eth': 'ethereum', 'ethereum': 'ethereum',
    'sol': 'solana', 'solana': 'solana',
    'xrp': 'ripple', 'ripple': 'ripple',
    'ada': 'cardano', 'cardano': 'cardano',
    'doge': 'dogecoin', 'dogecoin': 'dogecoin',
    'bnb': 'binancecoin', 'binance': 'binancecoin',
    'dot': 'polkadot', 'polkadot': 'polkadot',
    'link': 'chainlink', 'chainlink': 'chainlink',
    'ltc': 'litecoin', 'litecoin': 'litecoin',
}

# CoinGecko ID -> Trading ticker symbol (top coins for display, rest use API symbol)
CRYPTO_TICKERS = {
    'bitcoin': 'BTC',
    'ethereum': 'ETH',
    'solana': 'SOL',
    'ripple': 'XRP',
    'cardano': 'ADA',
    'dogecoin': 'DOGE',
    'polkadot': 'DOT',
    'chainlink': 'LINK',
    'litecoin': 'LTC',
    'binancecoin': 'BNB',
}

# Display names for assets (top crypto + stocks; other crypto use API name)
ASSET_NAMES = {
    # Top 10 Crypto (others get name from CoinGecko API)
    'bitcoin': 'Bitcoin', 'btc': 'Bitcoin',
    'ethereum': 'Ethereum', 'eth': 'Ethereum',
    'solana': 'Solana', 'sol': 'Solana',
    'ripple': 'XRP', 'xrp': 'XRP',
    'cardano': 'Cardano', 'ada': 'Cardano',
    'dogecoin': 'Dogecoin', 'doge': 'Dogecoin',
    'polkadot': 'Polkadot', 'dot': 'Polkadot',
    'chainlink': 'Chainlink', 'link': 'Chainlink',
    'litecoin': 'Litecoin', 'ltc': 'Litecoin',
    'binancecoin': 'BNB', 'bnb': 'BNB',
    # Stocks
    'aapl': 'Apple', 'apple': 'Apple',
    'msft': 'Microsoft', 'microsoft': 'Microsoft',
    'googl': 'Google', 'goog': 'Google', 'google': 'Google',
    'amzn': 'Amazon', 'amazon': 'Amazon',
    'meta': 'Meta',
    'tsla': 'Tesla', 'tesla': 'Tesla',
    'nvda': 'NVIDIA', 'nvidia': 'NVIDIA',
    'amd': 'AMD',
    'intc': 'Intel', 'intel': 'Intel',
    'nflx': 'Netflix', 'netflix': 'Netflix',
    'dis': 'Disney', 'disney': 'Disney',
    'v': 'Visa', 'visa': 'Visa',
    'ma': 'Mastercard', 'mastercard': 'Mastercard',
    'jpm': 'JPMorgan', 'jpmorgan': 'JPMorgan',
    'wmt': 'Walmart', 'walmart': 'Walmart',
    'ko': 'Coca-Cola', 'coca-cola': 'Coca-Cola',
    'pep': 'PepsiCo', 'pepsico': 'PepsiCo',
    'mcd': "McDonald's", 'mcdonalds': "McDonald's",
    'ba': 'Boeing', 'boeing': 'Boeing',
    'ibm': 'IBM',
    'crm': 'Salesforce', 'salesforce': 'Salesforce',
    'orcl': 'Oracle', 'oracle': 'Oracle',
    'csco': 'Cisco', 'cisco': 'Cisco',
    'adbe': 'Adobe', 'adobe': 'Adobe',
    'pypl': 'PayPal', 'paypal': 'PayPal',
    'uber': 'Uber',
    'lyft': 'Lyft',
    'spot': 'Spotify', 'spotify': 'Spotify',
    'snap': 'Snap',
    'sq': 'Block', 'block': 'Block',
    'coin': 'Coinbase', 'coinbase': 'Coinbase',
    'hood': 'Robinhood', 'robinhood': 'Robinhood',
    'pltr': 'Palantir', 'palantir': 'Palantir',
    'snow': 'Snowflake', 'snowflake': 'Snowflake',
    'zm': 'Zoom', 'zoom': 'Zoom',
    'docu': 'DocuSign', 'docusign': 'DocuSign',
    'sbux': 'Starbucks', 'starbucks': 'Starbucks',
    'cost': 'Costco', 'costco': 'Costco',
    'hd': 'Home Depot', 'home depot': 'Home Depot',
    'bac': 'Bank of America',
}

# Popular stock symbols (set for fast lookup)
STOCK_SYMBOLS = {
    'aapl', 'msft', 'googl', 'goog', 'amzn', 'meta', 'tsla', 'nvda', 'amd', 'intc',
    'nflx', 'dis', 'pypl', 'v', 'ma', 'jpm', 'bac', 'wmt', 'ko', 'pep',
    'mcd', 'sbux', 'cost', 'hd', 'crm', 'adbe', 'orcl', 'csco', 'ibm',
    'uber', 'lyft', 'snap', 'sq', 'shop', 'spot', 'zoom', 'zm', 'pltr', 'coin', 'hood',
    'spy', 'qqq', 'voo', 'iwm', 'dia', 'arkk', 'ba', 'snow', 'docu'
}

# Company name -> stock symbol
COMPANY_TO_SYMBOL = {
    'apple': 'aapl', 'microsoft': 'msft', 'google': 'googl', 'alphabet': 'googl',
    'amazon': 'amzn', 'facebook': 'meta', 'meta': 'meta', 'tesla': 'tsla',
    'nvidia': 'nvda', 'netflix': 'nflx', 'disney': 'dis', 'paypal': 'pypl',
    'visa': 'v', 'mastercard': 'ma', 'jpmorgan': 'jpm', 'walmart': 'wmt',
    'coca-cola': 'ko', 'coca cola': 'ko', 'pepsi': 'pep', 'pepsico': 'pep',
    'mcdonalds': 'mcd', "mcdonald's": 'mcd', 'starbucks': 'sbux', 'costco': 'cost',
    'home depot': 'hd', 'salesforce': 'crm', 'adobe': 'adbe', 'oracle': 'orcl',
    'cisco': 'csco', 'ibm': 'ibm', 'uber': 'uber', 'lyft': 'lyft', 'snapchat': 'snap',
    'spotify': 'spot', 'palantir': 'pltr', 'coinbase': 'coin', 'robinhood': 'hood',
    'intel': 'intc', 'amd': 'amd', 'advanced micro': 'amd', 'boeing': 'ba',
    'snowflake': 'snow', 'docusign': 'docu', 'zoom': 'zm',
    'bank of america': 'bac'
}

# News symbol mapping (name -> CryptoCompare symbol for top coins)
NEWS_SYMBOL_MAP = {
    'BITCOIN': 'BTC', 'ETHEREUM': 'ETH', 'SOLANA': 'SOL',
    'CARDANO': 'ADA', 'DOGECOIN': 'DOGE', 'RIPPLE': 'XRP',
    'POLKADOT': 'DOT', 'CHAINLINK': 'LINK', 'LITECOIN': 'LTC',
    'BINANCECOIN': 'BNB', 'BINANCE': 'BNB',
}


def get_coin_ticker(coin_id: str) -> str:
    """Get trading ticker for a CoinGecko ID."""
    return CRYPTO_TICKERS.get(coin_id, coin_id.upper())


def get_coin_id(query: str) -> str | None:
    """Get CoinGecko ID from a query (symbol or name)."""
    return CRYPTO_SYMBOLS.get(query.lower())


def get_news_symbol(query: str) -> str:
    """Get CryptoCompare news symbol from query."""
    upper = query.upper()
    return NEWS_SYMBOL_MAP.get(upper, upper)


def get_display_name(asset: str) -> str:
    """Get display name for an asset."""
    return ASSET_NAMES.get(asset.lower(), asset.capitalize())


def is_stock_symbol(symbol: str) -> bool:
    """Check if a symbol is a known stock."""
    return symbol.lower() in STOCK_SYMBOLS


def get_stock_from_company(company: str) -> str | None:
    """Get stock symbol from company name."""
    return COMPANY_TO_SYMBOL.get(company.lower())


def get_frontend_config() -> dict:
    """Get config for frontend JavaScript."""
    return {
        'crypto_symbols': CRYPTO_SYMBOLS,
        'crypto_tickers': CRYPTO_TICKERS,
        'asset_names': ASSET_NAMES,
        'stock_symbols': list(STOCK_SYMBOLS),
        'company_to_symbol': COMPANY_TO_SYMBOL,
    }
