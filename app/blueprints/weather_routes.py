"""Weather routes blueprint.

Routes:
- GET /weather-test - Test page for weather animations
- GET /weather - Get weather based on geolocation or IP
"""

import logging

import requests
from flask import Blueprint, jsonify, render_template, request

from app.limiter import limiter
from app.blueprints.shared import get_client_ip

logger = logging.getLogger(__name__)

weather_bp = Blueprint('weather', __name__)


@weather_bp.route('/weather-test', methods=['GET'])
def weather_test():
    """Test page for weather Lottie animations."""
    return render_template('weather-test.html')


@weather_bp.route('/weather', methods=['GET'])
@limiter.limit("30 per minute")
def get_weather():
    """Get weather based on browser geolocation or client IP. No auth required."""
    try:
        # Check if coordinates provided (browser geolocation)
        provided_lat = request.args.get('lat')
        provided_lon = request.args.get('lon')

        if provided_lat and provided_lon:
            # Use browser-provided coordinates
            lat = float(provided_lat)
            lon = float(provided_lon)
            # Reverse geocode to get city name
            try:
                geo_response = requests.get(
                    f'https://nominatim.openstreetmap.org/reverse?lat={lat}&lon={lon}&format=json',
                    headers={'User-Agent': 'WeatherApp/1.0'},
                    timeout=5
                )
                geo_data = geo_response.json()
                city = geo_data.get('address', {}).get('city') or \
                       geo_data.get('address', {}).get('town') or \
                       geo_data.get('address', {}).get('village') or \
                       'Unknown'
            except:
                city = 'Unknown'
            client_ip = 'browser-geo'
        else:
            # Get client IP
            client_ip = get_client_ip()

            # Skip for localhost/private IPs - use default location
            if client_ip in ('127.0.0.1', '0.0.0.0') or client_ip.startswith('192.168.') or client_ip.startswith('10.'):
                # Default to a neutral location for development
                lat, lon, city = 40.7128, -74.0060, 'New York'
            else:
                # Get location from IP
                try:
                    # Check if IPv6 (contains colons)
                    is_ipv6 = ':' in client_ip

                    if is_ipv6:
                        # Use ipinfo.io for IPv6 (better accuracy)
                        ip_response = requests.get(
                            f'https://ipinfo.io/{client_ip}/json',
                            timeout=5
                        )
                        ip_data = ip_response.json()
                        if ip_data.get('loc'):
                            loc_parts = ip_data.get('loc').split(',')
                            lat = float(loc_parts[0])
                            lon = float(loc_parts[1])
                            city = ip_data.get('city', 'Unknown')
                        else:
                            return jsonify({'error': 'Could not determine location'}), 400
                    else:
                        # Use ip-api.com for IPv4
                        ip_response = requests.get(
                            f'http://ip-api.com/json/{client_ip}?fields=status,lat,lon,city',
                            timeout=5
                        )
                        ip_data = ip_response.json()
                        if ip_data.get('status') == 'success':
                            lat = ip_data.get('lat')
                            lon = ip_data.get('lon')
                            city = ip_data.get('city', 'Unknown')
                        else:
                            return jsonify({'error': 'Could not determine location'}), 400
                except Exception as e:
                    logger.warning(f"IP geolocation failed: {e}")
                    return jsonify({'error': 'Location service unavailable'}), 503

        # Fetch weather from Open-Meteo
        try:
            weather_response = requests.get(
                f'https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current=weather_code,temperature_2m&timezone=auto',
                timeout=5
            )
            weather_data = weather_response.json()

            return jsonify({
                'weather_code': weather_data.get('current', {}).get('weather_code'),
                'temperature': weather_data.get('current', {}).get('temperature_2m'),
                'city': city,
                'lat': lat,
                'lon': lon
            })
        except Exception as e:
            logger.warning(f"Weather API failed: {e}")
            return jsonify({'error': 'Weather service unavailable'}), 503

    except Exception as e:
        logger.error(f"Weather endpoint error: {e}")
        return jsonify({'error': 'Internal error'}), 500
