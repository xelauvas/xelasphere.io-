# crypto_api.py
"""
Crypto & Stock Price API Module
Uses CoinGecko API for cryptocurrency data
Provides endpoints for price data and historical charts
"""

import requests
import time
from datetime import datetime, timedelta
from functools import lru_cache
from flask import Blueprint, jsonify, request

# yfinance is used as a thin extension over Yahoo's chart endpoint to
# fetch fields the unauthenticated chart API doesn't expose (notably
# market cap). Imported lazily/optionally so the module degrades
# gracefully if yfinance isn't installed.
try:
    import yfinance as _yf
    _YF_AVAILABLE = True
except ImportError:
    _YF_AVAILABLE = False

from .asset_config import (
    CRYPTO_SYMBOLS, CRYPTO_TICKERS, ASSET_NAMES, STOCK_SYMBOLS,
    COMPANY_TO_SYMBOL, NEWS_SYMBOL_MAP, get_coin_ticker, get_coin_id,
    get_news_symbol, get_display_name, get_frontend_config
)

crypto_bp = Blueprint('crypto', __name__)

# CoinGecko API base URL (free, no API key needed)
COINGECKO_BASE = 'https://api.coingecko.com/api/v3'

# ==================== FALLBACK APIs ==================== #
# CoinPaprika API (free tier: 1000 req/day, no key needed)
COINPAPRIKA_BASE = 'https://api.coinpaprika.com/v1'

# Binance Public API (free, no key needed for market data)
BINANCE_BASE = 'https://api.binance.com/api/v3'

# CoinGecko ID -> CoinPaprika ID mapping (top 30 coins for fallback)
# Only needed when CoinGecko is rate limited
COINGECKO_TO_PAPRIKA = {
    'bitcoin': 'btc-bitcoin',
    'ethereum': 'eth-ethereum',
    'solana': 'sol-solana',
    'ripple': 'xrp-xrp',
    'cardano': 'ada-cardano',
    'dogecoin': 'doge-dogecoin',
    'polkadot': 'dot-polkadot',
    'matic-network': 'matic-polygon',
    'shiba-inu': 'shib-shiba-inu',
    'avalanche-2': 'avax-avalanche',
    'chainlink': 'link-chainlink',
    'uniswap': 'uni-uniswap',
    'cosmos': 'atom-cosmos',
    'litecoin': 'ltc-litecoin',
    'binancecoin': 'bnb-binance-coin',
    'tron': 'trx-tron',
    'stellar': 'xlm-stellar',
    'toncoin': 'toncoin-the-open-network',
    'ethereum-classic': 'etc-ethereum-classic',
    'vechain': 'vet-vechain',
    'hedera-hashgraph': 'hbar-hedera',
    'filecoin': 'fil-filecoin',
    'monero': 'xmr-monero',
    'algorand': 'algo-algorand',
    'bitcoin-cash': 'bch-bitcoin-cash',
}

# CoinGecko ID -> Binance trading pair (top 30 coins for fallback)
# Only needed when CoinGecko is rate limited
COINGECKO_TO_BINANCE = {
    'bitcoin': 'BTCUSDT',
    'ethereum': 'ETHUSDT',
    'solana': 'SOLUSDT',
    'ripple': 'XRPUSDT',
    'cardano': 'ADAUSDT',
    'dogecoin': 'DOGEUSDT',
    'polkadot': 'DOTUSDT',
    'matic-network': 'MATICUSDT',
    'shiba-inu': 'SHIBUSDT',
    'avalanche-2': 'AVAXUSDT',
    'chainlink': 'LINKUSDT',
    'uniswap': 'UNIUSDT',
    'cosmos': 'ATOMUSDT',
    'litecoin': 'LTCUSDT',
    'binancecoin': 'BNBUSDT',
    'tron': 'TRXUSDT',
    'stellar': 'XLMUSDT',
    'toncoin': 'TONUSDT',
    'ethereum-classic': 'ETCUSDT',
    'vechain': 'VETUSDT',
    'hedera-hashgraph': 'HBARUSDT',
    'filecoin': 'FILUSDT',
    'algorand': 'ALGOUSDT',
    'bitcoin-cash': 'BCHUSDT',
}

# Cache for coin list (refreshed every hour)
# In-memory mirror retained for sub-millisecond reads; authoritative
# storage is Redis (see get_coin_list which now uses cache_get/cache_set).
_coin_list_cache = {'data': None, 'timestamp': 0}
_CACHE_DURATION = 3600  # 1 hour

# Cache for chart data (reduces API calls to avoid rate limiting).
# Migrated to Redis 2026-04-28: see crypto:chart:* keys. The Python dicts
# below are only retained as fallback when Redis is unreachable.
_chart_cache = {}
_CHART_CACHE_DURATION = 60   # 1 minute fresh
_CHART_STALE_TTL = 3600      # Redis-stored entries valid up to 1 hour
                             # for stale-cache fallback when CG is down

# Cache for price data — migrated to Redis 2026-04-28
_price_cache = {}
_PRICE_CACHE_DURATION = 30
_PRICE_STALE_TTL = 1800      # 30 minutes stale fallback


def _chart_cache_get(cache_key: str):
    """Get chart data from Redis. Returns the cached envelope
    {'data': ..., 'timestamp': ...} or None. The caller decides if
    timestamp is fresh enough vs. stale-fallback."""
    from app.redis_client import cache_get
    cached = cache_get(f'crypto:chart:{cache_key}')
    if cached and 'data' in cached and 'timestamp' in cached:
        return cached
    return None


def _chart_cache_set(cache_key: str, data) -> None:
    """Store chart data envelope in Redis with the stale TTL window."""
    from app.redis_client import cache_set
    cache_set(f'crypto:chart:{cache_key}',
              {'data': data, 'timestamp': time.time()},
              _CHART_STALE_TTL)


def _price_cache_get(cache_key: str):
    """Get price data from Redis."""
    from app.redis_client import cache_get
    cached = cache_get(f'crypto:price:{cache_key}')
    if cached and 'data' in cached and 'timestamp' in cached:
        return cached
    return None


def _price_cache_set(cache_key: str, data) -> None:
    """Store price data envelope in Redis."""
    from app.redis_client import cache_set
    cache_set(f'crypto:price:{cache_key}',
              {'data': data, 'timestamp': time.time()},
              _PRICE_STALE_TTL)

# Fast lookup for top 10 coins (skip API call for common queries)
# Everything else is looked up dynamically from CoinGecko's coin list
TOP_COINS_FAST = {
    'btc': 'bitcoin', 'bitcoin': 'bitcoin',
    'eth': 'ethereum', 'ethereum': 'ethereum',
    'sol': 'solana', 'solana': 'solana',
    'xrp': 'ripple',
    'ada': 'cardano',
    'doge': 'dogecoin',
    'bnb': 'binancecoin',
    'dot': 'polkadot',
    'link': 'chainlink',
    'ltc': 'litecoin',
}


# ==================== FALLBACK API HELPERS ==================== #

def _fetch_from_coinpaprika(coin_id: str, currency: str = 'usd') -> dict | None:
    """
    Fetch price data from CoinPaprika as fallback.
    Returns normalized data structure matching CoinGecko response.
    """
    paprika_id = COINGECKO_TO_PAPRIKA.get(coin_id)
    if not paprika_id:
        print(f'[CoinPaprika] No mapping for coin_id: {coin_id}')
        return None

    try:
        # CoinPaprika ticker endpoint includes price and market data
        response = requests.get(
            f'{COINPAPRIKA_BASE}/tickers/{paprika_id}',
            params={'quotes': currency.upper()},
            headers={'Accept': 'application/json'},
            timeout=10
        )

        if response.status_code == 200:
            data = response.json()
            quote = data.get('quotes', {}).get(currency.upper(), {})

            result = {
                'success': True,
                'coin_id': coin_id,
                'symbol': data.get('symbol', '').upper(),
                'name': data.get('name', ''),
                'currency': currency.upper(),
                'price': quote.get('price', 0),
                'change_24h': quote.get('percent_change_24h', 0),
                'volume_24h': quote.get('volume_24h', 0),
                'market_cap': quote.get('market_cap', 0),
                'data_source': 'coinpaprika'
            }
            print(f'[CoinPaprika] Successfully fetched price for {coin_id}: ${result["price"]}')
            return result

        print(f'[CoinPaprika] API error: {response.status_code}')
        return None

    except Exception as e:
        print(f'[CoinPaprika] Error fetching {coin_id}: {e}')
        return None


def _fetch_from_binance(coin_id: str, currency: str = 'usd') -> dict | None:
    """
    Fetch price data from Binance as fallback.
    Returns normalized data structure matching CoinGecko response.
    Note: Binance only supports USDT pairs, so currency param is ignored.
    """
    symbol = COINGECKO_TO_BINANCE.get(coin_id)
    if not symbol:
        print(f'[Binance] No mapping for coin_id: {coin_id}')
        return None

    try:
        # Get current price
        price_response = requests.get(
            f'{BINANCE_BASE}/ticker/price',
            params={'symbol': symbol},
            timeout=10
        )

        # Get 24h stats
        stats_response = requests.get(
            f'{BINANCE_BASE}/ticker/24hr',
            params={'symbol': symbol},
            timeout=10
        )

        if price_response.status_code == 200 and stats_response.status_code == 200:
            price_data = price_response.json()
            stats_data = stats_response.json()

            price = float(price_data.get('price', 0))
            change_24h = float(stats_data.get('priceChangePercent', 0))
            volume_24h = float(stats_data.get('quoteVolume', 0))  # Volume in USDT

            # Extract base symbol from pair (e.g., BTCUSDT -> BTC)
            base_symbol = symbol.replace('USDT', '')

            result = {
                'success': True,
                'coin_id': coin_id,
                'symbol': base_symbol,
                'currency': 'USD',  # Binance USDT ≈ USD
                'price': price,
                'change_24h': change_24h,
                'volume_24h': volume_24h,
                'market_cap': 0,  # Binance doesn't provide market cap
                'data_source': 'binance'
            }
            print(f'[Binance] Successfully fetched price for {coin_id}: ${result["price"]}')
            return result

        print(f'[Binance] API error: price={price_response.status_code}, stats={stats_response.status_code}')
        return None

    except Exception as e:
        print(f'[Binance] Error fetching {coin_id}: {e}')
        return None


