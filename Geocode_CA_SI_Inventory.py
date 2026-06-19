#!/usr/bin/env python
# coding: utf-8

# In[ ]:


"""
California Social-Infrastructure Losses in Wildfires, 2018-2025 - analysis pipeline.

Consolidated from the original notebook. Starting from the assembled, geocoded
facility inventory, it: (1) attributes facility losses to fires via point-in-
perimeter + nearest-DINS matching, (2) tabulates per-fire and statewide losses,
(3) overlays the CDC/ATSDR SVI, and (4) runs the E2SFCA access model with a
per-fire attribution. Geocoding of the DSS files is an UPSTREAM step (see the
separate geocoding script); this pipeline assumes lat/lon/has_coords are present.

Outputs (to OUT_DIR):
    facility_inventory_UNIFIED.csv      full assembled inventory (NEW: now saved)
    facility_fire_level_results.csv     facility x fire status (the figures' input)
    per_fire_by_category.csv / _by_subtype.csv
    statewide_by_category.csv / _by_subtype.csv
    social_infra_fire_losses_RESULTS.xlsx
    displaced_capacity_by_fire.csv      beds/slots/enrollment displaced per fire (NEW)
    facility_svi.csv                    facility x SVI theme percentiles
    access_desert_by_tract.csv (+ .gpkg)  tract access, all-fires
    access_drop_by_fire_tract.csv       tract access drop attributed to each fire

Requires: geopandas, shapely, pyproj, pandas, numpy, scipy (pip install geopandas scipy).

"""

import glob
import warnings
from pathlib import Path

import numpy as np
import pandas as pd
import geopandas as gpd
from scipy.spatial import cKDTree

warnings.filterwarnings("ignore")
pd.set_option("display.max_columns", 40)

# ============================ EDIT PATHS HERE ============================
DATA_DIR = Path("../data")        # searched recursively for the inputs below
OUT_DIR  = Path("../outputs")     # all results written here

# input file patterns (resolved recursively under DATA_DIR)
INVENTORY_GLOB = "category*.csv"                              # assembled inventory parts
DINS_GLOB      = "POSTFIRE_MASTER_DATA_SHARE_*.csv"           # CAL FIRE DINS master
PERIMETER_GLOB = "California_Historic_Fire_Perimeters_*.geojson"
SVI_GLOB       = "*SVI2022*tract.gdb"                         # CDC/ATSDR SVI 2022 geodatabase
# =========================================================================

# ---- constants (preserved from the original) ----
WORKING_CRS = "EPSG:3310"
YEAR_MIN, YEAR_MAX = 2018, 2025
MATCH_BUFFER_M = 100

DAMAGE_BUCKET = {
    "No Damage":         "no_damage",
    "Affected (>0-10%)": "impacted",
    "Minor (10-25%)":    "impacted",
    "Major (25-50%)":    "complete_loss",
    "Destroyed (>50%)":  "complete_loss",
    "Inaccessible":       None,
}
CATEGORIES = ["hospital_clinic", "childcare_center", "school", "residential_elderly_disabled"]

# catchment radius (m) + demand population field per category
CATCHMENT = {
    "hospital_clinic":              dict(d0=50000, pop="E_TOTPOP"),
    "residential_elderly_disabled": dict(d0=30000, pop="E_AGE65"),
    "childcare_center":             dict(d0=8000,  pop="E_AGE17"),
    "school":                       dict(d0=12000, pop="E_AGE17"),
}

# category -> column name in displaced_capacity_by_fire.csv
CAT_TO_CAPCOL = {
    "residential_elderly_disabled": "residential_beds",
    "childcare_center":             "childcare_slots",
    "school":                       "students_enrolled",
    "hospital_clinic":              "hospital_beds",
}

SVI_THEMES = ["RPL_THEMES", "RPL_THEME1", "RPL_THEME2", "RPL_THEME3", "RPL_THEME4"]


def _find(pattern):
    """First file matching pattern anywhere under DATA_DIR."""
    hits = sorted(glob.glob(str(DATA_DIR / "**" / pattern), recursive=True))
    if not hits:
        raise FileNotFoundError(f"Couldn't find {pattern} anywhere under {DATA_DIR}")
    return hits[0]


