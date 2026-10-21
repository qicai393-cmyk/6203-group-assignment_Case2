"""Deterministic plumbing check; no LLM, attack evaluation or policy ranking."""
from pathlib import Path
from agentdojo.agent_pipeline.agent_pipeline import AgentPipeline
from agentdojo.agent_pipeline.basic_elements import InitQuery, SystemMessage
from agentdojo.agent_pipeline.tool_execution import ToolsExecutionLoop, ToolsExecutor
from agentdojo.approval import ApprovalFunctionsRuntime, ApprovalGate, FullApprovePolicy, DenyEscalationsReviewer, JsonlApprovalLogger
from agentdojo.benchmark import benchmark_suite_without_injections
from agentdojo.functions_runtime import FunctionCall
from agentdojo.logging import OutputLogger
from agentdojo.task_suite.load_suites import get_suite
from agentdojo.types import ChatAssistantMessage, text_content_block_from_string


class ScriptedCalendarAgent:
    def __init__(self): self.sent = False
    def query(self, query, runtime, env, messages=(), extra_args=None):
        if not self.sent:
            output = ChatAssistantMessage(role='assistant', content=None,
                tool_calls=[FunctionCall(function='search_calendar_events', args={'query': 'Networking event', 'date': '2024-05-26'}, id='smoke-call')])
            self.sent = True
        else:
            output = ChatAssistantMessage(role='assistant', content=[text_content_block_from_string(messages[-1]['content'][0]['content'])], tool_calls=None)
        return query, runtime, env, [*messages, output], extra_args or {}


def main():
    root = Path('runs/offline-smoke')
    root.mkdir(parents=True, exist_ok=True)
    log = root / 'audit.jsonl'
    log.unlink(missing_ok=True)
    logger = JsonlApprovalLogger(log, {'run_id': 'offline-smoke'})
    class Runtime(ApprovalFunctionsRuntime):
        def __init__(self, functions):
            super().__init__(functions, ApprovalGate(FullApprovePolicy(), DenyEscalationsReviewer(), 0, logger))
    agent = ScriptedCalendarAgent()
    pipeline = AgentPipeline([SystemMessage('Offline smoke test'), InitQuery(), agent, ToolsExecutionLoop([ToolsExecutor(), agent])])
    pipeline.name = 'offline-smoke'
    with OutputLogger(str(root)):
        results = benchmark_suite_without_injections(pipeline, get_suite('v1.2.2', 'workspace'), root,
            force_rerun=True, user_tasks=['user_task_0'], benchmark_version='v1.2.2', runtime_class=Runtime)
    print(results)
    print(f'Audit log: {log}')


if __name__ == '__main__': main()
