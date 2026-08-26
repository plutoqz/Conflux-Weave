# Conflux-Weave 设计文档 v0.3

> 文档状态：`Proposed`
>
> 日期：2026-08-25
>
> 设计对象：Conflux-Weave 个人研究与工程智能工作台
>
> 前一版本：`docs/design/deprecated/v0.2/Conflux-Weave设计文档v0.2.md`
>
> 本版本依据：当前仓库实现与状态、用户确认的八类产品能力，以及《深入理解 AI Agent：设计原理与工程实践》v1.2 中关于 Harness、文件系统、通信、工具和上下文工程的相关理念。

## 0. 文档定位

v0.3 重新定义 Conflux-Weave 的产品范围和技术路线。它不再把多智能体、完整 RAG、Memory、MCP 或 Skill 视为只有在后续实验通过后才可进入的候选能力，而是把它们作为用户已经明确需要建设的产品能力。

本版本同时保留工程上的稳定边界：能力可以主动引入，权威状态、权限、预算、证据、文件写入和恢复语义必须由 Runtime 与 Harness 负责。评测和可观测性用于发现问题、比较方案和支持迭代，不负责决定产品是否允许拥有某项能力。

本文件不把附带书籍中的示例、练习、具体框架、实验数字或作者对其他系统的判断直接当作 Conflux-Weave 的需求。书籍内容只作为架构参考；Conflux-Weave 的目标、数据边界、验收标准和安全策略仍由本项目定义。

## 1. 产品目标

Conflux-Weave 是一个本地优先、可扩展、可观测的个人研究与工程智能工作台。用户通过统一对话入口提出问题，系统根据任务需要调用研究、文档、项目、Coding、Memory、Skill 和 MCP 能力，形成可持续编辑、可追溯、可恢复的交付结果。

核心用户价值：

```text
提出问题
  -> 理解任务与上下文
  -> 选择信息源、工具和 Agent
  -> 获取并验证证据
  -> 生成回答、笔记或项目结论
  -> 保存为可编辑 Artifact
  -> 支持后续追问、修订、补充和复用
```

### 1.1 首要能力

1. 根据自然语言描述获取相关论文，支持主题、时间、来源和相关性边界。
2. 分析用户提供的论文或文档，生成并持续修订 Markdown 或 HTML 笔记。
3. 综合网络信息、用户知识库和模型知识，回答深度研究问题，标注引用、证据范围和可信度。
4. 管理研究与工程项目，提供项目概述、项目问答、Git 状态、建议和相关论文获取。
5. 提供审美与可用性兼顾的工作台，明确区分对话、研究、文档、项目、知识、运行记录和配置。
6. 保存用户兴趣、偏好和经确认的个人记忆，并区分会话、项目和全局作用域。
7. 接入 Skill 与 MCP，也通过 MCP 对外开放稳定能力。
8. 以统一对话作为入口，根据任务动态调用上述能力。

### 1.2 产品优先级

用户功能优先级保持为：

```text
论文获取
> 文档分析与笔记
> 深度研究与 RAG
> 项目管理与项目问答
> 工作台与配置
> 个人记忆
> Skill/MCP 生态
> 统一多智能体调度优化
```

这是产品价值顺序，不是代码必须严格串行的阶段顺序。Runtime、文件系统、事件、上下文和配置是所有能力的共享基础，应在第一批垂直切片中同步建设。

## 2. 设计原则

### 2.1 能力主动引入，合同稳定不变

不再要求每个新能力先通过独立的“准入委员会”。新增能力只需满足当前产品任务、拥有明确输入输出、接入统一 Runtime，并能够被测试、追踪、停止和恢复。

必须保持稳定的合同包括：

- `Task`、`Run`、`Step`、`Attempt` 和事件状态；
- `ToolCall`、权限、预算、超时和取消语义；
- `Evidence`、`Claim`、`Citation`、`Artifact` 和来源版本；
- 文件系统挂载、可见性、生命周期和写入边界；
- Agent 消息信封、状态查询、终止和幂等语义；
- Provider、Embedding、Reranker 和 Skill 的可替换接口。

### 2.2 Harness 是系统核心

