import httpx
import pytest

from scripts.smoke import check_boston_sales, check_cambridge_parcels


@pytest.mark.parametrize(('municipality', 'street', 'map_lot', 'error'), [
    ('Cambridge', '106 Magazine Street', '102-51', None),
    ('Cambridge', '95 Magazine Street', '101-200', 'expected map-lot 102-51, got 101-200'),
    ('Cambridge', '106 Magazine Street', '102-50', 'expected map-lot 102-51, got 102-50'),
    ('Cambridge', '106 Magazine Court', '102-51', 'unexpected assessor address'),
    ('Somerville', '106 Magazine Street', '102-51', 'wrong or missing municipality'),
])
def test_cambridge_smoke_checks_both_addresses(municipality, street, map_lot, error):
    requested = []

    def respond(request):
        address = request.url.params['address']
        requested.append(address)
        city, site, lot = ('Cambridge', '95 Magazine St', '101-200') if address.startswith('95 ') else (municipality, street, map_lot)
        return httpx.Response(200, json={
            'municipality_match': {'municipality': city},
            'parcel': {'assessor_site_address': site, 'parcel_number': lot},
        })

    with httpx.Client(base_url='https://example.test', transport=httpx.MockTransport(respond)) as client:
        if error:
            with pytest.raises(AssertionError, match=error):
                check_cambridge_parcels(client)
        else:
            check_cambridge_parcels(client)
    assert requested == ['95 Magazine St, Cambridge, MA', '106 Magazine St, Cambridge, MA']


def test_boston_smoke_accepts_registry_pending_only():
    check_boston_sales({
        "truncated": False,
        "candidates": [],
        "count": 0,
        "pending_registry_review": [{"deed_verification": {"status": "pending"}}],
    })


@pytest.mark.parametrize("report", [
    {"status": "unavailable", "candidates": []},
    {"status": "No qualifying recorded sales found", "candidates": [], "truncated": True},
    {
        "truncated": False,
        "candidates": [{"sale_price": 100_000}],
        "count": 1,
        "pending_registry_review": [],
    },
])
def test_boston_smoke_rejects_unavailable_incomplete_or_unverified_sales(report):
    with pytest.raises(AssertionError):
        check_boston_sales(report)
