"""F-006: print every view's projected balance per component (diagnostic; FORK_F006_TABLE=path)."""
import json
import os
from datetime import date

import pytest

from tests.fork import _budget_data as data
from tests.fork import _projection_data as pdata
from tests.fork import _projection_views as views

DATES = (date(2026, 10, 31), date(2026, 11, 10), date(2026, 11, 20), date(2026, 11, 25),
         date(2026, 11, 30), date(2026, 12, 31))


@pytest.mark.skipif(not os.environ.get("FORK_F006_TABLE"), reason="diagnostic table only on request")
@pytest.mark.parametrize("component", list(pdata.COMPONENTS))
@pytest.mark.parametrize("scope", ["all", "checking"])
@pytest.mark.asyncio
async def test_component_table(session, test_user, test_workspace, monkeypatch, component, scope):
    data.pin_today(monkeypatch)
    base = await pdata.build(session, test_user, test_workspace, component)
    account_ids = None if scope == "all" else [base.checking.id]
    rows = {}
    for on in DATES:
        rows[on.isoformat()] = {k: (None if v is None else str(v)) for k, v in
                                (await views.read_all(session, test_workspace, test_user, on, account_ids)).items()}
    with open(os.environ["FORK_F006_TABLE"], "a") as out:
        out.write(json.dumps({"component": component, "scope": scope, "rows": rows}) + "\n")
