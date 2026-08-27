---
slug: release-process
source: docs/RELEASE_PROCESS.md
source_sha256: sha256:82961559c0645676fcec5126113c53f17492ccd1b7c0c7100552689b770353ae
---
# 发布流程

使用 `scripts/build_local_candidate.py --output <new-directory>` 从干净、已提交的工作树构建隔离的本地候选版本。它会打包生成的文档/UI 和本地 wheel。验证 `SHA256SUMS`，检查 `candidate-manifest.json`、`sbom.cdx.json` 和 `EVIDENCE.md`，然后使用新前缀执行文档中说明的干净安装。发布标签、远程分支、包、镜像或公开版本绝不是普通的实现操作。

## 发布清单

1. 完成实现和一次独立的集成审查。
2. 运行聚焦测试，并完成一个有意义的生产构建/浏览器验证轮廓。
3. 运行 `git diff --check` 和公开表面审计。
4. 提交精确的已接受树。
5. 从该提交构建候选版本，绝不从未提交的工作树构建。
6. 验证清单、校验和、SBOM、构建身份、文档路由和干净安装。
7. 本地提升前保留先前的运行时和数据库备份。
8. 将任何公开推送、标签、包、镜像或发布视为单独且须经所有者批准的操作。

发布证据证明测试和打包的内容；它本身并不批准公开发布，也不证明每项依赖的许可证/安全属性。

## OCI 发布分发

已批准发布的 alpha 版本还会在 `ghcr.io/iunknown404i/threadcells-release-bundle` 提供公开 OCI 分发构件。它包含经验证的发布归档、Python wheel、校验和清单、候选清单、SBOM，以及针对一个精确发布标签和源修订的发布包元数据。

该包是分发包，而非 Docker 镜像或受支持的容器部署环境。验证其校验和后，使用正常的候选安装和部署流程；不要尝试将 OCI 构件作为 ThreadCells 服务运行。

`.github/workflows/publish-release-bundle.yml` 在已批准的 GitHub Release 或显式回填调度时发布。它仅接受具有现有非草稿预发布版本的带注释 `v0.X.Y-alpha.N` 标签，重新构建并验证精确的带标签源，拒绝替换版本标签不匹配的内容，并且只更新 `latest-alpha`。技术预览期间，ThreadCells 不会发布无条件的 `latest` 标签。

## 版本线约定

ThreadCells 遵循标准 SemVer 预发布排序。在 alpha 预览期间，`0.1.X` 标识有意义的产品、可靠性或文档迭代；当同一迭代中确有必要进行额外发布时，`alpha.N` 标识这些额外发布。

- `v0.1.0-alpha.1` 是首个公开 alpha 版本。
- `v0.1.0-alpha.2` 是不可变的已发布技术预览版本。
- `v0.2.0-alpha.1` 是整合多语言和可靠性工作的发布线。
- `v0.3.0-alpha.1` 增加生命周期一致性、持久创建顺序、Full Cleanup 和系统化路由策略。
- `v0.3.0-alpha.2` 修复 Workflow Composer 投递，并阻止已退出终端重新获得可执行工作流权限。
- `v0.3.0-alpha.3` 为已认证界面增加英语和俄语本地化，并让应用与公共网站共用按语言归属的单一规范 Docs 语料库。
- 同一发布线内的后续发布只递增 alpha 序列；新的产品轮廓会审慎地递增语义版本。

绝不可移动现有标签。仅仓库治理变更不会触发版本递增或发布。只有当下一个有意义的实现轮廓已准备好发布时，才同时更新所有规范版本承载表面。
