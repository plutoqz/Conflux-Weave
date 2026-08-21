# W0 旧 Conflux 数据资产只读盘点

> 状态：`frozen_read_only_inventory`
>
> 盘点时间：2026-08-21
>
> 机器可校验清单：`W0-legacy-export-inventory.json`

## 边界与结论

本次仅读取旧 `D:\code\Conflux` 及其两个本地 Runtime DB，没有复制、迁移、
规范化或删除任何资产。源 revision 为
`441734a22ab827479a8eee46139d89c29482ac67`，相对 `origin/main` ahead 7；
唯一未跟踪项 `docs/plans/Conflux-Weave设计文档v0.1.md` 属于用户资产，未纳入
数据盘点，也未修改或清理。

盘点结论不是迁移授权：所有资产均为 `direct_import_allowed=false`。旧评测分数、
Run、Trace 和报告只能说明旧 Conflux 的历史状态，不能成为 Conflux-Weave 的能力
证据；Chroma 等派生索引必须从经准入的 Source Snapshot 重建。

## 建议动作

| 动作 | 资产 | 进入条件 |
|---|---|---|
| `export_candidate` | 三语 RAG 30 cases、`v2_gold` 单场景、tracked test fixtures | 有 W1 消费者；逐文件复核并生成新 Manifest |
| `defer_export_w2` | P2 GIS radar 75 labels / 56 papers | W2 检索链路需要且接受 abstract-only 代审边界 |
| `defer_export_w6` | KG/LLM radar 136 labels | W6 出现实际 radar 消费者 |
| `test_fixture_only` | P4 panel 18 verification cases / 100 claims，另 10 extracted records | 仅用于确定性合同测试，不报告为真实能力 |
| `selective_export_after_review` | 72 tracked documents，4,062,629 bytes | 逐文件完成许可证、隐私、权威性和相关性复核 |
| `local_archive_only` | 112 local files，含 99 PDFs，365,331,469 bytes | 用户单独授权选择；不进入 Git |
| `archive_only` | golden dataset、M5、reports、两个 Runtime DB | 只读历史审计；需要迁移时另建显式 exporter |
| `reject_and_rebuild` | 484,098,048-byte ChromaDB | 从 Source Snapshot、embedding 配置和导入 Manifest 重建 |
| `reject` | 62 个 pytest SQLite/Chroma 临时文件及 sidecar | 不进入任何导出或导入清单 |

## Runtime 边界

主库 `C:\Users\lenovo\AppData\Local\Conflux\conflux.db` 完整性检查为 `ok`，
包含 8 Runs（3 completed、2 completed_with_warnings、2 failed、1 cancelled）、
56 Checkpoints、224 SourceSnapshots、96 Claims 和 251 Evidence items，但 Artifact
记录为 0。建议只做静态归档；未来若确需迁移，必须由显式、版本化 exporter 选择
字段并生成规范化 import manifest，不能把 SQLite 文件直接放入 Weave Runtime。

次库 `C:\Users\lenovo\.conflux\conflux.db` 完整性检查为 `ok`，无 Run，仅有
1 Paper，默认只归档或在后续迁移复核中拒绝。

## Hash 方法

文本文件按 UTF-8 读取并将 CRLF/CR 统一为 LF 后计算 SHA-256；二进制文件按原始
字节计算。目录 hash 由按序排列的仓库相对路径、方法名和文件 hash 再汇总得到。
这些 hash 只绑定本次盘点；实际 export 必须对被选文件重新计算逐文件 hash，并
记录 source revision、选择规则、规范化步骤和 reviewer decision。

## 下一验收点

W0.4 已达到 `validated_offline`。下一单一验收点是 W0.5：运行全量离线测试、包
构建、Manifest/清单合同校验和 Git 敏感路径检查，将实际 revision、Python 版本、
case 数、命令结果与未验证边界写入现有 W0 计划及项目状态。仍不进入 W1 Workflow、
Provider 或网络检索实现。
