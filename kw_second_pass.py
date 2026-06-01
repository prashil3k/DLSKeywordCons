#!/usr/bin/env python3
"""
Second-Pass Keyword Consolidator
=================================
Takes the first-pass processed CSV and:
1. Aggressively merges semantically similar intents using word-overlap
2. Drops any intent with total variant_count of 1 (no demand signal)
3. Filters out already-covered keywords
4. Outputs only serviceable new opportunities

Usage:
    python3 kw_second_pass.py <processed_csv> [--output <output_file>] [--min-variants 2]
"""

import argparse
import csv
import re
from collections import defaultdict


# Stop words to ignore during semantic comparison
STOP_WORDS = {
    'a', 'an', 'the', 'in', 'on', 'to', 'for', 'from', 'with', 'of', 'at',
    'by', 'and', 'or', 'is', 'it', 'my', 'your', 'our', 'how', 'do', 'i',
    'you', 'can', 'does', 'using', 'via', 'through', 'into', 'be', 'been',
    'being', 'was', 'were', 'are', 'am', 'has', 'have', 'had', 'will',
    'would', 'could', 'should', 'shall', 'may', 'might', 'must',
    'that', 'this', 'these', 'those', 'which', 'what', 'where', 'when',
    'who', 'whom', 'whose', 'why', 'so', 'if', 'then', 'than',
    'but', 'not', 'no', 'nor', 'just', 'also', 'too', 'very',
    'about', 'up', 'out', 'off', 'over', 'under', 'again', 'further',
    'each', 'every', 'all', 'any', 'both', 'few', 'more', 'most',
    'other', 'some', 'such', 'only', 'own', 'same', 'between',
    'after', 'before', 'during', 'while', 'still', 'already',
    'use', 'used', 'uses', 'way', 'ways', 'step', 'steps',
    'guide', 'tutorial', 'example', 'examples', 'best', 'practice',
    'practices', 'tip', 'tips', 'trick', 'tricks', 'method', 'methods',
    'new', 'old', 'one', 'two', 'multiple', 'several', 'different',
    'within', 'without', 'like', 'need', 'want', 'try', 'able',
    'easily', 'quickly', 'simple', 'simply', 'basic', 'advanced',
    'properly', 'correctly', 'effectively', 'efficiently',
}

