"""Generate the PBIR report: four pages in the Milestone house style.

    python etl/build_report.py

Rewrites "AdventureWorks Performance.Report/definition/pages" from scratch every run - a renamed
visual left on disk still renders, as an empty box. Close Power BI Desktop first.

The pages follow the d3 version of this dashboard (aw-dashboard/), panel for panel where Power BI
has a native equivalent. Where it does not: the slope chart is a clustered bar of last year and
this year, the Sankey is a region-by-category heat matrix, the tile map is a growth bar chart
coloured better/worse, and the packed circles are a ranked bar chart.
"""

from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

import milestone_pbir as mb
from milestone_pbir import (BODY, CARD, GOLD, INK, LIGHT, MUTED, NAVY, RULE, SLATE, colour, column, lit, obj)

ROOT = Path(__file__).resolve().parents[1]
NAME = "AdventureWorks Performance"
REPORT = ROOT / f"{NAME}.Report"
PAGES = REPORT / "definition" / "pages"
RESOURCES = REPORT / "StaticResources" / "RegisteredResources"
SHARED = REPORT / "StaticResources" / "SharedResources" / "BaseThemes"
ASSETS = ROOT / "etl" / "assets"
THEME_NAME = "MilestoneTheme.json"
BASE_THEME = "CY25SU12"

F = mb.Fields("Metrics")
CATEGORY_COLOURS = {"Bikes": NAVY, "Components": GOLD, "Clothing": SLATE, "Accessories": LIGHT}
CHANNEL_COLOURS = {"Reseller": NAVY, "Internet": GOLD}

# Shared top-right controls.
FY_X, CH_X, CTRL_Y, CTRL_H = 812, 1118, 72, 64


# --------------------------------------------------------------------------------------------
# Small helpers on top of milestone_pbir
# --------------------------------------------------------------------------------------------

def chrome(title: str, subtitle: str | None = None) -> dict:
    return mb.dynamic_chrome(lit(title), lit(subtitle) if subtitle else None)


def dyn_chrome(title: str, subtitle_measure: str) -> dict:
    return mb.dynamic_chrome(lit(title), F.expr(subtitle_measure))


def value_colours(table: str, col: str, mapping: dict) -> list:
    return [{"properties": {"fill": colour(v)}, "selector": {"data": [{"scopeId": {"Comparison": {
        "ComparisonKind": 0,
        "Left": {"Column": {"Expression": {"SourceRef": {"Entity": table}}, "Property": col}},
        "Right": lit(k)["expr"]}}}]}} for k, v in mapping.items()]


def series_colours(mapping: dict) -> list:
    return [{"properties": {"fill": colour(v)}, "selector": {"metadata": F.ref(k)}} for k, v in mapping.items()]


def measure_fill(measure: str) -> list:
    return [{"properties": {"fill": {"solid": {"color": F.expr(measure)}}},
             "selector": {"data": [{"dataViewWildcard": {"matchingOption": 1}}]}}]


def legend(show: bool = True) -> list:
    return obj(show=lit(show), position=lit("Top"), showTitle=lit(False), fontSize=lit(8.5), labelColor=colour(MUTED))


def labels(show: bool = True, size: float = 8.5, millions: bool = False) -> list:
    """Data labels in whole units, or in £M to one decimal. Units are written as literals like
    '1D': lit(1.0) gives '1.0D', which the validator rejects."""
    if not show:
        return obj(show=lit(False))
    units = "1000000D" if millions else "1D"
    props = dict(show=lit(True), fontSize=lit(size), color=colour(BODY),
                 labelDisplayUnits={"expr": {"Literal": {"Value": units}}})
    if millions:
        props["labelPrecision"] = lit(1)
    return obj(**props)


def sort(field: dict, direction: str = "Descending") -> dict:
    return mb.sort_by(field, direction)


def measure_at_least(name: str, measure: str, low: float) -> dict:
    """Visual-level 'measure >= low'."""
    ref = {"Measure": {"Expression": {"SourceRef": {"Source": "m"}}, "Property": measure}}
    return {"name": name,
            "field": {"Measure": {"Expression": {"SourceRef": {"Entity": "Metrics"}}, "Property": measure}},
            "type": "Advanced",
            "filter": {"Version": 2, "From": [{"Name": "m", "Entity": "Metrics", "Type": 0}],
                       "Where": [{"Condition": {"Comparison": {"ComparisonKind": 2, "Left": ref,
                                                               "Right": lit(low)["expr"]}}}]}}


def chart(name, vtype, x, y, w, h, z, roles: dict, objects: dict, container: dict,
          sort_def: dict | None = None, filters=None) -> dict:
    query = {"queryState": {role: {"projections": fields} for role, fields in roles.items()}}
    if sort_def:
        query["sortDefinition"] = sort_def
    return mb.visual(name, vtype, x, y, w, h, z, query=query, objects=objects, container=container, filters=filters)


