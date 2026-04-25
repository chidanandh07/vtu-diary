import json
import asyncio
import functools
from datetime import date, timedelta
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

# ─── CONFIG ───────────────────────────────────────────────────────────────────
BASE_URL         = "https://vtu.internyet.in"
DATA_FILE        = "diary_data.json"
MAX_RETRIES      = 3          # retries per date on network / timeout errors
RETRY_DELAY      = 5          # seconds between retries
LOGIN_KEYWORDS   = ["sign-in", "login", "sign_in", "log-in"]
SESSION_KEYWORDS = ["session expired", "logged out", "please login",
                    "please sign in", "unauthorized"]
# ──────────────────────────────────────────────────────────────────────────────

MONTH_NAMES = ["Jan","Feb","Mar","Apr","May","Jun",
               "Jul","Aug","Sep","Oct","Nov","Dec"]


# ═══════════════════════════════════════════════════════════════════════════════
#  HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def ordinal(n: int) -> str:
    if 11 <= (n % 100) <= 13:
        return f"{n}th"
    return f"{n}{['th','st','nd','rd','th','th','th','th','th','th'][n % 10]}"


def day_aria_label(d: date) -> str:
    label = f"{d.strftime('%A')}, {d.strftime('%B')} {ordinal(d.day)}, {d.year}"
    if d == date.today():
        label = f"Today, {label}"
    return label


def get_weekdays(start: date, end: date) -> list:
    days, current = [], start
    while current <= end:
        if current.weekday() not in (5, 6):
            days.append(current)
        current += timedelta(days=1)
    return days


# ═══════════════════════════════════════════════════════════════════════════════
#  SESSION / NETWORK GUARDS
# ═══════════════════════════════════════════════════════════════════════════════

def is_login_page(url: str) -> bool:
    url_lower = url.lower()
    return any(kw in url_lower for kw in LOGIN_KEYWORDS)


async def is_logged_out(page) -> bool:
    """Detect session expiry by URL drift or body text."""
    if is_login_page(page.url):
        return True
    try:
        body = (await page.inner_text("body")).lower()
        if any(kw in body for kw in SESSION_KEYWORDS):
            return True
    except Exception:
        pass
    return False


async def wait_for_network(page, timeout: int = 30_000):
    """
    Wait for networkidle; on timeout fall back to domcontentloaded
    after a short pause so a slow server doesn't kill the run.
    """
    try:
        await page.wait_for_load_state("networkidle", timeout=timeout)
    except PlaywrightTimeout:
        print("  ⏳ Network slow — retrying with domcontentloaded...")
        await page.wait_for_timeout(2000)
        await page.wait_for_load_state("domcontentloaded", timeout=timeout)


# ═══════════════════════════════════════════════════════════════════════════════
#  AUTH
# ═══════════════════════════════════════════════════════════════════════════════

async def login(page, username: str, password: str):
    """Navigate to /sign-in and log in."""
    await page.goto(f"{BASE_URL}/sign-in")
    await wait_for_network(page)

    await page.get_by_placeholder("Enter your email address").fill(username)
    await page.get_by_placeholder("Enter your password").fill(password)
    await page.get_by_role("button", name="Sign In").click()

    await page.wait_for_url(lambda url: "sign-in" not in url, timeout=20000)
    print("✅ Logged in successfully")


# ═══════════════════════════════════════════════════════════════════════════════
#  DIARY NAVIGATION
# ═══════════════════════════════════════════════════════════════════════════════

async def select_internship_and_date(page, internship_partial_name: str,
                                     diary_date: date, username: str, password: str):
    """Go to diary page, pick internship + date, click Continue."""
    await page.goto(f"{BASE_URL}/dashboard/student/student-diary")
    await wait_for_network(page)

    # Guard: navigating home might have redirected us to login
    if await is_logged_out(page):
        print("  🔐 Redirected to login during navigation — re-logging in...")
        await login(page, username, password)
        await page.goto(f"{BASE_URL}/dashboard/student/student-diary")
        await wait_for_network(page)

    # 1. Select Internship dropdown
    await page.locator("#internship_id").click()
    await page.wait_for_selector("role=option", timeout=8000)
    await page.get_by_role("option", name=internship_partial_name, exact=False).first.click()

    # 2. Open the date picker
    await page.get_by_role("button", name="Pick a Date").click()
    await page.wait_for_selector("role=dialog", timeout=8000)

    # 3. Set month
    await page.get_by_label("Choose the Month").select_option(MONTH_NAMES[diary_date.month - 1])
    await page.wait_for_timeout(300)

    # 4. Set year
    await page.get_by_label("Choose the Year").select_option(str(diary_date.year))
    await page.wait_for_timeout(300)

    # 5. Click the correct day button
    await page.get_by_role("button", name=day_aria_label(diary_date), exact=True).click()

    # 6. Click Continue
    await page.get_by_role("button", name="Continue").click()
    await page.wait_for_timeout(2000)
    print(f"  📅 Opened form for {diary_date.strftime('%d %b %Y')}")


