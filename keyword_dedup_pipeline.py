#!/usr/bin/env python3
"""
==========================================================================
 KEYWORD DEDUP PIPELINE — Reusable "How To" Search Query Deduplicator
==========================================================================

A consolidated, battle-tested pipeline for deduplicating "how to [verb] [object]"
search query keyword lists. Handles DIRTY lists: converts non-standard formats,
filters non-tutorial noise, fixes typos, normalizes synonyms, and merges
duplicates across 4 proven passes — all in one run.

USAGE:
  # Python only (free, instant):
  python keyword_dedup_pipeline.py -i FILE -p PRODUCT

  # Python + AI (recommended, ~$0.10-0.25):
  ANTHROPIC_API_KEY=sk-... python keyword_dedup_pipeline.py -i FILE -p PRODUCT --ai-pass

  # Python + Haiku + Sonnet (most thorough, ~$0.25-0.50):
  ANTHROPIC_API_KEY=sk-... python keyword_dedup_pipeline.py -i FILE -p PRODUCT --ai-pass --sonnet-pass

EXAMPLES:
  python keyword_dedup_pipeline.py -i keywords_raw.csv -p "wordpress" --ai-pass

  python keyword_dedup_pipeline.py -i keywords_raw.csv -p "powerpoint" --ai-pass --sonnet-pass

  python keyword_dedup_pipeline.py -i keywords_raw.csv -p "meta" \
      --extra-products "facebook ads,instagram ads,facebook" --ai-pass

  python keyword_dedup_pipeline.py -i keywords_raw.csv -p "shopify"

CONFIGURATION:
  The script uses product-agnostic defaults that work for any "how to" keyword
  list. For best results on a NEW product, you should:

    1. Run with defaults first — inspect the output
    2. Add product-specific entries to the TYPOS, FILTER_PATTERNS, OBJ_SYNONYMS,
       CONCEPT_NORMALIZATIONS, and SOURCES dictionaries below as needed
    3. Re-run

  The current dictionaries are pre-loaded with terms proven for Google Slides
  and PowerPoint. They'll still work for other products — you just won't get
  the product-specific synonym coverage until you add those terms.

WHAT EACH PASS DOES:
  Pass 1 — Lexical Dedup:  Exact case-insensitive dedup, basic format normalization
  Pass 2 — Intent Dedup:   Filter non-tutorials, fix typos, strip versions,
                            verb/object synonym canonicalization, word-order
                            independence, device/format/source preservation
  Pass 3 — Sub-Intent:     Aggressive filler removal, "do/get/have" normalization,
                            singular/plural merging
  Pass 4 — Cluster Dedup:  Concept normalization (loop/autoplay/repeat → same),
                            massive noise word removal, final merge
  Cleanup — Format:        Convert remaining non-"how to" queries, final exact dedup

SAFETY:
  - NEVER merges opposite intents (add ≠ remove, show ≠ hide, etc.)
  - NEVER merges different conversion directions (pdf→slides ≠ slides→pdf)
  - Preserves device variants (ipad, chromebook, phone, etc.)
  - Preserves format variants (pdf, mp4, png, etc.)
  - Preserves source/integration variants (youtube, canva, excel, etc.)
  - Record ≠ Add for audio context (creating vs inserting existing)

TYPICAL RESULTS:
  60-70% reduction for dirty lists (8K-10K → 2.5K-3.6K)
  50-60% reduction for semi-clean lists (5K → 2K-2.5K)

ARCHITECTURE:
  Two-layer approach:

  Layer 1 — Python (free, instant, ~60% reduction):
    Pure deterministic regex + dictionary lookups.
    Handles: format conversion, exact/lexical dedup, verb/object synonym
    mapping, word-order normalization, noise removal, typo correction.

  Layer 2 — AI Dedup Pass (optional, ~$0.10-0.25, ~15-30 seconds):
    Sorts keywords alphabetically, chunks into ~400-keyword batches, sends
    to Haiku for semantic dedup. Optional Sonnet final pass for deep cleaning.
    Usage: --ai-pass flag + ANTHROPIC_API_KEY env var.

  Bonus — AI Review (optional, catches bad Python merges):
    Sends RISKY merge decisions to Claude API for validation.
    Usage: --ai-review flag. Alternative: --review-file for manual review.

  Typical flow:
    Raw keywords → [Python: free, 3s] → Draft → [Haiku: ~$0.10, 15s] → Final
    With Sonnet:
    Raw keywords → [Python] → [Haiku] → [Sonnet: ~$0.15] → Final
==========================================================================
"""

import re
import argparse
import sys
import os
import json
import time
import ssl
import urllib.request
import urllib.error
from collections import defaultdict


# ============================================================================
# AI DEDUP CONFIG (used by Section 10 and run_pipeline defaults)
# ============================================================================
AI_HAIKU_MODEL = "claude-haiku-4-5-20251001"
AI_SONNET_MODEL = "claude-sonnet-4-20250514"
AI_CHUNK_SIZE = 400          # keywords per Haiku chunk (alphabetically sorted)
AI_MAX_TOKENS = 8192         # max response tokens for Haiku
AI_SONNET_MAX_TOKENS = 16000 # max response tokens for Sonnet
AI_SONNET_BUDGET = 10000     # extended thinking budget for Sonnet


# ============================================================================
# SECTION 1: CONFIGURATION — Edit these for new products
# ============================================================================

# --- 1a. FILTER PATTERNS ---
# Regex patterns for queries that should be REMOVED entirely.
# These match content that is NOT an actionable software tutorial.

FILTER_PATTERNS = [
    # Non-English
    r"\b(crear|cómo|hacer|como fazer|como usar|como criar|membuat|presentasi|insertar|graficos|apresentacao|basico|en espanol|espanol)\b",
    r"¿",

    # Too vague / aesthetic / subjective advice
    r"^how to (make|get) .*(look |look$)(good|better|nice|nicer|pretty|cool|cute|aesthetic|professional|creative|interesting|appealing|engaging)",
    r"^how to (beautify|decorate|spice up|spruce up|jazz up|style|upgrade|improve|pimp) (google slides|powerpoint)",
    r"^how to make (nice|pretty|good|cool|cute|fancy|creative|beautiful|better|aesthetic|interesting|the best) (google slides|powerpoint)",
    r"^how to make (google slides|powerpoint) (interesting|less boring|more appealing|more engaging|visually appealing|cooler|look)",
    r"^how to improve (google slides|powerpoint)$",
    r"^how to improve (google slides|powerpoint) (presentation|skills|tips)",
    r"^how to make your (google slides|powerpoint) (look creative|stand out|presentation stand out)",
    r"^how to make the best (google slides|powerpoint)",

    # Presentation skills / public speaking (not software tutorials)
    r"^how to (begin|start|conclude|end|introduce yourself|engage audience|give a good|give a |give feedback|practice|plan a|structure a) .*(presentation|powerpoint|google slides)",
    r"^how to present (a case|a powerpoint|budget|challenges|data|demographic|feedback|financial|goals|kpi|numbers|qualitative|quantitative|risks|statistics|strengths|survey|your)",
    r"^tips on how to present",
    r"^how to (study|learn|get better at|get good at|work in) (powerpoint|google slides)",
    r"^how to learn (powerpoint|google slides)",
    r"^steps (in|to) (making|create|make) .*(presentation|powerpoint)",
    r"^how to write (a research paper|an essay) (powerpoint|google slides)",
    r"^how to host a powerpoint party",
    r"^how to avoid death by powerpoint",

    # Acquisition / pricing
    r"^how to (get|download|find) (free |microsoft )?(powerpoint|google slides)$",
    r"^how to get (free|microsoft) (powerpoint|google slides)",
    r"^how much ",
    r"for free$",

    # Spelling / meta
    r"^how to spell ",

    # Crash/corrupt (potentially malicious)
    r"^how to (crash|corrupt) (a )?(powerpoint|google slides)",

    # Not a tutorial - just a concept
    r"^(google slides|powerpoint) integration$",

    # Non-specific tutorial references
    r"^(google slides|powerpoint) tutorial",
    r"^(ms|microsoft|quick) (google slides|powerpoint) tutorial",
    r"^tutorial (google slides|powerpoint)",

    # Template/asset download searches (not tutorials)
    r"^\d+ step .* template",
    r"^step (by step|process|up process) .*(powerpoint|google slides|template)",
    r"^(next steps|steps) (icon|image|powerpoint|google slides|template)",
    r"^(powerpoint|google slides) (stair step|step by step|steps) (diagram|template|slide)",
    r"^(powerpoint|google slides) steps$",

    # Non-software queries
    r"^how to say .* in (spanish|french|german|chinese|japanese)",
    r"^how to check .* for ai$",
    r"^how to check plagiarism",

    # Garbled/concatenated queries
    r"how to.*how to",

    # Citation format queries (academic process, not software feature)
    r"^how to (apa|mla|chicago|harvard|ieee) (cite|format|reference)",
    r"^how to cite .* (apa|mla|chicago|harvard|ieee)",
    r"^how to cite .* in (apa|mla|chicago) (format|style|citation)",
    r"^how to cite a .*(presentation|powerpoint|google slides).* (apa|mla|chicago|harvard)",
    r"^how to (do |use )(apa|mla|chicago) (format|citation|reference|style) (on|in)",
    r"^how to reference .* (apa|mla|chicago|harvard)",

    # "how to make X" where X is a creative project theme, not a software feature
    r"^how to make a (sandwich|christmas|funeral|graduation|wedding|birthday|baby shower) .*(powerpoint|google slides)",
    r"^how to make (netflix|tinder|spotify) (powerpoint|google slides)",

    # Hacking/security exploit queries (not appropriate SEO targets)
    r"^how to hack ",
    r"^how to (brute force|exploit|crack|bypass login|phish)",

    # NSFW / adult content
    r"\b(porn|pornograph|hentai|xxx|nsfw|nude|naked|nudes)\b",
    r"\b(onlyfans|only fans|feetfinder|feet finder)\b",
    r"\b(blow ?job|hand ?job|bj )\b",
    r"\b(escort|hooker|prostitut|stripper)\b",
    r"\b(masturbat|orgasm|erotic|fetish)\b",
    r"\bhow to (have sex|hookup|hook up)\b",
    r"\bhow to (sext|send nudes|get laid)\b",
    r"\bhow to (seduce|flirt|attract.*sex)\b",

    # Drugs / substance abuse workarounds
    r"\bhow to (beat|pass|cheat) .*(drug test|mouth swab|peth test|urine test)\b",
    r"\bhow to (hide|fake|beat) .*(polygraph|lie detector)\b",

    # Incomplete / nonsensical queries (too short or garbled)
    r"^how to \w+ in wordpress$" + "(?<! wordpress)",  # Keep this commented — too broad
    r"^how in ",
    r"^how to wordpress$",
    r"^how to wordpress (admin panel|hosting|seo|seo plugin)$",
    r"^how to (html|video|videos) in ",
    r"^\w+ (website|site) how to$",
    r"^how to .* teach you",

    # Dated queries (old years that imply outdated content)
    r"\b201[0-8]\b",
]


# --- 1b. TYPO CORRECTIONS ---
TYPOS = {
    "verticle": "vertical",
    "backround": "background",
    "backgroud": "background",
    "imbed": "embed",
    "imbedded": "embedded",
    "presentaion": "presentation",
    "presentaton": "presentation",
    "presentaiton": "presentation",
    "slidehow": "slideshow",
    "animtion": "animation",
    "animaion": "animation",
    "tansparency": "transparency",
    "tansparent": "transparent",
    "boarder": "border",
    "boarders": "borders",
    "striketrhough": "strikethrough",
    "superscirpt": "superscript",
    "subscirpt": "subscript",
    "powepoint": "powerpoint",
    "powerpiont": "powerpoint",
    "gogle": "google",
    "googl": "google",
    "gooogle": "google",
    "goolge": "google",
    "slids": "slides",
    "acess": "access",
    "arcive": "archive",
    "funuel": "funnel",
    "colour": "color",
    "colours": "colors",
    "slidess": "slides",
    "peardeck": "pear deck",
    "slidesai.io": "slidesai",
    "thinkcell": "think cell",
}