# Action synonyms — words that mean roughly the same thing in tutorial context
ACTION_SYNONYMS = {
    # Create family
    'create': 'create', 'make': 'create', 'build': 'create',
    'set up': 'create', 'setup': 'create', 'generate': 'create',
    'compose': 'create', 'draft': 'create', 'design': 'create',
    'establish': 'create', 'initialize': 'create', 'init': 'create',
    'provision': 'create', 'spin up': 'create', 'instantiate': 'create',
    'define': 'create', 'write': 'create',
    # Add family
    'add': 'add', 'insert': 'add', 'include': 'add', 'put': 'add',
    'append': 'add', 'inject': 'add', 'embed': 'add', 'place': 'add',
    # Delete family
    'delete': 'delete', 'remove': 'delete', 'clear': 'delete',
    'erase': 'delete', 'destroy': 'delete', 'purge': 'delete',
    'discard': 'delete', 'drop': 'delete', 'wipe': 'delete',
    'uninstall': 'delete', 'eliminate': 'delete',
    # Edit family
    'edit': 'edit', 'modify': 'edit', 'change': 'edit',
    'update': 'edit', 'alter': 'edit', 'revise': 'edit',
    'amend': 'edit', 'tweak': 'edit', 'adjust': 'edit',
    'rename': 'edit', 'rewrite': 'edit', 'override': 'edit',
    # View family
    'view': 'view', 'see': 'view', 'check': 'view', 'find': 'view',
    'look': 'view', 'show': 'view', 'display': 'view', 'get': 'view',
    'access': 'view', 'browse': 'view', 'inspect': 'view',
    'retrieve': 'view', 'fetch': 'view', 'pull': 'view',
    'read': 'view', 'list': 'view', 'locate': 'view',
    'preview': 'view', 'review': 'view', 'lookup': 'view',
    'look up': 'view', 'navigate': 'view', 'go to': 'view',
    # Export family
    'export': 'export', 'download': 'export', 'extract': 'export',
    'save as': 'export', 'output': 'export', 'print': 'export',
    'dump': 'export', 'backup': 'export', 'back up': 'export',
    # Import family
    'import': 'import', 'upload': 'import', 'load': 'import',
    'ingest': 'import', 'bring in': 'import', 'pull in': 'import',
    'bulk upload': 'import', 'bulk import': 'import', 'restore': 'import',
    # Move family
    'move': 'move', 'transfer': 'move', 'migrate': 'move',
    'shift': 'move', 'relocate': 'move', 'drag': 'move',
    'reorder': 'move', 'rearrange': 'move', 'reorganize': 'move',
    # Copy family
    'copy': 'copy', 'clone': 'copy', 'duplicate': 'copy',
    'replicate': 'copy', 'mirror': 'copy',
    # Assign family
    'assign': 'assign', 'allocate': 'assign', 'delegate': 'assign',
    'reassign': 'assign', 'distribute': 'assign',
    # Link family
    'link': 'link', 'connect': 'link', 'attach': 'link',
    'associate': 'link', 'relate': 'link', 'reference': 'link',
    'map': 'link', 'bind': 'link', 'couple': 'link',
    # Configure family
    'configure': 'configure', 'customize': 'configure',
    'personalize': 'configure', 'tailor': 'configure',
    'set': 'configure', 'specify': 'configure', 'tune': 'configure',
    # Manage family
    'manage': 'manage', 'organize': 'manage', 'handle': 'manage',
    'administer': 'manage', 'maintain': 'manage', 'govern': 'manage',
    'oversee': 'manage', 'control': 'manage',
    # Track family
    'track': 'track', 'monitor': 'track', 'log': 'track',
    'measure': 'track', 'audit': 'track', 'record': 'track',
    'observe': 'track', 'watch': 'track',
    # Enable family
    'enable': 'enable', 'activate': 'enable', 'turn on': 'enable',
    'switch on': 'enable', 'allow': 'enable', 'unlock': 'enable',
    # Disable family
    'disable': 'disable', 'deactivate': 'disable', 'turn off': 'disable',
    'switch off': 'disable', 'block': 'disable', 'restrict': 'disable',
    'hide': 'disable', 'suppress': 'disable', 'mute': 'disable',
    # Filter family
    'filter': 'filter', 'sort': 'filter', 'search': 'filter',
    'query': 'filter', 'narrow': 'filter', 'refine': 'filter',
    # Close family
    'close': 'close', 'complete': 'close', 'finish': 'close',
    'resolve': 'close', 'done': 'close', 'archive': 'close',
    'end': 'close', 'wrap up': 'close',
    # Start family
    'start': 'start', 'begin': 'start', 'open': 'start',
    'launch': 'start', 'initiate': 'start', 'kick off': 'start',
    'trigger': 'start', 'run': 'start', 'execute': 'start',
    # Share family
    'share': 'share', 'send': 'share', 'distribute': 'share',
    'publish': 'share', 'post': 'share', 'broadcast': 'share',
    'forward': 'share', 'submit': 'share',
    # Approve family
    'approve': 'approve', 'accept': 'approve', 'confirm': 'approve',
    'authorize': 'approve', 'sign off': 'approve', 'validate': 'approve',
    # Deny family
    'deny': 'deny', 'reject': 'deny', 'decline': 'deny', 'refuse': 'deny',
    # Merge family
    'merge': 'merge', 'combine': 'merge', 'consolidate': 'merge',
    'unify': 'merge', 'join': 'merge', 'aggregate': 'merge',
    # Split family
    'split': 'split', 'separate': 'split', 'divide': 'split',
    'break': 'split', 'decompose': 'split',
    # Schedule family
    'schedule': 'schedule', 'plan': 'schedule', 'calendar': 'schedule',
    'book': 'schedule', 'reserve': 'schedule',
    # Automate family
    'automate': 'automate', 'auto': 'automate', 'automatically': 'automate',
    'batch': 'automate', 'bulk': 'automate', 'mass': 'automate',
    # Sync family
    'sync': 'sync', 'synchronize': 'sync', 'synch': 'sync',
    'real-time': 'sync', 'realtime': 'sync', 'two-way': 'sync',
    # Integrate family
    'integrate': 'integrate', 'integration': 'integrate',
    'interop': 'integrate', 'interoperability': 'integrate',
    # Fix/Troubleshoot family
    'fix': 'fix', 'repair': 'fix', 'troubleshoot': 'fix',
    'debug': 'fix', 'diagnose': 'fix', 'solve': 'fix',
    'workaround': 'fix', 'patch': 'fix',
    # Restrict/Limit family
    'limit': 'limit', 'restrict': 'limit', 'cap': 'limit',
    'constrain': 'limit', 'throttle': 'limit',
    # Convert family
    'convert': 'convert', 'transform': 'convert', 'translate': 'convert',
    'format': 'convert', 'parse': 'convert',
}