def table(name, x, y, w, h, z, rows: list, values: list, container: dict, sort_def=None, filters=None,
          image_width: float | None = 120.0, colour_rules: list | None = None, columns: list | None = None,
          image_height: float = 16.0) -> dict:
    """House pivotTable. colour_rules: (property, colour measure, target measure) - a value
    column's font or background from a measure."""
    objects = mb.pivot_objects(image_width)
    if image_width:
        objects["grid"][0]["properties"]["imageHeight"] = lit(image_height)
    objects["values"] = [{"properties": {"fontSize": lit(9.0), "fontColorPrimary": colour(BODY),
                                         "backColorPrimary": colour(CARD), "backColorSecondary": colour(CARD)}}]
    for prop, colour_measure, target in colour_rules or []:
        objects["values"].append({
            "properties": {prop: {"solid": {"color": F.expr(colour_measure)}}},
            "selector": {"data": [{"dataViewWildcard": {"matchingOption": 1}}], "metadata": F.ref(target)}})
    roles = {"Rows": rows, "Values": values}
    if columns:
        roles["Columns"] = columns
    return chart(name, "pivotTable", x, y, w, h, z, roles, objects, container, sort_def, filters)


def hover_guard(name, x, y, w, h, z) -> dict:
    """A transparent shape over a matrix of SVG cells. Hovering an image cell otherwise shows
    its raw 'data:image/svg+xml...' text in the grid's own tooltip, which no setting turns off.
    Costs clicks on the grid underneath, so only over display-only matrices."""
    clear = {"show": lit(True), "fillColor": colour(mb.PAPER), "transparency": lit(100.0)}
    off = {"show": lit(False)}
    container = mb.no_chrome()
    container["visualHeader"] = obj(show=lit(False))
    node = mb.visual(name, "shape", x, y, w, h, z, container=container)
    node["visual"]["objects"] = {
        "shape": [{"properties": {"tileShape": lit("rectangle")}, "selector": {"id": "default"}}],
        "fill": [{"properties": clear}, {"properties": clear, "selector": {"id": "default"}}],
        "outline": [{"properties": off}, {"properties": off, "selector": {"id": "default"}}],
    }
    return node


def channel_slicer(name: str, z: int) -> dict:
    """Two tiles, nothing selected by default: no selection means both channels."""
    node = mb.button_slicer(name, CH_X, CTRL_Y, 298, CTRL_H, z, "Channel", "Channel", "Internet", 2,
                            header="CHANNEL  (NONE SELECTED = BOTH)")
    objects = node["visual"]["objects"]
    del objects["general"]
    objects["selection"] = [{"properties": {"strictSingleSelect": lit(False), "selectAllCheckboxEnabled": lit(False)}}]
    node["visual"]["syncGroup"] = {"groupName": "Channel", "fieldChanges": True, "filterChanges": True}
    return node


def fy_slicer(name: str, z: int) -> dict:
    node = mb.button_slicer(name, FY_X, CTRL_Y, 290, CTRL_H, z, "Date", "Fiscal Year", "FY2020", 3,
                            header="FISCAL YEAR")
    node["visual"]["syncGroup"] = {"groupName": "FiscalYear", "fieldChanges": True, "filterChanges": True}
    return node


def header(slug: str, title: str, ref: str) -> list[dict]:
    return mb.masthead(slug, title, ref, title_width=780) + [
        mb.dynamic_text(f"vStand{slug}", 18, 112, 780, 56, 95, F.expr("Title Standfirst"), size=10.0),
        fy_slicer(f"vFY{slug}", 400),
        channel_slicer(f"vCh{slug}", 410),
    ]


def no_filter(source: str, targets: list[str]) -> list[dict]:
    return [{"source": source, "target": t, "type": "NoFilter"} for t in targets]


# --------------------------------------------------------------------------------------------
# Pages
# --------------------------------------------------------------------------------------------