借鉴书中 `Model + Harness` 的视角，模型不是完整 Agent。Conflux-Weave 的 Harness 包含：

```text
上下文构造 + 工具接口 + 文件系统 + 通信控制
+ 权限与预算 + 验证与纠正 + 观测与恢复
```

Harness 负责把模型能力转化为可靠的产品行为，但不以静态规则取代 Agent 的任务判断。

### 2.3 Agent 负责决策，Runtime 负责事实

Agent 可以提出计划、选择工具、生成候选结论和修订建议；不能直接决定外部副作用已经成功，不能绕过权限和预算，不能直接篡改权威 Run、Evidence Ledger 或项目 Git 历史。

### 2.4 上下文隔离优先于全量共享

默认采用隔离式协作：每个 Agent 只获得任务所需的 Context Bundle，通过结构化参数、文件路径、Artifact 引用和消息信封通信。只有确实需要零信息损失的少量阶段，才使用共享轨迹或显式 handoff。

### 2.5 数据平面与控制平面分离

```text
数据平面：文件、Artifact、Evidence、索引、项目工作区
控制平面：任务分派、消息、状态、心跳、取消、终止、预算结算
```

Agent 之间不通过互相读取完整思考历史协作，而是通过可追溯的产物和消息协作。

### 2.6 评测驱动优化，不是评测驱动准入

评测关注产品问题：检索是否命中、引用是否闭合、笔记是否可用、项目结论是否正确、成本和延迟是否可接受。评测结果用于选择实现、发现回归和优化 Harness；不把评测框架自身变成独立产品或功能门槛。

## 3. 总体架构

```text
┌─────────────────────────────────────────────────────────────┐
│                         Workbench UI                        │
│ Chat | Research | Documents | Projects | Knowledge | Runs   │
└─────────────────────────────┬───────────────────────────────┘
                              │ HTTP/SSE/WebSocket
┌─────────────────────────────▼───────────────────────────────┐
│                    Conversation Gateway                     │
│ intent routing | task extraction | user confirmation         │
└─────────────────────────────┬───────────────────────────────┘
                              │ TaskSpec
┌─────────────────────────────▼───────────────────────────────┐
│                  Task Orchestrator / Manager                 │
│ plan DAG | dispatch | budget | timeout | recovery | compose   │
└───────┬─────────┬─────────┬─────────┬─────────┬─────────────┘
        │         │         │         │         │
   Research   Document   Project   Coding   Memory
    Agent      Agent      Agent    Agent    Agent
        └─────────┴─────────┴─────────┴─────────┘
                              │
┌─────────────────────────────▼───────────────────────────────┐
│                         Harness Layer                        │
│ Context Builder | Tool Gateway | Virtual FS | Message Bus    │
│ Policy/Budget   | Verifier     | Trace       | Evaluator      │
└──────┬──────────┬──────────────┬──────────────┬─────────────┘
       │          │              │              │
 Evidence      Artifact       Project        Knowledge
 Store         Store          Store          / RAG Index
```

### 3.1 模块边界

| 模块 | 责任 | 不负责 |
|---|---|---|
| Conversation Gateway | 统一入口、意图和任务约束提取 | 执行长任务、直接调用外部工具 |
| Orchestrator | DAG、Agent 调度、预算、状态、恢复 | 生成事实答案、直接写文件内容 |
| Agent | 局部决策和结构化结果 | 权威状态、权限、最终交付提交 |
| Harness | 上下文、工具、文件、通信、验证、纠正 | 代替用户决定研究目标 |
| Evidence Store | Source、Evidence、Claim、Citation | 保存任意 Agent 思考历史 |
| Artifact Store | Markdown、HTML、报告、草稿和版本 | 解释证据是否真实 |
| Project Store | 项目元数据、Git 快照、项目索引 | 自动修改项目文件 |
| Evaluation/Trace | 观察、评估、回归和诊断 | 拥有 Run 状态或阻塞正常请求 |

## 4. 多智能体设计

### 4.1 Agent 角色

第一批角色如下：

