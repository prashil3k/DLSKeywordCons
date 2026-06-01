#!/usr/bin/env python3
"""
Keyword Processor Launcher
==========================
Just run:  python3 run.py
It handles everything else.
"""

import os
import shutil
import sys
import subprocess
from pathlib import Path

# ── Config ──────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent.resolve()
NEW_KW_DIR = BASE_DIR / "new-keywords"
PROCESSING_DIR = NEW_KW_DIR / "_processing"
RAW_DIR = NEW_KW_DIR / "_raw"
PUBLISHED = BASE_DIR / "Published keywords.csv"
WRITERS = BASE_DIR / "writer-sheets"
API_KEY_FILE = BASE_DIR / ".api_key"

STEP1 = BASE_DIR / "kw_processor.py"
STEP2 = BASE_DIR / "kw_second_pass.py"
STEP3 = BASE_DIR / "kw_ai_cleanup.py"


def clear():
    os.system("cls" if os.name == "nt" else "clear")


def banner():
    print("=" * 60)
    print("  KEYWORD PROCESSOR")
    print("  Drop a CSV/TXT in new-keywords/ → get cleaned results")
    print("=" * 60)
    print()


def get_api_key():
    """Load or ask for API key once, then save it."""
    if API_KEY_FILE.exists():
        key = API_KEY_FILE.read_text().strip()
        if key:
            return key

    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if key:
        API_KEY_FILE.write_text(key)
        return key

    print("┌─────────────────────────────────────────────┐")
    print("│  First-time setup: need your Anthropic key   │")
    print("│  (only asked once — saved for future runs)   │")
    print("└─────────────────────────────────────────────┘")
    print()
    key = input("  Paste API key: ").strip()
    if not key:
        print("\n  ✗ No key provided. Exiting.")
        sys.exit(1)
    API_KEY_FILE.write_text(key)
    print("  ✓ Saved!\n")
    return key


def is_raw_input(f):
    """Check if a file is a raw input file (not intermediate output)."""
    skip_tags = {"_processed", "_final", "_cleaned", "_irrelevant"}
    return not any(s in f.stem.lower() for s in skip_tags)


def has_finished_output(filepath, tool_name):
    """Check if a tool-named final output already exists."""
    cleaned_by_tool = NEW_KW_DIR / f"{tool_name}_cleaned.csv"
    cleaned_by_stem = NEW_KW_DIR / f"{filepath.stem}_cleaned.csv"
    # Also check in processing dir for intermediate files
    return cleaned_by_tool.exists() or cleaned_by_stem.exists()


def pick_file():
    """Show numbered list of unprocessed files in new-keywords/."""
    if not NEW_KW_DIR.exists():
        NEW_KW_DIR.mkdir(parents=True)
        print(f"  Created folder: {NEW_KW_DIR}")
        print(f"  Drop your keyword CSV/TXT files there and re-run.\n")
        sys.exit(0)

    # Find raw files (top-level only, skip _processing and _raw subdirs)
    suffixes = {".csv", ".txt", ".xls", ".xlsx"}
    files = []
    for f in sorted(NEW_KW_DIR.iterdir()):
        if f.is_file() and f.suffix.lower() in suffixes and is_raw_input(f):
            files.append(f)

    if not files:
        print("  No keyword files found in new-keywords/")
        print(f"  Put your raw CSV or TXT exports in:\n    {NEW_KW_DIR}\n")
        sys.exit(0)

    # Check which already have cleaned output
    print("  FILES IN new-keywords/:\n")
    for i, f in enumerate(files, 1):
        # Check both tool-name and stem-based output
        stem = f.stem
        cleaned_stem = NEW_KW_DIR / f"{stem}_cleaned.csv"
        processed = PROCESSING_DIR / f"{stem}_processed.csv"
        final = PROCESSING_DIR / f"{stem}_final.csv"

        if cleaned_stem.exists():
            status = "  ✅ done"
        elif final.exists():
            status = "  🔶 needs AI cleanup (step 3)"
        elif processed.exists():
            status = "  🔶 needs second pass (step 2+3)"
        else:
            status = "  ⬚  not started"

        print(f"    {i}. {f.name}{status}")

    print()
    print(f"    0. Process ALL unfinished")
    print()

    choice = input("  Pick a number: ").strip()

    if choice == "0":
        # Return all unfinished
        unfinished = []
        for f in files:
            cleaned = NEW_KW_DIR / f"{f.stem}_cleaned.csv"
            if not cleaned.exists():
                unfinished.append(f)
        if not unfinished:
            print("\n  All files are already processed! ✅\n")
            sys.exit(0)
        return unfinished

    try:
        idx = int(choice) - 1
        if 0 <= idx < len(files):
            return [files[idx]]
    except ValueError:
        pass

    print("\n  ✗ Invalid choice. Try again.\n")
    sys.exit(1)


def guess_tool_name(filepath):
    """Guess tool name from filename."""
    name = filepath.stem

    # Strip common Ahrefs/Semrush export prefixes
    prefixes_to_strip = [
        "google_us_", "google_", "us_", "uk_",
        "DLS new kw WIP sheet - ", "DLS new kw WIP sheet -",
        "Demo Led SEO 2026 - Clean - ",
    ]
    for prefix in prefixes_to_strip:
        if name.lower().startswith(prefix.lower()):
            name = name[len(prefix):]

    # Strip common suffixes (dates, "matching-terms", etc.)
    import re
    name = re.sub(r'_?matching-terms.*$', '', name, flags=re.IGNORECASE)
    name = re.sub(r'_?\d{4}-\d{2}-\d{2}.*$', '', name)
    name = re.sub(r'_?\d{2}-\d{2}-\d{2}$', '', name)

    name = name.replace("-", " ").replace("_", " ").strip()
    return name


