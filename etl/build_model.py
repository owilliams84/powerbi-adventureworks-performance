"""Generate the TMDL semantic model for AdventureWorks Performance.

    python etl/build_model.py            # partitions read data/*.csv from GitHub raw
    python etl/build_model.py --local    # partitions read data/ on this machine (offline)

Rewrites "AdventureWorks Performance.SemanticModel/definition" from scratch every run. The CSVs
are the ones prepare_data.py writes; GitHub raw is what lets the published model refresh.

Comparisons are fiscal year on fiscal year. The data ends on 15 June 2020, two weeks before the
end of FY2020, so every prior-year measure cuts the earlier year at the same day of the fiscal
year - the 'like for like' rule lives in one place, PY_VARS, and every PY measure uses it.
"""

from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path

import milestone_pbir as mb

ROOT = Path(__file__).resolve().parents[1]
NAME = "AdventureWorks Performance"
MODEL = ROOT / f"{NAME}.SemanticModel"
DEFN = MODEL / "definition"
DATA = ROOT / "data"

tag = mb.tagger("8d2f4a61-3c7e-4b9a-a1d5-6e0f2b7c9d14", "aw")
q = mb.q

USD = "\\$#,0"
INT = "#,0"
PCT = "0.0%"


# --------------------------------------------------------------------------------------------
# Schema: (column, source, dtype, options)
# --------------------------------------------------------------------------------------------

def col(name, source, dtype, **o):
    return dict(name=name, source=source, dtype=dtype, **o)


M_TYPE = {"string": "type text", "int64": "Int64.Type", "double": "type number", "dateTime": "type date"}

TABLES = {
    "Date": dict(
        file="dim_date.csv", date_table=True,
        doc="One row per day over three whole fiscal years, 1 Jul 2017 to 30 Jun 2020. Fiscal years\n"
            "run July to June and are named for the year they end in: FY2020 is Jul 2019 to Jun 2020.",
        columns=[
            col("Date", "Date", "dateTime", key=True, format="d mmm yyyy"),
            col("Fiscal Year", "FiscalYear", "string", sortBy="FY Sort"),
            col("FY Sort", "FYSort", "int64", hidden=True, format="0"),
            col("Month", "Month", "string", sortBy="Month No"),
            col("Month No", "MonthNo", "int64", hidden=True, format="0", doc="1 = July."),
            col("Month Year", "MonthYear", "string", sortBy="Month Index"),
            col("Month Index", "MonthIndex", "int64", hidden=True, format="0",
                doc="0 for Jul 2017 to 35 for Jun 2020: the x position of a sparkline point."),
            col("Month Start", "MonthStart", "dateTime", hidden=True, format="mmm yyyy"),
            col("Day of FY", "DayOfFY", "int64", hidden=True, format="0",
                doc="1 on 1 July. Prior-year measures cut the earlier year on this column."),
        ],
    ),
    "Product": dict(
        file="dim_product.csv",
        doc="397 products in 119 models, 37 subcategories and 4 categories.",
        columns=[
            col("Product Key", "ProductKey", "int64", key=True, hidden=True, format="0"),
            col("Product", "Product", "string"),
            col("Model", "Model", "string"),
            col("Subcategory", "Subcategory", "string"),
            col("Category", "Category", "string", sortBy="Category Sort"),
            col("Category Sort", "CategorySort", "int64", hidden=True, format="0"),
            col("Color", "Color", "string"),
            col("List Price", "ListPrice", "double", format="\\$#,0.00"),
        ],
    ),
    "Territory": dict(
        file="dim_territory.csv",
        doc="Eleven sales territories in three groups. Corporate HQ has no sales.",
        columns=[
            col("Territory Key", "SalesTerritoryKey", "int64", key=True, hidden=True, format="0"),
            col("Region", "Region", "string"),
            col("Country", "Country", "string"),
            col("Group", "TerritoryGroup", "string", sortBy="Group Sort"),
            col("Group Sort", "GroupSort", "int64", hidden=True, format="0"),
        ],
    ),
    "Reseller": dict(
        file="dim_reseller.csv",
        doc="701 resellers, plus key -1 for internet sales. 'Buyer Type' names that row 'Internet'\n"
            "so one column splits every sale by who bought it.",
        columns=[
            col("Reseller Key", "ResellerKey", "int64", key=True, hidden=True, format="0"),
            col("Reseller", "Reseller", "string"),
            col("Business Type", "BusinessType", "string"),
            col("Buyer Type", "BuyerType", "string", sortBy="Buyer Type Sort"),
            col("Buyer Type Sort", "BuyerTypeSort", "int64", hidden=True, format="0"),
            col("City", "City", "string"),
            col("State", "State", "string"),
            col("Country", "Country", "string"),
        ],
    ),
    "Customer": dict(
        file="dim_customer.csv",
        doc="18,484 internet customers, plus key -1 for reseller sales.",
        columns=[
            col("Customer Key", "CustomerKey", "int64", key=True, hidden=True, format="0"),
            col("Customer", "Customer", "string"),
            col("City", "City", "string"),
            col("State", "State", "string"),
            col("Country", "Country", "string"),
        ],
    ),
    "Channel": dict(
        file="dim_channel.csv",
        doc="Internet or Reseller.",
        columns=[
            col("Channel Key", "ChannelKey", "int64", key=True, hidden=True, format="0"),
            col("Channel", "Channel", "string"),
        ],
    ),
    "Line Size": dict(
        file="dim_line_size.csv",
        doc="Order-line quantity bands. AdventureWorks' reseller volume discounts start at 11, 15,\n"
            "25 and 41 units, so the bands follow those steps.",
        columns=[
            col("Line Size Key", "LineSizeKey", "int64", key=True, hidden=True, format="0"),
            col("Line Size", "LineSize", "string", sortBy="Line Size Key"),
        ],
    ),
    "Sales Order": dict(
        file="dim_order.csv",
        doc="One row per order. 'Order Size' bands the whole order's value, fixed at build time, so\n"
            "an order keeps its band whatever product filter is on the page.",
        columns=[
            col("Order ID", "OrderID", "string", key=True),
            col("Order Value", "OrderValue", "double", hidden=True, format=USD),
            col("Order Size", "OrderSize", "string", sortBy="Order Size Sort"),
            col("Order Size Sort", "OrderSizeSort", "int64", hidden=True, format="0"),
        ],
    ),
    "Sales": dict(
        file="fact_sales.csv",
        doc="One row per order line, 121,253 of them. Every column is hidden; the measures in\n"
            "Metrics own how each amount is aggregated and compared.",
        columns=[
            col("Line Key", "LineKey", "int64", hidden=True, format="0"),
            col("Order ID", "OrderID", "string", hidden=True),
            col("Order Date", "OrderDate", "dateTime", hidden=True, format="yyyy-mm-dd"),
            col("Product Key", "ProductKey", "int64", hidden=True, format="0"),
            col("Territory Key", "SalesTerritoryKey", "int64", hidden=True, format="0"),
            col("Reseller Key", "ResellerKey", "int64", hidden=True, format="0"),
            col("Customer Key", "CustomerKey", "int64", hidden=True, format="0"),
            col("Channel Key", "ChannelKey", "int64", hidden=True, format="0"),
            col("Line Size Key", "LineSizeKey", "int64", hidden=True, format="0"),
            col("Account Key", "AccountKey", "string", hidden=True,
                doc="C<customer> or R<reseller>: one key across both channels, so 'Customers' counts\n"
                    "internet customers and resellers together."),
            col("Account First FY", "AccountFirstFY", "int64", hidden=True, format="0",
                doc="Fiscal year of the account's first order in the data. New customers are the\n"
                    "accounts whose first fiscal year is the one on screen."),
            col("Quantity", "Quantity", "int64", hidden=True, format="0"),
            col("Unit Price", "UnitPrice", "double", hidden=True, format="\\$#,0.00"),
            col("Extended Amount", "ExtendedAmount", "double", hidden=True, format="\\$#,0.00",
                doc="Quantity at list price, before any discount."),
            col("Sales Amount", "SalesAmount", "double", hidden=True, format="\\$#,0.00"),
            col("Total Product Cost", "TotalProductCost", "double", hidden=True, format="\\$#,0.00"),
        ],
    ),
}

