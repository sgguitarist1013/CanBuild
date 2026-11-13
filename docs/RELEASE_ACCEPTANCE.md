# Release acceptance gate

Every push and pull request runs the complete unit suite and Chromium browser suite. The browser suite performs an unmocked property search for `9 Gorham St, Somerville, MA 02144`. The test passes only when the API returns a publishable report, the UI renders it, every required evidence check is supported, the as-is range has at least three qualifying recorded sales, zoning rules and site screens are verified, and the redevelopment result includes sellout and cost ranges with a cost breakdown.

The staged and production release workflow runs the same report contract after each Render deployment. A failed query, withheld report, missing source stage, or incomplete underwriting fails the release. Render production auto-deploy is configured to wait for GitHub checks; this gate is part of the required test workflow.

The acceptance test is intentionally based on a live property search. A mocked response cannot satisfy it. When source evidence is unavailable, the correct product behavior remains to withhold property facts, while the failing release gate keeps the incomplete state visible to the development team.
