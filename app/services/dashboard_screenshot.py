from __future__ import annotations

from typing import Any

from app.config import TelegramSettings


DESKTOP_VIEWPORT = {"width": 1440, "height": 980}

# This promise is a real event listener (with a MutationObserver fallback), not
# a fixed sleep.  It resolves only after the dashboard has explicitly completed
# a successful live-data render and all required statistics contain final text.
WAIT_FOR_DASHBOARD_READY = r"""
({ selector, timeoutMs }) => new Promise((resolve, reject) => {
    const deadline = Date.now() + timeoutMs;
    let observer;
    let timer;
    let stabilityTimer;

    const cleanup = () => {
        observer?.disconnect();
        window.removeEventListener("dashboard:snapshot-ready", scheduleCheck);
        clearTimeout(timer);
        clearTimeout(stabilityTimer);
    };
    const ready = () => {
        const captureRoot = document.querySelector(selector);
        const stateRoot = captureRoot?.closest("[data-snapshot-state]");
        if (!captureRoot || !stateRoot
            || stateRoot.dataset.snapshotReady !== "true"
            || stateRoot.dataset.snapshotState !== "ready"
            || document.readyState !== "complete") return false;
        const loader = document.getElementById("smart-loader");
        if (loader?.classList.contains("active")) return false;
        const required = [
            "registered-traders", "active-traders", "model-trades-today",
            "open-model-trades", "model-pl-martingale", "model-maximum-stake",
            "model-pl-fixed", "model-flat-stake", "total-trades", "total-wins",
            "total-losses", "win-rate", "session-clock"
        ];
        return required.every(id => {
            const text = document.getElementById(id)?.textContent?.trim();
            return text && !/loading|connecting|unavailable|--:--|^—$/i.test(text);
        });
    };
    const check = () => {
        if (!ready()) return;
        // Stop observing before the quiet window. The countdown clock updates
        // every second forever and must not keep postponing an otherwise fully
        // rendered capture.
        observer?.disconnect();
        window.removeEventListener("dashboard:snapshot-ready", scheduleCheck);
        clearTimeout(stabilityTimer);
        // Require a quiet render window so a websocket update cannot be caught
        // halfway through updating related figures.
        stabilityTimer = setTimeout(() => {
            if (!ready()) {
                observer?.observe(document.documentElement, {
                    subtree: true, childList: true, characterData: true, attributes: true
                });
                window.addEventListener("dashboard:snapshot-ready", scheduleCheck);
                return;
            }
            cleanup();
            resolve(true);
        }, 1500);
    };
    const scheduleCheck = () => queueMicrotask(check);

    observer = new MutationObserver(scheduleCheck);
    observer.observe(document.documentElement, {
        subtree: true, childList: true, characterData: true, attributes: true
    });
    window.addEventListener("dashboard:snapshot-ready", scheduleCheck);
    timer = setTimeout(() => {
        cleanup();
        reject(new Error(`dashboard_not_ready_after_${timeoutMs}ms`));
    }, Math.max(0, deadline - Date.now()));
    check();
})
"""


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
                        # Keep Telegram independent of the visitor's device:
                        # hourly reports always use the full desktop layout.
                        viewport=DESKTOP_VIEWPORT,
                        device_scale_factor=1,
                        color_scheme="dark",
                    )
                    page.set_default_timeout(timeout_ms)
                    await page.goto(
                        self.settings.dashboard_url,
                        wait_until="domcontentloaded",
                        timeout=timeout_ms,
                    )
                    locator = page.locator(self.settings.dashboard_selector)
                    await locator.wait_for(state="visible", timeout=timeout_ms)
                    await page.evaluate(
                        WAIT_FOR_DASHBOARD_READY,
                        {
                            "selector": self.settings.dashboard_selector,
                            "timeoutMs": timeout_ms,
                        },
                    )
                    await page.evaluate(
                        "document.fonts?.ready ? document.fonts.ready : Promise.resolve()"
                    )
                    await page.wait_for_timeout(250)
                    screenshot = await locator.screenshot(
                        type="png",
                        animations="disabled",
                        scale="css",
                        style="""
                            html { background: #020912 !important; }
                            #smart-loader { display: none !important; }
                            #telegram-dashboard-snapshot {
                                padding: 2px 12px 12px !important;
                                background: #020912 !important;
                            }
                        """,
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
