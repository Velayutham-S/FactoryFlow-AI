# Derived values in the starter master dataset

Every row in `data/master/*.csv` is transcribed from the **Example records** tables of
`FACTORY_MASTER_DATA_DESIGN.md` §1–§29, with one exception: six `business_rule` rows the
Prediction Agent requires and the document's example set does not contain. They are recorded at
the end of this file. No other row was added, and none was removed.

Two documented values were corrected because they contradicted the frozen DDL. Both were
changed in the design document and in the CSV together, so the two remain consistent; they are
recorded at the end of this file.

207 rows across 29 datasets — 201 transcribed, 6 added. The document presents three of them
(`machine_type_parameter`, `alert_threshold_rule`, `machine_type_failure_mode`) as several
tables grouped under a parent machine type or profile heading; that parent is transcribed from
the heading, not derived.

This file records the **117 field values that the design document does not state** and that
the frozen SQLite schema declares `NOT NULL` without a default. Without them no row could be
inserted. They fall into two kinds.

| Kind | Count | Meaning |
|---|---|---|
| **Structural** | 16 | Only one value is possible given the documented data |
| **Generated** | 101 | A plausible value consistent with every documented constraint |

Nothing here is a business fact taken from anywhere. For development and testing only.

---

## Structural (16)

### `plant_code` on `plant_area` (7), `department` (5), `shift` (4)

All set to `PLT-01`.

The Example records for these three entities omit the parent reference. §1 business rule 1
states "Exactly one `plant` row is active in this deployment", and `PLT-01` is the only
`plant` row in the document, so `PLT-01` is the only value the foreign key can take.

---

## Generated (101)

### 1. `plant` — 2 values

| Column | Value | Basis |
|---|---|---|
| `address_line` | `Plot 14, SIDCO Industrial Estate, Kurichi` | A street address in the documented city. `VARCHAR(200)` |
| `state_region` | `Tamil Nadu` | The state containing the documented city, Coimbatore, consistent with `country_code = IN` |

### 2. `production_line.commissioned_date` — 4 values

Each on or after `plant.commissioned_date` (2016-04-01) and on or before the earliest
`installation_date` of a machine on that line, as §10 business rule 8 requires. Ordered so the
critical line is the oldest and the packaging line the newest.

| Line | Value |
|---|---|
| `LN-01` | 2016-06-01 |
| `LN-02` | 2017-03-01 |
| `LN-03` | 2018-09-01 |
| `LN-04` | 2019-01-15 |

### 3. `machine.serial_number` — 8 values

`UNIQUE`, `NOT NULL`, `VARCHAR(60)`, no format constraint. Follows the shape of the example in
the §10 attribute table, `MMT-VMC-2019-4471`: manufacturer initials, model family, installation
year, sequence. Manufacturer initials come from the documented `machine_type.manufacturer`.

| Machine | Serial | Manufacturer |
|---|---|---|
| `MC-0101` | `MMT-VMC-2016-4471` | Meridian Machine Tools |
| `MC-0102` | `MMT-TC-2016-2318` | Meridian Machine Tools |
| `MC-0103` | `KME-BCM-2016-0912` | Kestrel Metrology |
| `MC-0201` | `AAU-RW6X-2017-1185` | Aravalli Automation |
| `MC-0202` | `BXC-BC12-2017-3340` | Beltrix Conveyors |
| `MC-0301` | `MMT-TC-2018-2907` | Meridian Machine Tools |
| `MC-0302` | `MMT-VMC-2018-5126` | Meridian Machine Tools |
| `MC-0401` | `SPS-CS3-2019-0774` | SealPro Systems |

### 4. `machine.installation_date` and `machine.commissioned_date` — 16 values

`ck_machine_commissioned_after_installation` requires `commissioned_date >= installation_date`.
§10 business rule 8 additionally requires `installation_date` on or after the line's
commissioning date. Each machine is installed after its line opens, in `line_position` order,
and commissioned about two weeks later.

| Machine | Line (commissioned) | Installed | Commissioned |
|---|---|---|---|
| `MC-0101` | `LN-01` (2016-06-01) | 2016-07-12 | 2016-07-26 |
| `MC-0102` | `LN-01` | 2016-07-20 | 2016-08-03 |
| `MC-0103` | `LN-01` | 2016-08-05 | 2016-08-19 |
| `MC-0201` | `LN-02` (2017-03-01) | 2017-04-10 | 2017-04-24 |
| `MC-0202` | `LN-02` | 2017-04-18 | 2017-05-02 |
| `MC-0301` | `LN-03` (2018-09-01) | 2018-10-08 | 2018-10-22 |
| `MC-0302` | `LN-03` | 2018-10-15 | 2018-10-29 |
| `MC-0401` | `LN-04` (2019-01-15) | 2019-02-11 | 2019-02-25 |

### 5. `worker.hire_date` — 13 values

