from __future__ import annotations

from typing import Any

from app.config import TelegramSettings


class DashboardScreenshotCapture:
    """Capture the public global dashboard card without personal account content."""

    def __init__(self, settings: TelegramSettings, logger: Any) -> None:
        self.settings = settings
        self.logger = logger

    async def capture(self) -> bytes | None:
        if not self.settings.dashboard_screenshot_enabled:
            return None
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            self.logger.warning(
                "TELEGRAM_DASHBOARD_SCREENSHOT_FAILED reason=playwright_unavailable"
            )
            return None

        timeout_ms = int(
            self.settings.dashboard_screenshot_timeout_seconds * 1000
        )
        try:
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(
                    headless=True,
                    args=["--no-sandbox", "--disable-dev-shm-usage"],
                )
                try:
                    page = await browser.new_page(
                        viewport={"width": 760, "height": 1000},
                        device_scale_factor=1,
                        color_scheme="light",
                    )
                    page.set_default_timeout(timeout_ms)
                    await page.goto(
                        self.settings.dashboard_url,
                        wait_until="domcontentloaded",
                        timeout=timeout_ms,
                    )
                    locator = page.locator(self.settings.dashboard_selector)
                    await locator.wait_for(state="visible", timeout=timeout_ms)
                    await page.wait_for_function(
                        """
                        selector => document.querySelector(selector)?.dataset.snapshotReady
                            === "true"
                        """,
                        arg=self.settings.dashboard_selector,
                        timeout=timeout_ms,
                    )
                    await page.evaluate(
                        "document.fonts?.ready ? document.fonts.ready : Promise.resolve()"
                    )
                    screenshot = await locator.screenshot(
                        type="png",
                        animations="disabled",
                        scale="css",
                        style=(
                            f"{self.settings.dashboard_selector} {{"
                            " padding: 0 12px 12px !important;"
                            " }"
                        ),
                        timeout=timeout_ms,
                    )
                    self.logger.info(
                        "TELEGRAM_DASHBOARD_SCREENSHOT_CAPTURED bytes=%s",
                        len(screenshot),
                    )
                    return screenshot
                finally:
                    await browser.close()
        except Exception as exc:
            self.logger.warning(
                "TELEGRAM_DASHBOARD_SCREENSHOT_FAILED error=%s",
                type(exc).__name__,
            )
            return None
