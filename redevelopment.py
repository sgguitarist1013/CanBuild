"""Preliminary, automatically populated Cambridge C-1 redevelopment scenarios."""
from math import ceil
from statistics import quantiles

from valuation import positive_number

STUDY = "https://www.cambridgema.gov/-/media/Files/CDD/Housing/Inclusionary/cambridgeinclusionaryhousingstudy2026.pdf"
INCLUSIONARY = "https://www.cambridgema.gov/CDD/housing/fordevelopersandpropmanagers/inclusionarydevelopers"
LENDING = "https://www.occ.gov/publications-and-resources/publications/comptrollers-handbook/files/commercial-real-estate-lending/index-commercial-real-estate-lending.html"
UNIT_SIZES = (700, 1050, 1400)
UNIT_BATHS = (1, 2, 2)
TARGET_MIX = (.20, .50, .30)  # RKG table 34
COMMON_FRACTION = .18


def unit_mix(net_area):
    """Fit a mix close to the target bedroom distribution, using whole units."""
    counts = [0, 0, 0]
    left = net_area
    while left >= UNIT_SIZES[0]:
        feasible = [i for i, area in enumerate(UNIT_SIZES) if area <= left]
        total = sum(counts) + 1
        i = max(feasible, key=lambda j: TARGET_MIX[j] * total - counts[j])
        counts[i] += 1
        left -= UNIT_SIZES[i]
    return counts


def reserve_affordable(counts, required_area):
    """Reserve full units, preferring a mix proportionate to the proposed program."""
    reserved = [0, 0, 0]
    allocated_area = 0
    while allocated_area < required_area:
        i = max(
            (j for j in range(3) if reserved[j] < counts[j]),
            key=lambda j: counts[j] * .20 - reserved[j],
        )
        reserved[i] += 1
        allocated_area += UNIT_SIZES[i]
    return reserved


def matching_condos(references, beds, baths, area):
    """Select nearby finished-condo proxies without relaxing the unit match."""
    ranked = sorted((
        row for row in references
        if row.get("project_key") and row.get("distance_miles") is not None
        and abs(row["living_area_sqft"] - area) <= .15 * area
        and row.get("bedrooms_reported") == beds and row.get("bathrooms_reported") == baths
    ), key=lambda row: (row["distance_miles"], abs(row["living_area_sqft"] - area)))
    projects = {}
    for row in ranked:
        projects.setdefault(row["project_key"], row)
    ranked = list(projects.values())
    nearby = [row for row in ranked if row["distance_miles"] <= .5]
    return nearby[:4] if len(nearby) >= 3 else ranked[:3]


def pricing(references):
    rates = []
    verified = [
        row for row in references
        if (deed := row.get("deed_verification") or {}).get("status") == "verified"
        and positive_number(row.get("sale_price")) and deed.get("consideration") == row["sale_price"]
        and positive_number(row.get("price_per_living_sqft"))
    ]
    for beds, (area, baths) in enumerate(zip(UNIT_SIZES, UNIT_BATHS), 1):
        rows = matching_condos(verified, beds, baths, area)
        if len(rows) >= 3:
            low, _, high = quantiles([r["price_per_living_sqft"] for r in rows], n=4, method="inclusive")
            rates.append((round(low), round(high), "MassLandRecords-confirmed Cambridge sales", rows))
        else:
            rates.append((None, None, "Insufficient matching deed-confirmed sales", rows))
    return rates


