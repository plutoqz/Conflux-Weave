# Conflux-Weave 设计文档 v0.2

> 文档状态：`Proposed`，供架构评审，不代表已经实现
>
> 日期：2026-08-21
>
> 设计对象：Conflux 的下一代实现，暂定名 Conflux-Weave
>
> 与旧系统关系：新内核、选择性迁移、历史证据保留；不在旧代码上继续无边界修补
>
> 修订说明：在 v0.1 的 Core Capability First 基础上，增加个人持续使用、开源首次成功、产品复杂度预算和 AI Coding 实施节奏；将 W1-W6 调整为产品垂直切片优先

## 0. 执行摘要

Conflux-Weave 定位为一个面向个人研究者的本地优先 Research Agent Workbench。它首先解决一个核心问题：

> 用户提出研究问题后，系统能够在明确预算和权限内，形成可恢复的执行过程，返回可追溯到原始来源的答案，并允许用户判断答案为何可信、哪里仍不确定。

它不是以 Agent 数量、工作流节点数量、评测指标数量或功能页面数量衡量成熟度。Agent 是可替换的决策策略。Research Runtime、Evidence Model、Policy、Budget、State 和 Artifact 构成产品稳定核心；Trace、测试、Evaluation 和内部审计构成围绕核心工作的质量支撑面。

第一版只交付一条黄金路径：

```text
研究问题
  -> 检索本地资料和外部来源
  -> 形成结构化 Evidence
  -> 生成逐声明可追溯的回答
  -> 展示引用、限制、运行状态和恢复动作
```

论文雷达、项目审计、记忆、Skills、MCP、多智能体评审团均为后续能力，不得阻塞黄金路径达到可用、稳定、可评估的状态。

本设计采用以下核心原则：

1. `Core Capability First`：Conflux-Weave 首先交付研究功能；测试、Trace、评估和内部审计没有独立产品路线。
2. `Agent as Strategy`：Agent 是工作流中的受限策略，不是系统本体。
3. `Deterministic Shell, Probabilistic Core`：确定性运行时包围概率性模型调用。
4. `Evidence before Answer`：外部可核验声明必须先绑定 Evidence，再进入最终回答。
5. `Bounded Autonomy`：每次自主决策都有工具白名单、预算、停止条件和升级路径。
6. `State != Trace != Artifact != Evidence`：权威状态、可观测记录、交付产物和证据对象分别建模。
7. `Quality Evidence with Every Core Slice`：每个核心功能切片带有最低必要的验证证据，评估不脱离功能单独扩张。
8. `Frameworks behind Adapters`：LangGraph、Phoenix、Ragas、DeepEval、ChromaDB 和搜索 Skill 都不能成为核心领域合同。
9. `One Golden Path First`：先使一个真实用户任务可靠，再增加产品面。

本设计采用 `Feature-led, Evidence-gated Improvement`：功能价值主导，测试评估伴生，优化决策由证据约束。评测结果用于发现问题、验证修复和控制回归，不把 Conflux-Weave 变成通用 Agent 实验平台。

### 0.1 产品北极星与目标优先级

Conflux-Weave 的产品北极星是：

> **先构建一个本人每周会使用的研究型 Agent 工作台，再将其中稳定、可信、可安装的研究工作流开源，并以可恢复、证据原生、受限自主的实现证明 Agent 工程能力。**

目标优先级固定为：

```text
P0：本人持续使用的研究工作台
P1：别人可以安装、理解和使用的开源应用
P2：能够证明 Agent / AI Application 工程能力的代表项目
```

```text
P0 > P1 > P2
```

P2 不能驱动功能膨胀；P1 不能牺牲 P0 的真实使用体验；质量支撑面只有在服务 P0 或 P1 时才建设。

产品叙事采用：

```text
Research Agent Workbench
        -> Evidence-native Runtime
        -> Bounded Agent Strategy
```

Runtime 是技术核心，Research Agent 是用户价值，Evidence 是可信中心，Trace 和 Evaluation 是质量支撑，不把 Runtime 单独包装成产品。

## 1. 文档职责

本文档回答：

- Conflux-Weave 解决什么问题，以及明确不解决什么。
- 为什么选择新内核而不是继续修补旧系统。
- Agent、Workflow、Evidence、State、Trace、Evaluation 的边界如何划分。
- 如何做到易用、易扩展、易审计、易评估、易优化和可恢复。
- 后续 AI Coding 应按什么顺序实施，每阶段如何验收和停止。
- 专家应使用什么标准评审本设计。
- 新设计与现有简历内容的关系是什么。

本文档不承担：

- 宣称 Conflux-Weave 已经实现。
- 为尚未冻结的模型、Provider 或搜索服务作选型承诺。
- 复用旧 Conflux 的历史结果作为新实现的通过证据。
- 给出数据库迁移脚本、完整 API Schema 或页面视觉稿。

## 2. 问题定义与设计假设

### 2.1 当前问题

根据用户对当前产品的实际使用判断，旧 Conflux 已经出现“功能存在但产品不可用、服务难以恢复、修改影响难以预测”的问题。

本设计将以下内容视为需要在重写前验证的候选结构问题，而不是已经由本文件证明的根因：

1. 产品范围过宽，研究查询、论文雷达、项目审计、对话、记忆、Skills、MCP 和多智能体能力同时进入主系统。
2. Web/API、运行状态、工作流和具体领域能力边界不足，导致局部故障容易扩大为全局不可用。
3. 状态、Trace、日志和 Artifact 之间缺少单一且可解释的语义。
4. 评测晚于功能扩展，系统复杂度没有持续接受数据约束。
5. LangGraph 或其他编排细节可能泄漏到核心状态和业务模块，使替换成本升高。

### 2.2 方法假设

本设计的可证伪假设为：

> 使用小型模块化单体、稳定内部合同、显式状态机、证据原生数据模型、受限 Agent 策略和随功能切片建设的最低必要质量证据，可以在保留研究能力上限的同时，降低首次使用成本、故障定位成本、组件替换成本和回归风险。

以下情况将否定或削弱该假设：

- 黄金路径仍需理解多个内部模块才能完成一次查询。
- 替换 Retriever、Model Provider 或 Workflow Strategy 需要修改核心状态语义。
- Phoenix、Ragas 或 DeepEval 不可用时生产请求无法执行。
- 运行失败后无法根据持久状态判断能否恢复、从何处恢复及哪些副作用可能重复。
- 关键声明无法回到原始 Source Snapshot 和精确 Evidence Locator。
- 增加 Agent 节点后没有可重复的质量收益证据。

### 2.3 成功定义

Conflux-Weave 达到首版产品成功，需要同时满足：

- 新用户在完成必要 Provider 配置后，可以通过一个入口发起研究查询。
- 用户始终能识别任务处于排队、执行、等待确认、完成、部分完成、取消或失败中的哪一种状态。
- 完成的回答包含可打开的来源和声明级引用；证据不足时降低措辞或显式标记。
- 服务进程中断后，已持久化任务能够恢复或给出确定、可行动的终态。
- 核心路径不要求 Phoenix、Ragas、DeepEval、LangGraph、MCP 或 ChromaDB 必须在线。
- 第一版没有为了未来扩展提前建设通用插件平台、微服务系统或多租户系统。
- 当核心功能需要修改时，有最低必要的测试和评估证据判断是否解决问题、引入回归或满足发布条件。
- 本人能够在连续真实研究任务中持续使用核心路径，而不是只完成一次演示。
- 新用户能够在不阅读架构文档的情况下完成第一次查询并获得带引用的结果。

## 3. 目标、非目标与用户范围

### 3.1 目标用户

- 首要用户：项目作者本人，使用 Conflux-Weave 完成论文发现、论文比较和本地项目证据问答。
- 需要查找论文、技术资料和本地项目材料的研究生或个人研究者。
- 需要保留来源、引用、实验记录和决策依据的研究工程人员。
- 愿意配置模型 Provider，并能理解来源质量、证据不足和预算限制的技术型用户。

### 3.2 首版用户任务

首版只冻结三个任务，按优先级排序：

1. 输入研究问题，获得带声明级引用和限制说明的答案。
2. 查看任务正在执行什么、花费多少、为什么失败，以及如何恢复。
3. 查看来源、证据和限制，并在失败或证据不足时采取明确的恢复动作。

### 3.3 个人真实研究任务

首版至少冻结以下两类本人每周可能重复使用的任务：

1. 论文发现：查找指定主题、时间范围和相关性边界内的论文，区分真正相关与仅关键词相似的结果。
2. 论文或项目证据问答：比较多篇论文的方法、数据、实验和局限，或检查本地代码、文档、测试和提交是否支持某个实现判断。

每个任务必须登记：

```text
输入 -> 可交付结果 -> 必要证据 -> 允许确认点 -> 可接受降级 -> 完成判定
```

