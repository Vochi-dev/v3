import asyncio
from playwright.async_api import async_playwright

OAUTH_URL = "https://c2ccdn.coolkit.cc/oauth/index.html?clientId=yjbs7ZRaIgNiqJ9uINiXjKcX01czdTdB&redirectUrl=https%3A%2F%2Fbot.vochi.by%2Fewelink-callback%2F&grantType=authorization_code&state=1752480099&nonce=1752480099&showQRCode=false"
LOGIN = "evgeny.baevski@vochi.by"
PASSWORD = "DrQsfLWHXN"

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto(OAUTH_URL)
        try:
            await page.wait_for_selector('input[type="text"]', timeout=7000)
            await page.fill('input[type="text"]', LOGIN)
            await page.fill('input[type="password"]', PASSWORD)
            await page.screenshot(path="form_filled.png")
            await page.click('button:has-text("Oauth и вход в систему")')
        except Exception as e:
            print(f"Ошибка при заполнении формы: {e}")
            await page.screenshot(path="form_error.png")
        # Ждём редиректа или появления кода
        await page.wait_for_timeout(15000)
        print("Текущий URL:", page.url)
        await browser.close()

if __name__ == "__main__":
    asyncio.run(main()) 