def cost_budget(gross_area, existing_area, stories, units, acquisition, sellout, *, stress):
    """Model costs through disposition; rates are documented assumptions, not quotes."""
    building = gross_area * (320 if stories <= 4 else 372) * (1.10 if stress else 1)
    lines = {
        "Building construction, GC overhead and profit": building,
        "Demolition and ordinary abatement allowance": existing_area * (25 if stress else 15),
        "Sitework, civil, utility connections and offsite allowance": gross_area * (35 if stress else 22),
        "Dedicated parking construction": 0,
    }
    hard = sum(lines.values())
    lines.update({
        "Hard-cost contingency and escalation": hard * (.12 if stress else .075),
        "Architecture, engineering and consultants": hard * .08,
        "Permits, approvals, inspection and utility fees": hard * .025,
        "Survey, geotechnical, environmental and testing": hard * .015,
        "Legal, accounting, condo documents and development management": hard * .03,
        "Construction insurance, security and temporary utilities": hard * .015,
        "Taxes, post-completion carry and initial condo reserves": units * (10000 if stress else 7000),
    })
    development = sum(lines.values())
    # Illustrative 70% LTC, average 50% drawn on development, land drawn at close.
    months = (24 if stories > 4 else 18) + (9 if stress else 6)
    lines["Financing fees and interest through sellout"] = (
        .70 * development * (.0125 + .075 * .5 * months / 12)
        + .70 * (acquisition or 0) * (.0125 + .075 * months / 12)
    )
    lines["Brokerage, marketing, closing, excise and concessions"] = (
        sellout * (.075 if stress else .055) if sellout is not None else None
    )
    lines["Acquisition proxy and buyer closing costs"] = acquisition * 1.02 if acquisition else None
    return {key: round(value) if value is not None else None for key, value in lines.items()}


