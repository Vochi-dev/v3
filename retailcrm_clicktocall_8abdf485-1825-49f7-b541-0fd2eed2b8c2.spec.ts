
import { test } from '@playwright/test';
import { expect } from '@playwright/test';

test('RetailCRM_ClickToCall_2025-08-09', async ({ page, context }) => {
  
    // Navigate to URL
    await page.goto('https://evgenybaevski.retailcrm.ru');

    // Navigate to URL
    await page.goto('https://evgenybaevski.retailcrm.ru');

    // Fill input field
    await page.fill('input[name="username"]', 'evgeny.baevski@gmail.com');

    // Fill input field
    await page.fill('#login-email', 'evgeny.baevski@gmail.com');

    // Fill input field
    await page.fill('#login-password', '47916565+');

    // Click element
    await page.click('button:has-text("Войти")');

    // Navigate to URL
    await page.goto('https://evgenybaevski.retailcrm.ru/customers');

    // Click element
    await page.click('text="Иванов Иван Иванович"');

    // Hover over element
    await page.hover('text="+375296254070"');

    // Click element
    await page.click('a.make-call[data-phone="+375296254070"]');
});