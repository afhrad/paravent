"""Extract every {@tag ...} occurrence from the 5etools JSON corpus.

    python data/library/tags/scan_tags.py /path/to/5etools-data

Without an argument the corpus is the one ``PARAVENT_REFERENCE_DIR`` names. The result,
``tags.json`` in the current folder, is the raw material of ``5etools_tags.json``.

The examples it keeps are short on purpose (``MAX_EXAMPLE_WORDS``, ``MAX_EXAMPLE_CHARS``):
the catalogue shows how a tag is written, not what the books say. It is committed and
public, so no sentence of a rulebook belongs in it; an example of a free-text tag
(``{@note …}``, ``{@comic …}``) is better written in our own words.
"""

import collections
import json
import os
import re
import sys
from pathlib import Path

if len(sys.argv) > 1:
    ROOT = Path(sys.argv[1])
elif os.environ.get("PARAVENT_REFERENCE_DIR"):
    ROOT = Path(os.environ["PARAVENT_REFERENCE_DIR"])
else:
    sys.exit("usage: scan_tags.py /path/to/5etools-data (or set PARAVENT_REFERENCE_DIR)")

# An example longer than this is prose from a book, not an illustration of the syntax.
MAX_EXAMPLE_WORDS = 8
MAX_EXAMPLE_CHARS = 70
WORD_RE = re.compile(r"[A-Za-z0-9']+")


def short_enough(snip: str) -> bool:
    """Whether a tag is brief enough to stand as an example in a public file."""
    payload = re.sub(r"\{@\w+ ?", "", snip).replace("|", " ")
    return len(snip) <= MAX_EXAMPLE_CHARS and len(WORD_RE.findall(payload)) <= MAX_EXAMPLE_WORDS


# 5etools tags are {@name payload} and CAN nest: {@damage {@dice 1d6}}.
# Match the tag NAME only - that is what we are cataloguing.
TAG_RE = re.compile(r"\{@([a-zA-Z0-9_]+)(?=[\s}])")

counts = collections.Counter()
examples = collections.defaultdict(list)
per_file = collections.Counter()
files_seen = 0
bytes_seen = 0

for path in sorted(ROOT.rglob("*.json")):
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        print(f"SKIP {path.name}: {e}", file=sys.stderr)
        continue
    files_seen += 1
    bytes_seen += len(raw)
    found = TAG_RE.findall(raw)
    if not found:
        continue
    per_file[str(path.relative_to(ROOT))] = len(found)
    for name in found:
        counts[name] += 1
    # capture a few full examples per tag for the ones we have not filled yet
    for m in re.finditer(r"\{@([a-zA-Z0-9_]+)[ }]", raw):
        nm = m.group(1)
        if len(examples[nm]) >= 3:
            continue
        # walk forward balancing braces to get the whole tag
        i = m.start()
        depth = 0
        end = None
        for j in range(i, min(i + 400, len(raw))):
            if raw[j] == "{":
                depth += 1
            elif raw[j] == "}":
                depth -= 1
                if depth == 0:
                    end = j + 1
                    break
        if end:
            snip = raw[i:end]
            if snip not in examples[nm] and short_enough(snip):
                examples[nm].append(snip)

out = {
    "files_scanned": files_seen,
    "bytes_scanned": bytes_seen,
    "distinct_tags": len(counts),
    "total_occurrences": sum(counts.values()),
    "counts": dict(counts.most_common()),
    "examples": {k: examples[k] for k in counts},
}
Path("tags.json").write_text(json.dumps(out, indent=1, ensure_ascii=False))
print(
    f"files={files_seen}  bytes={bytes_seen:,}  distinct_tags={len(counts)}  occurrences={sum(counts.values()):,}"
)
