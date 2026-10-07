"""Populate completed_hours in a student CSV through the Alexandria portal.

Usage:
    python student_completed_hours_pipeline.py students.csv

Authentication:
    Set ALEXU_USERNAME and ALEXU_PASSWORD, or run interactively and enter them
    when prompted. Credentials are never written to the CSV or source code.

Program selection:
    Interactive runs show a numbered menu. Automated runs can use --program
    or ALEXU_PROGRAM.

Safety:
    Chrome is deliberately detached. This script never calls close() or quit().
    It attempts logout after success and after every failure. If logout cannot
    be completed, the Chrome tab remains open for manual intervention.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
import unicodedata
from io import BytesIO
from pathlib import Path

import pandas as pd
from selenium import webdriver
from selenium.common.exceptions import (
    StaleElementReferenceException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver import ChromeOptions
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from pypdf import PdfReader


PORTAL_URL = "https://gs.alexu.edu.eg/FCDS/index.php"
PROGRAMS = (
    "برنامج الحوسبة وعلوم البيانات",
    "برنامج تحليلات الأعمال",
    "برنامج النظم الذكية",
    "برنامج تحليلات الوسائط الإعلامية",
    "برنامج تحليلات ومعلوماتية الرعاية الصحية",
    "برنامج الأمن السيبراني",
)
DEFAULT_PROGRAM = PROGRAMS[0]
ACADEMIC_YEAR = "2026/2027"
SEMESTER = "Fall"
WAIT_SECONDS = 60
DEFAULT_PDF_OUTPUT_DIR = Path(__file__).resolve().parent / "output" / "pdf"


def log(message: str) -> None:
    print(f"[alexu] {message}", flush=True)


class DomWait(WebDriverWait):
    """Re-evaluate conditions when navigation invalidates a DOM node."""
    def until(self, method, message=''):
        def refreshed(driver):
            try:
                return method(driver)
            except StaleElementReferenceException:
                return False
            except WebDriverException as exc:
                if 'Node with given id does not belong to the document' in str(exc):
                    return False
                raise
        return super().until(refreshed, message)


def normalize_label(value: str) -> str:
    """Ignore whitespace, Unicode presentation and punctuation differences."""
    return re.sub(r"[\W_]+", "", unicodedata.normalize("NFKC", value).casefold())


def click_select2_option(
    wait: WebDriverWait,
    rendered_container_id: str,
    results_id: str,
    option_text: str,
) -> None:
    """Select a unique label, tolerating Unicode/spacing differences."""
    def selected_label(driver):
        rendered = driver.find_element(By.ID, rendered_container_id)
        return (rendered.get_attribute('title') or rendered.text or '').strip()
    current_label = wait.until(selected_label)
    if normalize_label(current_label) == normalize_label(option_text):
        log(f"Already selected {option_text!r}")
        return
    def open_options(driver):
        if any(item.is_displayed() for item in driver.find_elements(By.CSS_SELECTOR, f'#{results_id}')):
            return True
        arrow = EC.element_to_be_clickable((By.XPATH, f"//*[@id='{rendered_container_id}']/following-sibling::span[contains(@class,'select2-selection__arrow')]"))(driver)
        if not arrow:
            return False
        arrow.click()
        return True
    wait.until(open_options)
    available: list[str] = []
    def matching_option(driver):
        selected = driver.find_element(By.ID, rendered_container_id)
        if normalize_label(selected.get_attribute('title') or selected.text) == normalize_label(option_text):
            return True
        options = driver.find_elements(By.CSS_SELECTOR, f"#{results_id} li")
        available[:] = [item.text for item in options]
        matches = [item for item in options if normalize_label(item.text) == normalize_label(option_text)]
        if len(matches) == 1 and matches[0].is_displayed() and matches[0].is_enabled():
            matches[0].click()
            return True
        return False
    try:
        wait.until(matching_option)
    except TimeoutException as exc:
        raise ValueError(f"Requested selection {option_text!r} unavailable or ambiguous; portal options: {available}") from exc

    wait.until(
        lambda driver: driver.find_element(By.ID, rendered_container_id)
        and normalize_label(driver.find_element(By.ID, rendered_container_id).get_attribute("title") or driver.find_element(By.ID, rendered_container_id).text)
        == normalize_label(option_text)
    )
    log(f"Selected {option_text!r}")


def xpath_literal(value: str) -> str:
    """Return a safe XPath string literal."""
    if "'" not in value:
        return f"'{value}'"
    if '"' not in value:
        return f'"{value}"'
    parts = value.split("'")
    return "concat(" + ', "\'", '.join(f"'{part}'" for part in parts) + ")"


def logout_if_possible(driver: webdriver.Chrome, wait: WebDriverWait) -> bool:
    """Log out through the avatar dropdown, leaving the resulting tab open."""
    try:
        # Follow the page's actual logout link even when a modal covers it.
        logout_links = driver.find_elements(By.CSS_SELECTOR, "a[href*='logout.php']")
        if logout_links:
            driver.get(logout_links[0].get_attribute('href'))
            wait.until(EC.visibility_of_element_located((By.ID, 'username')))
            wait.until(EC.visibility_of_element_located((By.ID, 'password')))
            wait.until(lambda current: not current.find_elements(By.CSS_SELECTOR, 'a.pro-pic'))
            log('Logged out successfully; the Chrome tab remains open.')
            return True
        avatar = wait.until(
            EC.element_to_be_clickable(
                (
                    By.XPATH,
                    "//a[contains(@class,'pro-pic')]",
                )
            )
        )
        avatar.click()

        logout = wait.until(
            EC.element_to_be_clickable(
                (
                    By.XPATH,
                    "//a[contains(@href,'logout.php')]",
                )
            )
        )
        logout.click()
        wait.until(EC.visibility_of_element_located((By.ID, "username")))
        wait.until(EC.visibility_of_element_located((By.ID, "password")))
        wait.until(lambda current: not current.find_elements(By.CSS_SELECTOR, 'a.pro-pic'))
        log("Logged out successfully; the Chrome tab remains open.")
        return True
    except Exception:
        log("Logout controls were not available. The Chrome tab remains open for manual logout.")
        return False


def click_reports_link(wait: WebDriverWait) -> None:
    """Click the reports route while tolerating one dynamic DOM refresh."""
    locator = (
        By.XPATH,
        "//a[contains(@href,'registrar_report.php')]"
        "[.//*[contains(normalize-space(.),'Student') and "
        "contains(normalize-space(.),'Report')]]",
    )
    for attempt in range(2):
        try:
            wait.until(EC.element_to_be_clickable(locator)).click()
            return
        except StaleElementReferenceException:
            if attempt == 1:
                raise


def extract_completed_hours(pdf_path: Path, expected_student_code: str) -> str:
    """Validate the report identity and extract X from 'Completed: X Hrs'."""
    if expected_student_code not in pdf_path.stem:
        raise ValueError(
            f"Unexpected PDF output for {expected_student_code}: "
            "the student-specific filename does not match"
        )
    reader = PdfReader(str(pdf_path))
    if not reader.pages:
        raise ValueError(f"Unexpected empty PDF output for {expected_student_code}")
    report_text = "\n".join(page.extract_text() or "" for page in reader.pages)
    match = re.search(
        r"Completed\s*:\s*([0-9]+(?:\.[0-9]+)?)\s*Hrs",
        report_text,
        flags=re.IGNORECASE,
    )
    if not match:
        raise ValueError("Could not find 'Completed: X Hrs' in the downloaded PDF")
    return match.group(1)


def load_students(csv_path: Path) -> tuple[pd.DataFrame, list[str]]:
    """Read a CSV with Pandas and validate its student IDs."""
    dataframe = pd.read_csv(csv_path, dtype={"student_id": "string"})
    if "student_id" not in dataframe.columns:
        raise ValueError("Missing required CSV column: student_id")
    if "completed_hours" not in dataframe.columns:
        dataframe["completed_hours"] = pd.NA

    student_codes = dataframe["student_id"].fillna("").str.strip().tolist()
    if not student_codes or any(not code for code in student_codes):
        raise ValueError(f"Missing student_id value in {csv_path}")
    if len(student_codes) != len(set(student_codes)):
        raise ValueError(f"Duplicate student_id value in {csv_path}")
    return dataframe, student_codes


def save_completed_hours(
    dataframe: pd.DataFrame,
    results: dict[str, str],
    csv_path: Path,
) -> None:
    """Atomically update only completed_hours in the original CSV."""
    updated = dataframe.copy()
    updated["completed_hours"] = updated["student_id"].map(results)
    if updated["completed_hours"].isna().any():
        missing_ids = updated.loc[
            updated["completed_hours"].isna(), "student_id"
        ].tolist()
        raise ValueError(f"Missing completed hours for IDs: {missing_ids}")

    temporary_path = csv_path.with_suffix(".csv.tmp")
    updated.to_csv(temporary_path, index=False, encoding="utf-8")
    deadline = time.monotonic() + 30
    while True:
        try:
            os.replace(temporary_path, csv_path)
            break
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.5)


def student_pdf_path(student_code: str, pdf_output_dir: Path) -> Path:
    """Return the exact required student-specific PDF filename."""
    return pdf_output_dir / f"{student_code}.pdf"


def cache_snapshot(cache_directory: Path) -> dict[Path, tuple[int, int]]:
    """Record modification time and size for the current Chrome cache files."""
    snapshot: dict[Path, tuple[int, int]] = {}
    if not cache_directory.exists():
        return snapshot
    for path in cache_directory.iterdir():
        if not path.is_file():
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        snapshot[path] = (stat.st_mtime_ns, stat.st_size)
    return snapshot


def extract_pdf_from_cache_file(cache_file: Path) -> bytes | None:
    """Extract a complete raw PDF embedded in a Chrome cache entry."""
    try:
        cache_bytes = cache_file.read_bytes()
    except OSError:
        return None
    start = cache_bytes.find(b"%PDF-")
    end = cache_bytes.rfind(b"%%EOF")
    if start < 0 or end < start:
        return None
    pdf_bytes = cache_bytes[start : end + len(b"%%EOF")]
    try:
        if not PdfReader(BytesIO(pdf_bytes)).pages:
            return None
    except Exception:
        return None
    return pdf_bytes


def capture_cached_student_report(
    driver: webdriver.Chrome,
    cache_directory: Path,
    student_code: str,
    pdf_output_dir: Path,
) -> Path | None:
    """Click the real button and read its PDF from Chrome's disk cache."""
    not_found_message = (
        "Error: No Matching Student Record in the Selected Program Was Found for "
        f"{student_code}"
    )
    # Fall may first require opening a student-specific report-choice page.
    report_button = (By.CSS_SELECTOR, "button[type='submit'][name='lectures'], input[type='submit'][name='lectures']")
    if not driver.find_elements(*report_button):
        driver.find_element(By.CSS_SELECTOR, "form[name='toplistform'] button[type='submit'], form[name='toplistform'] input[type='submit']").click()
        WebDriverWait(driver, WAIT_SECONDS).until(
            lambda current: not_found_message in current.page_source or current.find_elements(*report_button)
        )
        if not_found_message in driver.page_source:
            return None
    before = cache_snapshot(cache_directory)
    driver.find_element(*report_button).click()

    deadline = time.monotonic() + WAIT_SECONDS
    examined_states: set[tuple[Path, int, int]] = set()
    while time.monotonic() < deadline:
        if not_found_message in driver.page_source:
            return None

        after = cache_snapshot(cache_directory)
        changed_files = [
            path for path, state in after.items() if before.get(path) != state
        ]
        for cache_file in sorted(
            changed_files,
            key=lambda path: after[path][0],
            reverse=True,
        ):
            state = (cache_file, *after[cache_file])
            if state in examined_states:
                continue
            examined_states.add(state)
            content = extract_pdf_from_cache_file(cache_file)
            if content is None:
                continue

            pdf_path = student_pdf_path(student_code, pdf_output_dir)
            temporary_path = pdf_path.with_suffix(".pdf.part")
            try:
                temporary_path.write_bytes(content)
                os.replace(temporary_path, pdf_path)
            except OSError:
                continue
            return pdf_path

        time.sleep(0.2)

    raise TimeoutException(
        f"No cached PDF or matching not-found message appeared for {student_code}"
    )