def page_summary():
    v = header("Sum", "AdventureWorks performance", "01 / EXECUTIVE SUMMARY")
    cards = ["Card Sales", "Card Gross Profit", "Card Gross Margin", "Card Orders", "Card Average Order",
             "Card Customers"]
    for i, c in enumerate(cards):
        v.append(mb.svg_image(f"vCard{i + 1}Sum", [24, 258, 492, 727, 961, 1196][i], 176, 220, 120, 500 + i,
                              F.expr(c)))

    v.append(chart(
        "vMonthlySum", "lineChart", 24, 312, 776, 284, 600,
        {"Category": [column("Date", "Month Start", "Month")],
         "Series": [column("Channel", "Channel")],
         "Y": [F.m("Sales Line", "Sales")]},
        {"categoryAxis": mb.axis(axisType=lit("Scalar")), "valueAxis": mb.axis(gridlines=True),
         "legend": legend(), "labels": labels(False),
         "lineStyles": obj(strokeWidth=lit(2), showMarker=lit(False)),
         "dataPoint": value_colours("Channel", "Channel", CHANNEL_COLOURS)},
        chrome("Monthly sales, all three years",
               "Jul 2017 to 15 Jun 2020, so June 2020 is half a month. The year slicer does not apply"),
        sort(column("Date", "Month Start"), "Ascending")))

    v.append(table(
        "vSeasonSum", 816, 312, 600, 284, 610,
        [column("Date", "Fiscal Year", "Year")], [F.m("Cell Season", "Sales £M")],
        chrome("Seasonality: sales by month, £ millions", "Darker cells sold more. The year slicer does not apply"),
        image_width=30.0, image_height=22.0, columns=[column("Date", "Month")]))
    v.append(hover_guard("vSeasonGuardSum", 816, 362, 600, 234, 611))

    v.append(chart(
        "vWaterfallSum", "waterfallChart", 24, 612, 452, 264, 620,
        {"Category": [column("Product", "Category")], "Y": [F.m("Sales vs PY", "Change in sales")],
         "Tooltips": [F.m("Sales"), F.m("Sales PY", "Prior year")]},
        {"categoryAxis": obj(show=lit(True), showAxisTitle=lit(False), fontSize=lit(8.5), labelColor=colour(MUTED)),
         "valueAxis": mb.axis(gridlines=True), "legend": legend(False), "labels": labels(True, 8.0, millions=True),
         "sentimentColors": obj(increaseFill=colour(mb.GOOD), decreaseFill=colour(mb.BAD), totalFill=colour(NAVY))},
        dyn_chrome("What changed on the year before", "Subtitle Waterfall"),
        sort(column("Product", "Category"), "Ascending")))

    v.append(report_tooltip(chart(
        "vTreemapSum", "treemap", 492, 612, 452, 264, 630,
        {"Group": [column("Product", "Category")], "Details": [column("Product", "Subcategory")],
         "Values": [F.m("Sales")], "Tooltips": [F.m("Gross Margin %", "Gross margin")]},
        {"legend": legend(False), "labels": obj(show=lit(True), fontSize=lit(8.0)),
         "dataPoint": value_colours("Product", "Category", CATEGORY_COLOURS)},
        chrome("Where sales come from", "Area is sales; colour is category. Hover for the trend, click to filter"))))

    v.append(report_tooltip(chart(
        "vRegionsSum", "barChart", 960, 612, 456, 264, 640,
        {"Category": [column("Territory", "Region")], "Y": [F.m("Sales")],
         "Tooltips": [F.m("Sales vs PY %", "vs prior year"), F.m("Gross Margin %", "Gross margin")]},
        {"categoryAxis": mb.axis(), "valueAxis": mb.axis(gridlines=True), "legend": legend(False),
         "labels": labels(True, 8.0, millions=True), "dataPoint": obj(fill=colour(NAVY))},
        chrome("Top regions", "Hover for the trend; right-click a region for its detail page"),
        sort(F.m("Sales")))))

    pg = mb.page("pgSummary", "Executive summary")
    pg["visualInteractions"] = no_filter("vFYSum", ["vMonthlySum", "vSeasonSum"])
    return pg, v


def page_products():
    v = header("Prd", "What sells, and what it earns", "02 / PRODUCTS AND MARGIN")

    v.append(report_tooltip(chart(
        "vScatterPrd", "scatterChart", 24, 176, 900, 340, 500,
        {"Category": [column("Product", "Subcategory")], "Series": [column("Product", "Category")],
         "X": [F.m("Sales")], "Y": [F.m("Gross Margin %", "Gross margin")], "Size": [F.m("Units")]},
        {"categoryAxis": mb.axis(gridlines=True, logAxisScale=lit(True)), "valueAxis": mb.axis(gridlines=True),
         "legend": legend(), "dataPoint": value_colours("Product", "Category", CATEGORY_COLOURS),
         "categoryLabels": obj(show=lit(True), fontSize=lit(8.0), color=colour(BODY))},
        chrome("Margin against sales, by subcategory",
               "Across is sales on a log scale, up is gross margin, size is units sold. Hover for the trend"))))

    v.append(table(
        "vTopProductsPrd", 940, 176, 476, 340, 510,
        [column("Product", "Product")],
        [F.m("Product Rank", "#"), F.m("Sales"), F.m("Bar Product Sales", " "), F.m("Gross Margin %", "Margin")],
        chrome("Top 10 products", "Ranked by sales in the year on screen. Right-click one for its detail"),
        sort(F.m("Product Rank"), "Ascending"), [F.range_filter("fTop10Prd", "Product Rank", 1, 10)],
        image_width=90.0))

    v.append(chart(
        "vMarginPrd", "clusteredBarChart", 24, 532, 452, 344, 520,
        {"Category": [column("Product", "Subcategory")],
         "Y": [F.m("Gross Margin % PY", "Last year"), F.m("Gross Margin %", "This year")]},
        {"categoryAxis": mb.axis(maxMarginFactor=lit(40)), "valueAxis": mb.axis(gridlines=True),
         "legend": legend(), "labels": labels(False),
         "dataPoint": series_colours({"Gross Margin % PY": LIGHT, "Gross Margin %": NAVY})},
        dyn_chrome("Margin, last year and this year", "Subtitle Margin"),
        sort(F.m("Gross Margin %")), [F.range_filter("fTop8Prd", "Subcategory Rank", 1, 8)]))

    v.append(chart(
        "vModelsPrd", "barChart", 492, 532, 452, 344, 530,
        {"Category": [column("Product", "Model")], "Y": [F.m("Gross Profit")],
         "Tooltips": [F.m("Sales"), F.m("Gross Margin %", "Gross margin")]},
        {"categoryAxis": mb.axis(maxMarginFactor=lit(40)), "valueAxis": mb.axis(gridlines=True),
         "legend": legend(False), "labels": labels(False), "dataPoint": measure_fill("Model Profit Colour")},
        chrome("Gross profit by model", "The 7 most and 3 least profitable; grey loses money. Right-click to drill"),
        sort(F.m("Gross Profit")), [F.range_filter("fModelsPrd", "Model Shown", 1, 1)]))

    v.append(table(
        "vSubcatsPrd", 960, 532, 456, 344, 540,
        [column("Product", "Subcategory")],
        [F.m("Sales"), F.m("Bar Subcategory Sales", " "), F.m("Sales vs PY Label", "vs PY"),
         F.m("Gross Margin %", "Margin")],
        chrome("Subcategories", "Ranked by sales. 'new' means little or nothing sold the year before"),
        sort(F.m("Sales")), image_width=80.0,
        colour_rules=[("fontColor", "Growth Colour", "Sales vs PY Label")]))

    return mb.page("pgProducts", "Products & margin"), v