RELATIONSHIPS = [
    ("Date to Sales", "Sales.'Order Date'", "Date.Date"),
    ("Product to Sales", "Sales.'Product Key'", "Product.'Product Key'"),
    ("Territory to Sales", "Sales.'Territory Key'", "Territory.'Territory Key'"),
    ("Reseller to Sales", "Sales.'Reseller Key'", "Reseller.'Reseller Key'"),
    ("Customer to Sales", "Sales.'Customer Key'", "Customer.'Customer Key'"),
    ("Channel to Sales", "Sales.'Channel Key'", "Channel.'Channel Key'"),
    ("Line Size to Sales", "Sales.'Line Size Key'", "'Line Size'.'Line Size Key'"),
    ("Sales Order to Sales", "Sales.'Order ID'", "'Sales Order'.'Order ID'"),
]


# --------------------------------------------------------------------------------------------
# DAX building blocks
# --------------------------------------------------------------------------------------------

# The fiscal year on screen, where the data ends, and the like-for-like cut. CutDay is the day of
# the fiscal year one year before the last order (Jun 15 2019 is day 350 of FY2019); it only bites
# when the year on screen is the one the data ends in.
PY_VARS = """VAR FY = SELECTEDVALUE('Date'[FY Sort])
VAR EndDate = CALCULATE(MAX(Sales[Order Date]), REMOVEFILTERS())
VAR EndFY = YEAR(EndDate) + IF(MONTH(EndDate) >= 7, 1, 0)
VAR FirstFY = CALCULATE(MIN(Sales[Account First FY]), REMOVEFILTERS())
VAR CutDay = IF(FY = EndFY, DATEDIFF(DATE(EndFY - 2, 7, 1), EDATE(EndDate, -12), DAY) + 1, 366)"""