# --- 1c. VERB CANONICALIZATION ---
# Maps verb synonyms → canonical form. Used in Passes 2-4.
# Key principle: antonyms are NEVER in the same group.
VERB_CANON = {
    # add family
    "add": "add", "insert": "add", "put": "add", "embed": "add",
    "place": "add", "include": "add", "stick": "add", "attach": "add",
    "imbed": "add",
    # import family (merged with add in Pass 4 for max dedup)
    "import": "import", "upload": "import", "load": "import",
    # create family
    "create": "create", "make": "create", "build": "create",
    "design": "create", "generate": "create", "craft": "create",
    "construct": "create",
    # change family
    "change": "change", "edit": "change", "modify": "change",
    "update": "change", "adjust": "change", "alter": "change",
    "customize": "change", "tweak": "change",
    # remove family
    "remove": "remove", "delete": "remove", "erase": "remove",
    "clear": "remove", "get rid of": "remove",
    "take down": "remove", "take out": "remove", "take off": "remove",
    "uninstall": "remove", "deactivate": "deactivate",
    # exit family
    "get out of": "exit", "exit": "exit",
    # enter family
    "get into": "enter", "enter": "enter",
    # copy family
    "copy": "copy", "duplicate": "copy", "clone": "copy",
    # convert family
    "convert": "convert", "transform": "convert",
    # transfer family
    "transfer": "transfer", "move": "transfer",
    # download/save/export
    "download": "download", "save": "download", "export": "export",
    # record family
    "record": "record", "narrate": "record", "capture": "record",
    # view family
    "view": "view", "see": "view", "check": "view",
    "find": "find", "locate": "find", "search": "find",
    # hide/show (opposites — separate groups)
    "hide": "hide", "conceal": "hide",
    "show": "show", "display": "show", "reveal": "show", "unhide": "show",
    # resize
    "resize": "resize", "scale": "resize",
    # crop
    "crop": "crop", "trim": "crop",
    # rotate/flip — SEPARATE (different visual operations)
    "rotate": "rotate",
    "flip": "flip", "mirror": "flip",
    # lock/unlock (opposites — separate)
    "lock": "lock", "freeze": "lock",
    "unlock": "unlock", "unfreeze": "unlock",
    # align
    "align": "align", "center": "align",
    # animate
    "animate": "animate",
    # format
    "format": "format",
    # highlight
    "highlight": "highlight",
    # group/ungroup (opposites)
    "group": "group",
    "ungroup": "ungroup",
    # merge
    "merge": "merge", "combine": "merge",
    # share
    "share": "share", "send": "share",
    # link
    "link": "link", "hyperlink": "link",
    # print
    "print": "print",
    # wrap
    "wrap": "wrap",
    # cite
    "cite": "cite", "reference": "cite",
    # compress
    "compress": "compress", "condense": "compress", "shrink": "compress",
    "minimize": "compress", "decrease": "compress", "reduce": "compress",
    # present
    "present": "present",
    # loop
    "loop": "loop", "autoplay": "loop", "repeat": "loop",
    # access
    "access": "access", "open": "access",
    # write
    "write": "write", "type": "write",
    # draw
    "draw": "draw",
    # number
    "number": "number",
    # indent
    "indent": "indent",
    # outline
    "outline": "outline",
    # install family (merged with add in Pass 4)
    "install": "install", "setup": "install", "configure": "install",
    "integrate": "install", "connect": "install",
    # misc
    "set": "set", "use": "use", "apply": "apply",
    "enable": "enable", "disable": "disable",
    "fix": "fix", "repair": "fix", "recover": "fix",
    "undo": "undo",
    "run": "run", "play": "run", "start": "run",
    "stop": "stop", "pause": "stop",
    "collaborate": "collaborate",
    "translate": "translate",
    "annotate": "annotate",
    "zoom": "zoom",
    "blur": "blur",
    "turn": "change", "switch": "change",
}

# Verb pairs that should NEVER be merged even if they share a canonical form
NEVER_MERGE_VERB_PAIRS = {
    frozenset({"flip", "rotate"}),
    frozenset({"record", "add"}),
    frozenset({"add", "remove"}),
    frozenset({"show", "hide"}),
    frozenset({"lock", "unlock"}),
    frozenset({"group", "ungroup"}),
    frozenset({"import", "export"}),
    frozenset({"download", "upload"}),
    frozenset({"enable", "disable"}),
    frozenset({"add", "create"}),
    frozenset({"enter", "exit"}),
    frozenset({"run", "stop"}),
    frozenset({"deactivate", "activate"}),
    frozenset({"remove", "activate"}),
    frozenset({"remove", "enter"}),
}


# --- 1d. OBJECT SYNONYMS ---
OBJ_SYNONYMS = {
    # image
    "picture": "image", "photo": "image", "pic": "image", "img": "image",
    "pictures": "image", "photos": "image", "pics": "image", "images": "image",
    # bullets
    "bullet point": "bullet", "bullet points": "bullet",
    "bulletpoint": "bullet", "bulletpoints": "bullet",
    "bullets": "bullet", "bulleted list": "bullet",
    # slide numbers
    "slide number": "slide-number", "slide numbers": "slide-number",
    "page number": "slide-number", "page numbers": "slide-number",
    # speaker notes
    "speaker note": "speaker-note", "speaker notes": "speaker-note",
    "presenter notes": "speaker-note", "presenter note": "speaker-note",
    # voiceover
    "voice over": "voiceover", "voice-over": "voiceover",
    "voice recording": "voiceover",
    # text box
    "textbox": "text-box", "text box": "text-box",
    # bg
    "bg": "background",
    # site/website
    "website": "site", "websites": "site", "sites": "site",
    "webpage": "page", "webpages": "page", "web page": "page",
    "blog post": "post", "blog posts": "post", "posts": "post",
    # transparency/opacity
    "opacity": "transparency",
    # portrait/vertical
    "vertical": "portrait", "vertically": "portrait",
    # curves
    "arch": "curve", "arc": "curve", "bend": "curve", "warp": "curve",
    "arched": "curved", "bent": "curved", "warped": "curved",
    # strikethrough
    "strike through": "strikethrough", "strike out": "strikethrough",
    "cross out": "strikethrough", "crossout": "strikethrough",
    "strikeout": "strikethrough",
    # sound/audio
    "sound": "audio", "sfx": "audio", "sound effect": "audio",
    "sound effects": "audio", "sounds": "audio", "audios": "audio",
    # slideshow/presentation
    "slideshow": "presentation", "slide show": "presentation",
    "deck": "presentation",
    # equations/math
    "equation": "equation", "equations": "equation",
    "formula": "equation", "formulas": "equation",
    "math equation": "equation", "math equations": "equation",
    # exponents
    "exponent": "exponent", "exponents": "exponent",
    # fractions
    "fraction": "fraction", "fractions": "fraction",
    # fonts
    "custom font": "custom-font", "custom fonts": "custom-font",
    "downloaded font": "custom-font", "downloaded fonts": "custom-font",
    # plurals → singular canonical
    "animation": "animation", "animations": "animation",
    "transition": "transition", "transitions": "transition",
    "border": "border", "borders": "border",
    "template": "template", "templates": "template",
    "theme": "theme", "themes": "theme",
    "emoji": "emoji", "emojis": "emoji",
    "comment": "comment", "comments": "comment",
    "recording": "recording", "recordings": "recording",
    "shadow": "shadow", "shadows": "shadow", "drop shadow": "shadow",
    "footnote": "footnote", "footnotes": "footnote",
    "arrow": "arrow", "arrows": "arrow",
    "columns": "column", "column": "column",
    "check mark": "checkmark", "checkbox": "checkbox",
    "check box": "checkbox", "checkboxes": "checkbox",
    "gifs": "gif",
    "videos": "video", "video clip": "video", "video clips": "video",
    "notes": "note", "slides": "slide",
    "shapes": "shape", "layers": "layer",
    "charts": "chart", "graph": "chart", "graphs": "chart",
    "tables": "table", "timers": "timer", "countdown": "timer",
    "countdown timer": "timer",
    "icons": "icon", "headers": "header", "footers": "footer",
    "rows": "row", "lines": "line", "fonts": "font",
    "colors": "color", "backgrounds": "background",
    "watermarks": "watermark", "captions": "caption",
    "subtitles": "subtitle", "titles": "title",
    "buttons": "button", "guidelines": "guide", "guides": "guide",
    "links": "link", "hyperlinks": "link",
    "sections": "section", "tabs": "tab",
    "margins": "margin", "labels": "label",
    "placeholders": "placeholder",
    # compound object synonyms
    "org chart": "org-chart", "organizational chart": "org-chart",
    "organization chart": "org-chart",
    "pie chart": "pie-chart", "gantt chart": "gantt-chart",
    "bar chart": "bar-chart",
    "flow chart": "flowchart", "flow charts": "flowchart",
    "word cloud": "word-cloud",
    "text to speech": "text-to-speech",
    "color picker": "color-picker", "eyedropper": "color-picker",
    "eye dropper": "color-picker",
    "paint format": "format-painter", "format painter": "format-painter",
    "version history": "version-history", "edit history": "version-history",
    "revision history": "version-history",
    "master slide": "master-slide", "slide master": "master-slide",
    "design ideas": "design-ideas", "designer": "design-ideas",
    "presenter view": "presenter-view", "presenter mode": "presenter-view",
    # WordPress-specific object synonyms
    "admin dashboard": "admin", "admin panel": "admin",
    "admin page": "admin", "admin login": "admin",
    "dashboard": "admin",
    "admin area": "admin", "admin console": "admin",
    "table of content": "toc", "table of contents": "toc",
    "social media icons": "social-icons", "social icons": "social-icons",
    "social media buttons": "social-icons", "social media links": "social-icons",
    "social media widget": "social-icons",
    "contact form": "contactform", "contact us form": "contactform",
    "landing page": "landingpage", "landing pages": "landingpage",
    "coming soon page": "comingsoon", "maintenance mode": "comingsoon",
    "under construction": "comingsoon",
    "featured image": "featuredimage", "featured images": "featuredimage",
    "thumbnail": "featuredimage",
    "google map": "googlemap", "google maps": "googlemap",
    "search bar": "searchbar", "search box": "searchbar",
    "search form": "searchbar",
    "popup form": "popup", "popup banner": "popup",
    "pop up form": "popup", "pop up banner": "popup",
    "dropdown menu": "dropdown", "drop down menu": "dropdown",
    "dropdown list": "dropdown", "drop down list": "dropdown",
    "navigation menu": "menu", "nav menu": "menu",
    "navigation bar": "menu", "nav bar": "menu",
    "custom post type": "cpt", "custom post types": "cpt",
    "custom field": "customfield", "custom fields": "customfield",
    "xml sitemap": "sitemap", "html sitemap": "sitemap",
}


# --- 1e. CONCEPT NORMALIZATIONS ---
# Regex-based phrase-level normalization. Applied in Pass 4.
CONCEPT_NORMALIZATIONS = [
    # Loop/autoplay/repeat cluster
    (r'\b(auto ?loop|auto ?play|autoplay|loop|repeat|continuously play|play continuously|'
     r'continuously loop|continuous loop|play on loop|play on a loop|keep looping|keep replaying|'
     r'run on a loop|run on loop|play automatically|automatically play|auto advance|'
     r'automatically loop|play on repeat|play repeatedly|run continuously|make it loop|'
     r'make them loop|looping|keep playing)\b', 'loop'),

    # Portrait/vertical orientation cluster
    (r'\b(portrait|vertical|portrait mode|portrait orientation|vertically|'
     r'landscape to portrait|horizontal to portrait|horizontal to vertical)\b', 'portrait'),

    # Transparency/opacity cluster
    (r'\b(transparent|transparency|opacity|translucent|see through|see-through)\b', 'transparent'),

    # File size / compress cluster
    (r'\b(compress|compress file|file size smaller|smaller file|reduce file size|'
     r'file size|smaller size|shrink|condense|minimize size)\b', 'compress'),
]


# --- 1f. DEVICES / FORMATS / SOURCES ---
# Keywords that MUST be preserved as distinct modifiers (never merged away).

DEVICES = {"ipad", "iphone", "chromebook", "mac", "windows", "phone",
           "mobile", "android", "tablet", "laptop", "desktop", "pc",
           "app", "browser", "web", "ios", "apple pencil"}

FORMATS = {"pdf", "mp4", "mp3", "m4a", "wav", "mov", "jpg", "png",
           "gif", "svg", "pptx", "ppt", "docx", "xlsx", "csv", "html",
           "keynote", "tiff", "bmp", "wmv", "avi", "webm", "ogg", "webp"}

