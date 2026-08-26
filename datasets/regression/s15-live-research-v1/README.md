# S1.5-C live research matrix v1

该数据集冻结 S1.5-C 首次八任务真实验收。它不是离线 fixture，也不授权自动调用
Provider；执行器必须收到显式 `--execute-live`。

任务组成：1 个 arXiv 新论文发现、2 个单篇研究、两组单 Agent/Manager 同题对比，
以及 1 个无答案案例。`local` 使用原 179 篇 PDF，`new` 只使用两篇 2026-08 新下载
PDF，`mixed` 使用两者合并后的索引。

首次执行结果必须原样保留。Citation closure 只证明引用闭合，不证明语义支持；所有关键
Claim 仍需逐条人工核对。无答案案例只有在系统明确拒答并保留检索边界时才通过，异常失败
或从无关 Evidence 生成结论均不通过。
