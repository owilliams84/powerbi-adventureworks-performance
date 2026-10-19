# AdventureWorks Performance (Power BI)

A four-page executive report on Microsoft's free **AdventureWorks Sales.xlsx** sample, in the Milestone BI
house style. It is the native Power BI version of the d3.js dashboard in `../aw-dashboard/`.

| Page | What it answers |
|---|---|
| 01 Executive summary | Six KPI cards with 36-month sparklines, monthly sales by channel, a seasonality heat map, the year-on-year waterfall by category, a category treemap, top regions |
| 02 Products & margin | Margin against sales by subcategory (log scatter), top 10 products, margin last year vs this year, the most and least profitable models, a subcategory table |
| 03 Customers & channels | New vs returning customers, channel economics, channel mix by category, buyer type × category, sales by order size, top resellers |
| 04 Regions & discounts | Region × category heat map, a regions table, growth by region, what discounts cost, volume-discount bands |

Comparisons are fiscal year (July to June) on fiscal year. The data ends on 15 June 2020, so FY2020 is
compared with FY2019 **to the same day**.

## Build

Everything is generated: close Power BI Desktop first, then

```powershell
python -I etl\prepare_data.py "..\data\AdventureWorks Sales.xlsx"   # star-schema CSVs in data\
powershell -ExecutionPolicy Bypass -File etl\cycle.ps1             # model + report, validate, open, refresh, check, screenshot
```

`cycle.ps1` runs `build_model.py` (TMDL), `check_tmdl.ps1` (Desktop's own parser), `build_report.py`
(PBIR), `powerbi-report-author validate`, opens the PBIP, refreshes over XMLA, runs `compare.py`
(DAX against pandas, three year/channel states, every breakdown the pages show) and captures each
page to `screenshots\`.

`prepare_data.py` takes the path to `AdventureWorks Sales.xlsx`, downloaded from the link below; it is not
kept in this repository, only the CSVs shaped from it.

## Data

`data/` holds CSVs shaped from Microsoft's
[AdventureWorks Sales sample](https://github.com/microsoft/powerbi-desktop-samples/tree/main/AdventureWorks%20Sales%20Sample)
(MIT licence). The model reads them from this repository's raw URLs, so the published copy refreshes in the
Power BI Service with anonymous Web credentials. `python etl\build_model.py --local` reads them from disk instead.