黄金路径不是抽象 Pipeline，而是本人实际会重复执行的研究动作。

### 3.4 个人使用验收

在 W4 结束前，使用真实研究问题完成至少 5 次运行，覆盖上述两类任务中的至少两类。验收关注：

- 不查看源码和终端即可完成主要流程。
- 结果被实际用于笔记、综述、实验决策或项目汇报中的至少一种。
- 失败时能够理解原因并恢复，或得到明确的部分结果。
- 运行状态、引用和限制足以支持下一步研究判断。

这些是拟定的最小验收门槛，实施前可根据真实使用频率调整，但不能删除“重复使用”和“实际研究用途”两个条件。

### 3.5 首版非目标

- 不做通用聊天机器人。
- 不做完整项目管理工具。
- 不默认启动多智能体协商或评审团。
- 不做插件市场、远程插件沙箱或可视化工作流编排器。
- 不做多租户 SaaS、微服务、Redis、Kafka 或分布式调度。
- 不把记忆、Skills 和 MCP 放入首版关键路径。
- 不自动修改用户项目文件、Git 历史、研究计划或权威知识库。
- 不以报告长度、调用次数、Agent 数量或页面数量作为质量指标。
- 不建设面向普通用户的实验管理、指标看板或通用 Agent 评测平台。
- 不为产生指标而运行没有明确功能问题和优化决策的实验。

### 3.6 开源目标

开源用户的最小成功路径为：

```text
Fresh Clone -> Install -> Configure One Provider -> Start
    -> Ask First Question -> Cited Answer
```

W5 的开源验收要求：

- 清洁环境下无需阅读架构文档即可完成第一次查询。
- 默认只需配置一个模型 Provider；网络搜索和向量索引有明确降级说明。
- 提供最小示例语料、离线 smoke 模式和一条可复制命令。
- README 首屏先说明研究任务、输入和输出，再介绍 Runtime 架构。
- 不把 API Key、用户目录、研究内容或本地数据库提交到仓库。
- Phoenix、Ragas、DeepEval 不属于默认安装依赖。
- 首次失败有可行动的诊断信息，版本、隐私和数据存储边界明确。

### 3.7 后续候选能力

只有黄金路径通过真实验收后，才按独立垂直切片评估：

- 论文雷达与论文入库。
- 项目证据审计和周期总结。
- 经过确认的用户偏好记忆。
- 声明式 Skills。
- MCP Server 或 MCP Client。
- 条件触发的 Planner、Verifier 或多模型评审。

每个候选能力都必须回答：现有能力为何不能承担、解决哪个真实用户问题、增加了什么新合同、如何验证用户收益、失败会影响哪些主路径。

## 4. 方案比较与决策

### 4.1 方案 A：继续收敛旧 Conflux

优点：

- 已有功能、数据和测试可以直接复用。
- 短期无需建设兼容导入工具。

局限：

- 新边界会长期受历史调用关系和状态语义约束。
- 很难判断修复是在降低复杂度，还是增加新的兼容层。
- 在可用性尚未恢复前，继续扩展评测基础设施的边际成本较高。

适用条件：旧系统仍有稳定黄金路径，且可通过少量边界调整恢复。当前用户判断不支持这一前提。

### 4.2 方案 B：完整功能重写

优点：

- 可以清除历史技术债。
- 可以重新统一技术栈和开发规范。

局限：

- 容易把旧系统的全部产品范围重新实现一遍，形成 Big Bang Rewrite。
- 首次可用时间长，旧系统中已验证的协议和评测资产可能被遗漏。
- 没有垂直闭环约束时，新项目仍可能重复臃肿过程。

### 4.3 方案 C：新内核 + 黄金路径 + 选择性迁移

优点：

- 新核心不继承旧内部依赖。
- 从第一条链路建立 Trace 和 Evaluation。
- 旧系统只作为数据、算法和评测资产来源，不作为运行依赖。
- 每迁移一个能力都需要通过独立收益门禁。

局限：

- 一段时间内会存在两个代码产品。
- 必须明确哪些旧资产可以导入，不能直接复制整模块。
- 需要冻结旧系统，避免双边持续开发。

### 4.4 决策

选择方案 C。

Conflux-Weave 建立新包、新数据库和新启动入口。旧 Conflux 进入维护冻结状态，只允许：

- 导出历史数据和不可变评测资产。
- 修复阻塞导出的严重问题。
- 查询历史实现和证据。

禁止让 Conflux-Weave 在运行时 import 旧 Conflux 模块。代码迁移采用“理解语义后重新实现稳定合同”，而不是复制调用链。

### 4.5 首版产品复杂度预算

为了防止重写再次变成完整功能迁移，W0-W5 的首版预算冻结为：

```text
一个 FastAPI 应用
一个 SQLite 权威数据库
一个 Worker 和一个持久队列
一个默认 Fixed Workflow
一个 Web Search Adapter
一个主要向量检索 Adapter
一个主要用户入口
一个默认报告/引用输出格式
```

以下内容不得在首版并行建设：

- 多个 Agent 编排框架。
- 多个 Web 搜索 Provider 的统一平台。
- 实验管理 UI 或通用 Evaluator 平台。
- 论文雷达、项目审计、Memory、Skills、MCP 的同时迁移。
- 多 Worker、远程执行、插件市场或多租户能力。

新增核心概念或依赖必须提交一条简短决策记录，回答：

```text
真实用户问题是什么？现有能力为什么不能承担？
失败是否影响黄金路径？如何回滚？
是否已有至少两个真实消费者？
```

不能回答这些问题的抽象保留在实验或局部实现中，不进入稳定 Core。

## 5. Agent 设计哲学

### 5.1 Agent 不是系统中心

系统中心是一个可恢复、可审计的任务运行时。Agent 只负责在明确授权范围内选择下一步或生成结构化候选结果。

```text
错误结构：UI -> Agent -> 所有工具、状态、存储和业务规则

目标结构：UI -> Application -> Runtime
                              -> Workflow Strategy
                              -> Capability Ports
                              -> Policy / Budget / Evidence
```

任何 Agent 都不能：

- 直接修改权威 Run 状态。
- 绕过 Tool Schema、Policy 或预算。
- 直接写入最终 Evidence Ledger。
- 自己决定不可逆副作用已经成功。
- 通过自然语言描述代替结构化失败状态。

### 5.2 确定性外壳

以下职责必须由确定性代码承担：

- 输入和输出 Schema 校验。
- 状态转换、幂等键、租约、取消和超时。
- 工具权限、路径权限和副作用门禁。
- 预算预扣、使用量记账和硬停止。
- 来源身份、内容哈希、引用定位和 Artifact 提交。
- 评价数据集版本、运行 Manifest 和指标归档。

模型适合承担：

- 查询解释和候选子问题生成。
- 语义相关性判断。
- 证据支持、矛盾和限制分析。
- 在证据约束下综合答案。
- 在允许工具集合中提出下一动作。

### 5.3 自主等级

| 等级 | 行为 | 首版用途 |
|---|---|---|
| L0 | 完全确定性流程 | 数据导入、索引、引用编译、恢复 |
| L1 | 固定工作流中的模型节点 | 默认研究查询 |
| L2 | 有限步 Planner 选择工具或补证 | 条件启用、必须评测 |
| L3 | 多角色或多模型协作 | 后置，需证明相对 L1/L2 的收益 |

默认使用 L1。只有固定评测集证明质量收益超过成本、延迟和失败率代价时，才提升自主等级。

### 5.4 上下文是编译结果

Agent 不读取完整数据库对象或无限历史。`ContextBuilder` 根据当前 Step、预算、权限和 Evidence 引用生成版本化 `ContextBundle`：

```text
ContextBundle
  task_summary
  current_goal
  allowed_tools
  selected_evidence_refs
  prior_decisions
  budget_remaining
  output_schema
  stop_conditions
```

每个 Bundle 保存构建器版本、输入引用和内容哈希。上下文压缩不得静默改写证据含义。

### 5.5 计划是候选，不是事实

Planner 输出 `PlanProposal`。Runtime 校验工具存在性、参数 Schema、预算、循环上限、权限和依赖后，才生成可执行 `StepPlan`。

计划修改必须记录：

- 原计划版本。
- 触发修改的失败或新 Evidence。
- 新增、删除和重排步骤。
- 预算变化。
- 策略和模型版本。

### 5.6 Verifier 是门禁，不是装饰

优先使用确定性验证：Schema、引用存在性、来源身份、哈希、覆盖率和权限。只有语义支持关系无法确定时才调用模型 Verifier。

Verifier 可以输出 `accepted`、`rejected`、`uncertain`，不得把模型置信度等价为事实真值。`uncertain` 必须导致降低措辞、补证或向用户暴露限制。

## 6. 总体架构

