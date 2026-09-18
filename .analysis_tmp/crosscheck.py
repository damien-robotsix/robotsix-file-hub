import glob, json, os, sys
import tomllib

from packaging.version import Version, InvalidVersion

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # repo root

# 1. parse uv.lock -> {(name_lower): set(versions)}
with open(os.path.join(REPO, "uv.lock"), "rb") as f:
    lock = tomllib.load(f)

locked = {}
for pkg in lock.get("package", []):
    name = pkg.get("name")
    ver = pkg.get("version")
    if name and ver:
        locked.setdefault(name.lower(), set()).add(ver)

# normalize OSV package name (case, - vs _)
def norm(s):
    return s.lower().replace("_", "-")

# aliases: our lock may name package differently than OSV (e.g. python-docx -> docx handled by deptry map)
# OSV PyPI uses the canonical distribution name. Map some known.
alias = {
    "pydantic-ai-slim": "pydantic-ai",
    "python-docx": "docx",
    "openai": "openai",
}

def event_covers(events, ver):
    """Return True if version ver is in the affected range per OSV events."""
    introduced = None
    fixed = None
    last_affected = None
    for ev in events:
        if "introduced" in ev:
            introduced = Version(ev["introduced"]) if ev["introduced"] != "0" else Version("0")
        if "fixed" in ev:
            fixed = Version(ev["fixed"])
        if "last_affected" in ev:
            last_affected = Version(ev["last_affected"])
        if "limit" in ev:
            # handled below
            pass
    # A version is vulnerable if >= introduced and (not fixed or < fixed)
    if last_affected is not None and fixed is None:
        return ver >= (introduced or Version("0")) and ver <= last_affected
    lo = introduced or Version("0")
    hi = fixed  # exclusive
    if hi is None:
        return ver >= lo and last_affected is not None and ver <= last_affected
    return ver >= lo and ver < hi

def in_ranges(ranges, ver):
    for r in ranges:
        if r.get("type") != "SEMVER":
            # try anyway by events
            pass
        if event_covers(r.get("events", []), ver):
            return True
    return False

matches = []
json_files = glob.glob(os.path.join(REPO, ".analysis_tmp", "ghsa", "advisories", "**", "*.json"), recursive=True)
print("advisories scanned:", len(json_files))
name_hits = {}  # locked package name -> count of advisories mentioning it (any version)
all_pypi = 0
for jf in json_files:
    try:
        with open(jf) as f:
            adv = json.load(f)
    except Exception:
        continue
    for aff in adv.get("affected", []):
        pkg = aff.get("package", {})
        eco = pkg.get("ecosystem")
        if eco != "PyPI":
            continue
        all_pypi += 1
        osv_name = norm(pkg.get("name", ""))
        # find matching locked name
        locked_names = [n for n in locked if n == osv_name or alias.get(n) == osv_name or osv_name == alias.get(n)]
        if not locked_names:
            continue
        for n in locked_names:
            name_hits.setdefault(n, 0)
            name_hits[n] += 1
            for ver in locked[n]:
                try:
                    v = Version(ver)
                except InvalidVersion:
                    continue
                if in_ranges(aff.get("ranges", []), v):
                    fixed = None
                    for r in aff.get("ranges", []):
                        for ev in r.get("events", []):
                            if "fixed" in ev:
                                fixed = ev["fixed"]
                    matches.append({
                        "ghsa": adv.get("id"),
                        "alias": adv.get("aliases", []),
                        "summary": (adv.get("summary") or "")[:160],
                        "severity": [s.get("score") for s in adv.get("database_specific", {}).get("severity", []) if "score" in s],
                        "package": osv_name,
                        "locked_version": ver,
                        "fixed": fixed,
                    })
                    break  # one match per package/version/adv
            break  # first matching locked name

# dedupe
seen = set()
uniq = []
for m in matches:
    k = (m["ghsa"], m["package"], m["locked_version"])
    if k in seen:
        continue
    seen.add(k)
    uniq.append(m)

print("PyPI advisories seen:", all_pypi)
print("name-level hits per locked package:")
for n, c in sorted(name_hits.items()):
    print(f"  {n}: {c}")
print("MATCHES:", len(uniq))
for m in sorted(uniq, key=lambda x: (x["package"], x["locked_version"])):
    print(f"- {m['ghsa']} {m['alias']} | {m['package']} {m['locked_version']} -> fixed={m['fixed']} | sev={m['severity']} | {m['summary']}")