- `ConversationRouter`：识别用户意图、作用域和交付格式。
- `Manager`：将复杂任务拆为有依赖关系的子任务，分配预算并汇总结果。
- `ResearchAgent`：规划研究问题、调用网络和知识库检索、整理证据。
- `DocumentAgent`：解析文档、提炼结构、生成或修订笔记 Artifact。
- `ProjectAgent`：读取项目目录、Git 状态、文档和测试，回答项目问题。
- `CodingAgent`：在隔离工作区中分析或修改代码，默认只读，写入需确认。
- `MemoryAgent`：提出记忆候选、去重、冲突检测和作用域归档。
- `Verifier`：检查格式、引用闭合、事实支持、代码测试结果和交付条件。
- `Editor`：将结构化结果组织为用户可读的回答、Markdown 或 HTML。

角色不是永久的进程或独立服务。它们是共享 Runtime 上可替换的 Agent Profile：系统提示、工具集合、输入输出合同、模型配置和预算共同决定角色。

### 4.2 管理者模式

Manager 采用中心化编排，但不能成为第二个数据库。一次任务的权威状态由 Runtime 保存，Manager 只读取状态并提交计划或决策。

Manager 的最小输出：

```json
{
  "objective": "...",
  "steps": [
    {
      "step_id": "research-1",
      "agent": "research",
      "inputs": ["context://..."],
      "outputs": ["artifact://...", "evidence://..."],
      "depends_on": [],
      "budget": {"steps": 4, "tokens": 2400}
    }
  ],
  "stop_conditions": ["..."],
  "user_confirmation_required": false
}
```

调度策略：

1. 简单任务直接调用一个 Agent，不启动完整 Manager。
2. 可并行的检索或解析任务使用独立 Context Bundle 并行执行。
3. 一个 Agent 的产物通过 Artifact/Evidence 引用交给下一个 Agent，不传递全量轨迹。
4. Manager 根据中间结果重新分配剩余预算，但不能无上限扩展步骤。
5. 出现成功、证据充分、预算不足、超时或用户取消时，Runtime 负责统一收束。

### 4.3 Agent 通信协议

所有控制消息使用统一信封：

```json
{
  "message_id": "msg-...",
  "run_id": "run-...",
  "sender": "manager",
  "recipient": "research-1",
  "type": "task_assigned",
  "causation_id": "msg-...",
  "created_at": "...",
  "payload": {}
}
```

至少支持：

- `task_assigned`
- `status_update`
- `artifact_published`
- `evidence_found`
- `needs_input`
- `result_submitted`
- `terminate_requested`
- `terminated`
- `failure_reported`

子 Agent 状态至少包括 `queued`、`running`、`waiting_input`、`completed`、`failed`、`cancelled` 和 `terminated`。状态更新既可由消息推送，也可由 Manager 查询；心跳和无活动超时用于防止任务永久阻塞。

### 4.4 终止和恢复

优先使用 graceful termination：Agent 在安全点停止，写入未完成产物、释放锁并返回确认。只有在超时或进程失效时才强制终止。所有外部调用和文件提交必须有幂等键与 Attempt 记录，避免恢复时重复副作用。

## 5. 虚拟文件系统与 Artifact

文件系统是多 Agent 协作的数据平面，也是用户可编辑知识和交付物的长期接口。Agent 通过统一的 `read_file`、`write_file`、`list_dir`、`search_files` 和 `publish_artifact` 访问虚拟路径，底层可以映射到本地目录、SQLite 元数据、对象存储或外部挂载。

### 5.1 目录布局

```text
weave://
├── system/
│   ├── skills/                 # 内置 Skill，只读
│   ├── templates/              # 笔记和报告模板，只读
│   └── tool-catalog/           # 工具描述和能力索引，只读
├── users/<user_id>/
│   ├── memories/               # 用户确认的长期记忆
│   ├── preferences/            # 模型、输出、语言和界面偏好
│   └── inbox/                  # 用户上传文件
├── projects/<project_id>/
│   ├── project.md              # 项目概述和边界
│   ├── sources/                # 项目资料和外部来源快照
│   ├── notes/                  # 用户可编辑笔记
│   ├── reports/                # 研究和诊断交付物
│   ├── indexes/                # BM25/Dense/metadata 索引引用
│   └── snapshots/              # Git、配置和运行快照
├── runs/<run_id>/
│   ├── context/                # 可重建 Context Bundle
│   ├── messages/               # 控制消息和状态事件
│   └── artifacts/              # 运行中间产物
└── scratch/<agent_instance>/   # Agent 私有临时工作区
```

