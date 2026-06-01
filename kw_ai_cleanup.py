#!/usr/bin/env python3
"""
AI-Powered Keyword Cleanup (Third Pass)
========================================
Takes the second-pass CSV and uses Claude to:
1. Fix garbled integration partner names
2. Generate proper suggested titles
3. Flag remaining duplicates for merging
4. Identify any entries that are actually irrelevant

Uses Claude Haiku for cost efficiency — processes in batches of 25.

Usage:
    python3 kw_ai_cleanup.py <final_csv> --tool "Jira" [-o output.csv]

Requires: ANTHROPIC_API_KEY environment variable
"""

import argparse
import csv
import json
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

try:
    import anthropic
except ImportError:
    print("ERROR: anthropic package not installed. Run: pip3 install anthropic")
    sys.exit(1)


BATCH_SIZE = 25  # Keywords per API call
MODEL = "claude-sonnet-4-20250514"  # Fast and cheap


def build_batch_prompt(entries, tool_name, batch_type="mixed"):
    """Build a prompt for a batch of keyword entries."""

    entries_text = ""
    for i, entry in enumerate(entries):
        entries_text += f"\n[{i}] canonical: {entry['canonical_keyword']}"
        entries_text += f"\n    category: {entry['category']}"
        entries_text += f"\n    suggested_title: {entry['suggested_title']}"
        entries_text += f"\n    variants ({entry['variant_count']}): {entry['all_variants'][:300]}"
        entries_text += "\n"

    prompt = f"""You are cleaning up a keyword research dataset for {tool_name} tutorials.

Each entry below represents a unique search intent with its variants. Your job:

1. **Fix the suggested_title**: Many integration titles have GARBLED partner names (e.g., "Ttermost" should be "Mattermost", "Thropic" should be "Anthropic", "Visu Studio" should be "Visual Studio"). Look at the VARIANTS to figure out the correct name — the variants contain the original unmangled keywords.

2. **For integrations**: Identify the EXACT correct integration partner name. Use proper casing (e.g., "ServiceNow" not "Servicenow", "GitHub" not "Github", "Power BI" not "Power Bi").

3. **For how-tos**: Generate a clean title in format "How to [action] in {tool_name}" that accurately describes the intent.

4. **Flag irrelevant entries**: If an entry is not something a tutorial article could be written about, mark it as irrelevant. Examples: branded error messages, product announcements, job postings.

5. **Flag duplicates**: If two entries in this batch clearly describe the same intent, note their indices.

Here are the entries:
{entries_text}

Respond with ONLY a JSON array. Each element must have:
- "index": the [i] number
- "fixed_title": the corrected suggested title
- "integration_partner": the correct partner name (or null if not an integration)
- "irrelevant": true/false
- "duplicate_of": index number if this is a duplicate of another entry in this batch (or null)

Example response:
[
  {{"index": 0, "fixed_title": "How to Integrate {tool_name} with Mattermost", "integration_partner": "Mattermost", "irrelevant": false, "duplicate_of": null}},
  {{"index": 1, "fixed_title": "How to Create a Dashboard in {tool_name}", "integration_partner": null, "irrelevant": false, "duplicate_of": null}}
]

IMPORTANT: Return ONLY the JSON array, no markdown code blocks, no explanation."""

    return prompt


def call_claude(client, prompt, retries=3):
    """Call Claude API with retry logic."""
    for attempt in range(retries):
        try:
            response = client.messages.create(
                model=MODEL,
                max_tokens=4096,
                messages=[{"role": "user", "content": prompt}],
            )
            text = response.content[0].text.strip()

            # Handle markdown code blocks
            if text.startswith("```"):
                text = text.split("\n", 1)[1]  # Remove first line
                if text.endswith("```"):
                    text = text[:-3]
                text = text.strip()

            return json.loads(text)
        except json.JSONDecodeError as e:
            print(f"    JSON parse error (attempt {attempt + 1}): {e}")
            if attempt < retries - 1:
                time.sleep(2)
            else:
                print(f"    Raw response: {text[:500]}")
                return None
        except anthropic.RateLimitError:
            wait = 10 * (attempt + 1)
            print(f"    Rate limited, waiting {wait}s...")
            time.sleep(wait)
        except Exception as e:
            print(f"    API error (attempt {attempt + 1}): {e}")
            if attempt < retries - 1:
                time.sleep(3)
            else:
                return None
    return None