def py(expr: str) -> str:
    """The same measure in the prior fiscal year, like for like. Replaces only the fiscal-year
    columns, so a category, region or month-of-year on the visual still applies. Never filters
    'Date'[Date]: on a marked date table that would clear every Date filter, month included."""
    return f"""{PY_VARS}
RETURN
    IF(
        NOT ISBLANK(FY) && FY > FirstFY,
        CALCULATE(
            {expr},
            REMOVEFILTERS('Date'[Fiscal Year], 'Date'[FY Sort]),
            'Date'[FY Sort] = FY - 1,
            'Date'[Day of FY] <= CutDay
        )
    )"""


def short_money(e: str) -> str:
    """'$51.9M', '$315.8K', '$5,904' as DAX text."""
    return (f'IF(ABS({e}) >= 1000000, FORMAT({e} / 1000000, "$#,0.0") & "M", '
            f'IF(ABS({e}) >= 1000, FORMAT({e} / 1000, "$#,0.0") & "K", FORMAT({e}, "$#,0")))')


def change_text(chg: str, kind: str) -> str:
    """'▲ +57.7%' / '▼ −1.6 pts' as SVG-safe DAX text (entities keep the URI ASCII)."""
    if kind == "pp":
        body = f'FORMAT(ABS({chg}) * 100, "0.0") & " pts"'
    else:
        body = f'FORMAT(ABS({chg}), "0.0%")'
    return f'IF({chg} >= 0, "&#9650; +", "&#9660; &#8722;") & {body}'


SPARK_W, SPARK_X0, SPARK_STEP, SPARK_Y0, SPARK_H = 220, 14, 5.6, 112, 24


def kpi_card(label: str, value: str, change: str, kind: str, series: str, zero_floor: bool = True) -> str:
    """A 220x120 KPI card: label, value, change on the prior year, and a 36-month sparkline with
    the year on screen drawn in navy over a paper band. Each month is computed with every Date
    filter removed, so the line always shows all three years whatever the year slicer says.
    (Not 'Start' for a variable name: it is a DAX keyword, from START AT.)"""
    lo = "MIN(0, MINX(P, [@v]))" if zero_floor else "MINX(P, [@v])"
    pt = (f'FORMAT({SPARK_X0} + \'Date\'[Month Index] * {SPARK_STEP}, "0.0") & "," & '
          f'FORMAT({SPARK_Y0} - ([@v] - Lo) / Span * {SPARK_H}, "0.0")')
    return f"""
VAR FY = SELECTEDVALUE('Date'[FY Sort])
VAR FirstFY = CALCULATE(MIN(Sales[Account First FY]), REMOVEFILTERS())
VAR Chg = {change}
VAR Lbl = [Comparison Label]
VAR Col = IF(Chg >= 0, "{mb.GOOD}", "{mb.BAD}")
VAR ChgTxt = IF(ISBLANK(Chg), "", {change_text("Chg", kind)})
VAR Tail = IF(ISBLANK(Chg), "No prior year to compare", " vs " & Lbl)
VAR T =
    ADDCOLUMNS(
        ALL('Date'[Month Index]),
        "@v", VAR i = 'Date'[Month Index] RETURN CALCULATE({series}, REMOVEFILTERS('Date'), 'Date'[Month Index] = i)
    )
VAR P = FILTER(T, NOT ISBLANK([@v]))
VAR Lo = {lo}
VAR Hi = MAXX(P, [@v])
VAR Span = IF(Hi = Lo, 1, Hi - Lo)
VAR SelStart = (FY - FirstFY) * 12
VAR Pts = CONCATENATEX(P, {pt}, " ", 'Date'[Month Index], ASC)
VAR PtsSel = CONCATENATEX(FILTER(P, 'Date'[Month Index] >= SelStart && 'Date'[Month Index] < SelStart + 12), {pt}, " ", 'Date'[Month Index], ASC)
VAR Svg =
    "<svg xmlns='http://www.w3.org/2000/svg' width='220' height='120' viewBox='0 0 220 120' font-family='Segoe UI, sans-serif'>"
        & "<rect x='0.5' y='0.5' width='219' height='119' rx='4' fill='#FFFFFF' stroke='{mb.RULE}'/><rect width='3' height='120' fill='{mb.GOLD}'/>"
        & "<text x='14' y='22' font-size='11' font-weight='700' fill='{mb.MUTED}'>{label}</text>"
        & "<text x='14' y='52' font-size='26' font-weight='700' fill='{mb.INK}'>" & {value} & "</text>"
        & "<text x='14' y='71' font-size='10.5'><tspan font-weight='700' fill='" & Col & "'>" & ChgTxt & "</tspan><tspan fill='{mb.MUTED}'>" & Tail & "</tspan></text>"
        & IF(NOT ISBLANK(FY), "<rect x='" & FORMAT({SPARK_X0} + SelStart * {SPARK_STEP} - 2.8, "0.0") & "' y='84' width='{11 * SPARK_STEP + 5.6:.1f}' height='30' fill='{mb.PAPER}'/>")
        & "<polyline points='" & Pts & "' fill='none' stroke='{mb.LIGHT}' stroke-width='1.3'/>"
        & "<polyline points='" & PtsSel & "' fill='none' stroke='{mb.NAVY}' stroke-width='2'/>"
        & "</svg>"
RETURN
    IF(NOT ISBLANK({value}), {mb.svg_uri()})"""


