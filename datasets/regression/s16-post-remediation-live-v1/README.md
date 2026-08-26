# S1.6-C post-remediation live protocol v1

该数据集冻结 S1.6-C 修复后的八任务真实验收协议。题目与 S1.5-C 同构，以便直接比较，
但 case ID、SQLite、summary schema 和幂等命名空间均为新的 `s16c`，不得覆盖或重放
首次矩阵。

正式执行前必须在已提交源码 revision 上通过独立 preflight。`local`、`new` 和 `mixed`
继续使用冻结 manifest，runner 在任何 live Provider 调用前核对其 SHA-256。发现任务允许
`partial`，但交付 Claim 必须逐条人工确认；无答案任务必须成功发布 `no_answer` Delivery。

Manager 的覆盖短语属于协议级验收项。它们必须在计划中逐字出现并被 coverage auditor
标为 `covered`。Manager 质量收益不是通过条件，但 token 和耗时必须如实记录，不能从
机制测试推断收益。
