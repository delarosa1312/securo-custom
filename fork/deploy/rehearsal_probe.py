#!/usr/bin/env python3
"""Rehearsal helper for fork/deploy/rehearse.sh: one synthetic attachment, data snapshots, snapshot comparison.

  attach   --base-url URL                 upload one synthetic text file to the first transaction
  snapshot --base-url URL --db CONTAINER [--baseline FILE] > FILE
           per table: row count and a checksum of every row; over REST: accounts, the Cash Flow report,
           the attachment's SHA-256, the API version, whether a token issued before still works.
           With --baseline, only the tables, columns and response keys the baseline had are compared, so the
           columns and fields a fork migration adds do not count as a change.
  compare  BASELINE OTHER                 prints every difference; exit 1 when there is one
Login: the synthetic seed user of fork/stack/seed.py. Stdlib only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

EMAIL = "e2e@example.com"
PASSWORD = "Synthetic-E2E-2026"  # local stacks only, as in fork/stack/seed.py
ATTACHMENT = b"<receipt><note>Synthetic receipt for the rehearsal. No real data.</note></receipt>\n"


def call(
    base: str,
    method: str,
    path: str,
    token: str | None = None,
    data: bytes | None = None,
    content_type: str | None = None,
    raw: bool = False,
):
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if content_type:
        headers["Content-Type"] = content_type
    req = urllib.request.Request(base.rstrip("/") + path, data=data, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        body = resp.read()
    return body if raw else json.loads(body or b"null")


def login(base: str) -> str:
    form = urllib.parse.urlencode({"username": EMAIL, "password": PASSWORD}).encode()
    for attempt in range(6):
        try:
            return call(
                base,
                "POST",
                "/api/auth/login",
                data=form,
                content_type="application/x-www-form-urlencoded",
            )["access_token"]
        except urllib.error.HTTPError as err:
            if err.code != 429 or attempt == 5:
                raise
            time.sleep(20)  # the login rate limit
    raise AssertionError("unreachable")


def first_transaction(base: str, token: str) -> str:
    page = call(base, "GET", "/api/transactions?limit=1&skip=0", token)
    items = page["items"] if isinstance(page, dict) else page
    return sorted(items, key=lambda t: t["id"])[0]["id"]


def attach(base: str) -> None:
    token = login(base)
    tx = first_transaction(base, token)
    boundary = uuid.uuid4().hex
    body = (
        (
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="synthetic-receipt.xml"\r\n'
            "Content-Type: application/xml\r\n\r\n"
        ).encode()
        + ATTACHMENT
        + f"\r\n--{boundary}--\r\n".encode()
    )
    out = call(
        base,
        "POST",
        f"/api/transactions/{tx}/attachments",
        token,
        body,
        f"multipart/form-data; boundary={boundary}",
    )
    print(
        json.dumps(
            {
                "transaction": tx,
                "attachment": out["id"],
                "sha256": hashlib.sha256(ATTACHMENT).hexdigest(),
            }
        )
    )


def psql(container: str, sql: str) -> str:
    return subprocess.check_output(
        [
            "docker",
            "exec",
            "-i",
            container,
            "psql",
            "-U",
            "postgres",
            "-d",
            "securo",
            "-AtX",
            "-v",
            "ON_ERROR_STOP=1",
        ],
        input=sql,
        text=True,
    ).strip()


def db_snapshot(container: str, baseline: dict | None) -> dict:
    columns: dict[str, list[str]] = {}
    for line in psql(
        container,
        """
        select table_name, string_agg(column_name, ',' order by column_name)
        from information_schema.columns where table_schema = 'public' group by table_name order by 1;
    """,
    ).splitlines():
        table, cols = line.split("|", 1)
        columns[table] = cols.split(",")
    tables = {}
    for table, cols in columns.items():
        if baseline is not None:
            if table not in baseline["db"]["tables"]:
                continue
            cols = [c for c in cols if c in baseline["db"]["tables"][table]["columns"]]
        if table == "alembic_version":
            continue
        # Secret tables (the generated SECRET_KEY) count rows only: no digest of a key leaves the database.
        picked = ", ".join(f'"{c}"' for c in cols) if "secret" not in table else "1"
        count, digest = psql(
            container,
            f"""
            select count(*), coalesce(md5(string_agg(r::text, '|' order by r::text)), '')
            from (select {picked} from public."{table}") r;
        """,
        ).split("|")
        tables[table] = {"columns": cols, "rows": int(count), "md5": digest}
    extra = sorted(set(columns) - set(baseline["db"]["tables"])) if baseline is not None else []
    return {
        "alembic_version": sorted(
            psql(container, "select version_num from alembic_version;").split()
        ),
        "tables": tables,
        "tables_not_in_baseline": extra,
    }


def keep_keys(value, shape):
    """The parts of a REST response the baseline had: new fields a fork release adds are not a change."""
    if isinstance(value, dict) and isinstance(shape, dict):
        return {k: keep_keys(v, shape[k]) for k, v in value.items() if k in shape}
    if isinstance(value, list) and isinstance(shape, list) and shape:
        return [keep_keys(v, shape[0]) for v in value]
    return value


def snapshot(base: str, container: str, baseline_path: Path | None, state: Path) -> None:
    baseline = json.loads(baseline_path.read_text()) if baseline_path else None
    old_token = state.read_text().strip() if state.exists() else None
    old_token_works = None
    if old_token:
        try:
            call(base, "GET", "/api/users/me", old_token)
            old_token_works = True
        except urllib.error.HTTPError:
            old_token_works = False
    token = login(base)
    if not state.exists():
        state.write_text(token)
    accounts = sorted(call(base, "GET", "/api/accounts", token), key=lambda a: a["id"])
    cash_flow = call(base, "GET", "/api/reports/cash-flow?months=3&interval=monthly", token)
    tx = first_transaction(base, token)
    files = call(base, "GET", f"/api/transactions/{tx}/attachments", token)
    shas = sorted(
        hashlib.sha256(
            call(base, "GET", f"/api/transactions/{tx}/attachments/{f['id']}", token, raw=True)
        ).hexdigest()
        for f in files
    )
    rest = {"accounts": accounts, "cash_flow": cash_flow}
    if baseline is not None:
        rest = {k: keep_keys(v, baseline["rest"][k]) for k, v in rest.items()}
    print(
        json.dumps(
            {
                "db": db_snapshot(container, baseline),
                "rest": rest,
                "attachments_sha256": shas,
                "token_from_first_snapshot_accepted": old_token_works,
                "health": call(base, "GET", "/api/health"),
            },
            indent=1,
            sort_keys=True,
        )
    )


def canonical(value):
    """Lists compare as multisets: upstream does not order every list (the Cash Flow composition slices come
    in database row order, which a restore changes), and the order is not data."""
    if isinstance(value, dict):
        return {k: canonical(v) for k, v in value.items()}
    if isinstance(value, list):
        return sorted((canonical(v) for v in value), key=lambda v: json.dumps(v, sort_keys=True))
    return value


def compare(a_path: Path, b_path: Path) -> int:
    a, b = json.loads(a_path.read_text()), json.loads(b_path.read_text())
    diffs = []
    ta, tb = a["db"]["tables"], b["db"]["tables"]
    for table in sorted(set(ta) | set(tb)):
        if table not in tb:
            diffs.append(f"table {table} missing")
        elif table not in ta:
            continue
        elif (ta[table]["rows"], ta[table]["md5"]) != (tb[table]["rows"], tb[table]["md5"]):
            diffs.append(
                f"table {table}: {ta[table]['rows']} rows -> {tb[table]['rows']} rows, checksum changed"
            )
    for key in ("rest", "attachments_sha256"):
        if canonical(a[key]) != canonical(b[key]):
            diffs.append(f"{key} differs")
    print(
        f"compared {len(ta)} tables, REST accounts and Cash Flow, attachments: "
        + ("no difference" if not diffs else f"{len(diffs)} differences")
    )
    for d in diffs:
        print("  " + d)
    return 1 if diffs else 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["attach", "snapshot", "compare"])
    parser.add_argument("files", nargs="*", type=Path)
    parser.add_argument("--base-url", default="http://127.0.0.1:28000")
    parser.add_argument("--db", default="securo-custom-rehearsal-db-1")
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--token-file", type=Path, default=Path("token.txt"))
    args = parser.parse_args()
    if args.command == "attach":
        attach(args.base_url)
        return 0
    if args.command == "snapshot":
        snapshot(args.base_url, args.db, args.baseline, args.token_file)
        return 0
    return compare(*args.files)


if __name__ == "__main__":
    sys.exit(main())
