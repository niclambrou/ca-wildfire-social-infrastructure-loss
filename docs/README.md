# CA statewide wildfire social-infrastructure loss — data & code

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
