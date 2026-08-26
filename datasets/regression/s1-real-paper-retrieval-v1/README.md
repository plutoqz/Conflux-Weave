# S1 real paper retrieval v1

该数据集用于验证真实 PDF 导入、BM25、LanceDB Dense、RRF 与 qwen3-rerank 四段链路。

- 6 个正例由 AcademyHunter `papers.id/title`、PDF 文件名和 Conflux-Weave 导入 manifest
  确定性对齐；相关项是目标论文的 SourceSnapshot，而不是模型生成标签。
- 2 个无答案案例是明确超出当前 Agent 论文语料主题的负例。
- 指标在论文级计算，同一论文的多个 page chunk 先去重。
- 当前正例查询接近论文标题或核心主题，属于低难度实现保真度验证。全 1.0 结果不能证明
  一般语义检索、跨论文 RAG、Claim support 或独立人工 relevance judgment 已通过。
- 无答案阈值 `0.5` 当前只由 2 个负例覆盖；扩大负例集前不能作稳定校准声明。