def _fetch_chart_from_binance(coin_id: str, days: str = '1', before_ms: int | None = None) -> dict | None:
    """
    Fetch chart data from Binance klines API as fallback.
    Returns normalized data structure matching CoinGecko chart response.

    When ``before_ms`` is provided, returns a single page of klines ending
    at that timestamp (used by the lazy-load history feature: as the user
    pans left past the loaded window, the frontend asks for one chunk at
    a time using the previous chunk's earliest timestamp). The interval
    is still derived from ``days`` so each timeframe paginates at its own
    resolution (1D → 5m, 1W → 1h, etc.).
    """
    symbol = COINGECKO_TO_BINANCE.get(coin_id)
    if not symbol:
        print(f'[Binance] No chart mapping for coin_id: {coin_id}')
        return None

    # Map days to Binance interval and limit. When user asks for 'max',
    # use the maximum daily candles Binance allows in a single request
    # (1000 ≈ 2.7 years). For true all-time history (BTC since 2009 etc.),
    # pagination would be required — see TODO below.
    try:
        days_int = int(days) if days != 'max' else 1000
    except (ValueError, TypeError):
        days_int = 1

    if days_int <= 1:
        interval = '5m'
        limit = 288  # 24 hours at 5-min intervals
    elif days_int <= 7:
        interval = '1h'
        limit = days_int * 24
    elif days_int <= 30:
        interval = '4h'
        limit = days_int * 6
    elif days_int <= 90:
        interval = '1d'
        limit = days_int
    else:
        interval = '1d'
        limit = min(days_int, 1000)  # Binance per-page kline cap is 1000;
                                     # 'max' takes the paginated path below

    try:
        # For 'max' with daily interval, paginate. Binance kline limit
        # is 1000 per request. We chain requests with descending endTime
        # to walk back through history until Binance returns fewer than
        # the limit (= reached the start of trading) or we hit a safety
        # cap. Single-page case takes the regular path.
        klines = None
        if before_ms is not None:
            # Lazy-load historical chunk. One page ending at before_ms.
            response = requests.get(
                f'{BINANCE_BASE}/klines',
                params={
                    'symbol': symbol,
                    'interval': interval,
                    'limit': min(limit, 1000),
                    'endTime': before_ms,
                },
                timeout=15
            )
            if response.status_code != 200:
                print(f'[Binance] Chart API error (before): {response.status_code}')
                return None
            klines = response.json()
            print(f'[Binance] History chunk for {coin_id} before {before_ms}: {len(klines)} bars')
        elif days == 'max' and interval == '1d':
            klines = []
            end_time_ms = int(time.time() * 1000)
            PAGE_LIMIT = 1000
            MAX_PAGES = 18  # 18 × 1000 = 18000 daily bars ≈ 49 years
            for _ in range(MAX_PAGES):
                page_resp = requests.get(
                    f'{BINANCE_BASE}/klines',
                    params={
                        'symbol': symbol,
                        'interval': '1d',
                        'limit': PAGE_LIMIT,
                        'endTime': end_time_ms,
                    },
                    timeout=15
                )
                if page_resp.status_code != 200:
                    break
                page = page_resp.json()
                if not page:
                    break
                # Page is ascending; prepend to growing list.
                klines = page + klines
                # Step endTime to 1ms before this page's first kline.
                end_time_ms = page[0][0] - 1
                if len(page) < PAGE_LIMIT:
                    break
            print(f'[Binance] Paginated max for {coin_id}: {len(klines)} bars across pages')
        else:
            response = requests.get(
                f'{BINANCE_BASE}/klines',
                params={
                    'symbol': symbol,
                    'interval': interval,
                    'limit': limit
                },
                timeout=15
            )
            if response.status_code != 200:
                print(f'[Binance] Chart API error: {response.status_code}')
                return None
            klines = response.json()

        if klines is not None:

            # Binance kline format: [open_time, open, high, low, close, volume, ...]
            chart_data = []
            volume_data = []
            for i, kline in enumerate(klines):
                timestamp_ms = kline[0]
                close_price = float(kline[4])
                volume = float(kline[5])
                ts = int(timestamp_ms / 1000)
                chart_data.append({'time': ts, 'value': close_price})
                # Volume in BASE currency * close price ≈ USD value
                # (klines volume[5] is base-asset units, e.g., BTC; for
                # consistency with CoinGecko which returns USD volume,
                # multiply by close price to get USD-equivalent).
                vol_usd = volume * close_price
                prev_close = float(klines[i - 1][4]) if i > 0 else close_price
                color = 'rgba(34, 197, 94, 0.4)' if close_price >= prev_close else 'rgba(239, 68, 68, 0.4)'
                volume_data.append({'time': ts, 'value': vol_usd, 'color': color})

            # Empty historical chunk = reached start of asset's trading
            # history. Return a successful empty result so the frontend
            # can mark "no more history" without treating it as an error.
            if before_ms is not None and len(chart_data) == 0:
                base_symbol = symbol.replace('USDT', '')
                return {
                    'success': True,
                    'coin_id': coin_id,
                    'symbol': base_symbol,
                    'currency': 'USD',
                    'days': days,
                    'data': [],
                    'volume_data': [],
                    'historical_chunk': True,
                    'history_exhausted': True,
                    'data_source': 'binance',
                }

            # Reject empty/single-point responses. Binance can return a
            # valid 200 with [] klines for symbols that don't trade or
            # for ranges that exceed available history. Without this
            # guard, the empty result gets cached as a 'success' for 60s
            # and the chart silently shows $0.00 / no line.
            if len(chart_data) < 2:
                print(f'[Binance] Insufficient chart data ({len(chart_data)} points) for {coin_id}')
                return None

            first_price = chart_data[0]['value']
            last_price = chart_data[-1]['value']
            change_pct = ((last_price - first_price) / first_price) * 100 if first_price > 0 else 0

            # Get ticker symbol
            base_symbol = symbol.replace('USDT', '')

            result = {
                'success': True,
                'coin_id': coin_id,
                'symbol': base_symbol,
                'currency': 'USD',
                'days': days,
                'data': chart_data,
                'volume_data': volume_data,
                'current_price': chart_data[-1]['value'] if chart_data else 0,
                'change_percent': round(change_pct, 2),
                'is_positive': change_pct >= 0,
                'data_source': 'binance'
            }
            if before_ms is not None:
                # Mark this as a historical chunk so the frontend knows
                # to prepend rather than replace the loaded series.
                result['historical_chunk'] = True
                # If the chunk came back short, we've reached the start
                # of trading — flag so the frontend stops asking.
                result['history_exhausted'] = len(klines) < min(limit, 1000)
            print(f'[Binance] Successfully fetched chart for {coin_id}: {len(chart_data)} points')
            return result

        return None

    except Exception as e:
        print(f'[Binance] Chart error for {coin_id}: {e}')
        return None


def _fetch_price_with_fallback(coin_id: str, currency: str = 'usd') -> dict | None:
    """
    Fetch price data with automatic fallback chain:
    1. CoinGecko (primary)
    2. CoinPaprika (fallback #1)
    3. Binance (fallback #2)

    Returns normalized data structure with 'data_source' field.
    """
    # Check cache first (Redis-backed; survives restarts, shared workers)
    cache_key = f"{coin_id}_{currency}"
    now = time.time()
    cached = _price_cache_get(cache_key)
    if cached:
        cache_age = now - cached['timestamp']
        if cache_age < _PRICE_CACHE_DURATION:
            result = cached['data'].copy()
            result['cache_age_seconds'] = round(cache_age)
            if 'data_source' not in result:
                result['data_source'] = 'cache'
            return result

    # Try CoinGecko first
    try:
        response = requests.get(
            f'{COINGECKO_BASE}/simple/price',
            params={
                'ids': coin_id,
                'vs_currencies': currency,
                'include_24hr_change': 'true',
                'include_24hr_vol': 'true',
                'include_market_cap': 'true'
            },
            headers={
                'User-Agent': 'Mozilla/5.0',
                'Accept': 'application/json'
            },
            timeout=10
        )

        if response.status_code == 200:
            data = response.json()
            if coin_id in data:
                coin_data = data[coin_id]
                result = {
                    'success': True,
                    'coin_id': coin_id,
                    'symbol': get_ticker_for_coin(coin_id),
                    'currency': currency.upper(),
                    'price': coin_data.get(currency, 0),
                    'change_24h': coin_data.get(f'{currency}_24h_change', 0),
                    'volume_24h': coin_data.get(f'{currency}_24h_vol', 0),
                    'market_cap': coin_data.get(f'{currency}_market_cap', 0),
                    'data_source': 'coingecko'
                }
                # Cache the result
                _price_cache_set(cache_key, result)
                print(f'[CoinGecko] Successfully fetched price for {coin_id}')
                return result

        # Rate limited - try fallbacks
        if response.status_code == 429:
            print(f'[CoinGecko] Rate limited (429), trying fallbacks...')
        else:
            print(f'[CoinGecko] Error {response.status_code}, trying fallbacks...')

    except Exception as e:
        print(f'[CoinGecko] Exception: {e}, trying fallbacks...')

    # Fallback #1: CoinPaprika
    result = _fetch_from_coinpaprika(coin_id, currency)
    if result:
        _price_cache_set(cache_key, result)
        return result

    # Fallback #2: Binance
    result = _fetch_from_binance(coin_id, currency)
    if result:
        _price_cache_set(cache_key, result)
        return result

    print(f'[PriceFallback] All APIs failed for {coin_id}')
    return None


def get_coin_id(query: str) -> str | None:
    """
    Convert a crypto symbol or name to CoinGecko coin ID.
    Uses dynamic lookup from CoinGecko's coin list (cached for 1 hour).
    """
    query_lower = query.lower().strip()

    # Exclude common English words that might match obscure coin names
    EXCLUDED_WORDS = {
        'help', 'the', 'and', 'for', 'are', 'but', 'not', 'you', 'all', 'can',
        'her', 'was', 'one', 'our', 'out', 'day', 'get', 'has', 'him', 'his',
        'how', 'its', 'may', 'new', 'now', 'old', 'see', 'two', 'way', 'who',
        'any', 'ask', 'did', 'own', 'say', 'she', 'too', 'use', 'what', 'when',
        'where', 'why', 'will', 'with', 'your', 'about', 'more', 'some', 'than',
        'them', 'then', 'this', 'want', 'write', 'email', 'good', 'make', 'time',
        'very', 'just', 'know', 'take', 'come', 'could', 'think', 'look', 'give',
        'most', 'only', 'over', 'such', 'tell', 'show', 'also', 'back', 'after',
        'first', 'well', 'even', 'must', 'before', 'great', 'through', 'high',
        'long', 'little', 'every', 'world', 'still', 'last', 'never', 'said',
        'yes', 'let', 'here', 'same', 'another', 'while', 'keep', 'should'
    }
    if query_lower in EXCLUDED_WORDS:
        return None

    # 1. Fast path: check the local curated list. TOP_COINS_FAST has top
    # 10 by some forms; CRYPTO_SYMBOLS (asset_config) has top ~30 by
    # both symbol AND name forms ("vechain" + "vet" both resolve).
    # Curated list catches popular coins instantly even when CoinGecko's
    # /coins/list cache is cold (race after Flask restart).
    if query_lower in CRYPTO_SYMBOLS:
        return CRYPTO_SYMBOLS[query_lower]
    if query_lower in TOP_COINS_FAST:
        return TOP_COINS_FAST[query_lower]

    # 2. Dynamic lookup from CoinGecko's cached coin list
    coin_list = get_coin_list()
    if coin_list:
        # First pass: exact match on symbol or id
        for coin in coin_list:
            if coin.get('symbol', '').lower() == query_lower:
                return coin.get('id')
            if coin.get('id', '').lower() == query_lower:
                return coin.get('id')

        # Second pass: match on name (e.g., "vechain" -> "vechain")
        for coin in coin_list:
            if coin.get('name', '').lower() == query_lower:
                return coin.get('id')

    return None


def get_coin_list():
    """Get cached list of all coins from CoinGecko.

    Uses Redis as the cache backend (was in-memory dict). Benefits:
    - Survives Flask restarts (no cold-start race that returned 'Unknown
      cryptocurrency: vechain' until cache warmed)
    - Shared across gunicorn workers (one fetch warms all workers)
    - 1-hour TTL same as before
    """
    from app.redis_client import cache_get, cache_set

    cached = cache_get('crypto:coin_list')
    if cached:
        # Also keep the in-memory mirror for sub-millisecond hot reads
        # within a single request cycle (avoids repeated Redis hits).
        global _coin_list_cache
        _coin_list_cache = {'data': cached, 'timestamp': time.time()}
        return cached

    try:
        response = requests.get(
            f'{COINGECKO_BASE}/coins/list',
            headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Accept': 'application/json'
            },
            timeout=10
        )
        if response.status_code == 200:
            data = response.json()
            cache_set('crypto:coin_list', data, _CACHE_DURATION)
            _coin_list_cache = {'data': data, 'timestamp': time.time()}
            return data
    except Exception as e:
        print(f'[CryptoAPI] Error fetching coin list: {e}')

    # Fall back to whatever's in the in-memory mirror (might be stale
    # from earlier in this process's lifetime).
    return _coin_list_cache.get('data', [])


def get_ticker_for_coin(coin_id: str) -> str:
    """
    Get the proper ticker symbol for a coin ID.
    First checks hardcoded tickers (top 10), then looks up from CoinGecko coin list.
    """
    # Fast path: check hardcoded tickers
    ticker = get_coin_ticker(coin_id)
    if ticker and ticker != coin_id.upper():
        return ticker

    # Dynamic lookup from coin list
    coin_list = get_coin_list()
    for coin in coin_list:
        if coin.get('id') == coin_id:
            return coin.get('symbol', coin_id).upper()

    return coin_id.upper()


