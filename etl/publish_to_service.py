"""Publish the semantic model and the report to a Power BI workspace, then refresh in the Service.

    python etl/publish_to_service.py                 # My workspace
    python etl/publish_to_service.py --workspace <id>

Uses the signed-in Azure CLI account (az login) for both APIs:
  * Fabric REST creates or updates the SemanticModel item from the TMDL folder and the Report item
    from the PBIR folder. The report's definition.pbir is rewritten byPath -> byConnection on the
    way up; the local file is left as it is, so Desktop keeps opening the PBIP.
  * Power BI REST sets the model's Web data sources (this repo's GitHub raw CSVs) to anonymous
    credentials and runs a refresh, waiting for it to finish.
Updates in place when items of the same name exist. Prints the ids and the report URL.
"""

from __future__ import annotations

import argparse
import base64
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NAME = "AdventureWorks Performance"
MODEL_DIR = ROOT / f"{NAME}.SemanticModel"
REPORT_DIR = ROOT / f"{NAME}.Report"
FABRIC = "https://api.fabric.microsoft.com/v1"
PBI = "https://api.powerbi.com/v1.0/myorg"


def token(resource: str) -> str:
    out = subprocess.run(f'az account get-access-token --resource "{resource}" --query accessToken -o tsv',
                         shell=True, check=True, capture_output=True, text=True)
    return out.stdout.strip()


TOKENS = {}


def call(method: str, url: str, body=None, resource="https://api.fabric.microsoft.com"):
    """Returns (status, headers, json-or-None). Raises with the response text on 4xx/5xx."""
    if resource not in TOKENS:
        TOKENS[resource] = token(resource)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {TOKENS[resource]}", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as r:
            raw = r.read()
            return r.status, dict(r.headers), (json.loads(raw) if raw.strip() else None)
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{method} {url} -> {e.code}: {e.read().decode(errors='replace')[:1500]}") from None


def wait(status: int, headers: dict, what: str):
    """Polls a Fabric long-running operation to the end; returns its result when it has one."""
    if status != 202:
        return None
    op = headers.get("x-ms-operation-id") or headers.get("X-Ms-Operation-Id")
    for _ in range(120):
        time.sleep(int(headers.get("Retry-After", 3)))
        _, _, st = call("GET", f"{FABRIC}/operations/{op}")
        if st["status"] in ("Succeeded", "Failed", "Undefined"):
            if st["status"] != "Succeeded":
                raise RuntimeError(f"{what} failed: {json.dumps(st)[:1500]}")
            try:
                return call("GET", f"{FABRIC}/operations/{op}/result")[2]
            except RuntimeError:
                return None
    raise RuntimeError(f"{what}: timed out")


def parts(folder: Path, replace: dict[str, bytes] | None = None) -> list[dict]:
    out = []
    for f in sorted(folder.rglob("*")):
        if not f.is_file() or f.name == ".platform" or ".pbi" in f.parts:
            continue
        rel = f.relative_to(folder).as_posix()
        payload = (replace or {}).get(rel, f.read_bytes())
        out.append({"path": rel, "payload": base64.b64encode(payload).decode(), "payloadType": "InlineBase64"})
    return out


def upsert(ws: str, kind: str, definition_parts: list[dict]) -> str:
    """Create the item, or overwrite its whole definition if one of this name exists."""
    _, _, items = call("GET", f"{FABRIC}/workspaces/{ws}/{kind}")
    found = next((i for i in items["value"] if i["displayName"] == NAME), None)
    if found:
        s, h, _ = call("POST", f"{FABRIC}/workspaces/{ws}/{kind}/{found['id']}/updateDefinition",
                       {"definition": {"parts": definition_parts}})
        wait(s, h, f"update {kind}")
        print(f"  updated {kind[:-1]} {found['id']}")
        return found["id"]
    s, h, body = call("POST", f"{FABRIC}/workspaces/{ws}/{kind}",
                      {"displayName": NAME, "definition": {"parts": definition_parts}})
    result = wait(s, h, f"create {kind}") or body
    if not result or "id" not in result:   # never re-POST after a 202: look it up instead
        _, _, items = call("GET", f"{FABRIC}/workspaces/{ws}/{kind}")
        result = next(i for i in items["value"] if i["displayName"] == NAME)
    print(f"  created {kind[:-1]} {result['id']}")
    return result["id"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workspace", help="workspace id (default: My workspace)")
    args = ap.parse_args()

    _, _, wss = call("GET", f"{FABRIC}/workspaces")
    ws = args.workspace or next(w["id"] for w in wss["value"] if w.get("type") == "Personal")
    personal = next((w for w in wss["value"] if w["id"] == ws), {}).get("type") == "Personal"
    print(f"workspace {ws}{' (My workspace)' if personal else ''}")

    model_id = upsert(ws, "semanticModels", parts(MODEL_DIR))

    # Anonymous credentials on every GitHub raw source, then a refresh in the Service.
    pbi = "https://analysis.windows.net/powerbi/api"
    base = f"{PBI}/datasets/{model_id}" if personal else f"{PBI}/groups/{ws}/datasets/{model_id}"
    _, _, sources = call("GET", f"{base}/datasources", resource=pbi)
    creds = {"credentialDetails": {"credentialType": "Anonymous", "credentials": json.dumps({"credentialData": ""}),
                                   "encryptedConnection": "Encrypted", "encryptionAlgorithm": "None",
                                   "privacyLevel": "Public"}}
    for src in sources["value"]:
        call("PATCH", f"{PBI}/gateways/{src['gatewayId']}/datasources/{src['datasourceId']}", creds, resource=pbi)
    print(f"  anonymous credentials on {len(sources['value'])} web source(s)")
    call("POST", f"{base}/refreshes", {"notifyOption": "NoNotification"}, resource=pbi)
    started = time.time()
    while True:
        time.sleep(10)
        _, _, hist = call("GET", f"{base}/refreshes?$top=1", resource=pbi)
        last = hist["value"][0]
        if last["status"] in ("Completed", "Failed", "Disabled", "Cancelled"):
            break
        if time.time() - started > 900:
            sys.exit("refresh still running after 15 minutes")
    if last["status"] != "Completed":
        sys.exit(f"refresh {last['status']}: {json.dumps(last)[:1500]}")
    print(f"  refresh completed in {time.time() - started:.0f}s")

    pbir = json.loads((REPORT_DIR / "definition.pbir").read_text(encoding="utf-8"))
    pbir["datasetReference"] = {"byConnection": {"connectionString": f"semanticmodelid={model_id}"}}
    report_id = upsert(ws, "reports", parts(REPORT_DIR, {"definition.pbir": json.dumps(pbir, indent=2).encode()}))

    group = "me" if personal else ws
    print(f"\nmodel  {model_id}\nreport {report_id}\nhttps://app.powerbi.com/groups/{group}/reports/{report_id}")
    (ROOT / "etl" / ".service.json").write_text(json.dumps(
        {"workspace": ws, "personal": personal, "model": model_id, "report": report_id}, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