def page_customers():
    v = header("Cst", "Who buys, and how", "03 / CUSTOMERS AND CHANNELS")
    for i, c in enumerate(["Card Customers Total", "Card New Customers", "Card Returning Customers",
                           "Card Returning Sales"]):
        v.append(mb.svg_image(f"vCard{i + 1}Cst", 24 + i * 352, 176, 336, 96, 500 + i, F.expr(c)))

    v.append(chart(
        "vNewRetCst", "columnChart", 24, 288, 452, 308, 600,
        {"Category": [column("Date", "Fiscal Year", "Year")],
         "Y": [F.m("Returning Customers", "Returning"), F.m("New Customers", "New")]},
        {"categoryAxis": mb.axis(), "valueAxis": mb.axis(gridlines=True), "legend": legend(),
         "labels": labels(True, 8.0),
         "dataPoint": series_colours({"Returning Customers": NAVY, "New Customers": GOLD})},
        chrome("New and returning customers",
               "New means a first order in that year; everyone is new in FY2018, where the data starts"),
        sort(column("Date", "Fiscal Year"), "Ascending")))

    v.append(table(
        "vChannelsCst", 492, 288, 924, 112, 610,
        [column("Channel", "Channel")],
        [F.m("Sales"), F.m("Sales Share of Channels", "Share"), F.m("Gross Margin %", "Gross margin"),
         F.m("Orders"), F.m("Average Order Value", "Avg order"), F.m("Units per Order", "Units / order"),
         F.m("Customers"), F.m("Sales per Customer", "Sales / customer")],
        chrome("Channel economics: both channels, whatever the channel slicer says"),
        sort(F.m("Sales")), image_width=None))

    v.append(chart(
        "vMixCst", "hundredPercentStackedBarChart", 492, 412, 924, 184, 615,
        {"Category": [column("Product", "Category")], "Series": [column("Channel", "Channel")],
         "Y": [F.m("Sales")]},
        {"categoryAxis": mb.axis(), "valueAxis": obj(show=lit(False), showAxisTitle=lit(False)),
         "legend": legend(), "labels": obj(show=lit(True), fontSize=lit(8.0), color=colour(CARD), labelPrecision=lit(0)),
         "dataPoint": value_colours("Channel", "Channel", CHANNEL_COLOURS)},
        chrome("Channel mix: share of each category's sales"),
        sort(column("Product", "Category"), "Ascending")))

    v.append(table(
        "vWhoCst", 24, 612, 520, 264, 620,
        [column("Reseller", "Buyer Type", "Buyer")], [F.m("Cell Buyer", "Sales")],
        chrome("Who buys what", "Sales by buyer type and category. Darker cells sold more"),
        image_width=66.0, image_height=22.0, columns=[column("Product", "Category")]))
    v.append(hover_guard("vWhoGuardCst", 24, 662, 520, 214, 621))

    v.append(chart(
        "vOrderSizeCst", "columnChart", 560, 612, 400, 264, 630,
        {"Category": [column("Sales Order", "Order Size")], "Y": [F.m("Sales")], "Tooltips": [F.m("Orders")]},
        {"categoryAxis": mb.axis(), "valueAxis": mb.axis(gridlines=True), "legend": legend(False),
         "labels": labels(True, 8.0, millions=True), "dataPoint": obj(fill=colour(NAVY))},
        chrome("Sales by order size", "Orders banded by their whole value; hover for the order count"),
        sort(column("Sales Order", "Order Size"), "Ascending")))

    v.append(chart(
        "vResellersCst", "barChart", 976, 612, 440, 264, 640,
        {"Category": [column("Reseller", "Reseller")], "Y": [F.m("Sales")],
         "Tooltips": [F.m("Orders"), F.m("Gross Margin %", "Gross margin")]},
        {"categoryAxis": mb.axis(maxMarginFactor=lit(45)), "valueAxis": mb.axis(gridlines=True),
         "legend": legend(False), "labels": labels(False), "dataPoint": obj(fill=colour(NAVY))},
        chrome("Top 15 resellers", "Ranked by sales; scroll for all fifteen"),
        sort(F.m("Sales")), [F.range_filter("fTop15Cst", "Reseller Rank", 1, 15)]))

    pg = mb.page("pgCustomers", "Customers & channels")
    pg["visualInteractions"] = no_filter("vFYCst", ["vNewRetCst"]) + no_filter("vChCst", ["vChannelsCst", "vMixCst"])
    return pg, v