def return_to_report_form(driver: webdriver.Chrome) -> None:
    """Support both direct reports and the extra student report-choice page."""
    for _ in range(3):
        driver.back()
        try:
            WebDriverWait(driver, 5).until(EC.visibility_of_element_located((By.ID, 'scodeBox')))
            return
        except TimeoutException:
            continue
    raise TimeoutException('Could not return to student report form; website remains open')


def parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Fill completed_hours in a CSV by retrieving each student's "
            "lecture report from the Alexandria University portal."
        )
    )
    parser.add_argument(
        "csv_path",
        nargs="?",
        default="data.csv",
        type=Path,
        help="Input CSV containing student_id; updated in place (default: data.csv).",
    )
    parser.add_argument(
        "--pdf-dir",
        type=Path,
        default=DEFAULT_PDF_OUTPUT_DIR,
        help="Directory for exact <student_id>.pdf files.",
    )
    parser.add_argument(
        "--username",
        help="Portal username; defaults to ALEXU_USERNAME or an interactive prompt.",
    )
    parser.add_argument(
        "--program",
        choices=PROGRAMS,
        help=(
            "Student program; defaults to ALEXU_PROGRAM, an interactive menu, "
            "or the computing/data-science program in non-interactive runs."
        ),
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=WAIT_SECONDS,
        help=f"Portal/cache timeout in seconds (default: {WAIT_SECONDS}).",
    )
    parser.add_argument(
        "--failure-screenshot",
        type=Path,
        default=Path("alexu_failure.png"),
        help="Screenshot path used when an unexpected failure occurs.",
    )
    return parser.parse_args(argv)


