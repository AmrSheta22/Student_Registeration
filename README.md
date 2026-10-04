# Alexandria University Student Completed-Hours Pipeline

The registration script also accepts `registeration.xlsx`: first column `ID`,
course names as the remaining headers, and `1` in each requested student/course
cell. It creates a `Registration log` worksheet in the same workbook and
checkpoints every subject. See `HOW_TO_USE.txt` for the two-student example.
No browser tab is closed automatically; logout must be confirmed before anyone
closes the website.

This project automates collecting each student's completed credit hours from
the Alexandria University FCDS graduate-studies portal:

`https://gs.alexu.edu.eg/FCDS/index.php`

The pipeline reads student IDs from a CSV file, opens the portal in visible
Chrome with Selenium, selects the requested academic program, retrieves each
student's lecture-table PDF, extracts the value from `Completed: X Hrs` with
PyPDF, and writes the result into `completed_hours` in the same CSV.

## Project files

- `student_completed_hours_pipeline.py` - the complete Selenium, PDF, and CSV
  pipeline.
- `data.csv` - the current student input and completed-hours output.
- `output/pdf/` - valid reports saved as exactly `<student_id>.pdf`.
- `student_program_page.html`, `id_for_report.html`, and the other saved HTML
  files - local page references used while developing the selectors.

## Requirements

- Python 3.10 or later
- Google Chrome
- A Chrome version compatible with Selenium/ChromeDriver
- Network access to the university portal
- Python packages:
  - `selenium`
  - `pandas`
  - `pypdf`

Install the Python packages with:

```powershell
python -m pip install selenium pandas pypdf
```

## Input CSV

The CSV must contain a `student_id` column:

```csv
student_id,name,completed_hours
2401241123,Example Student,
2401241158,Another Student,
```

Rules:

- `student_id` is required.
- IDs must be present and unique.
- Other columns, such as `name`, are preserved.
- If `completed_hours` is absent, the script creates it.
- The completed CSV replaces the input CSV only after every ID has been
  handled successfully.
- Keep the CSV closed in Excel while the pipeline is running. Excel can lock
  the file and prevent the final update.

## Authentication

Credentials are not stored in the source code.

For an interactive run, the script prompts for any missing credentials. For an
automated run, set:

```powershell
$env:ALEXU_USERNAME="your_username"
$env:ALEXU_PASSWORD="your_password"
```

The username can also be supplied with `--username`. The password should use
the environment variable or the secure interactive prompt so it is not exposed
in command history.

## Running the pipeline

Run against the default `data.csv`:

```powershell
python student_completed_hours_pipeline.py
```

Run against another CSV:

```powershell
python student_completed_hours_pipeline.py students.csv
```

### Program selection

An interactive terminal run displays these choices:

1. برنامج الحوسبة وعلوم البيانات
2. برنامج تحليلات الأعمال
3. برنامج النظم الذكية
4. برنامج تحليلات الوسائط الإعلامية
5. برنامج تحليلات ومعلوماتية الرعاية الصحية
6. برنامج الأمن السيبراني

Pressing Enter selects the first program.

The program can also be supplied directly:

```powershell
python student_completed_hours_pipeline.py data.csv `
  --program "برنامج الأمن السيبراني"
```

Or through an environment variable:

```powershell
$env:ALEXU_PROGRAM="برنامج تحليلات الأعمال"
python student_completed_hours_pipeline.py data.csv
```

Additional command-line options are available through:

```powershell
python student_completed_hours_pipeline.py --help
```

## Portal workflow

The script performs the following actions in one authenticated session:

1. Logs in.
2. Opens Student's Reports.
3. Selects the chosen academic program.
4. Selects academic year `2026/2027`.
5. Selects semester `Fall`.
6. Continues to the action-choice page.
7. Opens Student's Reports again.
8. Enters each `student_id`.
9. Requests the student's lecture-table report.
10. Extracts `Completed: X Hrs`.
11. Repeats without logging out between students.
12. Updates the input CSV after all students are handled.
13. Logs out after the final student.

## Missing or incorrect student IDs

The portal reports a missing student with:

`Error: No Matching Student Record in the Selected Program Was Found for ...`

When this exact response appears, the pipeline:

1. Writes `unknown` to that student's `completed_hours`.
2. Uses the browser Back action to leave the POST result page.
3. Waits for the clean student-code form.
4. Continues with the next ID.

Returning to the clean form prevents Chrome's form-resubmission error on the
next student.

## PDF handling

Chrome's normal PDF download path was unreliable because downloads could be
blocked during virus scanning. The project does **not** disable antivirus or
Chrome security.

Instead, after the genuine report-button click, the script reads the valid PDF
bytes from the current Selenium Chrome profile's disk cache. It then:

- validates that a readable PDF is present;
- saves it as exactly `output/pdf/<student_id>.pdf`;
- extracts `Completed: X Hrs` with PyPDF.

Only valid students receive a PDF. A student marked `unknown` does not receive
one.

## Browser and session safety constraints

These safeguards are intentional requirements:

- Chrome runs visibly.
- Selenium uses detached mode.
- The script never calls `driver.close()` or `driver.quit()`.
- The page tab must never be closed automatically.
- The script attempts to log out after a successful batch.
- The script also attempts to log out after every unexpected exception.
- If it lands on a PDF viewer or error page, it tries browser Back before
  looking for the logout controls.
- If automation cannot complete logout, Chrome remains open so the user can log
  out and close it manually.

## Failure behavior

- Results are held in memory until all IDs are handled.
- The CSV is updated atomically only after the complete batch succeeds.
- A temporary CSV is used before replacing the original.
- Transient Windows file-lock errors are retried for 30 seconds.
- On an unexpected portal response, malformed PDF, timeout, or selector error,
  the script stops, saves `alexu_failure.png`, attempts logout, and leaves
  Chrome open.
- PDFs saved before a failure remain available, but a rerun currently starts
  from the beginning of the CSV.

## Current limitations

- The completed-hours academic year defaults to `2026/2027` in source.
- The completed-hours semester defaults to `Fall` in source.
- Registration accepts `--year` and `--semester` overrides.
- The report type is fixed to the student's lecture table.
- The extraction expects the exact English pattern `Completed: X Hrs`.
- The automation depends on the portal's current HTML IDs, link routes, Select2
  markup, and error message.
- The PDF recovery depends on Chrome's current disk-cache format and the
  Selenium-created Chrome profile layout. A major Chrome update may require
  changes.
- The pipeline is designed around visible Google Chrome and is not intended for
  headless mode or other browsers.
- It assumes the selected program, year, and semester are available in the
  portal.
- It does not bypass authentication, antivirus, access controls, or university
  portal restrictions.
- Large batches are processed sequentially to keep one controlled portal
  session and avoid parallel submissions.
- The university system may lock or expire a session. If that happens, manual
  intervention may be required in the Chrome tab that remains open.

## Security notes

- Do not commit usernames, passwords, session tokens, or generated private
  student reports to a public repository.
- Treat the CSV and PDFs as private student data.
- Prefer the secure password prompt or an environment variable over a
  command-line password.
- Clear credential environment variables after use if the machine is shared.