# Object synonyms — nouns that mean the same thing across SaaS tools
# These are tool-agnostic so they work for any SaaS product
OBJECT_SYNONYMS = {
    # Work items
    'ticket': 'issue', 'issue': 'issue', 'task': 'issue',
    'item': 'issue', 'card': 'issue', 'work item': 'issue',
    'request': 'issue', 'incident': 'issue', 'case': 'issue',
    'bug': 'bug', 'defect': 'bug', 'error': 'bug', 'problem': 'bug',
    'story': 'story', 'user story': 'story', 'requirement': 'story',
    'feature request': 'story', 'feature': 'story',
    # Agile concepts
    'sprint': 'sprint', 'iteration': 'sprint', 'cycle': 'sprint',
    'board': 'board', 'kanban': 'board', 'scrum board': 'board',
    'kanban board': 'board', 'agile board': 'board',
    'backlog': 'backlog', 'product backlog': 'backlog',
    'sprint backlog': 'backlog',
    'epic': 'epic', 'initiative': 'epic', 'theme': 'epic',
    'velocity': 'velocity', 'throughput': 'velocity',
    'burndown': 'burndown', 'burn down': 'burndown',
    'burnup': 'burndown', 'burn up': 'burndown',
    # Structure
    'project': 'project', 'workspace': 'project', 'space': 'project',
    'repo': 'project', 'repository': 'project',
    'folder': 'folder', 'directory': 'folder', 'collection': 'folder',
    'group': 'folder', 'category': 'folder',
    # Workflow/Process
    'workflow': 'workflow', 'pipeline': 'workflow', 'process': 'workflow',
    'flow': 'workflow', 'stage': 'workflow',
    'automation': 'automation', 'rule': 'automation',
    'automation rule': 'automation', 'trigger': 'automation',
    'status': 'status', 'state': 'status', 'column': 'status',
    'transition': 'transition', 'move': 'transition',
    # Visualization
    'dashboard': 'dashboard', 'overview': 'dashboard', 'home': 'dashboard',
    'report': 'report', 'chart': 'report', 'graph': 'report',
    'analytics': 'report', 'metrics': 'report', 'statistics': 'report',
    'stats': 'report', 'insight': 'report', 'insights': 'report',
    'visualization': 'report', 'widget': 'report', 'gadget': 'report',
    'roadmap': 'roadmap', 'timeline': 'roadmap', 'gantt': 'roadmap',
    'gantt chart': 'roadmap', 'plan': 'roadmap', 'schedule': 'roadmap',
    # Fields/Properties
    'field': 'field', 'custom field': 'field', 'attribute': 'field',
    'property': 'field', 'metadata': 'field', 'column': 'field',
    'label': 'label', 'tag': 'label', 'category': 'label',
    'component': 'component', 'module': 'component',
    'priority': 'priority', 'severity': 'priority', 'urgency': 'priority',
    # Estimation
    'estimate': 'estimate', 'time tracking': 'estimate',
    'story point': 'estimate', 'story points': 'estimate',
    'time estimate': 'estimate', 'effort': 'estimate',
    'hours': 'estimate', 'time spent': 'estimate',
    'time logged': 'estimate', 'work log': 'estimate', 'worklog': 'estimate',
    # Communication
    'notification': 'notification', 'alert': 'notification',
    'email notification': 'notification', 'reminder': 'notification',
    'mention': 'notification', 'ping': 'notification',
    'comment': 'comment', 'note': 'comment', 'remark': 'comment',
    'reply': 'comment', 'feedback': 'comment', 'annotation': 'comment',
    'message': 'message', 'chat': 'message', 'dm': 'message',
    'direct message': 'message', 'conversation': 'message',
    # Hierarchy
    'subtask': 'subtask', 'sub-task': 'subtask', 'child issue': 'subtask',
    'child task': 'subtask', 'sub-issue': 'subtask', 'sub issue': 'subtask',
    'child': 'subtask', 'sub': 'subtask',
    'parent': 'parent', 'parent issue': 'parent', 'parent task': 'parent',
    # Files
    'attachment': 'attachment', 'file': 'attachment', 'document': 'attachment',
    'doc': 'attachment', 'image': 'attachment', 'screenshot': 'attachment',
    # Versioning
    'version': 'version', 'release': 'version', 'fix version': 'version',
    'deploy': 'version', 'deployment': 'version', 'build': 'version',
    # Auth/Access
    'permission': 'permission', 'role': 'permission', 'access': 'permission',
    'privilege': 'permission', 'right': 'permission',
    'user': 'user', 'member': 'user', 'team member': 'user',
    'account': 'user', 'profile': 'user', 'assignee': 'user',
    'admin': 'admin', 'administrator': 'admin', 'owner': 'admin',
    # Config
    'template': 'template', 'blueprint': 'template', 'preset': 'template',
    'default': 'template', 'boilerplate': 'template',
    'scheme': 'scheme', 'schema': 'scheme', 'configuration': 'scheme',
    'screen': 'screen', 'form': 'screen', 'layout': 'screen',
    'view': 'screen', 'page': 'screen', 'interface': 'screen',
    'resolution': 'resolution', 'outcome': 'resolution',
    # Tech
    'api': 'api', 'rest api': 'api', 'graphql': 'api', 'endpoint': 'api',
    'webhook': 'webhook', 'callback': 'webhook', 'hook': 'webhook',
    'plugin': 'plugin', 'app': 'plugin', 'add-on': 'plugin',
    'addon': 'plugin', 'marketplace': 'plugin', 'extension': 'plugin',
    'integration': 'integration', 'connector': 'integration',
    'bridge': 'integration', 'adapter': 'integration',
    # Common abbreviations and their full forms
    'sla': 'sla', 'service level agreement': 'sla',
    'slo': 'sla', 'service level objective': 'sla',
    'kpi': 'kpi', 'key performance indicator': 'kpi',
    'okr': 'okr', 'objective key result': 'okr',
    'objectives and key results': 'okr',
    'roi': 'roi', 'return on investment': 'roi',
    'csv': 'csv', 'spreadsheet': 'csv', 'excel file': 'csv',
    'pdf': 'pdf', 'portable document': 'pdf',
    'sso': 'sso', 'single sign on': 'sso', 'single sign-on': 'sso',
    'saml': 'sso', 'oauth': 'sso', 'oidc': 'sso',
    'mfa': 'mfa', 'multi-factor authentication': 'mfa',
    'multi factor authentication': 'mfa',
    'two-factor authentication': 'mfa', '2fa': 'mfa',
    'two factor authentication': 'mfa', 'two factor': 'mfa',
    'rbac': 'rbac', 'role-based access control': 'rbac',
    'role based access control': 'rbac',
    'ci/cd': 'cicd', 'ci cd': 'cicd', 'cicd': 'cicd',
    'continuous integration': 'cicd', 'continuous deployment': 'cicd',
    'continuous delivery': 'cicd',
    'etl': 'etl', 'extract transform load': 'etl',
    'jql': 'jql', 'query language': 'jql', 'query': 'jql',
    'wbs': 'wbs', 'work breakdown structure': 'wbs',
    'raci': 'raci', 'responsibility matrix': 'raci',
    'crud': 'crud', 'create read update delete': 'crud',
    'crm': 'crm', 'customer relationship management': 'crm',
    'erp': 'erp', 'enterprise resource planning': 'erp',
    'itsm': 'itsm', 'it service management': 'itsm',
    'itil': 'itsm', 'it infrastructure library': 'itsm',
    'devops': 'devops', 'dev ops': 'devops',
    'devsecops': 'devops', 'dev sec ops': 'devops',
    'scrum': 'scrum', 'agile': 'scrum',
    'pr': 'pr', 'pull request': 'pr', 'merge request': 'pr',
    'mr': 'pr',
    'ide': 'ide', 'editor': 'ide', 'code editor': 'ide',
    'vscode': 'ide', 'vs code': 'ide', 'visual studio code': 'ide',
    'db': 'database', 'database': 'database',
    'sql': 'database', 'mysql': 'database', 'postgresql': 'database',
    'ui': 'ui', 'user interface': 'ui', 'interface': 'ui',
    'ux': 'ui', 'user experience': 'ui',
}