# ═══════════════════════════════════════════════════════════════════════════════
#  EXISTING ENTRY DETECTION & EDITING
# ═══════════════════════════════════════════════════════════════════════════════

async def entry_exists(page) -> bool:
    """Return True if an entry already exists for the selected date."""
    try:
        if await page.get_by_role("button", name="Edit", exact=False).count() > 0:
            return True
    except Exception:
        pass
    try:
        body = await page.inner_text("body")
        keywords = ["already submitted", "entry exists", "already filled", "edit entry"]
        if any(kw in body.lower() for kw in keywords):
            return True
    except Exception:
        pass
    return False


async def click_edit(page):
    """Click the Edit button to enter edit mode."""
    await page.get_by_role("button", name="Edit", exact=False).first.click()
    await wait_for_network(page)
    await page.wait_for_timeout(800)
    print("  ✏️  Switched to edit mode")


async def read_current_entry(page) -> dict:
    """Read live form values for comparison."""
    current = {}
    for key, selector in [
        ("work_summary",    "textarea[placeholder*='work you did']"),
        ("hours_worked",    "input[type='number']"),
        ("learnings",       "textarea[placeholder*='learn or ship']"),
        ("reference_links", "textarea[placeholder*='relevant links']"),
        ("blockers",        "textarea[placeholder*='slowed you down']"),
    ]:
        try:
            current[key] = await page.locator(selector).first.input_value()
        except Exception:
            current[key] = ""
    return current


def needs_update(current: dict, desired: dict) -> bool:
    fields = ["work_summary", "learnings", "reference_links", "blockers", "hours_worked"]
    return any(
        str(current.get(f, "")).strip() != str(desired.get(f, "")).strip()
        for f in fields
    )


async def clear_skill_tags(page):
    """Remove all existing skill chips before adding fresh ones."""
    for selector in [
        "[aria-label*='Remove'], [aria-label*='remove'], button.tag-remove",
        "button:has-text('×'), button:has-text('✕'), span:has-text('×')",
    ]:
        try:
            buttons = page.locator(selector)
            count = await buttons.count()
            for _ in range(count):
                await buttons.first.click()
                await page.wait_for_timeout(300)
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════════════════════
#  FORM FILL & SUBMIT
# ═══════════════════════════════════════════════════════════════════════════════

async def fill_and_submit_entry(page, entry: dict, is_edit: bool = False):
    """Fill all mandatory fields (+ optional if present) and save."""
    await wait_for_network(page)

    async def fill(selector: str, value: str):
        field = page.locator(selector).first
        await field.triple_click()
        await field.fill(value)

    await fill("textarea[placeholder*='work you did']",  entry["work_summary"])
    await fill("input[type='number']",                   str(entry["hours_worked"]))
    await fill("textarea[placeholder*='learn or ship']", entry["learnings"])

    for selector, key in [
        ("textarea[placeholder*='relevant links']",  "reference_links"),
        ("textarea[placeholder*='slowed you down']", "blockers"),
    ]:
        try:
            await fill(selector, entry.get(key, ""))
        except Exception:
            pass

    # ── Skills ─────────────────────────────────────────────────────────────────
    if is_edit:
        await clear_skill_tags(page)
        await page.wait_for_timeout(500)

    skills_input = page.locator("input[role='combobox'][aria-autocomplete='list']").first
    for skill in entry.get("skills", []):
        await skills_input.fill(skill)
        await page.wait_for_timeout(600)
        option = page.get_by_role("option", name=skill, exact=False).first
        try:
            await option.click(timeout=2000)
        except PlaywrightTimeout:
            await skills_input.press("Enter")
        await page.wait_for_timeout(300)

    # ── Save ───────────────────────────────────────────────────────────────────
    await page.get_by_role("button", name="Save").first.click()
    await page.wait_for_timeout(2000)
    print("  ✅ Entry saved")


# ═══════════════════════════════════════════════════════════════════════════════
#  PER-DATE ORCHESTRATOR  (retry + re-login)
# ═══════════════════════════════════════════════════════════════════════════════

