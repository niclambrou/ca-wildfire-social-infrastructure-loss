#!/usr/bin/env python
# coding: utf-8

# In[1]:


pip install geopandas matplotlib mapclassify pyogrio


# In[8]:


"""
Access-desert maps (Applied Geography submission)

Outputs: locator.png, camp_fire.png, eaton_fire.png, palisades_fire.png
Each fire panel: grey tract mesh, tracts with a meaningful drop shaded,
perimeter outline, facility outcome points. Locator shows fire stars,
major cities, and (optionally) major roads.

INPUTS (4 you have + 1 required download + 1 optional download)
  facility_fire_level_results.csv
  access_drop_by_fire_tract.csv
  California_Historic_Fire_Perimeters_*.geojson
  tl_2020_06_tract.shp        REQUIRED  https://www2.census.gov/geo/tiger/TIGER2020/TRACT/tl_2020_06_tract.zip
  tl_2020_us_primaryroads.shp OPTIONAL  https://www2.census.gov/geo/tiger/TIGER2020/PRIMARYROADS/tl_2020_us_primaryroads.zip
        (interstates + US highways; if absent the locator just omits roads)

Run:  pip install geopandas matplotlib mapclassify pyogrio shapely
      python make_fire_maps.py
"""

import os
import geopandas as gpd
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.lines import Line2D
from matplotlib.cm import ScalarMappable
from matplotlib.colors import Normalize
from shapely.geometry import box as shp_box

ALBERS = "EPSG:3310"
CMAP, NORM = "OrRd", Normalize(0, 100)

FACILITIES = "../outputs/facility_fire_level_results.csv"
ACCESS     = "../outputs/access_drop_by_fire_tract.csv"
PERIMETERS = "../data/raw/CalFire/California_Historic_Fire_Perimeters_-4891938132824355098.geojson"
TRACTS     = "../data/raw/CA_Census_Tracts/tl_2020_06_tract/tl_2020_06_tract.shp"
ROADS      = "../data/raw/CA_Roads/tl_2020_us_primaryroads/tl_2020_us_primaryroads.shp"   # optional

FIRES = [
    ("Camp",      "CAMP",      2018, "residential_elderly_disabled"),
    ("Eaton",     "EATON",     2025, "residential_elderly_disabled"),
    ("Palisades", "PALISADES", 2025, "childcare_center"),
]
SERVICE_LABEL = {"residential_elderly_disabled": "residential", "childcare_center": "childcare",
                 "school": "school", "hospital_clinic": "hospital"}

VIEW_MARGIN = 0.30
FRAME_DROP_THRESHOLD = 10   # %; tracts below this don't drive the zoom
COLOR_MIN_PCT = 2.0         # %; tracts below this stay grey (raise -> more grey)
MARKER_SIZE = 30            # facility point size (was 70)
BASE_W, MIN_H, MAX_H = 6.5, 4.5, 8.5

BUCKET_TO_OUTCOME = {"complete_loss": "lost", "impacted": "impacted", "no_damage": "survived"}
OUTCOME_STYLE = {
    "lost":     dict(marker="^", face="#B2182B", edge="black",   label="Lost"),
    "impacted": dict(marker="s", face="#F4A582", edge="black",   label="Impacted"),
    "survived": dict(marker="o", face="white",   edge="#444444", label="Survived"),
}
LOC_LABEL_OFFSET = {"Camp": (12, 8), "Eaton": (34, 22), "Palisades": (34, -26)}

# name, lat, lon, label offset (pts), ha
CITIES = [
    ("San Francisco", 37.7749, -122.4194, (-6, 5), "right"),
    ("San Jose",      37.3382, -121.8863, (6, -9), "left"),
    ("Sacramento",    38.5816, -121.4944, (6, 2),  "left"),
    ("Redding",       40.5865, -122.3917, (6, 2),  "left"),
    ("Fresno",        36.7378, -119.7871, (6, 0),  "left"),
    ("Bakersfield",   35.3733, -119.0187, (6, -2), "left"),
    ("Los Angeles",   34.0522, -118.2437, (-7, -13), "right"),
    ("San Diego",     32.7157, -117.1611, (6, -2), "left"),
]

