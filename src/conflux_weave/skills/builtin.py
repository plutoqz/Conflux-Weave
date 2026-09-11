"""Built-in authoritative skills for Conflux-Weave (P5.1)."""

from __future__ import annotations

from conflux_weave.skills.spec import SkillBudget, SkillCategory, SkillSpec, SkillStatus

LITERATURE_COMPARATIVE_SURVEY = SkillSpec(
    skill_id="literature_comparative_survey",
    version="1.0.0",
    name="文献对比综述工作流",
    description="跨多篇学术论文进行核心指标、实验设计与理论局限的结构化横向对比分析，生成高密度 Markdown 矩阵与综合评述。",
    category=SkillCategory.RESEARCH,
    author="Conflux-Weave Core",
    input_schema={
        "type": "object",
        "properties": {
            "paper_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": "待比对的学术论文 ID 列表 (2-5 篇)",
                "minItems": 1,
            },
            "focus_dimensions": {
                "type": "array",
                "items": {"type": "string"},
                "description": "关注的比对维度列表，如 ['理论假设', '架构创新', '关键指标', '计算开销', '局限性']",
            },
        },
        "required": ["paper_ids"],
    },
    required_tools=(
        "rag_hybrid_search",
        "get_paper_evidence",
    ),
    prompt_template="""你是一位严谨的学术综述专家。
请对以下指定的学术论文进行深度的结构化横向比对：
论文清单：{paper_ids}
比对维度：{focus_dimensions}

请遵守以下学术综述准则：
1. 【对比矩阵】：首先输出一个规范的 Markdown 对比表格，横向为比对维度，纵向为各篇论文；
2. 【核心差异与演进】：剖析论文在核心算法、理论假设与工程实现上的代际差异与演进脉络；
3. 【客观实验评估】：对比各篇论文在基准数据集上的实测指标与显存/算力开销；
4. 【未决挑战与未来方向】：总结当前学术界在该问题上的共性缺陷与未来潜在攻关路径；
5. 回答保持学术中立，杜绝空洞夸大词汇，所有数据必须标明出处。
""",
    rules=(
        "必须包含 Markdown 对比矩阵表格",
        "对比维度不少于 3 个",
        "结论必须指出各方案的适用边界与局限",
    ),
    default_budget=SkillBudget(
        max_tokens=8000,
        max_steps=10,
        estimated_time_seconds=45.0,
    ),
    is_builtin=True,
    status=SkillStatus.ACTIVE,
)

CODE_ARCHITECTURE_AUDIT = SkillSpec(
    skill_id="code_architecture_audit",
    version="1.0.0",
    name="代码架构治理与坏味道体检",
    description="对目标代码仓库执行架构坏味道体检、上帝文件排查、隐式环境泄露扫描与学术理论映射断裂点审计。",
    category=SkillCategory.GOVERNANCE,
    author="Conflux-Weave Core",
    input_schema={
        "type": "object",
        "properties": {
            "project_id": {
                "type": "string",
                "description": "待审计的目标项目 ID",
            },
            "target_modules": {
                "type": "array",
                "items": {"type": "string"},
                "description": "可选关注的子模块或目录列表",
            },
            "severity_threshold": {
                "type": "string",
                "enum": ["low", "medium", "high"],
                "default": "medium",
                "description": "问题严重度告警阈值",
            },
        },
        "required": ["project_id"],
    },
    required_tools=(
        "project_walkthrough",
        "project_audit",
        "git_semantic_diff",
    ),
    prompt_template="""你是一位资深的系统架构治理专家与代码体检审计师。
请对目标项目进行全景架构与契约健康度审计：
目标项目：{project_id}
关注模块：{target_modules}
告警阈值：{severity_threshold}

请遵守以下审计准则：
1. 【健康度总览】：给出项目综合健康评分、缺陷统计分布与模块依赖拓扑摘要；
2. 【核心代码坏味道】：精确定位上帝文件（God Files）、圈复杂度过高方法及未经保护的环境变量泄漏；
3. 【学术理论映射断裂点】：检查论文提及的算法（如 RRF 排序、OCC 乐观锁、ReAct 调度）在物理源码中是否具有闭环行级对应；
4. 【解耦与重构方案】：针对高风险模块提出明确的重构建议与伪代码契约切分方案。
""",
    rules=(
        "必须定位具体的代码坏味道类型与影响范围",
        "必须列出理论映射的健全性评估",
        "重构建议必须具备明确的操作步骤",
    ),
    default_budget=SkillBudget(
        max_tokens=6000,
        max_steps=8,
        estimated_time_seconds=30.0,
    ),
    is_builtin=True,
    status=SkillStatus.ACTIVE,
)

LATEX_PAPER_POLISHER = SkillSpec(
    skill_id="latex_paper_polisher",
    version="1.0.0",
    name="LaTeX 论文文风润色与公式校验",
    description="面向顶级学术会议（NeurIPS/ICLR/ACL/CVPR）进行学术文风深度润色、逻辑连贯性提升与数学公式符号一致性校验。",
    category=SkillCategory.WRITING,
    author="Conflux-Weave Core",
    input_schema={
        "type": "object",
        "properties": {
            "latex_content": {
                "type": "string",
                "description": "待润色的 LaTeX 源代码片段或段落",
            },
            "target_conference": {
                "type": "string",
                "default": "NeurIPS",
                "description": "目标学术会议或期刊文风偏好",
            },
            "focus_sections": {
                "type": "array",
                "items": {"type": "string"},
                "description": "重点关注的章节，如 ['Abstract', 'Introduction', 'Method']",
            },
        },
        "required": ["latex_content"],
    },
    required_tools=(),
    prompt_template="""你是一位在顶级学术会议（{target_conference}）拥有丰富审稿经验的资深学术编辑。
请对以下 LaTeX 内容进行深度润色与严格的公式符号审查：
目标会议：{target_conference}
关注章节：{focus_sections}

原始内容：
```latex
{latex_content}
```

请按以下三部分输出：
1. 【润色后内容】：保留所有原有的 LaTeX 标签、引用（\\cite）、公式与交叉引用（\\ref），产出文风地道、论证严密的 LaTeX 正文；
2. 【关键修改对照与理由】：列举 3~5 处重大表达优化的前后对照，解释为何修改能提升被接收概率；
3. 【数学符号与公式自检】：严格检查公式中的符号（如粗体向量 \\mathbf、下标、期望记号 \\mathbb{{E}}）是否存在未定义先使用或上下文记号冲突，若有则给出修正建议。
""",
    rules=(
        "严禁破坏或丢失原有的 \\cite 和 \\ref 标签",
        "输出必须包含公式符号一致性检查表",
        "润色后文风需符合目标学术会议的标准风格",
    ),
    default_budget=SkillBudget(
        max_tokens=5000,
        max_steps=4,
        estimated_time_seconds=20.0,
    ),
    is_builtin=True,
    status=SkillStatus.ACTIVE,
)

BUILTIN_SKILLS: tuple[SkillSpec, ...] = (
    LITERATURE_COMPARATIVE_SURVEY,
    CODE_ARCHITECTURE_AUDIT,
    LATEX_PAPER_POLISHER,
)
