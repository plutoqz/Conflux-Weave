# ADR 0003：使用 LanceDB 作为 Dense 索引后端，并后置图片多模态 RAG

- 状态：`accepted_for_v0.3_s1.2`
- 日期：2026-08-26
- 范围：v0.3 S1 Dense 检索与 P2 多模态 RAG 规划

## 决策

1. S1.2 的正式 Dense 向量后端采用本地持久化 LanceDB。
2. `DenseIndexPort` 隔离 LanceDB 具体 API；SQLite 只保存 IndexManifest、表版本、配置哈希和发布状态。
3. 原始 PDF、解析文本、Embedding 批次、模型响应和 LanceDB 重建输入继续由 Artifact Store 保存。
4. 当前 JSON 向量矩阵保留为迁移输入、离线回放和回滚路径；完成冻结案例的一致性校验后才切换默认读取路径。
5. 图片优先的多模态 RAG 后置到 P2，先完成图片 Artifact、页码/区域定位、parent-child lineage，再接入可替换 ImageEmbeddingPort 和 LanceDB modality 字段。

## 原因

- 4,043 个真实 chunks 已超出仅用 JSON 矩阵作为默认服务存储的合适边界。
- LanceDB 提供本地持久化、向量检索和元数据过滤，适合当前单机研究工作台，不要求提前引入独立服务。
- 保留 Artifact 和 JSON 回放可以审计原始响应、重建索引并在数据库迁移失败时回滚。
- 图片检索需要稳定的图片 Artifact、页码/区域和父子关系；先建立 lineage 再增加模型，避免把图像描述误当作证据。

## 不在本决策内

- 不引入 GraphRAG、分布式向量集群或外部托管数据库。
- 不在 S1 内完成 OCR、公式/表格理解或图像模型准入。
- 不以 LanceDB 行号作为 Evidence ID；Evidence 必须使用稳定 Chunk/SourceSnapshot 定位。

## 验收与回滚

- LanceDB 临时表完整写入并通过数量、维度、Chunk ID 和冻结案例检索一致性检查后，原子发布 IndexManifest。
- 批次失败、表损坏或版本不兼容时，不更新默认 manifest，继续使用旧表版本或 JSON 回放 Adapter。
- P2 多模态只有在图片 Evidence 可定位、文本回归不退化、成本/延迟/失败类别可查时才开放为可选路径。