def merge_duplicates(entries, duplicate_map):
    """Merge entries flagged as duplicates by AI."""
    # Build merge chains
    merged_into = {}  # index -> target index
    for dup_idx, target_idx in duplicate_map.items():
        if target_idx is not None and target_idx != dup_idx:
            # Follow chain
            while target_idx in merged_into:
                target_idx = merged_into[target_idx]
            merged_into[dup_idx] = target_idx

    # Group by merge target
    groups = defaultdict(list)
    for i in range(len(entries)):
        target = merged_into.get(i, i)
        groups[target].append(i)

    # Build merged entries
    result = []
    for target_idx, member_indices in groups.items():
        if len(member_indices) == 1:
            result.append(entries[target_idx])
        else:
            # Merge: keep the target's title, sum variants
            primary = entries[target_idx]
            total_variants = sum(int(entries[i]['variant_count']) for i in member_indices)
            all_vars = []
            for i in member_indices:
                all_vars.extend(entries[i]['all_variants'].split(' | '))

            merged = dict(primary)
            merged['variant_count'] = str(total_variants)
            merged['all_variants'] = ' | '.join(all_vars[:15])
            merged['cluster_size'] = str(int(primary.get('cluster_size', 1)) + len(member_indices) - 1)
            result.append(merged)

    return result