SOURCES = {
    # --- Presentation / Design ---
    "canva", "figma", "adobe", "photoshop", "illustrator", "premiere",
    "keynote", "prezi", "slidesgo", "flaticon", "unsplash", "pexels",
    "giphy", "slidesai", "think cell", "beautiful.ai", "plus ai",
    "slides carnival", "visme", "pitch", "gamma", "miro", "mural",
    "lucidchart", "whimsical", "excalidraw", "lottie",
    # --- Google / Microsoft Office ---
    "excel", "word", "google docs", "google sheets", "google forms",
    "google slides", "google drive", "google calendar", "google meet",
    "power bi", "sharepoint", "onenote", "onedrive", "microsoft 365",
    "google workspace", "google classroom", "google sites", "google tag manager",
    "google analytics", "google search console", "google adsense", "google ads",
    "google my business", "google merchant center", "google optimize",
    "looker studio", "data studio", "appsheet",
    # --- Communication / Meetings ---
    "zoom", "teams", "slack", "discord", "telegram", "whatsapp",
    "skype", "webex", "google chat", "microsoft teams",
    # --- Email ---
    "outlook", "gmail", "mailchimp", "klaviyo", "sendgrid", "mailgun",
    "constant contact", "convertkit", "activecampaign", "drip", "aweber",
    "sendinblue", "brevo", "moosend", "getresponse", "campaign monitor",
    "beehiiv", "substack", "buttondown", "flodesk",
    # --- Social Media ---
    "tiktok", "instagram", "facebook", "linkedin", "twitter", "x",
    "pinterest", "snapchat", "reddit", "threads", "bluesky", "mastodon",
    "buffer", "hootsuite", "sprout social", "later", "planoly",
    # --- Video / Audio / Recording ---
    "youtube", "vimeo", "spotify", "soundcloud", "audacity", "obs",
    "loom", "screencastify", "descript", "riverside", "streamyard",
    "anchor", "buzzsprout", "podbean", "transistor", "capcut",
    "davinci resolve", "final cut", "camtasia", "bandcamp", "wistia",
    "vidyard", "synthesia", "heygen",
    # --- CRM / Sales ---
    "salesforce", "hubspot", "pipedrive", "zoho", "zoho crm",
    "freshsales", "close", "copper", "insightly", "monday sales",
    "dynamics 365", "sugarcrm", "nimble", "keap", "infusionsoft",
    "highrise", "capsule crm", "nutshell", "agile crm",
    # --- Project Management / Productivity ---
    "notion", "airtable", "asana", "trello", "monday", "clickup",
    "basecamp", "jira", "linear", "height", "todoist", "ticktick",
    "coda", "smartsheet", "wrike", "teamwork", "hive", "productive",
    "taskade", "roam research", "obsidian", "logseq", "anytype",
    # --- Website Builders / CMS ---
    "wordpress", "squarespace", "wix", "webflow", "shopify",
    "ghost", "blogger", "weebly", "godaddy", "hostinger",
    "duda", "carrd", "framer", "bubble", "softr", "glide",
    "strikingly", "jimdo", "zyro", "site123",
    # --- E-commerce ---
    "woocommerce", "bigcommerce", "magento", "prestashop",
    "ecwid", "sellfy", "gumroad", "lemonsqueezy", "paddle",
    "podia", "teachable", "thinkific", "kajabi", "samcart",
    "clickfunnels", "kartra", "systeme", "stan store",
    # --- Payment / Finance ---
    "stripe", "paypal", "square", "braintree", "authorize.net",
    "razorpay", "mollie", "adyen", "wise", "plaid", "quickbooks",
    "xero", "freshbooks", "wave", "harvest", "toggl",
    # --- Automation / Integration ---
    "zapier", "make", "integromat", "ifttt", "n8n", "power automate",
    "tray.io", "workato", "pabbly", "automate.io", "integrately",
    "albato", "activepieces", "relay",
    # --- Customer Support ---
    "zendesk", "intercom", "freshdesk", "helpscout", "crisp",
    "tidio", "drift", "livechat", "olark", "tawk.to",
    "gorgias", "front", "gladly", "kayako", "groovehq",
    # --- Analytics / Data ---
    "hotjar", "mixpanel", "amplitude", "heap", "fullstory",
    "posthog", "segment", "matomo", "plausible", "fathom",
    "chartmogul", "baremetrics", "profitwell", "datadog", "grafana",
    "tableau", "metabase", "redash", "superset",
    # --- SEO / Marketing ---
    "ahrefs", "semrush", "moz", "screaming frog", "surfer seo",
    "clearscope", "marketmuse", "frase", "rank math", "yoast",
    "all in one seo", "seo press", "ubersuggest", "mangools",
    "se ranking", "serpstat", "brightedge", "conductor",
    # --- Advertising ---
    "facebook ads", "instagram ads", "tiktok ads", "linkedin ads",
    "twitter ads", "pinterest ads", "snapchat ads", "reddit ads",
    "amazon ads", "bing ads", "taboola", "outbrain",
    # --- Forms / Surveys ---
    "typeform", "jotform", "tally", "paperform", "cognito forms",
    "surveymonkey", "google surveys", "qualtrics", "formstack",
    "gravity forms", "wpforms", "ninja forms", "contact form 7",
    "fluent forms", "formidable forms", "happyforms",
    # --- HR / Hiring ---
    "gusto", "rippling", "deel", "bamboohr", "workday",
    "greenhouse", "lever", "ashby", "recruitee", "breezy",
    "lattice", "15five", "culture amp", "betterworks",
    # --- Development / DevOps ---
    "github", "gitlab", "bitbucket", "vercel", "netlify",
    "heroku", "aws", "azure", "google cloud", "digitalocean",
    "cloudflare", "render", "railway", "supabase", "firebase",
    "planetscale", "neon", "turso", "upstash", "redis",
    "docker", "kubernetes", "terraform", "jenkins", "circleci",
    "github actions", "sentry", "logrocket", "bugsnag",
    # --- AI / ML ---
    "chatgpt", "gemini", "copilot", "claude", "gpt", "openai",
    "dall-e", "midjourney", "sora", "stable diffusion", "runway",
    "elevenlabs", "jasper", "copy.ai", "writesonic", "grammarly",
    "otter.ai", "fireflies", "perplexity", "anthropic", "cohere",
    "hugging face", "replicate", "cursor", "lovable", "bolt",
    "v0", "replit",
    # --- Education ---
    "canvas", "schoology", "peardeck", "pear deck", "mentimeter",
    "kahoot", "nearpod", "edpuzzle", "quizlet", "quizizz",
    "padlet", "seesaw", "flipgrid", "loom education", "classdojo",
    "moodle", "blackboard", "brightspace",
    # --- WordPress plugins/themes/builders ---
    "elementor", "divi", "beaver builder", "gutenberg", "avada",
    "astra", "generatepress",
    "oceanwp", "flatsome",
    "jetpack", "wpforms", "acf",
    "rank math", "yoast", "all in one seo", "updraftplus", "sucuri", "wordfence", "akismet",
}

# --- 1g. NOISE WORDS ---
# Words that carry no distinguishing intent in a "how to" query.

NOISE_WORDS = {
    "a", "an", "the", "some", "my", "your", "our", "its", "their",
    "in", "on", "to", "into", "onto", "for", "from", "with", "at",
    "of", "by", "about", "is", "are", "was", "were", "be", "been",
    "being", "and", "or", "but", "just", "only", "even", "also",
    "that", "this", "these", "those", "which", "where", "when",
    "new", "more", "another", "other", "multiple", "own",
    "up", "down", "out", "over", "off", "so", "can", "will",
    "every", "all", "any", "each", "still", "already",
    "get", "got", "do", "did", "does", "done",
    "have", "has", "had", "having",
    "would", "could", "should", "might", "shall",
    "them", "it", "itself", "themselves",
    "way", "like", "using", "without", "while",
    "keep", "kept", "make", "making", "made",
    "put", "putting", "go", "going", "went", "gone",
    "back", "again", "same", "thing", "things", "stuff",
    "able", "need", "want", "try", "trying",
    "here", "there", "then", "now",
    "clip", "clips", "file", "files", "item", "items",
    "element", "elements", "piece", "pieces", "object", "objects",
    "content", "part", "parts", "component", "components",
    "entry", "entries", "asset", "assets",
    "own", "such", "well", "really", "actually",
    "one", "two", "specific", "certain", "particular",
    "mode", "option", "setting", "feature",
    "different", "various",
}

MULTI_WORD_VERBS = [
    "get rid of", "get out of", "get into", "get back to",
    "take down", "take out", "take off",
    "set up", "look at", "opt out of",
    "sign up for", "log in to", "sign in to", "come up with",
    "turn on", "turn off", "bring to", "send to",
    "bring forward", "send backward", "bring back", "send back",
    "turn into", "switch to", "cut out", "zoom in", "zoom out",
    "line up", "fill in", "blur out", "fade in", "fade out",
    "lay out", "round off", "round out",
    "voice over", "voice record", "screen record",
    "auto play", "auto loop",
]


# ============================================================================
# SECTION 2: UTILITY FUNCTIONS
# ============================================================================

def fix_typos(query):
    """Fix known typos in a query."""
    q = query
    for typo, fix in TYPOS.items():
        q = re.sub(r'\b' + re.escape(typo) + r'\b', fix, q, flags=re.IGNORECASE)
    return q


def strip_version(query, product):
    """Remove version years from queries (e.g., powerpoint 2016 → powerpoint)."""
    q = re.sub(
        r'\b(' + re.escape(product) + r')\s+(200[37]|201[0-9]|202[0-5]|365)\b',
        r'\1', query
    )
    q = re.sub(r'\s+', ' ', q).strip()
    return q


def singularize(word):
    """Simple rule-based singularization."""
    w = word.lower()
    skip = {"slides", "notes", "this", "always", "across", "process",
            "address", "access", "less", "class", "pass", "press",
            "success", "status", "canvas", "plus", "bus", "minus",
            "analysis", "basis", "crisis", "thesis", "emphasis",
            "graphics", "settings", "customs", "columns"}
    if w in skip:
        return w
    if w.endswith("ies") and len(w) > 4:
        return w[:-3] + "y"
    if w.endswith("ses") and len(w) > 4:
        return w[:-2]
    if w.endswith("s") and not w.endswith(("ss", "us", "is", "ness", "ous")) and len(w) > 3:
        return w[:-1]
    return w


def should_filter(query):
    """Check if query should be removed (non-tutorial content)."""
    q = query.lower().strip()
    for pat in FILTER_PATTERNS:
        try:
            if re.search(pat, q, re.IGNORECASE):
                return True
        except re.error:
            pass
    return False


def apply_obj_synonyms(text):
    """Apply object synonym mapping to text (multi-word first, then single)."""
    t = text
    for orig, canon in sorted(OBJ_SYNONYMS.items(), key=lambda x: -len(x[0])):
        t = re.sub(r'\b' + re.escape(orig) + r'\b', canon, t, flags=re.IGNORECASE)
    return t


def extract_verb_and_rest(query, product):
    """Extract verb and rest from a 'how to' query."""
    q = query.lower().strip()
    # Normalize "product how to X" → "how to X"
    if q.startswith(f"{product} how to "):
        q = "how to " + q[len(f"{product} how to "):]
    if not q.startswith("how to "):
        return None, q
    rest = q[7:]
    # Check multi-word verbs first (longest first)
    for mv in sorted(MULTI_WORD_VERBS, key=len, reverse=True):
        if rest.startswith(mv + " "):
            return mv, rest[len(mv)+1:]
        elif rest == mv:
            return mv, ""
    # Single word verb
    m = re.match(r'^(\w+)\s*(.*)', rest)
    if m:
        return m.group(1), m.group(2).strip()
    return None, rest


def extract_modifiers(query, product):
    """Extract device/format/source modifiers that must be preserved."""
    q = query.lower()
    mods = set()
    for d in DEVICES:
        if re.search(r'\b' + re.escape(d) + r'\b', q):
            mods.add(f"D:{d}")
    for f in FORMATS:
        if re.search(r'\b' + re.escape(f) + r'\b', q):
            mods.add(f"F:{f}")
    for s in sorted(SOURCES, key=len, reverse=True):
        if s in q and s != product:
            mods.add(f"S:{s}")
    return mods