def normalize_for_merge(text, tool_name):
    """Aggressively normalize a keyword for second-pass merging."""
    text = text.lower().strip()

    # Remove tool name and variants
    tool_lower = tool_name.lower()
    for variant in [f'{tool_lower} software cloud', f'{tool_lower} cloud',
                    f'{tool_lower} software server', f'{tool_lower} software',
                    f'{tool_lower} service desk', f'{tool_lower} service management',
                    tool_lower]:
        text = text.replace(variant, ' ')

    # Remove how-to prefixes
    for prefix in ['how to ', 'how do i ', 'how do you ', 'how can i ',
                   'how can you ', 'how can we ']:
        if text.startswith(prefix):
            text = text[len(prefix):]
            break

    # Remove question marks
    text = text.rstrip('?').strip()

    # Apply action synonyms (multi-word first)
    for phrase, canonical in sorted(ACTION_SYNONYMS.items(), key=lambda x: -len(x[0])):
        if ' ' in phrase:
            text = text.replace(phrase, canonical)

    # Split into words
    words = text.split()

    # Apply single-word action synonyms
    words = [ACTION_SYNONYMS.get(w, w) for w in words]

    # Apply object synonyms (multi-word phrases first)
    text_rejoined = ' '.join(words)
    for phrase, canonical in sorted(OBJECT_SYNONYMS.items(), key=lambda x: -len(x[0])):
        if ' ' in phrase:
            text_rejoined = text_rejoined.replace(phrase, canonical)
    words = text_rejoined.split()

    # Apply single-word object synonyms
    words = [OBJECT_SYNONYMS.get(w, w) for w in words]

    # Remove stop words
    words = [w for w in words if w not in STOP_WORDS and len(w) > 1]

    # Simple plural stripping
    normalized = []
    for w in words:
        if w.endswith('ies') and len(w) > 4:
            normalized.append(w[:-3] + 'y')
        elif w.endswith('ses') and len(w) > 4:
            normalized.append(w[:-2])
        elif w.endswith('s') and not w.endswith('ss') and not w.endswith('us') and len(w) > 3:
            normalized.append(w[:-1])
        else:
            normalized.append(w)

    # Sort words for order-independent matching
    return tuple(sorted(set(normalized)))