def analyze_redevelopment(massing, building, references=(), *, current_market_range=None, include_costs=True):
    if not massing or not massing.get("gross_area_high_sqft"):
        return {"status": "No supported redevelopment envelope for this parcel."}
    building = building or {}
    market = current_market_range or {}
    low, high = market.get("low"), market.get("high")
    acquisition = (low + high) / 2 if positive_number(low) and positive_number(high) and low <= high else None
    existing_gross = building.get("building_area_sqft")
    existing_living = building.get("residential_area_sqft")
    existing_units = building.get("units")
    has_existing_area = positive_number(existing_gross)
    demolition_area = existing_gross if has_existing_area else massing["gross_area_high_sqft"] / massing["stories"]
    prices = pricing(references)
    options = [("Base redevelopment", massing, False)]
    if bonus := massing.get("inclusionary_scenario"):
        options.append(("Conditional inclusionary height option", bonus, True))
    modeled = []
    rejected = []
    for name, option, conditional in options:
        gross = option["gross_area_high_sqft"]
        common = ceil(gross * COMMON_FRACTION)
        counts = unit_mix(gross - common)
        if not sum(counts):
            continue
        unit_area = sum(count * size for count, size in zip(counts, UNIT_SIZES))
        # Living area is also a lower bound on existing gross floor area.
        if ((has_existing_area and gross <= existing_gross)
                or (positive_number(existing_living) and unit_area <= existing_living)
                or (positive_number(existing_units) and sum(counts) < existing_units)):
            rejected.append({"name": name, "proposed_gross_area_sqft": gross,
                             "proposed_living_area_sqft": unit_area, "proposed_units": sum(counts)})
            continue
        # Use the gross envelope as a conservative proxy for the 10,000 sq ft trigger.
        restricted_area = ceil(.20 * unit_area) if conditional or sum(counts) >= 10 or gross >= 10000 else 0
        restricted = reserve_affordable(counts, restricted_area)
        mix = []
        for i, (count, affordable, area, price) in enumerate(zip(counts, restricted, UNIT_SIZES, prices), start=1):
            if count:
                mix.append({"bedrooms": i, "area_sqft": area, "market_units": count - affordable,
                            "restricted_units": affordable, "price_per_sqft_low": price[0],
                            "price_per_sqft_high": price[1], "pricing_basis": price[2], "comparable_projects": len(price[3]),
                            "bathrooms_assumed": UNIT_BATHS[i-1], "sale_matches": price[3]})
        scenario = {
            "name": name, "conditional": conditional, "stories": option["stories"],
            "height_ft": option.get("height_ft"), "gross_area_sqft": gross,
            "common_area_sqft": common, "unallocated_area_sqft": gross - common - unit_area,
            "market_area_sqft": sum(row["market_units"] * row["area_sqft"] for row in mix),
            "restricted_area_sqft": sum(row["restricted_units"] * row["area_sqft"] for row in mix),
            "market_units": sum(counts) - sum(restricted), "restricted_units": sum(restricted), "unit_mix": mix,
            "sellout_low": None, "sellout_high": None, "cost_low": None, "cost_high": None,
            "surplus_low": None, "surplus_high": None, "cost_lines": [],
        }
        market_mix = [row for row in mix if row["market_units"]]
        if include_costs:
            sale_low = sale_high = None
            if market_mix and all(row["price_per_sqft_low"] is not None for row in market_mix):
                sale_low, sale_high = (
                    sum(row["market_units"] * row["area_sqft"] * row[f"price_per_sqft_{band}"] for row in market_mix)
                    for band in ("low", "high")
                )
            downside = cost_budget(gross, demolition_area, option["stories"], sum(counts), acquisition, sale_low, stress=True)
            upside = cost_budget(gross, demolition_area, option["stories"], sum(counts), acquisition, sale_high, stress=False)
            costs_high = sum(x for x in downside.values() if x is not None)
            costs_low = sum(x for x in upside.values() if x is not None)
            complete_project = sale_high is not None and acquisition is not None and not scenario["restricted_units"]
            scenario.update(
                sellout_low=sale_low, sellout_high=sale_high, cost_low=costs_low, cost_high=costs_high,
                surplus_low=sale_low - costs_high if complete_project else None,
                surplus_high=sale_high - costs_low if complete_project else None,
                cost_lines=[{"name": key, "upside": value, "downside": downside[key]} for key, value in upside.items()],
            )
        modeled.append(scenario)
    if not modeled:
        if rejected:
            return {"status": "The modeled replacement would lose existing space or units.",
                    "decision": "retain_and_review", "rejected_options": rejected}
        return {"status": "The screened envelope does not fit a modeled unit."}
    priced = all(row["surplus_high"] is not None for row in modeled)
    if priced:
        modeled.sort(key=lambda row: row["surplus_high"], reverse=True)
        ranking = "Ranked by highest modeled surplus after acquisition."
    else:
        modeled.sort(key=lambda row: row["gross_area_sqft"], reverse=True)
        ranking = "Largest modeled floor area. Financial ranking requires pricing for all units and an as-is market estimate."
    best = modeled[0]
    decision = "screened"
    if best["sellout_high"] is not None and best["sellout_high"] < best["cost_low"]:
        decision = "market_rate_shortfall" if best["restricted_units"] else "modeled_loss"
    return {
        "status": "Preliminary redevelopment screen",
        "decision": decision,
        "best": best, "alternatives": [
            {key: row[key] for key in ("name", "conditional", "stories", "gross_area_sqft", "sellout_low", "sellout_high", "surplus_low", "surplus_high")}
            for row in modeled[1:]
        ],
        "ranking": ranking, "acquisition_proxy": acquisition, "demolition_area_proxy": not has_existing_area,
        "restricted_proceeds_credited": 0,
        "assumptions": "18% common/service area, standard assumed unit sizes, no dedicated parking, no restricted-unit proceeds, and no subsidies. Demolition area uses the assessor building area or a one-floor footprint proxy. Budget allowances are screening assumptions. Large abatement, relocation, remediation, unusual foundations, parking, insurance or offsite work can materially change costs. Surplus is before income tax and investor distributions.",
        "sources": [{"name": "Cambridge September 2026 underwriting study · tables 27, 30 and 34", "url": STUDY},
                    {"name": "Cambridge inclusionary requirements", "url": INCLUSIONARY},
                    {"name": "OCC construction lending and interest reserves", "url": LENDING}],
    }