Each on or after `plant.commissioned_date`, and consistent with the documented `skill_level`,
`worker_role.seniority_rank`, and — for the five who are also maintenance engineers — the
documented `years_experience`. Managers and the most experienced engineer are the earliest
hires; the `contract` junior technician is the most recent.

| Worker | Role | Skill | Hired |
|---|---|---|---|
| `EMP-1001` Anand Selvam | `ROL-PMGR` | expert | 2016-04-15 |
| `EMP-1011` Suresh Iyer | `ROL-MENG` | expert (12 yr) | 2016-05-23 |
| `EMP-1010` Divya Menon | `ROL-DMGR` | expert | 2016-06-06 |
| `EMP-1002` Priya Nair | `ROL-LSUP` | senior | 2016-09-01 |
| `EMP-1014` Ganesh Pillai | `ROL-MENG` | senior (9 yr) | 2017-02-13 |
| `EMP-1004` Meera Joseph | `ROL-LSUP` | senior | 2017-05-08 |
| `EMP-1012` Vinod Balan | `ROL-MENG` | senior (8 yr) | 2017-08-21 |
| `EMP-1020` Fathima Rasheed | `ROL-QINS` | senior | 2018-03-19 |
| `EMP-1005` Karthik Rajan | `ROL-SRO` | senior | 2018-11-12 |
| `EMP-1003` Ravi Kumar | `ROL-OP` | intermediate | 2019-06-17 |
| `EMP-1030` Mohan Kurup | `ROL-STK` | intermediate | 2019-09-02 |
| `EMP-1013` Latha Chandran | `ROL-MTECH` | intermediate (5 yr) | 2020-02-10 |
| `EMP-1015` Arjun Das | `ROL-MTECH` | junior, contract (3 yr) | 2022-07-04 |

### 6. `product_line_capability.effective_from_date` — 7 values

Each on or after both the product's documented `introduced_date` and the line's commissioning
date. Primary routes take effect when the product is introduced. The one secondary route —
valve bodies on `LN-01`, the documented recovery option — takes effect later, consistent with
its 2026 qualification expiry.

| Product | Line | Effective from | Basis |
|---|---|---|---|
| `PRD-GH-100` | `LN-01` | 2019-02-11 | product introduced |
| `PRD-VB-075` | `LN-03` | 2020-11-05 | product introduced |
| `PRD-VB-075` | `LN-01` | 2021-03-01 | secondary route qualified later |
| `PRD-PMP-220` | `LN-02` | 2018-07-30 | product introduced |
| `PRD-GH-100` | `LN-04` | 2019-02-11 | product introduced, after `LN-04` opened |
| `PRD-VB-075` | `LN-04` | 2020-11-05 | product introduced |
| `PRD-PMP-220` | `LN-04` | 2019-01-15 | `LN-04` commissioning; the product predates the line |

### 7. `bill_of_materials.effective_from_date` — 10 values

Set to the parent product's documented `introduced_date`, since a bill of materials takes
effect with the product: `PRD-GH-100` 2019-02-11 (3 rows), `PRD-PMP-220` 2018-07-30 (4 rows),
`PRD-VB-075` 2020-11-05 (3 rows).

### 8. `failure_severity_level.description` — 5 values

`TEXT NOT NULL`, non-blank. Each restates that row's own documented flags
(`requires_line_stop`, `requires_immediate_escalation`, `requires_manager_acknowledgement`,
`max_acknowledgement_minutes`, `target_response_time_minutes`) in prose. No new policy.

### 9. `failure_category.description` — 12 values

`TEXT NOT NULL`, non-blank. Each restates the documented `category_name`, `failure_domain`,
`requires_spare_part` and `has_safety_implication` for that row. Verified consistent with both
flags on all 12 rows.

### 10. `business_rule.description` — 11 values

`TEXT NOT NULL`, non-blank. Each restates the documented `rule_name`, `value_type`, value,
`unit` and scope. The two line-scoped rules state which line they override and why, following
the document's own explanation of the escalation and costing overrides.

### 11. `machine_type_failure_mode.leading_indicator_description` — 13 values

`TEXT NOT NULL`, non-blank. Each describes the documented `primary_machine_parameter_code`
moving over the documented `typical_warning_period_hours`. The three rows with no primary
parameter and `is_model_predictable = 0` say so explicitly rather than inventing a signal.

---

## Applied documentation corrections (2)

Two values in the Example records contradicted the frozen DDL and could not be inserted. Both
were documentation defects rather than implementation choices, so each was corrected in
`FACTORY_MASTER_DATA_DESIGN.md` and in the corresponding CSV together. All 201 transcribed rows
now load.

These are the only two documented values that differ from the document's original text.

### 1. `inventory_location` — `LOC-WIP-01` → `LOC-WP-01`

`15_inventory_location.csv` line 5, and §18 of the design document.

```
ck_inventory_location_code_format
  CHECK (inventory_location_code GLOB 'LOC-[A-Z][A-Z]-[A-Z0-9]*' AND ...)
```

