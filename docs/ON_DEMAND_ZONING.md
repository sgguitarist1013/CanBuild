# On-demand zoning research

Selecting or submitting an address starts a normal parcel report. Once matched, CanBuild researches official zoning sources for the mapped district alongside sales and state overlay checks. If a current district cannot be established, the key is municipality-only. There are no extra user inputs.

Discovery starts at registered code links and the official municipality website. The checked-in directory was generated from the [Massachusetts Secretary of the Commonwealth directory](https://www.mass.gov/lists/massachusetts-city-and-town-websites), matching its 351 municipality links against `massachusetts_municipalities.json` (including the directory spelling “North Attleboro”). It follows a small number of zoning-related links on those municipal hosts or known code publishers. Some sites block automated requests, rely on client-side rendering, use scanned documents, or do not provide a discoverable code. The report identifies incomplete evidence. A linked source is not proof of an applicable or current regulation.

Source links, content hashes, collection and refresh dates, and selected excerpts are saved by municipality and district. A successful check is reused for 24 hours, while unavailable sources are retried on later searches after 15 minutes. Changed source text creates a pending revision. Concurrent requests in a worker share the same in-flight lookup. Failed refreshes retain the preceding sources with a stale-data message.

With both `OPENAI_API_KEY` and `ZONING_RESEARCH_MODEL` set, the server can extract candidate fields and exact source text via the Responses API. The app sends only the municipality, district and public excerpts. Candidate text must match the underlying excerpt. If the model is disabled or unavailable, the system still finds and saves source passages. Candidate values never enter the public calculator. Incorporating reviewed rules into the existing engine requires a code change and regression checks. Permits, survey geometry, overlays and adoption dates still require verification.

## Review and storage

- Set `ZONING_RESEARCH_DB` to an absolute SQLite path on a persistent volume when deploying a single-instance service. The default is local temporary storage shared by requests on the same service instance. A restart or replacement of a free Render instance can clear it.
- Operators with access to the same database can list pending revisions with `python -m scripts.review_zoning`. Record a decision with `python -m scripts.review_zoning --revision 123 --decision reviewed --note "Checked ordinance section and effective date"`.
- Operators can also set `ZONING_REVIEW_TOKEN` and call `GET /api/internal/zoning-review` with an Authorization bearer header. Submit `{"decision":"reviewed","note":"..."}` or `rejected` to `POST /api/internal/zoning-review/{revision}`. The API rejects superseded, stale or previously decided revisions.