@crypto_bp.route('/api/crypto/price', methods=['GET'])
def get_crypto_price():
    """
    Get current price and 24h change for a cryptocurrency.
    Uses automatic fallback: CoinGecko -> CoinPaprika -> Binance

    Query params:
        - coin: Coin symbol (btc, eth) or ID (bitcoin, ethereum)
        - currency: Target currency (default: usd)
    """
    coin_query = request.args.get('coin', '').strip()
    currency = request.args.get('currency', 'usd').lower()

    if not coin_query:
        return jsonify({'error': 'Missing coin parameter'}), 400

    coin_id = get_coin_id(coin_query)
    if not coin_id:
        return jsonify({'error': f'Unknown cryptocurrency: {coin_query}'}), 404

    # Use fallback-enabled price fetcher
    result = _fetch_price_with_fallback(coin_id, currency)

    if result:
        return jsonify(result)

    return jsonify({'error': 'All price APIs failed. Please try again later.'}), 503


# Cache for detailed coin data
_details_cache = {}
_DETAILS_CACHE_DURATION = 60  # 1 minute cache


@crypto_bp.route('/api/crypto/details', methods=['GET'])
def get_crypto_details():
    """
    Get detailed information about a cryptocurrency including:
    - Market cap
    - 24h high/low
    - All-time high (ATH)
    - Circulating supply
    - Total supply

    Query params:
        - coin: Coin symbol (btc, eth) or ID (bitcoin, ethereum)
        - currency: Target currency (default: usd)
    """
    global _details_cache

    coin_query = request.args.get('coin', '').strip()
    currency = request.args.get('currency', 'usd').lower()

    if not coin_query:
        return jsonify({'error': 'Missing coin parameter'}), 400

    coin_id = get_coin_id(coin_query)
    if not coin_id:
        return jsonify({'error': f'Unknown cryptocurrency: {coin_query}'}), 404

    # Check cache first
    cache_key = f"details_{coin_id}_{currency}"
    now = time.time()
    if cache_key in _details_cache:
        cached = _details_cache[cache_key]
        cache_age = now - cached['timestamp']
        if cache_age < _DETAILS_CACHE_DURATION:
            print(f'[CryptoAPI] Returning cached details for {cache_key}')
            result = cached['data'].copy()
            result['data_source'] = 'cache'
            result['cache_age_seconds'] = round(cache_age)
            return jsonify(result)

    try:
        response = requests.get(
            f'{COINGECKO_BASE}/coins/{coin_id}',
            params={
                'localization': 'false',
                'tickers': 'false',
                'market_data': 'true',
                'community_data': 'false',
                'developer_data': 'false',
                'sparkline': 'false'
            },
            headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Accept': 'application/json'
            },
            timeout=10
        )

        if response.status_code == 200:
            data = response.json()
            market_data = data.get('market_data', {})

            # Extract relevant data
            result = {
                'success': True,
                'coin_id': coin_id,
                'name': data.get('name', ''),
                'symbol': data.get('symbol', '').upper(),
                'image': data.get('image', {}).get('small', ''),
                'currency': currency.upper(),
                # Current price
                'price': market_data.get('current_price', {}).get(currency, 0),
                'change_24h': market_data.get('price_change_percentage_24h', 0),
                # Market cap
                'market_cap': market_data.get('market_cap', {}).get(currency, 0),
                'market_cap_rank': market_data.get('market_cap_rank', 0),
                # 24h high/low
                'high_24h': market_data.get('high_24h', {}).get(currency, 0),
                'low_24h': market_data.get('low_24h', {}).get(currency, 0),
                # Volume
                'volume_24h': market_data.get('total_volume', {}).get(currency, 0),
                # All-time high
                'ath': market_data.get('ath', {}).get(currency, 0),
                'ath_change_percentage': market_data.get('ath_change_percentage', {}).get(currency, 0),
                'ath_date': market_data.get('ath_date', {}).get(currency, ''),
                # All-time low
                'atl': market_data.get('atl', {}).get(currency, 0),
                'atl_change_percentage': market_data.get('atl_change_percentage', {}).get(currency, 0),
                # Supply
                'circulating_supply': market_data.get('circulating_supply', 0),
                'total_supply': market_data.get('total_supply', 0),
                'max_supply': market_data.get('max_supply', 0),
                # Additional info
                'fully_diluted_valuation': market_data.get('fully_diluted_valuation', {}).get(currency, 0),
                'last_updated': data.get('last_updated', ''),
                'data_source': 'live'
            }

            # Cache the result
            _details_cache[cache_key] = {
                'data': result,
                'timestamp': time.time()
            }

            return jsonify(result)

        # Rate limited - try to return stale cache if available
        if response.status_code == 429:
            print(f'[CryptoAPI] CoinGecko details rate limited, checking stale cache...')
            if cache_key in _details_cache:
                cached = _details_cache[cache_key]
                cache_age = now - cached['timestamp']
                result = cached['data'].copy()
                result['data_source'] = 'stale_cache'
                result['cache_age_seconds'] = round(cache_age)
                print(f'[CryptoAPI] Returning stale details cache for {cache_key}')
                return jsonify(result)

        print(f'[CryptoAPI] Details error: status={response.status_code}, body={response.text[:500]}')
        # Return 200 with error info so frontend doesn't throw — details are non-critical
        return jsonify({
            'success': False,
            'error': f'CoinGecko API error: {response.status_code}',
            'rate_limited': response.status_code == 429,
            'coin_id': coin_id
        })

    except Exception as e:
        print(f'[CryptoAPI] Details error: {e}')
        # On exception, try stale cache
        if cache_key in _details_cache:
            cached = _details_cache[cache_key]
            cache_age = now - cached['timestamp']
            result = cached['data'].copy()
            result['data_source'] = 'stale_cache'
            result['cache_age_seconds'] = round(cache_age)
            return jsonify(result)
        return jsonify({'error': str(e)}), 500


# Crypto news via free RSS feeds (no API key needed)
_CRYPTO_NEWS_RSS_FEEDS = [
    'https://www.coindesk.com/arc/outboundfeeds/rss/',
    'https://cointelegraph.com/rss',
]

# Cache for news data
_news_cache = {}
_NEWS_CACHE_DURATION = 300  # 5 minutes cache for news


def _fetch_rss_news(coin_symbol: str = '', limit: int = 5) -> list:
    """Fetch crypto news from RSS feeds and optionally filter by coin symbol."""
    import xml.etree.ElementTree as ET
    from html import unescape

    all_articles = []
    coin_upper = coin_symbol.upper() if coin_symbol else ''
    coin_lower = coin_symbol.lower() if coin_symbol else ''

    # Map symbols to searchable names
    _SYMBOL_NAMES = {
        'BTC': ['bitcoin', 'btc'],
        'ETH': ['ethereum', 'eth', 'ether'],
        'SOL': ['solana', 'sol'],
        'ADA': ['cardano', 'ada'],
        'DOGE': ['dogecoin', 'doge'],
        'XRP': ['xrp', 'ripple'],
        'DOT': ['polkadot', 'dot'],
        'LINK': ['chainlink', 'link'],
        'LTC': ['litecoin', 'ltc'],
        'AVAX': ['avalanche', 'avax'],
        'MATIC': ['polygon', 'matic'],
        'UNI': ['uniswap', 'uni'],
        'ATOM': ['cosmos', 'atom'],
        'BNB': ['binance', 'bnb'],
    }
    search_terms = _SYMBOL_NAMES.get(coin_upper, [coin_lower] if coin_lower else [])

    import re as _re

    for feed_url in _CRYPTO_NEWS_RSS_FEEDS:
        try:
            resp = requests.get(feed_url, timeout=8, headers={
                'User-Agent': 'Mozilla/5.0 (compatible; XelaSphere/1.0)'
            })
            if resp.status_code != 200:
                continue

            root = ET.fromstring(resp.content)
            items = root.findall('.//item')

            for item in items:
                title = ''
                link = ''
                desc = ''
                pub_date = ''

                title_el = item.find('title')
                if title_el is not None and title_el.text:
                    title = title_el.text

                link_el = item.find('link')
                if link_el is not None:
                    link = link_el.text or ''

                desc_el = item.find('description')
                if desc_el is not None and desc_el.text:
                    desc = unescape(desc_el.text)
                    desc = _re.sub(r'<[^>]+>', '', desc).strip()

                pub_el = item.find('pubDate')
                if pub_el is not None and pub_el.text:
                    pub_date = pub_el.text

                source_name = 'CoinDesk' if 'coindesk' in feed_url else 'CoinTelegraph' if 'cointelegraph' in feed_url else 'Crypto News'

                article = {
                    'title': title.strip(),
                    'body': desc[:200] + '...' if len(desc) > 200 else desc,
                    'url': link.strip(),
                    'source': source_name,
                    'image': '',
                    'published_on': pub_date,
                    'categories': [coin_upper] if coin_upper else []
                }

                if not title.strip():
                    continue

                # Filter by coin if specified
                if search_terms:
                    combined = (title + ' ' + desc).lower()
                    if not any(term in combined for term in search_terms):
                        continue

                all_articles.append(article)

        except Exception as e:
            import traceback
            print(f'[CryptoAPI] RSS feed error ({feed_url}): {e}')
            traceback.print_exc()
            continue

    # Deduplicate by title
    seen_titles = set()
    unique = []
    for a in all_articles:
        t = a['title'].lower().strip()
        if t and t not in seen_titles:
            seen_titles.add(t)
            unique.append(a)

    return unique[:limit]

# ==================== AI CONTEXT CACHE ==================== #
# Unified cache for AI context injection - consolidates price, details, and news
_ai_context_cache = {}
_AI_CONTEXT_CACHE_DURATION = 300  # 5 minutes - matches news cache

# Stock price cache for AI context
_stock_price_cache = {}
_STOCK_CACHE_DURATION = 60  # 1 minute for stocks

# NEWS_SYMBOL_MAP imported from asset_config


def _fetch_news_internal(coin_symbol: str, limit: int = 5) -> dict | None:
    """
    Internal function to fetch news without Flask request context.
    Used by _build_ai_context for proactive news fetching.
    Returns cached data if available, fetches fresh if not.
    """
    global _news_cache

    symbol_upper = coin_symbol.upper()
    news_symbol = NEWS_SYMBOL_MAP.get(symbol_upper, symbol_upper)

    cache_key = f"news_{news_symbol}_{limit}"
    now = time.time()

    if cache_key in _news_cache:
        cached = _news_cache[cache_key]
        if now - cached['timestamp'] < _NEWS_CACHE_DURATION:
            return cached['data']

    print(f'[CryptoAPI] _fetch_news_internal: fetching RSS news for {news_symbol}')
    try:
        news_items = _fetch_rss_news(news_symbol, limit)

        result = {
            'success': True,
            'coin': news_symbol,
            'count': len(news_items),
            'articles': news_items
        }

        _news_cache[cache_key] = {
            'data': result,
            'timestamp': time.time()
        }

        print(f'[CryptoAPI] _fetch_news_internal: fetched {len(news_items)} articles for {news_symbol}')
        return result

    except Exception as e:
        print(f'[CryptoAPI] _fetch_news_internal error: {e}')
        return None