def word_overlap_score(words_a, words_b):
    """Calculate Jaccard similarity between two word sets."""
    if not words_a or not words_b:
        return 0.0
    set_a = set(words_a)
    set_b = set(words_b)
    intersection = set_a & set_b
    union = set_a | set_b
    return len(intersection) / len(union) if union else 0.0


def merge_groups(rows, tool_name, threshold=0.75):
    """
    Merge rows with high word overlap using centroid-based clustering.
    Each entry is compared against the cluster representative (highest-variant member),
    NOT transitively chained. This prevents "create sprint" merging into "create project"
    via intermediate steps.
    """
    # Normalize all entries
    normalized = []
    for i, row in enumerate(rows):
        words = normalize_for_merge(row['canonical_keyword'], tool_name)
        normalized.append(words)

    # Sort by variant count descending — process high-signal entries first
    # They become cluster representatives
    indexed = list(range(len(rows)))
    indexed.sort(key=lambda i: -int(rows[i]['variant_count']))

    # Centroid-based greedy clustering
    # Each cluster has a representative (the highest-variant entry)
    # New entries only merge if they match the representative, not any member
    clusters = []  # list of (rep_words, rep_idx, [member_indices])

    # Build inverted index for speed: word -> cluster indices
    word_to_clusters = defaultdict(list)

    for i in indexed:
        words_i = normalized[i]
        cat_i = rows[i]['category']
        set_i = set(words_i)

        best_cluster = None
        best_score = 0.0

        # Only check clusters that share at least one word
        candidate_clusters = set()
        for w in words_i:
            for c_idx in word_to_clusters.get(w, []):
                candidate_clusters.add(c_idx)

        for c_idx in candidate_clusters:
            rep_words, rep_i, members = clusters[c_idx]
            # Must be same category
            if rows[rep_i]['category'] != cat_i:
                continue
            score = word_overlap_score(words_i, rep_words)
            if score >= threshold and score > best_score:
                best_score = score
                best_cluster = c_idx

        if best_cluster is not None:
            clusters[best_cluster][2].append(i)
        else:
            # Create new cluster with this entry as representative
            c_idx = len(clusters)
            clusters.append((words_i, i, [i]))
            for w in words_i:
                word_to_clusters[w].append(c_idx)

    # Convert to dict format
    result = {}
    for rep_words, rep_i, members in clusters:
        result[rep_i] = members

    return result


