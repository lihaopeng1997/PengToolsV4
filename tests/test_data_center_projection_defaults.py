import json
import unittest

from tools.data_center.contracts import RunContext, ToolResult
from tools.data_center.host_runtime import ScopedResultProjector


def _size(result):
    return len(json.dumps(result.as_dict(),ensure_ascii=False,separators=(",",":"),default=str).encode("utf-8"))


def _context():
    return RunContext(run_id="projection-default-fixture", tab_id="fixture-tab",
                      model_config_id="fixture-model", connection_id="fixture-connection",
                      database="fixture", schema_allowlist=("fixture",), profile_revision="fixture-v1",
                      policy_version="fixture-policy")


class ProjectionDefaultTests(unittest.TestCase):
    def test_default_runtime_projection_bounds_unicode_sql_and_structured_results(self):
        for data in (
            {"columns":[{"name":"description","type":"VARCHAR"}], "rows":[["字"*512] for _ in range(50)]},
            {"objects":[{"object_id":f"fixture-{index}", "value":"字"*512} for index in range(40)]},
        ):
            with self.subTest(tabular="columns" in data):
                projector=ScopedResultProjector()
                result=projector.project_for_context(ToolResult(ok=True, data=data, query_id="fixture-query"), _context())
                self.assertTrue(result.ok)
                self.assertTrue(result.truncated)
                self.assertLessEqual(_size(result),12*1024)
                projector.release(_context())

    def test_mixed_tool_results_share_documented_cumulative_budget(self):
        projector=ScopedResultProjector()
        context=_context()
        data={"objects":[{"object_id":f"fixture-{index}", "value":"x"*400} for index in range(20)]}
        results=[projector.project_for_context(ToolResult(ok=True,data=data,query_id=f"fixture-{index}"),context) for index in range(8)]
        self.assertLessEqual(sum(_size(result) for result in results),48*1024)
        self.assertFalse(results[0].truncated)
        self.assertTrue(any(result.truncated for result in results))
        projector.release(context)
        fresh=projector.project_for_context(ToolResult(ok=True,data=data),context)
        self.assertFalse(fresh.truncated)
        projector.release(context)


if __name__=="__main__":
    unittest.main()