# ---- load ----------------------------------------------------------------
fac = pd.read_csv(FACILITIES, low_memory=False)
fac = fac[fac["has_coords"] == True].copy()
fac["outcome"] = fac["bucket"].map(BUCKET_TO_OUTCOME)
fac = gpd.GeoDataFrame(fac, geometry=gpd.points_from_xy(fac.lon, fac.lat),
                       crs="EPSG:4326").to_crs(ALBERS)

acc = pd.read_csv(ACCESS)
acc["GEOID"] = acc["FIPS"].astype("int64").astype(str).str.zfill(11)

perims = gpd.read_file(PERIMETERS, engine="pyogrio").to_crs(ALBERS)
tracts = gpd.read_file(TRACTS, engine="pyogrio").to_crs(ALBERS)
tracts["GEOID"] = tracts["GEOID"].astype(str).str.zfill(11)
ca_outline = tracts.dissolve()

facility_handles = [
    Line2D([0], [0], marker=st["marker"], color="w", markerfacecolor=st["face"],
           markeredgecolor=st["edge"], markersize=8, label=st["label"])
    for st in OUTCOME_STYLE.values()
]

def get_perim(fname, year):
    """Largest-area polygon among name+year matches (e.g. two 'CAMP' 2018 fires)."""
    m = perims[(perims["FIRE_NAME"] == fname) & (perims["YEAR_"] == year)]
    if len(m) > 1:
        m = m.loc[[m.geometry.area.idxmax()]]
    return m

# ---- per-fire figure -----------------------------------------------------
def draw_fire(disp, fname, year, service):
    perim = get_perim(fname, year)
    if perim.empty:
        print(f"  ! {disp}: perimeter not found"); return

    a = acc[(acc["fire"] == fname) & (acc["category"] == service)][["GEOID", "drop_pct"]]
    affected = tracts[tracts["GEOID"].isin(a["GEOID"])].merge(a, on="GEOID", how="left")

    core = affected[affected["drop_pct"] >= FRAME_DROP_THRESHOLD] if not affected.empty else affected
    base = core if not core.empty else perim
    minx, miny, maxx, maxy = pd.concat([base, perim]).total_bounds
    dx, dy = maxx - minx, maxy - miny
    minx -= dx * VIEW_MARGIN; maxx += dx * VIEW_MARGIN
    miny -= dy * VIEW_MARGIN; maxy += dy * VIEW_MARGIN
    bbox = shp_box(minx, miny, maxx, maxy)

    # grey tract mesh fills the whole frame; only meaningful drops get colored
    mesh = gpd.clip(tracts[tracts.intersects(bbox)], bbox)
    colored = affected[affected["drop_pct"] >= COLOR_MIN_PCT]
    colored = gpd.clip(colored, bbox) if not colored.empty else colored

    h = min(max(BASE_W * (maxy - miny) / (maxx - minx), MIN_H), MAX_H)
    fig, ax = plt.subplots(figsize=(BASE_W, h))

    mesh.plot(ax=ax, color="#efefef", edgecolor="#bdbdbd", linewidth=0.4)
    if not colored.empty:
        colored.plot(ax=ax, column="drop_pct", cmap=CMAP, norm=NORM,
                     edgecolor="white", linewidth=0.3)
    perim.boundary.plot(ax=ax, color="black", linewidth=1.2, zorder=4)

    f = fac[(fac["fire_name"] == fname) & (fac["fire_year"] == year)]
    for outcome, st in OUTCOME_STYLE.items():
        p = f[f["outcome"] == outcome]
        ax.scatter(p.geometry.x, p.geometry.y, marker=st["marker"], c=st["face"],
                   edgecolors=st["edge"], linewidths=0.5, s=MARKER_SIZE, alpha=0.95,
                   zorder=5 if outcome != "survived" else 4)

    ax.set_xlim(minx, maxx); ax.set_ylim(miny, maxy)
    ax.set_aspect("equal"); ax.set_axis_off()
    nlost = (f["outcome"] != "survived").sum()
    ax.set_title(f"{disp} Fire ({year}) — {nlost} lost/impacted\n"
                 f"tract {SERVICE_LABEL[service]} access drop", fontsize=12)

    sm = ScalarMappable(norm=NORM, cmap=CMAP); sm.set_array([])
    fig.colorbar(sm, ax=ax, shrink=0.7, fraction=0.046, pad=0.02, label="% access drop")
    ax.legend(handles=facility_handles, loc="lower center", ncol=3,
              bbox_to_anchor=(0.5, -0.08), frameon=False, fontsize=9)

    out = f"{disp.lower()}_fire.png"
    plt.savefig(out, dpi=300, bbox_inches="tight"); plt.close(fig)
    print(f"  wrote {out}  ({BASE_W:.1f}x{h:.1f} in)")