```text
                         产品主链路

┌──────────────────────────────────────────────────────────────┐
│                    Web UI / CLI                              │
├──────────────────────────────────────────────────────────────┤
│              FastAPI Application Boundary                    │
│ Commands | Queries | SSE Event Projection | Auth/Config      │
├──────────────────────────────────────────────────────────────┤
│                  Application Use Cases                       │
│ SubmitResearch | ResumeRun | CancelRun | InspectEvidence     │
├──────────────────────────────────────────────────────────────┤
│                    Weave Runtime Core                         │
│ Task | Run | Step | State Machine | Scheduler | Checkpoint   │
│ Policy | Budget | Tool Gateway | Artifact Commit             │
├───────────────────────┬──────────────────────────────────────┤
│ Workflow Strategies   │ Evidence Domain                      │
│ Fixed Research        │ SourceSnapshot | Evidence | Claim    │
│ Bounded Planner       │ Citation | Assessment | Provenance   │
├───────────────────────┴──────────────────────────────────────┤
│ Capability Ports                                             │
│ Model | Search | Fetch | Parse | Retrieve | Rerank | Render  │
├──────────────────────────────────────────────────────────────┤
│ Infrastructure Adapters                                      │
│ SQLite | File Artifact Store | FTS/BM25 | Vector Index       │
│ Provider SDKs | Search Providers | LangGraph Strategy        │
└────────────────────────────┬─────────────────────────────────┘
                             │ Events / Traces / Artifacts
                             v
                 ┌──────────────────────────────┐
                 │        质量支撑面             │
                 │ Tests | OTel/Phoenix         │
                 │ Evaluators | Regression      │
                 │ Internal Audit Reports       │
                 └──────────────────────────────┘
```

质量支撑面观察和评价产品主链路，但不拥有 Run 状态、不提交业务 Artifact，也不参与在线请求的成功判定。它可以在 CI 或发布流程中阻止有证据的退化进入产品，但其故障不能阻断正常用户请求。

### 6.1 依赖方向

允许：

```text
interface -> application -> core
strategy -> core contracts
adapter -> ports + external SDK
evaluation adapter -> evaluation contracts + external framework
```

禁止：

```text
core -> FastAPI / LangGraph / Phoenix / Ragas / DeepEval / ChromaDB
evidence domain -> UI or Provider SDK
capability adapter -> Runtime repository internals
workflow strategy -> SQLite connection
quality support -> Runtime write path or user request completion
```

产品核心向质量支撑面暴露稳定事件、Trace 和 Artifact 引用；质量支撑面不得反向成为产品核心依赖。

### 6.2 模块建议

```text
conflux_weave/
  core/
    tasks.py
    runs.py
    steps.py
    state_machine.py
    policy.py
    budget.py
    errors.py
  evidence/
    sources.py
    evidence.py
    claims.py
    citations.py
    assessments.py
  application/
    commands.py
    queries.py
    services.py
  workflows/
    fixed_research.py
    bounded_planner.py
  capabilities/
    ports.py
    contracts.py
  adapters/
    models/
    search/
    retrieval/
    persistence/
    artifacts/
  observability/
    otel.py
    openinference.py
  evaluation/
    contracts.py
    datasets.py
    metrics/
    adapters/
      ragas.py
      deepeval.py
  api/
  ui/
```

目录只在对应垂直切片实现时创建，不一次性生成空框架。

## 7. 核心领域合同

### 7.1 Task、Run 与 Step

```python
class TaskSpec:
    task_id: str
    kind: str
    input: dict
    requested_policy: str
    idempotency_key: str

class RunRecord:
    run_id: str
    task_id: str
    status: str
    workflow_version: str
    config_snapshot_ref: str
    budget: "BudgetLedger"
    created_at: str
    updated_at: str

class StepRecord:
    step_id: str
    run_id: str
    kind: str
    attempt: int
    status: str
    input_refs: list[str]
    output_refs: list[str]
    error_ref: str | None
```

`Task` 表示用户意图，`Run` 表示一次冻结配置下的执行，`Step` 表示可检查、可重试或可跳过的最小恢复单元。

### 7.2 状态机

Run 状态：

```text
accepted -> queued -> running
running -> waiting_for_user | cancelling | succeeded
running -> partial | failed
waiting_for_user -> queued | cancelled | expired
cancelling -> cancelled | failed
```

约束：

- 所有状态变化由 Runtime 事务提交。
- 终态不可被普通重试重新打开；重试创建新 attempt 或新 Run。
- `partial` 表示存在可交付产物但未满足全部交付条件。
- `failed` 必须带结构化 ErrorRecord 和恢复建议。
- `succeeded` 必须在最终 Artifact 与必要 Evidence 引用原子可见后写入。

### 7.3 Artifact

```python
class ArtifactRef:
    artifact_id: str
    media_type: str
    content_hash: str
    storage_uri: str
    producer_step_id: str
    schema_version: str
```

Artifact 一旦提交不可原位修改。新版本生成新 ID，并通过 lineage 关联旧版本。

### 7.4 Evidence、Claim 与 Citation

```python
class SourceSnapshot:
    source_id: str
    source_type: str
    canonical_uri: str | None
    acquired_at: str
    content_hash: str
    artifact_ref: str

class EvidenceRef:
    evidence_id: str
    source_snapshot_id: str
    locator: dict
    quote: str
    extraction_method: str

class Claim:
    claim_id: str
    text: str
    claim_type: str
    importance: str
    generated_by_step: str

class ClaimAssessment:
    claim_id: str
    evidence_ids: list[str]
    relation: str
    verdict: str
    rationale: str
    evaluator_ref: str

class Citation:
    citation_id: str
    claim_id: str
    evidence_id: str
    display_index: int
```

`locator` 必须能定位到页码、段落、字符范围、代码行、提交或测试产物中的至少一种。仅保存 URL 不构成完整 Evidence。

### 7.5 Tool Contract

```python
class ToolSpec:
    tool_id: str
    version: str
    input_schema: dict
    output_schema: dict
    permissions: list[str]
    side_effect: str
    timeout_seconds: int

class ToolResult:
    status: str
    output: dict | None
    evidence_refs: list[str]
    artifact_refs: list[str]
    usage: dict
    error: dict | None
```

工具必须声明副作用语义：`none`、`idempotent_write`、`non_idempotent_write`。非幂等写操作默认需要人工确认，并保存外部请求 ID。

### 7.6 Budget 与 Policy

预算至少覆盖：

- 墙钟时间。
- 模型输入和输出 Token。
- 金额估算。
- 工具调用次数。
- 搜索、下载和补证轮数。
- 并发数。

预算必须预留最终综合、确定性引用编译和 Artifact 提交所需资源。前序步骤不得耗尽所有预算后仍将 Run 标记为正常完成。

Policy 决定：

- 允许的工具和 Provider。
- 可读写路径。
- 是否允许网络。
- 哪些行为需要人工确认。
- 证据不足时是补证、降级、部分交付还是失败。

## 8. 执行与恢复语义

### 8.1 标准执行流程

```text
1. Accept
   校验请求，生成 idempotency key
2. Freeze
   冻结代码 revision、workflow、模型、Prompt、预算和数据版本
3. Prepare
   创建 Run、初始 Step 和 config manifest
4. Execute
   Runtime 调用 Workflow Strategy，Strategy 通过 Tool Gateway 请求能力
5. Checkpoint
   每个外部副作用和高成本步骤前后提交状态
6. Assess
   生成 Claim、Evidence 关系和质量结果
7. Finalize
   确定性编译引用，提交 Artifact
8. Publish
   原子写入终态，并输出用户可理解的摘要
```

### 8.2 恢复级别

首版承诺 Step 级重放，不承诺任意模型内部状态精确续跑。

恢复时：

- 已提交且输出完整的 Step 不重复。
- 无副作用、未提交完成的 Step 可以重放。
- 有外部副作用的 Step 必须通过幂等键查询或进入人工确认。
- Provider 请求在未获得可验证响应 ID 时视为结果未知，不假装未执行。
- 恢复事件记录原 Worker、最后心跳、恢复原因和新 attempt。

### 8.3 并发与租约

- SQLite 是权威队列和状态存储。
- Worker 使用有期限 lease claim Step。
- 心跳只延长当前 attempt 的 lease。
- lease 过期后，新 Worker 必须先核对副作用状态，再决定重放。
- 同一 `idempotency_key` 不得创建多个逻辑 Task。

### 8.4 失败语义

```python
class ErrorRecord:
    code: str
    category: str
    stage: str
    retryable: bool
    user_message: str
    technical_detail_ref: str
    affected_artifact_refs: list[str]
    recovery_action: str
```

失败分类至少包括：输入、配置、Provider、网络、工具、预算、策略、证据、持久化、取消和未知系统错误。