def confirm_tool_name(filepath):
    """Let user confirm or change the tool name."""
    guess = guess_tool_name(filepath)
    resp = input(f"  Tool name for {filepath.name}? [{guess}]: ").strip()
    return resp if resp else guess


def run_step(label, cmd, env=None):
    """Run a subprocess and show live output."""
    print(f"\n  {'─' * 50}")
    print(f"  {label}")
    print(f"  {'─' * 50}\n")

    merged_env = {**os.environ, **(env or {})}
    result = subprocess.run(
        cmd,
        cwd=str(BASE_DIR),
        env=merged_env,
        capture_output=False,
    )
    if result.returncode != 0:
        print(f"\n  ✗ {label} failed (exit code {result.returncode})")
        return False
    return True


def process_one(filepath, tool_name, api_key):
    """Run all 3 steps for one keyword file."""
    # Ensure directories exist
    PROCESSING_DIR.mkdir(exist_ok=True)
    RAW_DIR.mkdir(exist_ok=True)

    stem = filepath.stem
    processed = PROCESSING_DIR / f"{stem}_processed.csv"
    irrelevant = PROCESSING_DIR / f"{stem}_processed_irrelevant.csv"
    final = PROCESSING_DIR / f"{stem}_final.csv"
    cleaned_temp = PROCESSING_DIR / f"{stem}_cleaned.csv"
    cleaned_final = NEW_KW_DIR / f"{tool_name}_cleaned.csv"

    print(f"\n{'=' * 60}")
    print(f"  PROCESSING: {filepath.name}  →  tool: {tool_name}")
    print(f"{'=' * 60}")

    # Step 1
    if not processed.exists():
        ok = run_step(
            "STEP 1/3 — Categorize, dedupe, cross-reference (free)",
            [
                sys.executable, str(STEP1),
                str(filepath), tool_name,
                "--existing", str(PUBLISHED),
                "--writers", str(WRITERS),
                "-o", str(processed),
            ],
        )
        if not ok:
            return False
        # Step 1 creates irrelevant file next to processed — move it if it landed in new-keywords
        old_irrelevant = NEW_KW_DIR / f"{stem}_processed_irrelevant.csv"
        if old_irrelevant.exists():
            shutil.move(str(old_irrelevant), str(PROCESSING_DIR / old_irrelevant.name))
    else:
        print(f"\n  ⏭  Step 1 already done")

    # Step 2
    if not final.exists():
        ok = run_step(
            "STEP 2/3 — Aggressive synonym merge, drop singles (free)",
            [
                sys.executable, str(STEP2),
                str(processed),
                "--tool", tool_name,
                "--min-variants", "2",
                "-o", str(final),
            ],
        )
        if not ok:
            return False
    else:
        print(f"\n  ⏭  Step 2 already done")

    # Step 3
    if not cleaned_final.exists():
        ok = run_step(
            "STEP 3/3 — AI cleanup: fix names, titles (~$0.50-1.00)",
            [
                sys.executable, str(STEP3),
                str(final),
                "--tool", tool_name,
                "-o", str(cleaned_temp),
            ],
            env={"ANTHROPIC_API_KEY": api_key},
        )
        if not ok:
            return False

        # Rename cleaned output to tool name
        if cleaned_temp.exists():
            shutil.move(str(cleaned_temp), str(cleaned_final))
    else:
        print(f"\n  ⏭  Step 3 already done")

    # Move source file to _raw
    raw_dest = RAW_DIR / filepath.name
    if filepath.exists() and not raw_dest.exists():
        shutil.move(str(filepath), str(raw_dest))

    print(f"\n  ✅ DONE → {cleaned_final.name}")
    return True


def cleanup_old_files():
    """Move any old intermediate files from previous runs into _processing."""
    PROCESSING_DIR.mkdir(exist_ok=True)
    tags = ["_processed.csv", "_processed_irrelevant.csv", "_final.csv"]
    moved = 0
    for f in NEW_KW_DIR.iterdir():
        if f.is_file():
            for tag in tags:
                if f.name.endswith(tag):
                    dest = PROCESSING_DIR / f.name
                    if not dest.exists():
                        shutil.move(str(f), str(dest))
                        moved += 1
                    break
    if moved:
        print(f"  (moved {moved} old intermediate files to _processing/)\n")


def main():
    clear()
    banner()

    # Clean up any old intermediate files sitting in new-keywords/
    cleanup_old_files()

    # Pick files
    files = pick_file()

    # Get tool names
    tool_names = {}
    print()
    if len(files) == 1:
        tool_names[files[0]] = confirm_tool_name(files[0])
    else:
        print("  Confirm tool names (press Enter to accept suggestion):\n")
        for f in files:
            tool_names[f] = confirm_tool_name(f)

    # API key
    api_key = get_api_key()

    # Process
    results = {}
    for f in files:
        ok = process_one(f, tool_names[f], api_key)
        results[f.name] = (ok, tool_names[f])

    # Summary
    print(f"\n{'=' * 60}")
    print("  SUMMARY")
    print(f"{'=' * 60}\n")
    for name, (ok, tool) in results.items():
        if ok:
            print(f"  ✅ {name}  →  {tool}_cleaned.csv")
        else:
            print(f"  ✗  {name}  →  failed")
    print()
    print(f"  Final results:      {NEW_KW_DIR}/")
    print(f"  Intermediate files: {PROCESSING_DIR}/")
    print(f"  Raw source files:   {RAW_DIR}/")
    print()


if __name__ == "__main__":
    main()