### 5.2 四类挂载区域

| 区域 | 可见性 | 生命周期 | 写入 | 并发策略 |
|---|---|---|---|---|
| `scratch` | 单 Agent | 实例级 | 可写 | 私有，无需协调 |
| `projects` / `runs` | 协作 Agent 与用户 | 持久 | 受授权写入 | 版本、锁、幂等提交 |
| `mounts/*` | 由外部授权决定 | 外部源决定 | 默认只读 | 适配器、缓存、权限过滤 |
| `system` | 所有 Agent | 跨会话 | 只读 | 无需协调 |

Agent 的临时搜索结果、调试日志和中间草稿留在 `scratch`；只有经过验证的结果才发布到共享空间。Agent 之间优先传递路径和 ArtifactRef，而不是把大段内容复制进上下文。

### 5.3 文件写入和版本

- 用户可见文档采用 Markdown 作为权威编辑格式，HTML 是可重建的派生交付物。
- 每次发布生成新的 Artifact 版本，不覆盖历史版本。
- 写入使用临时文件、校验、原子替换和内容哈希。
- 多 Agent 修改共享文档时使用工作副本、乐观锁或段落级 patch，禁止无条件覆盖。
- 外部挂载默认只读；写回外部系统必须单独授权并记录目标、差异和结果。

## 6. 上下文工程

### 6.1 Context Bundle

每次 Agent 调用都由代码生成 Context Bundle，而不是让 Agent 自己拼接任意历史：

```text
Identity      当前 Agent 角色和权限
Objective     当前子任务与完成条件
State Bar     Run、步骤、预算、时间和工具状态
Inputs        用户要求、相关文件和 ArtifactRef
Evidence      与当前问题相关的证据摘要和来源
Tools         当前任务允许使用的工具，按需披露
Constraints   输出格式、隐私、禁止操作和升级条件
```

固定前缀（角色、稳定规则、工具 schema）尽量不变；动态状态追加在末尾，以利于缓存和可检查性。

### 6.2 状态栏

状态栏由代码维护，不由 LLM 自己统计。至少包括：

```text
run_id, step_id, current_agent, elapsed_time
budget_used / budget_remaining
tool_calls, retrieval_rounds, pending_tasks
workspace, project, git_branch, dirty_files
last_failure, recovery_action, user_confirmation
```

状态栏是事实投影，不得直接使用未经验证的网页内容、模型猜测或自由文本覆盖。它可以帮助 qwen3.7flash 快速感知环境，但不能替代原始 Artifact 和 Evidence。

### 6.3 渐进式披露和分层上下文

资源和 Skill 使用三层表示：

```text
L0：名称、类型、摘要、权限和相关性
L1：概览、适用场景、关键结论和入口链接
L2：全文、原始文件、完整轨迹或详细工具结果
```

默认只加载 L0/L1；只有任务需要时才读取 L2。搜索结果、大型文档和代码目录优先保存到文件系统，并向模型提供摘要预览和路径。

### 6.4 上下文隔离和交接

子 Agent 返回结构化摘要，而非完整思考历史：

```json
{
  "summary": "...",
  "facts": ["..."],
  "evidence_refs": ["evidence://..."],
  "artifact_refs": ["weave://..."],
  "uncertainties": ["..."],
  "recommended_next_steps": ["..."]
}
```

需要完整细节时，Manager 通过引用重新读取原始 Artifact，而不是默认把全部轨迹传给下游 Agent。

### 6.5 分层压缩

上下文管理依次采用：

1. 工具结果存盘，模型只看摘要和路径；
2. 删除低价值噪声，不为噪声额外摘要；
3. 在接近窗口或成本阈值时批量压缩；
4. 生成带任务目标和 EvidenceRef 的结构化摘要；
5. 连续压缩失败时熔断，保留原始 Artifact 并请求用户或降级。

