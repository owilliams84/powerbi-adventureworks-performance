"""Shape Microsoft's AdventureWorks Sales.xlsx sample into star-schema CSVs for the model.

    python -I etl/prepare_data.py "../data/AdventureWorks Sales.xlsx"

The workbook is already close to a star schema; this adds what the report needs and Power Query
would otherwise compute on every refresh: a fiscal (July to June) calendar, a channel key on the
fact, one account key across internet customers and resellers with the fiscal year of each
account's first order, order-value and line-size bands, and a buyer type for the reseller dimension.
Writes data/*.csv (UTF-8, LF).
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data"

CATEGORY_SORT = {"Bikes": 1, "Components": 2, "Clothing": 3, "Accessories": 4}
GROUP_SORT = {"North America": 1, "Europe": 2, "Pacific": 3, "Corporate HQ": 4}
BUYER_SORT = {"Internet": 1, "Bike shops": 2, "Value-add resellers": 3, "Warehouses": 4}
BUYER_NAME = {"Specialty Bike Shop": "Bike shops", "Value Added Reseller": "Value-add resellers", "Warehouse": "Warehouses"}
ORDER_BANDS = [(0, 100, "Under $100"), (100, 500, "$100-500"), (500, 2500, "$500-2.5K"),
               (2500, 10000, "$2.5K-10K"), (10000, 50000, "$10K-50K"), (50000, np.inf, "$50K+")]
# Half-open [lo, hi): AdventureWorks' reseller volume discounts start at 11, 15, 25 and 41 units.
LINE_BANDS = [(1, 11, "1-10 units"), (11, 15, "11-14 units"), (15, 25, "15-24 units"),
              (25, 41, "25-40 units"), (41, np.inf, "41+ units")]


def fiscal_year(d: pd.Series) -> pd.Series:
    return d.dt.year + (d.dt.month >= 7).astype(int)


def band(values: pd.Series, bands: list) -> pd.Series:
    out = pd.Series(0, index=values.index)
    for i, (lo, hi, _) in enumerate(bands, start=1):
        out[(values >= lo) & (values < hi)] = i
    return out


def main(xlsx: str) -> None:
    x = pd.read_excel(xlsx, sheet_name=None)
    OUT.mkdir(exist_ok=True)

    # ---- Date: one row per day over three whole fiscal years -------------------------------
    d = pd.DataFrame({"Date": pd.date_range("2017-07-01", "2020-06-30", freq="D")})
    d["FYSort"] = fiscal_year(d.Date)
    d["FiscalYear"] = "FY" + d.FYSort.astype(str)
    d["MonthNo"] = (d.Date.dt.month - 7) % 12 + 1
    d["Month"] = d.Date.dt.strftime("%b")
    d["MonthIndex"] = (d.FYSort - 2018) * 12 + d.MonthNo - 1
    d["MonthYear"] = d.Date.dt.strftime("%b %Y")
    d["MonthStart"] = d.Date.values.astype("datetime64[M]")
    d["DayOfFY"] = (d.Date - pd.to_datetime((d.FYSort - 1).astype(str) + "-07-01")).dt.days + 1

    # ---- Dimensions ---------------------------------------------------------------------------
    prod = x["Product_data"].rename(columns={"List Price": "ListPrice", "Standard Cost": "StandardCost"})
    prod["Color"] = prod.Color.fillna("")
    prod["CategorySort"] = prod.Category.map(CATEGORY_SORT)
    prod = prod[["ProductKey", "Product", "Model", "Subcategory", "Category", "CategorySort", "Color", "ListPrice"]]

    terr = x["Sales Territory_data"].rename(columns={"Group": "TerritoryGroup"})
    terr["GroupSort"] = terr.TerritoryGroup.map(GROUP_SORT)

    res = x["Reseller_data"].rename(columns={"Business Type": "BusinessType", "Country-Region": "Country",
                                             "State-Province": "State"})
    res["BuyerType"] = np.where(res.ResellerKey == -1, "Internet", res.BusinessType.map(BUYER_NAME))
    res.loc[res.ResellerKey == -1, ["Reseller", "BusinessType"]] = ["(Internet sale)", "(Internet sale)"]
    res["BuyerTypeSort"] = res.BuyerType.map(BUYER_SORT)
    res = res[["ResellerKey", "Reseller", "BusinessType", "BuyerType", "BuyerTypeSort", "City", "State", "Country"]]

    cust = x["Customer_data"].rename(columns={"Country-Region": "Country", "State-Province": "State"})
    cust.loc[cust.CustomerKey == -1, "Customer"] = "(Reseller sale)"
    cust = cust[["CustomerKey", "Customer", "City", "State", "Country"]]

    chan = pd.DataFrame({"ChannelKey": [1, 2], "Channel": ["Internet", "Reseller"]})
    lines = pd.DataFrame({"LineSizeKey": range(1, len(LINE_BANDS) + 1),
                          "LineSize": [b[2] for b in LINE_BANDS]})

    # ---- Fact -----------------------------------------------------------------------------------
    s = x["Sales_data"].merge(x["Sales Order_data"], on="SalesOrderLineKey", validate="one_to_one")
    s["OrderDate"] = pd.to_datetime(s.OrderDateKey.astype(str))
    s["ChannelKey"] = np.where(s.Channel == "Internet", 1, 2)
    s["AccountKey"] = np.where(s.ChannelKey == 1, "C" + s.CustomerKey.astype(str), "R" + s.ResellerKey.astype(str))
    s["FY"] = fiscal_year(s.OrderDate)
    s["AccountFirstFY"] = s.groupby("AccountKey").FY.transform("min")
    s["LineSizeKey"] = band(s["Order Quantity"], LINE_BANDS)
    assert (s.LineSizeKey > 0).all()

    orders = s.groupby("Sales Order", as_index=False)["Sales Amount"].sum().rename(
        columns={"Sales Order": "OrderID", "Sales Amount": "OrderValue"})
    orders["OrderSizeSort"] = band(orders.OrderValue, ORDER_BANDS)
    orders["OrderSize"] = orders.OrderSizeSort.map({i + 1: b[2] for i, b in enumerate(ORDER_BANDS)})
    assert (orders.OrderSizeSort > 0).all()

    fact = s.rename(columns={"SalesOrderLineKey": "LineKey", "Sales Order": "OrderID", "Order Quantity": "Quantity",
                             "Unit Price": "UnitPrice", "Extended Amount": "ExtendedAmount",
                             "Sales Amount": "SalesAmount", "Total Product Cost": "TotalProductCost"})
    fact = fact[["LineKey", "OrderID", "OrderDate", "ProductKey", "SalesTerritoryKey", "ResellerKey", "CustomerKey",
                 "ChannelKey", "LineSizeKey", "AccountKey", "AccountFirstFY", "Quantity", "UnitPrice",
                 "ExtendedAmount", "SalesAmount", "TotalProductCost"]]

    for name, df in {"dim_date": d, "dim_product": prod, "dim_territory": terr, "dim_reseller": res,
                     "dim_customer": cust, "dim_channel": chan, "dim_line_size": lines,
                     "dim_order": orders[["OrderID", "OrderValue", "OrderSize", "OrderSizeSort"]],
                     "fact_sales": fact}.items():
        df.to_csv(OUT / f"{name}.csv", index=False, date_format="%Y-%m-%d", lineterminator="\n")
        print(f"  {name:14s} {len(df):7,d} rows")
    print(f"sales total {fact.SalesAmount.sum():,.2f}")


if __name__ == "__main__":
    main(sys.argv[1])
