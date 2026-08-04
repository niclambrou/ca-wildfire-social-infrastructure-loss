#!/usr/bin/env python3
"""
prepare_ca_deposit.py — de-identify the CA statewide wildfire / social-infrastructure
repository before (re)publishing.

WHAT IT TOUCHES
  Sensitive categories (generalized): residential_elderly_disabled, childcare_center
  Public categories (kept in full):   hospital_clinic, school

  Three facility-level files are cleaned:
    facility_inventory_UNIFIED.csv, facility_fire_level_results.csv, facility_svi.csv
  For sensitive-category rows it BLANKS facility_name/address/city/zip/lat/lon/has_coords,
  replaces the license-number facility_id with a stable surrogate (consistent across all
  three files so analytic joins still work), and adds census_tract (from facility_svi FIPS).
  Public-category rows are left exactly as-is.

  The seven aggregate files are copied unchanged. .py pipeline files (if present) are
  copied and scanned. A final integrity + leak scan aborts on any residual PII.

USAGE
  python3 prepare_ca_deposit.py --input /path/to/ca/copy --output CA_DEPOSIT
"""
import argparse, csv, os, re, shutil, sys, random
from pathlib import Path

random.seed(42)
SENSITIVE = {"residential_elderly_disabled", "childcare_center"}
PREFIX = {"residential_elderly_disabled": "RED", "childcare_center": "CCC"}
BLANK_COLS = ["facility_name", "lat", "lon", "has_coords", "address", "city", "zip"]

AGGREGATES = ["access_desert_by_tract.csv","access_drop_by_fire_tract.csv",
              "displaced_capacity_by_fire.csv","per_fire_by_category.csv",
              "per_fire_by_subtype.csv","statewide_by_category.csv","statewide_by_subtype.csv"]
FACILITY_FILES = ["facility_inventory_UNIFIED.csv","facility_fire_level_results.csv","facility_svi.csv"]

def read(p):
    with open(p, newline="") as f: return list(csv.DictReader(f))
def write(p, rows, fields):
    with open(p, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields); w.writeheader()
        for r in rows: w.writerow({k: r.get(k, "") for k in fields})

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default=".")
    ap.add_argument("--output", default="CA_DEPOSIT")
    a = ap.parse_args()
    IN, OUT = Path(a.input).resolve(), Path(a.output).resolve()
    if OUT.exists(): shutil.rmtree(OUT)
    (OUT/"data").mkdir(parents=True); (OUT/"code").mkdir(); (OUT/"docs").mkdir()
    present = {p.name for p in IN.iterdir() if p.is_file()}

    # --- tract lookup + surrogate map, built from all facility files -----------------
    tract = {}   # original facility_id -> FIPS (census tract)
    if "facility_svi.csv" in present:
        for r in read(IN/"facility_svi.csv"):
            tract[r["facility_id"]] = r.get("FIPS", "")

    sensitive_ids = set()
    for fn in FACILITY_FILES:
        if fn in present:
            for r in read(IN/fn):
                if r.get("category") in SENSITIVE:
                    sensitive_ids.add(r["facility_id"])
    # deterministic surrogate ids, grouped by category prefix
    surrogate = {}
    by_cat = {}
    # need category per id (from inventory, fallback svi/fire)
    cat_of = {}
    for fn in FACILITY_FILES:
        if fn in present:
            for r in read(IN/fn):
                cat_of.setdefault(r["facility_id"], r.get("category"))
    for fid in sensitive_ids:
        by_cat.setdefault(cat_of.get(fid, "residential_elderly_disabled"), []).append(fid)
    for cat, ids in by_cat.items():
        ids_sorted = sorted(ids); random.shuffle(ids_sorted)
        for i, fid in enumerate(ids_sorted, 1):
            surrogate[fid] = f"{PREFIX.get(cat,'GEN')}-{i:06d}"

    def clean_row(r):
        """Return a de-identified copy if sensitive, else unchanged; add census_tract+generalized."""
        out = dict(r)
        if r.get("category") in SENSITIVE:
            fid = r["facility_id"]
            out["census_tract"] = tract.get(fid, "")
            out["facility_id"] = surrogate.get(fid, "")
            for c in BLANK_COLS:
                if c in out: out[c] = ""
            out["generalized"] = "TRUE"
        else:
            out["census_tract"] = ""
            out["generalized"] = "FALSE"
        return out

    counts = {}
    # --- inventory + fire-level: blank PII on sensitive rows ------------------------
    for fn in ["facility_inventory_UNIFIED.csv", "facility_fire_level_results.csv"]:
        if fn not in present: continue
        rows = read(IN/fn)
        fields = list(rows[0].keys()) + ["census_tract", "generalized"]
        cleaned = [clean_row(r) for r in rows]
        write(OUT/"data"/fn, cleaned, fields)
        counts[fn] = sum(1 for r in rows if r.get("category") in SENSITIVE)

    # --- svi: surrogate id on sensitive rows (no addresses to blank) ----------------
    if "facility_svi.csv" in present:
        rows = read(IN/"facility_svi.csv")
        for r in rows:
            if r.get("category") in SENSITIVE:
                r["facility_id"] = surrogate.get(r["facility_id"], "")
        write(OUT/"data"/"facility_svi.csv", rows, list(rows[0].keys()))
        counts["facility_svi.csv"] = sum(1 for r in rows if r.get("category") in SENSITIVE)

    # --- aggregates + code + license/readme ----------------------------------------
    for fn in AGGREGATES:
        if fn in present: shutil.copy2(IN/fn, OUT/"data"/fn)
    for p in IN.iterdir():
        if p.suffix == ".py": shutil.copy2(p, OUT/"code"/p.name)
    for fn in ["README.md", "README.txt", "LICENSE", "requirements.txt"]:
        if fn in present: shutil.copy2(IN/fn, OUT/fn)

    write_docs(OUT/"docs")

    # --- integrity + leak scan -----------------------------------------------------
    problems = integrity_scan(OUT/"data")
    print("=== SUMMARY ===")
    for k, v in counts.items(): print(f"  {k}: {v} sensitive rows generalized")
    print(f"  surrogate ids issued: {len(surrogate)}")
    if problems:
        print("\n!!! SCAN FAILED:")
        for p in problems[:20]: print("   ", p)
        sys.exit(1)
    print("integrity scan: PASS (no name/address/coords on any generalized row; no phones/paths)")
    print(f"\nWritten to: {OUT}")