# ---- locator -------------------------------------------------------------
def draw_locator():
    fig, ax = plt.subplots(figsize=(5.5, 7))
    ca_outline.plot(ax=ax, color="#f2f2f2", edgecolor="#999999", linewidth=0.6, zorder=1)

    if os.path.exists(ROADS):
        roads = gpd.read_file(ROADS, engine="pyogrio").to_crs(ALBERS)
        roads = gpd.clip(roads, ca_outline)
        roads.plot(ax=ax, color="#c2c2c2", linewidth=0.6, zorder=2)
    else:
        print("  (no roads file found; locator drawn without roads)")

    cpts = gpd.GeoDataFrame(
        {"name": [c[0] for c in CITIES]},
        geometry=gpd.points_from_xy([c[2] for c in CITIES], [c[1] for c in CITIES]),
        crs="EPSG:4326").to_crs(ALBERS)
    ax.scatter(cpts.geometry.x, cpts.geometry.y, s=16, c="#333333", zorder=4)
    for (name, _, _, off, ha), pt in zip(CITIES, cpts.geometry):
        ax.annotate(name, (pt.x, pt.y), xytext=off, textcoords="offset points",
                    fontsize=8, color="#333333", ha=ha, zorder=5)

    for disp, fname, year, _ in FIRES:
        perim = get_perim(fname, year)
        if perim.empty:
            continue
        perim.plot(ax=ax, color="#B2182B", edgecolor="#7f0000", linewidth=0.6, zorder=6)
        c = perim.union_all().centroid
        ax.annotate(disp, (c.x, c.y), xytext=LOC_LABEL_OFFSET.get(disp, (10, 6)),
                    textcoords="offset points", fontsize=10, fontweight="bold",
                    zorder=7, arrowprops=dict(arrowstyle="-", lw=0.6, color="black"))

    ax.set_title("Fire locations", fontsize=12)
    ax.set_axis_off(); ax.set_aspect("equal")
    plt.savefig("locator.png", dpi=300, bbox_inches="tight"); plt.close(fig)
    print("  wrote locator.png")

if __name__ == "__main__":
    draw_locator()
    for spec in FIRES:
        draw_fire(*spec)
    print("done.")


# In[22]:


"""
Two-track capacity test: beds vs slots.
LEFT  (skilled nursing): absorption ratio = surviving county SNF vacant beds /
      displaced SNF beds, from HCAI LTC utilization (occupancy). >1 = absorbable.
RIGHT (childcare): county coverage = licensed center slots / children; a
      structural-deficit market (<<1) with no surplus. Benchmark: CA Child Care
      Portfolio (~25.6% of children 0-12 with working parents covered, 2023).

Run: pip install pandas matplotlib openpyxl ; python make_two_track_capacity.py
"""
import math
from pathlib import Path
import pandas as pd
import matplotlib.pyplot as plt

# ============================ EDIT PATHS HERE ============================
DATA_DIR = Path(".")          # folder that holds the input files (set once)
OUT_DIR  = Path(".")          # where the figure is written