def main():
    parser = argparse.ArgumentParser(description='AI-powered keyword cleanup')
    parser.add_argument('input_file', help='Second-pass CSV file')
    parser.add_argument('--tool', '-t', required=True, help='Tool name (e.g., "Jira")')
    parser.add_argument('--output', '-o', help='Output CSV path')
    parser.add_argument('--dry-run', action='store_true', help='Process first batch only (for testing)')

    args = parser.parse_args()

    api_key = os.environ.get('ANTHROPIC_API_KEY')
    if not api_key:
        print("ERROR: Set ANTHROPIC_API_KEY environment variable")
        print("  export ANTHROPIC_API_KEY='sk-ant-...'")
        sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key)

    # Load entries
    with open(args.input_file, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        entries = list(reader)

    print(f"Loaded {len(entries)} entries from {args.input_file}")
    print(f"Using model: {MODEL}")

    # Process in batches
    total_batches = (len(entries) + BATCH_SIZE - 1) // BATCH_SIZE
    print(f"Processing in {total_batches} batches of {BATCH_SIZE}...")

    all_fixes = {}  # global index -> fix dict
    duplicate_map = {}  # global index -> global target index

    for batch_num in range(total_batches):
        start = batch_num * BATCH_SIZE
        end = min(start + BATCH_SIZE, len(entries))
        batch = entries[start:end]

        print(f"\n  Batch {batch_num + 1}/{total_batches} (entries {start}-{end - 1})...", end=" ", flush=True)

        prompt = build_batch_prompt(batch, args.tool)
        fixes = call_claude(client, prompt)

        if fixes is None:
            print("FAILED - keeping originals")
            continue

        applied = 0
        for fix in fixes:
            local_idx = fix['index']
            global_idx = start + local_idx

            if global_idx >= len(entries):
                continue

            all_fixes[global_idx] = fix

            if fix.get('duplicate_of') is not None:
                dup_target = start + fix['duplicate_of']
                duplicate_map[global_idx] = dup_target

            applied += 1

        print(f"OK ({applied} fixes)")

        if args.dry_run:
            print("\n  DRY RUN — showing first batch results:")
            for fix in fixes[:10]:
                idx = fix['index']
                orig = entries[start + idx]['suggested_title']
                new = fix['fixed_title']
                if orig != new:
                    print(f"    [{idx}] {orig}")
                    print(f"         → {new}")
                    if fix.get('integration_partner'):
                        print(f"         partner: {fix['integration_partner']}")
                if fix.get('irrelevant'):
                    print(f"    [{idx}] IRRELEVANT: {entries[start + idx]['canonical_keyword']}")
                if fix.get('duplicate_of') is not None:
                    print(f"    [{idx}] DUPLICATE OF [{fix['duplicate_of']}]")
            return

        # Small delay between batches to respect rate limits
        if batch_num < total_batches - 1:
            time.sleep(0.5)

    # Apply fixes
    print(f"\n{'='*60}")
    print(f"Applying fixes...")

    fixed_count = 0
    irrelevant_count = 0
    dup_count = len(duplicate_map)

    for global_idx, fix in all_fixes.items():
        entry = entries[global_idx]

        if fix.get('irrelevant'):
            entry['_irrelevant'] = True
            irrelevant_count += 1

        if fix.get('fixed_title') and fix['fixed_title'] != entry['suggested_title']:
            entry['suggested_title'] = fix['fixed_title']
            fixed_count += 1

        if fix.get('integration_partner'):
            entry['integration_partner'] = fix['integration_partner']

    print(f"  Titles fixed: {fixed_count}")
    print(f"  Marked irrelevant: {irrelevant_count}")
    print(f"  Duplicates flagged: {dup_count}")

    # Remove irrelevant
    cleaned = [e for e in entries if not e.get('_irrelevant')]
    print(f"  After removing irrelevant: {len(cleaned)}")

    # Merge duplicates
    if duplicate_map:
        # Remap indices after irrelevant removal
        # Actually, let's just merge from the original indices before filtering
        cleaned = merge_duplicates(cleaned, {})  # Skip for now, duplicates across batches are hard
        # For within-batch duplicates, they were already handled

    # Re-merge within-batch duplicates properly
    final = []
    skip = set()
    for i, entry in enumerate(entries):
        if entry.get('_irrelevant'):
            continue
        if i in duplicate_map and not entries[duplicate_map[i]].get('_irrelevant'):
            skip.add(i)
            # Add variants to target
            target = duplicate_map[i]
            target_entry = entries[target]
            combined_variants = int(target_entry['variant_count']) + int(entry['variant_count'])
            target_entry['variant_count'] = str(combined_variants)
            continue

    for i, entry in enumerate(entries):
        if entry.get('_irrelevant') or i in skip:
            continue
        final.append(entry)

    # Update signal strength based on new variant counts
    for entry in final:
        vc = int(entry['variant_count'])
        entry['signal_strength'] = 'high' if vc >= 5 else 'medium' if vc >= 3 else 'low'

    # Sort by variant count
    final.sort(key=lambda x: -int(x['variant_count']))

    print(f"  Final count: {len(final)}")

    # Stats
    cats = defaultdict(int)
    for e in final:
        cats[e['category']] += 1
    print(f"\n  By category:")
    for cat, count in sorted(cats.items(), key=lambda x: -x[1]):
        print(f"    {cat}: {count}")

    # Print top entries
    print(f"\n{'='*80}")
    print(f"  TOP 30 CLEANED OPPORTUNITIES")
    print(f"{'='*80}")
    for i, e in enumerate(final[:30], 1):
        marker = '★' if e['signal_strength'] == 'high' else '●'
        partner = f" [partner: {e.get('integration_partner', '')}]" if e.get('integration_partner') else ''
        print(f"  {i}. {marker} [{e['variant_count']} variants] {e['suggested_title']}{partner}")

    # Write output
    output_path = args.output
    if not output_path:
        stem = Path(args.input_file).stem
        output_path = str(Path(args.input_file).parent / f"{stem.replace('_final', '')}_cleaned.csv")

    fieldnames = [
        'canonical_keyword', 'suggested_title', 'category',
        'variant_count', 'signal_strength', 'integration_partner',
        'cluster_size', 'all_variants', 'merged_from'
    ]

    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction='ignore')
        writer.writeheader()
        for entry in final:
            writer.writerow(entry)

    print(f"\n✓ Cleaned results written to: {output_path}")
    print(f"✓ {len(final)} final opportunities\n")

    # Estimate cost
    total_input_tokens = total_batches * 1500  # rough estimate
    total_output_tokens = total_batches * 800
    cost = (total_input_tokens * 3 / 1_000_000) + (total_output_tokens * 15 / 1_000_000)
    print(f"  Estimated API cost: ~${cost:.3f}")


if __name__ == '__main__':
    main()
