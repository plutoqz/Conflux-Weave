# W2 retrieval and evidence cases v1.0.0-provisional

Status: `provisional`, awaiting independent human review.

This dataset normalizes all 30 records from the legacy multilingual RAG input
and adds five W2 outcome-boundary cases. It does not promote historical answers,
keywords, scores, or source documents to Weave evidence.

`corpus.jsonl` is a constructed deterministic fixture made only from legacy
source identifiers and `must_contain` annotations. It supports tokenizer and
ranking regression tests; it is not a real corpus and cannot prove answer quality.

Cases 009 and 010 in each language are held out from implementation decisions.
Three legacy English cases reference a missing source and are retained as
explicit partial/missing-source records rather than removed or scored.

The five boundary cases cover no-answer, partial evidence, unverified source,
conflicting evidence and uncited context. No network, Provider or paid evaluator
is authorized by this dataset.
