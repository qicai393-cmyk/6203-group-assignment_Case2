from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from pydantic import BaseModel

from agentdojo.agent_pipeline.agent_pipeline import AgentPipeline
from agentdojo.agent_pipeline.basic_elements import InitQuery, SystemMessage
from agentdojo.agent_pipeline.tool_execution import ToolsExecutionLoop, ToolsExecutor
from agentdojo.approval import (
    ApprovalAction,
    ApprovalFunctionsRuntime,
    ApprovalGate,
    DenyEscalationsReviewer,
    FullApprovePolicy,
    JsonlApprovalLogger,
    ReviewerDecision,
    RuleBasedPolicy,
    ToolCallRequest,
)
from agentdojo.benchmark import benchmark_suite_without_injections
from agentdojo.functions_runtime import Depends, Function, FunctionCall, TaskEnvironment
from agentdojo.logging import OutputLogger
from agentdojo.task_suite.load_suites import get_suite
from agentdojo.types import ChatAssistantMessage, ChatUserMessage, text_content_block_from_string


class _Arguments(BaseModel):
    value: int


class _Environment(TaskEnvironment):
    values: list[int] = []


class _ApprovingReviewer:
    def __init__(self) -> None:
        self.calls = 0

    def review(self, request: ToolCallRequest) -> ReviewerDecision:
        self.calls += 1
        return ReviewerDecision.APPROVE


class _GroundTruthScriptedModel:
    def __init__(self, task) -> None:
        self.task = task
        self.calls_sent = False

    def query(self, query, runtime, env, messages=(), extra_args={}):
        if not self.calls_sent:
            message = ChatAssistantMessage(role="assistant", content=None, tool_calls=self.task.ground_truth(env))
            self.calls_sent = True
        else:
            message = ChatAssistantMessage(
                role="assistant",
                content=[text_content_block_from_string(self.task.GROUND_TRUTH_OUTPUT)],
                tool_calls=None,
            )
        return query, runtime, env, [*messages, message], extra_args


def _writer(environment: _Environment, value: int) -> int:
    environment.values.append(value)
    return value


def _make_writer() -> Function:
    return Function(
        name="write_value",
        description="Write a value into the test environment.",
        parameters=_Arguments,
        dependencies={"environment": Depends(lambda environment: environment)},
        run=_writer,
        full_docstring="",
        return_type=int,
    )


class ApprovalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.log_path = Path(self.temp_dir.name) / "decisions.jsonl"
        self.logger = JsonlApprovalLogger(self.log_path)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_escalation_budget_is_per_gate_and_fail_closed_after_k(self) -> None:
        reviewer = _ApprovingReviewer()
        gate = ApprovalGate(RuleBasedPolicy(), reviewer, 1, self.logger)
        request = ToolCallRequest("suite", "task", "write once", "write_value", {"value": 1}, True, [])

        first = gate.authorize(request)
        second = gate.authorize(request)

        self.assertEqual(first.action, ApprovalAction.AUTO_APPROVE)
        self.assertEqual(first.escalations_used, 1)
        self.assertEqual(second.action, ApprovalAction.AUTO_DENY)
        self.assertEqual(second.reason, "escalation budget exhausted")
        self.assertEqual(reviewer.calls, 1)

    def test_unknown_tool_is_denied_even_by_full_approve(self) -> None:
        gate = ApprovalGate(FullApprovePolicy(), DenyEscalationsReviewer(), 0, self.logger)
        request = ToolCallRequest("suite", "task", "do work", "not_registered", {}, False, [])

        result = gate.authorize(request)

        self.assertEqual(result.action, ApprovalAction.AUTO_DENY)

    def test_runtime_blocks_unapproved_write_and_logs_result(self) -> None:
        gate = ApprovalGate(RuleBasedPolicy(), DenyEscalationsReviewer(), 0, self.logger)
        runtime = ApprovalFunctionsRuntime([_make_writer()], gate)
        runtime.set_task_context("suite", "task", "read only")
        environment = _Environment()

        result, error = runtime.run_function(environment, "write_value", {"value": 9})

        self.assertEqual(result, "")
        self.assertIn("escalation budget exhausted", error or "")
        self.assertEqual(environment.values, [])
        events = [json.loads(line) for line in self.log_path.read_text().splitlines()]
        self.assertEqual([event["event"] for event in events], ["tool_decision", "tool_result"])
        self.assertFalse(events[-1]["executed"])

    def test_tools_executor_logs_unknown_assistant_call(self) -> None:
        gate = ApprovalGate(FullApprovePolicy(), DenyEscalationsReviewer(), 0, self.logger)
        runtime = ApprovalFunctionsRuntime([_make_writer()], gate)
        runtime.set_task_context("suite", "task", "do a task")
        messages = [
            ChatUserMessage(role="user", content=[text_content_block_from_string("do a task")]),
            ChatAssistantMessage(
                role="assistant",
                content=None,
                tool_calls=[FunctionCall(function="missing_tool", args={}, id="call-1")],
            ),
        ]

        _, _, _, messages, _ = ToolsExecutor().query("do a task", runtime, _Environment(), messages)

        self.assertEqual(messages[-1]["role"], "tool")
        self.assertIn("not available", messages[-1]["error"] or "")
        events = [json.loads(line) for line in self.log_path.read_text().splitlines()]
        self.assertEqual(events[0]["function"], "missing_tool")
        self.assertEqual(events[0]["final_action"], "auto_deny")

    def test_benchmark_task_context_and_tool_call_use_the_gate(self) -> None:
        suite = get_suite("v1.2.2", "workspace")
        task = suite.get_user_task_by_id("user_task_0")
        model = _GroundTruthScriptedModel(task)
        pipeline = AgentPipeline(
            [SystemMessage("test system prompt"), InitQuery(), model, ToolsExecutionLoop([ToolsExecutor(), model])]
        )
        pipeline.name = "scripted-test"
        gate_logger = self.logger

        class GatedRuntime(ApprovalFunctionsRuntime):
            def __init__(self, functions):
                super().__init__(
                    functions,
                    ApprovalGate(RuleBasedPolicy(), DenyEscalationsReviewer(), 0, gate_logger),
                )

        trace_dir = Path(self.temp_dir.name) / "benchmark-traces"
        with OutputLogger(str(trace_dir)):
            result = benchmark_suite_without_injections(
                pipeline,
                suite,
                logdir=trace_dir,
                force_rerun=True,
                user_tasks=["user_task_0"],
                benchmark_version="v1.2.2",
                runtime_class=GatedRuntime,
            )

        self.assertTrue(result["utility_results"][("user_task_0", "")])
        self.assertTrue(result["security_results"][("user_task_0", "")])
        events = [json.loads(line) for line in self.log_path.read_text().splitlines()]
        self.assertEqual([event["event"] for event in events], ["tool_decision", "tool_result", "task_end"])
        self.assertEqual(events[0]["suite"], "workspace")
        self.assertEqual(events[0]["task_id"], "user_task_0")
        self.assertEqual(events[0]["function"], "search_calendar_events")
        self.assertEqual(events[0]["final_action"], "auto_approve")
        self.assertEqual(events[1]["execution_status"], "completed")
        self.assertEqual(events[0]["decision_id"], events[1]["decision_id"])
        trace_file = trace_dir / "scripted-test" / "workspace" / "user_task_0" / "none" / "none.json"
        self.assertTrue(trace_file.is_file())


if __name__ == "__main__":
    unittest.main()
