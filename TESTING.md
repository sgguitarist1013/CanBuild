# PlotDocket testing and release

## Local checks

Python 3.12 is used in CI.

```
pip install -r requirements-dev.txt
python -m playwright install --with-deps chromium
python -m pytest -q --ignore=tests/test_browser.py
python -m pytest tests/test_browser.py -q --tracing retain-on-failure --screenshot only-on-failure
PYTHONPATH=. python scripts/smoke.py https://canbuild.onrender.com
```

The suite covers parcel identity, municipal rule boundaries, envelope calculations, API validation, suggestion filtering, browser search/results, withheld estimates, error/retry behavior, mobile layout, and monitoring request-data scrubbing. Browser responses are mocked for repeatability. The live smoke script independently checks the municipality, assessor address and map-lot for [95 Magazine St (101-200)](https://gis.cambridgema.gov/blockmaps/Block_101.pdf) and [106 Magazine St (102-51)](https://gis.cambridgema.gov/blockmaps/Block_102.pdf), along with the Boston alias and registry status. Browser failures produce traces and screenshots in `test-results`.

## Product review

1. Record each failure in the relevant GitHub issue or draft PR. Include the build SHA, address or search steps, expected and actual result, source links and dates, and what decision the error could affect. For mobile defects, include the viewport and browser trace or screenshot.
2. Reproduce against staging when possible. Separate app defects from temporary source failures and from unresolved source evidence. Track evidence blockers with the missing record or approval and the next check. Keep them open while other cases proceed.
3. Add a reproducible regression for the defect and run the relevant local tests. For changes to a buyer-facing report, verify at 320 and 390 px that the lead answer, figures, collapsed evidence and Ask PlotDocket links work with complete and incomplete data.
4. Require review and passing CI before integration, and a passing live staging smoke test on the release commit before production. A passing parcel identity check only confirms which property the report describes. Review zoning, sales and underwriting evidence separately before marking a municipal case verified.

## Required host configuration before release

The files alone do not change Render's existing commit-triggered auto-deploy setting.

1. Disable automatic deployments for production and staging. This is required for the staged release workflow to control deployments. Alternatively, CI-gated automatic deploys provide only test gating, not the staging-before-production sequence.
2. Create a separate Render staging service using this repository, Python runtime, `pip install -r requirements.txt`, `uvicorn app:app --host 0.0.0.0 --port $PORT`, and health check `/api/health`.
3. In GitHub create `staging` and `production` environments. For each, set secret `RENDER_API_KEY` and variables `RENDER_SERVICE_ID` and `SERVICE_URL` to the corresponding service. Never commit credentials.
4. Require `regression-and-browser` in main branch protection. Restrict direct main pushes. Restrict both release environments to main.
5. Create a Sentry Python/FastAPI project and set `SENTRY_DSN` on each Render service, with `SENTRY_ENVIRONMENT=staging` or `production`. Monitoring remains disabled without a DSN. Request payloads, breadcrumbs, user fields and local variables are excluded; error messages may still need project-level scrubbing.
6. Merge only after CI passes and the host controls above are configured. Run **Staging to production** from main. It tests, deploys the exact commit to staging, waits for that deployment, requires the live parcel smoke check, then deploys the same commit to production and repeats the smoke check. A failure blocks downstream jobs; no automatic rollback is performed.

## Current limits

Sentry integration captures backend errors; browser-side Sentry and alert routing are not configured. Live public-data dependencies may legitimately fail the release smoke test; investigate rather than bypass parcel identity checks. No workflow can prevent a manual Render deploy or a direct push unless account permissions and branch rules also enforce the policy.
