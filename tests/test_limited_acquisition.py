import pytest

from redevelopment import analyze_redevelopment
from tests.test_redevelopment import verified_condos


@pytest.mark.parametrize("market", [
    {"low": 1000000, "high": 1200000, "confidence": "limited", "comp_count": 2},
    {"low": 1000000, "high": 1200000, "comp_count": 1},
])
def test_limited_as_is_sales_do_not_price_project_acquisition_or_profit(market):
    comps = [dict(row, arm_length_verified=True, new_construction_verified=True)
             for row in verified_condos(1700)]
    result = analyze_redevelopment(
        {"gross_area_high_sqft": 8000, "stories": 4}, {}, comps,
        current_market_range=market,
    )
    best = result["best"]
    assert best["sellout_high"] > 0
    assert result["acquisition_proxy"] is None
    assert best["surplus_low"] is best["surplus_high"] is None
    acquisition = next(row for row in best["cost_lines"] if row["name"].startswith("Acquisition proxy"))
    assert acquisition["upside"] is acquisition["downside"] is None