def simple_card(label: str, value: str, note: str, note_colour: str = f'"{mb.MUTED}"') -> str:
    """A 336x96 card: label, value and one line of explanation."""
    return f"""
VAR Note = {note}
VAR NoteCol = {note_colour}
VAR Svg =
    "<svg xmlns='http://www.w3.org/2000/svg' width='336' height='96' viewBox='0 0 336 96' font-family='Segoe UI, sans-serif'>"
        & "<rect x='0.5' y='0.5' width='335' height='95' rx='4' fill='#FFFFFF' stroke='{mb.RULE}'/><rect width='3' height='96' fill='{mb.GOLD}'/>"
        & "<text x='16' y='22' font-size='11' font-weight='700' fill='{mb.MUTED}'>{label}</text>"
        & "<text x='16' y='56' font-size='28' font-weight='700' fill='{mb.INK}'>" & {value} & "</text>"
        & "<text x='16' y='81' font-size='11.5' fill='" & NoteCol & "'>" & Note & "</text>"
        & "</svg>"
RETURN
    {mb.svg_uri()}"""


def cell_bar(value: str, pool_max: str, fill: str = mb.NAVY) -> str:
    """A 120x16 bar for a table cell, scaled to the largest row."""
    return f"""
VAR V = {value}
VAR Mx = {pool_max}
VAR W = DIVIDE(V, Mx) * 116
VAR Svg =
    "<svg xmlns='http://www.w3.org/2000/svg' width='120' height='16' viewBox='0 0 120 16'>"
        & "<rect x='0' y='3' width='" & FORMAT(MAX(W, 1), "0.0") & "' height='10' rx='1' fill='{fill}'/></svg>"
RETURN
    IF(V > 0, {mb.svg_uri()})"""


HEAT = ["#E8ECF3", "#BCC1D2", "#7C8598", "#3B4A66", mb.NAVY]


def heat_cell(pool: str, text: str, w: int, h: int = 22) -> str:
    """A heat-map matrix cell drawn as an SVG: a five-step navy ramp by the cell's share of the
    biggest cell in `pool`, with the value written on it (white on the three darkest steps).

    The pool is a CROSSJOIN of ALL over the matrix's own row and column fields AND their sort-by
    columns, so its axes come off and filters on other tables (channel) stay. Without the sort
    columns the visual's query (which groups by them) kept each cell pinned to itself: every cell
    was its own maximum, while a hand-written SUMMARIZECOLUMNS over the display columns looked
    right."""
    return f"""
VAR Me = [Sales]
VAR Mx = MAXX({pool}, CALCULATE([Sales]))
VAR R = DIVIDE(Me, Mx)
VAR Bg = SWITCH(TRUE(), R > 0.8, "{HEAT[4]}", R > 0.6, "{HEAT[3]}", R > 0.4, "{HEAT[2]}", R > 0.2, "{HEAT[1]}", "{HEAT[0]}")
VAR Fg = IF(R > 0.4, "#FFFFFF", "{mb.BODY}")
VAR Svg =
    "<svg xmlns='http://www.w3.org/2000/svg' width='{w}' height='{h}' viewBox='0 0 {w} {h}' font-family='Segoe UI, sans-serif'>"
        & "<rect x='1' y='1' width='{w - 2}' height='{h - 2}' rx='2' fill='" & Bg & "'/>"
        & "<text x='{w / 2:g}' y='{h / 2 + 4:g}' font-size='11' text-anchor='middle' fill='" & Fg & "'>" & {text} & "</text>"
        & "</svg>"
RETURN
    IF(NOT ISBLANK(Me), {mb.svg_uri()})"""


def m(name, dax, fmt=None, doc=None, category=None, folder=None, hidden=False):
    spec = mb.measure(name, dax, fmt, doc, category, hidden)
    spec["folder"] = folder
    return spec