def get_stock_ai_context(symbol: str) -> dict | None:
    """
    Get stock data for AI context injection.
    Returns cached data if available, otherwise fetches fresh.
    """
    global _stock_price_cache

    symbol_upper = symbol.upper()
    cache_key = f"stock_ai_{symbol_upper}"
    now = time.time()

    # Check cache first
    if cache_key in _stock_price_cache:
        cached = _stock_price_cache[cache_key]
        if now - cached['timestamp'] < _STOCK_CACHE_DURATION:
            print(f'[StockAPI] Returning cached AI context for {symbol_upper}')
            return cached['data']

    # Fetch fresh data from Yahoo Finance
    try:
        url = f'https://query1.finance.yahoo.com/v8/finance/chart/{symbol_upper}'
        params = {'interval': '1d', 'range': '5d'}

        response = requests.get(url, params=params, timeout=10, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })

        if response.status_code == 200:
            data = response.json()
            result = data.get('chart', {}).get('result', [])

            if result:
                quote = result[0]
                meta = quote.get('meta', {})

                current_price = meta.get('regularMarketPrice', 0)
                previous_close = meta.get('previousClose', current_price)
                change = current_price - previous_close
                change_pct = (change / previous_close * 100) if previous_close else 0

                context = {
                    'symbol': symbol_upper,
                    'name': POPULAR_STOCKS.get(symbol_upper.lower(), meta.get('shortName', symbol_upper)),
                    'price': round(current_price, 2),
                    'change': round(change, 2),
                    'change_percent': round(change_pct, 2),
                    'currency': meta.get('currency', 'USD'),
                    'exchange': meta.get('exchangeName', 'Unknown'),
                    'market_state': meta.get('marketState', 'CLOSED'),
                    'previous_close': round(previous_close, 2),
                    'asset_type': 'stock',
                    'last_updated': datetime.now().isoformat()
                }

                # Cache it
                _stock_price_cache[cache_key] = {
                    'data': context,
                    'timestamp': now
                }

                return context

    except Exception as e:
        print(f'[StockAPI] AI context error: {e}')

    return None


def format_stock_context_for_ai(context: dict) -> str:
    """
    Format stock context into a readable string for AI injection.
    """
    if not context:
        return ""

    lines = []
    lines.append(f"=== LIVE {context.get('symbol', 'STOCK')} DATA (as of {context.get('last_updated', 'now')}) ===")

    if context.get('name'):
        lines.append(f"Company: {context['name']} ({context.get('symbol', '')})")

    if context.get('price'):
        price = context['price']
        change = context.get('change', 0)
        change_pct = context.get('change_percent', 0)
        sign = '+' if change >= 0 else ''
        lines.append(f"Current Price: ${price:,.2f} ({sign}{change:,.2f} / {sign}{change_pct:.2f}%)")

    if context.get('previous_close'):
        lines.append(f"Previous Close: ${context['previous_close']:,.2f}")

    if context.get('exchange'):
        lines.append(f"Exchange: {context['exchange']}")

    if context.get('market_state'):
        lines.append(f"Market Status: {context['market_state']}")

    lines.append("=" * 50)
    lines.append("Use the above data to answer the user's question about this stock.")

    return "\n".join(lines)


def get_crypto_ai_context(coin_query: str, currency: str = 'usd') -> dict | None:
    """
    Get consolidated crypto data for AI context injection.
    Returns cached data if available and fresh, otherwise fetches new data.

    This is used to provide the AI with real-time crypto info without web search.

    Returns:
        dict with: name, symbol, price, change_24h, market_cap, volume,
                   high_24h, low_24h, ath, news (list of headlines)
        or None if coin not found
    """
    global _ai_context_cache

    coin_id = get_coin_id(coin_query)
    if not coin_id:
        return None

    cache_key = f"ai_context_{coin_id}_{currency}"
    now = time.time()

    # Check cache first
    if cache_key in _ai_context_cache:
        cached = _ai_context_cache[cache_key]
        if now - cached['timestamp'] < _AI_CONTEXT_CACHE_DURATION:
            print(f'[CryptoAPI] Returning cached AI context for {coin_id}')
            return cached['data']

    # Build context from existing caches or fetch fresh
    context = _build_ai_context(coin_id, coin_query, currency)

    if context:
        _ai_context_cache[cache_key] = {
            'data': context,
            'timestamp': now
        }

    return context


def _build_ai_context(coin_id: str, original_query: str, currency: str = 'usd') -> dict | None:
    """
    Build AI context by consolidating data from various sources.
    Uses existing caches when available, fetches fresh data if needed.
    """
    now = time.time()
    oldest_cache_age = 0  # Track oldest data for freshness indication

    context = {
        'coin_id': coin_id,
        'symbol': original_query.upper(),
        'currency': currency.upper(),
        'source': 'cached_data',
        'last_updated': datetime.now().isoformat()
    }

    # Try to get price data - use cache if available, otherwise fetch with fallback
    price_cache_key = f"{coin_id}_{currency}"
    price_data = None

    cached = _price_cache_get(price_cache_key)
    if cached:
        cache_age = now - cached['timestamp']
        if cache_age < _PRICE_CACHE_DURATION * 60:  # Allow up to 30 min stale
            oldest_cache_age = max(oldest_cache_age, cache_age)
            price_data = cached['data']

    # Proactively fetch price if not in cache (uses fallback chain)
    if not price_data:
        print(f'[CryptoAPI] AI context: proactively fetching price for {coin_id}')
        price_data = _fetch_price_with_fallback(coin_id, currency)

    if price_data:
        context['price'] = price_data.get('price')
        context['change_24h'] = price_data.get('change_24h')
        context['volume_24h'] = price_data.get('volume_24h')
        context['market_cap'] = price_data.get('market_cap')
        context['price_source'] = price_data.get('data_source', 'unknown')

    # Try to get detailed data (from cache) - allow very stale data for AI context
    details_cache_key = f"details_{coin_id}_{currency}"
    if details_cache_key in _details_cache:
        cached = _details_cache[details_cache_key]
        cache_age = now - cached['timestamp']
        if cache_age < _DETAILS_CACHE_DURATION * 30:  # Allow up to 30 min stale
            oldest_cache_age = max(oldest_cache_age, cache_age)
            details_data = cached['data']
            context['name'] = details_data.get('name')
            # Get actual trading symbol from CoinGecko (e.g., "xlm" for Stellar)
            if details_data.get('symbol'):
                context['symbol'] = details_data.get('symbol').upper()
            # else keep the original_query.upper() as fallback
            context['high_24h'] = details_data.get('high_24h')
            context['low_24h'] = details_data.get('low_24h')
            context['ath'] = details_data.get('ath')
            context['ath_change_percentage'] = details_data.get('ath_change_percentage')
            context['circulating_supply'] = details_data.get('circulating_supply')
            context['max_supply'] = details_data.get('max_supply')
            context['market_cap_rank'] = details_data.get('market_cap_rank')
            # Update price from details if not set
            if not context.get('price'):
                context['price'] = details_data.get('price')
                context['change_24h'] = details_data.get('change_24h')

    # Try to get news (from cache or fetch proactively)
    symbol_upper = original_query.upper()
    # Use the global symbol map
    news_symbol = NEWS_SYMBOL_MAP.get(symbol_upper, symbol_upper)

    news_cache_key = f"news_{news_symbol}_5"
    print(f'[CryptoAPI] AI context: checking news cache key {news_cache_key}')

    news_data = None
    news_from_cache = False
    if news_cache_key in _news_cache:
        cached = _news_cache[news_cache_key]
        cache_age = now - cached['timestamp']
        print(f'[CryptoAPI] AI context: found news cache, age={cache_age:.0f}s')
        if cache_age < _NEWS_CACHE_DURATION * 6:  # Allow up to 30 min stale
            oldest_cache_age = max(oldest_cache_age, cache_age)
            news_data = cached['data']
            news_from_cache = True

    # Proactively fetch news if not cached
    if not news_data:
        print(f'[CryptoAPI] AI context: proactively fetching news for {news_symbol}')
        news_data = _fetch_news_internal(news_symbol, limit=5)
        # Fresh fetch = 0 age for news

    # Extract articles for context
    if news_data:
        articles = news_data.get('articles', [])
        context['news'] = [
            {
                'title': article.get('title', ''),
                'summary': article.get('body', ''),
                'source': article.get('source', ''),
                'published': article.get('published_on', 0)
            }
            for article in articles[:5]
        ]
        print(f'[CryptoAPI] AI context: loaded {len(context["news"])} news articles')
    else:
        print(f'[CryptoAPI] AI context: no news available for {news_symbol}')

    # Return context if we have ANY useful data (price, name, OR news)
    has_data = context.get('price') or context.get('name') or context.get('news')
    print(f'[CryptoAPI] AI context build result: price={context.get("price")}, name={context.get("name")}, news={len(context.get("news", []))} articles, returning={has_data}')
    if has_data:
        # Add data freshness info
        context['cache_age_seconds'] = round(oldest_cache_age)
        if oldest_cache_age < 30:
            context['data_freshness'] = 'live'
        elif oldest_cache_age < 120:
            context['data_freshness'] = 'recent'
        elif oldest_cache_age < 600:
            context['data_freshness'] = 'cached'
        else:
            context['data_freshness'] = 'stale'
        return context

    return None


def format_crypto_context_for_ai(context: dict) -> str:
    """
    Format the crypto context into a readable string for AI injection.
    """
    if not context:
        return ""

    lines = []
    # Build header with data freshness and source indicator
    freshness = context.get('data_freshness', 'cached').upper()
    cache_age = context.get('cache_age_seconds', 0)
    price_source = context.get('price_source', 'unknown')
    if cache_age < 60:
        age_str = f"{cache_age}s ago"
    else:
        age_str = f"{cache_age // 60}m ago"
    source_str = f" via {price_source}" if price_source != 'unknown' else ""
    lines.append(f"=== {freshness} {context.get('symbol', 'CRYPTO')} DATA ({age_str}{source_str}) ===")

    if context.get('name'):
        lines.append(f"Name: {context['name']} ({context.get('symbol', '')})")

    if context.get('price'):
        price = context['price']
        change = context.get('change_24h')
        if change is not None:
            change_str = f"+{change:.2f}%" if change >= 0 else f"{change:.2f}%"
            lines.append(f"Current Price: ${price:,.2f} ({change_str} 24h)")
        else:
            lines.append(f"Current Price: ${price:,.2f}")

    if context.get('market_cap'):
        mc = context['market_cap']
        if mc >= 1e12:
            mc_str = f"${mc/1e12:.2f}T"
        elif mc >= 1e9:
            mc_str = f"${mc/1e9:.2f}B"
        else:
            mc_str = f"${mc/1e6:.2f}M"
        lines.append(f"Market Cap: {mc_str}" + (f" (Rank #{context.get('market_cap_rank')})" if context.get('market_cap_rank') else ""))

    if context.get('volume_24h'):
        vol = context['volume_24h']
        if vol >= 1e9:
            vol_str = f"${vol/1e9:.2f}B"
        else:
            vol_str = f"${vol/1e6:.2f}M"
        lines.append(f"24h Volume: {vol_str}")

    if context.get('high_24h') and context.get('low_24h'):
        lines.append(f"24h Range: ${context['low_24h']:,.2f} - ${context['high_24h']:,.2f}")

    if context.get('ath'):
        ath_change = context.get('ath_change_percentage', 0)
        lines.append(f"All-Time High: ${context['ath']:,.2f} ({ath_change:.1f}% from ATH)")

    if context.get('news') and len(context['news']) > 0:
        lines.append("\nRecent News Headlines:")
        for i, news in enumerate(context['news'][:5], 1):
            title = news.get('title', '')
            source = news.get('source', '')
            lines.append(f"  {i}. {title} ({source})")
            if news.get('summary'):
                # Truncate summary for context
                summary = news['summary'][:150] + "..." if len(news['summary']) > 150 else news['summary']
                lines.append(f"     Summary: {summary}")

    lines.append("=" * 50)
    lines.append("Use the above data to answer the user's question. Only search the web if the question requires information not covered above.")

    return "\n".join(lines)


@crypto_bp.route('/api/crypto/news', methods=['GET'])
def get_crypto_news():
    """
    Get latest crypto news from RSS feeds (CoinDesk, CoinTelegraph).

    Query params:
        - coin: Coin symbol (btc, eth) or name (bitcoin, ethereum) - optional
        - limit: Number of articles (default: 5, max: 10)
    """
    global _news_cache

    coin_query = request.args.get('coin', '').strip().upper()
    limit = min(int(request.args.get('limit', '5')), 10)

    # Map common names to symbols
    symbol_map = {
        'BITCOIN': 'BTC', 'ETHEREUM': 'ETH', 'SOLANA': 'SOL',
        'CARDANO': 'ADA', 'DOGECOIN': 'DOGE', 'RIPPLE': 'XRP',
        'POLKADOT': 'DOT', 'CHAINLINK': 'LINK', 'LITECOIN': 'LTC',
        'AVALANCHE': 'AVAX', 'POLYGON': 'MATIC', 'UNISWAP': 'UNI',
        'COSMOS': 'ATOM', 'BINANCE': 'BNB',
    }

    if coin_query in symbol_map:
        coin_query = symbol_map[coin_query]

    cache_key = f"news_{coin_query}_{limit}"
    now = time.time()
    if cache_key in _news_cache:
        cached = _news_cache[cache_key]
        if now - cached['timestamp'] < _NEWS_CACHE_DURATION:
            return jsonify(cached['data'])

    try:
        news_items = _fetch_rss_news(coin_query, limit)

        result = {
            'success': True,
            'coin': coin_query or 'ALL',
            'count': len(news_items),
            'articles': news_items
        }

        _news_cache[cache_key] = {
            'data': result,
            'timestamp': time.time()
        }

        return jsonify(result)

    except Exception as e:
        print(f'[CryptoAPI] News error: {e}')
        return jsonify({'error': str(e)}), 500


