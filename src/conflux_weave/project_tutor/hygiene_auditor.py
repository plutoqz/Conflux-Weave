"""Targeted AI Hygiene & Bureaucratic Ritualism Static Auditor for Modern Codebases."""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path
import re
from typing import Any

from conflux_weave.project_tutor.spec import (
    HygieneAuditFinding,
    HygieneCategory,
    ProjectHygieneReport,
)


IGNORE_DIRS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "dist",
    "build",
    "__pycache__",
    ".idea",
    ".vscode",
    ".pytest_cache",
    "var",
    "tmp",
}


class CodeVisitor(ast.NodeVisitor):
    """AST visitor detecting typical AI-generated anti-patterns and bureaucratic waste."""

    def __init__(self, rel_path: str, source_lines: list[str]) -> None:
        self.rel_path = rel_path
        self.source_lines = source_lines
        self.findings: list[HygieneAuditFinding] = []
        self.with_stack: list[ast.With | ast.AsyncWith] = []

    def _get_snippet(self, node: ast.AST, max_lines: int = 5) -> str:
        start = getattr(node, "lineno", 1) - 1
        end = min(len(self.source_lines), start + max_lines)
        return "\n".join(self.source_lines[start:end])

    def visit_Try(self, node: ast.Try) -> None:
        for handler in node.handlers:
            # Check for silent exception swallowing: except Exception: pass or except: pass
            is_bare_or_broad = False
            if handler.type is None:
                is_bare_or_broad = True
            elif isinstance(handler.type, ast.Name) and handler.type.id in ("Exception", "BaseException"):
                is_bare_or_broad = True

            if is_bare_or_broad:
                # Check if body is just pass
                if len(handler.body) == 1 and isinstance(handler.body[0], ast.Pass):
                    finding_id = f"swallow_{len(self.findings) + 1}_{getattr(handler, 'lineno', 1)}"
                    snippet = self._get_snippet(handler, 3)
                    self.findings.append(
                        HygieneAuditFinding(
                            finding_id=finding_id,
                            category=HygieneCategory.SILENT_SWALLOWING,
                            severity="critical",
                            file_path=self.rel_path,
                            line_number=getattr(handler, "lineno", 1),
                            code_snippet=snippet,
                            issue_description="滥用裸 except / except Exception: pass 静默吞噬异常。",
                            harm_analysis="静默吞异常会彻底掩盖由于语法错误、数据格式漂移或系统内部崩溃导致的致命故障，使得排查问题时宛如盲人摸象。",
                            refactor_diff_suggestion=(
                                f"--- a/{self.rel_path}\n"
                                f"+++ b/{self.rel_path}\n"
                                f"@@ -{getattr(handler, 'lineno', 1)} +{getattr(handler, 'lineno', 1)} @@\n"
                                f"-except Exception:\n"
                                f"-    pass\n"
                                f"+except SpecificError as exc:\n"
                                f"+    logger.warning('捕获明确受控异常: %s', exc)\n"
                            ),
                        )
                    )
        self.generic_visit(node)

    def visit_With(self, node: ast.With) -> None:
        self.with_stack.append(node)
        self.generic_visit(node)
        self.with_stack.pop()

    def visit_AsyncWith(self, node: ast.AsyncWith) -> None:
        self.with_stack.append(node)
        self.generic_visit(node)
        self.with_stack.pop()

    def visit_Call(self, node: ast.Call) -> None:
        # Check for unmanaged bare open() resource leaks
        if isinstance(node.func, ast.Name) and node.func.id == "open":
            is_in_with = False
            for w in self.with_stack:
                for item in w.items:
                    if item.context_expr == node or getattr(item, "context_expr", None) is node:
                        is_in_with = True
                        break
                if is_in_with:
                    break
            if not is_in_with:
                finding_id = f"leak_{len(self.findings) + 1}_{node.lineno}"
                self.findings.append(
                    HygieneAuditFinding(
                        finding_id=finding_id,
                        category=HygieneCategory.ARCHITECTURAL_VULNERABILITY,
                        severity="warning",
                        file_path=self.rel_path,
                        line_number=node.lineno,
                        code_snippet=self._get_snippet(node, 2),
                        issue_description="检测到未使用 with 上下文管理器的裸 open() 调用，存在资源泄漏隐患。",
                        harm_analysis="未通过上下文管理器管理的文件句柄在遇到未受控异常或GC延迟时，极易造成句柄耗尽或文件锁死冲突。",
                        refactor_diff_suggestion="重构为 `with open(...) as handle:` 确保异常安全与文件句柄及时回收。",
                    )
                )
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._check_function_stubs(node)
        self._check_unused_parameters(node)
        self._check_mutable_defaults(node)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._check_function_stubs(node)  # type: ignore[arg-type]
        self._check_unused_parameters(node)  # type: ignore[arg-type]
        self._check_mutable_defaults(node)  # type: ignore[arg-type]
        self._check_async_concurrency_risks(node)
        self.generic_visit(node)

    def _check_mutable_defaults(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        all_defaults = [d for d in node.args.defaults if d is not None] + [
            d for d in node.args.kw_defaults if d is not None
        ]
        for d in all_defaults:
            if isinstance(d, (ast.List, ast.Dict, ast.Set)):
                finding_id = f"mutable_default_{len(self.findings) + 1}_{node.lineno}"
                self.findings.append(
                    HygieneAuditFinding(
                        finding_id=finding_id,
                        category=HygieneCategory.ARCHITECTURAL_VULNERABILITY,
                        severity="warning",
                        file_path=self.rel_path,
                        line_number=node.lineno,
                        code_snippet=self._get_snippet(node, 2),
                        issue_description=f"函数 `{node.name}` 使用了可变默认参数 (list/dict/set)。",
                        harm_analysis="Python 可变默认参数在模块加载时单例初始化，后续多次调用将共享并累计修改，在并发或长程请求中引发难以排查的数据污染。",
                        refactor_diff_suggestion="将默认形参重构为 None，并在函数体内通过动态初始判断赋值。",
                    )
                )

    def _check_async_concurrency_risks(self, node: ast.AsyncFunctionDef) -> None:
        for stmt in node.body:
            if isinstance(stmt, ast.Global):
                finding_id = f"concurrency_race_{len(self.findings) + 1}_{node.lineno}"
                self.findings.append(
                    HygieneAuditFinding(
                        finding_id=finding_id,
                        category=HygieneCategory.ARCHITECTURAL_VULNERABILITY,
                        severity="warning",
                        file_path=self.rel_path,
                        line_number=node.lineno,
                        code_snippet=self._get_snippet(node, 3),
                        issue_description=f"异步函数 `{node.name}` 直接声明并读写全局变量 global，存在并发竞态风险。",
                        harm_analysis="在 asyncio 事件循环高并发切换调度场景下，多协程无锁交替写入全局状态容易引发数据覆盖与脏读。",
                        refactor_diff_suggestion="使用带有互斥锁 (asyncio.Lock) 的受控状态机或依赖注入隔离状态上下文。",
                    )
                )

    def _check_function_stubs(self, node: ast.FunctionDef) -> None:
        # Check for stub implementations: pass, ..., or raise NotImplementedError
        # Exclude abstract methods decorated with @abstractmethod
        is_abstract = any(
            (isinstance(d, ast.Name) and d.id == "abstractmethod")
            or (isinstance(d, ast.Attribute) and d.attr == "abstractmethod")
            for d in node.decorator_list
        )
        if is_abstract:
            return

        body = node.body
        # Filter out docstrings
        effective_body = body
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str):
            effective_body = body[1:]

        is_stub = False
        stub_desc = ""

        if len(effective_body) == 0:
            is_stub = True
            stub_desc = f"函数 `{node.name}` 仅有文档字符串或空函数体，伪装假完成度。"
        elif len(effective_body) == 1:
            first_stmt = effective_body[0]
            if isinstance(first_stmt, ast.Pass):
                is_stub = True
                stub_desc = f"函数 `{node.name}` 仅有 pass 或省略号桩代码，伪装假完成度。"
            elif isinstance(first_stmt, ast.Expr) and isinstance(first_stmt.value, ast.Constant) and first_stmt.value.value is ...:
                is_stub = True
                stub_desc = f"函数 `{node.name}` 仅有 pass 或省略号桩代码，伪装假完成度。"
            elif isinstance(first_stmt, ast.Raise):
                exc = first_stmt.exc
                if (isinstance(exc, ast.Name) and exc.id == "NotImplementedError") or (
                    isinstance(exc, ast.Call) and isinstance(exc.func, ast.Name) and exc.func.id == "NotImplementedError"
                ):
                    is_stub = True
                    stub_desc = f"函数 `{node.name}` 未实现真实业务逻辑，直接抛出 NotImplementedError 占位桩。"

        if is_stub:
            finding_id = f"stub_{len(self.findings) + 1}_{node.lineno}"
            self.findings.append(
                HygieneAuditFinding(
                    finding_id=finding_id,
                    category=HygieneCategory.STUB_PLACEHOLDER,
                    severity="warning",
                    file_path=self.rel_path,
                    line_number=node.lineno,
                    code_snippet=self._get_snippet(node, 4),
                    issue_description=stub_desc,
                    harm_analysis="在未声明 @abstractmethod 的生产代码中遗留空壳桩函数，上层调用时将无任何反馈，引发难以追踪的逻辑断层。",
                    refactor_diff_suggestion=(
                        f"--- a/{self.rel_path}\n"
                        f"+++ b/{self.rel_path}\n"
                        f"@@ -{node.lineno} +{node.lineno} @@\n"
                        f" def {node.name}(...):\n"
                        f"-    pass\n"
                        f"+    # 补充真实业务计算或明确显式抛出异常\n"
                        f"+    raise NotImplementedError('请根据需求完成 {node.name} 真实实现')\n"
                    ),
                )
            )

    def _check_unused_parameters(self, node: ast.FunctionDef) -> None:
        # Check for phantom arguments never referenced in body
        arg_names = {arg.arg for arg in node.args.args if arg.arg not in ("self", "cls")}
        if not arg_names or len(arg_names) > 8:
            return

        # Collect all referenced names in body
        referenced_names: set[str] = set()
        for child in ast.walk(node):
            if isinstance(child, ast.Name):
                referenced_names.add(child.id)

        unused = arg_names - referenced_names
        for u in unused:
            if u.startswith("_"):
                continue
            finding_id = f"phantom_arg_{len(self.findings) + 1}_{node.lineno}"
            self.findings.append(
                HygieneAuditFinding(
                    finding_id=finding_id,
                    category=HygieneCategory.PHANTOM_CONFIG,
                    severity="info",
                    file_path=self.rel_path,
                    line_number=node.lineno,
                    code_snippet=self._get_snippet(node, 2),
                    issue_description=f"函数 `{node.name}` 声明了未被消费的幽灵形参 `{u}`。",
                    harm_analysis="AI 编程中常因上下文幻觉生成看似有用但内部完全不使用的冗余形参，导致调用方产生误解与参数维护负担。",
                    refactor_diff_suggestion=f"移除形参 `{u}` 或将其前缀重命名为 `_{u}` 显式声明保留备用。",
                )
            )