## 9. 检索与证据链路

### 9.1 检索管线

采用清晰的阶段合同：

```text
query
  -> normalize/decompose
  -> retrieve candidates
  -> deterministic filter
  -> rank/rerank
  -> project bounded context
  -> evidence extraction
```

Dense、BM25、RRF、Reranker 均是可替换策略。`512/128` 父子块是可评测配置，不进入核心语义。

### 9.2 来源层级

来源至少区分：

- 本地已登记文档。
- 论文元数据。
- 论文全文。
- Web 搜索结果摘要。
- Web 页面正文快照。
- 模型参数化知识。

搜索摘要不能自动升级为正文证据；模型参数化知识不能伪装成外部来源。

### 9.3 Web Search 适配

Web Search 通过 `SearchPort` 接入。AnySearch Skill、DDGS 或其他服务都只能作为 Provider Adapter 候选。

候选适配器必须经过同一评测：

- 搜索成功率和超时率。
- 目标来源覆盖。
- 结果重复率。
- 正文可获取率。
- 来源身份和时间字段完整度。
- 延迟、费用和凭证要求。

只有在两个以上真实查询场景中体现稳定价值，才考虑把某个 Skill 升级为首选适配器。Skill 的 Prompt、脚本或外部依赖不能泄漏到 Core。

### 9.4 Evidence 门禁

最终回答中的外部可核验 Claim 必须满足：

1. 至少绑定一个存在的 EvidenceRef。
2. Citation 可以反向定位到 SourceSnapshot。
3. Evidence Locator 可由确定性程序解析。
4. ClaimAssessment 没有将 `contradicts` 误作 `supports`。
5. 证据不足或冲突时，回答显式表达限制。

## 10. 持久化设计

### 10.1 权威与派生数据

SQLite 保存权威状态：

- Task、Run、Step、Attempt、Lease。
- Config Snapshot 和 Budget Ledger。
- Source Snapshot、Evidence、Claim、Assessment、Citation。
- Artifact 元数据和 lineage。
- Policy Decision、Approval、Evaluation Run 索引。

文件 Artifact Store 保存不可变大对象：

- 原始来源快照。
- 解析文本。
- 报告、Evidence sidecar、Trace export。
- 评测原始响应和报告。

FTS/BM25 和向量索引是派生数据，可由 Source Snapshot 重建。ChromaDB 可以继续作为向量适配器，但不能成为来源、任务或证据的权威存储。

### 10.2 数据库原则

- Migration 单向、可重复检测、带 schema version。
- 写入通过 Repository，不允许业务模块持有裸连接。
- Run 状态和最终 Artifact 引用在同一事务可见。
- 不使用数据库和 YAML 双写同一权威配置。
- 删除采用保留期或 tombstone；评测和审计引用的历史对象不可直接覆盖。

### 10.3 旧数据迁移

迁移采用导入器，不让新系统读取旧数据库作为运行依赖：

```text
old export -> validate -> normalize -> import manifest -> new store
```

每批迁移保存源路径、旧 schema、记录数、拒绝项、内容哈希和导入版本。历史 Run 可以标记为 `legacy_imported`，不得伪装成 Weave 原生 Trace。

## 11. API 与产品体验

### 11.1 单一服务边界

首版只有一个 FastAPI ASGI 应用、一个端口和一个 OpenAPI 文档。Worker 可以作为同包中的独立进程运行，但共享同一数据库合同。

最小 API：

```text
POST   /api/v1/tasks/research
GET    /api/v1/runs/{run_id}
POST   /api/v1/runs/{run_id}/cancel
POST   /api/v1/runs/{run_id}/resume
GET    /api/v1/runs/{run_id}/events?after={cursor}
GET    /api/v1/runs/{run_id}/artifacts
GET    /api/v1/evidence/{evidence_id}
GET    /api/v1/health/ready
```

评测不进入首版公共产品 API。开发者通过内部 CLI、测试命令或 CI 运行，例如：

```text
conflux-weave dev eval retrieval
conflux-weave dev eval research
```

### 11.2 SSE 语义

- SSE 是持久事件的投影，不是唯一状态源。
- 客户端断线后使用 cursor 续读。
- 重复事件通过 event ID 去重。
- 事件流丢失时，客户端重新读取 Run 快照。
- 终态由 Run API 确认，不以最后一条浏览器消息推断。

### 11.3 首版界面

首屏直接是研究工作台，不设置营销页：

1. 查询输入和模式选择。
2. 当前 Run 的紧凑进度、预算和取消操作。
3. 回答正文与数字引用。
4. 可展开 Evidence、限制和运行详情。
5. 历史 Runs 与重新打开入口。

高级配置和 Trace 不占据默认主视图。首版不建设实验管理、指标看板或 Evaluator 配置界面。系统状态使用用户语言表达，并提供技术详情展开项。

### 11.4 易用性约束

- 首次启动只要求一个明确的模型配置流程。
- Provider 或索引未就绪时，在提交前阻止任务并给出修复动作。
- 不向用户暴露 LangGraph node、内部 Python 异常或数据库状态名作为主要错误信息。
- 所有长时任务都可取消、刷新页面后继续查看、关闭浏览器后重新进入。
- 320px 宽度和 200% 文本缩放下无关键操作丢失或文本遮挡。
- 键盘可完成提交、取消、打开引用和查看错误详情。

## 12. 质量支撑面：Observability 与 Evaluation

质量支撑面只服务于 Conflux-Weave 核心功能的正确性、稳定性和持续优化，不形成独立产品路线。任何测试、Trace、评估或内部审计工作都必须关联一个用户功能、已识别风险或发布决策；不能为了指标完整、框架覆盖或实验数量而建设。

### 12.1 四类对象必须分离

| 类型 | 用途 | 权威性 |
|---|---|---|
| State/Event | 恢复、并发和终态判断 | 运行权威 |
| Trace/Span | 性能、调用链和诊断 | 观测记录 |
| Artifact | 交付结果、原始响应和不可变文件 | 内容产物权威 |
| Evidence | 来源定位、Claim 支持关系和引用 | 证据关系权威 |

Trace 丢失不能改变 Run 终态；State 成功也不能替代 Artifact 和 Evidence 验收。

Evidence 可追溯是用户获得可信研究结果所需的产品能力。运行审计、质量报告和评测结果属于内部支撑；后续“项目审计”只有在独立用户需求成立时才作为产品垂直切片建设。三者不能因为都使用“审计”一词而混为同一模块。

### 12.2 按核心功能切片接入

- W1：OpenTelemetry、必要的 OpenInference 语义、确定性测试和最小 Custom Evaluator，用于诊断 Runtime、状态与恢复。
- W1 可选开发配置：Phoenix 本地 Trace UI。Phoenix 接入不得延迟 Runtime 黄金骨架验收。
- W2：Ragas Adapter 随真实 Retrieval/Evidence 功能接入，仅回答已经出现的检索或上下文质量问题。
- W3：DeepEval Adapter 随 Bounded Planner 接入，仅回答 Agent 工具选择、参数、轨迹和停止条件问题。
- 后续 Custom Evaluators：只在 Evidence、Citation、Recovery 或其他核心功能存在明确验收需求时增加。

生产 Runtime 只通过 OTel/OTLP 导出。Phoenix 不可用时，任务继续执行，并记录 telemetry drop 计数。

### 12.3 Trace 业务字段

每个 Span 在适用时记录：

```text
task_id, run_id, step_id, attempt
workflow_id, workflow_version
strategy_id, strategy_version
model_provider, model_name, prompt_version
tool_id, tool_version
input_artifact_refs, output_artifact_refs
evidence_ids, claim_ids
latency_ms, token_usage, estimated_cost
budget_before, budget_after
status, error_code
```

不得把 Secret、完整敏感文档或未经授权的用户目录内容写入 Trace。

### 12.4 内部 Eval Contract

```python
class EvaluationCase:
    case_id: str
    dataset_version: str
    input: dict
    expected: dict
    metadata: dict

class EvaluationRun:
    evaluation_run_id: str
    case_ids: list[str]
    system_manifest_ref: str
    status: str
    metric_results: list["MetricResult"]

class MetricResult:
    metric_id: str
    value: float | int | str | None
    status: str
    evaluator_id: str
    evaluator_version: str
    evidence_artifact_refs: list[str]
    explanation: str | None
```

Ragas、DeepEval 和自定义 Evaluator 的输出都转换为 `MetricResult`。第三方对象不能写入 Runtime 权威状态；评测结果作为带 Manifest 的内部质量 Artifact 保存。

### 12.5 按功能问题选择评测层级

三层评测是可选诊断工具箱，不是每个版本都必须完整运行的固定流水线。当前功能问题位于哪一层，就只使用能够支撑决策的最低必要指标。

#### Component

