# Zoning and property data rollout

Reviewed November 6, 2026. Priorities are an implementation order based on development use cases, not an econometric valuation ranking.

1. Cambridge, Somerville, Boston: building type, housing capacity, subdistricts, overlays and redevelopment.
2. Watertown, Everett, Chelsea, Revere: corridor redevelopment, industrial conversion and transit context. These connectors remain queued; no verified coverage is claimed.
3. Chicopee provides a tested addition and nonconforming-lot reference case.

## Published sources

- Cambridge municipal zoning: https://data.cambridgema.gov/resource/yc3q-q3hf.json . Query the selected parcel polygon, not the Census street point. A unique district must cover at least 98% of the parcel. Existing C-1 geometric scenarios remain conceptual and do not resolve overlays, architectural design or amendments. Adopted multifamily amendment overview: https://www.cambridgema.gov/cdd/projects/zoning/multifamilyhousing .
- Boston municipal GIS: https://gis.boston.gov/arcgis/rest/services/Basemaps/basemap_IPS/MapServer/28 . District, article, and dimensional fields are displayed as GIS references, not a comprehensive rule determination. Selected Boston assessor parcel geometry is used. Screened layers: planned development areas, groundwater conservation, interim planning overlays. This is not exhaustive. Official explanation: https://content.boston.gov/departments/inspectional-services/how-check-buildings-zoning-designation .
- Somerville's current Planning page links the ViewPro zoning atlas: https://www.somervillema.gov/departments/strategy-and-development/planning-division . The atlas's public ArcGIS webmap item `ea720dd78e494aada89eea1b31ab5ec0` identifies `https://cos-geoportal.somervillema.gov/server/rest/services/enCodePlus/ZoningByParcel/MapServer/0` . Metadata reports FY2025 parcels, amendments through May 22, 2025 (Ordinance 2025-07), and a June 17, 2025 update. Direct district-specific code links are read only from allowlisted HTTPS enCodePlus fields. Later amendments remain unverified. The older maps.somervillema.gov/2022 service is not used.
- Chicopee municipal GIS source and date statement: https://chicopeema.gov/566/Geographic-Information-Systems-GIS . Published data date January 1, 2025. The city's viewer configuration identifies `https://hostingdata3.tighebond.com/arcgis/rest/services/ChicopeeMA/ChicopeeDynamic/MapServer` . Base zoning layer 4; overlays 105; historic districts 99; mapped resource buffers 102; mapped floodplain 107. These are screening layers, not site-specific delineations or regulatory clearance.
- Chicopee residential A/B reference standards: https://ecode360.com/15068550 (§§ 275-52 and 275-53). Nonconforming-use and nonconforming-lot review references: §§ 275-22 and 275-37. A below-minimum lot does not automatically establish whether an addition is allowed or prohibited. No footprint or addition area is calculated for Chicopee.
- MassGIS parcels: https://www.mass.gov/info-details/massgis-data-property-tax-parcels . Living area and assessor building area are kept distinct; zero unit counts are treated as unavailable or conflicting observations, not legal dwelling counts. Latest-sale book/page and registered references are assessor observations. The deed has not been retrieved or verified.
- Registry lookup: https://www.masslandrecords.com/ . Link and reference only. No automated latest-deed search, title certification, or non-arm's-length determination is claimed.

## Matching and failure behavior

Municipal zoning uses the matched parcel polygon in EPSG:3857, converted from the existing local ground-foot approximation. District polygons are clipped to the parcel, duplicate records are unioned, and split districts or incomplete coverage cause the district to be withheld. Boundary touches and tiny GIS slivers do not establish a district. Each failed constraint query is marked unavailable; it never becomes “no mapped intersection.” Updates to local standards require a fresh source review.

Sales candidates use same-municipality, same-use-group and comparable-area screening, a three-year window, and a one-mile parcel-centroid distance. Polygon geometry is supported. Missing area, future dates, duplicate parcels and mismatched residential types are excluded. This still uses assessor last-sale records; the LA3 transaction feed, deed-level verification, permits and full statewide rule sets remain future integrations. Neither a median candidate price nor a GIS capacity field is presented as an appraisal.