def main():
    parser = argparse.ArgumentParser(description='Second-pass keyword consolidation')
    parser.add_argument('input_file', help='First-pass processed CSV')
    parser.add_argument('--tool', '-t', default='Jira', help='Tool name (default: Jira)')
    parser.add_argument('--output', '-o', help='Output CSV file path')
    parser.add_argument('--min-variants', type=int, default=2,
                        help='Minimum combined variant count to keep (default: 2)')
    parser.add_argument('--threshold', type=float, default=0.7,
                        help='Word overlap threshold for merging (default: 0.7)')

    args = parser.parse_args()
    tool_name = args.tool

    # Load first-pass results
    with open(args.input_file, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    print(f"Loaded {len(rows)} intents from first pass")

    # Filter out already covered and junk entries
    junk_prefixes = ('#', '=', '!', '@')
    junk_rows = [r for r in rows if r['canonical_keyword'].startswith(junk_prefixes)]
    if junk_rows:
        print(f"Excel formula artifacts removed: {len(junk_rows)}")
    new_rows = [r for r in rows if r['already_covered'] != 'True'
                and not r['canonical_keyword'].startswith(junk_prefixes)]
    covered_rows = [r for r in rows if r['already_covered'] == 'True']
    print(f"Already covered: {len(covered_rows)} (removed)")
    print(f"New candidates: {len(new_rows)}")

    # Merge semantically similar
    print(f"\nRunning second-pass consolidation (threshold={args.threshold})...")
    clusters = merge_groups(new_rows, tool_name, threshold=args.threshold)

    # Build merged results
    merged = []
    for rep_idx, member_indices in clusters.items():
        members = [new_rows[i] for i in member_indices]

        # Combine variant counts
        total_variants = sum(int(m['variant_count']) for m in members)

        # Pick the best canonical (highest variant count member)
        best = max(members, key=lambda m: int(m['variant_count']))

        # Combine all variants
        all_variants = []
        for m in members:
            all_variants.extend(m['all_variants'].split(' | '))

        # Determine category (majority vote)
        cat_votes = defaultdict(int)
        for m in members:
            cat_votes[m['category']] += int(m['variant_count'])
        category = max(cat_votes.items(), key=lambda x: x[1])[0]

        signal = 'high' if total_variants >= 5 else 'medium' if total_variants >= 3 else 'low'

        merged.append({
            'canonical_keyword': best['canonical_keyword'],
            'suggested_title': best['suggested_title'],
            'category': category,
            'variant_count': total_variants,
            'signal_strength': signal,
            'cluster_size': len(members),
            'all_variants': ' | '.join(all_variants[:15]),
            'merged_from': ' | '.join(m['canonical_keyword'] for m in members) if len(members) > 1 else '',
        })

    print(f"After merging: {len(merged)} intents (from {len(new_rows)} pre-merge)")

    # Filter by minimum variant count
    serviceable = [m for m in merged if m['variant_count'] >= args.min_variants]
    dropped = len(merged) - len(serviceable)
    print(f"Dropped {dropped} intents with <{args.min_variants} variants")
    print(f"Final serviceable opportunities: {len(serviceable)}")

    # Sort by variant count descending
    serviceable.sort(key=lambda x: -x['variant_count'])

    # Stats
    cat_counts = defaultdict(int)
    for s in serviceable:
        cat_counts[s['category']] += 1

    print(f"\nBy category:")
    for cat, count in sorted(cat_counts.items(), key=lambda x: -x[1]):
        print(f"  {cat}: {count}")

    signal_counts = defaultdict(int)
    for s in serviceable:
        signal_counts[s['signal_strength']] += 1
    print(f"\nBy signal strength:")
    for sig in ['high', 'medium', 'low']:
        print(f"  {sig}: {signal_counts.get(sig, 0)}")

    # Print top opportunities
    print(f"\n{'='*80}")
    print(f"  TOP 50 NEW OPPORTUNITIES")
    print(f"{'='*80}")
    for i, s in enumerate(serviceable[:50], 1):
        marker = '★' if s['signal_strength'] == 'high' else '●' if s['signal_strength'] == 'medium' else '○'
        merge_note = f" (merged {s['cluster_size']} intents)" if s['cluster_size'] > 1 else ''
        print(f"\n  {i}. {marker} [{s['variant_count']} variants] {s['canonical_keyword']}{merge_note}")
        print(f"     Category: {s['category']}")

    # Write output
    output_path = args.output
    if not output_path:
        from pathlib import Path
        stem = Path(args.input_file).stem
        output_path = str(Path(args.input_file).parent / f"{stem}_final.csv")

    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow([
            'canonical_keyword', 'suggested_title', 'category',
            'variant_count', 'signal_strength', 'cluster_size',
            'all_variants', 'merged_from'
        ])
        for s in serviceable:
            writer.writerow([
                s['canonical_keyword'],
                s['suggested_title'],
                s['category'],
                s['variant_count'],
                s['signal_strength'],
                s['cluster_size'],
                s['all_variants'],
                s['merged_from'],
            ])

    print(f"\n✓ Final results written to: {output_path}")
    print(f"✓ {len(serviceable)} serviceable new keyword opportunities\n")


if __name__ == '__main__':
    main()