压缩是有损的，Evidence、原始来源、代码 diff 和 Artifact 版本必须保留为可回溯索引，不能只保留一段模型摘要。

## 7. 工具、Skill 与 MCP

### 7.1 工具分类

```text
感知工具：read_file、search_files、read_document、git_status、web_search
执行工具：write_file、run_command、run_tests、build_index
协作工具：spawn_agent、send_message、get_status、cancel_agent
事件工具：wait_for_event、schedule_task、notify_user
用户工具：ask_confirmation、publish_note、show_diff
```

### 7.2 工具设计

- 稳定、复杂参数、敏感权限和高风险副作用使用专用结构化工具。
- 高频、低风险、变化快的能力可通过 Skill + 通用执行器表达。
- 工具描述必须说明何时使用、何时不能使用、参数示例、输出结构和失败语义。
- 工具数量过多时使用能力索引和渐进式披露，不把全部工具 schema 塞进每次调用。
- 文件和命令工具必须有工作区、路径、超时、大小、网络和权限限制。

### 7.3 Skill

Skill 是可版本化的任务能力包，至少包含：

```text
skill_id, version, purpose, inputs, outputs
required_tools, context_requirements, procedure
failure_modes, permissions, examples
```

Skill 不直接拥有特权。它通过 Tool Gateway 执行，并将生成的文件、证据和日志写入当前 Run。

### 7.4 MCP

MCP 作为外部工具和资源的接入协议，不成为核心领域合同。MCP Gateway 负责：

- Server 注册、版本和能力发现；
- 凭证、权限、来源和租户边界；
- 超时、预算、速率和输出大小；
- 工具调用 trace、结果 Artifact 化和错误标准化；
- 外部内容与指令分离，防止间接提示注入。

对外 MCP 暴露的是稳定的研究、文档、项目和 Artifact 能力，不暴露 SQLite、内部消息表或任意文件系统写权限。

## 8. RAG 与深度研究

### 8.1 数据管线

```text
source adapter
-> source snapshot
-> parse / normalize
-> document version
-> structure-aware chunks
-> sparse + dense indexes
-> hybrid retrieval
-> rerank / deduplicate
-> context assembly
-> claim generation
-> citation verification
```

第一版完整 RAG 使用 BM25 与 Dense Hybrid；RRF、Reranker、结构化索引和轻量链接导航作为可配置组件接入。GraphRAG 不作为默认前提，只有当项目关系、实体链路或跨文档推理确实需要时再增加图结构。

### 8.2 Agentic RAG

简单问题使用一次检索，复杂问题由 ResearchAgent 迭代检索：

```text
问题理解
-> 子问题分解
-> 并行检索
-> 证据覆盖检查
-> 缺口查询
-> 交叉验证
-> 结论与引用
```

每轮都记录查询、命中、未命中、工具成本和停止原因。Agent 不能因为“感觉信息足够”就绕过 Citation 检查。

### 8.3 可信度

可信度由可解释因素组成：

- 来源类型、权威性和时效性；
- 检索相关性和重复来源一致性；
- Claim 与 Evidence 的蕴含关系；
- 是否存在冲突或缺失；
- 是否只依赖模型自身知识。

最终报告同时展示引用、证据范围、来源限制和高/中/低可信度，不输出没有依据的精确概率。

## 9. 八类产品能力

### 9.1 论文获取

输入自然语言研究描述，系统提取主题、排除条件、时间、来源和交付格式；ResearchAgent 调用网络搜索、论文 API 和本地索引，输出候选论文、相关性说明、证据和限制。

### 9.2 文档分析与笔记

用户上传 PDF、Markdown、HTML、DOCX 或代码文档后，DocumentAgent 解析结构并生成 Markdown 权威笔记，Editor 生成 HTML 视图。后续请求以 Artifact 版本和 patch 方式进行补充、重组、压缩、扩展或风格调整。

### 9.3 深度研究

ResearchAgent 根据问题选择网络、项目知识库、个人知识库或模型知识，必要时多轮检索和交叉验证，输出研究结论、证据链、引用、未知项和后续建议。

### 9.4 项目管理与项目问答

ProjectAgent 读取项目概述、目录、文档、测试、Git 状态和历史快照，回答：