# ==================== STOCK NEWS API (Finnhub) ==================== #
FINNHUB_BASE = 'https://finnhub.io/api/v1'
_stock_news_cache = {}
_STOCK_NEWS_CACHE_DURATION = 300  # 5 minutes cache


@crypto_bp.route('/api/stock/news', methods=['GET'])
def get_stock_news():
    """
    Get latest stock news from Finnhub API.
    Query params:
        - symbol: Stock symbol (e.g., AAPL, TSLA)
        - limit: Number of articles (default: 5)
    """
    from .config import Config

    symbol = request.args.get('symbol', '').upper()
    limit = min(int(request.args.get('limit', 5)), 20)

    if not symbol:
        return jsonify({'error': 'Symbol required'}), 400

    finnhub_key = Config.FINNHUB_API_KEY
    if not finnhub_key:
        return jsonify({'error': 'Stock news API not configured', 'success': False}), 503

    # Check cache
    cache_key = f"stock_news_{symbol}_{limit}"
    if cache_key in _stock_news_cache:
        cached, timestamp = _stock_news_cache[cache_key]
        if time.time() - timestamp < _STOCK_NEWS_CACHE_DURATION:
            print(f'[StockNews] Returning cached news for {symbol}')
            return jsonify(cached)

    try:
        # Get news from last 7 days
        from datetime import datetime, timedelta
        today = datetime.now()
        week_ago = today - timedelta(days=7)

        url = f'{FINNHUB_BASE}/company-news'
        params = {
            'symbol': symbol,
            'from': week_ago.strftime('%Y-%m-%d'),
            'to': today.strftime('%Y-%m-%d'),
            'token': finnhub_key
        }

        response = requests.get(url, params=params, timeout=10)

        if response.status_code == 200:
            news_data = response.json()

            # Format articles to match crypto news format
            articles = []
            for item in news_data[:limit]:
                articles.append({
                    'title': item.get('headline', ''),
                    'body': item.get('summary', ''),
                    'url': item.get('url', ''),
                    'source': item.get('source', 'Finnhub'),
                    'published_on': item.get('datetime', 0),
                    'imageurl': item.get('image', '')
                })

            result = {
                'success': True,
                'symbol': symbol,
                'articles': articles
            }

            # Cache result
            _stock_news_cache[cache_key] = (result, time.time())

            print(f'[StockNews] Fetched {len(articles)} articles for {symbol}')
            return jsonify(result)
        else:
            print(f'[StockNews] API error: {response.status_code}')
            return jsonify({'error': f'Finnhub API error: {response.status_code}', 'success': False}), response.status_code

    except Exception as e:
        print(f'[StockNews] Error: {e}')
        return jsonify({'error': str(e), 'success': False}), 500


# ==================== WHALE ALERT API ==================== #
WHALE_ALERT_BASE = 'https://api.whale-alert.io/v1'
WHALE_ALERT_API_KEY = 'ESCvZgdPPlrDQRz5eJXwYqAPvKKsYNIk'

# Cache for whale alerts
_whale_cache = {}
_WHALE_CACHE_DURATION = 120  # 2 minutes cache

# Minimum transaction value in USD to show (filter out smaller transactions)
WHALE_MIN_VALUE_USD = 500000  # $500K minimum (more alerts in free tier's 10-min window)

# Whale Alert currency symbols mapping (CoinGecko ID -> Whale Alert currency code)
# Whale Alert uses short symbols, not full names
COINGECKO_TO_WHALE = {
    'bitcoin': 'btc',
    'ethereum': 'eth',
    'ripple': 'xrp',
    'tether': 'usdt',
    'binancecoin': 'bnb',
    'cardano': 'ada',
    'solana': 'sol',
    'dogecoin': 'doge',
    'polkadot': 'dot',
    'matic-network': 'matic',
    'litecoin': 'ltc',
    'tron': 'trx',
    'avalanche-2': 'avax',
    'chainlink': 'link',
    'stellar': 'xlm',
    'uniswap': 'uni',
    'cosmos': 'atom',
    'monero': 'xmr',
    'bitcoin-cash': 'bch',
    'ethereum-classic': 'etc',
}


def _format_whale_owner(owner: dict) -> str:
    """Format whale transaction owner (from/to) for display."""
    owner_type = owner.get('owner_type', 'unknown')
    owner_name = owner.get('owner', '')

    if owner_type == 'exchange':
        return owner_name.title() if owner_name else 'Exchange'
    elif owner_type == 'unknown':
        return 'Unknown Wallet'
    else:
        return owner_name.title() if owner_name else owner_type.title()


def _fetch_blockchain_fallback(limit: int = 10) -> list:
    """
    Fallback: Fetch recent large Bitcoin transactions from Blockchain.info
    when Whale Alert returns empty results.
    """
    try:
        # Get recent unconfirmed transactions (mempool) - these are the freshest
        response = requests.get(
            'https://blockchain.info/unconfirmed-transactions?format=json',
            timeout=10
        )

        if response.status_code != 200:
            print(f'[BlockchainFallback] HTTP error: {response.status_code}')
            return []

        data = response.json()
        txs = data.get('txs', [])

        # Get current BTC price for USD conversion
        try:
            price_resp = requests.get('https://blockchain.info/ticker', timeout=5)
            btc_price = price_resp.json().get('USD', {}).get('last', 95000) if price_resp.status_code == 200 else 95000
        except:
            btc_price = 95000  # Fallback price

        # Filter and format large transactions (>1 BTC)
        alerts = []
        for tx in txs:
            # Calculate total output value in BTC
            total_out = sum(out.get('value', 0) for out in tx.get('out', [])) / 1e8
            total_usd = total_out * btc_price

            # Only include transactions > $100K
            if total_usd < 100000:
                continue

            # Get input addresses (from)
            inputs = tx.get('inputs', [])
            from_addr = 'Multiple Wallets' if len(inputs) > 1 else 'Wallet'

            # Get output addresses (to)
            outputs = tx.get('out', [])
            to_addr = 'Multiple Wallets' if len(outputs) > 1 else 'Wallet'

            # Format amount
            if total_out >= 1000:
                amount_str = f'{total_out:,.0f}'
            else:
                amount_str = f'{total_out:,.2f}'

            alerts.append({
                'symbol': 'BTC',
                'amount': total_out,
                'amount_formatted': amount_str,
                'amount_usd': total_usd,
                'amount_usd_formatted': _format_usd_value(total_usd),
                'from': from_addr,
                'to': to_addr,
                'from_type': 'unknown',
                'to_type': 'unknown',
                'tx_type': 'transfer',
                'emoji': '🔗',
                'hash': tx.get('hash', '')[:16] + '...',
                'full_hash': tx.get('hash', ''),
                'blockchain': 'bitcoin',
                'timestamp': tx.get('time', int(time.time())),
                'display': f"🔗 {amount_str} BTC ({_format_usd_value(total_usd)}) {from_addr} → {to_addr}",
                'source': 'blockchain.info'
            })

            if len(alerts) >= limit:
                break

        print(f'[BlockchainFallback] Found {len(alerts)} large BTC transactions')
        return alerts

    except Exception as e:
        print(f'[BlockchainFallback] Error: {e}')
        return []


def _format_usd_value(value: float) -> str:
    """Format large USD values with K/M/B suffix."""
    if value >= 1e9:
        return f'${value / 1e9:.1f}B'
    elif value >= 1e6:
        return f'${value / 1e6:.1f}M'
    elif value >= 1e3:
        return f'${value / 1e3:.0f}K'
    else:
        return f'${value:.0f}'


@crypto_bp.route('/api/crypto/whales', methods=['GET'])
def get_whale_alerts():
    """
    Get recent whale (large transaction) alerts from Whale Alert API.

    Query params:
        - coin: Coin symbol or ID (optional, default: all)
        - min_value: Minimum USD value (default: 1000000)
        - limit: Number of alerts (default: 10, max: 20)
    """
    global _whale_cache

    coin_query = request.args.get('coin', '').strip().lower()
    min_value = int(request.args.get('min_value', WHALE_MIN_VALUE_USD))
    limit = min(int(request.args.get('limit', '10')), 20)

    # Map coin to Whale Alert symbol if provided
    whale_symbol = None
    if coin_query:
        coin_id = get_coin_id(coin_query)
        if coin_id:
            whale_symbol = COINGECKO_TO_WHALE.get(coin_id)

    # Cache key based on params
    cache_key = f"whale_{whale_symbol or 'all'}_{min_value}_{limit}"
    now = time.time()

    if cache_key in _whale_cache:
        cached = _whale_cache[cache_key]
        if now - cached['timestamp'] < _WHALE_CACHE_DURATION:
            print(f'[WhaleAlert] Returning cached data for {cache_key}')
            return jsonify(cached['data'])

    try:
        # Whale Alert API - free tier only allows last 10 minutes
        # Use 10 min window to stay within free tier limits
        start_time = int(now - 600)  # 10 minutes ago (free tier limit)

        params = {
            'api_key': WHALE_ALERT_API_KEY,
            'min_value': min_value,
            'start': start_time,
        }

        # Add currency filter if specified
        if whale_symbol:
            params['currency'] = whale_symbol

        url = f'{WHALE_ALERT_BASE}/transactions'
        print(f'[WhaleAlert] Requesting: {url} with params: {params}')

        response = requests.get(
            url,
            params=params,
            headers={
                'Accept': 'application/json'
            },
            timeout=10
        )

        if response.status_code == 200:
            data = response.json()

            if data.get('result') == 'success':
                transactions = data.get('transactions', [])

                # Sort by timestamp (most recent first) and limit
                transactions.sort(key=lambda x: x.get('timestamp', 0), reverse=True)
                transactions = transactions[:limit]

                # Format for frontend
                alerts = []
                for tx in transactions:
                    symbol = tx.get('symbol', '').upper()
                    amount = tx.get('amount', 0)
                    amount_usd = tx.get('amount_usd', 0)
                    from_owner = _format_whale_owner(tx.get('from', {}))
                    to_owner = _format_whale_owner(tx.get('to', {}))
                    tx_hash = tx.get('hash', '')
                    blockchain = tx.get('blockchain', '')
                    timestamp = tx.get('timestamp', 0)

                    # Determine transaction type
                    from_type = tx.get('from', {}).get('owner_type', 'unknown')
                    to_type = tx.get('to', {}).get('owner_type', 'unknown')

                    # Skip transactions between unknown wallets (not interesting)
                    if from_type == 'unknown' and to_type == 'unknown':
                        continue

                    if from_type == 'exchange' and to_type == 'unknown':
                        tx_type = 'withdrawal'
                        emoji = '📤'
                    elif from_type == 'unknown' and to_type == 'exchange':
                        tx_type = 'deposit'
                        emoji = '📥'
                    elif from_type == 'exchange' and to_type == 'exchange':
                        tx_type = 'exchange_transfer'
                        emoji = '🔄'
                    else:
                        tx_type = 'transfer'
                        emoji = '🐋'

                    # Format amount with appropriate decimals
                    if amount >= 1000:
                        amount_str = f'{amount:,.0f}'
                    elif amount >= 1:
                        amount_str = f'{amount:,.2f}'
                    else:
                        amount_str = f'{amount:,.4f}'

                    alerts.append({
                        'symbol': symbol,
                        'amount': amount,
                        'amount_formatted': amount_str,
                        'amount_usd': amount_usd,
                        'amount_usd_formatted': _format_usd_value(amount_usd),
                        'from': from_owner,
                        'to': to_owner,
                        'from_type': from_type,
                        'to_type': to_type,
                        'tx_type': tx_type,
                        'emoji': emoji,
                        'hash': tx_hash[:16] + '...' if len(tx_hash) > 16 else tx_hash,
                        'full_hash': tx_hash,
                        'blockchain': blockchain,
                        'timestamp': timestamp,
                        # Pre-formatted display string
                        'display': f"{emoji} {amount_str} {symbol} ({_format_usd_value(amount_usd)}) {from_owner} → {to_owner}"
                    })

                # If no alerts from Whale Alert, try blockchain.info fallback
                if len(alerts) == 0:
                    print('[WhaleAlert] No alerts, trying blockchain.info fallback...')
                    alerts = _fetch_blockchain_fallback(limit)

                result = {
                    'success': True,
                    'count': len(alerts),
                    'alerts': alerts,
                    'min_value': min_value,
                    'currency_filter': whale_symbol,
                    'source': 'blockchain.info' if alerts and alerts[0].get('source') == 'blockchain.info' else 'whale-alert'
                }

                # Cache the result
                _whale_cache[cache_key] = {
                    'data': result,
                    'timestamp': now
                }

                print(f'[WhaleAlert] Fetched {len(alerts)} alerts for {whale_symbol or "all"} (source: {result["source"]})')
                return jsonify(result)

            else:
                error_msg = data.get('message', 'Unknown error')
                print(f'[WhaleAlert] API error: {error_msg}, trying fallback...')
                # Try blockchain.info fallback
                alerts = _fetch_blockchain_fallback(limit)
                if alerts:
                    return jsonify({
                        'success': True,
                        'count': len(alerts),
                        'alerts': alerts,
                        'source': 'blockchain.info'
                    })
                return jsonify({'error': error_msg}), 500

        elif response.status_code == 429:
            print('[WhaleAlert] Rate limited, trying fallback...')
            # Return cached data if available
            if cache_key in _whale_cache:
                return jsonify(_whale_cache[cache_key]['data'])
            # Try blockchain.info fallback
            alerts = _fetch_blockchain_fallback(limit)
            if alerts:
                return jsonify({
                    'success': True,
                    'count': len(alerts),
                    'alerts': alerts,
                    'source': 'blockchain.info'
                })
            return jsonify({'error': 'Rate limited, please try again later'}), 429

        else:
            print(f'[WhaleAlert] HTTP error: {response.status_code}, trying fallback...')
            # Try blockchain.info fallback
            alerts = _fetch_blockchain_fallback(limit)
            if alerts:
                return jsonify({
                    'success': True,
                    'count': len(alerts),
                    'alerts': alerts,
                    'source': 'blockchain.info'
                })
            return jsonify({'error': f'API error: {response.status_code}'}), 500

    except Exception as e:
        print(f'[WhaleAlert] Error: {e}, trying fallback...')
        # Return cached data on error
        if cache_key in _whale_cache:
            return jsonify(_whale_cache[cache_key]['data'])
        # Try blockchain.info fallback
        alerts = _fetch_blockchain_fallback(limit)
        if alerts:
            return jsonify({
                'success': True,
                'count': len(alerts),
                'alerts': alerts,
                'source': 'blockchain.info'
            })
        return jsonify({'error': str(e)}), 500


