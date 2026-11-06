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
    print('Live health, search UI and 95 Magazine parcel checks passed.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('base_url')
    args = parser.parse_args()
    check(args.base_url)
