"""Live service checks; fail closed on unavailable or incorrect parcel data."""
import argparse
import time
import httpx
from parcel_engine import requested_house_number


def check(base_url):
    with httpx.Client(base_url=base_url.rstrip('/'), timeout=90, follow_redirects=True) as client:
        response = client.get('/api/health')
        response.raise_for_status()
        assert response.json().get('ok') is True, 'Unhealthy service'
        response = client.get('/')
        response.raise_for_status()
        assert 'Property address' in response.text, 'Property search UI missing'
        response = client.get('/api/property', params={'address': '95 Magazine St, Cambridge, MA'})
        response.raise_for_status()
        data = response.json()
        parcel = data.get('parcel') or {}
        assert requested_house_number(parcel.get('assessor_site_address') or '') == '95', 'Expected parcel 95; wrong or missing parcel'
        assert 'MAGAZINE' in (parcel.get('assessor_site_address') or '').upper(), 'Wrong street'
        assert parcel.get('parcel_number'), 'Missing parcel identifier'
        response = client.get('/api/property', params={'address': 'One Lewis Wharf, Boston, MA'})
        response.raise_for_status()
        candidates = response.json().get('parcel_candidates', [])
        assert any(c.get('parcel_number') == '0303029000' for c in candidates), 'Boston alias must offer the municipal assessor candidate'
        response = client.get('/api/property', params={'address': 'One Lewis Wharf, Boston, MA', 'boston_pid': '0303029000'})
        response.raise_for_status()
        selected = response.json()
        assert selected.get('parcel', {}).get('parcel_number') == '0303029000', 'Selected Boston PID changed'
        assert selected['parcel'].get('recorded_owners'), 'Boston owner missing'
        assert selected.get('sales_research', {}).get('status') in {'Recorded sale candidates found', 'No qualifying recorded sales found'}, 'Boston recorded sales lookup failed'
    print('Live health, search UI, 95 Magazine, Boston alias/owner and recorded-sales checks passed.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('base_url')
    args = parser.parse_args()
    check(args.base_url)
