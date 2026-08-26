import asyncio
import logging

from core.browser import douyin_network_modes, get_browser


logger = logging.getLogger(__name__)


CHAT_PAGE_URL = "https://creator.douyin.com/creator-micro/data/following/chat"
FRIENDS_TAB_SELECTOR = 'xpath=//*[@id="sub-app"]/div/div/div[1]/div[2]'
TARGET_SELECTOR = (
    'xpath=//*[@id="sub-app"]/div/div[1]/div[2]/div[2]'
    '//div[contains(@class, "semi-list-item-body semi-list-item-body-flex-start")]'
)
SCROLLABLE_FRIENDS_SELECTOR = (
    'xpath=//*[@id="sub-app"]/div/div[1]/div[2]/div[2]/div/div/div[3]/div/div/div/ul/div'
)
NO_MORE_SELECTOR = 'xpath=//div[contains(@class, "no-more-tip-ftdJnu")]'
LOADING_SELECTOR = 'xpath=//div[contains(@class, "semi-spin")]'
FIRST_FRIEND_SELECTOR = (
    'xpath=//*[@id="sub-app"]/div/div/div[2]/div[2]/div/div/div[1]/div/div/div/ul/div/div/div[1]/li/div'
)
FRIEND_NAME_SELECTOR = """xpath=.//span[contains(@class, "item-header-name-")]"""
LOGIN_MASK_SELECTORS = [".login-mask", ".login-guide-container", ".login-img-code-wrapper"]
NON_LOGIN_DIALOG_DISMISS_TEXTS = (
    "我知道了",
    "知道了",
    "好的",
    "确定",
    "确认",
    "稍后再说",
    "关闭",
)
NON_LOGIN_DIALOG_CLOSE_SELECTORS = (
    ".semi-modal-close",
    'button[aria-label="Close"]',
    'button[aria-label="关闭"]',
    '[aria-label="Close"]',
    '[aria-label="关闭"]',
)
FRIEND_LIST_EMPTY_ROUNDS = 6
FRIEND_LIST_EMPTY_WAIT_SECONDS = 1.5


def update_collection_progress(new_names_count, no_more_visible, scroll_moved, idle_rounds, stuck_rounds, idle_limit=5, stuck_limit=2):
    next_idle_rounds = 0 if new_names_count > 0 else idle_rounds + 1
    next_stuck_rounds = 0 if scroll_moved else stuck_rounds + 1
    should_stop = no_more_visible or next_idle_rounds >= idle_limit or next_stuck_rounds >= stuck_limit
    return should_stop, next_idle_rounds, next_stuck_rounds


async def _ensure_logged_in(page):
    for selector in LOGIN_MASK_SELECTORS:
        try:
            locator = page.locator(selector).first
            if await locator.count() > 0 and await locator.is_visible():
                raise RuntimeError("账号登录已失效，请重新扫码登录")
        except RuntimeError:
            raise
        except Exception:
            continue


async def _dismiss_non_login_dialogs(page):
    for text in NON_LOGIN_DIALOG_DISMISS_TEXTS:
        try:
            locator = page.get_by_text(text, exact=False).first
            if await locator.count() > 0 and await locator.is_visible():
                await locator.click(timeout=3000)
                await asyncio.sleep(1)
                return True
        except Exception:
            continue

    for selector in NON_LOGIN_DIALOG_CLOSE_SELECTORS:
        try:
            locator = page.locator(selector).first
            if await locator.count() > 0 and await locator.is_visible():
                await locator.click(timeout=3000)
                await asyncio.sleep(1)
                return True
        except Exception:
            continue
    return False


async def _click_friends_tab(page):
    await page.wait_for_selector("#sub-app", timeout=30000)
    try:
        await page.locator(FRIENDS_TAB_SELECTOR).click(timeout=10000)
        return
    except Exception:
        pass

    try:
        await page.get_by_text("朋友私信", exact=True).click(timeout=10000)
        return
    except Exception as exc:
        raise RuntimeError("未找到朋友私信标签") from exc


async def _friend_list_dom_summary(page):
    return await page.evaluate(
        """() => {
            const sub = document.querySelector('#sub-app');
            if (!sub) {
                return { hasSubApp: false, ulCount: 0, liCount: 0, listItemCount: 0, nameSpanCount: 0, text: '' };
            }
            return {
                hasSubApp: true,
                ulCount: sub.querySelectorAll('ul').length,
                liCount: sub.querySelectorAll('li').length,
                listItemCount: sub.querySelectorAll('[class*="list-item"]').length,
                nameSpanCount: sub.querySelectorAll('[class*="item-header-name"]').length,
                text: (sub.innerText || '').split(String.fromCharCode(10)).join(' ').slice(0, 500),
            };
        }"""
    )