def resolve_program(
    args: argparse.Namespace,
    interactive: bool | None = None,
) -> str:
    """Resolve the program from CLI, environment, or a numbered terminal menu."""
    configured_program = args.program or os.environ.get("ALEXU_PROGRAM")
    if configured_program:
        if configured_program not in PROGRAMS:
            available = "\n".join(f"  {index}. {name}" for index, name in enumerate(PROGRAMS, 1))
            raise ValueError(
                f"Unknown program: {configured_program}\nAvailable programs:\n{available}"
            )
        return configured_program

    if interactive is None:
        interactive = sys.stdin.isatty()
    if not interactive:
        return DEFAULT_PROGRAM

    print("\nSelect the student program:")
    for index, program_name in enumerate(PROGRAMS, start=1):
        default_label = " (default)" if program_name == DEFAULT_PROGRAM else ""
        print(f"  {index}. {program_name}{default_label}")

    while True:
        selection = input(f"Program [1-{len(PROGRAMS)}] (default 1): ").strip()
        if not selection:
            return DEFAULT_PROGRAM
        if selection.isdigit() and 1 <= int(selection) <= len(PROGRAMS):
            return PROGRAMS[int(selection) - 1]
        if selection in PROGRAMS:
            return selection
        print(f"Please enter a number from 1 to {len(PROGRAMS)}.")


