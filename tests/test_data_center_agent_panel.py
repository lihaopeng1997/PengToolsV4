"""Focused behavioural tests for the standalone data-center Agent panel."""

from __future__ import annotations

import ast
import inspect
import os
import sys
import threading
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from tools.data_center.agent import AgentRunner
from tools.data_center.contracts import AgentEvent, AgentEventType, AgentTurn, RunContext
from ui.data_center.agent_panel import DataCenterAgentPanel


class DataCenterAgentPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(sys.argv)

    def setUp(self):
        self.panel = DataCenterAgentPanel(
            channel_limits={"reasoning": 20, "answer": 24, "evidence": 80, "sql": 120, "result": 80}
        )
        self.sink = self.panel.start_run("run-a", "tab-a", target="demo target", model="demo model")

    def tearDown(self):
        self.panel.close()
        self.panel.deleteLater()
        self.app.processEvents()

    def _event(self, sequence: int, event_type: str | AgentEventType, **payload) -> AgentEvent:
        return AgentEvent("run-a", "tab-a", sequence, event_type, payload)

    def _flush(self):
        self.app.processEvents()
        self.app.processEvents()

    def test_worker_thread_event_is_queued_before_ui_mutation_ends(self):
        event = self._event(1, AgentEventType.MODEL_TEXT_DELTA, text="queued answer")

        thread = threading.Thread(target=lambda: self.sink(event))
        thread.start()
        thread.join()
        self.assertNotIn("queued answer", self.panel.answer_view.toPlainText())

        self._flush()
        self.assertIn("queued answer", self.panel.answer_view.toPlainText())

    def test_terminal_timeout_reports_worker_snapshot_and_ignores_unapproved_fields(self):
        self.sink(self._event(
            1,
            AgentEventType.CANCELLED,
            reason="timeout",
            context_status="deferred",
            query_worker_pending=True,
            query_worker_status="pending",
            private_payload="must not appear",
        ))
        self._flush()
        phase = self.panel.phase_label.text()
        self.assertIn("本轮终止时查询 worker 尚未完成", phase)
        self.assertNotIn("must not appear", phase)
        self.assertNotIn("private_payload", phase)
        self.panel.set_display_context("en", "query")
        self.assertIn("worker was unfinished when this run terminated", self.panel.phase_label.toolTip())

        self.panel.start_run("run-b", "tab-b")
        self.panel.set_display_context("en", "query")
        sink = self.panel.event_sink()
        sink(AgentEvent("run-b", "tab-b", 1, AgentEventType.CANCELLED, {
            "reason": "timeout",
            "context_status": "deferred",
            "query_worker_pending": 1,
            "query_worker_status": "pending",
            "private_payload": "must not appear",
        }))
        self._flush()
        self.assertNotIn("worker was unfinished", self.panel.phase_label.text())
        self.assertNotIn("must not appear", self.panel.phase_label.text())

    def test_reasoning_summary_and_answer_are_separate_explicit_channels(self):
        self.sink(self._event(1, AgentEventType.MODEL_REASONING_DELTA, text="explicit summary"))
        self.sink(self._event(2, AgentEventType.MODEL_TEXT_DELTA, text="visible answer"))
        self._flush()

        self.assertIn("explicit summary", self.panel.reasoning_view.toPlainText())
        self.assertNotIn("visible answer", self.panel.reasoning_view.toPlainText())
        self.assertIn("visible answer", self.panel.answer_view.toPlainText())
        self.assertNotIn("explicit summary", self.panel.answer_view.toPlainText())

    def test_final_reasoning_summary_does_not_duplicate_prior_delta(self):
        self.sink(self._event(1, AgentEventType.MODEL_REASONING_DELTA, text="explicit summary"))
        self.sink(self._event(2, AgentEventType.FINAL, reasoning_summary="explicit summary", verified=False))
        self._flush()
        self.assertEqual(self.panel.reasoning_view.toPlainText(), "explicit summary")

    def test_real_agent_runner_events_reach_panel_and_terminally_stop(self):
        class FakeModel:
            def complete_turn(self, request, *, cancellation, on_delta):
                on_delta({"kind": "reasoning", "text": "runner summary"})
                on_delta({"kind": "text", "text": "runner answer"})
                return AgentTurn(text="runner answer", model_id="fake-model")

        class EmptyTools:
            def definitions(self):
                return ()

            def execute(self, call, *, context, cancellation):  # pragma: no cover - no tool call expected
                raise AssertionError("fake run should not execute tools")

        context = RunContext(
            run_id="run-a",
            tab_id="tab-a",
            model_config_id="demo",
            connection_id="demo-connection",
            database="demo",
        )
        runner = AgentRunner(FakeModel(), EmptyTools(), on_event=self.sink)
        result = runner.run(context, "show a safe summary")
        self.assertEqual(result.status.value, "completed")
        self._flush()
        self.assertIn("runner summary", self.panel.reasoning_view.toPlainText())
        self.assertIn("runner answer", self.panel.answer_view.toPlainText())
        self.assertFalse(self.panel.is_active)
        self.assertIn("已完成", self.panel.phase_label.text())

    def test_needs_clarification_uses_runner_text_and_is_terminal(self):
        self.sink(self._event(1, AgentEventType.NEEDS_CLARIFICATION, text="请选择一个对象"))
        self.sink(self._event(2, AgentEventType.MODEL_TEXT_DELTA, text="late delta"))
        self._flush()
        self.assertIn("请选择一个对象", self.panel.answer_view.toPlainText())
        self.assertNotIn("late delta", self.panel.answer_view.toPlainText())
        self.assertFalse(self.panel.is_active)
        self.assertFalse(self.panel.stop_button.isEnabled())

    def test_timeout_cancellation_keeps_timeout_reason_distinct_from_success(self):
        self.sink(self._event(1, AgentEventType.CANCELLED, status="timeout", reason="timeout"))
        self.sink(self._event(2, AgentEventType.FINAL, verified=True, text="late success"))
        self._flush()
        self.assertIn("超时", self.panel.phase_label.text())
        self.assertNotIn("已完成", self.panel.phase_label.text())
        self.assertNotIn("late success", self.panel.answer_view.toPlainText())

    def test_stop_emits_pending_and_does_not_claim_late_final_success(self):
        stopped: list[str] = []
        pending: list[str] = []
        self.panel.stop_requested.connect(stopped.append)
        self.panel.cancel_pending.connect(pending.append)

        self.panel.stop_button.click()
        self._flush()
        self.assertEqual(stopped, ["run-a"])
        self.assertEqual(pending, ["run-a"])
        self.assertTrue(self.panel.is_cancel_pending)
        self.assertNotIn("已完成", self.panel.phase_label.text())

        self.sink(self._event(1, AgentEventType.FINAL, verified=True, text="late final"))
        self._flush()
        self.assertTrue(self.panel.is_cancel_pending)
        self.assertNotIn("已完成", self.panel.phase_label.text())
        self.assertNotIn("late final", self.panel.answer_view.toPlainText())

        self.sink(self._event(2, AgentEventType.CANCELLED, reason="user"))
        self._flush()
        self.assertFalse(self.panel.is_cancel_pending)
        self.assertIn("已取消", self.panel.phase_label.text())

    def test_old_generation_run_and_sequence_events_are_dropped(self):
        old_sink = self.sink
        old_sink(self._event(1, AgentEventType.MODEL_TEXT_DELTA, text="old text"))
        self._flush()
        self.panel.start_run("run-b", "tab-b")
        old_sink(self._event(2, AgentEventType.MODEL_TEXT_DELTA, text="late old text"))
        self.panel.event_received.emit(
            AgentEvent("run-a", "tab-a", 99, AgentEventType.MODEL_TEXT_DELTA, {"text": "wrong scope"})
        )
        self._flush()
        self.assertNotIn("late old text", self.panel.answer_view.toPlainText())
        self.assertNotIn("wrong scope", self.panel.answer_view.toPlainText())

        current = self.panel.event_sink()
        current(AgentEvent("run-b", "tab-b", 1, AgentEventType.MODEL_TEXT_DELTA, {"text": "current"}))
        current(AgentEvent("run-b", "tab-b", 1, AgentEventType.MODEL_TEXT_DELTA, {"text": "duplicate"}))
        self._flush()
        self.assertIn("current", self.panel.answer_view.toPlainText())
        self.assertNotIn("duplicate", self.panel.answer_view.toPlainText())

    def test_each_channel_has_its_own_display_cap_and_marker(self):
        self.sink(self._event(1, AgentEventType.MODEL_TEXT_DELTA, text="123456789012345678901234567890"))
        self.sink(self._event(2, AgentEventType.MODEL_REASONING_DELTA, text="abcdefghijklmnopqrstuv"))
        self._flush()
        self.assertIn("显示已截断", self.panel.answer_view.toPlainText())
        self.assertIn("显示已截断", self.panel.reasoning_view.toPlainText())
        self.assertLessEqual(len(self.panel._channels["answer"].value), 24)
        self.assertLessEqual(len(self.panel._channels["reasoning"].value), 20)

    def test_tool_sql_copy_manual_and_new_conversation_signals(self):
        copied: list[str] = []
        put: list[str] = []
        created: list[bool] = []
        self.panel.copy_sql_requested.connect(copied.append)
        self.panel.put_sql_requested.connect(put.append)
        self.panel.new_conversation_requested.connect(lambda: created.append(True))
        sql = "SELECT order_id FROM orders LIMIT 5"
        self.sink(
            self._event(
                1,
                AgentEventType.TOOL_CALL,
                name="query_readonly",
                call_id="c1",
                arguments={"sql": sql},
            )
        )
        self.sink(
            self._event(
                2,
                AgentEventType.TOOL_RESULT,
                name="query_readonly",
                ok=True,
                query_id="q1",
                evidence_id="ev1",
                data={"rows": [[1, "paid"]]},
            )
        )
        self._flush()
        self.panel.copy_sql_button.click()
        self.panel.put_sql_button.click()
        self.assertIn("q1", self.panel.query_id_label.text())
        self.panel.new_conversation_button.click()
        self.assertEqual(copied, [sql])
        self.assertEqual(put, [sql])
        self.assertEqual(created, [True])
        self.assertEqual(self.panel.query_id_label.text(), "query_id：—")
        self.assertEqual(self.panel.result_preview_view.toPlainText(), "")

    def test_truncated_sql_disables_copy_and_manual_handoff(self):
        copied: list[str] = []
        self.panel.copy_sql_requested.connect(copied.append)
        long_sql = "SELECT " + ("very_long_column, " * 30) + "id FROM demo"
        self.sink(
            self._event(
                1,
                AgentEventType.TOOL_CALL,
                name="query_readonly",
                call_id="c-long",
                arguments={"sql": long_sql},
            )
        )
        self._flush()
        self.assertTrue(self.panel._channels["sql"].truncated)
        self.assertFalse(self.panel.copy_sql_button.isEnabled())
        self.assertFalse(self.panel.put_sql_button.isEnabled())
        self.panel.copy_sql_button.click()
        self.assertEqual(copied, [])

    def test_prompt_send_rejects_blank_and_emits_one_stripped_value(self):
        prompts: list[str] = []
        self.panel.prompt_send_requested.connect(prompts.append)
        self.panel.reset_run()

        self.panel.prompt_input.setPlainText("  \n  ")
        self.assertFalse(self.panel.send_button.isEnabled())
        self.panel.send_button.click()
        self.assertEqual(prompts, [])

        self.panel.prompt_input.setPlainText("  summarize orders\nby status  ")
        self.assertTrue(self.panel.send_button.isEnabled())
        self.panel.send_button.click()
        self.assertEqual(prompts, ["summarize orders\nby status"])
        self.assertEqual(self.panel.prompt_input.toPlainText(), "  summarize orders\nby status  ")

    def test_prompt_send_is_disabled_during_active_and_cancel_pending_states(self):
        self.panel.prompt_input.setPlainText("show recent orders")
        self.assertFalse(self.panel.send_button.isEnabled())

        self.panel.stop_button.click()
        self.assertTrue(self.panel.is_cancel_pending)
        self.assertFalse(self.panel.send_button.isEnabled())

        self.sink(self._event(1, AgentEventType.CANCELLED, reason="user"))
        self._flush()
        self.assertTrue(self.panel.send_button.isEnabled())

        self.panel.start_run("run-b", "tab-b")
        self.assertFalse(self.panel.send_button.isEnabled())
        self.panel.new_conversation_button.click()
        self.assertTrue(self.panel.send_button.isEnabled())

        self.panel.start_run("run-c", "tab-c")
        self.assertFalse(self.panel.send_button.isEnabled())
        self.panel.reset_run()
        self.assertTrue(self.panel.send_button.isEnabled())

    def test_host_acknowledgement_clears_only_the_accepted_prompt(self):
        self.panel.reset_run()
        self.panel.prompt_input.setPlainText("first request")
        self.assertFalse(self.panel.acknowledge_prompt("older request"))
        self.assertEqual(self.panel.prompt_input.toPlainText(), "first request")

        self.assertTrue(self.panel.acknowledge_prompt("  first request  "))
        self.assertEqual(self.panel.prompt_input.toPlainText(), "")
        self.assertFalse(self.panel.send_button.isEnabled())

    def test_panel_declares_no_driver_or_config_imports(self):
        module = sys.modules[DataCenterAgentPanel.__module__]
        tree = ast.parse(inspect.getsource(module))
        imports = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imports.update(
            node.module or ""
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
        )
        blocked = [
            name
            for name in imports
            if "driver" in name.lower() or name == "config" or name.startswith("config.")
        ]
        self.assertEqual(blocked, [])

    def test_narrow_sidebar_stacks_context_without_horizontal_overflow(self):
        self.panel.resize(340, 900)
        self.panel.show()
        self._flush()

        self.assertEqual(self.panel.width(), 340)
        self.assertTrue(self.panel._narrow_mode)
        self.assertLessEqual(self.panel.minimumSizeHint().width(), 340)
        self.assertEqual(self.panel.body_scroll.horizontalScrollBar().maximum(), 0)
        self.assertGreater(self.panel.body_scroll.verticalScrollBar().maximum(), 0)
        self.assertTrue(self.panel.stop_button.isVisible())
        self.assertTrue(self.panel.new_conversation_button.isVisible())
        self.assertTrue(self.panel.copy_sql_button.isVisible())
        self.assertTrue(self.panel.prompt_input.isVisible())
        self.assertTrue(self.panel.send_button.isVisible())
        self.assertLessEqual(
            self.panel.send_button.geometry().right(),
            self.panel.send_button.parentWidget().width(),
        )
        self.assertIn("仅显示模型明确提供", self.panel._preview_subtitle.toolTip())
        self.assertTrue(
            self.panel._preview_subtitle.text() == self.panel._preview_subtitle.toolTip()
            or self.panel._preview_subtitle.text().endswith("…")
        )
        composer = self.panel.prompt_input.parentWidget()
        self.assertLess(self.panel.body_scroll.geometry().bottom(), composer.geometry().top())
        self.assertLessEqual(
            self.panel.prompt_input.geometry().bottom(),
            self.panel.prompt_input.parentWidget().rect().bottom(),
        )
        for widget in (
            self.panel.target_caption,
            self.panel.target_value_label,
            self.panel.model_caption,
            self.panel.model_value_label,
            self.panel.read_only_label,
        ):
            self.assertLessEqual(widget.geometry().right(), widget.parentWidget().width())

    def test_wide_to_narrow_resize_reaches_sidebar_width(self):
        self.panel.resize(1_440, 900)
        self.panel.show()
        self._flush()
        self.assertEqual(self.panel.width(), 1_440)
        self.assertFalse(self.panel._narrow_mode)

        self.panel.resize(340, 900)
        self._flush()
        self.assertEqual(self.panel.width(), 340)
        self.assertTrue(self.panel._narrow_mode)
        self.assertEqual(self.panel.body_scroll.horizontalScrollBar().maximum(), 0)

    def test_display_context_is_bilingual_and_does_not_rebind_runtime_state(self):
        self.panel.prompt_input.setPlainText("keep this prompt")
        channels = self.panel._channels
        state = (
            self.panel.run_id,
            self.panel.tab_id,
            self.panel.binding_generation,
            self.panel.last_sequence,
            self.panel.is_active,
            self.panel.is_cancel_pending,
            self.panel.target_value_label.text(),
            self.panel.model_value_label.text(),
            self.panel.prompt_input.toPlainText(),
        )

        self.panel.set_display_context(" EN-us ", " DRAFT ")

        self.assertEqual(self.panel._display_language, "en")
        self.assertEqual(self.panel._display_mode, "draft")
        self.assertEqual(self.panel.title_label.text(), "Draft assistant")
        self.assertEqual(self.panel.preview_badge.text(), "Draft")
        self.assertIn("no database access", self.panel.read_only_label.text())
        self.assertIn("does not access the database", self.panel.prompt_input.placeholderText())
        self.assertIs(self.panel._channels, channels)
        self.assertEqual(
            (
                self.panel.run_id,
                self.panel.tab_id,
                self.panel.binding_generation,
                self.panel.last_sequence,
                self.panel.is_active,
                self.panel.is_cancel_pending,
                self.panel.target_value_label.text(),
                self.panel.model_value_label.text(),
                self.panel.prompt_input.toPlainText(),
            ),
            state,
        )

        self.panel.set_display_context("zh-CN", "query")
        self.assertEqual(self.panel.title_label.text(), "只读查询助手")
        self.assertEqual(self.panel.preview_badge.text(), "只读")
        self.assertIn("通过工具执行", self.panel.preview_badge.toolTip())

    def test_dynamic_status_and_error_copy_follow_display_language(self):
        self.panel.set_display_context("en", "query")
        self.sink(self._event(1, "model_text_delta", text="visible answer"))
        self._flush()
        self.assertEqual(self.panel.phase_label.text(), "Generating answer…")

        self.sink(self._event(2, "failed", error="fixture error"))
        self._flush()
        self.assertEqual(self.panel.phase_label.text(), "Execution failed")
        self.assertEqual(self.panel.result_status_label.text(), "Failure recorded")
        self.assertIn("Failure: fixture error", self.panel.evidence_view.toPlainText())

    def test_clean_teardown_invalidates_event_facade(self):
        sink = self.panel.event_sink()
        self.panel.close()
        sink(self._event(1, AgentEventType.MODEL_TEXT_DELTA, text="after close"))
        self.app.processEvents()
        self.assertNotIn("after close", self.panel.answer_view.toPlainText())


if __name__ == "__main__":
    unittest.main()