def page_regions():
    v = header("Reg", "Where it sells, and what discounts cost", "04 / REGIONS AND DISCOUNTS")

    v.append(table(
        "vMatrixReg", 24, 176, 688, 340, 500,
        [column("Territory", "Region")], [F.m("Cell Region", "Sales")],
        chrome("Sales by region and category", "Darker cells sold more within this grid"),
        image_width=110.0, image_height=18.0, columns=[column("Product", "Category")]))
    v.append(hover_guard("vMatrixGuardReg", 24, 226, 688, 290, 501))

    v.append(table(
        "vRegionsReg", 728, 176, 688, 340, 510,
        [column("Territory", "Region")],
        [F.m("Sales"), F.m("Bar Region Sales", " "), F.m("Sales vs PY Label", "vs PY"),
         F.m("Sales Share of Regions", "Share"), F.m("Gross Margin %", "Margin"), F.m("Orders")],
        chrome("Regions", "Ranked by sales. Right-click a region for its detail page"),
        sort(F.m("Sales")), colour_rules=[("fontColor", "Growth Colour", "Sales vs PY Label")]))

    v.append(chart(
        "vGrowthReg", "barChart", 24, 532, 452, 344, 520,
        {"Category": [column("Territory", "Region")], "Y": [F.m("Sales vs PY %", "Growth")],
         "Tooltips": [F.m("Sales"), F.m("Sales PY", "Prior year")]},
        {"categoryAxis": mb.axis(), "valueAxis": mb.axis(gridlines=True), "legend": legend(False),
         "labels": labels(True, 8.0), "dataPoint": measure_fill("Growth Colour")},
        dyn_chrome("Growth by region", "Subtitle Growth"),
        sort(F.m("Sales vs PY %"))))

    v.append(chart(
        "vDiscountReg", "scatterChart", 492, 532, 452, 344, 530,
        {"Category": [column("Product", "Model")], "Series": [column("Product", "Category")],
         "X": [F.m("Discount Rate", "Average discount")], "Y": [F.m("Gross Margin %", "Gross margin")],
         "Size": [F.m("Discount")]},
        {"categoryAxis": mb.axis(gridlines=True), "valueAxis": mb.axis(gridlines=True), "legend": legend(),
         "dataPoint": value_colours("Product", "Category", CATEGORY_COLOURS)},
        dyn_chrome("What discounts cost", "Subtitle Discounts"),
        filters=[measure_at_least("fDiscReg", "Discount", 1.0)]))

    v.append(table(
        "vVolumeReg", 960, 532, 456, 344, 540,
        [column("Line Size", "Line Size")],
        [F.m("Sales"), F.m("Discount", "Discount"), F.m("Bar Discount", " "), F.m("Discount Rate", "Rate"),
         F.m("Gross Margin %", "Margin")],
        chrome("Volume discounts", "Order lines by quantity. Resellers earn discounts from 11 units"),
        sort(column("Line Size", "Line Size"), "Ascending"), image_width=80.0))

    return mb.page("pgRegions", "Regions & discounts"), v


# --------------------------------------------------------------------------------------------
# Drill-through and tooltip pages
# --------------------------------------------------------------------------------------------

TIP_PAGE = "pgTipTrend"
KPI_CARDS = ["Card Sales", "Card Gross Profit", "Card Gross Margin", "Card Orders", "Card Average Order",
             "Card Customers"]
KPI_X = [24, 258, 492, 727, 961, 1196]


def report_tooltip(node: dict) -> dict:
    """Hovering this visual shows the tooltip page instead of the default field list."""
    node["visual"]["visualContainerObjects"]["visualTooltip"] = obj(
        show=lit(True), type=lit("Canvas"), section=lit(TIP_PAGE))
    return node


def back_button(name: str) -> dict:
    """Back to the page the drill-through came from: white on ink with a gold edge, level with
    the slicer tiles. Ctrl+click in Desktop's edit mode, plain click in the Service."""
    node = mb.action_button(name, 1296, 94, 120, 36, 420, "‹  Back", None, fill=INK, text_colour=CARD,
                            outline=GOLD)
    node["visual"]["visualContainerObjects"]["visualLink"] = obj(show=lit(True), type=lit("Back"))
    return node


def drill_header(slug: str, ref: str, title_measure: str, stand_measure: str) -> list[dict]:
    """Masthead with a measure for the title, the standfirst, the shared slicers and Back.
    The slicers sit left of Back; the title is narrower than on the overview pages."""
    v = [n for n in mb.masthead(slug, "", ref) if not n["name"].startswith("vTitle")]
    title = mb.dynamic_text(f"vTitle{slug}", 18, 70, 640, 42, 90, F.expr(title_measure), size=20.0, color=INK)
    title["visual"]["visualContainerObjects"]["title"][0]["properties"]["bold"] = lit(True)
    v += [title,
          mb.dynamic_text(f"vStand{slug}", 18, 112, 640, 56, 95, F.expr(stand_measure), size=10.0)]
    fy = fy_slicer(f"vFY{slug}", 400)
    ch = channel_slicer(f"vCh{slug}", 410)
    fy["position"].update(x=676, width=290)
    ch["position"].update(x=982, width=298)
    return v + [fy, ch, back_button(f"vBack{slug}")]


