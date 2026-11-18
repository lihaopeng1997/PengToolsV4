"""Verify result-cell redaction with synthetic database rows only."""

from copy import deepcopy
from datetime import date
from decimal import Decimal
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import _isolation  # noqa: E402

from tools.data_center.result_projection import ProjectionBudget, project_result  # noqa: E402


def cell(value, **limits):
    result = project_result({"data": {"columns": ["remark"], "rows": [(value,)]}}, **limits)
    return result.data["rows"][0][0]


def explicit_credentials():
    fixtures = (
        ('登录 pwd="sample first second"', ("sample first", "second")),
        ('{"api_key":\n "sample escaped \\" piece", "status": "ok"}', ("sample escaped", "piece")),
        ("Authorization: Bearer sample-auth", ("sample-auth",)),
        ("Bearer sample-standalone", ("sample-standalone",)),
        ("redis://sample-user:sample-pass@demo.invalid:6379/0", ("sample-user", "sample-pass")),
        ("Cookie: session=sample-cookie; tracking=sample-tracking", ("sample-cookie", "sample-tracking")),
        ("-----BEGIN PRIVATE KEY-----\nsample-pem\n-----END PRIVATE KEY-----", ("sample-pem",)),
    )
    return all(projected["type"] == "string" and all(fragment not in projected["value"] for fragment in forbidden)
               for source, forbidden in fixtures if (projected := cell(source)))


def ordinary_values():
    samples = ("sampleLooksRandom123!", "记录密码长度，下一步人工确认", "203.0.113.5", "12345678901")
    return all(cell(text) == {"type": "string", "value": text} for text in samples)


def private_addresses():
    return cell("10.2.3.4, 172.16.7.8, 192.168.0.1, 172.32.1.2") == {
        "type": "string", "value": "10.x.x.x, 172.x.x.x, 192.x.x.x, 172.32.1.2",
    }


def truncated_credentials_and_ip():
    cases = (
        ('开始 pwd="' + 'sample-prefix ' * 100, 29, "sample-prefix"),
        ("入口 https://sample-user:" + "sample-pass" * 100 + "@demo.invalid/", 30, "sample-user"),
        ("发生于 192.168.123.254，后文" * 100, 12, "192.168"),
        ("pwd=" + "s" * 78 + "\nIP 192.168.123.254，后文", 32, "192.168"),
    )
    return all(projected["type"] == "string" and projected["truncated"]
               and len(projected["value"]) <= limit and forbidden not in projected["value"]
               for source, limit, forbidden in cases if (projected := cell(source, max_cell_chars=limit)))


def nested_values():
    source = {"notes": ["10.42.5.6", {"remark": "token=sample-nested", "password": "sample-keyed",
                                     "Set-Cookie": ["sample-cookie"]}],
              "192.168.0.10": 1, "192.168.0.20": 2}
    before = deepcopy(source)
    projected = cell(source)
    notes = projected["value"]["notes"]["value"]
    return (source == before and projected["type"] == "object" and notes[0]["value"] == "10.x.x.x"
            and notes[1]["value"]["password"] == notes[1]["value"]["Set-Cookie"] == {"type": "redacted"}
            and "sample-nested" not in str(projected) and "192.168.0." not in str(projected)
            and sorted(item["value"] for item in projected["value"].values() if item["type"] == "integer") == [1, 2])


def nested_depth_cap():
    class NoRawRepresentation(list):
        def __str__(self):
            raise AssertionError("depth cap must not stringify a nested container")

    nested = NoRawRepresentation([{"pwd": "sample-deep"}])
    for _ in range(8):
        nested = [nested]
    projected = cell(nested)
    for _ in range(8):
        projected = projected["value"][0]
    return projected["type"] == "string" and projected["truncated"] and "sample-deep" not in projected["value"]


def limited_scan():
    class ScanProbe(str):
        def __getitem__(self, key):
            if not isinstance(key, slice) or key.stop > 128:
                raise AssertionError("projection read past its bounded prefix")
            return super().__getitem__(key)

    return cell(ScanProbe("普通文本" + "a" * 500_000), max_cell_chars=32)["truncated"] is True


def native_types():
    return [
        cell(value) for value in (None, "", False, 0, Decimal("1.250"), date(2026, 11, 18), b"fake")
    ] == [
        {"type": "null"}, {"type": "string", "value": ""}, {"type": "boolean", "value": False},
        {"type": "integer", "value": 0}, {"type": "decimal", "value": "1.250"},
        {"type": "date", "value": "2026-11-18"}, {"type": "bytes", "size": 4},
    ]


def budgets_and_column_policy():
    budget = ProjectionBudget(max_bytes=4096)
    source = {"data": {"columns": ["customer_credential", "remark"], "rows": [
        ("sample-column", "pwd=sample-row"), ("sample-other", "second row"),
    ]}}
    result = project_result(source, max_rows=1, budget=budget, max_result_bytes=1024)
    wire = result.as_model_message("synthetic")["content"]
    return (result.truncated and result.limits["returned_rows"] == 1
            and result.data["rows"][0][0] == {"type": "redacted"}
            and "sample-" not in wire and "second row" not in wire
            and 0 < len(wire.encode("utf-8")) == result.bytes_used == budget.used_bytes <= 1024)


checks = (explicit_credentials, ordinary_values, private_addresses, truncated_credentials_and_ip,
          nested_values, nested_depth_cap, limited_scan, native_types, budgets_and_column_policy)
passed = 0
try:
    for check in checks:
        try:
            ok = bool(check())
        except Exception as error:
            print(f"FAIL {check.__name__} ({type(error).__name__})")
            continue
        passed += int(ok)
        print(f"{'PASS' if ok else 'FAIL'} {check.__name__}")
finally:
    _isolation.assert_untouched()

failed = len(checks) - passed
print(f"RESULT: {passed} passed, {failed} failed")
raise SystemExit(bool(failed))
