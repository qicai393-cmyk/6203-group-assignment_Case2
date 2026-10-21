# 第一组交付

本包只保留第一组新增或修改的文件，合并到团队 AgentDojo 仓库后运行。
不重复打包 AgentDojo 原框架、依赖环境、无关测试和展示素材。
基础版本：AgentDojo 0.1.35，commit 089ed468cf3ed0322acc66b0211f26d9d90dbf60。

## 安装

有团队源码仓库：将本包文件按相同路径合并，保留仓库其他文件。
没有源码仓库：先获取基础版本，再合并本包。

```bash
git clone https://github.com/ethz-spylab/agentdojo.git
cd agentdojo
git checkout 089ed468cf3ed0322acc66b0211f26d9d90dbf60
# 将本包内容复制到当前目录后继续
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

Windows 激活命令：`.venv\Scripts\activate`。
团队需要固定依赖时，在合并后的仓库运行 `uv sync --frozen`。
.env.example 仅作模板；已有 .env 时保留原配置。不要提交真实密钥。

## 验证和运行

```bash
python -m unittest discover -s tests -p 'test_approval*.py' -v
python -m unittest discover -s tests -p test_batch.py -v
python offline_smoke.py
python run_batch.py batch.example.json
```

前两项共22个离线测试；第三项为固定日历查询；第四项只生成12个运行计划。
真实运行：配置API、修改batch.example.json里的模型和任务，再执行：

```bash
python run_batch.py batch.example.json --execute
```

full_approve不调用审核员；full_escalate在预算k内审核每个已注册工具。
自研策略加入batch.example.json的policies列表，格式为`模块名:类名`。
策略实现`name`及`decide(request) -> PolicyProposal`，提供无参数构造。
审核员修改approval.py中的OpenAIReviewer，保留`review(request) -> ReviewerDecision`。
自研LLM每次响应后调用`record_completion(response, model)`记录用量。

日志有tool_decision、tool_result、llm_usage和task_end事件。
decision_id关联工具审批与结果；task_execution_id关联同一任务的模型用量。
OpenAI agent和reviewer已接入用量采集；其他provider需适配。

汇总命令：

```bash
python -m agentdojo.scripts.summarize_approval 日志路径 --output summary.json
```

美元成本需要价格JSON；单次运行用`--prices 文件路径`，批量配置增加`prices`字段。
格式：`{"模型名":{"input_per_million":实际输入价格,"output_per_million":实际输出价格}}`。
价格单位为美元/百万token；未提供价格时美元成本为null。
严格TCR/HITL需第三组完成未授权动作标注，汇总程序不会用security替代TCR。

## 交付状态

审批关卡、预算、两个基线、批量入口、日志、用量接口和22项离线测试已完成。
英文系统实现报告见System_Implementation_Report.docx。
两个基线的正常任务API联调通过；第二组最终策略/审核员接入后的正常与攻击任务联调尚未完成。本包没有正式安全实验结果。

## API配置

OpenRouter或其他OpenAI兼容服务：按.env.example填写本地.env。
Agent与审核员会使用OPENAI_COMPATIBLE_BASE_URL和OPENAI_COMPATIBLE_API_KEY。
模型从AGENTDOJO_MODEL_ID和AGENTDOJO_REVIEWER_MODEL读取；CLI参数可覆盖模型。
已有配置文件可用--env-file指定，不必复制密钥。

```bash
python -m unittest discover -s tests -p test_api_config.py -v
python -m agentdojo.scripts.approval_benchmark --env-file .env --model openai-compatible -s workspace -ut user_task_0 --strategy full_escalate -k 2
```

API配置传递测试2项通过。已用本机已有OpenRouter配置成功读取模型列表。
两条基线的模拟日历任务、审核员推理和用量日志已完成联网验证；第二组最终模块与攻击任务仍待联调。
密钥不在包里，请勿提交本地.env。