# inputs (override an individual line if that file lives somewhere else)
FACILITIES    = DATA_DIR / "../outputs/facility_fire_level_results.csv"
INVENTORY     = DATA_DIR / "../data/processed/facility_inventory_UNIFIED.csv"
ACCESS_DESERT = DATA_DIR / "../outputs/access_desert_by_tract.csv"
LTC_2018      = DATA_DIR / "../data/raw/Long-term_Care_Facilities/ltc18_util_data_final.xlsx"     # Camp fire-year
LTC_2025      = DATA_DIR / "../data/raw/Long-term_Care_Facilities/ltc25_util_data_prelim.xlsx"    # Eaton/Palisades fire-year

# output
OUT_PNG       = OUT_DIR / "../outputs/two_track_capacity.png"
# =========================================================================

# label, FIRE_NAME, county, LTC utilization file for that fire-year
SNF_PLAN = [("Camp",  "CAMP",  "BUTTE",       LTC_2018),
            ("Eaton", "EATON", "LOS ANGELES", LTC_2025)]

def ltc_county(yf, county):
    d = pd.read_excel(yf, sheet_name="Page 1-5", engine="openpyxl").iloc[4:].copy()
    for c in ["TOT_PAT_DAYS_FOR", "TOT_LIC_BED_DAYS"]:
        d[c] = pd.to_numeric(d[c], errors="coerce")
    d = d[d["TOT_LIC_BED_DAYS"] > 0]
    d = d[d["COUNTY"].astype(str).str.upper().str.contains(county)]
    vac = (d["TOT_LIC_BED_DAYS"] - d["TOT_PAT_DAYS_FOR"]).sum() / 365.0
    occ = (d["TOT_PAT_DAYS_FOR"] / d["TOT_LIC_BED_DAYS"]).median()
    return vac, occ

f = pd.read_csv(FACILITIES, low_memory=False)
f["fire_name"] = f["fire_name"].str.strip()
snf = f[(f["category"] == "residential_elderly_disabled") &
        (f["bucket"].isin(["complete_loss", "impacted"])) &
        (f["subtype"].str.contains("Skilled Nursing", case=False, na=False))]

snf_rows = []
for label, fire, cty, yf in SNF_PLAN:
    D = snf[snf["fire_name"] == fire]["capacity"].sum()
    vac, occ = ltc_county(yf, cty)
    surviving = vac - D * (1 - occ)
    snf_rows.append((label, D, surviving, surviving / D, occ, cty.title()))

# childcare coverage
inv = pd.read_csv(INVENTORY, low_memory=False)
inv["capacity"] = pd.to_numeric(inv["capacity"], errors="coerce")
cc = inv[(inv["category"] == "childcare_center") & (inv["status"] == "LICENSED")]
pop = pd.read_csv(ACCESS_DESERT)
pop["cty"] = pop["FIPS"].astype("int64").astype(str).str.zfill(11).str[2:5]
cc_lost = f[(f["category"] == "childcare_center") & (f["bucket"].isin(["complete_loss", "impacted"]))]

cov_rows = []
for code, name, disp_fires in [("007", "BUTTE", ["CAMP"]),
                               ("037", "LOS ANGELES", ["EATON", "PALISADES"])]:
    slots = cc[cc["county"].astype(str).str.upper() == name]["capacity"].sum()
    u18 = pop[pop["cty"] == code]["E_AGE17"].sum()
    fires_txt = " + ".join(f"{fr.title()} {cc_lost[cc_lost.fire_name==fr]['capacity'].sum():.0f}" for fr in disp_fires)
    cov_rows.append((name.title(), slots / u18, fires_txt))