def should_not_merge(q1, q2, product, all_products):
    """
    Safety check — prevents merging queries with genuinely different intents.
    Returns True if the queries should NOT be merged.
    """
    q1l, q2l = q1.lower(), q2.lower()

    # 1. Opposite verb check
    v1, _ = extract_verb_and_rest(q1l, product)
    v2, _ = extract_verb_and_rest(q2l, product)

    if v1 and v2:
        if frozenset({v1, v2}) in NEVER_MERGE_VERB_PAIRS:
            return True
        v1c = VERB_CANON.get(v1, v1)
        v2c = VERB_CANON.get(v2, v2)
        if frozenset({v1c, v2c}) in NEVER_MERGE_VERB_PAIRS:
            return True

        # Record vs add for audio context
        audio = {"audio", "voice", "narration", "sound", "recording", "voiceover", "music"}
        if any(w in q1l for w in audio) and any(w in q2l for w in audio):
            if (v1 == "record") != (v2 == "record"):
                return True

    # 2. Device check (word boundary to avoid "web" matching inside "website")
    d1 = {d for d in DEVICES if re.search(r'\b' + re.escape(d) + r'\b', q1l)}
    d2 = {d for d in DEVICES if re.search(r'\b' + re.escape(d) + r'\b', q2l)}
    if d1 != d2:
        return True

    # 3. Format check
    f1 = {f for f in FORMATS if re.search(r'\b' + re.escape(f) + r'\b', q1l)}
    f2 = {f for f in FORMATS if re.search(r'\b' + re.escape(f) + r'\b', q2l)}
    if f1 != f2:
        return True

    # 4. Source check (word boundary to avoid "word" matching inside "wordpress")
    s1 = {s for s in SOURCES if re.search(r'\b' + re.escape(s) + r'\b', q1l) and s != product}
    s2 = {s for s in SOURCES if re.search(r'\b' + re.escape(s) + r'\b', q2l) and s != product}
    if s1 != s2:
        return True

    # 5. Direction check for conversion queries
    prods = list(all_products) + ["canva", "pdf", "word", "excel", "keynote"]
    prods = list(set(prods))
    for p1 in prods:
        for p2 in prods:
            if p1 == p2:
                continue
            try:
                if p1 in q1l and p2 in q1l and p1 in q2l and p2 in q2l:
                    if (q1l.index(p1) < q1l.index(p2)) != (q2l.index(p1) < q2l.index(p2)):
                        return True
            except ValueError:
                pass

    # 6. Target object check
    targets = {"text", "image", "shape", "slide", "table", "chart",
               "video", "audio", "background", "title", "header", "footer",
               "text box", "paragraph", "cell"}
    _, r1 = extract_verb_and_rest(q1l, product)
    _, r2 = extract_verb_and_rest(q2l, product)
    t1 = {t for t in targets if re.search(r'\b' + re.escape(t) + r'\b', (r1 or "").lower())}
    t2 = {t for t in targets if re.search(r'\b' + re.escape(t) + r'\b', (r2 or "").lower())}
    if t1 and t2 and t1 != t2:
        if not t1.issubset(t2) and not t2.issubset(t1):
            return True

    return False


def pick_best(queries, product):
    """Pick the best canonical form from a group of duplicates."""
    scored = []
    for q in queries:
        score = 0
        ql = q.lower()
        if ql.startswith("how to") and product in ql:
            score += 20
        elif ql.startswith("how to"):
            score += 15
        v, _ = extract_verb_and_rest(ql, product)
        if v in ("add", "insert"): score += 5
        elif v in ("create", "make"): score += 4
        elif v in ("change", "edit"): score += 3
        elif v in ("convert", "remove", "delete"): score += 3
        elif v in ("do", "get", "have", "go"): score -= 5
        words = len(ql.split())
        if 5 <= words <= 9: score += 5
        elif words > 12: score -= 3
        elif words < 4: score -= 2
        if product in ql: score += 3
        # Penalize typos
        for typo in TYPOS:
            if typo in ql:
                score -= 10
        scored.append((score, len(q), q))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return scored[0][2]


def merge_groups(groups, product, all_products):
    """Merge keyword groups with safety checks. Returns (final_list, merge_log)."""
    final = []
    merge_log = []
    for key, group in groups.items():
        if len(group) == 1:
            final.append(group[0])
        else:
            # Split into sub-groups that are safe to merge
            sub_groups = []
            for kw in group:
                placed = False
                for sg in sub_groups:
                    if not any(should_not_merge(kw, existing, product, all_products) for existing in sg):
                        sg.append(kw)
                        placed = True
                        break
                if not placed:
                    sub_groups.append([kw])
            for sg in sub_groups:
                best = pick_best(sg, product)
                final.append(best)
                if len(sg) > 1:
                    merge_log.append((best, sg))
    return final, merge_log


# ============================================================================
# SECTION 3: PASS 1 — LEXICAL DEDUP
# ============================================================================

def pass1_lexical(keywords, product):
    """
    Pass 1: Exact case-insensitive dedup + basic format normalization.
    Also normalizes spacing variants and strips redundant brand prefixes.
    """
    # Normalize product name variants
    product_lower = product.lower()

    # --- Spacing normalizations: "drop down" → "dropdown", etc. ---
    SPACING_NORMALIZATIONS = [
        (r'\bset ?up\b', 'setup'),
        (r'\bdrop ?down\b', 'dropdown'),
        (r'\bsub ?menu\b', 'submenu'),
        (r'\bsub ?page\b', 'subpage'),
        (r'\bsub ?pages\b', 'subpages'),
        (r'\bsub ?categor', 'subcategor'),
        (r'\bsub ?domain\b', 'subdomain'),
        (r'\bdo ?follow\b', 'dofollow'),
        (r'\bno ?follow\b', 'nofollow'),
        (r'\bno ?index\b', 'noindex'),
        (r'\bsign ?up\b', 'signup'),
        (r'\blog ?in\b', 'login'),
        (r'\blog ?out\b', 'logout'),
        (r'\bfont ?awesome\b', 'fontawesome'),
        (r'\bwoo ?commerce\b', 'woocommerce'),
        (r'\bads\.txt\b', 'adstxt'),
        (r'\bads txt\b', 'adstxt'),
        (r'\bhome ?page\b', 'homepage'),
        (r'\bweb ?site\b', 'website'),
        (r'\bweb ?page\b', 'webpage'),
        (r'\bblog ?post\b', 'blogpost'),
        (r'\bpop ?up\b', 'popup'),
        (r'\bcheck ?box\b', 'checkbox'),
        (r'\bcheck ?list\b', 'checklist'),
        (r'\bread ?more\b', 'readmore'),
        (r'\bback ?up\b', 'backup'),
        (r'\bmail ?chimp\b', 'mailchimp'),
    ]

    # --- Brand prefix stripping: "google adsense" → "adsense", etc. ---
    # These prefixes add nothing for dedup — "add adsense" = "add google adsense"
    BRAND_PREFIX_STRIP = [
        (r'\bgoogle adsense\b', 'adsense'),
        (r'\bgoogle analytics\b', 'analytics'),
        (r'\bgoogle tag manager\b', 'gtm'),
        (r'\bgoogle search console\b', 'searchconsole'),
        (r'\bgoogle recaptcha\b', 'recaptcha'),
        (r'\bgoogle maps?\b', 'googlemap'),
        (r'\bgoogle fonts?\b', 'googlefont'),
        (r'\bfacebook pixel\b', 'fbpixel'),
        (r'\bmeta pixel\b', 'fbpixel'),
    ]

    normalized = []
    for kw in keywords:
        q = kw.strip()
        if not q:
            continue
        ql = q.lower()

        # Standardize "how do/can I/you/we" → "how to"
        q_norm = re.sub(r'^how do (you|i|we) ', 'how to ', ql)
        q_norm = re.sub(r'^how can (you|i|we) ', 'how to ', q_norm)

        # Normalize product name
        if product_lower == "powerpoint":
            q_norm = re.sub(r'\bpower ?point\b', 'powerpoint', q_norm)
            q_norm = re.sub(r'\bppt\b', 'powerpoint', q_norm)
        elif product_lower == "google slides":
            q_norm = re.sub(r'\bgoogle slide\b', 'google slides', q_norm)
            q_norm = re.sub(r'\bgslides\b', 'google slides', q_norm)
        elif product_lower == "wordpress":
            q_norm = re.sub(r'\bword ?press\b', 'wordpress', q_norm)
            q_norm = re.sub(r'\bwp\b', 'wordpress', q_norm)

        # Apply spacing normalizations
        for pattern, replacement in SPACING_NORMALIZATIONS:
            q_norm = re.sub(pattern, replacement, q_norm)

        # Apply brand prefix stripping
        for pattern, replacement in BRAND_PREFIX_STRIP:
            q_norm = re.sub(pattern, replacement, q_norm)

        # Normalize prepositions before product
        q_norm = re.sub(
            r'\b(in|on|to|into|onto|for)\s+(' + re.escape(product_lower) + r')\b',
            r'in \2', q_norm
        )

        # Strip trailing year references (2019, 2020, ... 2026)
        q_norm = re.sub(r'\s*(in )?(201[9]|202[0-6])\s*$', '', q_norm)
        # Strip parenthesized years
        q_norm = re.sub(r'\s*\(?(201[9]|202[0-6])\)?\s*$', '', q_norm)

        q_norm = re.sub(r'\s+', ' ', q_norm).strip()
        normalized.append(q_norm)

    # Exact dedup
    seen = {}
    deduped = []
    for kw in normalized:
        key = kw.lower().strip()
        if key not in seen:
            seen[key] = kw
            deduped.append(kw)

    return deduped


# ============================================================================
# SECTION 4: PASS 2 — INTENT DEDUP
# ============================================================================

def pass2_intent(keywords, product, all_products):
    """
    Pass 2: Filter non-tutorials, fix typos, strip versions, intent-level dedup.
    """
    # Step 1: Filter
    filtered = [kw for kw in keywords if not should_filter(kw)]

    # Step 2: Convert non-standard to "how to" format
    converted = convert_nonstandard(filtered, product)

    # Step 3: Fix typos, strip versions
    cleaned = []
    for kw in converted:
        kw = fix_typos(kw)
        kw = strip_version(kw, product)
        cleaned.append(kw)

    # Step 4: Exact dedup after cleaning
    seen = {}
    deduped = []
    for kw in cleaned:
        key = kw.lower().strip()
        if key not in seen:
            seen[key] = kw
            deduped.append(kw)

    # Step 5: Intent-level grouping
    groups = defaultdict(list)
    for kw in deduped:
        key = create_intent_key(kw, product)
        groups[key].append(kw)

    # Step 6: Merge with safety
    final, merge_log = merge_groups(groups, product, all_products)

    return final, merge_log


def create_intent_key(query, product):
    """Create an intent-level key for grouping."""
    q = query.lower().strip()
    q = fix_typos(q)
    q = strip_version(q, product)

    verb, rest = extract_verb_and_rest(q, product)
    if verb is None:
        q_clean = q.replace(product, "").strip()
        q_clean = apply_obj_synonyms(q_clean)
        tokens = [singularize(t) for t in q_clean.split() if t not in NOISE_WORDS]
        return "NS::" + " ".join(sorted(tokens))

    canon_verb = VERB_CANON.get(verb, verb)
    mods = extract_modifiers(query, product)

    rest_clean = rest.replace(product, "").strip()
    rest_clean = apply_obj_synonyms(rest_clean)

    tokens = rest_clean.split()
    content_tokens = []
    for t in tokens:
        t_clean = re.sub(r'[^a-z0-9-]', '', t.lower())
        if t_clean and t_clean not in NOISE_WORDS:
            t_clean = singularize(t_clean)
            is_mod = any(t_clean in m for m in mods)
            if not is_mod:
                content_tokens.append(t_clean)

    content_sorted = " ".join(sorted(set(content_tokens)))
    mods_str = "|".join(sorted(mods)) if mods else ""
    return f"{canon_verb}::{content_sorted}::{mods_str}"


def convert_nonstandard(keywords, product):
    """Convert non-'how to' queries to standard format where possible."""
    result = []
    for kw in keywords:
        q = kw.lower().strip()
        if q.startswith("how to "):
            result.append(kw)
            continue
        # "product how to X" → "how to X in product"
        if q.startswith(f"{product} how to "):
            action = q[len(f"{product} how to "):]
            if product not in action:
                result.append(f"how to {action} in {product}")
            else:
                result.append(f"how to {action}")
            continue
        # "how can i X" → "how to X"
        m = re.match(r'^how can i (.+)', q)
        if m:
            result.append(f"how to {m.group(1)}")
            continue
        # "integrate/connect X" → "how to integrate/connect X"
        if q.startswith("integrate ") or q.startswith("connect "):
            result.append(f"how to {q}")
            continue
        # "[tool] how to X" → "how to X on [tool]"
        m = re.match(r'^(\w+)\s+how to (.+)', q)
        if m:
            tool = m.group(1)
            action = m.group(2)
            result.append(f"how to {action} on {tool}")
            continue
        # "X steps" → "how to X"
        m = re.match(r'^(.+?)\s+steps$', q)
        if m:
            result.append(f"how to {m.group(1)}")
            continue
        # "learn/explain how to X" → "how to X"
        m = re.match(r'^(learn|explain)\s+how to (.+)', q)
        if m:
            result.append(f"how to {m.group(2)}")
            continue
        # "product [verb] [object]" with known verb → "how to [verb] [object] in product"
        m = re.match(rf'^{re.escape(product)}\s+(\w+)\s+(.+?)$', q)
        if m and m.group(1) in VERB_CANON:
            obj = re.sub(r'\s*tutorial\s*$', '', m.group(2)).strip()
            if obj:
                result.append(f"how to {m.group(1)} {obj} in {product}")
                continue
        # Skip generic tutorials
        if "tutorial" in q:
            continue
        # Keep as-is if can't convert
        result.append(kw)
    return result