def drill_page(name: str, display: str, fields: list[tuple[str, str, str]]) -> dict:
    """A drill-through page, hidden from the page tabs. fields: (table, column, value the page
    opens on when it is not reached by a drill). Each field is a filter with howCreated
    Drillthrough plus a pageBinding parameter bound to it. Power BI offers the drill on any
    visual that carries one of them (tested in the Service: the products table offers it with
    Product alone, the models bar with Model alone) and clears the other."""
    pg = mb.page(name, display)
    pg["visibility"] = "HiddenInViewMode"
    filters, params = [], []
    for i, (table, col, default) in enumerate(fields):
        fname = f"fDrill{name[2:]}{i}"
        f = mb.categorical_filter(fname, table, col, [default])
        if default is None:
            del f["filter"]
        f["howCreated"] = "Drillthrough"
        filters.append(f)
        params.append({"name": f"Param_{fname}", "boundFilter": fname, "fieldExpr": f["field"]})
    pg["filterConfig"] = {"filters": filters}
    # acceptsFilterContext None: only the drilled field comes across. With the default, the source
    # page's year and channel arrived as page filters and cut each slicer here to a single tile;
    # the slicers' sync groups carry the year and channel instead.
    pg["pageBinding"] = {"name": f"Pod{name[2:]}", "type": "Drillthrough", "parameters": params,
                         "acceptsFilterContext": "None"}
    return pg


def kpi_row(slug: str) -> list[dict]:
    return [mb.svg_image(f"vCard{i + 1}{slug}", KPI_X[i], 176, 220, 120, 500 + i, F.expr(c))
            for i, c in enumerate(KPI_CARDS)]


def monthly_line(name: str, z: int, subtitle: str) -> dict:
    return chart(
        name, "lineChart", 24, 312, 776, 284, z,
        {"Category": [column("Date", "Month Start", "Month")], "Series": [column("Channel", "Channel")],
         "Y": [F.m("Sales Line", "Sales")]},
        {"categoryAxis": mb.axis(axisType=lit("Scalar")), "valueAxis": mb.axis(gridlines=True),
         "legend": legend(), "labels": labels(False),
         "lineStyles": obj(strokeWidth=lit(2), showMarker=lit(False)),
         "dataPoint": value_colours("Channel", "Channel", CHANNEL_COLOURS)},
        chrome("Monthly sales by channel, all three years", subtitle),
        sort(column("Date", "Month Start"), "Ascending"))


def page_product_detail():
    slug = "PD"
    v = drill_header(slug, "DRILL-THROUGH / PRODUCT DETAIL", "Drill Product Title", "Drill Product Standfirst")
    v += kpi_row(slug)
    v.append(monthly_line(f"vMonthly{slug}", 600, "Jul 2017 to 15 Jun 2020. The year slicer does not apply"))

    v.append(chart(
        f"vMargin{slug}", "clusteredColumnChart", 816, 312, 600, 284, 610,
        {"Category": [column("Date", "Month")],
         "Y": [F.m("Gross Margin % PY", "Last year"), F.m("Gross Margin %", "This year")]},
        {"categoryAxis": mb.axis(), "valueAxis": mb.axis(gridlines=True), "legend": legend(),
         "labels": labels(False), "dataPoint": series_colours({"Gross Margin % PY": LIGHT, "Gross Margin %": NAVY})},
        dyn_chrome("Margin against the prior year", "Subtitle Margin Month"),
        sort(column("Date", "Month"), "Ascending")))

    v.append(chart(
        f"vRegions{slug}", "barChart", 24, 612, 452, 264, 620,
        {"Category": [column("Territory", "Region")], "Y": [F.m("Sales")],
         "Tooltips": [F.m("Sales vs PY %", "vs prior year"), F.m("Gross Margin %", "Gross margin")]},
        {"categoryAxis": mb.axis(), "valueAxis": mb.axis(gridlines=True), "legend": legend(False),
         "labels": labels(True, 8.0), "dataPoint": obj(fill=colour(NAVY))},
        chrome("Top regions for it", "Ranked by sales; scroll for every region it sold in. Right-click to drill"),
        sort(F.m("Sales"))))

    v.append(chart(
        f"vBuyers{slug}", "barChart", 492, 612, 452, 264, 630,
        {"Category": [column("Reseller", "Buyer Type", "Buyer")], "Y": [F.m("Sales")],
         "Tooltips": [F.m("Orders"), F.m("Gross Margin %", "Gross margin")]},
        {"categoryAxis": mb.axis(), "valueAxis": mb.axis(gridlines=True), "legend": legend(False),
         "labels": labels(True, 8.0), "dataPoint": obj(fill=colour(GOLD))},
        chrome("Who buys it", "Sales by buyer type: internet shoppers or the reseller's business type"),
        sort(F.m("Sales"))))

    v.append(table(
        f"vItems{slug}", 960, 612, 456, 264, 640,
        [column("Product", "Product")],
        [F.m("Sales"), F.m("Sales vs PY Label", "vs PY"), F.m("Gross Margin %", "Margin"), F.m("Units")],
        chrome("Sizes and colours", "Every product in the drill, ranked by sales"),
        sort(F.m("Sales")), image_width=None,
        colour_rules=[("fontColor", "Growth Colour", "Sales vs PY Label")]))

    pg = drill_page("pgProductDetail", "Product detail",
                    [("Product", "Model", "Mountain-200"), ("Product", "Product", None)])
    pg["visualInteractions"] = no_filter(f"vFY{slug}", [f"vMonthly{slug}"])
    return pg, v