def gauss(d, d0):
    """Kwan Gaussian decay used by E2SFCA: 1 at d=0, 0 at d=d0."""
    g = (np.exp(-0.5 * (d / d0) ** 2) - np.exp(-0.5)) / (1 - np.exp(-0.5))
    return np.clip(g, 0, None)


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    inventory_parts = sorted(glob.glob(str(DATA_DIR / "**" / INVENTORY_GLOB), recursive=True))
    if not inventory_parts:
        raise FileNotFoundError(f"No {INVENTORY_GLOB} files found under {DATA_DIR}")
    dins_path = _find(DINS_GLOB)
    perimeter_path = _find(PERIMETER_GLOB)
    print(f"inventory parts: {len(inventory_parts)} files")
    print("dins      :", dins_path)
    print("perimeters:", perimeter_path)

    # ---------------------------------------------------------------- Step 1
    # Full assembled inventory; located subset becomes the facility points.
    inv_all = pd.concat(
        [pd.read_csv(p, dtype={"zip": str, "capacity": str}) for p in inventory_parts],
        ignore_index=True,
    )
    inv_all.to_csv(OUT_DIR / "facility_inventory_UNIFIED.csv", index=False)  # NEW: persist the unified inventory

    located = inv_all[inv_all["has_coords"] == True].copy()
    located["first_year"] = pd.to_numeric(located["first_year"], errors="coerce")
    located["last_year"]  = pd.to_numeric(located["last_year"],  errors="coerce")
    fac = gpd.GeoDataFrame(
        located, geometry=gpd.points_from_xy(located.lon, located.lat), crs="EPSG:4326"
    ).to_crs(WORKING_CRS)
    print(f"{len(fac)} located facilities")
    print(fac["category"].value_counts())

    # ---------------------------------------------------------------- Step 2
    # Fire perimeters, 2018-2025.
    perims = gpd.read_file(perimeter_path).to_crs(WORKING_CRS)
    perims["YEAR_"] = pd.to_numeric(perims["YEAR_"], errors="coerce")
    perims = perims[(perims["YEAR_"] >= YEAR_MIN) & (perims["YEAR_"] <= YEAR_MAX)].copy()
    perims["fire_id"] = (perims["FIRE_NAME"].astype(str).str.strip()
                         + "_" + perims["YEAR_"].astype("Int64").astype(str))
    perims = perims[["fire_id", "FIRE_NAME", "YEAR_", "geometry"]].rename(
        columns={"FIRE_NAME": "fire_name", "YEAR_": "fire_year"})
    print(f"{len(perims)} perimeters {YEAR_MIN}-{YEAR_MAX}")

    # ---------------------------------------------------------------- Step 3
    # DINS inspection points -> damage buckets, 2018+.
    din = pd.read_csv(dins_path,
                      usecols=["* Damage", "* Incident Name", "Incident Start Date",
                               "Latitude", "Longitude"],
                      dtype=str, low_memory=False)
    din.columns = ["damage_raw", "incident", "start_date", "lat", "lon"]
    din["dins_year"] = pd.to_datetime(din["start_date"], errors="coerce").dt.year
    din["bucket"]    = din["damage_raw"].str.strip().map(DAMAGE_BUCKET)
    din = din[din["bucket"].notna()]
    din = din[(din["dins_year"] >= YEAR_MIN) & (din["dins_year"] <= YEAR_MAX)]
    din["lat"] = pd.to_numeric(din["lat"], errors="coerce")
    din["lon"] = pd.to_numeric(din["lon"], errors="coerce")
    din = din.dropna(subset=["lat", "lon"])
    dins = gpd.GeoDataFrame(
        din, geometry=gpd.points_from_xy(din.lon, din.lat), crs="EPSG:4326"
    ).to_crs(WORKING_CRS)
    print(f"{len(dins)} DINS points {YEAR_MIN}-{YEAR_MAX}")

    # ---------------------------------------------------------------- Step 4
    # Facilities inside perimeters and active that year = the denominator.
    ff = gpd.sjoin(fac, perims, how="inner", predicate="within").drop(columns="index_right")
    active = ((ff["first_year"].fillna(1900) <= ff["fire_year"])
              & (ff["last_year"].fillna(YEAR_MAX) >= ff["fire_year"]))
    ff = ff[active].copy()
    print(f"{len(ff)} facility-fire pairs; "
          f"{ff['facility_id'].nunique()} facilities across {ff['fire_id'].nunique()} fires")

    # ---------------------------------------------------------------- Step 5
    # Nearest DINS point within MATCH_BUFFER_M; prefer worst damage, then closest.
    near = gpd.sjoin_nearest(fac[["facility_id", "geometry"]],
                             dins[["bucket", "dins_year", "geometry"]],
                             how="left", max_distance=MATCH_BUFFER_M, distance_col="dist_m")
    rank = {"complete_loss": 0, "impacted": 1, "no_damage": 2}
    near["r"] = near["bucket"].map(rank).fillna(3)
    near = near.sort_values(["facility_id", "r", "dist_m"]).drop_duplicates("facility_id", keep="first")
    fac_dmg = near[["facility_id", "bucket", "dins_year", "dist_m"]]

    # ---------------------------------------------------------------- Step 6
    # Status per fire: damaged only if matched DINS year == fire year.
    ff = ff.merge(fac_dmg, on="facility_id", how="left")
    same_fire = ff["dins_year"] == ff["fire_year"]
    ff["status"] = np.where(same_fire & ff["bucket"].notna(), ff["bucket"], "not_matched")
    ff["status"] = ff["status"].fillna("not_matched")
    print(ff["status"].value_counts())

    # ---------------------------------------------------------------- Step 7
    # Per-fire and statewide summaries.
    def summarize(df, group_cols):
        g = (df.assign(n=1)
               .pivot_table(index=group_cols, columns="status", values="n",
                            aggfunc="sum", fill_value=0).reset_index())
        for c in ["complete_loss", "impacted", "no_damage", "not_matched"]:
            if c not in g:
                g[c] = 0
        g["total_in_perimeter"]  = g[["complete_loss", "impacted", "no_damage", "not_matched"]].sum(axis=1)
        g["lost_or_impacted"]    = g["complete_loss"] + g["impacted"]
        g["pct_complete_loss"]   = (100 * g["complete_loss"] / g["total_in_perimeter"]).round(1)
        g["pct_impacted"]        = (100 * g["impacted"] / g["total_in_perimeter"]).round(1)
        g["pct_lost_or_impacted"] = (100 * g["lost_or_impacted"] / g["total_in_perimeter"]).round(1)
        return g

    per_fire_cat = summarize(ff, ["fire_id", "fire_name", "fire_year", "category"])
    per_fire_sub = summarize(ff, ["fire_id", "fire_name", "fire_year", "category", "subtype"])
    keep = ff.groupby("fire_id").apply(
        lambda d: d["status"].isin(["complete_loss", "impacted"]).any())
    keep = set(keep[keep].index)
    per_fire_cat = per_fire_cat[per_fire_cat["fire_id"].isin(keep)].sort_values(
        ["fire_year", "fire_name", "category"])
    per_fire_sub = per_fire_sub[per_fire_sub["fire_id"].isin(keep)]
    print(f"{len(keep)} fires touched at least one social-infrastructure facility")

    # one worst-status row per facility for statewide denominators
    ff_worst = (ff.assign(r=ff["status"].map(
                    {"complete_loss": 0, "impacted": 1, "no_damage": 2, "not_matched": 3}))
                  .sort_values("r").drop_duplicates("facility_id", keep="first")[["facility_id", "status"]])
    base = fac[["facility_id", "category", "subtype"]].merge(ff_worst, on="facility_id", how="left")
    base["status"] = base["status"].fillna("outside_all_perimeters")

    def statewide(df, group_cols):
        g = (df.assign(n=1).pivot_table(index=group_cols, columns="status", values="n",
                                        aggfunc="sum", fill_value=0).reset_index())
        for c in ["complete_loss", "impacted", "no_damage", "not_matched", "outside_all_perimeters"]:
            if c not in g:
                g[c] = 0
        g["total_active"] = g[["complete_loss", "impacted", "no_damage",
                               "not_matched", "outside_all_perimeters"]].sum(axis=1)
        g["in_a_perimeter"]   = g["total_active"] - g["outside_all_perimeters"]
        g["lost_or_impacted"] = g["complete_loss"] + g["impacted"]
        g["pct_of_all_lost_or_impacted"] = (100 * g["lost_or_impacted"] / g["total_active"]).round(2)
        g["pct_of_all_complete_loss"]    = (100 * g["complete_loss"] / g["total_active"]).round(2)
        return g

    statewide_cat = statewide(base, ["category"])
    statewide_sub = statewide(base, ["category", "subtype"])

    # ---------------------------------------------------------------- Step 8
    # Write facility-level results, summaries, and displaced capacity.
    ff.drop(columns="geometry").to_csv(OUT_DIR / "facility_fire_level_results.csv", index=False)
    per_fire_cat.to_csv(OUT_DIR / "per_fire_by_category.csv", index=False)
    per_fire_sub.to_csv(OUT_DIR / "per_fire_by_subtype.csv", index=False)
    statewide_cat.to_csv(OUT_DIR / "statewide_by_category.csv", index=False)
    statewide_sub.to_csv(OUT_DIR / "statewide_by_subtype.csv", index=False)
    with pd.ExcelWriter(OUT_DIR / "social_infra_fire_losses_RESULTS.xlsx") as xl:
        statewide_cat.to_excel(xl, "statewide_by_category", index=False)
        statewide_sub.to_excel(xl, "statewide_by_subtype", index=False)
        per_fire_cat.to_excel(xl, "per_fire_by_category", index=False)
        per_fire_sub.to_excel(xl, "per_fire_by_subtype", index=False)

    # NEW: displaced capacity per fire (beds / slots / enrollment) from the lost/impacted set.
    disp = ff[ff["status"].isin(["complete_loss", "impacted"])].copy()
    disp["cap_num"] = pd.to_numeric(disp["capacity"], errors="coerce")
    dcap = (disp.assign(fire=disp["fire_name"].astype(str).str.strip().str.title())
                .groupby(["fire", "category"])["cap_num"].sum()
                .unstack(fill_value=0)
                .rename(columns=CAT_TO_CAPCOL))
    for col in CAT_TO_CAPCOL.values():
        if col not in dcap:
            dcap[col] = 0
    dcap = dcap[list(CAT_TO_CAPCOL.values())].reset_index()
    # If the inventory carries hospital throughput columns (e.g. annual discharges /
    # ED visits), add them here by summing those columns over `disp` per fire; column
    # names depend on your inventory and are left out by default.
    dcap.to_csv(OUT_DIR / "displaced_capacity_by_fire.csv", index=False)
    print("Wrote facility/summary tables, unified inventory, and displaced_capacity_by_fire.csv")

    # ---------------------------------------------------------------- Step 9
    # SVI overlay (CDC/ATSDR SVI 2022).
    svi_path = _find(SVI_GLOB)
    print("SVI:", svi_path)
    try:
        layers = gpd.list_layers(svi_path)["name"].tolist()
        lyr = next((l for l in layers if "tract" in l.lower() or "svi" in l.lower()), layers[0])
        svi = gpd.read_file(svi_path, layer=lyr)
    except Exception:
        svi = gpd.read_file(svi_path)

    keep_cols = ["FIPS"] + SVI_THEMES + ["E_TOTPOP", "E_AGE65", "E_AGE17", "geometry"]
    svi = svi[[c for c in keep_cols if c in svi.columns]].to_crs(WORKING_CRS)
    for c in SVI_THEMES:
        svi[c] = pd.to_numeric(svi[c], errors="coerce")
        svi.loc[svi[c] < 0, c] = np.nan          # -999 = missing
    for c in ["E_TOTPOP", "E_AGE65", "E_AGE17"]:
        svi[c] = pd.to_numeric(svi[c], errors="coerce")
        svi.loc[svi[c] < 0, c] = np.nan
    svi = svi[svi.geometry.notna()].reset_index(drop=True)

    fac_svi = (gpd.sjoin(fac, svi, how="left", predicate="within")
                 .drop(columns="index_right").drop_duplicates("facility_id"))
    worst = (ff.assign(r=ff.status.map(
                {"complete_loss": 0, "impacted": 1, "no_damage": 2, "not_matched": 3}))
               .sort_values("r").drop_duplicates("facility_id")[["facility_id", "status"]]
               .rename(columns={"status": "fire_status"}))
    fac_svi = fac_svi.merge(worst, on="facility_id", how="left")
    fac_svi["outcome"] = np.select(
        [fac_svi.fire_status.isin(["complete_loss", "impacted"]),
         fac_svi.fire_status.isin(["no_damage", "not_matched"])],
        ["lost_or_impacted", "in_perimeter_survived"], default="outside_all_perimeters")
    (fac_svi[["facility_id", "category", "fire_status", "outcome", "FIPS"] + SVI_THEMES]
        .to_csv(OUT_DIR / "facility_svi.csv", index=False))
    print("Mean tract SVI (RPL_THEMES) by outcome:")
    print(fac_svi.groupby("outcome")["RPL_THEMES"].agg(["count", "mean", "median"]).round(3))

    # ---------------------------------------------------------------- Step 10
    # E2SFCA access, all-fires vs post-fire (lost removed), per tract.
    txx = svi.geometry.centroid.x.values
    tyy = svi.geometry.centroid.y.values
    tree = cKDTree(np.c_[txx, tyy])
    ntr = len(txx)

    e_inv = fac.copy()
    e_inv["capacity"] = pd.to_numeric(e_inv["capacity"], errors="coerce")
    e_inv["supply"] = (e_inv.groupby("category")["capacity"]
                            .transform(lambda s: s.fillna(s.median())).fillna(1.0))
    e_inv["fx"] = fac.geometry.x.values
    e_inv["fy"] = fac.geometry.y.values

    lost_ids = set(ff.loc[ff["status"].isin(["complete_loss", "impacted"]), "facility_id"])
    print(f"facilities located: {len(e_inv)} | lost to fire: {len(lost_ids)}")

    def e2sfca(fac_cat, d0, pop):
        fxy = np.c_[fac_cat["fx"].values, fac_cat["fy"].values]
        S = fac_cat["supply"].values
        neigh = tree.query_ball_point(fxy, r=d0)
        Rj = np.zeros(len(fac_cat))
        for j, idx in enumerate(neigh):
            if not idx:
                continue
            idx = np.asarray(idx)
            w = gauss(np.hypot(txx[idx] - fxy[j, 0], tyy[idx] - fxy[j, 1]), d0)
            denom = float((pop[idx] * w).sum())
            if denom > 0:
                Rj[j] = S[j] / denom
        A = np.zeros(ntr)
        for j, idx in enumerate(neigh):
            if not idx or Rj[j] == 0:
                continue
            idx = np.asarray(idx)
            w = gauss(np.hypot(txx[idx] - fxy[j, 0], tyy[idx] - fxy[j, 1]), d0)
            A[idx] += Rj[j] * w
        return A

    out = svi[["FIPS", "E_TOTPOP", "E_AGE65", "E_AGE17", "geometry"]].copy()
    for cat, cfg in CATCHMENT.items():
        fac_cat = e_inv[e_inv.category == cat]
        if fac_cat.empty:
            print(f"  !! {cat}: 0 facilities (check category label)")
            continue
        pop = pd.to_numeric(svi[cfg["pop"]], errors="coerce").fillna(0).values
        A_all  = e2sfca(fac_cat, cfg["d0"], pop)
        A_post = e2sfca(fac_cat[~fac_cat.facility_id.isin(lost_ids)], cfg["d0"], pop)
        drop = A_all - A_post
        with np.errstate(divide="ignore", invalid="ignore"):
            dpct = np.where(A_all > 0, drop / A_all * 100, 0.0)
        out[f"acc_{cat}"]     = A_all
        out[f"drop_{cat}"]    = drop
        out[f"droppct_{cat}"] = dpct
        print(f"  {cat:30s} facilities={len(fac_cat):5d}  "
              f"tracts w/ access loss={int((drop > 1e-12).sum())}")

    out.drop(columns="geometry").to_csv(OUT_DIR / "access_desert_by_tract.csv", index=False)
    out.to_file(OUT_DIR / "access_desert_by_tract.gpkg", driver="GPKG")
    print("wrote access_desert_by_tract.csv + .gpkg")

    # ---------------------------------------------------------------- Step 11
    # Per-fire attribution of access loss (linear in the surviving facility set).
    loss = ff[ff["status"].isin(["complete_loss", "impacted"])][["facility_id", "fire_name"]].copy()
    loss["fire_name"] = loss["fire_name"].astype(str).str.strip().str.upper()
    fire_of = loss.drop_duplicates("facility_id").set_index("facility_id")["fire_name"].to_dict()

    def ratios(fac_cat, d0, pop):
        fxy = np.c_[fac_cat["fx"].values, fac_cat["fy"].values]
        S = fac_cat["supply"].values
        neigh = tree.query_ball_point(fxy, r=d0)
        Rj = np.zeros(len(fac_cat))
        for j, idx in enumerate(neigh):
            if not idx:
                continue
            idx = np.asarray(idx)
            w = gauss(np.hypot(txx[idx] - fxy[j, 0], tyy[idx] - fxy[j, 1]), d0)
            den = float((pop[idx] * w).sum())
            if den > 0:
                Rj[j] = S[j] / den
        return Rj, neigh, fxy

    pivot, records = [], []
    for cat, cfg in CATCHMENT.items():
        fac_cat = e_inv[e_inv.category == cat].reset_index(drop=True)
        if fac_cat.empty:
            continue
        d0 = cfg["d0"]
        pop = pd.to_numeric(svi[cfg["pop"]], errors="coerce").fillna(0).values
        Rj, neigh, fxy = ratios(fac_cat, d0, pop)

        A_all = np.zeros(ntr)
        for j, idx in enumerate(neigh):
            if not idx or Rj[j] == 0:
                continue
            idx = np.asarray(idx)
            A_all[idx] += Rj[j] * gauss(np.hypot(txx[idx] - fxy[j, 0], tyy[idx] - fxy[j, 1]), d0)

        lost_by_fire = {}
        for j in range(len(fac_cat)):
            fr = fire_of.get(fac_cat.facility_id.iat[j])
            if fr and Rj[j] != 0:
                lost_by_fire.setdefault(fr, []).append(j)

        for fire, js in sorted(lost_by_fire.items()):
            drop = np.zeros(ntr)
            for j in js:
                idx = neigh[j]
                if not idx:
                    continue
                idx = np.asarray(idx)
                drop[idx] += Rj[j] * gauss(np.hypot(txx[idx] - fxy[j, 0], tyy[idx] - fxy[j, 1]), d0)
            with np.errstate(divide="ignore", invalid="ignore"):
                dpct = np.where(A_all > 0, drop / A_all * 100, 0.0)
            hit = drop > 1e-12
            pivot.append(dict(fire=fire, category=cat, tracts=int(hit.sum()),
                              pop_at_25plus=int(pop[hit & (dpct >= 25)].sum()),
                              max_drop_pct=round(float(dpct.max() if hit.any() else 0), 1)))
            records.append(pd.DataFrame(dict(FIPS=svi.loc[hit, "FIPS"].values, fire=fire,
                                             category=cat, drop=drop[hit], drop_pct=dpct[hit])))

    summ = pd.DataFrame(pivot)
    print("=== people in tracts losing >=25% access, by FIRE x CATEGORY ===")
    print(summ.pivot_table(index="fire", columns="category", values="pop_at_25plus",
                           aggfunc="sum", fill_value=0).to_string())
    tidy = pd.concat(records, ignore_index=True)
    tidy.to_csv(OUT_DIR / "access_drop_by_fire_tract.csv", index=False)
    print(f"wrote access_drop_by_fire_tract.csv ({len(tidy):,} rows)")


if __name__ == "__main__":
    main()