# ============================================================================
# SECTION 5: PASS 3 — SUB-INTENT DEDUP
# ============================================================================

def pass3_sub_intent(keywords, product, all_products):
    """
    Pass 3: Aggressive filler removal, vague verb normalization.
    """
    MEDIA_WORDS = {"audio", "video", "image", "music", "sound", "font",
                   "gif", "pdf", "pptx", "mp3", "mp4", "wav",
                   "animation", "transition", "recording"}
    FILLER_AFTER_OBJECT = {
        "clip", "clips", "file", "files", "item", "items", "thing", "things",
        "stuff", "element", "elements", "piece", "pieces", "object", "objects",
        "content", "part", "parts", "section", "sections", "component",
        "entry", "entries", "asset", "assets", "source", "sources",
    }

    def create_sub_key(query):
        q = query.lower().strip()
        verb, rest = extract_verb_and_rest(q, product)
        if verb is None:
            cleaned = q.replace(product, "").strip()
            tokens = [t for t in cleaned.split() if t not in NOISE_WORDS]
            return "NS::" + " ".join(sorted(set(tokens)))

        canon_verb = VERB_CANON.get(verb, verb)
        # Normalize vague verbs
        if verb in ("do", "get", "have", "put", "go"):
            canon_verb = "add"

        mods = extract_modifiers(query, product)
        rest_clean = rest.replace(product, "").strip()

        # Apply object synonyms
        for orig, canon in sorted(OBJ_SYNONYMS.items(), key=lambda x: -len(x[0])):
            rest_clean = re.sub(r'\b' + re.escape(orig) + r'\b', canon, rest_clean)

        # Tokenize with filler removal
        tokens = rest_clean.split()
        cleaned_tokens = []
        i = 0
        while i < len(tokens):
            t = tokens[i].lower().strip(".,!?;:")
            if t in NOISE_WORDS:
                i += 1
                continue
            # Remove filler after media word
            if t in FILLER_AFTER_OBJECT and cleaned_tokens and cleaned_tokens[-1].lower() in MEDIA_WORDS:
                i += 1
                continue
            # Remove filler before media word
            if t in FILLER_AFTER_OBJECT and i + 1 < len(tokens) and tokens[i+1].lower() in MEDIA_WORDS:
                i += 1
                continue
            # Skip modifier tokens
            is_mod = any(t in m.split(":")[1] for m in mods)
            if is_mod:
                i += 1
                continue
            # Singularize
            if t.endswith("s") and not t.endswith(("ss", "us", "is", "ness", "ous")) and len(t) > 3:
                exceptions = {"slides", "notes", "this", "always", "across", "process",
                              "access", "less", "class", "pass", "press", "success",
                              "status", "canvas", "plus", "bus", "minus"}
                if t not in exceptions:
                    t = t[:-1]
            cleaned_tokens.append(t)
            i += 1

        content = " ".join(sorted(set(cleaned_tokens)))
        mods_str = "|".join(sorted(mods)) if mods else ""
        return f"{canon_verb}::{content}::{mods_str}"

    groups = defaultdict(list)
    for kw in keywords:
        key = create_sub_key(kw)
        groups[key].append(kw)

    final, merge_log = merge_groups(groups, product, all_products)
    return final, merge_log


# ============================================================================
# SECTION 6: PASS 4 — CLUSTER DEDUP
# ============================================================================

def pass4_cluster(keywords, product, all_products):
    """
    Pass 4: Concept normalization, aggressive noise removal, final merge.
    """
    # In this pass, aggressively merge verb families:
    # import/upload/load/install/setup/connect/integrate/use → add
    # transfer/move/export → convert
    VERB_CANON_AGG = dict(VERB_CANON)
    VERB_CANON_AGG["import"] = "add"
    VERB_CANON_AGG["upload"] = "add"
    VERB_CANON_AGG["load"] = "add"
    VERB_CANON_AGG["install"] = "add"
    VERB_CANON_AGG["setup"] = "add"
    VERB_CANON_AGG["configure"] = "add"
    VERB_CANON_AGG["integrate"] = "add"
    VERB_CANON_AGG["connect"] = "add"
    VERB_CANON_AGG["use"] = "add"
    VERB_CANON_AGG["paste"] = "add"
    VERB_CANON_AGG["transfer"] = "convert"
    VERB_CANON_AGG["move"] = "convert"
    VERB_CANON_AGG["export"] = "convert"
    VERB_CANON_AGG["bring"] = "convert"
    VERB_CANON_AGG["send"] = "convert"
    VERB_CANON_AGG["take"] = "convert"
    VERB_CANON_AGG["migrate"] = "convert"

    def create_cluster_key(query):
        q = query.lower().strip()
        if q.startswith(f"{product} how to "):
            q = "how to " + q[len(f"{product} how to "):]

        verb, rest = extract_verb_and_rest(q, product)
        if verb is None:
            cleaned = q.replace(product, "").strip()
            tokens = [singularize(t) for t in cleaned.split() if t not in NOISE_WORDS]
            return "NS::" + " ".join(sorted(set(tokens)))

        canon_verb = VERB_CANON_AGG.get(verb, verb)
        mods = extract_modifiers(query, product)
        rest = rest.replace(product, "").strip()

        # Apply concept normalizations
        for pattern, replacement in CONCEPT_NORMALIZATIONS:
            rest = re.sub(pattern, replacement, rest)

        # Apply object synonyms
        for orig, canon in sorted(OBJ_SYNONYMS.items(), key=lambda x: -len(x[0])):
            rest = re.sub(r'\b' + re.escape(orig) + r'\b', canon, rest)

        # Tokenize and clean
        tokens = rest.split()
        cleaned = []
        for t in tokens:
            t = re.sub(r'[^a-z0-9-]', '', t.lower())
            if not t or t in NOISE_WORDS:
                continue
            is_mod = any(t in m.split(":")[1] for m in mods)
            if is_mod:
                continue
            t = singularize(t)
            cleaned.append(t)

        content = " ".join(sorted(set(cleaned)))
        mods_str = "|".join(sorted(mods)) if mods else ""
        return f"{canon_verb}::{content}::{mods_str}"

    groups = defaultdict(list)
    for kw in keywords:
        key = create_cluster_key(kw)
        groups[key].append(kw)

    final, merge_log = merge_groups(groups, product, all_products)
    return final, merge_log


# ============================================================================
# SECTION 7: FINAL CLEANUP
# ============================================================================

def final_cleanup(keywords, product):
    """
    Final pass: Convert any remaining non-'how to' queries, then exact dedup.
    """
    converted = []
    for kw in keywords:
        q = kw.lower().strip()
        if q.startswith("how to "):
            converted.append(kw)
            continue

        # Try various conversion patterns
        # "how can i X" → "how to X"
        m = re.match(r'^how can i (.+)', q)
        if m:
            converted.append(f"how to {m.group(1)}")
            continue

        # "product how to X"
        m = re.match(rf'^{re.escape(product)}:?\s+how to (.+)', q)
        if m:
            action = m.group(1)
            converted.append(f"how to {action} in {product}" if product not in action else f"how to {action}")
            continue

        # "[X] how to [Y]"
        m = re.match(r'^(.+?)\s+how to\s*(.*)$', q)
        if m:
            content = m.group(1).strip()
            extra = m.group(2).strip()
            if extra:
                converted.append(f"how to {extra} {content}")
            else:
                converted.append(f"how to {content}")
            continue

        # "learn/explain how to X"
        m = re.match(r'^(learn|explain)\s+how to (.+)', q)
        if m:
            converted.append(f"how to {m.group(2)}")
            continue

        # Can't convert → drop it
        continue

    # Final exact dedup
    seen = {}
    deduped = []
    for kw in converted:
        key = kw.lower().strip()
        if key not in seen:
            seen[key] = kw
            deduped.append(kw)

    deduped.sort(key=lambda x: x.lower())
    return deduped


# ============================================================================
# SECTION 8: MAIN PIPELINE
# ============================================================================

def run_pipeline(input_file, product, output_file=None, extra_products=None,
                  verbose=True, ai_review=False, api_key=None,
                  review_file=False, risk_threshold=0.25,
                  ai_pass=False, sonnet_pass=False, chunk_size=AI_CHUNK_SIZE,
                  sonnet_threshold=1500):
    """
    Run the complete deduplication pipeline.

    Args:
        input_file:       Path to input CSV (one keyword per line)
        product:          Product name (e.g., "google slides", "powerpoint", "meta")
        output_file:      Path to output CSV (default: auto-generated)
        extra_products:   Additional product name variants (e.g., ["facebook ads", "instagram ads"])
        verbose:          Print progress and stats
        ai_review:        If True, run AI validation on risky merges (needs api_key)
        api_key:          Anthropic API key (or set ANTHROPIC_API_KEY env var)
        review_file:      If True, generate a review file instead of calling API
        risk_threshold:   Risk score cutoff for AI review (default 0.25)
        ai_pass:          If True, run AI dedup pass (Haiku chunks) after Python pipeline
        sonnet_pass:      If True, also run Sonnet final pass after Haiku
        chunk_size:       Keywords per Haiku chunk (default 400)
        sonnet_threshold: Only run Sonnet if keywords > this after Haiku (default 1500)

    Returns:
        list: Final deduplicated keyword list
    """
    product = product.lower().strip()
    all_products = {product}
    if extra_products:
        for p in extra_products:
            all_products.add(p.lower().strip())

    if output_file is None:
        base = os.path.splitext(os.path.basename(input_file))[0]
        output_file = os.path.join(os.path.dirname(input_file), f"{base}_DEDUPED.csv")

    # Read input
    keywords = []
    with open(input_file, 'r', encoding='utf-8') as f:
        for line in f:
            kw = line.strip()
            if kw:
                keywords.append(kw)

    total = len(keywords)
    if verbose:
        print(f"\n{'='*60}")
        print(f"  KEYWORD DEDUP PIPELINE")
        print(f"  Product: {product}")
        print(f"  Input:   {input_file}")
        print(f"  Output:  {output_file}")
        print(f"{'='*60}")
        print(f"\n  Raw input: {total:,} keywords")

    # --- Pass 1: Lexical Dedup ---
    kws = pass1_lexical(keywords, product)
    if verbose:
        print(f"\n  Pass 1 (Lexical):     {total:,} → {len(kws):,}  "
              f"(-{total - len(kws):,},  {(1-len(kws)/total)*100:.1f}% reduction)")
    p1_count = len(kws)

    # --- Pass 2: Intent Dedup ---
    kws, log2 = pass2_intent(kws, product, all_products)
    if verbose:
        print(f"  Pass 2 (Intent):      {p1_count:,} → {len(kws):,}  "
              f"(-{p1_count - len(kws):,},  {(1-len(kws)/p1_count)*100:.1f}% reduction)")
    p2_count = len(kws)

    # --- Pass 3: Sub-Intent Dedup ---
    kws, log3 = pass3_sub_intent(kws, product, all_products)
    if verbose:
        print(f"  Pass 3 (Sub-Intent):  {p2_count:,} → {len(kws):,}  "
              f"(-{p2_count - len(kws):,},  {(1-len(kws)/p2_count)*100:.1f}% reduction)")
    p3_count = len(kws)

    # --- Pass 4: Cluster Dedup ---
    kws, log4 = pass4_cluster(kws, product, all_products)
    if verbose:
        print(f"  Pass 4 (Cluster):     {p3_count:,} → {len(kws):,}  "
              f"(-{p3_count - len(kws):,},  {(1-len(kws)/p3_count)*100:.1f}% reduction)")
    p4_count = len(kws)

    # --- Final Cleanup ---
    kws = final_cleanup(kws, product)
    if verbose:
        print(f"  Cleanup (Format):     {p4_count:,} → {len(kws):,}  "
              f"(-{p4_count - len(kws):,})")

    # Collect merge logs for AI review
    all_merge_logs = [("Pass 2", log2), ("Pass 3", log3), ("Pass 4", log4)]

    if verbose:
        pct = (1 - len(kws)/total) * 100 if total > 0 else 0
        print(f"\n  {'='*50}")
        print(f"  PYTHON PIPELINE: {total:,} → {len(kws):,}  ({pct:.1f}% reduction)")
        print(f"  {'='*50}")

        # Print top merge examples from each pass
        for pass_name, log in all_merge_logs:
            if log:
                big = sorted(log, key=lambda x: -len(x[1]))[:5]
                if big:
                    print(f"\n  --- Top merges from {pass_name} ---")
                    for canonical, group in big:
                        others = [q for q in group if q != canonical][:3]
                        print(f"    KEPT: {canonical}")
                        for o in others:
                            print(f"      MERGED: {o}")
                        if len(group) > 4:
                            print(f"      ... and {len(group) - 4} more")

    # --- AI REVIEW LAYER ---
    if review_file:
        # Generate review file (free — no API key needed)
        review_path = output_file.replace('.csv', '_REVIEW.txt')
        generate_review_file(all_merge_logs, product, review_path, risk_threshold)

    if ai_review:
        # Resolve API key
        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            print("\n  ERROR: --ai-review requires an API key.")
            print("  Set ANTHROPIC_API_KEY environment variable or pass --api-key KEY")
            print("  Or use --review-file to generate a file for manual review instead.")
        else:
            kws = run_ai_review(kws, all_merge_logs, product, key,
                                threshold=risk_threshold, verbose=verbose)

    # Write Python-only output
    with open(output_file, 'w', encoding='utf-8') as f:
        for kw in kws:
            f.write(kw + '\n')

    if verbose:
        python_pct = (1 - len(kws)/total) * 100 if total > 0 else 0
        print(f"\n  {'='*50}")
        print(f"  PYTHON RESULT: {total:,} → {len(kws):,}  ({python_pct:.1f}% reduction)")
        print(f"  {'='*50}")
        print(f"  Output: {output_file}")

    # --- AI DEDUP PASS (optional) ---
    if ai_pass:
        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            print("\n  ERROR: --ai-pass requires an API key.")
            print("  Set ANTHROPIC_API_KEY environment variable or pass --api-key KEY")
        else:
            kws = run_ai_dedup_pass(
                kws, product, key,
                chunk_size=chunk_size,
                sonnet_pass=sonnet_pass,
                sonnet_threshold=sonnet_threshold,
                verbose=verbose,
            )

            # Write final AI-cleaned output
            base = os.path.splitext(output_file)[0]
            base = re.sub(r'_DEDUPED$', '', base)
            final_file = base + "_FINAL.csv"
            with open(final_file, 'w', encoding='utf-8') as f:
                for kw in kws:
                    f.write(kw + '\n')

            if verbose:
                total_pct = (1 - len(kws)/total) * 100 if total > 0 else 0
                print(f"\n  {'='*50}")
                print(f"  OVERALL: {total:,} → {len(kws):,}  ({total_pct:.1f}% total reduction)")
                print(f"  {'='*50}")
                print(f"  Python output: {output_file}")
                print(f"  Final output:  {final_file}")
    elif verbose:
        # No AI pass — Python output IS the final output
        pass

    return kws


