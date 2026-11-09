"""Live service checks; fail closed on unavailable or incorrect parcel data."""
import argparse
import time
import httpx
from parcel_engine import requested_house_number


def check_boston_sales(report):
    assert report.get('truncated') is False, 'Boston sales lookup returned incomplete data'
    candidates = report.get('candidates')
    assert isinstance(candidates, list), 'Boston sales candidates missing'
    if report.get('status') == 'No qualifying recorded sales found':
        assert not candidates, 'Empty Boston sales result contains candidates'
        return

    pending = report.get('pending_registry_review')
    assert isinstance(pending, list) and report.get('count') == len(candidates), 'Boston registry review data missing'
    assert candidates or pending, 'Boston sales lookup returned no sale results'
    assert all(row.get('deed_verification', {}).get('status') == 'verified' for row in candidates), 'Boston comparable sale lacks registry confirmation'
    assert all('sale_price' not in row and row.get('deed_verification', {}).get('status') not in (None, 'verified')
               for row in pending), 'Boston pending sale lead was treated as confirmed'


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
        check_boston_sales(selected.get('sales_research') or {})
    print('Live health, search UI, 95 Magazine, Boston alias/owner and recorded-sales checks passed.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('base_url')
    args = parser.parse_args()
    check(args.base_url)