def page_region_detail():
    slug = "RD"
    v = drill_header(slug, "DRILL-THROUGH / REGION DETAIL", "Drill Region Title", "Drill Region Standfirst")
    v += kpi_row(slug)
    v.append(monthly_line(f"vMonthly{slug}", 600, "Jul 2017 to 15 Jun 2020. The year slicer does not apply"))

    v.append(chart(
        f"vMix{slug}", "donutChart", 816, 312, 600, 284, 610,
        {"Category": [column("Product", "Category")], "Y": [F.m("Sales")]},
        {"legend": obj(show=lit(True), position=lit("Right"), showTitle=lit(False), fontSize=lit(9.0),
                       labelColor=colour(MUTED)),
         "labels": obj(show=lit(True), fontSize=lit(9.0), labelStyle=lit("Category, percent of total"),
                       percentageLabelPrecision=lit(1)),
         "dataPoint": value_colours("Product", "Category", CATEGORY_COLOURS)},
        chrome("Category mix", "Share of the region's sales in the year on screen"),
        sort(F.m("Sales"))))

    v.append(table(
        f"vTop{slug}", 24, 612, 452, 264, 620,
        [column("Product", "Product")],
        [F.m("Product Rank", "#"), F.m("Sales"), F.m("Bar Product Sales", " "), F.m("Gross Margin %", "Margin")],
        chrome("Top 10 products here", "Ranked by sales in the region. Right-click a product to drill"),
        sort(F.m("Product Rank"), "Ascending"), [F.range_filter(f"fTop10{slug}", "Product Rank", 1, 10)],
        image_width=70.0))

    v.append(chart(
        f"vCatGrowth{slug}", "clusteredBarChart", 492, 612, 452, 264, 630,
        {"Category": [column("Product", "Category")],
         "Y": [F.m("Sales PY", "Last year"), F.m("Sales", "This year")],
         "Tooltips": [F.m("Sales vs PY %", "Growth")]},
        {"categoryAxis": mb.axis(), "valueAxis": mb.axis(gridlines=True), "legend": legend(),
         "labels": labels(True, 8.0, millions=True),
         "dataPoint": series_colours({"Sales PY": LIGHT, "Sales": NAVY})},
        dyn_chrome("Growth on the prior year", "Subtitle Category Growth"),
        sort(column("Product", "Category"), "Ascending")))

    v.append(chart(
        f"vSubGrowth{slug}", "barChart", 960, 612, 456, 264, 640,
        {"Category": [column("Product", "Subcategory")], "Y": [F.m("Sales vs PY % Established", "Growth")],
         "Tooltips": [F.m("Sales"), F.m("Sales PY", "Prior year")]},
        {"categoryAxis": mb.axis(maxMarginFactor=lit(40)), "valueAxis": mb.axis(gridlines=True),
         "legend": legend(False), "labels": labels(True, 8.0), "dataPoint": measure_fill("Growth Colour")},
        dyn_chrome("Growth in its 8 biggest subcategories", "Subtitle Sub Growth"),
        sort(F.m("Sales vs PY % Established")), [F.range_filter(f"fTop8{slug}", "Subcategory Rank", 1, 8)]))

    pg = drill_page("pgRegionDetail", "Region detail", [("Territory", "Region", "Southwest")])
    pg["visualInteractions"] = no_filter(f"vFY{slug}", [f"vMonthly{slug}"])
    return pg, v


def page_tooltip():
    """A 320x240 report-tooltip page: one SVG card drawn by the 'Tip Card' measure."""
    pg = mb.page(TIP_PAGE, "Tooltip: 36-month trend")
    pg.update(width=320, height=240, visibility="HiddenInViewMode", type="Tooltip",
              displayOption="FitToPage")
    pg["pageBinding"] = {"name": "PodTipTrend", "type": "Tooltip", "parameters": []}
    pg["objects"]["background"] = obj(color=colour(CARD), transparency=lit(0.0))
    return pg, [mb.svg_image("vTipCard", 0, 0, 320, 240, 100, F.expr("Tip Card"))]


# --------------------------------------------------------------------------------------------
# Theme and assembly
# --------------------------------------------------------------------------------------------