# ============================================================================
# SECTION 9: AI VALIDATION LAYER
# ============================================================================
#
# The Python pipeline handles ~80% of dedup through deterministic rules.
# This section adds an AI safety net for the remaining ~20%: merge decisions
# where the two keywords have low word overlap and might be false positives.
#
# Architecture:
#   1. Collect all merge decisions from passes 2/3/4
#   2. Score each merge pair for "riskiness" (low word overlap = risky)
#   3. Batch the risky pairs and ask Claude API to validate
#   4. Split rejected merges back into the final output
#
# Cost: ~$0.10-0.50 per run (200-400 risky pairs → ~2K input tokens, ~1K output)
# Speed: ~5-15 seconds (one API call with batched pairs)

def compute_overlap(q1, q2, product):
    """
    Compute word overlap ratio between two queries (excluding noise/product).
    Returns float 0.0 (no overlap) to 1.0 (identical words).
    """
    def get_content_words(q):
        q_lower = q.lower()
        q_lower = q_lower.replace(product, "").strip()
        q_lower = re.sub(r'^how to\s+', '', q_lower)
        words = set(re.findall(r'[a-z]+', q_lower))
        words -= NOISE_WORDS
        words -= {"how", "to"}
        return words

    w1 = get_content_words(q1)
    w2 = get_content_words(q2)
    if not w1 and not w2:
        return 1.0
    if not w1 or not w2:
        return 0.0
    intersection = w1 & w2
    union = w1 | w2
    return len(intersection) / len(union) if union else 1.0


def compute_merge_risk(canonical, group, product):
    """
    Score how risky a merge decision is. Higher = riskier = needs AI review.

    Returns:
        risk_score (float): 0.0 (safe) to 1.0 (very risky)
        risk_reasons (list): Human-readable reasons for the risk
    """
    if len(group) <= 1:
        return 0.0, []

    risk = 0.0
    reasons = []

    # Factor 1: Word overlap between merged pairs
    min_overlap = 1.0
    for other in group:
        if other == canonical:
            continue
        overlap = compute_overlap(canonical, other, product)
        min_overlap = min(min_overlap, overlap)

    if min_overlap < 0.2:
        risk += 0.5
        reasons.append(f"very low word overlap ({min_overlap:.2f})")
    elif min_overlap < 0.4:
        risk += 0.3
        reasons.append(f"low word overlap ({min_overlap:.2f})")
    elif min_overlap < 0.6:
        risk += 0.15
        reasons.append(f"moderate word overlap ({min_overlap:.2f})")

    # Factor 2: Different verbs before canonicalization
    verbs_in_group = set()
    for kw in group:
        v, _ = extract_verb_and_rest(kw.lower(), product)
        if v:
            verbs_in_group.add(v)
    if len(verbs_in_group) > 1:
        risk += 0.15
        reasons.append(f"different original verbs: {verbs_in_group}")

    # Factor 3: Large group size (more things merged = more potential for error)
    if len(group) >= 5:
        risk += 0.15
        reasons.append(f"large merge group ({len(group)} items)")
    elif len(group) >= 3:
        risk += 0.05
        reasons.append(f"merge group of {len(group)}")

    # Factor 4: Very different lengths
    lengths = [len(kw.split()) for kw in group]
    if max(lengths) - min(lengths) >= 4:
        risk += 0.1
        reasons.append(f"big length difference ({min(lengths)} vs {max(lengths)} words)")

    return min(risk, 1.0), reasons


def identify_risky_merges(merge_logs, product, threshold=0.25):
    """
    Scan all merge logs and identify merges that exceed the risk threshold.

    Args:
        merge_logs: List of (pass_name, [(canonical, [group]), ...])
        product: Product name
        threshold: Risk score cutoff (0.25 = review merges with ≥25% risk)

    Returns:
        List of (pass_name, canonical, group, risk_score, reasons)
    """
    risky = []
    for pass_name, log in merge_logs:
        for canonical, group in log:
            score, reasons = compute_merge_risk(canonical, group, product)
            if score >= threshold:
                risky.append((pass_name, canonical, group, score, reasons))

    # Sort by risk score (highest first)
    risky.sort(key=lambda x: -x[3])
    return risky


def build_ai_review_prompt(risky_merges, product, max_pairs=400):
    """
    Build a prompt for the AI to validate risky merge decisions.

    Returns:
        prompt (str): The prompt to send to Claude
        pair_count (int): Number of merge pairs included
    """
    # Cap at max_pairs to control cost
    merges_to_review = risky_merges[:max_pairs]

    lines = []
    lines.append(f"You are reviewing keyword deduplication decisions for \"{product}\" software tutorials.")
    lines.append(f"Each entry below shows a KEPT keyword and the keywords that were MERGED into it (treated as duplicates).")
    lines.append(f"")
    lines.append(f"For each merge group, decide if the merge is CORRECT or PARTIALLY_WRONG or WRONG.")
    lines.append(f"- CORRECT = ALL merged keywords are genuinely the same intent as the KEPT keyword.")
    lines.append(f"- PARTIALLY_WRONG = MOST are correct, but 1-2 specific keywords do NOT belong. List the bad ones in \"bad_keywords\".")
    lines.append(f"- WRONG = The merge is fundamentally flawed — most/all merged keywords differ from KEPT.")
    lines.append(f"")
    lines.append(f"A merge is correct if a user searching either keyword would want the SAME tutorial article.")
    lines.append(f"A merge is wrong if the keywords would need DIFFERENT tutorial articles.")
    lines.append(f"")
    lines.append(f"IMPORTANT: If a group of 20 keywords has just 1 bad keyword, use PARTIALLY_WRONG and list only that 1 keyword.")
    lines.append(f"Do NOT mark the whole group as WRONG because of 1 outlier.")
    lines.append(f"")
    lines.append(f"Respond with a JSON array. Each element has:")
    lines.append(f'  {{"id": <number>, "verdict": "CORRECT" or "PARTIALLY_WRONG" or "WRONG", "reason": "<brief>", "bad_keywords": ["keyword1", "keyword2"]}}')
    lines.append(f"(bad_keywords is only needed for PARTIALLY_WRONG — list the specific keywords that should NOT have been merged)")
    lines.append(f"")
    lines.append(f"MERGE DECISIONS TO REVIEW:")
    lines.append(f"")

    for i, (pass_name, canonical, group, score, reasons) in enumerate(merges_to_review):
        others = [q for q in group if q != canonical]
        lines.append(f"--- #{i+1} (from {pass_name}, risk: {score:.2f}) ---")
        lines.append(f"  KEPT: {canonical}")
        for o in others:
            lines.append(f"  MERGED: {o}")
        lines.append(f"")

    return "\n".join(lines), len(merges_to_review)


def call_anthropic_api(prompt, api_key, max_tokens=8192):
    """
    Call the Anthropic Claude API to validate merge decisions.

    Uses the Messages API with claude-haiku-4.5 (cheapest, fastest).
    Falls back to a simple HTTPS request to avoid requiring the anthropic package.
    """
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}]
        )
        return message.content[0].text
    except ImportError:
        # Fall back to raw HTTPS if anthropic package not installed
        import urllib.request
        import ssl

        data = json.dumps({
            "model": "claude-haiku-4-5-20251001",
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}]
        }).encode("utf-8")

        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=data,
            headers={
                "Content-Type": "application/json",
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
            },
            method="POST"
        )

        ctx = ssl.create_default_context()
        with urllib.request.urlopen(req, context=ctx, timeout=120) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            return result["content"][0]["text"]


def estimate_prompt_tokens(prompt):
    """Rough token estimate: ~4 chars per token for English text."""
    return len(prompt) // 4


def parse_ai_response(response_text, verbose=True):
    """
    Parse the AI's JSON response into a list of verdicts.
    Handles: full JSON array, markdown-wrapped JSON, line-by-line JSON objects,
    and truncated responses.

    Returns:
        dict: {id_number: {"verdict": "CORRECT"/"WRONG", "reason": "..."}}
    """
    text = response_text.strip()

    if verbose and len(text) < 200:
        print(f"  DEBUG: Full AI response: {text[:500]}")
    elif verbose:
        print(f"  DEBUG: AI response length: {len(text)} chars, starts with: {text[:150]}...")

    # Strip markdown code fences if present
    text = re.sub(r'^```(?:json)?\s*\n?', '', text)
    text = re.sub(r'\n?```\s*$', '', text)

    # Strategy 1: Try parsing as a full JSON array
    try:
        verdicts_list = json.loads(text)
        if isinstance(verdicts_list, list):
            return _extract_verdicts(verdicts_list)
    except json.JSONDecodeError:
        pass

    # Strategy 2: Find the largest JSON array in the text
    match = re.search(r'\[.*\]', text, re.DOTALL)
    if match:
        try:
            verdicts_list = json.loads(match.group())
            if isinstance(verdicts_list, list):
                return _extract_verdicts(verdicts_list)
        except json.JSONDecodeError:
            pass

    # Strategy 3: Find individual JSON objects line by line (handles truncated arrays)
    verdicts = {}
    for match in re.finditer(r'\{[^{}]*\}', text):
        try:
            item = json.loads(match.group())
            vid = item.get("id")
            verdict = item.get("verdict", "").upper()
            reason = item.get("reason", "")
            if vid is not None and verdict in ("CORRECT", "WRONG"):
                verdicts[vid] = {"verdict": verdict, "reason": reason}
        except (json.JSONDecodeError, AttributeError, TypeError):
            continue

    if verdicts:
        if verbose:
            print(f"  (Parsed {len(verdicts)} verdicts via line-by-line extraction)")
        return verdicts

    # Strategy 4: Look for simple "WRONG" / "CORRECT" patterns with IDs
    for match in re.finditer(r'#(\d+).*?(CORRECT|WRONG)', text, re.IGNORECASE):
        vid = int(match.group(1))
        verdict = match.group(2).upper()
        verdicts[vid] = {"verdict": verdict, "reason": ""}

    if verdicts:
        if verbose:
            print(f"  (Parsed {len(verdicts)} verdicts via pattern matching)")
        return verdicts

    print("  WARNING: Could not parse AI response")
    return {}