The constraint requires a **two-letter** zone segment, and its comment states
`LOC-XX-XXXX`. §3.4 gives the format as `LOC-<zone>-<bin>` with the example `LOC-SP-B2`, also
two letters. `LOC-WIP-01` has a three-letter zone. The other four rows —
`LOC-RM-A1`, `LOC-SP-B2`, `LOC-TC-01`, `LOC-FG-01` — all conform.

Corrected to `LOC-WP-01`, preserving the two-letter zone convention. No `inventory_item`
references this location, so nothing downstream depended on the old value.

### 2. `alert_threshold_rule` — `ATP-VMC-TIGHT` / `PRM-TORQ` critical `SEV-2` → `SEV-1`

`23_alert_threshold_rule.csv` line 10, and §28 of the design document.

```
ck_atr_severities_differ
  CHECK (critical_severity_level_id <> warning_severity_level_id)
```

The row carried `warn_sev = SEV-2` and `crit_sev = SEV-2`. The DDL comment notes the frozen
rule is stronger still: critical must *outrank* warning.

The document explains how this arose. Its own commentary on the tightened profile says
"**Warning severity rises.** `SEV-3` becomes `SEV-2`" — applied to this row, warning was raised
to `SEV-2` while critical remained `SEV-2`, collapsing the two. The equivalent row in
`ATP-VMC-STD` is `SEV-3` / `SEV-2`, and the other three tightened rows keep the two levels
distinct.

Corrected by raising `crit_sev` to `SEV-1`, matching the other two tightened rules whose warning
was raised to `SEV-2` (`PRM-TEMP`, `PRM-VIB`), both of which pair it with `SEV-1` critical. The
warning severity is unchanged, so the document's "warning severity rises" commentary still
holds.

---

## Added rows (6) — `business_rule` for the Prediction Agent

`24_business_rule.csv` lines 13–18. **These are the only rows in the starter dataset that are
not transcribed from the design document's Example records.**

Two of the Prediction Agent's rules state that a threshold is `business_rule` data and forbid a
constant in code:

- `FACTORY_OPERATIONAL_DATA_DESIGN.md` §E15 rule 6 — "A completeness threshold below which
  snapshots are insufficient comes from `business_rule`, not from a constant."
- §E16 rule 6 — "`risk_severity_level_id` is derived from `failure_probability` using
  `business_rule` cut-offs, never a hardcoded mapping."

Neither document defines a code or a value for them, and §29's Example records contain no such
row, so without these six the agent cannot run at all. They use the existing structure and the
existing `maintenance_policy` category; no category, column or constraint was changed.

All six are plant-wide (`production_line_code` empty) and take effect on 2025-04-01, matching
every other plant-wide default in the file.

### `BR-PRED-COMPLETE-MIN` — 70.0000 percent

Bounded on both sides by the document's own commentary on §E15, not chosen freely.

| Bound | Source |
|---|---|
| **Above 42.50** | `FSN-20260729-04191` at 42.50 % is insufficient: "scoring the machine on 42 % of its data … would have returned a confident number derived largely from a broken instrument" |
| **At or below 71** | §E15's consumer table has the Supervisor Agent weighing "a 99 % snapshot and a 71 % snapshot", so a 71 % snapshot must still have produced a prediction |

`70.00` is the round value at the top of that interval. The two sufficient examples, 99.86 and
100.00, clear it; the two insufficient examples fail for their own recorded reasons
(`sensor_fault`, `window_spans_maintenance`) rather than on completeness, which this value
preserves.

### `BR-PRED-RISK-SEV-1` … `BR-PRED-RISK-SEV-5` — 0.8500, 0.6500, 0.4500, 0.2500, 0.0000

Each value is the lowest `failure_probability` that maps onto that severity level. Bands are
evaluated most severe first, and `SEV-5` sits at 0.0000 so every prediction receives a severity.

The four probability-to-severity pairs in §E16's Example records constrain the set, and this one
reproduces all four:

| Documented prediction | Probability | Documented severity | Band selected here |
|---|---|---|---|
| `PDN-20260729-0203` | 0.6800 | `SEV-2` | `SEV-2` (0.65 ≤ 0.68 < 0.85) |
| `PDN-20260729-0244` | 0.7400 | `SEV-2` | `SEV-2` (0.65 ≤ 0.74 < 0.85) |
| `PDN-20260729-0207` | 0.1100 | `SEV-5` | `SEV-5` (0.11 < 0.25) |
| `PDN-20260730-0119` | 0.0600 | `SEV-5` | `SEV-5` (0.06 < 0.25) |

Those pairs fix the two ends: `SEV-2` must begin at or below 0.68, `SEV-1` must begin above 0.74,
and `SEV-4` must begin above 0.11. The interior spacing is uniform at 0.20.

The set is also consistent with the two escalation rules already in the file. `BR-ESC-PROB`
escalates at 0.7000 and `BR-ESC-SEV` requires at least `SEV-2`: a probability of 0.70 lands in
`SEV-2`, so the probability test and the severity test agree rather than one silently overriding
the other.
