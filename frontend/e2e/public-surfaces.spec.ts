import { expect, test } from "@playwright/test";

const financePages = [
  ["/preview", "A financial picture that explains itself."],
  ["/housing", "The real cost of a house"],
  ["/retirement", "Retire on your terms"],
] as const;

const projectPages = [
  ["https://gordongouger.com/", "Gordon Gouger"],
  ["https://library.gordongouger.com/ggouger/", "Library"],
  ["https://athletic-analytics.gordongouger.com/", "Athletic"],
  ["https://gordongouger.com/14ers.html", "14er"],
] as const;

const mainSiteProjectPages = [
  ["https://gordongouger.com/athletic-analytics.html", "Athletic Analytics"],
  ["https://gordongouger.com/library.html", "Favorite shelf"],
  ["https://gordongouger.com/finance.html", "Finance"],
  ["https://gordongouger.com/14ers.html", "Statewide summit terrain"],
] as const;

async function assertNoPageOverflow(page: import("@playwright/test").Page) {
  const widths = await page.evaluate(() => ({
    viewport: document.documentElement.clientWidth,
    document: document.documentElement.scrollWidth,
    body: document.body.scrollWidth,
  }));
  expect(Math.max(widths.document, widths.body)).toBeLessThanOrEqual(widths.viewport + 1);
}

for (const [path, heading] of financePages) {
  test.describe(`Finance ${path}`, () => {
    for (const [name, viewport] of [["desktop", { width: 1440, height: 960 }], ["phone", { width: 390, height: 844 }]] as const) {
      test(`${name} keeps the visual story readable without horizontal overflow`, async ({ page }) => {
        await page.setViewportSize(viewport);
        await page.goto(path, { waitUntil: "networkidle" });
        await expect(page.getByRole("heading", { name: heading }).first()).toBeVisible();
        await assertNoPageOverflow(page);
      });
    }
  });
}

test("public Finance routes keep an explicit same-origin CSP fallback", async ({ page }) => {
  for (const [path] of financePages) {
    const response = await page.goto(`${path}?security-regression=1`, { waitUntil: "networkidle" });
    const policy = response?.headers()["content-security-policy"] || "";
    expect(policy).toContain("default-src 'self'");
    expect(policy).toContain("object-src 'none'");
  }
});

for (const [url, headingFragment] of projectPages) {
  test(`Public project surface is usable on a phone: ${url}`, async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto(url, { waitUntil: "networkidle" });
    await expect(page.locator("body")).toContainText(new RegExp(headingFragment, "i"));
    await assertNoPageOverflow(page);
  });
}

for (const [url, headingFragment] of mainSiteProjectPages) {
  test(`Main-site project preview stays usable on a phone: ${url}`, async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto(url, { waitUntil: "networkidle" });
    await expect(page.locator("body")).toContainText(new RegExp(headingFragment, "i"));
    await assertNoPageOverflow(page);
  });
}

test("Athletic preview keeps an honest sample story when live telemetry is unavailable", async ({ page }) => {
  await page.route("**/__athletics/summary", (route) => route.abort());
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("https://gordongouger.com/athletic-analytics.html", { waitUntil: "networkidle" });
  await expect(page.getByText("illustrative sample state")).toBeVisible();
  await expect(page.getByRole("link", { name: /Open Athletic Analytics/ }).first()).toBeVisible();
  await assertNoPageOverflow(page);
});

test("project headers share the portfolio hub and owner-auth handoff", async ({ page }) => {
  for (const url of [
    "https://finance.gordongouger.com/preview",
    "https://library.gordongouger.com/ggouger/",
    "https://athletic-analytics.gordongouger.com/",
    "https://14ers.gordongouger.com/",
  ]) {
    await page.goto(url, { waitUntil: "networkidle" });
    await expect(page.getByRole("link", { name: /all projects/i }).first()).toHaveAttribute(
      "href",
      "https://gordongouger.com/projects.html",
    );
  }

  await page.goto("https://14ers.gordongouger.com/", { waitUntil: "networkidle" });
  await page.getByRole("button", { name: /owner sign in/i }).click();
  await page.waitForURL(/auth\.gordongouger\.com/);

  const athleticsLogin = await page.request.get("https://athletic-analytics.gordongouger.com/login", {
    maxRedirects: 0,
  });
  expect(athleticsLogin.status()).toBe(302);
  expect(athleticsLogin.headers()["location"]).toMatch(/^https:\/\/auth\.gordongouger\.com\//);
});
