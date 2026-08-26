---
slug: provenance
source: docs/PROVENANCE.md
source_sha256: sha256:c57ec06cc83c35daec17670906144bc3460e4d341620e698af229eedc2b3eb00
---
# 来源

ThreadCells 是从 AWS Labs CLI Agent Orchestrator 派生的独立下游项目。保留的上游许可证和署名位于源候选版本的 `LICENSE` 和 `NOTICE` 文件中。本仓库在保持既有行为所需之处保留兼容的内部 `cao` 名称和命令；公开产品表面使用 ThreadCells。不暗示 AWS 的赞助或认可。

每个本地候选版本都会在 `candidate-manifest.json`、`SHA256SUMS` 和 `sbom.cdx.json` 中记录其已提交的源修订、文件清单、SHA-256 校验和以及直接依赖证据。SBOM 是已声明/已解析直接依赖的证据，而非许可证许可、漏洞评估或与上游一致性的声明。公开分发仍需要所有者批准公开仓库、安全联系渠道、品牌来源和依赖/许可证审查。