- 项目当前实现了什么；
- 某个功能、模块或依赖如何工作；
- Git 工作树、分支和最近变更是什么；
- 当前问题的证据、风险和建议是什么；
- 该项目相关领域有哪些论文或外部资料。

CodingAgent 默认只读。任何写文件、运行危险命令、提交 Git 或修改外部系统的操作都需要权限和用户确认。

### 9.5 工作台与配置

前端分区为 Chat、Research、Documents、Projects、Knowledge、Runs 和 Settings。各分区通过 Run、Artifact、Evidence 和 Project 作用域互相链接，而不是孤立页面。

配置中心支持按角色设置 Chat、Manager、Research、Writer、Verifier、Embedding 和 Reranker 模型。默认使用当前 qwen3.7flash，用户可以为特定角色覆盖 Provider、模型、API 参数、预算和缓存策略。

### 9.6 个人记忆

记忆分为：

```text
session memory：当前任务临时事实
project memory：项目约定、技术背景和决策
user memory：用户偏好、兴趣、语言和输出习惯
```

Agent 只能提交 MemoryCandidate，由系统去重、检查冲突和作用域，并默认要求用户确认后写入长期记忆。记忆保留来源、创建时间、更新时间和置信度，不把整段对话直接永久保存。

### 9.7 Skill、MCP 与对外开放

内部能力通过 Skill 描述、MCP Gateway 接入，外部 Agent 通过版本化 MCP API 调用研究、文档、项目和 Artifact 能力。所有外部调用必须经过相同的权限、预算、trace 和 Evidence/Artifact 规则。

### 9.8 统一对话

统一对话是默认入口，但不是唯一 UI。Router 负责提取用户意图、作用域、交付格式和是否允许外部调用；需要长任务时创建 Run，短任务直接返回。用户可以在对话中继续引用已有项目、文档、笔记和研究 Run。

## 10. 成本、延迟和质量

### 10.1 成本策略

- 简单任务不启动多 Agent。
- 子任务独立上下文，避免重复传递完整历史。
- 资源先以 L0/L1 摘要加载，全文按需读取。
- 大型工具结果存盘，使用路径和摘要引用。
- 相同查询、解析和索引结果可缓存，但缓存必须绑定来源版本和配置哈希。
- Manager 按子任务价值分配步骤和 token 预算，预算减少时从探索转向验证与交付。

### 10.2 质量指标

每个 Run 记录：

```text
任务成功率、交付完整度、Citation closure
检索 Recall/MRR、Evidence coverage、Claim support
笔记可读性和修订保真度
项目问答正确性、Git 状态准确性
输入/输出 token、工具调用、耗时、缓存命中
失败类别、恢复次数、用户确认次数
```

评测可使用自定义确定性指标、Ragas、DeepEval、LLM-as-a-Judge 或人工复核，但所有结果都必须绑定任务、版本、模型、Prompt、工具配置和原始 Artifact。

### 10.3 可观测性

Trace 是质量支撑面，不拥有业务状态。至少记录：

- Conversation、Run、Step、Attempt 和 Agent；
- Context Bundle 摘要、工具调用和通信消息；
- 文件读写、Artifact、Evidence 和引用关系；
- 模型、Provider、Prompt、token、耗时和错误；
- 取消、超时、重试、恢复和用户确认。

生产请求在 Phoenix、Ragas 或 DeepEval 不可用时仍可运行；本地 Trace 和 Artifact 保留是默认能力。

## 11. 安全、权限和失败语义

- 检索内容是数据，不是指令；外部文本不得直接改变系统规则或触发高风险工具。
- 用户、项目、Run、scratch、外部挂载和 system 资源有独立权限边界。
- 写文件、执行命令、发送外部消息、提交 Git 和写回外部系统均需明确授权。
- 未知外部调用结果不得自动重放；恢复必须先查询提交状态或请求用户决定。
- Agent、消息、Artifact 和工具调用使用幂等键，防止并发重复提交。
- 任务可在排队、执行、等待输入、部分完成、完成、失败、取消和终止之间迁移，并持久化事件。
- 任何自动重试都必须有次数、成本和故障类别边界，连续失败后熔断并保留原始证据。