def measures() -> list[dict]:
    out = [
        # ---- base ----
        m("Sales", "SUM(Sales[Sales Amount])", USD, "Sales Amount: after discount.", folder="Base"),
        m("Cost", "SUM(Sales[Total Product Cost])", USD, folder="Base"),
        m("Gross Profit", "[Sales] - [Cost]", USD, folder="Base"),
        m("Gross Margin %", "DIVIDE([Gross Profit], [Sales])", PCT, folder="Base"),
        m("Orders", "DISTINCTCOUNT(Sales[Order ID])", INT, "Distinct orders, not lines.", folder="Base"),
        m("Units", "SUM(Sales[Quantity])", INT, folder="Base"),
        m("Customers", "DISTINCTCOUNT(Sales[Account Key])", INT,
          "Distinct buying accounts: internet customers plus resellers.", folder="Base"),
        m("Average Order Value", "DIVIDE([Sales], [Orders])", USD, folder="Base"),
        m("Units per Order", "DIVIDE([Units], [Orders])", "0.0", folder="Base"),
        m("Sales per Customer", "DIVIDE([Sales], [Customers])", USD, folder="Base"),
        m("List Value", "SUM(Sales[Extended Amount])", USD, "Quantity at list price.", folder="Base"),
        m("Discount", "[List Value] - [Sales]", USD, "List value less sales amount.", folder="Base"),
        m("Discount Rate", "DIVIDE([Discount], [List Value])", PCT, folder="Base"),
        m("Discounted Sales",
          "CALCULATE([Sales], KEEPFILTERS(FILTER(Sales, Sales[Extended Amount] - Sales[Sales Amount] > 0.005)))",
          USD, "Sales on lines that carried any discount.", folder="Base"),
        m("Discounted Sales Share", "DIVIDE([Discounted Sales], [Sales])", PCT, folder="Base"),
        m("Sales $M", "DIVIDE([Sales], 1000000)", "0.0", "Sales in millions, for dense matrices.", folder="Base"),
        m("Sales Line", "[Sales] + 0", USD,
          "Sales with empty months as zero, so a month without reseller orders draws as a dip\n"
          "rather than a gap in the line.", folder="Base"),
        m("Sales Share of Channels", "DIVIDE([Sales], CALCULATE([Sales], REMOVEFILTERS(Channel)))", PCT,
          folder="Base"),
        m("Sales Share of Regions", "DIVIDE([Sales], CALCULATE([Sales], REMOVEFILTERS(Territory)))", PCT,
          folder="Base"),

        # ---- prior year, like for like ----
        m("Sales PY", py("[Sales]"), USD,
          "Prior fiscal year. When the year on screen is the one the data ends in (FY2020, to 15 Jun),\n"
          "the prior year is cut at the same day of its fiscal year. Blank in the first year.",
          folder="Prior year"),
        m("Gross Profit PY", py("[Gross Profit]"), USD, folder="Prior year"),
        m("Gross Margin % PY", "DIVIDE([Gross Profit PY], [Sales PY])", PCT, folder="Prior year"),
        m("Orders PY", py("[Orders]"), INT, folder="Prior year"),
        m("Customers PY", py("[Customers]"), INT, folder="Prior year"),
        m("Average Order Value PY", "DIVIDE([Sales PY], [Orders PY])", USD, folder="Prior year"),
        m("Sales vs PY", "VAR P = [Sales PY] RETURN IF(NOT ISBLANK(P), [Sales] - P)", USD, folder="Prior year"),
        m("Sales vs PY %", "VAR P = [Sales PY] RETURN IF(NOT ISBLANK(P), DIVIDE([Sales] - P, P))", PCT,
          folder="Prior year"),
        m("Gross Margin Change",
          "VAR P = [Gross Margin % PY] RETURN IF(NOT ISBLANK(P), [Gross Margin %] - P)", "0.0%",
          "In percentage points, shown as a percentage.", folder="Prior year"),
        m("Sales vs PY Label",
          f"""{PY_VARS}
VAR S = [Sales]
VAR P = [Sales PY]
RETURN
    SWITCH(
        TRUE(),
        ISBLANK(FY) || FY <= FirstFY || ISBLANK(S), BLANK(),
        ISBLANK(P) || P <= 0 || S / P > 20, "new",
        IF(S >= P, "+", "-") & FORMAT(ABS(S / P - 1), "0.0%")
    )""",
          doc="Growth as text for tables: 'new' where last year was nil or tiny, so a product launched\n"
              "late in FY2019 does not read +18,941%.", folder="Prior year"),
        m("Growth Colour", f"VAR C = [Sales vs PY] RETURN IF(NOT ISBLANK(C), {mb.tone('C')})",
          doc="Green when sales rose on the prior year, red when they fell.", folder="Prior year"),

        # ---- customers ----
        m("New Customers",
          "VAR FY = SELECTEDVALUE('Date'[FY Sort])\n"
          "RETURN\n"
          "    IF(NOT ISBLANK(FY), CALCULATE([Customers], KEEPFILTERS(Sales[Account First FY] = FY)) + 0)",
          INT, "Accounts whose first order in the data falls in the fiscal year on screen. Every account\n"
               "is new in FY2018, where the data starts.", folder="Customers"),
        m("Returning Customers", "[Customers] - [New Customers]", INT, folder="Customers"),
        m("New Customer Share", "DIVIDE([New Customers], [Customers])", PCT, folder="Customers"),
        m("Sales from Returning Customers",
          "VAR FY = SELECTEDVALUE('Date'[FY Sort])\n"
          "RETURN\n"
          "    IF(NOT ISBLANK(FY), CALCULATE([Sales], KEEPFILTERS(Sales[Account First FY] < FY)) + 0)",
          USD, folder="Customers"),
        m("Returning Sales Share", "DIVIDE([Sales from Returning Customers], [Sales])", PCT, folder="Customers"),

        # ---- labels ----
        m("Comparison Label",
          f"""{PY_VARS}
RETURN
    IF(
        NOT ISBLANK(FY) && FY > FirstFY,
        "FY" & (FY - 1) & IF(FY = EndFY, " to " & FORMAT(EDATE(EndDate, -12), "d mmm"), "")
    )""", doc="'FY2019 to 15 Jun' or 'FY2018'; blank in the first year.", folder="Labels"),
        m("Period Label",
          f"""{PY_VARS}
RETURN
    IF(
        NOT ISBLANK(FY),
        "FY" & FY & " (Jul " & (FY - 1) & " to " & IF(FY = EndFY, FORMAT(EndDate, "d mmm yyyy"), "Jun " & FY) & ")"
    )""", folder="Labels"),
        m("Scope Label",
          """VAR Ch = IF(HASONEVALUE(Channel[Channel]), SELECTEDVALUE(Channel[Channel]) & " channel", "both channels")
RETURN Ch""", folder="Labels"),
        m("Title Standfirst",
          f"""VAR S = [Sales]
VAR P = [Sales PY]
VAR G = DIVIDE(S - P, P)
VAR Change =
    IF(
        ISBLANK(P),
        ", the first year in the data",
        ", " & IF(G >= 0, "up ", "down ") & FORMAT(ABS(G), "0.0%") & " on " & [Comparison Label]
    )
RETURN
    [Period Label] & ", " & [Scope Label] & ": sales of " & {short_money("S")} & Change
        & ", from " & FORMAT([Orders], "#,0") & " orders at a gross margin of " & FORMAT([Gross Margin %], "0.0%") & "."
""".strip(), folder="Labels"),
        m("Subtitle Waterfall",
          """VAR L = [Comparison Label]
RETURN
    IF(
        ISBLANK(L),
        "FY2018 is the first year in the data, so there is nothing to compare",
        "Change in sales on " & L & ", by category. Green rose, red fell"
    )""", folder="Labels"),
        m("Subtitle Growth",
          """VAR L = [Comparison Label]
RETURN
    IF(ISBLANK(L), "FY2018 is the first year in the data, so there is nothing to compare",
        "Sales growth on " & L & ". Green rose, red fell")""", folder="Labels"),
        m("Subtitle Margin",
          """VAR L = [Comparison Label]
RETURN
    IF(ISBLANK(L), "FY2018 has no prior year; this year's margin only",
        "The 8 biggest subcategories: gross margin in " & L & " and this year")""", folder="Labels"),
        m("Subtitle Discounts",
          f"""VAR D = [Discount]
RETURN
    IF(
        D > 1,
        FORMAT([Discounted Sales Share], "0.0%") & " of sales carried a discount, worth " & {short_money("D")}
            & ". Bubble size is the discount",
        "No discounts here: internet sales are always at list price"
    )""", folder="Labels"),
    ]

    # ---- KPI cards (summary page) ----
    cards = [
        ("Card Sales", "TOTAL SALES", short_money("[Sales]"), "[Sales vs PY %]", "pct", "[Sales]", True),
        ("Card Gross Profit", "GROSS PROFIT", short_money("[Gross Profit]"),
         "VAR P = [Gross Profit PY] RETURN IF(NOT ISBLANK(P), DIVIDE([Gross Profit] - P, ABS(P)))", "pct",
         "[Gross Profit]", True),
        ("Card Gross Margin", "GROSS MARGIN", 'FORMAT([Gross Margin %], "0.0%")', "[Gross Margin Change]", "pp",
         "[Gross Margin %]", False),
        ("Card Orders", "ORDERS", 'FORMAT([Orders], "#,0")',
         "VAR P = [Orders PY] RETURN IF(NOT ISBLANK(P), DIVIDE([Orders] - P, P))", "pct", "[Orders]", True),
        ("Card Average Order", "AVERAGE ORDER VALUE", 'FORMAT([Average Order Value], "$#,0")',
         "VAR P = [Average Order Value PY] RETURN IF(NOT ISBLANK(P), DIVIDE([Average Order Value] - P, P))", "pct",
         "[Average Order Value]", True),
        ("Card Customers", "CUSTOMERS", 'FORMAT([Customers], "#,0")',
         "VAR P = [Customers PY] RETURN IF(NOT ISBLANK(P), DIVIDE([Customers] - P, P))", "pct", "[Customers]", True),
    ]
    for name, label, value, change, kind, series, floor in cards:
        out.append(m(name, kpi_card(label, value, change, kind, series, floor), category="ImageUrl", folder="Cards"))

    # ---- customer cards ----
    cust_change = ("VAR P = [Customers PY] VAR C = DIVIDE([Customers] - P, P) RETURN "
                   "IF(ISBLANK(P), \"No prior year to compare\", "
                   "IF(C >= 0, \"Up \", \"Down \") & FORMAT(ABS(C), \"0.0%\") & \" on \" & [Comparison Label])")
    cust_col = (f'VAR P = [Customers PY] RETURN IF(ISBLANK(P), "{mb.MUTED}", '
                f'IF([Customers] >= P, "{mb.GOOD}", "{mb.BAD}"))')
    out += [
        m("Card Customers Total", simple_card("CUSTOMERS", 'FORMAT([Customers], "#,0")', cust_change, cust_col),
          category="ImageUrl", folder="Cards"),
        m("Card New Customers", simple_card("NEW CUSTOMERS", 'FORMAT([New Customers], "#,0")',
                                            'FORMAT([New Customer Share], "0.0%") & " of customers placed a first order"'),
          category="ImageUrl", folder="Cards"),
        m("Card Returning Customers", simple_card("RETURNING CUSTOMERS", 'FORMAT([Returning Customers], "#,0")',
                                                  '"First ordered in an earlier fiscal year"'),
          category="ImageUrl", folder="Cards"),
        m("Card Returning Sales", simple_card("SALES FROM RETURNING CUSTOMERS",
                                              short_money("[Sales from Returning Customers]"),
                                              'FORMAT([Returning Sales Share], "0.0%") & " of this year\'s sales"'),
          category="ImageUrl", folder="Cards"),
    ]

    # ---- heat colours and table bars ----
    cells = {
        # Each axis column comes with its sort-by column: the matrix query groups by both, so a
        # pool over the display column alone leaves every cell filtered to itself.
        "Season": ("CROSSJOIN(ALL('Date'[Fiscal Year], 'Date'[FY Sort]), ALL('Date'[Month], 'Date'[Month No]))",
                   'FORMAT(Me / 1000000, "0.0")', 30),
        "Buyer": ("CROSSJOIN(ALL(Reseller[Buyer Type], Reseller[Buyer Type Sort]), "
                  "ALL('Product'[Category], 'Product'[Category Sort]))", short_money("Me"), 66),
        "Region": ("CROSSJOIN(ALL(Territory[Region]), ALL('Product'[Category], 'Product'[Category Sort]))",
                   short_money("Me"), 110, 18),
    }
    for key, (pool, text, width, *height) in cells.items():
        out.append(m(f"Cell {key}", heat_cell(pool, text, width, *height), category="ImageUrl", folder="Formatting"))

    out += [
        m("Bar Product Sales", cell_bar("[Sales]", "MAXX(ALL('Product'[Product]), [Sales])"),
          category="ImageUrl", folder="Formatting",
          doc="Pooled with ALL: the table's Top 10 filter would narrow ALLSELECTED to the rows kept."),
        m("Bar Subcategory Sales", cell_bar("[Sales]", "MAXX(ALLSELECTED('Product'[Subcategory]), [Sales])"),
          category="ImageUrl", folder="Formatting"),
        m("Bar Region Sales", cell_bar("[Sales]", "MAXX(ALLSELECTED(Territory[Region]), [Sales])"),
          category="ImageUrl", folder="Formatting"),
        m("Bar Discount", cell_bar("[Discount]", "MAXX(ALLSELECTED('Line Size'[Line Size]), [Discount])", mb.GOLD),
          category="ImageUrl", folder="Formatting"),
        m("Model Profit Colour", f'IF([Gross Profit] < 0, "{mb.SLATE}", "{mb.NAVY}")', folder="Formatting"),
    ]

    # ---- ranks (ties broken alphabetically so a Top N never shows N+1 rows) ----
    def rank(name, col, value, valid, fmt="0", direction=">", exclude=None):
        """valid is a measure test, true in both the row and the filter context; exclude names a
        member of col that never gets a rank (tested by name in each context)."""
        pool_test = valid + (f' && {col} <> "{exclude}"' if exclude else "")
        me_test = valid + (f' && Mine <> "{exclude}"' if exclude else "")
        return m(name, f"""VAR Me = {value}
VAR Mine = SELECTEDVALUE({col})
VAR Pool = FILTER(ALL({col}), {pool_test})
RETURN
    IF(
        HASONEVALUE({col}) && {me_test},
        COUNTROWS(FILTER(Pool, {value} {direction} Me))
            + COUNTROWS(FILTER(Pool, {value} = Me && {col} < Mine)) + 1
    )""", fmt, folder="Ranks")

    out += [
        rank("Product Rank", "'Product'[Product]", "[Sales]", "[Sales] > 0"),
        rank("Subcategory Rank", "'Product'[Subcategory]", "[Sales]", "[Sales] > 0"),
        rank("Reseller Rank", "Reseller[Reseller]", "[Sales]", "[Sales] > 0", exclude="(Internet sale)"),
        rank("Model Profit Rank", "'Product'[Model]", "[Gross Profit]", "NOT ISBLANK([Sales])"),
        rank("Model Loss Rank", "'Product'[Model]", "[Gross Profit]", "NOT ISBLANK([Sales])", direction="<"),
        m("Model Shown",
          "IF(NOT ISBLANK([Sales]) && ([Model Profit Rank] <= 7 || [Model Loss Rank] <= 3), 1)", "0", folder="Ranks",
          doc="1 for the 7 most and 3 least profitable models: the chart filters on it. The sales test\n"
              "matters: a model with no sales has a blank rank, and BLANK <= 12 is true."),
    ]
    return out