- Retriever 的 Hit@K、Recall@K、nDCG@K、MRR。
- Search Adapter 的成功率、来源覆盖、重复率、正文获取率。
- Tool 的选择正确率、参数正确率和错误分类准确率。

#### Workflow / Agent

- Task Success。
- Plan Validity。
- Step Efficiency。
- Tool Selection / Argument Correctness。
- Stop Condition Compliance。
- Budget Compliance。
- Recovery Success。

#### Research System

- Evidence Coverage。
- Unsupported Claim Rate。
- Citation Correctness。
- Citation Completeness。
- Contradiction Handling。
- Answer Completeness。
- Source Diversity 与 Source Directness。
- Quality / Latency / Cost Pareto。

### 12.6 评估准入合同

新增或运行一项非例行评估前，必须记录：

```text
Feature          服务哪个核心功能
Risk             当前需要控制什么风险
Question         评估要回答什么问题
Metric           使用什么指标及其局限
Decision         结果将触发什么产品或工程决策
ActionOnFail     失败后修改、回滚或阻止什么
StopCondition    什么时候停止继续评估
```

如果评估结果不会改变实现、优化、回滚或发布决策，就不应运行该评估。评测框架接入连续阻塞核心功能一个验收点时，先退回确定性 Custom Evaluator，并将框架接入推迟到存在真实消费者的阶段。

### 12.7 指标优先级

首版硬门禁优先使用确定性或带人工真值的指标：

- 引用是否存在且可定位。
- Claim 是否绑定 Evidence。
- 检索是否命中标注来源。
- 工具和参数是否符合期望轨迹。
- 状态恢复是否一致。
- 预算是否超限。

LLM-as-judge 用于语义支持、答案完整性和表达质量等难以确定性判断的指标，但必须固定：

- Provider 和模型版本。
- Prompt 和 Rubric 版本。
- 温度、采样和预算。
- 原始响应和失败样本。
- 与人工标注的一致性抽检。

指标是用户价值和功能风险的代理，不是最终目标。Hit@K、Faithfulness 或 Task Success 提高，不能覆盖用户任务失败、回答不可读、Evidence 不可定位或服务不可恢复。

### 12.8 数据集分层

```text
smoke       每次提交，少量确定性案例，无付费或低成本
regression  PR 或合并前，冻结回放和代表案例
benchmark   夜间或发布前，完整固定集和多策略比较
live        明确授权后，真实 Provider、真实网络和真实数据
```

现有三语 30 题检索集可以作为迁移候选，但必须保存原始数据、标注规则和历史运行，不得直接把旧系统的 `96.7%` 归为 Weave 结果。Weave 必须在冻结 revision 下重新运行。

### 12.9 阶段验收优先级

每个核心功能切片按以下顺序验收：

1. 用户任务是否完成。
2. 结果是否正确、可用并具备所需 Evidence。
3. 失败是否可解释、可恢复。
4. 成本和延迟是否满足该任务边界。
5. 内部诊断指标是否改善。

前四层未通过时，第五层指标不能使阶段通过。禁止以 Trace 完整、评测框架运行成功、引用率高或平均分提高替代真实可交付结果。

### 12.10 CI 门禁

- Core 单元和合同测试不安装 Phoenix、Ragas、DeepEval。
- Eval Adapter 有各自 extras 和合同测试。
- Smoke Eval 进入普通 CI。
- 高成本 Eval 独立执行，失败不被普通重试掩盖。
- 指标退化必须输出受影响案例，不只输出平均分。
- 阈值调整要提交理由和基线差异，不能为了通过测试事后放宽。
- 每个非通用门禁必须引用对应 Feature、Risk、Decision 和 ActionOnFail。
- CI 可以阻止退化代码合并或发布，但在线评测服务不能参与生产请求成功判定。

## 13. 易扩展与易优化设计

### 13.1 扩展点

首版只稳定以下 Ports：

- `ModelPort`
- `SearchPort`
- `DocumentFetchPort`
- `ParserPort`
- `RetrieverPort`
- `RerankerPort`
- `ArtifactStorePort`
- `WorkflowStrategy`
- `EvaluatorPort`

新增 Port 必须存在两个真实消费者，或者替换一个已经造成测试困难的外部依赖。否则保留为具体实现。

### 13.2 扩展验收

替换一个 Adapter 应满足：

- Core 无修改。
- 输入输出合同测试通过。
- 错误被规范化为统一分类。
- Trace 字段和评测结果仍可比较。
- 旧 Adapter 可以通过配置恢复。
- 性能和质量差异有实验报告。

### 13.3 可优化性

优化单位必须对应独立 Span 和独立指标：

```text
query understanding
search
fetch
parse
retrieve
rerank
context build
plan
tool execute
evidence assess
synthesize
citation compile
artifact commit
```

任何优化提案都应说明：

- 优化哪个阶段。
- 当前瓶颈证据。
- 预期改变的指标。
- 可能退化的指标。
- 基线、实验配置和退出条件。

不接受“换更强模型”“增加重试”“增加 Agent”作为没有阶段证据的默认优化。

## 14. 安全与治理

- Secret 只从环境、系统 Keyring 或受控 Secret Provider 获取。
- Tool 按文件、网络、模型和副作用权限声明能力。
- 所有用户路径使用解析后的允许根目录校验。
- 外部网页、PDF 和工具输出均视为不可信输入，不能覆盖系统 Policy。
- 写文件、修改 Git、写入知识库等行为需要显式 Capability 和批准记录。
- 日志、Trace 和评测数据默认脱敏，原始内容通过 Artifact 权限访问。
- 不把模型输出直接作为 SQL、Shell 或文件路径执行。

## 15. 技术选型

### 15.1 首版建议

| 领域 | 选择 | 地位 |
|---|---|---|
| 语言 | Python 3.11+ | 主实现语言 |
| HTTP | FastAPI + Uvicorn | 单一应用边界 |
| 权威状态 | SQLite | Task/Run/Evidence 权威存储；质量结果作为内部 Artifact |
| Artifact | 本地内容寻址目录 | 不可变大对象 |
| 关键词检索 | SQLite FTS5 或 BM25 Adapter | 可替换派生索引 |
| 向量检索 | ChromaDB Adapter | 可选派生索引 |
| 工作流 | 自有 Runtime + Fixed Strategy | 核心默认 |
| Agent 编排 | LangGraph Adapter | 可选策略实现 |
| Trace | OpenTelemetry + OpenInference | 强制协议 |
| Trace UI | Phoenix | 可选开发设施 |
| RAG Eval | Ragas Adapter | 开发/CI 设施 |
| Agent Eval | DeepEval Adapter | 开发/CI 设施 |

### 15.2 LangGraph 边界

LangGraph 可以实现 `WorkflowStrategy`，但不得拥有：

- 权威 Run 状态。
- 预算账本。
- Artifact 提交语义。
- Tool 权限。
- Evidence Ledger。
- API 事件协议。

若 LangGraph 被移除，固定工作流、历史 Run 读取、Evidence 和 Eval 不应失效。

### 15.3 安装分层

建议：

```text
conflux-weave                 最小 Runtime 和固定工作流
conflux-weave[vector]         向量索引适配器
conflux-weave[agent]          LangGraph 策略
conflux-weave[eval]           Ragas、DeepEval
conflux-weave[devtools]       Phoenix 和开发工具
```

## 16. 测试策略

### 16.1 测试金字塔

1. Core 状态、预算、Policy 和 Evidence 的纯单元测试。
2. Port/Adapter 合同测试。
3. SQLite、Artifact、API 和 Worker 的集成测试。
4. 中断、超时、重复提交和 Provider 失败的故障注入。
5. 冻结回放评测。
6. 真实 Provider、真实网络和真实数据的 Live Acceptance。

### 16.2 必测不变量

- 同一幂等键不会创建两个逻辑任务。
- 终态不会先于最终 Artifact 可见。
- 预算耗尽不会被标记为完整成功。
- 取消后不再启动新模型或工具调用。
- 恢复不会静默重复不可逆副作用。
- 每个最终 Citation 都能解析到 Evidence 和 SourceSnapshot。
- Trace 后端不可用不会阻断 Runtime。
- Eval 框架不可安装时生产路径仍可启动。
- 一个 Adapter 的错误不会泄漏外部异常类型到 API 合同。

## 17. 实施顺序与阶段验收

### W0：项目冻结与验证性骨架

目标：冻结用户问题、最小交付、不可交付条件和新旧边界，证明黄金路径的最小依赖方向可成立。

产物：

- 独立包和数据库路径。
- Core 合同草案及依赖规则测试。
- 旧数据导出清单。
- 首版 10 至 15 个代表用户案例、无答案案例和功能验收清单。

验收：

- 新包不 import 旧 Conflux。
- Core 不 import Web、LLM、LangGraph 或评测框架。
- 数据集和 Manifest 可版本化。
- 每个案例首先定义用户可交付结果，指标只作为其验收证据。

