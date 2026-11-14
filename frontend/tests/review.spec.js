import { test, expect } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

const input = process.env.KOPEYKA_TEST_INPUT;
test.skip(!input, "Set KOPEYKA_TEST_INPUT to the original case directory");
test("explain, save a request, append a document and reopen immutable versions", async ({
  page,
}) => {
  test.setTimeout(180000);
  const names = fs.readdirSync(input);
  const report = names.find((n) => n.startsWith("01_"));
  const february = names.find((n) => n.startsWith("02_02"));
  const documents = names
    .filter((n) => /^(0[2-9]|1[0-5])_/.test(n) && n !== february)
    .map((n) => path.join(input, n));
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto(process.env.KOPEYKA_TEST_URL || "http://127.0.0.1:8503");
  await page
    .getByLabel("Карточка КФЗ", { exact: true })
    .setInputFiles(path.join(input, report));
  await page
    .getByLabel("Подтверждающие документы", { exact: true })
    .setInputFiles(documents);
  const firstResponse = page.waitForResponse(
    (r) => r.url().endsWith("/api/check") && r.request().method() === "POST",
  );
  await page.getByRole("button", { name: "Начать сверку" }).click();
  const first = await (await firstResponse).json();
  await expect(
    page.getByRole("heading", { name: "Разобраться до копейки", exact: true }),
  ).toBeVisible();
  await expect(page.locator(".review-fact")).toContainText(
    "Причина расхождения пока не установлена",
  );
  await expect(page.locator(".proof-list li")).toHaveCount(4);
  await page.locator(".review").scrollIntoViewIfNeeded();
  await page.screenshot({
    path: "../outputs/review-analysis.png",
    fullPage: true,
  });
  await page.getByRole("tab", { name: "2. Запросить уточнение" }).click();
  const text = page.getByLabel("Текст запроса", { exact: true });
  await expect(text).toHaveValue(/Коллеги, добрый день!/);
  expect(await text.inputValue()).toContain("12 500,00");
  expect(await text.inputValue()).toContain("стр.");
  await text.fill(
    (await text.inputValue()) + "\nКомментарий: просьба уточнить регистр.",
  );
  await page
    .getByRole("button", { name: "Сохранить черновик", exact: true })
    .click();
  await expect(page.getByRole("status")).toContainText("Черновик сохранён");
  await page.screenshot({
    path: "../outputs/review-request.png",
    fullPage: true,
  });
  await page.getByRole("tab", { name: "3. Добавить ответ" }).click();
  await page
    .getByLabel("Дополнительные документы", { exact: true })
    .setInputFiles(path.join(input, february));
  await page
    .getByLabel("Комментарий к версии")
    .fill("Получена ведомость за февраль");
  const secondResponse = page.waitForResponse(
    (r) => r.url().endsWith("/documents") && r.request().method() === "POST",
  );
  await page
    .getByRole("button", { name: "Добавить и сравнить версии" })
    .click();
  const second = await (await secondResponse).json();
  await expect(
    page.getByRole("heading", { name: "Что изменилось · версия 1 → 2" }),
  ).toBeVisible();
  const payroll = page
    .locator(".comparison-table tr")
    .filter({ hasText: "Оплата труда" });
  await expect(payroll).toContainText("Недостаточно данных");
  await expect(payroll).toContainText("Совпадение суммы подтверждено");
  const other = page
    .locator(".comparison-table tr")
    .filter({ hasText: "0800" });
  await expect(other).toContainText("Без изменений");
  await page.screenshot({
    path: "../outputs/review-comparison.png",
    fullPage: true,
  });
  await page
    .getByLabel("Версия сверки", { exact: true })
    .selectOption(first.session);
  await page.getByRole("tab", { name: "2. Запросить уточнение" }).click();
  await expect(page.getByLabel("Текст запроса", { exact: true })).toHaveValue(
    /Комментарий: просьба уточнить регистр/,
  );
  await page
    .getByLabel("Версия сверки", { exact: true })
    .selectOption(second.session);
  await expect(
    page.getByRole("heading", { name: "Что изменилось · версия 1 → 2" }),
  ).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.locator(".review").scrollIntoViewIfNeeded();
  await page.screenshot({
    path: "../outputs/review-mobile.png",
    fullPage: true,
  });
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth + 1,
    ),
  ).toBe(true);
  expect(errors).toEqual([]);
});
