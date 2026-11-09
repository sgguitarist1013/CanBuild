import pytest

from scripts.smoke import check_boston_sales


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
