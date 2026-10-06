"""Address geocoding and map links shared by venues and events."""
import json
import urllib.parse
import urllib.request


def geocode(address):
    """(latitude, longitude) for an address via Nominatim, or None.

    This is a network call; any failure is swallowed and returns None.
    """
    if not address:
        return None
    try:
        query = urllib.parse.urlencode({'q': address, 'format': 'json', 'limit': 1})
        req = urllib.request.Request(f'https://nominatim.openstreetmap.org/search?{query}',
                                     headers={'User-Agent': 'TownHallApp'})
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode())
        if data:
            return float(data[0]['lat']), float(data[0]['lon'])
    except Exception:
        pass
    return None


def map_url(latitude, longitude, address=''):
    """OpenStreetMap link: a pin when coordinates are known, else an address search."""
    if latitude is not None and longitude is not None:
        return f'https://www.openstreetmap.org/?mlat={latitude}&mlon={longitude}#map=17/{latitude}/{longitude}'
    if address:
        return 'https://www.openstreetmap.org/search?' + urllib.parse.urlencode({'query': address})
    return ''


def map_embed_url(latitude, longitude, span=0.006):
    """OpenStreetMap iframe URL centred on a point, or '' without coordinates."""
    if latitude is None or longitude is None:
        return ''
    bbox = f'{longitude - span},{latitude - span / 2},{longitude + span},{latitude + span / 2}'
    return 'https://www.openstreetmap.org/export/embed.html?' + urllib.parse.urlencode(
        {'bbox': bbox, 'layer': 'mapnik', 'marker': f'{latitude},{longitude}'})