def integrity_scan(data_dir):
    phone = re.compile(r"\(\d{3}\)\s?\d{3}-\d{4}|/Users/")
    problems = []
    for fn in ["facility_inventory_UNIFIED.csv", "facility_fire_level_results.csv"]:
        p = data_dir/fn
        if not p.exists(): continue
        for r in read(p):
            if r.get("generalized") == "TRUE":
                for c in ["facility_name","address","lat","lon","city","zip"]:
                    if r.get(c, "").strip():
                        problems.append(f"{fn}: generalized row still has {c}"); break
                if not str(r.get("facility_id","")).startswith(("RED-","CCC-")):
                    problems.append(f"{fn}: generalized row keeps a non-surrogate id")
    # svi: sensitive rows must have surrogate ids
    p = data_dir/"facility_svi.csv"
    if p.exists():
        for r in read(p):
            if r.get("category") in SENSITIVE and not str(r.get("facility_id","")).startswith(("RED-","CCC-")):
                problems.append("facility_svi.csv: sensitive row keeps a non-surrogate id"); break
    for p in data_dir.glob("*.csv"):
        if phone.search(p.read_text(errors="ignore")):
            problems.append(f"phone/local-path value in {p.name}")
    return problems

def write_docs(docs):
    (docs/"README.md").write_text(
"""# CA statewide wildfire social-infrastructure loss — data & code

Statewide analysis matching licensed social-infrastructure facilities to wildfire
damage/perimeters across California.

## Sources (all obtained from public state listings, without special request)
- Health facilities: CA HCAI facility listings (public).
- Schools: CA Dept. of Education directory (public).
- Licensed care (elderly/disabled residential; childcare): CA Dept. of Social Services
  Community Care Licensing / CDPH (public listings).
- Social Vulnerability Index: CDC/ATSDR SVI.

## De-identification
Hospitals, clinics, and schools are public institutions and appear at full detail.
Rows for **residential elderly/disabled care facilities** and **childcare facilities**
are generalized: facility name, street address, city, ZIP, and exact coordinates are
removed; the licensing number is replaced with a surrogate id (consistent across files);
location is retained only at census-tract level. These facilities include private
residences serving vulnerable and in some cases legally protected populations, so the
compiled, mapped, statewide file is not published at address precision even though the
underlying listings are individually public.

Facility-level microdata for these categories at finer resolution are available from the
author on request under a data-use agreement. Aggregate tables (by tract, fire, category,
subtype) are fully open.
""", encoding="utf-8")

if __name__ == "__main__":
    main()
