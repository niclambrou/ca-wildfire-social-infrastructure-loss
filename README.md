# Disaster-Related Loss of Social Infrastructure and Spatial Access to Community Services: Evidence from California Wildfires

Starting from a geocoded, multi-agency inventory of California social-infrastructure facilities, this repository reproduces the analysis end to end: it attributes wildfire facility losses (2018–2025), translates them into displaced service capacity, overlays the CDC/ATSDR Social Vulnerability Index, models spatial access with an enhanced two-step floating catchment area (E2SFCA) model with per-fire attribution, tests capacity-adjusted absorption for skilled-nursing beds and childcare slots, and generates the figures.

## Repository layout

| File | Role |
|---|---|
| `Geocode_CA_SV_Inventory.py` | *(prep)* geocodes address-only facility records via the U.S. Census batch geocoder → coordinates |
| `CA_Social_Infrastructure_Pipeline.py` | main analysis: inventory → loss attribution → SVI overlay → E2SFCA access + per-fire attribution; writes every derived table |
| `data/` | geocoded inventory + derived result tables (see **Data**) |
| `requirements.txt` | Python dependencies |
| `LICENSE` | license terms |

Figures are produced from the derived tables (`facility_fire_level_results.csv`, `statewide_by_category.csv`, `facility_svi.csv`, `access_drop_by_fire_tract.csv`) by the plotting cells in the analysis notebook; they are not separate scripts in this repository.

## Data

**Included** (derived outputs, safe to redistribute):

- `facility_inventory_UNIFIED.csv` — the assembled, geocoded facility inventory (the analysis input)
- `facility_fire_level_results.csv`, `per_fire_by_category.csv`, `per_fire_by_subtype.csv`, `statewide_by_category.csv`, `statewide_by_subtype.csv` — loss attribution
- `displaced_capacity_by_fire.csv` — beds / slots / enrollment displaced per fire
- `facility_svi.csv` — facilities joined to SVI theme percentiles
- `access_desert_by_tract.csv` (+ `.gpkg`) and `access_drop_by_fire_tract.csv` — E2SFCA outputs

**External public inputs** (not re-hosted; download from source):

- CAL FIRE Damage Inspection (DINS) records and historic fire perimeters (CAL FIRE FRAP)
- HCAI licensed-facility listings and Long-Term Care Annual Utilization files
- California Department of Social Services childcare, residential-elder-care, and adult-residential files
- California Department of Education public-schools file and CALPADS enrollment
- CDC/ATSDR Social Vulnerability Index 2022, census-tract level (population estimates from the 2018–2022 ACS 5-year)
- U.S. Census Bureau TIGER/Line 2020 census-tract boundaries and primary roads
- California Child Care Resource & Referral Network — California Child Care Portfolio (childcare coverage benchmark)

## Reproduce

1. **Install** — `pip install -r requirements.txt` (Python ≥ 3.10 recommended).
2. **(Optional) Rebuild coordinates** — set the two paths in `Geocode_CA_SV_Inventory.py` and run it to regenerate `lat`/`lon` from raw addresses. Skip this if you use the included geocoded inventory (recommended; it is the canonical set of coordinates).
3. **Run the analysis** — set `DATA_DIR` and `OUT_DIR` in the EDIT PATHS block at the top of `CA_Social_Infrastructure_Pipeline.py`. This writes all derived tables to `OUT_DIR`.
4. **Make the figures** — set `DATA_DIR`/`OUT_DIR` in the EDIT PATHS block and run. `make_two_track_capacity` script also needs the HCAI Long-Term Care `.xlsx` files; `make_fire_maps` script needs the TIGER/Line 2020 tract shapefile and, optionally, the primary-roads shapefile.

Every script has a single EDIT PATHS block at the top — set the folder once and nothing else needs changing.

### Figure → script map

- Figures 2–8 → derived tables (summary charts; see note above)
- Figures 9–12 → `make_fire_maps` (locator, Camp, Eaton, Palisades)
- Figure 13 → `make_capacity_figure`
- Figure 14 → `make_two_track_capacity`

## Notes

The E2SFCA model uses straight-line distance in EPSG:3310; the capacity-adjusted absorption analysis uses a single occupancy vintage per fire and covers the skilled-nursing subtype of residential care; the social-vulnerability overlay uses the 2022 SVI. See the paper's Limitations (§3.6) for the full list. Re-running the pipeline should reproduce the headline numbers in the paper (79 facilities lost or impacted; Camp 19 / Eaton 33 / Palisades 16; ≥25% access-desert totals of 6,662 childcare, 5,256 residential, 4,047 schools).

## Citation

If you use this code or data, please cite the paper:

> Lambrou, N. Pending...

## License

Code is released under the MIT License; derived data under CC-BY 4.0. See `LICENSE`. The external public datasets listed above retain the terms of their original providers.

## Contact

Nicole Lambrou — nlambrou@cpp.edu / Cal Poly Pomona