# ---------- figure ----------
fig, (axA, axB) = plt.subplots(1, 2, figsize=(13.5, 5))
ys = [1, 0]
panel_mid = math.sqrt(0.4 * 60)
for y, (label, D, surv, ratio, occ, cty) in zip(ys, snf_rows):
    color = "#2C7D5A" if ratio >= 1 else "#B2182B"
    if ratio >= 1:
        axA.barh(y, ratio - 1, left=1, height=0.5, color=color)
    else:
        axA.barh(y, 1 - ratio, left=ratio, height=0.5, color=color)
    bar_mid = math.sqrt(min(ratio, 1.0) * max(ratio, 1.0))
    axA.text(bar_mid, y + 0.33, f"{ratio:.2f}\u00d7", va="bottom", ha="center",
             fontsize=12, fontweight="bold", color=color)
    axA.text(panel_mid, y - 0.33,
             f"{D:.0f} beds displaced vs {surv:.0f} vacant ({cty}, {occ*100:.0f}% occ)",
             va="center", ha="center", fontsize=8, color="#444")
axA.axvline(1, color="black", lw=1.1, ls="--")
axA.set_xscale("log"); axA.set_xlim(0.4, 60); axA.set_ylim(-0.6, 1.6)
axA.set_yticks(ys); axA.set_yticklabels([r[0] for r in snf_rows], fontsize=11)
axA.set_xlabel("absorption ratio = surviving vacant beds \u00f7 displaced beds (log)", fontsize=9)
axA.set_title("Beds (skilled nursing): could be absorbed?", fontsize=12, fontweight="bold")
axA.spines[["top", "right"]].set_visible(False)
axA.text(0.97, 1.55, "\u2190 shortfall", ha="right", fontsize=8, color="#B2182B")
axA.text(1.03, 1.55, "surplus \u2192", ha="left", fontsize=8, color="#2C7D5A")

for y, (name, cov, fires_txt) in zip(ys, cov_rows):
    axB.barh(y, cov, height=0.5, color="#C77D2E")
    axB.text(cov + 0.02, y, f"{cov:.2f}", va="center", fontsize=11, fontweight="bold", color="#C77D2E")
    axB.text(0.02, y - 0.3, f"displaced slots: {fires_txt}", va="center", fontsize=8, color="#444")
axB.axvline(1.0, color="black", lw=1.1, ls="--")
axB.text(1.0, 1.55, "\u2190 deficit (no surplus)", ha="right", fontsize=8, color="#B2182B")
axB.set_xlim(0, 1.15); axB.set_ylim(-0.6, 1.6)
axB.set_yticks(ys); axB.set_yticklabels([r[0] for r in cov_rows], fontsize=11)
axB.set_xlabel("coverage = licensed center slots \u00f7 children <18", fontsize=9)
axB.set_title("Slots (childcare): any surplus to absorb into?", fontsize=12, fontweight="bold")
axB.spines[["top", "right"]].set_visible(False)

fig.suptitle("Beds had slack to absorb the loss; childcare slots did not", fontsize=14, y=1.02)
fig.text(0.5, -0.04,
         "LA skilled nursing ran ~92% full but its scale left thousands of vacant beds (Eaton absorbed); Butte could not (Camp). "
         "Childcare runs far below 1 space per child (\u224825.6% of working-parent children statewide, CA Child Care Portfolio 2023) "
         "\u2014 a deficit market with no surplus, so displaced slots are net losses everywhere.",
         ha="center", fontsize=8.3, style="italic", wrap=True)
plt.tight_layout()
plt.savefig(OUT_PNG, dpi=300, bbox_inches="tight")
print(f"wrote {OUT_PNG}")


# In[23]:


"""
Displaced capacity vs. spatial access loss, by fire, for the two capacity-
constrained services (residential beds, childcare slots). Left: capacity removed.
Right: people in tracts losing >=25% access. The Eaton row shows capacity removed
with no access desert -- the gap the two-track absorption figure then resolves.

Run: pip install pandas matplotlib ; python make_capacity_figure.py
"""
from pathlib import Path
import pandas as pd
import matplotlib.pyplot as plt

# ============================ EDIT PATHS HERE ============================
DATA_DIR = Path(".")          # folder that holds the input files (set once)
OUT_DIR  = Path(".")          # where the figure is written