停止条件：为了启动骨架就需要迁移旧大模块，或核心合同连续两次因单个框架调整。

### W1：本人可用的最小 Research Query 垂直切片

目标：在最小范围内完成真实任务：研究问题 -> 一个已登记来源 -> 检索/读取 -> Evidence -> 引用回答。此阶段不追求完整论文雷达或多 Agent。

产物：

- 一个用户入口和一个默认 Fixed Workflow。
- 最小 Task/Run/Step 状态、Artifact 和报告输出。
- 一个本地文档导入路径和一个 SearchPort Adapter。
- SourceSnapshot、Evidence、Claim、Citation 的最小闭环。
- 最小状态测试、引用确定性检查和基础诊断信息。

验收：

- 本人使用真实研究问题完成至少 2 次端到端查询。
- 不查看源码即可提交、等待、查看回答和打开引用。
- 无答案或来源失败时不生成伪引用，并显示可行动的降级结果。
- Phoenix、Ragas、DeepEval 和 LangGraph 未安装时核心链路仍可运行。
- 结果被实际用于笔记、综述或项目判断中的至少一种。

停止条件：为了覆盖更多来源、报告格式或 Agent 角色而延迟首个真实任务。

### W2：Retrieval / Evidence 质量与可读交付

目标：提升 W1 的检索、证据定位和回答可读性，而不是增加产品面。

产物：

- BM25/Dense/RRF 中实际需要的检索策略。
- 一个最小 Web Search + Fetch 适配链路。
- Evidence Locator v1、声明级引用和来源限制展示。
- 现有 30 题集的迁移版、独立 held-out 案例和无答案案例。
- 只有确定性指标不足以回答真实优化问题时才接入 Ragas Adapter。

验收：

- 本人完成至少 3 次新的真实任务，覆盖两类任务。
- 每个关键 Claim 可反向定位到 SourceSnapshot 和精确块/段落。
- 无答案案例不生成伪引用，冲突和证据不足有明确语义。
- 结果正文可读，引用不会遮蔽主要结论。
- Retrieval 或 Evidence 修改有案例级前后对比，并服务于具体产品决策。

停止条件：指标已足以支持当前检索决策，继续扩充评测不能改变实现。

### W3：耐用运行、恢复与质量支撑

目标：围绕已经可用的研究查询，补齐长时运行的稳定性、恢复和诊断，而不是先建设独立 Runtime 平台。

产物：

- SQLite 权威状态、幂等、lease、heartbeat、cancel 和 Step 级 checkpoint。
- Artifact 原子提交、预算账本和结构化失败诊断。
- OTel/必要的 OpenInference Trace；Phoenix 仅作为可选开发观察面。
- 状态、恢复、预算和 Citation 的确定性测试及故障注入。

验收：

- 重复提交不会创建多个逻辑任务。
- Worker 强制终止、服务重启、超时和取消后，Run 能恢复或进入明确终态。
- 恢复不静默重复不可逆副作用。
- 预算耗尽不会伪装为完整成功。
- Trace 后端关闭不影响同一研究任务完成。
- 真实研究任务的完成率和交付质量不因稳定性改造下降。

停止条件：恢复机制开始要求引入第二套调度器、复杂分布式存储或改变产品目标。

### W4：Bounded Agent Strategy

目标：在 Fixed Workflow 已经可用、可恢复之后，判断 Agent 是否为本人研究任务带来实际收益。

产物：

- L1 Fixed Research Strategy 作为稳定基线。
- 可选 L2 Bounded Planner、Tool Gateway、ContextBuilder 和 Plan Validator。
- 只有 Planner 成为真实候选实现后才接入 DeepEval。
- Agent 决策、工具调用、预算和停止条件的最小诊断记录。

验收：

- Planner 不能绕过 Tool、Policy、Budget 或 Evidence 门禁。
- 循环存在硬上限和证据充分停止条件。
- 与 Fixed Workflow 相比，Agent 至少改善一个预先定义的真实任务问题，且没有不可接受的成本、延迟或失败率退化。
- 如果没有明确收益，Fixed Workflow 保持默认，Planner 保留为实验策略或不实现。

停止条件：Agent 只增加演示复杂度、指标数量或招聘叙事，却没有改善本人任务。

### W5：产品可用性与开源首次成功

目标：让本人和不熟悉内部实现的新用户都能持续完成核心研究任务。

- 单 FastAPI 服务和最小 Workbench。
- SSE cursor 恢复、取消、重试、历史 Runs。
- 就绪检查、配置诊断和错误恢复动作。
- 清洁环境安装、启动、配置和首次查询路径。
- README、最小示例语料、离线 smoke、配置模板和故障排查。
- 默认依赖与可选 dev/eval extras 分层。
- 数据隐私、Secret、版本和兼容性说明。

验收：

- W4 的本人真实任务验收通过。
- 关键任务通过键盘、320px 和 200% 文本缩放检查。
- 从 fresh clone 到带引用答案不超过 10 分钟，外部 Provider 等待时间单独记录。
- 新用户无需阅读架构文档即可完成首次成功。
- 默认安装不需要 Phoenix、Ragas、DeepEval。
- README 首屏展示 Research Agent 的输入、输出和示例，不以 Runtime 术语作为第一解释层。
- 即使内部指标改善，只要用户任务完成率、结果可用性或恢复体验未通过，本阶段仍不得完成。

停止条件：为了开源包装重新引入插件市场、多租户、复杂安装编排或第二套服务入口。

### W6：第二垂直切片与演进决策

目标：只在本人真实使用证明黄金路径有价值后，选择一个第二垂直切片；默认优先评估论文雷达，项目审计、Memory、Skills 和 MCP 不自动迁移。

验收：

- 第二切片 RFC、真实任务案例、回滚开关和最低必要质量证据。
- 旧数据的只读静态归档；首版只迁移评测集和测试语料。
- 已迁移数据有 Manifest 和抽样核对。
- 简历、README、架构文档和真实能力状态一致。
- 旧服务不再是 Weave 的运行依赖。
- 未迁移能力明确标记为历史能力、淘汰或待评估。
- 只有真实实现并通过对应验收的能力，才归入 Weave 当前简历表述。

### 17.1 AI Coding 工期与进度预期

以下估算假设：

- 由一个主 AI Coding 会话连续推进，不并行修改同一工作树。
- 用户每天能够完成需求裁决、运行授权和验收反馈。
- 不把旧 Conflux 大模块直接搬入 Weave。
- 默认使用真实 Provider 做少量验证，但不把长时间付费实验纳入普通开发。
- 代码由 AI 生成和修改，人工负责目标、边界、语义决策、真实运行和最终验收。

AI Coding 可以显著减少样板代码、测试草稿、接口实现和局部调试时间，但不能替代：

- 产品任务选择和优先级裁决。
- Provider、数据集和真实运行授权。
- UI 是否真正易用的人工检查。
- 失败语义、恢复语义和证据边界的最终判断。
- 开源清洁环境和简历主张的事实核对。

按一个人每天约 4-6 小时有效协作时间估算：

| 阶段 | AI Coding 主要工作 | 预计有效工时 | 典型日历时间 | 不含内容 |
|---|---|---:|---:|---|
| W0 | 产品契约、案例、目录、依赖边界、最小骨架 | 8-16h | 1-3 天 | 不含方向反复讨论 |
| W1 | 第一条真实 Research Query 垂直切片 | 24-40h | 4-7 天 | 不含复杂多源扩展 |
| W2 | Retrieval、Evidence、Citation 和可读交付 | 32-56h | 1-2 周 | 不含大规模语料清洗 |
| W3 | SQLite 状态、恢复、预算、Trace 和故障注入 | 32-48h | 1-2 周 | 不含分布式扩展 |
| W4 | Bounded Planner、Tool Gateway 和 Agent 决策门禁 | 24-40h | 4-7 天 | 无收益时可整体跳过 |
| W5 | Workbench、安装、README、首次成功和发布检查 | 24-40h | 4-7 天 | 不含完整开源运营体系 |
| W6 | 第二垂直切片和迁移/归档决策 | 40-80h | 1-3 周 | 取决于切片复杂度 |

预期节奏：

~~~
W0-W1：约 1-2 周，得到本人可运行的最小研究查询
W2-W3：约 2-4 周，得到有证据、可恢复、可诊断的核心路径
W4-W5：约 2-3 周，得到受限 Agent 版本和可开源的首成功路径
W6：额外约 1-3 周，按真实使用决定是否扩展第二切片
~~~

因此，在持续投入且没有重大方向变更的情况下：

- **最小可用版本**：约 1-2 周。
- **可持续自用版本**：约 4-6 周。
- **可开源首发版本**：约 6-9 周。
- **包含第二垂直切片的较完整版本**：约 8-12 周。