def theme() -> dict:
    return {
        "name": THEME_NAME,
        "dataColors": [NAVY, GOLD, SLATE, LIGHT, mb.GOOD, mb.BAD, mb.GOLD_TEXT, MUTED],
        "background": mb.PAPER, "foreground": BODY, "tableAccent": INK,
        "good": mb.GOOD, "neutral": MUTED, "bad": mb.BAD,
        "textClasses": {
            "title": {"fontFace": "Segoe UI Semibold", "fontSize": 14, "color": INK},
            "header": {"fontFace": "Segoe UI Semibold", "fontSize": 11, "color": INK},
            "label": {"fontFace": "Segoe UI", "fontSize": 9, "color": BODY},
            "callout": {"fontFace": "Segoe UI", "fontSize": 20, "color": INK},
        },
        "visualStyles": {"*": {"*": {
            "background": [{"show": True, "color": {"solid": {"color": CARD}}}],
            "border": [{"show": True, "color": {"solid": {"color": RULE}}, "radius": 4}],
            "padding": [{"top": 8, "bottom": 8, "left": 10, "right": 10}],
            "dropShadow": [{"show": False}],
        }}},
    }


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8", newline="\n")


def rmtree_retry(path: Path) -> None:
    for attempt in range(4):
        try:
            shutil.rmtree(path)
            return
        except FileNotFoundError:
            return
        except PermissionError:
            if attempt == 3:
                shutil.rmtree(path, ignore_errors=True)
                return
            time.sleep(0.4)


def main() -> None:
    rmtree_retry(PAGES)
    order, total = [], 0
    version = {"visual": "2.12.0", "report": "3.4.0", "page": "2.3.1"}
    for build in (page_summary, page_products, page_customers, page_regions, page_product_detail,
                  page_region_detail, page_tooltip):
        pg, visuals = build()
        names = set()
        for node in visuals:
            if node["name"] in names:
                sys.exit(f"ERROR: duplicate visual name {node['name']} on {pg['name']}")
            names.add(node["name"])
            write_json(PAGES / pg["name"] / "visuals" / node["name"] / "visual.json", node)
        for inter in pg.get("visualInteractions", []):
            assert inter["source"] in names and inter["target"] in names, inter
        write_json(PAGES / pg["name"] / "page.json", pg)
        order.append(pg["name"])
        total += len(visuals)
        print(f"  {pg['name']:12s} {len(visuals):2d} visuals  ({pg['displayName']})")

    write_json(PAGES / "pages.json", {
        "$schema": f"{mb.SCHEMA}/pagesMetadata/1.0.0/schema.json",
        "pageOrder": order, "activePageName": order[0]})
    write_json(REPORT / "definition" / "version.json", {
        "$schema": f"{mb.SCHEMA}/versionMetadata/1.0.0/schema.json", "version": "2.0.0"})
    write_json(REPORT / "definition" / "report.json", {
        "$schema": f"{mb.SCHEMA}/report/3.3.0/schema.json",
        "themeCollection": {
            "baseTheme": {"name": BASE_THEME, "reportVersionAtImport": version, "type": "SharedResources"},
            "customTheme": {"name": THEME_NAME, "reportVersionAtImport": version, "type": "RegisteredResources"},
        },
        "objects": {"section": [{"properties": {"verticalAlignment": lit("Top")}}],
                    "outspacePane": [{"properties": {"expanded": lit(False)}}]},
        "resourcePackages": [
            {"name": "RegisteredResources", "type": "RegisteredResources",
             "items": [{"name": THEME_NAME, "path": THEME_NAME, "type": "CustomTheme"},
                       {"name": mb.MARK_FILE, "path": mb.MARK_FILE, "type": "Image"}]},
            {"name": "SharedResources", "type": "SharedResources",
             "items": [{"name": BASE_THEME, "path": f"BaseThemes/{BASE_THEME}.json", "type": "BaseTheme"}]},
        ],
        "settings": {"useStylableVisualContainerHeader": True, "useEnhancedTooltips": False},
    })
    write_json(RESOURCES / THEME_NAME, theme())
    shutil.copyfile(ASSETS / "milestone-mark.svg", RESOURCES / mb.MARK_FILE)
    SHARED.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ASSETS / f"{BASE_THEME}.json", SHARED / f"{BASE_THEME}.json")

    write_json(REPORT / "definition.pbir", {
        "$schema": f"{mb.SCHEMA}Properties/2.0.0/schema.json",
        "version": "4.0",
        "datasetReference": {"byPath": {"path": f"../{NAME}.SemanticModel"}}})
    write_json(REPORT / ".platform", {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/gitIntegration/platformProperties/2.0.0/schema.json",
        "metadata": {"type": "Report", "displayName": NAME},
        "config": {"version": "2.0", "logicalId": "6a4e2c18-9b3d-4f7a-8c51-2d0e9f6b3a27"}})
    write_json(ROOT / f"{NAME}.pbip", {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/pbip/pbipProperties/1.0.0/schema.json",
        "version": "1.0", "artifacts": [{"report": {"path": f"{NAME}.Report"}}],
        "settings": {"enableAutoRecovery": True}})
    print(f"\n{len(order)} pages, {total} visuals -> {REPORT.name}")


if __name__ == "__main__":
    main()