async def _wait_for_first_friend_or_empty(page):
    for _ in range(FRIEND_LIST_EMPTY_ROUNDS):
        await _dismiss_non_login_dialogs(page)
        first_friend = page.locator(FIRST_FRIEND_SELECTOR).first
        try:
            if await first_friend.count() > 0 and await first_friend.is_visible():
                await first_friend.click()
                await asyncio.sleep(2)
                return True
        except Exception:
            pass

        summary = await _friend_list_dom_summary(page)
        has_any_list_content = any(
            int(summary.get(key) or 0) > 0
            for key in ("ulCount", "liCount", "listItemCount", "nameSpanCount")
        )
        if not has_any_list_content:
            await asyncio.sleep(FRIEND_LIST_EMPTY_WAIT_SECONDS)
            continue

        # The current DOM has list content but not the historical first-friend XPath.
        # Let the collector below try the more general TARGET_SELECTOR path.
        return False
    return False


async def _wait_for_chat_or_login(page, timeout_seconds=30):
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    while asyncio.get_running_loop().time() < deadline:
        await _ensure_logged_in(page)
        try:
            if await page.locator("#sub-app").count() > 0:
                return
        except Exception:
            pass
        await asyncio.sleep(0.5)
    raise RuntimeError("chat page did not load within timeout")


async def collect_friend_names(page):
    await _wait_for_chat_or_login(page)
    await _click_friends_tab(page)
    await asyncio.sleep(1)

    has_first_friend = await _wait_for_first_friend_or_empty(page)
    if not has_first_friend:
        summary = await _friend_list_dom_summary(page)
        has_any_list_content = any(
            int(summary.get(key) or 0) > 0
            for key in ("ulCount", "liCount", "listItemCount", "nameSpanCount")
        )
        if not has_any_list_content:
            return []

    found_names = []
    seen_names = set()
    idle_rounds = 0
    stuck_rounds = 0

    while True:
        target_elements = await page.locator(TARGET_SELECTOR).all()
        new_names_count = 0
        for element in target_elements:
            try:
                name = (await element.locator(FRIEND_NAME_SELECTOR).inner_text()).strip()
            except Exception:
                continue
            if not name or name in seen_names:
                continue
            seen_names.add(name)
            found_names.append(name)
            new_names_count += 1

        no_more = page.locator(NO_MORE_SELECTOR).first
        if await no_more.count() > 0 and await no_more.is_visible():
            return found_names

        loading = page.locator(LOADING_SELECTOR).first
        if await loading.count() > 0 and await loading.is_visible():
            await asyncio.sleep(1.5)

        scrollable_element = await page.locator(SCROLLABLE_FRIENDS_SELECTOR).element_handle()
        if not scrollable_element:
            if found_names:
                return found_names
            raise RuntimeError("未找到好友列表滚动容器")

        before_top = await page.evaluate("(element) => element.scrollTop", scrollable_element)
        await page.evaluate("(element) => element.scrollTop += 800", scrollable_element)
        await asyncio.sleep(1.5)
        after_top = await page.evaluate("(element) => element.scrollTop", scrollable_element)

        should_stop, idle_rounds, stuck_rounds = update_collection_progress(
            new_names_count=new_names_count,
            no_more_visible=False,
            scroll_moved=after_top > before_top,
            idle_rounds=idle_rounds,
            stuck_rounds=stuck_rounds,
        )
        if should_stop:
            return found_names


async def _fetch_account_friends_once(account, network_mode):
    cookies = list(account.get("cookies") or [])
    playwright = browser = context = page = None
    try:
        playwright, browser = await get_browser(GUI=False, network_mode=network_mode)
        context = await browser.new_context()
        context.set_default_navigation_timeout(120000)
        context.set_default_timeout(120000)
        page = await context.new_page()
        await context.add_cookies(cookies)
        await page.goto(CHAT_PAGE_URL, wait_until="commit", timeout=30000)
        await asyncio.sleep(1)

        friends = await collect_friend_names(page)
        return friends
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(f"刷新好友列表失败，请重试：{exc}") from exc
    finally:
        if page:
            await page.close()
        if context:
            await context.close()
        if browser:
            await browser.close()
        if playwright:
            await playwright.stop()


async def fetch_account_friends(account):
    cookies = list(account.get("cookies") or [])
    if not cookies:
        raise RuntimeError("account has no cookies; scan login QR code first")

    modes = douyin_network_modes()
    last_error = None
    for index, network_mode in enumerate(modes):
        try:
            friends = await _fetch_account_friends_once(account, network_mode)
            logger.info(
                "Friend refresh route=%s count=%s attempt=%s/%s",
                network_mode,
                len(friends),
                index + 1,
                len(modes),
            )
            if friends or index == len(modes) - 1:
                return friends
            logger.warning(
                "Friend refresh route=%s returned zero friends; trying next route",
                network_mode,
            )
        except RuntimeError as exc:
            text = str(exc).lower()
            if any(marker in text for marker in ("login", "cookie", "scan", "登录", "扫码")):
                raise
            last_error = exc
            logger.warning("Friend refresh route=%s failed; trying next route: %s", network_mode, exc)
        except Exception as exc:
            last_error = exc
            logger.warning("Friend refresh route=%s failed; trying next route: %s", network_mode, exc)
    raise RuntimeError(f"friend refresh failed after routes {modes}: {last_error}")
