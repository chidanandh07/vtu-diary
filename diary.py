import json
import asyncio
from datetime import date, timedelta
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout

# ─── CONFIG ───────────────────────────────────────────────────────────────────
BASE_URL  = "https://vtu.internyet.in"
DATA_FILE = "diary_data.json"
# ──────────────────────────────────────────────────────────────────────────────

MONTH_NAMES = ["Jan","Feb","Mar","Apr","May","Jun",
               "Jul","Aug","Sep","Oct","Nov","Dec"]

def ordinal(n: int) -> str:
    if 11 <= (n % 100) <= 13:
        return f"{n}th"
    return f"{n}{['th','st','nd','rd','th','th','th','th','th','th'][n % 10]}"

def day_aria_label(d: date) -> str:
    """
    Builds the exact aria-label the calendar uses for each day button.
    e.g.  'Monday, January 5th, 2026'
    Today gets prefix: 'Today, Sunday, March 22nd, 2026'
    """
    label = f"{d.strftime('%A')}, {d.strftime('%B')} {ordinal(d.day)}, {d.year}"
    if d == date.today():
        label = f"Today, {label}"
    return label

def get_weekdays(start: date, end: date) -> list:
    days, current = [], start
    while current <= end:
        if current.weekday() not in (5, 6):   # skip Sat=5, Sun=6
            days.append(current)
        current += timedelta(days=1)
    return days


async def login(page, username: str, password: str):
    """Navigate to /sign-in and log in."""
    await page.goto(f"{BASE_URL}/sign-in")
    await page.wait_for_load_state("networkidle")

    await page.get_by_placeholder("Enter your email address").fill(username)
    await page.get_by_placeholder("Enter your password").fill(password)
    await page.get_by_role("button", name="Sign In").click()

    # Wait until redirected away from /sign-in
    await page.wait_for_url(lambda url: "sign-in" not in url, timeout=15000)
    print("✅ Logged in successfully")


async def select_internship_and_date(page, internship_partial_name: str, diary_date: date):
    """Go to diary page, pick internship + date, click Continue."""
    await page.goto(f"{BASE_URL}/dashboard/student/student-diary")
    await page.wait_for_load_state("networkidle")

    # 1. Select Internship dropdown
    await page.locator("#internship_id").click()
    await page.wait_for_selector("role=option", timeout=5000)
    await page.get_by_role("option", name=internship_partial_name, exact=False).first.click()

    # 2. Open the date picker
    await page.get_by_role("button", name="Pick a Date").click()
    await page.wait_for_selector("role=dialog", timeout=5000)

    # 3. Set month via <select aria-label="Choose the Month">
    await page.get_by_label("Choose the Month").select_option(MONTH_NAMES[diary_date.month - 1])
    await page.wait_for_timeout(300)

    # 4. Set year via <select aria-label="Choose the Year">
    await page.get_by_label("Choose the Year").select_option(str(diary_date.year))
    await page.wait_for_timeout(300)

    # 5. Click the correct day button (full aria-label match)
    await page.get_by_role("button", name=day_aria_label(diary_date), exact=True).click()

    # 6. Click Continue
    await page.get_by_role("button", name="Continue").click()
    await page.wait_for_url("**/create-diary-entry**", timeout=10000)
    print(f"  📅 Opened form for {diary_date.strftime('%d %b %Y')}")


async def fill_and_submit_entry(page, entry: dict):
    """Fill all mandatory fields (+ optional if present) and save."""
    await page.wait_for_load_state("networkidle")

    # ── Mandatory ──────────────────────────────────────────────────────────────

    # Work Summary
    await page.locator("textarea[placeholder*='work you did']").fill(entry["work_summary"])

    # Hours Worked  — fill() clears existing value automatically
    await page.locator("input[type='number']").first.fill(str(entry["hours_worked"]))

    # Learnings / Outcomes
    await page.locator("textarea[placeholder*='learn or ship']").fill(entry["learnings"])

    # ── Optional ───────────────────────────────────────────────────────────────

    if entry.get("reference_links"):
        await page.locator("textarea[placeholder*='relevant links']").fill(entry["reference_links"])

    if entry.get("blockers"):
        await page.locator("textarea[placeholder*='slowed you down']").fill(entry["blockers"])

    # ── Skills Used (mandatory tag/combobox) ───────────────────────────────────
    skills_input = page.locator("input[role='combobox'][aria-autocomplete='list']").first
    for skill in entry["skills"]:
        await skills_input.fill(skill)
        await page.wait_for_timeout(600)
        option = page.get_by_role("option", name=skill, exact=False).first
        try:
            await option.click(timeout=2000)
        except PlaywrightTimeout:
            await skills_input.press("Enter")
        await page.wait_for_timeout(300)

    # ── Submit ─────────────────────────────────────────────────────────────────
    await page.get_by_role("button", name="Save").click()
    await page.wait_for_timeout(2000)
    print("  ✅ Entry saved")


async def run():
    with open(DATA_FILE, "r") as f:
        config = json.load(f)

    username        = config["credentials"]["username"]
    password        = config["credentials"]["password"]
    internship_name = config["internship_name"]
    start_date      = date(2026, 1, 1)
    end_date        = date.today()

    weekdays = get_weekdays(start_date, end_date)
    print(f"📆 Total weekdays to process: {len(weekdays)}")

    entries       = config.get("entries", {})   # per-date overrides
    default_entry = config["default_entry"]

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False, slow_mo=150)
        page    = await (await browser.new_context()).new_page()

        await login(page, username, password)

        for d in weekdays:
            date_key = d.strftime("%Y-%m-%d")
            entry    = entries.get(date_key, default_entry)
            print(f"\n📝 Processing {date_key}...")
            try:
                await select_internship_and_date(page, internship_name, d)

                # Skip if the site says entry already exists
                body = await page.inner_text("body")
                if "already submitted" in body.lower():
                    print("  ⚠️  Already exists, skipping")
                    continue

                await fill_and_submit_entry(page, entry)
                await page.wait_for_timeout(800)

            except Exception as e:
                print(f"  ❌ Error on {date_key}: {e}")
                await page.screenshot(path=f"error_{date_key}.png")
                continue   # move on to next date

        await browser.close()
        print("\n🎉 All done!")


if __name__ == "__main__":
    asyncio.run(run())