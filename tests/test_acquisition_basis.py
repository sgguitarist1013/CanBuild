import pytest

from redevelopment import analyze_redevelopment
from tests.test_redevelopment import verified_condos


@pytest.mark.parametrize(("median", "expected", "basis", "closing_total"), [
    (1340000, 1340000, "comp_median", 1366800),
    (None, 1860000, "range_midpoint", 1897200),
    (2500000, None, None, None),
    (True, None, None, None),
])
def test_acquisition_basis_from_asymmetric_market_range(median, expected, basis, closing_total):
    comps = [dict(row, arm_length_verified=True, new_construction_verified=True)
             for row in verified_condos(1700)]
    result = analyze_redevelopment(
        {"gross_area_high_sqft": 8000, "stories": 4}, {}, comps,
        current_market_range={"low": 1320000, "median": median, "high": 2400000},
    )
    assert result["acquisition_proxy"] == expected
    assert result["acquisition_basis"] == basis
    land = next(line for line in result["best"]["cost_lines"] if line["name"].startswith("Acquisition proxy"))
    assert land["upside"] == land["downside"] == closing_total