def resolve_credentials(args: argparse.Namespace) -> tuple[str, str]:
    username = args.username or os.environ.get("ALEXU_USERNAME")
    password = os.environ.get("ALEXU_PASSWORD")
    if not username:
        if not sys.stdin.isatty():
            raise ValueError(
                "Set ALEXU_USERNAME or pass --username when running non-interactively"
            )
        username = input("Alexandria portal username: ").strip()
    if not password:
        if not sys.stdin.isatty():
            raise ValueError(
                "Set ALEXU_PASSWORD when running non-interactively"
            )
        password = input("Alexandria portal password: ")
    if not username or not password:
        raise ValueError("Username and password must not be empty")
    return username, password


def main(argv: list[str] | None = None) -> int:
    global WAIT_SECONDS

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")

    args = parse_arguments(argv)
    WAIT_SECONDS = args.timeout
    student_csv = args.csv_path.resolve()
    pdf_output_dir = args.pdf_dir.resolve()
    failure_screenshot = args.failure_screenshot.resolve()
    if not student_csv.is_file():
        print(f"CSV file not found: {student_csv}", file=sys.stderr)
        return 2
    try:
        program = resolve_program(args)
        username, password = resolve_credentials(args)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    log(f"Selected student program: {program}")

    students, student_codes = load_students(student_csv)
    log(f"Loaded {len(student_codes)} students from {student_csv}.")
    pdf_output_dir.mkdir(parents=True, exist_ok=True)
    options = ChromeOptions()
    options.add_experimental_option("detach", True)
    options.add_argument("--start-maximized")

    log("Opening visible Chrome (detached mode: the tab will not be closed).")
    driver = webdriver.Chrome(options=options)
    driver.set_script_timeout(WAIT_SECONDS)
    cache_directory = (
        Path(driver.capabilities["chrome"]["userDataDir"])
        / "Default"
        / "Cache"
        / "Cache_Data"
    )
    log(f"Using Chrome PDF cache at {cache_directory}")
    wait = WebDriverWait(driver, WAIT_SECONDS)
    logged_in = False

    try:
        driver.get(PORTAL_URL)
        wait.until(EC.visibility_of_element_located((By.ID, "username"))).send_keys(
            username
        )
        driver.find_element(By.ID, "password").send_keys(password)
        driver.find_element(By.CSS_SELECTOR, "input[type='submit']").click()

        try:
            wait.until(
                EC.element_to_be_clickable(
                    (
                        By.XPATH,
                        "//span[contains(@class,'hide-menu') and "
                        "contains(normalize-space(.),'Student') and contains(normalize-space(.),'Report')]",
                    )
                )
            )
        except TimeoutException:
            log(f"Login transition stalled at URL: {driver.current_url}")
            log(f"Current page title: {driver.title!r}")
            driver.save_screenshot(str(failure_screenshot))
            log(f"Saved diagnostic screenshot to {failure_screenshot}")
            raise
        logged_in = True
        log("Login succeeded.")

        reports_label = driver.find_element(
            By.XPATH,
            "//span[contains(@class,'hide-menu') and "
            "contains(normalize-space(.),'Student') and contains(normalize-space(.),'Report')]",
        )
        reports_link = reports_label.find_element(By.XPATH, "./ancestor::a[1]")
        reports_link.click()
        wait.until(EC.presence_of_element_located((By.ID, "programBox")))
        log("Opened Student's Reports.")

        click_select2_option(
            wait,
            "select2-programBox-container",
            "select2-programBox-results",
            program,
        )
        click_select2_option(
            wait,
            "select2-yearBox-container",
            "select2-yearBox-results",
            ACADEMIC_YEAR,
        )
        click_select2_option(
            wait,
            "select2-semesterBox-container",
            "select2-semesterBox-results",
            SEMESTER,
        )

        wait.until(
            EC.element_to_be_clickable(
                (By.CSS_SELECTOR, "button[type='submit'][name='Accept']")
            )
        ).click()
        wait.until(
            EC.presence_of_element_located(
                (By.CSS_SELECTOR, "a[href*='registrar_report.php']")
            )
        )
        log("Continued to the action-choice page.")

        click_reports_link(wait)

        wait.until(
            EC.presence_of_element_located((By.CSS_SELECTOR, "form[name='toplistform']"))
        )
        results: dict[str, str] = {}
        for index, student_code in enumerate(student_codes, start=1):
            student_code_box = wait.until(
                EC.visibility_of_element_located((By.ID, "scodeBox"))
            )
            student_code_box.clear()
            student_code_box.send_keys(student_code)
            log(
                f"[{index}/{len(student_codes)}] Entered student code "
                f"{student_code}."
            )

            pdf_path = capture_cached_student_report(
                driver,
                cache_directory,
                student_code,
                pdf_output_dir,
            )
            if pdf_path is None:
                results[student_code] = "unknown"
                log(
                    f"[{index}/{len(student_codes)}] No matching student "
                    "record; completed_hours = unknown."
                )
                return_to_report_form(driver)
                log(
                    f"[{index}/{len(student_codes)}] Browser Back returned to "
                    "the clean student report form."
                )
                continue

            return_to_report_form(driver)
            completed_hours = extract_completed_hours(pdf_path, student_code)
            results[student_code] = completed_hours
            log(
                f"[{index}/{len(student_codes)}] Saved {pdf_path.name}; "
                f"X = {completed_hours}."
            )

        save_completed_hours(students, results, student_csv)
        log(f"Updated all {len(results)} completed_hours values in {student_csv}.")

        log("Batch report workflow completed. Logging out via the avatar menu.")
        return 0
    except Exception as exc:
        log(f"Smoke test failed: {type(exc).__name__}: {exc}")
        try:
            driver.save_screenshot(str(failure_screenshot))
            log(f"Saved diagnostic screenshot to {failure_screenshot}")
        except Exception:
            pass
        return 1
    finally:
        avatar_xpath = "//a[contains(@class,'pro-pic')][.//img[@alt='user']]"
        authenticated_avatar_present = bool(
            driver.find_elements(By.XPATH, avatar_xpath)
        )
        if logged_in and not authenticated_avatar_present:
            # A failed PDF response may leave Chrome on a viewer/error page.
            # Try returning to the authenticated application before logout.
            for _ in range(2):
                try:
                    driver.back()
                    WebDriverWait(driver, 5).until(
                        EC.presence_of_element_located((By.XPATH, avatar_xpath))
                    )
                    authenticated_avatar_present = True
                    break
                except TimeoutException:
                    continue

        if logged_in or authenticated_avatar_present:
            logout_if_possible(driver, WebDriverWait(driver, 10))
        else:
            log("No authenticated session was detected; the Chrome tab remains open.")

        # Give the final page a moment to settle. Never call close() or quit().
        time.sleep(2)


if __name__ == "__main__":
    raise SystemExit(main())