def audit_file_content(rel_path: str, content: str) -> list[HygieneAuditFinding]:
    """Audit single file content for 7 categories of AI hygiene anti-patterns."""
    findings: list[HygieneAuditFinding] = []
    lines = content.splitlines()

    # 1. AST-based auditing for Python files
    if rel_path.endswith(".py"):
        try:
            tree = ast.parse(content, filename=rel_path)
            visitor = CodeVisitor(rel_path, lines)
            visitor.visit(tree)
            findings.extend(visitor.findings)
        except Exception:
            pass

    # 2. Regex & Pattern audits
    is_test_file = "test" in rel_path.lower()

    for idx, line in enumerate(lines):
        line_num = idx + 1
        line_clean = line.strip()

        # Category: Mock Illusion in test files
        if is_test_file:
            if "MagicMock()" in line_clean and "assert_called" in content and "assert " not in content.replace("assert_called", ""):
                findings.append(
                    HygieneAuditFinding(
                        finding_id=f"mock_illusion_{line_num}",
                        category=HygieneCategory.MOCK_ILLUSION,
                        severity="warning",
                        file_path=rel_path,
                        line_number=line_num,
                        code_snippet=line_clean,
                        issue_description="测试代码存在滥用全量 Mock 制造虚假单测繁荣的倾向。",
                        harm_analysis="将所有底层依赖全部 Mock 隔离，仅断言 mock.assert_called_once()，容易产生通过率 100% 但真实运行一跑就崩的假象。",
                        refactor_diff_suggestion="替换为真实轻量级内存环境或端到端真实数据流断言，减少虚假打桩。",
                    )
                )

        # Category: Bureaucratic Ritualism (Overkill hashing & ritual frozen baselines)
        if any(kw in line_clean.lower() for kw in ("ritual_freeze", "scope_freeze_hash", "bureaucratic_hash", "freeze_manifest_v0")):
            findings.append(
                HygieneAuditFinding(
                    finding_id=f"bureaucratic_{line_num}",
                    category=HygieneCategory.BUREAUCRATIC_RITUALISM,
                    severity="info",
                    file_path=rel_path,
                    line_number=line_num,
                    code_snippet=line_clean,
                    issue_description="代码存在官僚式防御与形式主义哈希冻结清单标识。",
                    harm_analysis="过度生成的哈希校验与死板基线清单增加了机械式维护仪式感，但未带来实质性系统安全增益。",
                    refactor_diff_suggestion="精简冗余的形式主义冻结逻辑，依靠高覆盖率的端到端集成测试保障契约一致性。",
                )
            )

        # Category: Hardcoded secrets / tokens (Architectural Vulnerabilities)
        if re.search(r'''(?i)(?:sk-[a-zA-Z0-9]{20,}|ghp_[a-zA-Z0-9]{20,}|password\s*=\s*['"][a-zA-Z0-9@#\$%]{8,}['"])''', line_clean):
            findings.append(
                HygieneAuditFinding(
                    finding_id=f"secret_{line_num}",
                    category=HygieneCategory.ARCHITECTURAL_VULNERABILITY,
                    severity="critical",
                    file_path=rel_path,
                    line_number=line_num,
                    code_snippet="[REDACTED SENSITIVE LINE]",
                    issue_description="检测到源码中可能存在明文硬编码密钥或测试凭据。",
                    harm_analysis="硬编码凭据一旦提交至公共或团队仓库，将导致严峻的信息泄露与凭据冒用风险。",
                    refactor_diff_suggestion="将其转移至 `.env` 并在代码中通过 `os.getenv` 安全注入。",
                )
            )

        # Category: Zombie Defensiveness (Double None checks in single line)
        if re.search(r"if\s+([a-zA-Z0-9_]+)\s+is\s+not\s+None\s+and\s+\1\s+is\s+not\s+None", line_clean):
            findings.append(
                HygieneAuditFinding(
                    finding_id=f"zombie_{line_num}",
                    category=HygieneCategory.ZOMBIE_DEFENSIVENESS,
                    severity="warning",
                    file_path=rel_path,
                    line_number=line_num,
                    code_snippet=line_clean,
                    issue_description="检测到连续重复的冗余判空条件（死深防御代码）。",
                    harm_analysis="多层重复的防御性判空增加了代码认知负担，属于典型的 AI 复制粘贴冗余。",
                    refactor_diff_suggestion="简化为单一判空语句 `if obj is not None:`。",
                )
            )

    return findings


