# Dwelling totals and condo sellout

The property report displays the normalized assessor dwelling count and its source-quality note. A conflicting or missing count remains unresolved. It does not become zero or a verified legal count.

The editable sales program contains market condo types (quantity, average interior unit area, low and high sale-price assumptions), restricted/affordable condos and their separately entered sale-price cap, and existing units retained outside the sellout.

- Total proposed dwellings = market condos in sellout + restricted condos in sellout + dwellings retained outside sellout.
- Net dwelling change = proposed total minus reported existing count, when the latter is usable.
- Gross sellout = sum of each market type's quantity × interior area × price/sq ft, plus restricted condos × entered restricted sale price.
- Retained units count toward total dwellings and produce no condo-sale revenue in this scenario.

This handles additions, complete redevelopment and conversions without counting a converted existing dwelling twice. Proposed counts are program assumptions, not an approved zoning yield. No fixed unit size, net-area efficiency, new-construction sale price or inclusionary compliance is assumed. Gross revenue is before selling costs, concessions, construction, financing, taxes and other costs; it is not profit.

The optional condo reference search uses Boston's municipal assessor/recorded-sale services, Somerville's municipal assessor sales layer, or the MassGIS assessor last-sale layer elsewhere. It searches condominium records within approximately one mile and three years, with positive residential living area. The UI initially filters to a reported year built within five years; the user can change or clear that filter. This can miss newly converted older buildings. Reported construction date does not establish actual new-construction condition. Somerville condo rows include city-reported book/page references; each deed must still be checked before a row can set an observed pricing range.

At least three usable, unbundled unit references are required to display an observed min–max living-area price/sq ft. An explicit button copies that descriptive range into editable scenario assumptions. A shared recorded reference, date and total price across multiple units is excluded from the reference range as a possible bundled transaction. Deed price, arm's-length status, unit characteristics and construction condition remain unverified. Restrictive/affordable prices are never copied from market condo references.

The condo-reference request is separate from the property lookup, so slow pricing sources do not hold up the zoning report. Input assumptions survive reference-service errors. Changing properties clears the scenario and prior references.