# ==================== FEAR & GREED INDEX ==================== #
FEAR_GREED_API = 'https://api.alternative.me/fng/'
_fear_greed_cache = {'data': None, 'timestamp': 0}
_FEAR_GREED_CACHE_DURATION = 300  # 5 minutes


@crypto_bp.route('/api/crypto/fear-greed', methods=['GET'])
def get_fear_greed():
    """Get the current Fear & Greed Index for crypto market."""
    global _fear_greed_cache
    now = time.time()

    # Check cache
    if _fear_greed_cache['data'] and now - _fear_greed_cache['timestamp'] < _FEAR_GREED_CACHE_DURATION:
        return jsonify(_fear_greed_cache['data'])

    try:
        response = requests.get(FEAR_GREED_API, timeout=10)

        if response.status_code == 200:
            data = response.json()
            fng_data = data.get('data', [{}])[0]

            value = int(fng_data.get('value', 50))
            classification = fng_data.get('value_classification', 'Neutral')

            # Determine color based on value
            if value <= 24:
                color = '#ef4444'  # Red - Extreme Fear
                emoji = '🔴'
            elif value <= 44:
                color = '#f97316'  # Orange - Fear
                emoji = '🟠'
            elif value <= 55:
                color = '#eab308'  # Yellow - Neutral
                emoji = '🟡'
            elif value <= 74:
                color = '#84cc16'  # Light green - Greed
                emoji = '🟢'
            else:
                color = '#22c55e'  # Green - Extreme Greed
                emoji = '🟢'

            result = {
                'success': True,
                'value': value,
                'classification': classification,
                'color': color,
                'emoji': emoji,
                'timestamp': int(fng_data.get('timestamp', now)),
                'next_update': int(fng_data.get('time_until_update', 0))
            }

            _fear_greed_cache = {'data': result, 'timestamp': now}
            print(f'[FearGreed] Fetched: {value} ({classification})')
            return jsonify(result)

        else:
            print(f'[FearGreed] HTTP error: {response.status_code}')
            return jsonify({'success': False, 'error': 'API error'}), 500

    except Exception as e:
        print(f'[FearGreed] Error: {e}')
        # Return cached data if available
        if _fear_greed_cache['data']:
            return jsonify(_fear_greed_cache['data'])
        return jsonify({'success': False, 'error': str(e)}), 500


@crypto_bp.route('/api/crypto/chart', methods=['GET'])
def get_crypto_chart():
    """
    Get historical price data for charting.

    Query params:
        - coin: Coin symbol or ID
        - days: Time range (1, 7, 30, 90, 365, max)
        - currency: Target currency (default: usd)
        - before: Optional Unix-ms timestamp; when set, returns one
          historical chunk of klines ending at this time (used for the
          lazy-load-on-pan history extension on every timeframe).
    """
    global _chart_cache

    coin_query = request.args.get('coin', '').strip()
    days = request.args.get('days', '1')
    currency = request.args.get('currency', 'usd').lower()
    before_raw = request.args.get('before', '').strip()

    if not coin_query:
        return jsonify({'error': 'Missing coin parameter'}), 400

    coin_id = get_coin_id(coin_query)
    if not coin_id:
        return jsonify({'error': f'Unknown cryptocurrency: {coin_query}'}), 404

    # Validate days parameter - accept numeric strings and 'max'
    # CoinGecko accepts any number of days, so we just validate it's a valid input
    if days != 'max':
        try:
            days_int = int(days)
            if days_int < 1:
                days = '1'
        except (ValueError, TypeError):
            days = '1'

    # Lazy-load historical chunk path: when 'before' is set, skip
    # CoinGecko (free tier doesn't accept arbitrary endTime) and go
    # straight to Binance with that endTime. The interval still derives
    # from `days`, so each timeframe paginates at its own resolution.
    if before_raw:
        try:
            before_ms = int(before_raw)
            if before_ms <= 0:
                raise ValueError
        except (ValueError, TypeError):
            return jsonify({'error': 'Invalid before parameter'}), 400

        chunk_cache_key = f"{coin_id}_{days}_{currency}_before_{before_ms}"
        cached = _chart_cache_get(chunk_cache_key)
        if cached:
            cache_age = time.time() - cached['timestamp']
            result = cached['data'].copy()
            result['data_source'] = 'cache'
            result['cache_age_seconds'] = round(cache_age)
            return jsonify(result)

        result = _fetch_chart_from_binance(coin_id, days, before_ms=before_ms)
        if result:
            _chart_cache_set(chunk_cache_key, result)
            return jsonify(result)
        return jsonify({'error': 'Failed to fetch historical chunk'}), 502

    # Check cache first (Redis-backed; survives Flask restarts and is
    # shared across gunicorn workers — eliminates the cold-start race
    # that previously returned 'Unknown cryptocurrency: vechain' until
    # /coins/list warmed in each worker independently).
    cache_key = f"{coin_id}_{days}_{currency}"
    now = time.time()
    cached = _chart_cache_get(cache_key)
    if cached:
        cache_age = now - cached['timestamp']
        if cache_age < _CHART_CACHE_DURATION:
            print(f'[CryptoAPI] Returning cached chart data for {cache_key}')
            result = cached['data'].copy()
            result['data_source'] = 'cache'
            result['cache_age_seconds'] = round(cache_age)
            return jsonify(result)

    # Prefer Binance for any coin with a Binance mapping. CoinGecko's
    # total_volumes is rolling-24h cumulative, NOT per-bar volume —
    # which makes the volume histogram on 1D / 1W / 1M look uniform
    # ('ugly equal bars'). Binance klines return true per-bar volume
    # with natural variance. CoinGecko remains the fallback for coins
    # not listed on Binance.
    if currency == 'usd' and coin_id in COINGECKO_TO_BINANCE:
        binance_first = _fetch_chart_from_binance(coin_id, days)
        if binance_first:
            _chart_cache_set(cache_key, binance_first)
            return jsonify(binance_first)
        # Fall through to CoinGecko if Binance failed.
        print(f'[CryptoAPI] Binance preferred path failed for {coin_id}, trying CoinGecko')

    try:
        # Determine interval - use 'daily' for ranges > 1 day (reduces data points)
        interval = None
        if days != 'max':
            try:
                if int(days) > 1:
                    interval = 'daily'
            except (ValueError, TypeError):
                pass

        params = {
            'vs_currency': currency,
            'days': days,
        }
        # Only add interval if it's set (CoinGecko doesn't like null values)
        if interval:
            params['interval'] = interval

        response = requests.get(
            f'{COINGECKO_BASE}/coins/{coin_id}/market_chart',
            params=params,
            headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Accept': 'application/json'
            },
            timeout=15
        )

        if response.status_code == 200:
            data = response.json()
            prices = data.get('prices', [])
            volumes = data.get('total_volumes', [])
            # CoinGecko returns total_volumes as parallel array of
            # [timestamp_ms, volume_usd]. Index by timestamp for fast lookup
            # so the volume entry matches each price's exact timestamp.
            volume_by_ts = {int(v[0] / 1000): v[1] for v in volumes} if volumes else {}

            # Format for Lightweight Charts: [{time, value}, ...] for price
            # series + parallel volume_data array for the histogram pane.
            chart_data = []
            volume_data = []
            for i, point in enumerate(prices):
                timestamp_ms, price = point
                ts = int(timestamp_ms / 1000)
                chart_data.append({'time': ts, 'value': price})
                vol = volume_by_ts.get(ts)
                if vol is not None:
                    # Color volume bar by per-bar price direction (vs the
                    # previous bar). Green on up-move, red on down.
                    prev_price = prices[i - 1][1] if i > 0 else price
                    color = 'rgba(34, 197, 94, 0.4)' if price >= prev_price else 'rgba(239, 68, 68, 0.4)'
                    volume_data.append({'time': ts, 'value': vol, 'color': color})

            # Calculate price change
            if len(chart_data) >= 2:
                first_price = chart_data[0]['value']
                last_price = chart_data[-1]['value']
                change_pct = ((last_price - first_price) / first_price) * 100
            else:
                change_pct = 0

            result = {
                'success': True,
                'coin_id': coin_id,
                'symbol': get_ticker_for_coin(coin_id),
                'currency': currency.upper(),
                'days': days,
                'data': chart_data,
                'volume_data': volume_data,
                'current_price': chart_data[-1]['value'] if chart_data else 0,
                'change_percent': round(change_pct, 2),
                'is_positive': change_pct >= 0,
                'data_source': 'live'
            }

            # Cache the result (Redis)
            _chart_cache_set(cache_key, result)

            return jsonify(result)

        # Rate limited or error - try stale cache first, then Binance fallback
        if response.status_code == 429 or response.status_code >= 400:
            print(f'[CryptoAPI] CoinGecko chart error {response.status_code}, checking stale cache...')

            # Try stale cache first (Redis returns it as long as TTL window holds)
            stale = _chart_cache_get(cache_key)
            if stale:
                cache_age = now - stale['timestamp']
                result = stale['data'].copy()
                result['data_source'] = 'stale_cache'
                result['cache_age_seconds'] = round(cache_age)
                print(f'[CryptoAPI] Returning stale cache for {cache_key} (age: {round(cache_age)}s)')
                return jsonify(result)

            # No cache - try Binance fallback
            print(f'[CryptoAPI] No cache, trying Binance fallback for {coin_id}...')
            binance_result = _fetch_chart_from_binance(coin_id, days)
            if binance_result:
                _chart_cache_set(cache_key, binance_result)
                return jsonify(binance_result)

        # Log the actual error from CoinGecko
        print(f'[CryptoAPI] CoinGecko error: status={response.status_code}, body={response.text[:500]}')
        return jsonify({'error': f'CoinGecko API error: {response.status_code}', 'rate_limited': response.status_code == 429}), 500

    except Exception as e:
        print(f'[CryptoAPI] Chart error: {e}')
        # On exception, try stale cache first (Redis-backed)
        stale = _chart_cache_get(cache_key)
        if stale:
            cache_age = now - stale['timestamp']
            result = stale['data'].copy()
            result['data_source'] = 'stale_cache'
            result['cache_age_seconds'] = round(cache_age)
            return jsonify(result)

        # No cache - try Binance fallback
        print(f'[CryptoAPI] Exception occurred, trying Binance fallback for {coin_id}...')
        binance_result = _fetch_chart_from_binance(coin_id, days)
        if binance_result:
            _chart_cache_set(cache_key, binance_result)
            return jsonify(binance_result)

        return jsonify({'error': str(e)}), 500


