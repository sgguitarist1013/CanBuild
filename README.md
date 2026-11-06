# CanBuild

Massachusetts property research with municipality-specific preliminary zoning scenarios.

## Run and test

```sh
pip install -r requirements.txt
python3 -m uvicorn app:app --host 0.0.0.0 --port 8000
python3 -m unittest discover -s tests -v
npm ci
npx playwright install --with-deps chromium
npm test
```

Browser tests use deterministic API fixtures for search success, failed searches, withheld estimates, address suggestions, and mobile layout. They do not verify live municipal source data. Set `BASE_URL` to run against an already running server. `CANBUILD_LIVE_STARTUP_SMOKE=0` disables only the optional external-source startup smoke check; startup regression tests remain required.

## Deployment requirements

GitHub Actions runs Python regression tests and Playwright browser tests on main pushes and pull requests. Configure both as required branch checks and set Render Auto-Deploy to **After CI Checks Pass** before merging. A workflow file alone does not enable this Render setting. Keep the existing startup regression check as an additional safeguard. Manual deploys can bypass CI settings; deploy only a commit whose checks passed. Use `/api/health` as the service health check.

## Error reporting

Set `SENTRY_DSN` in Render to enable the Sentry FastAPI integration; leave it unset to disable reporting. Set `SENTRY_ENVIRONMENT` if needed. Release tags use `RENDER_GIT_COMMIT`. Request objects, user metadata, breadcrumbs and local variable capture are omitted; performance tracing is disabled. Review event contents in the Sentry project before broad rollout. This integration reports server errors; browser error reporting is not configured.
