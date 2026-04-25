# VTU Diary Auto-Fill

This project automates submitting VTU diary entries using Playwright.

## What this script does

- Logs into https://vtu.internyet.in
- Opens the student diary page
- Iterates through all weekdays from **2026-01-01** to today
- Fills and submits entries using:
  - `default_entry` for most dates
  - date-wise overrides from `entries` when provided
- Skips days where an entry is already submitted
- Captures screenshots like `error_YYYY-MM-DD.png` if any day fails

## 1) Create `diary_data.json`

Create a file named `diary_data.json` in the project root with this structure:

```json
{
  "credentials": {
    "username": "your_email@example.com",
    "password": "your_password"
  },
  "internship_name": "Your Internship Name",
  "default_entry": {
    "work_summary": "Worked on ...",
    "hours_worked": 6,
    "learnings": "Learned ...",
    "reference_links": "",
    "blockers": "",
    "skills": ["Python", "Machine Learning", "NLP"]
  },
  "entries": {
    "2026-01-02": {
      "work_summary": "Specific work summary for this date",
      "hours_worked": 5,
      "learnings": "Specific learnings for this date",
      "reference_links": "https://example.com",
      "blockers": "Optional blocker details",
      "skills": ["Python", "Git", "Data Analysis"]
    }
  }
}
```

### Notes for filling details

- `internship_name`: should match internship option text shown in the website dropdown (partial match is okay).
- `hours_worked`: must be a number.
- `skills`: should be an array of skill names.
- `entries`: optional date-specific overrides in `YYYY-MM-DD` format.
- Keep `reference_links` and `blockers` as empty strings if not needed.

## 2) Set up the project

Open terminal in this folder and run:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install playwright
playwright install chromium
```

If activation is blocked in PowerShell, run once:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

Then activate again.

## 3) Run the script

```powershell
python .\diary.py
```

The browser opens in non-headless mode so you can watch progress.

## Quick workflow

1. Fill `diary_data.json`.
2. Run `python .\diary.py`.
3. Check terminal logs for each date.
4. If any date fails, inspect the generated `error_YYYY-MM-DD.png` screenshot.

## Important behavior

- Date range is currently hardcoded in `diary.py`:
  - Start: `2026-01-01`
  - End: today
- Weekends are skipped automatically.
- Existing submitted entries are skipped.

If you want a custom date range, update `start_date` and `end_date` inside `diary.py`.