@crypto_bp.route('/api/crypto/search', methods=['GET'])
def search_crypto():
    """
    Search for cryptocurrencies by name or symbol.

    Query params:
        - q: Search query
    """
    query = request.args.get('q', '').strip()

    if not query or len(query) < 2:
        return jsonify({'error': 'Query too short'}), 400

    try:
        response = requests.get(
            f'{COINGECKO_BASE}/search',
            params={'query': query},
            headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Accept': 'application/json'
            },
            timeout=10
        )

        if response.status_code == 200:
            data = response.json()
            coins = data.get('coins', [])[:10]  # Limit to 10 results

            results = []
            for coin in coins:
                results.append({
                    'id': coin.get('id'),
                    'symbol': coin.get('symbol', '').upper(),
                    'name': coin.get('name'),
                    'thumb': coin.get('thumb'),
                    'market_cap_rank': coin.get('market_cap_rank')
                })

            return jsonify({
                'success': True,
                'results': results
            })

        return jsonify({'error': 'Search failed'}), 500

    except Exception as e:
        print(f'[CryptoAPI] Search error: {e}')
        return jsonify({'error': str(e)}), 500


@crypto_bp.route('/api/crypto/trending', methods=['GET'])
def get_trending():
    """Get trending cryptocurrencies."""
    try:
        response = requests.get(
            f'{COINGECKO_BASE}/search/trending',
            headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Accept': 'application/json'
            },
            timeout=10
        )

        if response.status_code == 200:
            data = response.json()
            coins = data.get('coins', [])[:7]

            results = []
            for item in coins:
                coin = item.get('item', {})
                results.append({
                    'id': coin.get('id'),
                    'symbol': coin.get('symbol', '').upper(),
                    'name': coin.get('name'),
                    'thumb': coin.get('thumb'),
                    'market_cap_rank': coin.get('market_cap_rank'),
                    'price_btc': coin.get('price_btc')
                })

            return jsonify({
                'success': True,
                'trending': results
            })

        return jsonify({'error': 'Failed to fetch trending'}), 500

    except Exception as e:
        print(f'[CryptoAPI] Trending error: {e}')
        return jsonify({'error': str(e)}), 500


# ==================== STOCK API ENDPOINTS ==================== #

# Popular stock symbols for quick lookup
POPULAR_STOCKS = {
    'aapl': 'Apple Inc.',
    'msft': 'Microsoft Corporation',
    'googl': 'Alphabet Inc.',
    'goog': 'Alphabet Inc.',
    'amzn': 'Amazon.com Inc.',
    'meta': 'Meta Platforms Inc.',
    'tsla': 'Tesla Inc.',
    'nvda': 'NVIDIA Corporation',
    'amd': 'Advanced Micro Devices',
    'intc': 'Intel Corporation',
    'nflx': 'Netflix Inc.',
    'dis': 'The Walt Disney Company',
    'pypl': 'PayPal Holdings',
    'v': 'Visa Inc.',
    'ma': 'Mastercard Inc.',
    'jpm': 'JPMorgan Chase',
    'bac': 'Bank of America',
    'wmt': 'Walmart Inc.',
    'ko': 'Coca-Cola Company',
    'pep': 'PepsiCo Inc.',
    'mcd': "McDonald's Corporation",
    'sbux': 'Starbucks Corporation',
    'cost': 'Costco Wholesale',
    'hd': 'Home Depot',
    'crm': 'Salesforce Inc.',
    'adbe': 'Adobe Inc.',
    'orcl': 'Oracle Corporation',
    'csco': 'Cisco Systems',
    'ibm': 'IBM Corporation',
    'uber': 'Uber Technologies',
    'lyft': 'Lyft Inc.',
    'snap': 'Snap Inc.',
    'twtr': 'Twitter Inc.',
    'sq': 'Block Inc.',
    'shop': 'Shopify Inc.',
    'spot': 'Spotify Technology',
    'zoom': 'Zoom Video',
    'pltr': 'Palantir Technologies',
    'coin': 'Coinbase Global',
    'hood': 'Robinhood Markets',
    'spy': 'SPDR S&P 500 ETF',
    'qqq': 'Invesco QQQ Trust',
    'voo': 'Vanguard S&P 500 ETF',
    'iwm': 'iShares Russell 2000',
    'dia': 'SPDR Dow Jones ETF',
    'arkk': 'ARK Innovation ETF'
}


def is_valid_stock_symbol(symbol: str) -> bool:
    """Check if a symbol looks like a valid stock ticker."""
    if not symbol:
        return False
    symbol = symbol.upper()
    # Stock symbols are typically 1-5 uppercase letters
    if len(symbol) < 1 or len(symbol) > 5:
        return False
    if not symbol.isalpha():
        return False
    return True


@crypto_bp.route('/api/stock/price', methods=['GET'])
def get_stock_price():
    """
    Get current price and daily change for a stock.
    Uses Yahoo Finance API.

    Query params:
        - symbol: Stock ticker symbol (e.g., AAPL, MSFT)
    """
    symbol = request.args.get('symbol', '').strip().upper()

    if not symbol:
        return jsonify({'error': 'Missing symbol parameter'}), 400

    if not is_valid_stock_symbol(symbol):
        return jsonify({'error': f'Invalid stock symbol: {symbol}'}), 400

    try:
        # Yahoo Finance API endpoint
        url = f'https://query1.finance.yahoo.com/v8/finance/chart/{symbol}'
        params = {
            'interval': '1d',
            'range': '1d'
        }

        response = requests.get(url, params=params, timeout=10, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })

        if response.status_code == 200:
            data = response.json()
            result = data.get('chart', {}).get('result', [])

            if not result:
                return jsonify({'error': f'Stock not found: {symbol}'}), 404

            quote = result[0]
            meta = quote.get('meta', {})
            indicators = quote.get('indicators', {}).get('quote', [{}])[0]

            current_price = meta.get('regularMarketPrice', 0)
            previous_close = meta.get('previousClose', current_price)
            change = current_price - previous_close
            change_pct = (change / previous_close * 100) if previous_close else 0

            # Pull richer fields directly from Yahoo's meta object.
            # All optional — frontend handles missing/null gracefully.
            day_high = meta.get('regularMarketDayHigh')
            day_low = meta.get('regularMarketDayLow')
            fifty_two_high = meta.get('fiftyTwoWeekHigh')
            fifty_two_low = meta.get('fiftyTwoWeekLow')
            volume = meta.get('regularMarketVolume')
            day_open = meta.get('regularMarketOpen')

            # market_cap, P/E ratio, EPS, dividend yield aren't in Yahoo's
            # chart meta — fetch via yfinance (handles Yahoo's crumb auth
            # automatically). Wrapped in try/except so a yfinance failure
            # doesn't break the price endpoint. Cached in Redis for 1h
            # since these are slow-moving fundamentals (P/E, EPS only
            # change quarterly; market_cap drifts with price but the
            # 1h refresh window is fine for chart context).
            market_cap = meta.get('marketCap')
            pe_ratio = None
            eps = None
            dividend_yield = None
            if _YF_AVAILABLE:
                from app.redis_client import cache_get, cache_set
                extras_key = f'stock:extras:{symbol}'
                extras = cache_get(extras_key)
                if not extras:
                    try:
                        ticker = _yf.Ticker(symbol)
                        # fast_info is sub-second; .info is slower but has
                        # the fundamentals. Pulling both — .info covers
                        # all, fast_info as backup for market_cap if .info
                        # times out.
                        info = ticker.info
                        extras = {
                            'market_cap': info.get('marketCap'),
                            'pe_ratio': info.get('trailingPE'),
                            'eps': info.get('trailingEps'),
                            'dividend_yield': info.get('dividendYield'),
                        }
                        cache_set(extras_key, extras, 3600)
                    except Exception as yf_err:
                        print(f'[StockAPI] yfinance .info fetch failed for {symbol}: {yf_err}')
                        # Try fast_info as a quick fallback for market_cap only
                        try:
                            fast_info = _yf.Ticker(symbol).fast_info
                            extras = {
                                'market_cap': getattr(fast_info, 'market_cap', None),
                                'pe_ratio': None,
                                'eps': None,
                                'dividend_yield': None,
                            }
                        except Exception:
                            extras = {}
                if extras:
                    market_cap = extras.get('market_cap') or market_cap
                    pe_ratio = extras.get('pe_ratio')
                    eps = extras.get('eps')
                    dividend_yield = extras.get('dividend_yield')

            return jsonify({
                'success': True,
                'symbol': symbol,
                'name': POPULAR_STOCKS.get(symbol.lower(), meta.get('shortName', symbol)),
                'price': round(current_price, 2),
                'change': round(change, 2),
                'change_percent': round(change_pct, 2),
                'is_positive': change >= 0,
                'currency': meta.get('currency', 'USD'),
                'exchange': meta.get('exchangeName', 'Unknown'),
                'market_state': meta.get('marketState', 'CLOSED'),
                'day_high': round(day_high, 2) if day_high else None,
                'day_low': round(day_low, 2) if day_low else None,
                'day_open': round(day_open, 2) if day_open else None,
                'previous_close': round(previous_close, 2) if previous_close else None,
                'fifty_two_week_high': round(fifty_two_high, 2) if fifty_two_high else None,
                'fifty_two_week_low': round(fifty_two_low, 2) if fifty_two_low else None,
                'volume': volume,
                'market_cap': market_cap,
                'pe_ratio': round(pe_ratio, 2) if pe_ratio else None,
                'eps': round(eps, 2) if eps else None,
                'dividend_yield': round(dividend_yield * 100, 2) if dividend_yield else None,
            })

        return jsonify({'error': 'Failed to fetch stock price'}), 500

    except Exception as e:
        print(f'[StockAPI] Price error: {e}')
        return jsonify({'error': str(e)}), 500