def _extract_verdicts(verdicts_list):
    """Extract verdict dict from a parsed JSON list."""
    verdicts = {}
    for item in verdicts_list:
        try:
            vid = item.get("id")
            verdict = item.get("verdict", "").upper()
            reason = item.get("reason", "")
            bad_keywords = item.get("bad_keywords", [])
            if vid is not None and verdict in ("CORRECT", "WRONG", "PARTIALLY_WRONG"):
                verdicts[vid] = {
                    "verdict": verdict,
                    "reason": reason,
                    "bad_keywords": bad_keywords or []
                }
        except (AttributeError, TypeError):
            continue
    return verdicts


def apply_ai_verdicts(final_keywords, risky_merges, verdicts, verbose=True):
    """
    Apply AI verdicts: for WRONG/PARTIALLY_WRONG merges, restore keywords.

    For WRONG: restore ALL merged keywords in the group.
    For PARTIALLY_WRONG: restore only the specific bad_keywords listed.

    Returns:
        Updated keyword list with wrongly-merged keywords restored
    """
    keywords_to_restore = []
    wrong_count = 0
    partial_count = 0
    correct_count = 0

    for i, (pass_name, canonical, group, score, reasons) in enumerate(risky_merges):
        vid = i + 1  # IDs are 1-indexed in the prompt
        if vid not in verdicts:
            continue

        verdict_data = verdicts[vid]
        verdict = verdict_data["verdict"]

        if verdict == "WRONG":
            wrong_count += 1
            others = [q for q in group if q != canonical]
            keywords_to_restore.extend(others)
            if verbose:
                reason = verdict_data.get("reason", "")
                print(f"    WRONG ({pass_name}): {canonical} — restored {len(others)} keywords")
                if reason:
                    print(f"      Reason: {reason}")

        elif verdict == "PARTIALLY_WRONG":
            partial_count += 1
            bad_kws = verdict_data.get("bad_keywords", [])
            if bad_kws:
                # Only restore the specific bad keywords
                bad_lower = {bk.lower().strip() for bk in bad_kws}
                restored = []
                for q in group:
                    if q.lower().strip() in bad_lower and q != canonical:
                        restored.append(q)
                keywords_to_restore.extend(restored)
                if verbose:
                    reason = verdict_data.get("reason", "")
                    print(f"    PARTIAL ({pass_name}): {canonical} — restored {len(restored)} of {len(group)-1}")
                    for r in restored:
                        print(f"      ← {r}")
                    if reason:
                        print(f"      Reason: {reason}")
            else:
                # AI said PARTIALLY_WRONG but didn't list bad keywords — treat as WRONG
                wrong_count += 1
                others = [q for q in group if q != canonical]
                keywords_to_restore.extend(others)
                if verbose:
                    print(f"    WRONG* ({pass_name}): {canonical} — no bad_keywords listed, restoring all {len(others)}")

        elif verdict == "CORRECT":
            correct_count += 1

    if keywords_to_restore:
        combined = list(final_keywords) + keywords_to_restore
        seen = {}
        deduped = []
        for kw in combined:
            key = kw.lower().strip()
            if key not in seen:
                seen[key] = kw
                deduped.append(kw)
        deduped.sort(key=lambda x: x.lower())
        final_keywords = deduped

    if verbose:
        print(f"\n  AI Review Results:")
        print(f"    Reviewed: {len(verdicts)} merge decisions")
        print(f"    Confirmed correct: {correct_count}")
        print(f"    Fully wrong (all restored): {wrong_count}")
        print(f"    Partially wrong (some restored): {partial_count}")
        print(f"    Keywords restored: {len(keywords_to_restore)}")

    return final_keywords


def run_ai_review(final_keywords, merge_logs, product, api_key,
                  threshold=0.25, max_pairs=400, verbose=True):
    """
    Full AI review workflow with automatic batching:
      1. Identify risky merges
      2. Split into batches if needed (to stay under token limits)
      3. Call API for each batch
      4. Parse responses & apply verdicts

    Args:
        final_keywords: Output from Python pipeline
        merge_logs: List of (pass_name, log) from each pass
        product: Product name
        api_key: Anthropic API key
        threshold: Risk score cutoff
        max_pairs: Max total pairs to review
        verbose: Print progress

    Returns:
        Updated keyword list
    """
    MAX_ITEMS_PER_BATCH = 100  # Keep batches small so Haiku can return valid JSON
    MAX_TOKENS_PER_BATCH = 15000  # Stay well under Haiku's context window

    if verbose:
        print(f"\n  {'='*50}")
        print(f"  AI VALIDATION LAYER")
        print(f"  {'='*50}")

    # Step 1: Find risky merges
    risky = identify_risky_merges(merge_logs, product, threshold)
    if verbose:
        total_merges = sum(len(log) for _, log in merge_logs)
        print(f"\n  Total merge decisions: {total_merges}")
        print(f"  Flagged as risky (score ≥ {threshold}): {len(risky)}")

    if not risky:
        if verbose:
            print(f"  No risky merges found — Python pipeline is confident.")
        return final_keywords

    # Cap at max_pairs
    risky = risky[:max_pairs]

    # Step 2: Split into batches based on estimated token count
    batches = []
    current_batch = []
    current_est_tokens = 500  # Base prompt overhead

    for item in risky:
        _, canonical, group, _, _ = item
        # Estimate tokens for this merge group
        item_tokens = (len(canonical) + sum(len(q) for q in group)) // 4 + 20
        batch_too_big = (current_est_tokens + item_tokens > MAX_TOKENS_PER_BATCH
                         or len(current_batch) >= MAX_ITEMS_PER_BATCH)
        if batch_too_big and current_batch:
            batches.append(current_batch)
            current_batch = [item]
            current_est_tokens = 500 + item_tokens
        else:
            current_batch.append(item)
            current_est_tokens += item_tokens

    if current_batch:
        batches.append(current_batch)

    if verbose:
        print(f"  Reviewing {len(risky)} merge pairs in {len(batches)} batch(es)")

    # Step 3: Process each batch
    all_verdicts = {}
    total_cost = 0.0
    total_time = 0.0
    global_id_offset = 0

    for batch_idx, batch in enumerate(batches):
        # Build prompt for this batch (renumber IDs starting from 1)
        prompt, pair_count = build_ai_review_prompt(batch, product, len(batch))

        est_input_tokens = estimate_prompt_tokens(prompt)
        est_output_tokens = pair_count * 30
        # Haiku 4.5 pricing: $1/M input, $5/M output
        batch_cost = (est_input_tokens * 1.0 + est_output_tokens * 5.0) / 1_000_000
        total_cost += batch_cost

        if verbose:
            print(f"\n  Batch {batch_idx + 1}/{len(batches)}: "
                  f"{pair_count} pairs, ~{est_input_tokens:,} input tokens, "
                  f"est ~${batch_cost:.3f}")

        start_time = time.time()
        try:
            # Each verdict is ~40-50 tokens. Give generous room.
            max_out = max(8192, pair_count * 50)
            response_text = call_anthropic_api(prompt, api_key, max_tokens=min(max_out, 16384))
        except Exception as e:
            print(f"\n  ERROR: API call failed for batch {batch_idx + 1}: {e}")
            print(f"  Skipping this batch. Remaining merges kept as-is.")
            global_id_offset += len(batch)
            continue

        elapsed = time.time() - start_time
        total_time += elapsed

        if verbose:
            print(f"  Response received in {elapsed:.1f}s")

        # Parse and remap local batch IDs → global IDs
        # Local IDs are 1-indexed within each batch prompt
        # Global IDs are 1-indexed across the full risky list
        batch_verdicts = parse_ai_response(response_text, verbose=verbose)
        for local_id, verdict in batch_verdicts.items():
            # local_id is 1-based within this batch
            global_id = local_id + global_id_offset
            all_verdicts[global_id] = verdict

        if verbose:
            print(f"  Parsed {len(batch_verdicts)} verdicts")

        global_id_offset += len(batch)

        # Small delay between batches to be polite to the API
        if batch_idx < len(batches) - 1:
            time.sleep(1)

    if verbose:
        print(f"\n  Total API time: {total_time:.1f}s")
        print(f"  Total estimated cost: ~${total_cost:.3f}")

    if not all_verdicts:
        print(f"  WARNING: No verdicts parsed. Keeping Python-only output.")
        return final_keywords

    # Step 4: Apply all verdicts
    # Need to renumber risky merges to match global IDs
    renumbered_risky = risky  # Already in order, verdicts use 1-indexed per batch
    updated = apply_ai_verdicts(final_keywords, renumbered_risky, all_verdicts, verbose)

    return updated


def generate_review_file(merge_logs, product, output_path, threshold=0.25, max_pairs=400):
    """
    Alternative to API call: generate a review file that a human can inspect,
    or that can be pasted into Claude chat for manual review.

    This is the FREE option — no API key needed.
    """
    risky = identify_risky_merges(merge_logs, product, threshold)

    prompt, pair_count = build_ai_review_prompt(risky, product, max_pairs)

    with open(output_path, 'w', encoding='utf-8') as f:
        f.write("=" * 70 + "\n")
        f.write("AI REVIEW FILE — Risky Merge Decisions\n")
        f.write("=" * 70 + "\n\n")
        f.write(f"Product: {product}\n")
        f.write(f"Total risky merges: {len(risky)}\n")
        f.write(f"Included in review: {pair_count}\n")
        f.write(f"Risk threshold: {threshold}\n\n")
        f.write("INSTRUCTIONS:\n")
        f.write("  Option A: Copy the prompt below into Claude.ai chat (free)\n")
        f.write("  Option B: Re-run the pipeline with --ai-review and ANTHROPIC_API_KEY\n\n")
        f.write("=" * 70 + "\n")
        f.write("PROMPT TO PASTE INTO CLAUDE:\n")
        f.write("=" * 70 + "\n\n")
        f.write(prompt)

    total_merges = sum(len(log) for _, log in merge_logs)
    print(f"\n  Review file generated: {output_path}")
    print(f"  Total merges: {total_merges}")
    print(f"  Flagged as risky: {len(risky)}")
    print(f"  Included in review: {pair_count}")
    print(f"\n  You can paste the prompt into Claude.ai for free review,")
    print(f"  or re-run with --ai-review and ANTHROPIC_API_KEY for automated review.")

    return risky


# ============================================================================
# SECTION 10: AI DEDUP PASS (Haiku + Sonnet)
# ============================================================================
#
# After the Python pipeline reduces the list (e.g. 6,600 → 2,700), this layer
# runs cheap AI calls to catch semantic duplicates that rules can't handle.
#
# Approach:
#   1. Sort alphabetically (near-duplicates become neighbors)
#   2. Chunk into ~400-keyword batches → Haiku (fast, cheap)
#   3. Optional: single Sonnet call with extended thinking (thorough)
#
# Cost: ~$0.10-0.25 total for a 2,500-keyword list
# Time: ~15-30 seconds

# -- System prompt for AI dedup --
AI_DEDUP_SYSTEM_PROMPT = """You are an expert SEO content strategist cleaning "how to" search query datasets for tutorial content creation.

## Task
You will receive a batch of search queries for a specific software product, sorted alphabetically. Near-duplicates will be close together. Your job is to identify queries to REMOVE.

## What to REMOVE:
1. Semantic duplicates (same intent, different wording):
   - "add" = "install" = "setup" = "enable" for plugins/tools -> keep ONE
   - "change" = "edit" = "modify" = "update" = "customize" -> keep ONE
   - "remove" = "delete" = "uninstall" -> keep ONE
   - "hide" = "disable" = "turn off" = "deactivate" -> keep ONE
   - "show" = "display" = "enable" = "turn on" -> keep ONE
   - "fix" = "solve" = "troubleshoot" = "resolve" -> keep ONE
   - "make" = "create" = "build" = "set up" -> keep ONE
   - "check" = "find" = "see" = "view" = "determine" = "know" -> keep ONE
   - "improve" = "optimize" = "boost" = "speed up" -> keep ONE
   - "site" = "website" = "blog" (same context) -> keep ONE
   - "front page" = "homepage" -> keep ONE
   - "image" = "picture" = "photo" (same context) -> keep ONE
2. Too broad/vague queries ("how to use [product]")
3. Business/career queries ("how to sell X", "how to make money with X")
4. Non-software topics
5. NSFW/unethical content

## What to KEEP:
- Genuinely different intents (add != remove, show != hide)
- Different objects even if verbs match
- Platform-specific variations when processes genuinely differ
- Integration queries with different tools

## Output Format
Return ONLY a JSON object:
{"remove": ["query1", "query2", ...]}

List ONLY queries to REMOVE. Everything not listed is kept.
Be precise — every query you list will be deleted.
Return ONLY the JSON, nothing else."""


