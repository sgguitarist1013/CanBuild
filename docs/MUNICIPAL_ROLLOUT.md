# Massachusetts municipal rollout

Cambridge remains the active pilot. Reproduce the reports at 95 and 106 Magazine St while other municipalities are researched in parallel. Take new municipalities in descending order of the FY2025 Massachusetts Department of Revenue mean single-family assessment. The [351-municipality queue](massachusetts_assessment_priority_fy2025.csv) keeps the source rank even when work proceeds in parallel. Keep Boston and Somerville work already in progress in the development backlog.

## First work items

| Statewide rank | Municipality | FY2025 mean assessment | Base map | Rule implementation | Verified report |
| ---: | --- | ---: | --- | --- | --- |
| 1 | Chilmark | $3,350,921 | Pending | Pending | Pending |
| 2 | Nantucket | $2,927,897 | Pending | Pending | Pending |
| 3 | Brookline | $2,505,452 | Pending | Pending | Pending |
| 4 | Edgartown | $2,502,856 | Pending | Pending | Pending |
| 5 | Weston | $2,294,016 | Pending | Pending | Pending |
| 6 | Cambridge | $1,976,958 | Connected | Partial, C-1 conceptual rules | Pilot in progress |
| 7 | Wellesley | $1,925,282 | Connected | Pending | Pending |
| 8 | Aquinnah | $1,843,417 | Pending | Pending | Pending |
| 9 | West Tisbury | $1,837,546 | Pending | Pending | Pending |
| 10 | Provincetown | $1,756,564 | Pending | Pending | Pending |

Start Chilmark with the [town zoning bylaw index and district maps](https://www.chilmarkma.gov/1865/Complete-List-of-Zoning-Bylaws). The town lists the August 2026 amendment posting and separate articles for dimensions, rate of development, nonconforming uses, and overlay districts. For parallel source work, use [Nantucket’s official GIS maps](https://www.nantucket-ma.gov/151/GIS-Maps) and [Brookline’s parcel and zoning resources](https://www.brooklinema.gov/1480/Find-Property-Information).

Map connection means a municipal source is available in the app. Track current ordinances, overlays and amendments separately. See [source coverage](DATA_SOURCES.md) and the `/api/coverage` endpoint for current map statuses, including historical references. Keep research and blocked work visible for each active municipality. Add a row when starting a municipality outside this table.

| Stage | Evidence to record |
| --- | --- |
| Base map | Official map and version, matched parcel geometry, district and overlay evidence. Label historical or unresolved sources. |
| Rule implementation | Ordinance sections and adopted amendments used in code, dimensional constraints, nonconforming rules, eligible uses and approval path. |
| Verified report | Named parcel fixtures, checked capacity, closed-sale verification status, sourced costs, and supported base and downside results. Record missing sales and cost evidence explicitly. |
| Reviewed and integrated | Link to the reviewed change and passing regression tests. Count toward statewide completion only when the earlier stages are documented and integrated. |

## Assessment source and reproduction

The [Town of Braintree FY2026 tax classification booklet](https://braintreema.gov/DocumentCenter/View/16989/FY2026-Tax-Classification-Booklet) republishes the Massachusetts DOR FY2025 table *Average Single Family Tax Bills by Municipality* on PDF pages 24–29. This is an FY2025 assessment snapshot. The [FY2026 DLS article](https://www.mass.gov/info-details/fy2026-statewide-average-single-family-tax-bill) describes 346 communities in its FY2026 tax-bill chart. Use the complete FY2025 table for a consistent initial queue.

Sort the table's **Average Single-Family Value** column descending and use the municipality name to break ties. Record DOR codes and parcel counts for traceability. This measures assessed single-family property values for rollout planning. The ranking excludes condominiums and can put condo-heavy cities below smaller high-assessment towns. Parcel pricing still uses qualified closed sales with registry verification.

Recreate the CSV from the downloaded booklet:

```bash
pdftotext -layout FY2026-Tax-Classification-Booklet.pdf dor-assessments.txt
python scripts/rank_municipalities.py dor-assessments.txt > docs/massachusetts_assessment_priority_fy2025.csv
```

Source PDF SHA-256: `b6f13261fed74dcab532646b4ca93c4aeecd595b9b8abdc08a64b1de70d23fa7`.