async def process_date(page, internship_name: str, d: date, entry: dict,
                       username: str, password: str):
    """
    Full flow for one date:
      • Retries up to MAX_RETRIES times on network / timeout errors.
      • Re-logs in immediately (without burning a retry) on session expiry.
      • Takes a screenshot on unrecoverable failure.
    """
    date_key = d.strftime("%Y-%m-%d")
    print(f"\n📝 Processing {date_key}...")

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            # ── Proactive session check ────────────────────────────────────────
            if await is_logged_out(page):
                print(f"  🔐 Not logged in (attempt {attempt}) — re-logging in...")
                await login(page, username, password)

            await select_internship_and_date(
                page, internship_name, d, username, password)

            # ── Post-navigation session guard ──────────────────────────────────
            if await is_logged_out(page):
                raise RuntimeError("Redirected to login after navigation")

            # ── Existing entry? ────────────────────────────────────────────────
            if await entry_exists(page):
                print("  ℹ️  Entry already exists — checking for changes...")
                await click_edit(page)

                if await is_logged_out(page):
                    raise RuntimeError("Session expired while opening edit form")

                current = await read_current_entry(page)
                if needs_update(current, entry):
                    print("  🔄 Changes detected — updating entry...")
                    await fill_and_submit_entry(page, entry, is_edit=True)
                else:
                    print("  ✔️  No changes needed — skipping")

            # ── New entry ──────────────────────────────────────────────────────
            else:
                if "create-diary-entry" not in page.url:
                    await page.wait_for_url("**/create-diary-entry**", timeout=12000)
                await fill_and_submit_entry(page, entry, is_edit=False)

            return  # ← success; stop retrying

        # ── Network / timeout → retry ──────────────────────────────────────────
        except (PlaywrightTimeout, OSError, ConnectionError) as exc:
            print(f"  ⚠️  Network/timeout error "
                  f"(attempt {attempt}/{MAX_RETRIES}): {type(exc).__name__}: {exc}")
            if attempt < MAX_RETRIES:
                print(f"  💤 Waiting {RETRY_DELAY} s before retry...")
                await asyncio.sleep(RETRY_DELAY)
            else:
                print(f"  ❌ All {MAX_RETRIES} attempts exhausted for {date_key}.")
                try:
                    await page.screenshot(path=f"error_{date_key}.png")
                except Exception:
                    pass

        # ── Session expired → re-login immediately (doesn't count as a retry) ──
        except RuntimeError as exc:
            print(f"  🔐 Session error: {exc} — re-logging in and retrying...")
            try:
                await login(page, username, password)
                # Re-login worked; loop continues (attempt counter NOT incremented
                # — we achieve this by not hitting the retry counter here)
                continue
            except Exception as login_exc:
                print(f"  ❌ Re-login failed: {login_exc}")
                try:
                    await page.screenshot(path=f"error_{date_key}.png")
                except Exception:
                    pass
                return  # can't recover; move to next date

        # ── Any other unexpected error → log and move on ───────────────────────
        except Exception as exc:
            print(f"  ❌ Unexpected error on {date_key}: "
                  f"{type(exc).__name__}: {exc}")
            try:
                await page.screenshot(path=f"error_{date_key}.png")
            except Exception:
                pass
            return


# ═══════════════════════════════════════════════════════════════════════════════
#  ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

async def run():
    with open(DATA_FILE, "r") as f:
        config = json.load(f)

    username        = config["credentials"]["username"]
    password        = config["credentials"]["password"]
    internship_name = config["internship_name"]

    entry_dates = sorted(
        [date.fromisoformat(d) for d in config.get("entries", {}).keys()])
    if entry_dates:
        start_date, end_date = entry_dates[0], entry_dates[-1]
    else:
        start_date, end_date = date(2026, 1, 1), date.today()

    weekdays = get_weekdays(start_date, end_date)
    print(f"📆 Total weekdays to process: {len(weekdays)}")

    entries       = config.get("entries", {})
    default_entry = config["default_entry"]

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, slow_mo=150)
        context = await browser.new_context()

        # ── Global timeouts ────────────────────────────────────────────────────
        context.set_default_timeout(30_000)            # 30 s per action
        context.set_default_navigation_timeout(40_000) # 40 s per navigation

        page = await context.new_page()

        # ── Initial login with retry ───────────────────────────────────────────
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                await login(page, username, password)
                break
            except Exception as exc:
                print(f"  ⚠️  Login attempt {attempt}/{MAX_RETRIES} failed: {exc}")
                if attempt == MAX_RETRIES:
                    print("  ❌ Could not log in. Exiting.")
                    await browser.close()
                    return
                print(f"  💤 Waiting {RETRY_DELAY} s...")
                await asyncio.sleep(RETRY_DELAY)

        # ── Process all dates ──────────────────────────────────────────────────
        for d in weekdays:
            date_key = d.strftime("%Y-%m-%d")
            entry    = entries.get(date_key, default_entry)
            await process_date(page, internship_name, d, entry, username, password)
            await page.wait_for_timeout(800)

        await browser.close()
        print("\n🎉 All done!")


if __name__ == "__main__":
    asyncio.run(run())