# --------------------------------------------------------------------------------------------
# Writers
# --------------------------------------------------------------------------------------------

RAW = "https://raw.githubusercontent.com/owilliams84/powerbi-adventureworks-performance/main/data/"
LOCAL = False   # --local: read data/ on this machine instead of GitHub (offline builds)


def m_partition(table: str, spec: dict) -> list[str]:
    """Reads the CSV from GitHub raw by default, so the published model refreshes in the Service
    with anonymous Web credentials; --local reads data/ on this machine."""
    if LOCAL:
        path = str((DATA / spec["file"]).resolve()).replace("\\", "\\\\")
        src = f'File.Contents("{path}")'
    else:
        src = f'Web.Contents("{RAW}{spec["file"]}")'
    types = ", ".join(f'{{"{c["source"]}", {M_TYPE[c["dtype"]]}}}' for c in spec["columns"])
    return [
        f"\tpartition {q(table)} = m",
        "\t\tmode: import",
        "\t\tsource =",
        "\t\t\t\tlet",
        f'\t\t\t\t    Source = Csv.Document({src}, [Delimiter=",", Columns={len(spec["columns"])}, '
        "Encoding=65001, QuoteStyle=QuoteStyle.Csv]),",
        '\t\t\t\t    #"Promoted Headers" = Table.PromoteHeaders(Source, [PromoteAllScalars=true]),',
        f'\t\t\t\t    #"Applied Types" = Table.TransformColumnTypes(#"Promoted Headers", {{{types}}}, "en-US")',
        "\t\t\t\tin",
        '\t\t\t\t    #"Applied Types"',
    ]


