const { test, expect } = require('@playwright/test');
const record = {
  address: '95 MAGAZINE ST, CAMBRIDGE, MA, 02139', checked_at: '2026-11-06T12:00:00Z',
  parcel: { parcel_number: '101-200', assessor_site_address: '95 MAGAZINE ST', fiscal_year: 2026, gis_area_sqft_approx: 4000 },
  existing_building: { units: 3, residential_area_sqft: 3200 },
  zoning: { district: 'C-1' }, verification: { parcel: 'Address matched', assessor: 'FY2026', zoning: 'GIS point match' },
  warnings: ['Parcel maps are not legal surveys.'], sources: [], zoning_potential: null,
};
test.beforeEach(async ({ page }) => {
  await page.route('**/api/suggest?*', route => route.fulfill({ json: { suggestions: ['95 Magazine St, Cambridge, MA'] } }));
});
test('health and initial mobile layout', async ({ page, request }) => {
  const response = await request.get('/api/health');
  expect(response.ok()).toBeTruthy(); expect((await response.json()).ok).toBe(true);
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Find the potential in any property.' })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});
test('selected suggestion loads matching parcel without invented estimates', async ({ page }) => {
  await page.route('**/api/property?*', route => route.fulfill({ json: record }));
  await page.goto('/'); await page.getByRole('combobox').fill('95 Mag');
  await page.getByRole('button', { name: '95 Magazine St, Cambridge, MA', exact: true }).click();
  await expect(page.locator('#propertyAddress')).toHaveText(record.address);
  await expect(page.locator('#facts')).toContainText('101-200');
  await expect(page.locator('#facts')).toContainText('Reported dwelling units3');
  await expect(page.locator('#potentialArea')).toHaveText('Not yet estimated');
  await expect(page.locator('#createdValue')).toHaveText('Needs sale comps');
  await expect(page.getByRole('combobox')).toHaveAttribute('aria-expanded', 'false');
});
test('failed lookup clears prior report and allows retry', async ({ page }) => {
  let calls = 0;
  await page.route('**/api/property?*', route => ++calls === 1
    ? route.fulfill({ json: record })
    : route.fulfill({ status: 404, json: { detail: 'Address not matched.' } }));
  await page.goto('/'); await page.getByRole('combobox').fill('95 Magazine St, Cambridge, MA');
  await page.getByRole('button', { name: 'Explore property' }).click();
  await expect(page.locator('#results')).toBeVisible();
  await page.getByRole('combobox').fill('999 Missing St, Cambridge, MA');
  await page.getByRole('combobox').press('Enter');
  await expect(page.locator('#status')).toHaveText('Address not matched.');
  await expect(page.locator('#results')).toBeHidden();
  await expect(page.getByRole('button', { name: 'Explore property' })).toBeEnabled();
});
test('short address validation does not send property request', async ({ page }) => {
  let calls = 0; await page.route('**/api/property?*', route => { calls++; return route.abort(); });
  await page.goto('/'); await page.getByRole('combobox').fill('95');
  await page.getByRole('button', { name: 'Explore property' }).click();
  await expect(page.locator('#status')).toHaveText('Enter a full Massachusetts address.');
  expect(calls).toBe(0);
});