DISPLACED_CAP = DATA_DIR / "../data/processed/displaced_capacity_by_fire.csv"
ACCESS_DROP   = DATA_DIR / "../outputs/access_drop_by_fire_tract.csv"
ACCESS_DESERT = DATA_DIR / "../outputs/access_desert_by_tract.csv"

OUT_PNG       = OUT_DIR / "../outputs/capacity_vs_access.png"
# =========================================================================

FIRES = ["Camp", "Eaton", "Palisades"]
CAP_COLOR, DESERT_COLOR = "#2C5F8A", "#B2182B"
DESERT_THRESHOLD = 25

# service: (row label, capacity column, capacity unit, access category, demand col)
SERVICES = [
    ("Residential care", "residential_beds", "beds (65+)",  "residential_elderly_disabled", "E_AGE65"),
    ("Childcare",        "childcare_slots",  "slots (<18)", "childcare_center",             "E_AGE17"),
]

cap = pd.read_csv(DISPLACED_CAP).set_index("fire")

acc = pd.read_csv(ACCESS_DROP)
acc["GEOID"] = acc["FIPS"].astype("int64").astype(str).str.zfill(11)
pop = pd.read_csv(ACCESS_DESERT)
pop["GEOID"] = pop["FIPS"].astype("int64").astype(str).str.zfill(11)
acc = acc.merge(pop[["GEOID", "E_TOTPOP", "E_AGE65", "E_AGE17"]], on="GEOID", how="left")

def desert_pop(fire_upper, category, demand_col):
    g = acc[(acc["fire"] == fire_upper) & (acc["category"] == category)]
    g = g[g["drop_pct"] >= DESERT_THRESHOLD]
    return int(g[demand_col].fillna(0).sum())

fig, axes = plt.subplots(len(SERVICES), 2, figsize=(11, 6), sharey=False)

for r, (label, cap_col, unit, category, demand_col) in enumerate(SERVICES):
    cap_vals    = [cap.loc[f, cap_col] if f in cap.index else 0 for f in FIRES]
    desert_vals = [desert_pop(f.upper(), category, demand_col) for f in FIRES]
    y = range(len(FIRES))[::-1]
    axL, axR = axes[r, 0], axes[r, 1]
    axL.barh(list(y), cap_vals, color=CAP_COLOR, height=0.6)
    axR.barh(list(y), desert_vals, color=DESERT_COLOR, height=0.6)
    for ax, vals in [(axL, cap_vals), (axR, desert_vals)]:
        ax.set_yticks(list(y)); ax.set_yticklabels(FIRES, fontsize=10)
        ax.spines[["top", "right"]].set_visible(False)
        m = max(vals) if max(vals) > 0 else 1
        ax.set_xlim(0, m * 1.18)
        for yi, v in zip(y, vals):
            ax.text(v + m * 0.02, yi, f"{v:,.0f}", va="center", fontsize=9)
    axL.set_ylabel(label, fontsize=12, fontweight="bold")
    axL.set_xlabel(f"capacity displaced \u2014 {unit}", fontsize=9)
    axR.set_xlabel("people in \u226525% access desert", fontsize=9)

axes[0, 0].set_title("Absolute capacity removed", fontsize=12, fontweight="bold", pad=10)
axes[0, 1].set_title("Spatial access desert", fontsize=12, fontweight="bold", pad=10)
fig.suptitle("Capacity loss is real even where the access metric reads zero", fontsize=13, y=0.98)
fig.text(0.5, 0.005,
         "Eaton removed bed/slot capacity comparable to Camp yet produced no \u226525% access desert: "
         "surviving facilities are counted as available substitutes (tested in the absorption figure).",
         ha="center", fontsize=8.5, style="italic", wrap=True)
plt.tight_layout(rect=[0, 0.03, 1, 0.96])
plt.savefig(OUT_PNG, dpi=300, bbox_inches="tight")
print(f"wrote {OUT_PNG}")