def write_table(name: str, spec: dict) -> None:
    lines = mb.doc(spec.get("doc"), "") + [f"table {q(name)}", f"\tlineageTag: {tag('table', name)}"]
    if spec.get("date_table"):
        lines.append("\tdataCategory: Time")
    for c in spec["columns"]:
        lines.append("")
        lines += mb.doc(c.get("doc"), "\t")
        lines += [f"\tcolumn {q(c['name'])}", f"\t\tdataType: {c['dtype']}"]
        if c.get("hidden"):
            lines.append("\t\tisHidden")
        if c.get("key"):
            lines.append("\t\tisKey")
        if c.get("format"):
            lines.append(f"\t\tformatString: {c['format']}")
        lines += [f"\t\tlineageTag: {tag('column', name, c['name'])}", "\t\tsummarizeBy: none",
                  f"\t\tsourceColumn: {c['source']}"]
        if c.get("sortBy"):
            lines.append(f"\t\tsortByColumn: {q(c['sortBy'])}")
    lines += [""] + m_partition(name, spec) + ["", "\tannotation PBI_ResultType = Table"]
    mb.write_lines(DEFN / "tables" / f"{name}.tmdl", lines)


def write_metrics(specs: list[dict]) -> None:
    lines = mb.measure_table_tmdl(
        "Metrics", specs, tag,
        "Measure-only table: every number on the report comes from here. The hidden column\n"
        "exists because a table needs one.")
    # measure_table_tmdl has no display folders; add them after each measure's format line.
    out, current = [], None
    for line in lines:
        out.append(line)
        if line.startswith("\tmeasure "):
            current = next(s for s in specs if line.startswith(f"\tmeasure {q(s['name'])} ="))
        if current and line.startswith("\t\tlineageTag:") and current.get("folder"):
            out.insert(len(out) - 1, f"\t\tdisplayFolder: {current['folder']}")
            current = None
    mb.write_lines(DEFN / "tables" / "Metrics.tmdl", out)