def audit_project_hygiene(
    root_path: Path | str,
    max_files: int = 100,
) -> ProjectHygieneReport:
    """Run full 7-dimension AI hygiene audit across project files."""
    path = Path(root_path).resolve()
    all_findings: list[HygieneAuditFinding] = []

    files_checked = 0
    for file_path in path.rglob("*"):
        if files_checked >= max_files:
            break
        if not file_path.is_file():
            continue
        # Check ignored dirs
        if any(ignored in file_path.parts for ignored in IGNORE_DIRS):
            continue
        if file_path.suffix.lower() not in (".py", ".ts", ".js", ".json", ".env"):
            continue

        try:
            rel_p = str(file_path.relative_to(path)).replace("\\", "/")
            content = file_path.read_text(encoding="utf-8", errors="ignore")
            f_res = audit_file_content(rel_p, content)
            all_findings.extend(f_res)
            files_checked += 1
        except Exception:
            pass

    # Calculate categories
    cat_counts: dict[str, int] = {cat.value: 0 for cat in HygieneCategory}
    for f in all_findings:
        cat_counts[f.category.value] = cat_counts.get(f.category.value, 0) + 1

    # Calculate health score: start at 100, deduct based on severity
    score = 100
    for f in all_findings:
        if f.severity == "critical":
            score -= 15
        elif f.severity == "warning":
            score -= 5
        else:
            score -= 2
    final_score = max(20, min(100, score))

    if final_score >= 85:
        verdict = f"【代码卫生评级: A (优良)】工程架构自律性高，仅发现少许低危提示项 ({len(all_findings)} 处)。"
    elif final_score >= 70:
        verdict = f"【代码卫生评级: B (中等)】存在若干空壳实现或异常吞噬点，建议参考重构 Diff 实施定向修复。"
    else:
        verdict = f"【代码卫生评级: C (待整改)】检测到多处关键安全或静默异常隐患，需严格执行体检报告所列重构方案。"

    return ProjectHygieneReport(
        project_path=str(path),
        hygiene_score=final_score,
        total_findings_count=len(all_findings),
        findings_by_category=cat_counts,
        findings=tuple(all_findings),
        executive_verdict=verdict,
    )