若只能利用晚间每天 1-2 小时，日历时间通常增加到约 2-3 倍。真实 Provider 不稳定、网页解析质量、UI 返工和用户决策等待是主要不确定因素，不能用 AI 生成代码速度抵消。

### 17.2 阶段检查点与进度表达

每个阶段只允许使用以下状态：

~~~
pending -> in_progress -> implemented -> validated_offline
                                  -> validated_live -> completed
~~~

阶段进度必须报告：

- 已完成的用户功能单元。
- 本阶段实际修改和测试结果。
- 真实运行或清洁环境证据。
- 尚未通过的验收项。
- 下一单一验收点。

“代码生成完成”“接口返回 200”“测试数量增加”不能作为阶段完成依据。AI Coding 的效率只体现在缩短实现和修复时间，不能改变能力状态的证据要求。

## 18. 专家评审标准

### 18.1 评审结论

评审者应给出三种结论之一：

- `Approve`：可进入 W0，只有不改变核心边界的小问题。
- `Conditional Approve`：方向成立，但必须先关闭列出的阻塞项。
- `Reject`：核心假设、边界或实施方法不可行，需要重做设计。

### 18.2 一票否决项

存在任一项时不得通过：

1. Core 依赖 LangGraph、Phoenix、Ragas、DeepEval 或具体 Provider 对象。
2. 没有明确区分 State、Trace、Artifact 和 Evidence。
3. Agent 可以绕过 Policy、Budget、Tool Schema 或人工确认。
4. 没有持久化状态机、幂等和中断恢复语义。
5. 最终 Claim 无法反向定位到来源快照。
6. 评测只给平均总分，不保留案例级结果、版本和原始证据。
7. 首版范围重新包含全部旧功能。
8. 历史测试或旧运行被直接当作 Weave 的完成证据。
9. 可观测或评测服务故障会使生产路径不可用。
10. 设计依赖尚未验证的“模型会正确遵循 Prompt”来保证安全或状态一致性。
11. 测试、Trace、评估或内部审计形成独立产品主线，延迟黄金路径，或者要求生产 Runtime 依赖其运行。

### 18.3 加权评分

每项按 1 至 5 分评分，再乘以权重。总分换算为 100 分。

| 维度 | 权重 | 1 分 | 3 分 | 5 分 |
|---|---:|---|---|---|
| 核心功能与用户价值 | 20 | 主要建设框架或基础设施，用户任务不清楚 | 黄金路径定义清楚但部分交付语义未闭合 | 用户问题、交付、失败和边界清晰，所有支撑工作可回到功能价值 |
| 易用性 | 15 | 依赖内部知识才能完成任务 | 黄金路径可用但配置/失败仍复杂 | 单入口、状态清晰、失败可行动、渐进披露 |
| 结果正确性与证据可信度 | 15 | 最终文本无法验证 | 有来源但声明级关系不完整 | Claim 到 SourceSnapshot 可追溯，证据不足和冲突有明确语义 |
| 稳定与恢复 | 12 | 内存状态、重启丢任务 | 有持久任务但边界场景不完整 | 幂等、租约、取消、故障注入和副作用语义完整 |
| Agent 哲学 | 10 | Agent 控制所有状态和工具 | Agent 有部分约束 | Agent 可替换、受限自主、确定性外壳、增益需功能证据证明 |
| 简洁性 | 8 | 首版复制全部旧功能 | 模块化但抽象偏多 | 单黄金路径、延迟抽象、没有双主路径 |
| 安全与治理 | 6 | 依赖 Prompt 约束 | 有权限和 Secret 管理 | 副作用分级、审批、脱敏、不可信输入边界可验证 |
| 易扩展 | 5 | 新能力修改核心 | 有接口但框架对象泄漏 | 稳定 Port、合同测试、真实使用触发抽象 |
| 可观测与内部审计 | 5 | 失败无法定位 | 有基础 Trace 或日志 | 支撑功能诊断且不反向控制 Runtime，记录可关联真实产物 |
| 易评估与易优化 | 4 | 没有回归证据或指标脱离功能 | 有固定案例和局部指标 | 每项评估关联 Feature/Risk/Decision，可按阶段归因并停止 |

通过条件：

- 无一票否决项。
- 总分不低于 80/100。
- 核心功能与用户价值、易用性、结果正确性与证据可信度、稳定与恢复、Agent 哲学均不低于 4/5。
- 可观测与内部审计、易评估与易优化均不低于 3/5，但不得通过扩大评测范围弥补产品维度失分。
- 任一其他维度不得低于 3/5。
- `Conditional Approve` 的阻塞项必须可以在 W0 前验证，不得留到功能开发后处理。

### 18.4 评审者应要求的证据

设计评审阶段：

- 依赖方向图和禁止依赖。
- 核心数据合同及状态转换。
- 黄金路径时序图。
- 故障与恢复矩阵。
- 代表用户案例、功能验收清单，以及与具体功能风险关联的数据集、指标和 Manifest 示例。
- 至少一个替换 Adapter 的演示设计。
- 首版页面任务流。

实现验收阶段：

- 可复现命令、Git revision 和环境 Manifest。
- 单元、合同、集成和故障注入结果。
- 原始 Trace、Artifact、Evidence 和 Eval 产物。
- 真实 Provider 运行记录及失败样本。
- 关闭 Phoenix、禁用 LangGraph、切换 Retriever 的独立性测试。

### 18.5 关键评审问题

1. 用户真正购买或持续使用的最小价值是否足够清楚？
2. Runtime 是否能在完全不知道 LangGraph 的情况下工作？
3. Agent 做出的每个决定是否有输入、约束、输出和失败语义？
4. 哪些行为必须确定性执行，设计是否错误地交给了模型？
5. 一个失败 Run 能否仅凭数据库和 Artifact 解释发生了什么？
6. 一个最终 Claim 能否在两次操作内打开原始证据位置？
7. 新 Retriever 或 Search Provider 是否只需实现 Port 并通过合同测试？
8. 是否能回答“质量下降发生在检索、上下文、规划、工具还是综合”？
9. 评测系统本身的模型波动、数据泄漏和阈值漂移如何被控制？
10. 首版是否仍然偷偷包含多个独立产品？
11. 多 Agent 相对固定工作流的收益如何被证伪？
12. 哪些旧能力应该明确放弃，而不是迁移？

### 18.6 专家评审回执模板

```markdown
# Conflux-Weave 架构评审回执

评审者：
日期：
评审版本：
结论：Approve / Conditional Approve / Reject

## 一票否决检查

- [ ] 未发现 Core 依赖具体 Agent、Trace 或 Eval 框架
- [ ] State、Trace、Artifact、Evidence 边界清楚
- [ ] Agent 不能绕过 Policy、Budget、Tool 和人工确认
- [ ] 持久状态、幂等、中断和副作用恢复语义完整
- [ ] Claim 可以反向定位到 SourceSnapshot
- [ ] 评测保留案例级结果、版本和原始证据
- [ ] 首版范围没有重新包含全部旧功能
- [ ] 历史证据没有被当作 Weave 完成证据
- [ ] Phoenix/Eval 服务故障不影响生产路径
- [ ] 安全与一致性不依赖 Prompt 自觉
- [ ] 测试、Trace、评估和内部审计没有形成独立产品主线

## 加权评分

| 维度 | 分数 1-5 | 主要依据 | 必须修改项 |
|---|---:|---|---|
| 核心功能与用户价值 | | | |
| 易用性 | | | |
| 结果正确性与证据可信度 | | | |
| 稳定与恢复 | | | |
| Agent 哲学 | | | |
| 简洁性 | | | |
| 安全与治理 | | | |
| 易扩展 | | | |
| 可观测与内部审计 | | | |
| 易评估与易优化 | | | |

加权总分：

## 阻塞项

1.

## 非阻塞建议

1.

## 被质疑的设计假设及验证方法

1.

## 建议进入的下一阶段

W0 / 返回设计修订
```

## 19. 功能优化主张与验证矩阵

矩阵用于验证具体功能设计和优化决策，不构成独立实验路线。只有当主张对应当前核心功能、真实风险和待执行决策时才进入活动矩阵；问题关闭后停止继续扩展实验。