def rmtree_retry(path: Path) -> None:
    for attempt in range(5):
        try:
            shutil.rmtree(path)
            return
        except FileNotFoundError:
            return
        except PermissionError:
            if attempt == 4:
                shutil.rmtree(path, ignore_errors=True)
            else:
                time.sleep(0.5)


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.strip("\n") + "\n", encoding="utf-8", newline="\n")


def main() -> None:
    global LOCAL
    LOCAL = "--local" in sys.argv[1:]
    rmtree_retry(DEFN)
    (DEFN / "tables").mkdir(parents=True, exist_ok=True)
    for name, spec in TABLES.items():
        write_table(name, spec)
    specs = measures()
    write_metrics(specs)

    mb.write_lines(DEFN / "database.tmdl", ["database", "\tcompatibilityLevel: 1606"])
    tables = list(TABLES) + ["Metrics"]
    order = ", ".join(f'"{t}"' for t in TABLES)
    mb.write_lines(DEFN / "model.tmdl", [
        "model Model",
        "\tculture: en-US",
        "\tdefaultPowerBIDataSourceVersion: powerBI_V3",
        "\tdiscourageImplicitMeasures",
        "\tsourceQueryCulture: en-US",
        "",
        f"annotation PBI_QueryOrder = [{order}]",
        "",
        "annotation __PBI_TimeIntelligenceEnabled = 0",
        "",
        'annotation PBI_ProTooling = ["DevMode"]',
        "",
    ] + [f"ref table {q(t)}" for t in tables])

    rel: list[str] = []
    for i, (name, frm, to) in enumerate(RELATIONSHIPS):
        rel += ([""] if i else []) + [f"relationship {q(name)}", f"\tfromColumn: {frm}", f"\ttoColumn: {to}"]
    mb.write_lines(DEFN / "relationships.tmdl", rel)

    write_text(MODEL / "definition.pbism", """
{
  "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/semanticModel/definitionProperties/1.0.0/schema.json",
  "version": "4.2",
  "settings": {"qnaEnabled": false}
}""")
    write_text(MODEL / ".platform", f"""
{{
  "$schema": "https://developer.microsoft.com/json-schemas/fabric/gitIntegration/platformProperties/2.0.0/schema.json",
  "metadata": {{"type": "SemanticModel", "displayName": "{NAME}"}},
  "config": {{"version": "2.0", "logicalId": "{tag('platform', 'model')}"}}
}}""")
    print(f"{len(tables)} tables, {sum(len(s['columns']) for s in TABLES.values())} columns, "
          f"{len(specs)} measures, {len(RELATIONSHIPS)} relationships -> {MODEL.name}")


if __name__ == "__main__":
    main()
