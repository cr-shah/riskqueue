import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

test("overview, capacity, queue filtering, drill-down and local review work", async ({
  page,
}) => {
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("./");
  await expect(
    page.getByRole("heading", { name: "Every review counts." }),
  ).toBeVisible();
  await page.locator("#quick-capacity").fill("137");
  await expect(page.locator("#capacity-value")).toHaveText("137");
  await page.getByRole("link", { name: "Open review queue" }).click();
  await expect(page.locator("#policy-capacity")).toHaveValue("137");
  await page.locator("#queue-type").selectOption("TRANSFER");
  const first = page.locator(".case-link").first();
  const id = await first.getAttribute("data-case");
  await page.locator("#queue-search").fill(id);
  await expect(page.locator(".case-link")).toHaveCount(1);
  await page.locator(".case-link").click();
  await expect(
    page.getByRole("heading", { name: "Observed historical context" }),
  ).toBeVisible();
  await page.locator("#case-status").selectOption("investigating");
  await page
    .locator("#case-note")
    .fill("Review amount relative to sender history.");
  await page.getByRole("button", { name: "Save local review" }).click();
  await page.reload();
  await page.locator("#queue-search").fill(id);
  await expect(page.locator("tbody")).toContainText("investigating");
  await page.locator(".case-link").click();
  await expect(page.locator("#case-note")).toHaveValue(
    "Review amount relative to sender history.",
  );
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).not.toBeVisible();
  expect(errors).toEqual([]);
});
test("scenario baseline, reproducible exports, and share URL survive reload", async ({
  page,
}) => {
  await page.goto("./#scenarios");
  await expect(
    page.getByRole("heading", { name: "What if you changed the policy?" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Coverage · 750 reviews" }).click();
  await expect(page.locator("#policy-capacity")).toHaveValue("750");
  await page.getByRole("button", { name: "Use current as baseline" }).click();
  await page.locator("#policy-capacity").fill("100");
  await page.locator("#policy-capacity").press("Tab");
  await expect(page.locator(".scenario-card").first()).toContainText(
    "750 capacity",
  );
  const downloadPromise = page.waitForEvent("download");
  await page.getByRole("button", { name: "Export analysis" }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toBe("riskqueue-analysis.json");
  const stream = await download.createReadStream();
  const chunks = [];
  for await (const chunk of stream) chunks.push(chunk);
  const result = JSON.parse(Buffer.concat(chunks).toString());
  expect(result.policy.capacity).toBe(100);
  expect(result.baseline.policy.capacity).toBe(750);
  expect(result.selectedTransactionIds).toHaveLength(100);
  await page.reload();
  await expect(page.locator("#policy-capacity")).toHaveValue("100");
  await expect(page.locator(".scenario-card").first()).toContainText(
    "750 capacity",
  );
});
test("development lab calculates without network scoring and displays exact contributions", async ({
  page,
}) => {
  await page.goto("./#scoring");
  await page
    .getByRole("button", { name: "Calculate development score" })
    .click();
  await expect(page.locator("#score-output")).toContainText(
    "DEVELOPMENT HEURISTIC",
  );
  await expect(page.locator("#score-output")).toContainText("jitter:");
  const initial = await page.locator("#score-output").textContent();
  await page
    .getByRole("button", { name: "Calculate development score" })
    .click();
  await expect(page.locator("#score-output")).toHaveText(initial);
});
test("empty queues, filtered CSV, and errors have useful states", async ({
  page,
}) => {
  await page.goto("./#queue");
  await expect(page.locator(".case-link").first()).toBeVisible();
  const dl = page.waitForEvent("download");
  await page.getByRole("button", { name: "Export filtered CSV" }).click();
  expect((await dl).suggestedFilename()).toBe("riskqueue-queue.csv");
  await page.locator("#policy-threshold").fill("1");
  await page.locator("#policy-threshold").press("Tab");
  await expect(
    page.getByRole("heading", { name: "No cases in this view" }),
  ).toBeVisible();
  await page.route("**/data.json", (route) =>
    route.fulfill({ status: 503, body: "Unavailable" }),
  );
  await page.reload();
  await expect(
    page.getByRole("heading", { name: "The snapshot could not load." }),
  ).toBeVisible();
  await page.unroute("**/data.json");
  await page.getByRole("button", { name: "Try again" }).click();
  await expect(
    page.getByRole("heading", { name: "Put the right cases first." }),
  ).toBeVisible();
});
test("all six views are accessible and fit the viewport", async ({ page }) => {
  for (const view of [
    "overview",
    "queue",
    "scenarios",
    "models",
    "scoring",
    "evidence",
  ]) {
    await page.goto(`./#${view}`);
    await expect(page.locator("main h1")).not.toHaveText(
      "Preparing your workspace",
    );
    const results = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
      .analyze();
    expect(
      results.violations,
      JSON.stringify(
        results.violations.map((v) => ({
          id: v.id,
          nodes: v.nodes.map((n) => n.target),
        })),
      ),
    ).toEqual([]);
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= window.innerWidth,
      ),
    ).toBe(true);
  }
  await page.goto("./#queue");
  await page.locator(".case-link").first().click();
  const results = await new AxeBuilder({ page })
    .withTags(["wcag2a", "wcag2aa", "wcag21aa"])
    .analyze();
  expect(results.violations).toEqual([]);
});
test("keyboard navigation opens and closes a case with focus restored", async ({
  page,
}) => {
  await page.goto("./#queue");
  await expect(page.locator(".case-link").first()).toBeVisible();
  await page.getByRole("link", { name: "Skip to workspace" }).focus();
  await page.keyboard.press("Enter");
  await expect(page.locator("main")).toBeFocused();
  await expect(
    page.getByRole("heading", { name: "Put the right cases first." }),
  ).toBeVisible();
  await page.locator(".case-link").first().focus();
  await page.keyboard.press("Enter");
  await expect(page.locator("#case-dialog")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.locator("#case-dialog")).not.toBeVisible();
  await expect(page.locator(".case-link").first()).toBeFocused();
});