def ai_call_api(system_prompt, user_message, api_key, model, label="",
                max_tokens=None, use_thinking=False, budget_tokens=None):
    """Call Claude API for AI dedup pass. Returns (text, usage_dict) or (None, None)."""

    _max = max_tokens or AI_MAX_TOKENS

    payload = {
        "model": model,
        "max_tokens": _max,
        "messages": [{"role": "user", "content": user_message}],
        "system": system_prompt,
    }

    if use_thinking and budget_tokens:
        payload["thinking"] = {"type": "enabled", "budget_tokens": budget_tokens}

    headers = {
        "Content-Type": "application/json",
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
    }

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=data, headers=headers, method="POST"
    )

    ctx = ssl.create_default_context()
    start = time.time()

    try:
        with urllib.request.urlopen(req, context=ctx, timeout=300) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err = ""
        try:
            err = e.read().decode("utf-8")
        except:
            pass
        print(f"    ERROR [{label}]: HTTP {e.code}: {err[:500]}")
        return None, None
    except Exception as e:
        print(f"    ERROR [{label}]: {e}")
        return None, None

    elapsed = time.time() - start

    # Extract text (skip thinking blocks)
    text = "\n".join(b["text"] for b in body.get("content", []) if b.get("type") == "text")

    usage = body.get("usage", {})
    inp = usage.get("input_tokens", 0)
    out = usage.get("output_tokens", 0)

    # Pricing
    if "haiku" in model:
        cost = (inp * 1 / 1_000_000) + (out * 5 / 1_000_000)
    else:
        cost = (inp * 3 / 1_000_000) + (out * 15 / 1_000_000)

    print(f"    [{label}] {elapsed:.1f}s | {inp}in/{out}out | ${cost:.3f}")
    return text, {"input": inp, "output": out, "cost": cost}


def ai_parse_removals(text):
    """Extract the remove list from AI dedup response."""
    if not text:
        return []

    # Strategy 1: Find {"remove": [...]}
    try:
        match = re.search(r'\{[\s\S]*?"remove"[\s\S]*?\}', text)
        if match:
            data = json.loads(match.group())
            return data.get("remove", [])
    except json.JSONDecodeError:
        pass

    # Strategy 2: Find any JSON array
    try:
        match = re.search(r'\[[\s\S]*?\]', text)
        if match:
            arr = json.loads(match.group())
            if all(isinstance(x, str) for x in arr):
                return arr
    except json.JSONDecodeError:
        pass

    # Strategy 3: Line-by-line extraction
    removals = []
    for line in text.split("\n"):
        line = line.strip().strip(",").strip('"').strip("'")
        if line.startswith("how to "):
            removals.append(line)

    if removals:
        return removals

    print(f"    WARNING: Could not parse AI dedup response. Preview: {text[:300]}")
    return []


def run_haiku_pass(keywords, product, api_key, chunk_size=AI_CHUNK_SIZE):
    """Run Haiku on alphabetically-sorted chunks. Returns set of removals."""

    # Sort alphabetically — near-duplicates become neighbors
    sorted_kws = sorted(keywords, key=lambda x: x.lower())

    total_chunks = (len(sorted_kws) + chunk_size - 1) // chunk_size
    print(f"\n  HAIKU PASS: {len(sorted_kws)} keywords in {total_chunks} chunks of ~{chunk_size}")

    all_removals = set()

    for i in range(0, len(sorted_kws), chunk_size):
        chunk_num = i // chunk_size + 1
        chunk = sorted_kws[i:i + chunk_size]

        user_msg = f"""Product: {product}

Here are {len(chunk)} "how to" search queries (sorted alphabetically). Identify which ones to REMOVE.

QUERIES:
{chr(10).join(chunk)}"""

        text, usage = ai_call_api(
            AI_DEDUP_SYSTEM_PROMPT, user_msg, api_key,
            model=AI_HAIKU_MODEL,
            label=f"Haiku {chunk_num}/{total_chunks} ({len(chunk)} kws)",
            max_tokens=AI_MAX_TOKENS,
        )

        removals = ai_parse_removals(text)
        for r in removals:
            all_removals.add(r.lower().strip())

        print(f"    Chunk {chunk_num}: removing {len(removals)}")

        if chunk_num < total_chunks:
            time.sleep(0.5)

    return all_removals


def run_sonnet_pass(keywords, product, api_key):
    """Run Sonnet with extended thinking on the full list. Returns set of removals."""

    print(f"\n  SONNET PASS: {len(keywords)} keywords (single call with extended thinking)")

    user_msg = f"""Product: {product}

Here are {len(keywords)} "how to" search queries that have already been through Python dedup and a Haiku AI pass. This is the FINAL quality check. Be thorough — catch any remaining duplicates across the entire list.

Look especially for:
1. Verb synonyms the first pass missed (add/install, change/edit, etc.)
2. Object synonyms (admin panel/dashboard, site/website, etc.)
3. Queries that are too similar in intent even if worded very differently
4. Any remaining junk (too broad, business queries, non-tutorial)

QUERIES:
{chr(10).join(keywords)}"""

    text, usage = ai_call_api(
        AI_DEDUP_SYSTEM_PROMPT, user_msg, api_key,
        model=AI_SONNET_MODEL,
        label="Sonnet Final",
        max_tokens=AI_SONNET_MAX_TOKENS,
        use_thinking=True,
        budget_tokens=AI_SONNET_BUDGET,
    )

    removals = ai_parse_removals(text)
    return set(r.lower().strip() for r in removals)


def run_ai_dedup_pass(keywords, product, api_key, chunk_size=AI_CHUNK_SIZE,
                      sonnet_pass=False, sonnet_threshold=1500, verbose=True):
    """
    Full AI dedup workflow: Haiku chunks → optional Sonnet polish.

    Args:
        keywords: List of deduplicated keywords from Python pipeline
        product: Product name
        api_key: Anthropic API key
        chunk_size: Keywords per Haiku chunk (default 400)
        sonnet_pass: If True, run Sonnet after Haiku
        sonnet_threshold: Only run Sonnet if keywords > this after Haiku
        verbose: Print progress

    Returns:
        list: Final deduplicated keyword list
    """
    if verbose:
        print(f"\n  {'='*50}")
        print(f"  AI DEDUP PASS")
        print(f"  {'='*50}")
        mode = "Haiku + Sonnet" if sonnet_pass else "Haiku only"
        print(f"  Input:    {len(keywords):,} keywords")
        print(f"  Mode:     {mode}")

    current = keywords[:]

    # --- HAIKU PASS ---
    haiku_removals = run_haiku_pass(current, product, api_key, chunk_size)

    before = len(current)
    current = [kw for kw in current if kw.lower().strip() not in haiku_removals]
    removed = before - len(current)
    if verbose:
        print(f"\n  Haiku result: {before:,} -> {len(current):,} (-{removed:,}, {removed/before*100:.1f}%)")

    # --- SONNET PASS ---
    run_sonnet = False
    if sonnet_pass and len(current) > sonnet_threshold:
        run_sonnet = True
    elif sonnet_pass and len(current) <= sonnet_threshold:
        if verbose:
            print(f"\n  Sonnet pass: Skipped ({len(current):,} keywords, under {sonnet_threshold:,} threshold)")

    if run_sonnet:
        # If list is still large, do Sonnet in 2 halves
        if len(current) > 2000:
            if verbose:
                print(f"  (List still large at {len(current):,}, running Sonnet in 2 halves)")
            sorted_current = sorted(current, key=lambda x: x.lower())
            mid = len(sorted_current) // 2

            s_removals_a = run_sonnet_pass(sorted_current[:mid], product, api_key)
            time.sleep(2)
            s_removals_b = run_sonnet_pass(sorted_current[mid:], product, api_key)

            sonnet_removals = s_removals_a | s_removals_b
        else:
            sonnet_removals = run_sonnet_pass(current, product, api_key)

        before = len(current)
        current = [kw for kw in current if kw.lower().strip() not in sonnet_removals]
        removed = before - len(current)
        if verbose:
            print(f"\n  Sonnet result: {before:,} -> {len(current):,} (-{removed:,})")

    # Final dedup (case-insensitive)
    seen = set()
    final = []
    for kw in current:
        kw_l = kw.strip().lower()
        if kw_l not in seen:
            seen.add(kw_l)
            final.append(kw.strip())

    final.sort(key=lambda x: x.lower())

    if verbose:
        total_removed = len(keywords) - len(final)
        print(f"\n  {'='*50}")
        print(f"  AI DEDUP: {len(keywords):,} -> {len(final):,} (-{total_removed:,}, {total_removed/len(keywords)*100:.1f}%)")
        print(f"  {'='*50}")

    return final


# ============================================================================
# SECTION 11: CLI ENTRY POINT
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Deduplicate 'how to' search query keyword lists",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Basic (Python only — free, instant):
  python keyword_dedup_pipeline.py -i raw.csv -p "google slides"

  # Python + AI dedup (Haiku — fast, ~$0.10):
  ANTHROPIC_API_KEY=sk-... python keyword_dedup_pipeline.py \\
      -i raw.csv -p "wordpress" --ai-pass

  # Python + Haiku + Sonnet final pass (~$0.25):
  ANTHROPIC_API_KEY=sk-... python keyword_dedup_pipeline.py \\
      -i raw.csv -p "wordpress" --ai-pass --sonnet-pass

  # With extra product name variants:
  ANTHROPIC_API_KEY=sk-... python keyword_dedup_pipeline.py \\
      -i raw.csv -p "meta" --extra-products "facebook ads,instagram ads" --ai-pass

  # AI merge validation (catches bad Python merges):
  ANTHROPIC_API_KEY=sk-... python keyword_dedup_pipeline.py \\
      -i raw.csv -p "powerpoint" --ai-review

  # Generate review file (free — paste into Claude.ai):
  python keyword_dedup_pipeline.py -i raw.csv -p "shopify" --review-file
        """
    )
    parser.add_argument('--input', '-i', required=True,
                        help='Input CSV file (one keyword per line)')
    parser.add_argument('--product', '-p', required=True,
                        help='Product name (e.g., "google slides")')
    parser.add_argument('--output', '-o',
                        help='Output CSV file (default: [input]_DEDUPED.csv)')
    parser.add_argument('--extra-products',
                        help='Comma-separated extra product name variants')
    parser.add_argument('--quiet', '-q', action='store_true',
                        help='Suppress progress output')

    # AI Dedup Pass (the main AI layer — Haiku + optional Sonnet)
    ai_dedup = parser.add_argument_group('AI Dedup Pass',
        'Semantic dedup via Claude AI after Python pipeline (~$0.10-0.25)')
    ai_dedup.add_argument('--ai-pass', action='store_true',
                          help='Run AI dedup pass after Python pipeline (needs API key)')
    ai_dedup.add_argument('--sonnet-pass', action='store_true',
                          help='Also run Sonnet final pass after Haiku (more thorough)')
    ai_dedup.add_argument('--chunk-size', type=int, default=AI_CHUNK_SIZE,
                          help=f'Keywords per Haiku chunk (default: {AI_CHUNK_SIZE})')
    ai_dedup.add_argument('--sonnet-threshold', type=int, default=1500,
                          help='Only run Sonnet if keywords > this after Haiku (default: 1500)')

    # AI Review (the old merge-validation layer)
    ai_review = parser.add_argument_group('AI Review',
        'Optional AI validation to catch bad Python merges')
    ai_review.add_argument('--ai-review', action='store_true',
                          help='Run AI validation on risky merge decisions (needs API key)')
    ai_review.add_argument('--review-file', action='store_true',
                          help='Generate a review file for manual inspection (free, no API)')
    ai_review.add_argument('--risk-threshold', type=float, default=0.25,
                          help='Risk score cutoff for AI review (default: 0.25)')

    # Shared
    parser.add_argument('--api-key',
                        help='Anthropic API key (or set ANTHROPIC_API_KEY env var)')

    args = parser.parse_args()

    extra = None
    if args.extra_products:
        extra = [p.strip() for p in args.extra_products.split(',')]

    run_pipeline(
        input_file=args.input,
        product=args.product,
        output_file=args.output,
        extra_products=extra,
        verbose=not args.quiet,
        ai_review=args.ai_review,
        api_key=args.api_key,
        review_file=args.review_file,
        risk_threshold=args.risk_threshold,
        ai_pass=args.ai_pass,
        sonnet_pass=args.sonnet_pass,
        chunk_size=args.chunk_size,
        sonnet_threshold=args.sonnet_threshold,
    )


if __name__ == "__main__":
    main()