| 设计主张 | 比较对象 | 指标 | 实验单元 | 所需证据 |
|---|---|---|---|---|
| 新用户更容易完成研究查询 | 旧 Conflux 或冻结任务录像 | 完成率、操作数、求助次数、用时 | 5 个代表用户任务 | 观察记录、屏幕录像、问卷 |
| 新 Runtime 更易恢复 | 无持久状态或旧路径 | 恢复成功率、重复副作用、恢复时间 | 进程终止、断网、超时、重启 | DB 快照、事件、Artifact、故障报告 |
| Agent 可替换 | Fixed vs LangGraph Strategy | 合同通过率、Core diff、功能一致性 | 同一任务集 | Git diff、合同测试、Eval Run |
| 功能切片伴生质量证据降低回归风险 | 无门禁基线 | 被 CI 捕获的功能退化、逃逸缺陷 | 检索/Prompt/模型变更 | 案例级结果和变更报告 |
| 声明级 Evidence 提高可审计性 | 仅报告尾部引用 | 引用正确率、定位成功率、审查时间 | 报告 Claim | 人工审查和 Evidence 产物 |
| Bounded Planner 提升效果 | Fixed L1 | 任务成功、覆盖、成本、延迟、失败率 | 冻结研究案例 | 预注册 A/B 报告 |

没有对应实验和证据的主张只能保留为设计假设。

## 20. 与现有简历内容的差异

### 20.1 总体判断

新设计与现有简历的研究主题和主要工程能力并不冲突，但叙事中心会发生明显变化：

```text
旧叙事：多智能体 + 多功能 ResearchOps 工作台
新叙事：面向真实研究任务的证据原生、可恢复 Agent 工作台 + 受限 Agent Runtime
```

因此不是“原简历全部失效”，而是：

- 产品定位高度延续。
- 技术栈大部分保留，但框架地位变化。
- 检索和证据能力可迁移，但必须重新验证。
- 多智能体、MCP、记忆和 Skills 从首版主能力降为历史能力或后续切片。

### 20.2 简历主张的事实归属

设计文档和简历必须区分以下状态：

| 状态 | 含义 | 简历处理 |
|---|---|---|
| legacy_verified | 旧 Conflux 已实现并有可复核证据 | 可作为 Conflux 历史成果 |
| weave_implemented | Weave 代码已落地，但尚未完成规定验收 | 不写成已验证能力 |
| weave_validated | Weave 在冻结版本、规定数据和真实流程下通过 | 可归入 Weave 当前成果 |

具体规则：

- LangGraph Planner/Verifier、MCP、Memory、Skills 和旧版多智能体能力，在未迁移并重新验收前只属于历史成果。
- Dense + BM25 + RRF、Evidence/Claim/Citation 和持久任务能力可以迁移，但必须通过 Weave 自己的合同和真实任务验收。
- 旧版 96.7% 指标不能自动归属于 Weave，除非在 Weave 版本和冻结评测协议下复现。
- 简历项目名称可以暂时保留为 Conflux；完成 W4 后再决定是否改为“Conflux：证据驱动的研究型 Agent 工作台”。
- 不为了保持简历关键词而提前迁移 MCP、Memory、Skills 或多 Agent 评审团。

### 20.3 逐项对照

| 现有简历内容 | 与 Weave 的关系 | 出入程度 | 处理原则 |
|---|---|---|---|
| 本地优先研究型 Agent 工作台 | 仍是产品定位 | 低 | 可继续保留 |
| 论文发现、多源证据检索、项目审计、自然语言交互 | 只有证据检索与自然语言查询进入首版 | 中 | 描述整个 Conflux 演进时可保留；描述 Weave MVP 时缩窄 |
| Python、FastAPI、SQLite | 继续作为核心技术 | 低 | 可保留 |
| LangGraph | 降为可选 Workflow Strategy | 中 | 可写“支持 LangGraph 策略”，不能写成 Weave Runtime 本身 |
| ChromaDB | 降为可重建向量索引 Adapter | 低至中 | 可保留，但应说明其非权威状态 |
| planner、verifier 多角色编排 | 后置且必须由消融证明 | 中至高 | 旧 Conflux 历史能力可保留；Weave 首版不应预先声称 |
| 状态、预算、失败降级、确定性门禁 | 成为新 Runtime 核心 | 低 | 新设计会强化这条经历 |
| FactCheck 和声明到证据绑定 | 成为 Evidence Domain 主线 | 低 | 可保留并进一步具体化 |
| Dense + BM25 + RRF、父子块、来源分层 | 作为可配置 Retrieval Strategy 迁移 | 低 | 算法可复用，参数和结果需重新评测 |
| Hit@20 86.7% -> 96.7% | 只属于旧冻结实现和原评测集 | 高证据敏感 | 必须保留旧证据边界；Weave 重跑前不能继承该结果 |
| FastAPI、增量输出和 SSE | 继续保留，SSE 改为持久事件投影 | 低至中 | 新实现通过验收后可保留 |
| MCP Server 5 个工具 | 首版后置 | 高 | 作为旧 Conflux 历史成果可写；不可作为 Weave MVP 当前能力 |
| SQLite 幂等 Job、心跳、重试、中断恢复 | 继续作为 Runtime 核心 | 低 | 新实现需重新做故障注入后再归属 Weave |
| 结构化偏好记忆 | W5 候选 | 高 | 首版简历不归入 Weave 当前能力 |
| 声明式 Skills 编译 | W5 候选 | 高 | 同上，等待独立需求和评测 |

### 20.4 是否需要修改简历

如果简历中的项目名称仍为“Conflux”，并把它描述为一个持续演进的项目，现有内容可以作为旧系统阶段成果保留，前提是相关实现和评测证据仍可复现。此时可以补一句“正在重构为 Conflux-Weave”，但在 W4 之前不能把 Proposed 设计写成已实现成果。

如果项目名称直接改为“Conflux-Weave”，则当前简历会产生较大事实错位，因为 MCP、记忆、Skills、多智能体和 `96.7%` 指标尚不能自动归属于新实现。

建议按阶段处理：

- W0-W2：简历继续写 Conflux 历史项目，不把 Weave 设计写成实现。
- W3-W4：可以写“Conflux / Conflux-Weave”，区分旧成果与新 Runtime 重构。
- W5 以后：只有迁移并重新验收的能力才改写为 Weave 当前能力。

### 20.5 重构后更强的简历主线

完成 W4 后，项目叙事可以从“实现了很多 Agent 功能”升级为：

1. 设计可替换 Agent 策略的持久化 Research Runtime。
2. 用 OTel/OpenInference 和分层 Evaluation 建立组件到系统的回归闭环。
3. 用 Evidence/Claim/Citation 合同保证声明级审计。
4. 用幂等、lease、checkpoint 和 Artifact commit 处理长时任务恢复。
5. 通过固定工作流与 Bounded Planner 消融，证明何时需要 Agent，而不是默认增加 Agent。

这与 Agent 工程岗位的匹配度会高于单纯强调“多智能体数量”，但前提仍是形成真实实现和可复现证据。

## 21. 待专家复核与实施验证的问题

1. 首版单一 Web Search Adapter 的最小范围是什么，是否同时承担正文获取？
2. W3 引入 LangGraph Strategy 的触发条件是否足够严格，还是只保留自研 Fixed Workflow？
3. SQLite 单 Worker、单写入通道的性能边界是什么，哪些真实指标触发多 Worker 评估？
4. Evidence Locator v1 如何在轻量实现与可复现定位之间取舍？
5. 质量支撑面是否仍有过度建设倾向，哪些合同可以等到首个真实消费者出现再创建？
6. 现有 30 题集是否存在开发集过拟合，如何建立独立 held-out 和无答案案例集？
7. 论文雷达是否确实是黄金路径后的最高价值垂直切片，还是应由真实用户任务重新排序？
8. 旧数据迁移是否应仅包含评测集和测试语料，其余历史 Run 是否全部静态只读归档？

## 22. 设计状态与下一闸门

当前状态：`Proposed`。

本文件完成只代表设计可供评审，不代表 W0 已启动。下一步应由独立专家按第 18 节给出评分、否决项、条件和修改意见。

本文件正文版本为 v0.2；文件路径暂保留 v0.1 以避免已有链接断裂。下一步评审应重点审查：个人真实任务是否足够具体、首次成功是否可达、复杂度预算是否可执行、AI Coding 工期假设是否合理。

评审通过后，开始实施前还必须重新冻结：

- Git revision、分支和工作树。
- 当前运行服务和端口。
- 旧数据库、Artifact 和评测集备份。
- W0 允许修改的路径和首个验收点。
- 首版真实 Provider、预算和 Live Test 授权边界。
- W0 的个人真实任务、开源 smoke 路径和复杂度预算。

## 23. 技术参考

以下资料只支撑工具能力和开放协议选型，不替代 Conflux-Weave 自身的合同与验收：

- [OpenTelemetry Documentation](https://opentelemetry.io/docs/)
- [OpenInference](https://github.com/Arize-ai/openinference)
- [Arize Phoenix Documentation](https://arize.com/docs/phoenix)
- [Ragas Documentation](https://docs.ragas.io/)
- [DeepEval Documentation](https://deepeval.com/docs/)
- [LangGraph Documentation](https://langchain-ai.github.io/langgraph/)