## 12. 技术选型

首版保持模块化单体和单一 HTTP 边界，使用 SQLite 保存权威元数据与事件，文件系统保存 Artifact 和工作区。随着并发和外部接入增加，再将消息总线、对象存储或 Worker 拆出；拆分由实际吞吐和可靠性问题触发，而不是提前建设分布式平台。

建议组件：

- Python 3.12、FastAPI、SQLite、SSE；
- OTel/OpenInference 作为 Trace 适配层；
- BM25 + 可替换 Dense Embedding + 可选 RRF/Reranker；
- LangGraph 或等价编排器只位于 Orchestrator Adapter，不进入领域合同；
- Markdown 为可编辑权威格式，HTML/PDF 为派生交付；
- MCP 作为外部工具接入协议，Skill 作为版本化能力包。

## 13. 交付路线

### P0：Harness 与共享基础

交付 Runtime、Virtual FS、Artifact/Evidence、Context Bundle、Tool Gateway、消息信封、状态栏、预算、Trace 和配置合同。

### P1：论文获取与完整 RAG

交付自然语言论文获取、文档解析、BM25/Dense Hybrid、Citation 验证、深度研究基本闭环和 qwen3.7flash 成本记录。

### P2：文档分析与可持续笔记

交付 PDF/Markdown/HTML/DOCX 输入、Markdown/HTML 笔记、版本、patch、局部修订和 Artifact 预览。

### P3：项目工作台

交付项目容器、目录和 Git 只读分析、项目 RAG、项目问答、相关论文获取，以及隔离的 CodingAgent 原型。

### P4：统一 Workbench 与 Memory

交付统一对话、分区导航、Run/Artifact/Evidence 联动、模型与 Embedding 配置、用户和项目记忆。

### P5：Skill/MCP 与多 Agent 协调增强

交付 Skill 注册、MCP Gateway、对外 MCP、异步消息、并行 Agent、优雅终止、恢复和跨 Run 复用。

### P6：持续优化

基于真实任务和 Trace 优化检索、上下文压缩、Manager 调度、模型路由、缓存、成本、延迟和前端交互。任何优化都保留 baseline、原始响应、失败样本和配置版本。

## 14. 每个能力切片的完成定义

功能不需要等待全系统“准入”，但每个交付切片必须具备：

1. 一个用户可执行的真实任务入口。
2. 清晰的输入、输出和失败语义。
3. Artifact/Evidence/消息或项目状态的可追溯记录。
4. 至少一条自动化合同测试和一条真实或回放路径。
5. token、耗时、工具调用和错误可观察。
6. 失败后可恢复、可降级或给出明确人工动作。
7. 不破坏已有路径，或提供 feature flag、兼容层和回滚方案。

这些是工程完成定义，不是阻止新能力进入的研究门槛。

## 15. 当前状态与下一步

v0.2 已归档至 `docs/design/deprecated/v0.2/`，作为历史设计和已有实现依据，不再作为完整产品路线。对应的 W0-W5 方案、冻结和验收记录保存在 `docs/plans/deprecated/v0.2/`。当前仓库可以复用其 SQLite、Artifact、Evidence、Recovery、FastAPI 和 Workbench 资产，但不能把 v0.2 中“首版不做多智能体、Memory、Skill、MCP 或完整 RAG”的旧非目标继续当作 v0.3 约束。

下一步应先冻结 P0 的合同与文件系统布局，再以论文获取 + 完整 RAG 作为第一条新产品垂直切片。P0 的实现不要求先迁移所有旧模块，也不要求一次性实现所有 Agent；但从第一天开始，新的 Agent、工具、上下文和产物都必须走统一 Harness。

## 16. 参考与适用边界

- 《深入理解 AI Agent：设计原理与工程实践》v1.2，李博杰，2026-07-20。本文主要参考第 1、2、3、4、5 和 10 章中关于 Harness、上下文工程、文件系统范式、Agentic RAG、工具、事件驱动 Agent、Coding Agent 和多 Agent 协作的讨论。
- OpenTelemetry、OpenInference、FastAPI、MCP 等官方文档只用于协议和工具能力参考，不替代本项目的运行时合同和验收。
