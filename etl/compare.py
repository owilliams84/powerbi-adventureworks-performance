"""Cross-check the model against pandas: the same breakdowns the pages show, computed both ways.

    python etl/compare.py            (Power BI Desktop must have the project open and refreshed)
    python etl/compare.py --service  (the published dataset, via executeQueries)

Each check runs one DAX query over the open model (etl/run_dax.ps1) with the slicers pinned by
TREATAS, recomputes it from data/*.csv, and compares every cell. Prints a line per check and the
first mismatches; exits 1 if anything differs.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
D = ROOT / "data"

s = pd.read_csv(D / "fact_sales.csv", parse_dates=["OrderDate"])
dates = pd.read_csv(D / "dim_date.csv", parse_dates=["Date"])
s = s.merge(dates[["Date", "FYSort", "DayOfFY", "Month"]], left_on="OrderDate", right_on="Date")
s = s.merge(pd.read_csv(D / "dim_product.csv"), on="ProductKey")
s = s.merge(pd.read_csv(D / "dim_territory.csv"), on="SalesTerritoryKey")
s = s.merge(pd.read_csv(D / "dim_reseller.csv")[["ResellerKey", "Reseller", "BuyerType"]], on="ResellerKey")
s = s.merge(pd.read_csv(D / "dim_line_size.csv"), on="LineSizeKey")
s = s.merge(pd.read_csv(D / "dim_order.csv")[["OrderID", "OrderSize"]], on="OrderID")
s["Channel"] = s.ChannelKey.map({1: "Internet", 2: "Reseller"})
END = s.OrderDate.max()
CUT = (END - pd.DateOffset(years=1) - pd.Timestamp(END.year - 2, 7, 1)).days + 1   # day of FY2019


def scope(fy: int, channel: str | None = None, prior: bool = False) -> pd.DataFrame:
    """Rows for a fiscal year (and channel); prior=True gives the like-for-like prior year."""
    x = s[s.Channel == channel] if channel else s
    if prior:
        x = x[x.FYSort == fy - 1]
        return x[x.DayOfFY <= CUT] if fy == END.year + (END.month >= 7) else x
    return x[x.FYSort == fy]


SERVICE = "--service" in sys.argv[1:]   # query the published dataset instead of Desktop


def dax_service(query: str) -> list:
    """executeQueries on the dataset publish_to_service.py recorded in etl/.service.json."""
    import urllib.request
    svc = json.loads((ROOT / "etl" / ".service.json").read_text(encoding="utf-8"))
    tok = subprocess.run('az account get-access-token --resource "https://analysis.windows.net/powerbi/api" '
                         '--query accessToken -o tsv', shell=True, check=True, capture_output=True, text=True).stdout.strip()
    base = "https://api.powerbi.com/v1.0/myorg" + ("" if svc["personal"] else f"/groups/{svc['workspace']}")
    req = urllib.request.Request(f"{base}/datasets/{svc['model']}/executeQueries", method="POST",
                                 data=json.dumps({"queries": [{"query": query}]}).encode(),
                                 headers={"Authorization": f"Bearer {tok}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req) as r:
        out = json.loads(r.read())
    res = out["results"][0]
    if "error" in res:
        raise RuntimeError(res["error"])
    return res["tables"][0]["rows"]


def dax(query: str) -> pd.DataFrame:
    if SERVICE:
        df = pd.DataFrame(dax_service(query))
        df.columns = [c.split("[")[-1].rstrip("]") for c in df.columns]
        return df
    with tempfile.TemporaryDirectory() as tmp:
        qf, of = Path(tmp) / "q.dax", Path(tmp) / "out.json"
        qf.write_text(query, encoding="utf-8")
        subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                        str(ROOT / "etl" / "run_dax.ps1"), "-Query", str(qf), "-Out", str(of)],
                       check=True, capture_output=True)
        data = json.loads(of.read_text(encoding="utf-8-sig"))
    if isinstance(data, dict) and "error" in data:
        raise RuntimeError(data["error"])
    df = pd.DataFrame(data)
    df.columns = [c.split("[")[-1].rstrip("]") for c in df.columns]
    return df


def filt(fy: int, channel: str | None) -> str:
    out = f'TREATAS({{"FY{fy}"}}, \'Date\'[Fiscal Year])'
    return out + (f', TREATAS({{"{channel}"}}, Channel[Channel])' if channel else "")


FAILS = 0


def check(label: str, got: pd.DataFrame, want: pd.DataFrame, key: list[str], tol: float = 0.01) -> None:
    global FAILS
    got = got.copy()
    for c in want.columns:          # executeQueries drops a column that is blank on every row,
        if c not in got.columns:    # and an empty result has no columns at all
            got[c] = pd.Series(dtype=want[c].dtype) if got.empty else None
    m = want.merge(got, on=key, how="outer", suffixes=("_pd", "_dax"), indicator=True)
    bad = []
    for c in [c for c in want.columns if c not in key]:
        a, b = m[f"{c}_pd"].astype(float).fillna(0), m[f"{c}_dax"].astype(float).fillna(0)
        diff = (a - b).abs() > a.abs() * 1e-7 + 0.005 * tol   # relative for money, absolute for ratios
        bad += [f"{c} @ {tuple(r[k] for k in key)}: pandas {a[i]:.4f} dax {b[i]:.4f}" for i, r in m[diff].iterrows()]
    missing = m[m._merge != "both"]
    n = len(m) * (len(want.columns) - len(key))
    FAILS += len(bad) + len(missing)
    print(f"  {'OK ' if not bad and missing.empty else 'BAD'} {label:58s} {n:4d} cells")
    for line in bad[:5]:
        print("      ", line)
    if not missing.empty:
        print("       rows on one side only:", missing[key].values.tolist()[:5])


def run(fy: int, channel: str | None) -> None:
    cy, py = scope(fy, channel), scope(fy, channel, prior=True)
    f = filt(fy, channel)
    print(f"FY{fy}, {channel or 'both channels'}")

    # Category: sales, prior year, margin (waterfall, treemap groups)
    g = cy.groupby("Category").agg(Sales=("SalesAmount", "sum"), Cost=("TotalProductCost", "sum")).reset_index()
    g["GM"] = 1 - g.Cost / g.Sales
    g["PY"] = g.Category.map(py.groupby("Category").SalesAmount.sum()) if fy > 2018 else None
    got = dax(f"""EVALUATE SUMMARIZECOLUMNS('Product'[Category], {f},
        "Sales", [Sales], "GM", [Gross Margin %], "PY", [Sales PY])""")
    check("category: sales, margin, prior year", got, g[["Category", "Sales", "GM", "PY"]], ["Category"])

    # Region: sales and growth (regions table, growth bars)
    r = cy.groupby("Region").SalesAmount.sum().rename("Sales").reset_index()
    if fy > 2018:
        p = py.groupby("Region").SalesAmount.sum()
        r["Growth"] = r.Sales / r.Region.map(p) - 1
    got = dax(f"""EVALUATE SUMMARIZECOLUMNS(Territory[Region], {f}, "Sales", [Sales]{', "Growth", [Sales vs PY %]' if fy > 2018 else ''})""")
    check("region: sales, growth", got, r, ["Region"])

    # Customers: total, new, returning, sales from returning (customer cards)
    acc = cy.groupby("AccountKey").agg(First=("AccountFirstFY", "first"), Sales=("SalesAmount", "sum"))
    want = pd.DataFrame([{"k": 1, "Customers": len(acc), "New": (acc.First == fy).sum(),
                          "Returning": (acc.First < fy).sum(), "RetSales": acc[acc.First < fy].Sales.sum()}])
    got = dax(f"""EVALUATE CALCULATETABLE(ROW("k", 1, "Customers", [Customers], "New", [New Customers],
        "Returning", [Returning Customers], "RetSales", [Sales from Returning Customers]), {f})""")
    check("customers: total, new, returning", got, want, ["k"])

    # Top 10 products by rank (top products table)
    t = cy.groupby("Product").SalesAmount.sum().rename("Sales").reset_index()
    t = t.sort_values(["Sales", "Product"], ascending=[False, True]).head(10)
    t["Rank"] = range(1, len(t) + 1)
    got = dax(f"""EVALUATE FILTER(SUMMARIZECOLUMNS('Product'[Product], {f}, "Rank", [Product Rank], "Sales", [Sales]),
        [Rank] <= 10)""")
    check("top 10 products: rank and sales", got, t, ["Product"])

    # Buyer type x category (who buys what)
    b = cy.groupby(["BuyerType", "Category"]).SalesAmount.sum().rename("Sales").reset_index()
    got = dax(f"""EVALUATE SUMMARIZECOLUMNS(Reseller[Buyer Type], 'Product'[Category], {f}, "Sales", [Sales])""")
    check("buyer type x category", got.rename(columns={"Buyer Type": "BuyerType"}), b, ["BuyerType", "Category"])

    # Line size: sales and discount (volume discounts table)
    v = cy.groupby("LineSize").agg(Sales=("SalesAmount", "sum"), Ext=("ExtendedAmount", "sum")).reset_index()
    v["Discount"] = v.Ext - v.Sales
    got = dax(f"""EVALUATE SUMMARIZECOLUMNS('Line Size'[Line Size], {f}, "Sales", [Sales], "Discount", [Discount])""")
    check("line size: sales and discount", got.rename(columns={"Line Size": "LineSize"}),
          v[["LineSize", "Sales", "Discount"]], ["LineSize"], tol=1)

    # Order size: sales and orders (order size chart)
    o = cy.groupby("OrderSize").agg(Sales=("SalesAmount", "sum"), Orders=("OrderID", "nunique")).reset_index()
    got = dax(f"""EVALUATE SUMMARIZECOLUMNS('Sales Order'[Order Size], {f}, "Sales", [Sales], "Orders", [Orders])""")
    check("order size: sales and orders", got.rename(columns={"Order Size": "OrderSize"}), o, ["OrderSize"])

    # Models shown: 7 most and 3 least profitable (gross profit chart)
    mo = cy.groupby("Model").agg(S=("SalesAmount", "sum"), C=("TotalProductCost", "sum"))
    mo["GP"] = mo.S - mo.C
    ranked = mo.sort_values(["GP"], ascending=False)
    keep = pd.concat([ranked.head(7), ranked.tail(3)]).index.unique()
    want = mo.loc[keep, ["GP"]].reset_index()
    got = dax(f"""EVALUATE FILTER(SUMMARIZECOLUMNS('Product'[Model], {f}, "GP", [Gross Profit], "Shown", [Model Shown]),
        [Shown] = 1)""")
    check("models shown and their gross profit", got[["Model", "GP"]], want, ["Model"])


def kpis(x: pd.DataFrame, p: pd.DataFrame | None) -> dict:
    out = {"k": 1, "Sales": x.SalesAmount.sum(), "GP": x.SalesAmount.sum() - x.TotalProductCost.sum(),
           "Orders": x.OrderID.nunique(), "Customers": x.AccountKey.nunique()}
    out["GM"] = out["GP"] / out["Sales"]
    out["AOV"] = out["Sales"] / out["Orders"]
    if p is not None:
        out["PY"] = p.SalesAmount.sum()
    return out


KPI_DAX = '"Sales", [Sales], "GP", [Gross Profit], "Orders", [Orders], "Customers", [Customers], ' \
          '"GM", [Gross Margin %], "AOV", [Average Order Value]'


def run_drill(fy: int, channel: str | None, region: str, model: str) -> None:
    """The drill-through pages: Region detail pinned to `region`, Product detail to `model`."""
    cy, py = scope(fy, channel), scope(fy, channel, prior=True)
    has_py = fy > 2018
    print(f"Drill-through FY{fy}, {channel or 'both channels'}: region {region}, model {model}")

    # Region detail: KPI cards, category last year / this year, top 10 products, top 8 subcategories' growth
    f = filt(fy, channel) + f', TREATAS({{"{region}"}}, Territory[Region])'
    rc, rp = cy[cy.Region == region], py[py.Region == region]
    got = dax(f"""EVALUATE CALCULATETABLE(ROW("k", 1, {KPI_DAX}{', "PY", [Sales PY]' if has_py else ''}), {f})""")
    check(f"region detail: KPI cards ({region})", got, pd.DataFrame([kpis(rc, rp if has_py else None)]), ["k"])

    g = rc.groupby("Category").SalesAmount.sum().rename("Sales").reset_index()
    if has_py:
        g["PY"] = g.Category.map(rp.groupby("Category").SalesAmount.sum())
    got = dax(f"""EVALUATE SUMMARIZECOLUMNS('Product'[Category], {f}, "Sales", [Sales]{', "PY", [Sales PY]' if has_py else ''})""")
    check("region detail: category this year and last", got, g, ["Category"])

    t = rc.groupby("Product").SalesAmount.sum().rename("Sales").reset_index()
    t = t.sort_values(["Sales", "Product"], ascending=[False, True]).head(10)
    t["Rank"] = range(1, len(t) + 1)
    got = dax(f"""EVALUATE FILTER(SUMMARIZECOLUMNS('Product'[Product], {f}, "Rank", [Product Rank], "Sales", [Sales]),
        [Rank] <= 10)""")
    check("region detail: top 10 products", got, t, ["Product"])

    if has_py:
        sc = rc.groupby("Subcategory").SalesAmount.sum().rename("Sales")
        top = sc.reset_index().sort_values(["Sales", "Subcategory"], ascending=[False, True]).head(8)
        ratio = top.Sales / top.Subcategory.map(rp.groupby("Subcategory").SalesAmount.sum())
        top["Growth"] = (ratio - 1).where(ratio <= 20)     # 'new' launches are left out of the chart
        got = dax(f"""EVALUATE FILTER(SUMMARIZECOLUMNS('Product'[Subcategory], {f}, "Sales", [Sales],
            "Growth", [Sales vs PY % Established], "Rank", [Subcategory Rank]), [Rank] >= 1 && [Rank] <= 8)""")
        check("region detail: top 8 subcategories' growth", got.drop(columns=["Rank"]),
              top[["Subcategory", "Sales", "Growth"]], ["Subcategory"])

    # Product detail: KPI cards, regions, buyer types, products in the model, margin by month
    f = filt(fy, channel) + f', TREATAS({{"{model}"}}, \'Product\'[Model])'
    mc, mp = cy[cy.Model == model], py[py.Model == model]
    got = dax(f"""EVALUATE CALCULATETABLE(ROW("k", 1, {KPI_DAX}{', "PY", [Sales PY]' if has_py else ''}), {f})""")
    check(f"product detail: KPI cards ({model})", got, pd.DataFrame([kpis(mc, mp if has_py else None)]), ["k"])

    r = mc.groupby("Region").SalesAmount.sum().rename("Sales").reset_index()
    got = dax(f"""EVALUATE SUMMARIZECOLUMNS(Territory[Region], {f}, "Sales", [Sales])""")
    check("product detail: sales by region", got, r, ["Region"])

    b = mc.groupby("BuyerType").agg(Sales=("SalesAmount", "sum"), Orders=("OrderID", "nunique")).reset_index()
    got = dax(f"""EVALUATE SUMMARIZECOLUMNS(Reseller[Buyer Type], {f}, "Sales", [Sales], "Orders", [Orders])""")
    check("product detail: buyer types", got.rename(columns={"Buyer Type": "BuyerType"}), b, ["BuyerType"])

    i = mc.groupby("Product").agg(Sales=("SalesAmount", "sum"), Units=("Quantity", "sum")).reset_index()
    got = dax(f"""EVALUATE SUMMARIZECOLUMNS('Product'[Product], {f}, "Sales", [Sales], "Units", [Units])""")
    check("product detail: sizes and colours", got, i, ["Product"])

    # Months with sales in either year: June 2020 can be empty while June 2019 sold.
    mm = mc.groupby("Month").agg(S=("SalesAmount", "sum"), C=("TotalProductCost", "sum"))
    if has_py:
        pm = mp.groupby("Month").agg(S=("SalesAmount", "sum"), C=("TotalProductCost", "sum"))
        mm = mm.reindex(mm.index.union(pm.index))
        mm["GMPY"] = 1 - pm.C / pm.S
    mm["GM"] = 1 - mm.C / mm.S
    want = mm.drop(columns=["S", "C"]).reset_index()
    got = dax(f"""EVALUATE SUMMARIZECOLUMNS('Date'[Month], {f}, "GM", [Gross Margin %]{', "GMPY", [Gross Margin % PY]' if has_py else ''})""")
    check("product detail: margin by month vs prior year", got, want, ["Month"])


if __name__ == "__main__":
    for fy, ch in [(2020, None), (2019, "Internet"), (2018, "Reseller")]:
        run(fy, ch)
    for fy, ch, reg, mod in [(2020, None, "Southwest", "Mountain-200"), (2019, "Reseller", "France", "Road-250"),
                             (2020, "Internet", "Australia", "Touring-1000")]:
        run_drill(fy, ch, reg, mod)
    print(f"\n{'ALL MATCH' if FAILS == 0 else f'{FAILS} MISMATCHES'}")
    sys.exit(1 if FAILS else 0)