# Per-interval pagination window for stocks. Each value is the seconds
# of history we ask Yahoo for in one historical chunk; chosen to stay
# within Yahoo's interval-vs-range constraints (intraday intervals are
# capped — 5m/15m/30m: 60 days max; 1h: 730 days max; daily and coarser
# are unconstrained). The numbers below are conservative so a single
# pagination request never trips Yahoo's "Data doesn't exist for
# startDate" error.
_STOCK_PAGE_WINDOW_SECONDS = {
    '5m':  30 * 86400,        # 30 days/page
    '15m': 60 * 86400,        # 60 days (Yahoo's hard cap for 15m)
    '30m': 60 * 86400,
    '1h':  365 * 86400,       # 1 year/page
    '60m': 365 * 86400,
    '1d':  5 * 365 * 86400,   # 5 years/page
    '1wk': 10 * 365 * 86400,  # 10 years/page
    '1mo': 30 * 365 * 86400,  # 30 years/page (covers most listed equities)
}


@crypto_bp.route('/api/stock/chart', methods=['GET'])
def get_stock_chart():
    """
    Get historical price data for stock charting.
    Uses Yahoo Finance API.

    Query params:
        - symbol: Stock ticker symbol
        - range: Time range (1d, 5d, 1mo, 3mo, 6mo, 1y, 5y, max)
        - before: Optional Unix-ms timestamp; when set, returns one
          historical chunk of bars ending at this time. Interval is
          derived from `range` so each timeframe paginates at its own
          resolution (parallels the crypto endpoint's contract).
    """
    symbol = request.args.get('symbol', '').strip().upper()
    range_param = request.args.get('range', '1d')
    before_raw = request.args.get('before', '').strip()

    if not symbol:
        return jsonify({'error': 'Missing symbol parameter'}), 400

    if not is_valid_stock_symbol(symbol):
        return jsonify({'error': f'Invalid stock symbol: {symbol}'}), 400

    # Map range to Yahoo Finance params
    range_config = {
        '1d': {'range': '1d', 'interval': '5m'},
        '5d': {'range': '5d', 'interval': '15m'},
        '1mo': {'range': '1mo', 'interval': '1h'},
        '3mo': {'range': '3mo', 'interval': '1d'},
        '6mo': {'range': '6mo', 'interval': '1d'},
        '1y': {'range': '1y', 'interval': '1d'},
        '5y': {'range': '5y', 'interval': '1wk'},
        'max': {'range': 'max', 'interval': '1mo'},
        'ytd': {'range': 'ytd', 'interval': '1d'},
        # Map from crypto-style ranges
        '1D': {'range': '1d', 'interval': '5m'},
        '5D': {'range': '5d', 'interval': '15m'},
        '1M': {'range': '1mo', 'interval': '1h'},
        '6M': {'range': '6mo', 'interval': '1d'},
        'YTD': {'range': 'ytd', 'interval': '1d'},
        '1Y': {'range': '1y', 'interval': '1d'},
        '5Y': {'range': '5y', 'interval': '1wk'},
        'MAX': {'range': 'max', 'interval': '1mo'},
    }

    config = range_config.get(range_param, range_config['1d'])

    # Lazy-load historical chunk path: when `before` is set, fetch one
    # window of older bars ending at that timestamp. Uses Yahoo's
    # period1/period2 (epoch seconds) instead of the range/interval
    # shortcut, since `range` doesn't allow arbitrary endpoints.
    if before_raw:
        try:
            before_ms = int(before_raw)
            if before_ms <= 0:
                raise ValueError
        except (ValueError, TypeError):
            return jsonify({'error': 'Invalid before parameter'}), 400

        interval = config['interval']
        window_seconds = _STOCK_PAGE_WINDOW_SECONDS.get(interval, 365 * 86400)
        period2 = before_ms // 1000
        period1 = max(0, period2 - window_seconds)

        try:
            url = f'https://query1.finance.yahoo.com/v8/finance/chart/{symbol}'
            params = {
                'interval': interval,
                'period1': period1,
                'period2': period2,
            }
            response = requests.get(url, params=params, timeout=15, headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            })

            if response.status_code != 200:
                # Yahoo returns 400 ("Data doesn't exist for startDate")
                # or 422 when period1 is past the symbol's IPO/listing
                # date. That's a clean "nothing older" signal, not an
                # error — tell the frontend to stop paginating.
                if response.status_code in (400, 422):
                    return jsonify({
                        'success': True,
                        'symbol': symbol,
                        'range': range_param,
                        'data': [],
                        'volume_data': [],
                        'history_exhausted': True,
                        'asset_type': 'stock',
                    })
                print(f'[StockAPI] Chart history error: {response.status_code}')
                return jsonify({'error': 'Failed to fetch historical chunk'}), 502

            data = response.json()
            result = data.get('chart', {}).get('result', [])
            if not result:
                return jsonify({
                    'success': True,
                    'symbol': symbol,
                    'range': range_param,
                    'data': [],
                    'volume_data': [],
                    'history_exhausted': True,
                    'asset_type': 'stock',
                })

            quote = result[0]
            timestamps = quote.get('timestamp') or []
            indicators = quote.get('indicators', {}).get('quote', [{}])[0]
            closes = indicators.get('close') or []

            chart_data = []
            for i, ts in enumerate(timestamps):
                if i < len(closes) and closes[i] is not None:
                    chart_data.append({'time': ts, 'value': round(closes[i], 2)})

            # If Yahoo returned no usable bars in this window, we've
            # walked past the listing date — stop paginating.
            return jsonify({
                'success': True,
                'symbol': symbol,
                'range': range_param,
                'data': chart_data,
                'volume_data': [],
                'history_exhausted': len(chart_data) == 0,
                'asset_type': 'stock',
            })
        except Exception as e:
            print(f'[StockAPI] History chunk error: {e}')
            return jsonify({'error': str(e)}), 500

    try:
        url = f'https://query1.finance.yahoo.com/v8/finance/chart/{symbol}'
        params = {
            'interval': config['interval'],
            'range': config['range']
        }

        response = requests.get(url, params=params, timeout=15, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })

        if response.status_code == 200:
            data = response.json()
            result = data.get('chart', {}).get('result', [])

            if not result:
                return jsonify({'error': f'Stock not found: {symbol}'}), 404

            quote = result[0]
            meta = quote.get('meta', {})
            timestamps = quote.get('timestamp', [])
            indicators = quote.get('indicators', {}).get('quote', [{}])[0]
            closes = indicators.get('close', [])

            # Format for Lightweight Charts
            chart_data = []
            for i, ts in enumerate(timestamps):
                if i < len(closes) and closes[i] is not None:
                    chart_data.append({
                        'time': ts,
                        'value': round(closes[i], 2)
                    })

            # Calculate change
            if len(chart_data) >= 2:
                first_price = chart_data[0]['value']
                last_price = chart_data[-1]['value']
                change_pct = ((last_price - first_price) / first_price) * 100
            else:
                change_pct = 0

            return jsonify({
                'success': True,
                'symbol': symbol,
                'name': POPULAR_STOCKS.get(symbol.lower(), meta.get('shortName', symbol)),
                'currency': meta.get('currency', 'USD'),
                'range': range_param,
                'data': chart_data,
                'current_price': chart_data[-1]['value'] if chart_data else 0,
                'change_percent': round(change_pct, 2),
                'is_positive': change_pct >= 0,
                'asset_type': 'stock'
            })

        return jsonify({'error': 'Failed to fetch chart data'}), 500

    except Exception as e:
        print(f'[StockAPI] Chart error: {e}')
        return jsonify({'error': str(e)}), 500


@crypto_bp.route('/api/stock/search', methods=['GET'])
def search_stocks():
    """
    Search for stocks by name or symbol.
    Uses Yahoo Finance API.

    Query params:
        - q: Search query
    """
    query = request.args.get('q', '').strip()

    if not query or len(query) < 1:
        return jsonify({'error': 'Query too short'}), 400

    try:
        url = 'https://query1.finance.yahoo.com/v1/finance/search'
        params = {
            'q': query,
            'quotesCount': 10,
            'newsCount': 0
        }

        response = requests.get(url, params=params, timeout=10, headers={
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })

        if response.status_code == 200:
            data = response.json()
            quotes = data.get('quotes', [])

            results = []
            for quote in quotes:
                # Filter to stocks/ETFs only
                quote_type = quote.get('quoteType', '')
                if quote_type in ['EQUITY', 'ETF']:
                    results.append({
                        'symbol': quote.get('symbol', ''),
                        'name': quote.get('shortname') or quote.get('longname', ''),
                        'exchange': quote.get('exchange', ''),
                        'type': quote_type
                    })

            return jsonify({
                'success': True,
                'results': results[:10]
            })

        return jsonify({'error': 'Search failed'}), 500

    except Exception as e:
        print(f'[StockAPI] Search error: {e}')
        return jsonify({'error': str(e)}), 500


# ==================== CONFIG ENDPOINT ==================== #

# Stock logos - symbol to company domain mapping for Clearbit
STOCK_LOGOS = {
    'AAPL': 'apple.com',
    'MSFT': 'microsoft.com',
    'GOOGL': 'google.com',
    'GOOG': 'google.com',
    'AMZN': 'amazon.com',
    'META': 'facebook.com',
    'TSLA': 'tesla.com',
    'NVDA': 'nvidia.com',
    'AMD': 'amd.com',
    'INTC': 'intel.com',
    'NFLX': 'netflix.com',
    'DIS': 'thewaltdisneycompany.com',
    'PYPL': 'paypal.com',
    'V': 'visa.com',
    'MA': 'mastercard.com',
    'JPM': 'jpmorganchase.com',
    'BAC': 'bankofamerica.com',
    'WMT': 'walmart.com',
    'KO': 'coca-colacompany.com',
    'PEP': 'pepsico.com',
    'MCD': 'mcdonalds.com',
    'SBUX': 'starbucks.com',
    'COST': 'costco.com',
    'HD': 'homedepot.com',
    'CRM': 'salesforce.com',
    'ADBE': 'adobe.com',
    'ORCL': 'oracle.com',
    'CSCO': 'cisco.com',
    'IBM': 'ibm.com',
    'UBER': 'uber.com',
    'LYFT': 'lyft.com',
    'SNAP': 'snap.com',
    'SPOT': 'spotify.com',
    'PLTR': 'palantir.com',
    'COIN': 'coinbase.com',
    'HOOD': 'robinhood.com',
    'SQ': 'squareup.com',
    'SHOP': 'shopify.com',
    'ZM': 'zoom.us',
}

# Time ranges for charts
TIME_RANGES = {
    '1D': {'days': '1', 'label': '1D'},
    '5D': {'days': '5', 'label': '5D'},
    '1M': {'days': '30', 'label': '1M'},
    '6M': {'days': '180', 'label': '6M'},
    'YTD': {'days': 'ytd', 'label': 'YTD'},
    '1Y': {'days': '365', 'label': '1Y'},
    '5Y': {'days': '1825', 'label': '5Y'},
    'MAX': {'days': 'max', 'label': 'MAX'},
}

# Config cache
_config_cache = {'data': None, 'timestamp': 0}
_CONFIG_CACHE_DURATION = 3600  # 1 hour


@crypto_bp.route('/api/crypto/config', methods=['GET'])
def get_chart_config():
    """
    Get chart configuration for the frontend.
    Crypto logos are fetched dynamically from /api/crypto/details.

    Returns:
        - stock_logos: Mapping of stock symbols to company domains
        - time_ranges: Available time range options
        - asset_names: Display names for assets
        - crypto_tickers: Ticker symbols for top coins
    """
    global _config_cache

    now = time.time()
    if _config_cache['data'] and (now - _config_cache['timestamp']) < _CONFIG_CACHE_DURATION:
        return jsonify(_config_cache['data'])

    # Get centralized asset config
    asset_cfg = get_frontend_config()

    config = {
        'success': True,
        'stock_logos': STOCK_LOGOS,
        'time_ranges': TIME_RANGES,
        # Field name kept as 'clearbit_base' for frontend compat, but
        # Clearbit's free Logo API was sunset late 2024. Pointing at
        # Google's favicon service which appends ?sz=128&domain=X.
        # Frontend appends the domain to this base URL.
        'clearbit_base': 'https://www.google.com/s2/favicons?sz=128&domain=',
        # Centralized asset mappings
        'asset_names': asset_cfg['asset_names'],
        'crypto_tickers': asset_cfg['crypto_tickers'],
    }

    _config_cache = {
        'data': config,
        'timestamp': now
    }

    return jsonify(config)
