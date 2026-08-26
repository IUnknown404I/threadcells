---
slug: deployment
source: docs/DEPLOYMENT.md
source_sha256: sha256:c62f86a3da68342ecd221d2eca8e7b02aaa252083db448bfbc64b28fe7451bc4
---
# 本地部署

ThreadCells 部署会将已验证的不可变候选版本提升到本地运行时。它不意味着发布、Git 推送/打标签、包发布或公开网络暴露。

## 候选版本纪律

从一个精确、干净的源提交构建，然后在暂存前验证候选版本：

```bash
python3 scripts/build_local_candidate.py --output "$PWD/threadcells-candidate"
python3 scripts/verify_local_candidate.py \
  --candidate "$PWD/threadcells-candidate/threadcells-0.3.0a3-local"
```

候选版本应包含来自同一修订版本的 Python 代码、打包 Web 资源、allowlist 中的 Docs bundle、构建身份、校验和和发布元数据。

主机暂存使用专门的发布维护组，因此运行中的控制平面可以读取但不能替换不可变候选版本，而 Housekeeping 服务可以移除已明确取消保护的发布版本。首次主机暂存前，请先创建一次该系统组：

```bash
sudo groupadd --system threadcells-release-admin
```

已安装的控制平面和 Housekeeping unit 会禁用 Python 字节码写入。这样可防止常规导入改变不可变发布版本内的所有权或内容，即使窄范围发布维护组处于活动状态也是如此。

如果该组不可用，暂存命令会按 fail-closed 原则拒绝。它会将发布候选版本、原子活动指针、暂存锁和发布保护元数据置于由 root 拥有的 `/var/lib/threadcells` 锚点下，位于运行时拥有的状态之外。生产服务通过 `/var/lib/threadcells/active` 执行，而非运行时可写的命令链接。候选路径必须是 `/var/lib/threadcells/releases` 的直接子目录；符号链接和替代锁/元数据目标会被拒绝。

## 安全提升顺序

1. 记录当前活动运行时及其健康状态。
2. 将其保留为已验证的回滚目标。
3. 创建并完整性检查数据库备份。
4. 使用仓库规范部署机制暂存精确的已验证候选版本。
5. 再次验证已暂存候选版本。
6. 原子提升已暂存身份。
7. 只重启必需的 ThreadCells 服务。
8. 在回环地址或现有受保护访问路径上执行生产验收。

不要原地覆盖活动目录。发布指针/符号链接或等效规范机制应明确标识活动、回滚和已暂存候选版本。

在暂存已记录精确候选版本后，通过规范的加锁操作提升它：

```bash
sudo python3 deployment/promote-ops-p1.py \
  --system-root / \
  --candidate-root /var/lib/threadcells/releases/RELEASE_ID \
  --expected-commit EXACT_PUBLIC_SHA
```

当已存在经验证的规范回滚发布版本时，使用 `--rollback-root`。该操作具备幂等性：重试会完成中断的指针/元数据转换，而不会虚构新的发布身份。

## 验收

至少检查：

- 健康状态和 Settings → About 构建身份；
- Home、Agents、Flows、Statistics、Settings、Docs 和 Spawn Agent；
- 提供商清单和一次安全预检；
- 操作员已配置/锁定/解锁/受保护变更行为；
- 全局 Telegram 安全配置状态；仅当已配置原生凭据时，才进行显式连接/测试行为；
- 终端连接与重新连接；
- 工作流/结果延续；
- 数据库完整性且没有用量重放重复；
- PWA manifest/icons/service-worker 注册，且没有动态缓存。

## 回滚

回滚会切换到保留的先前候选版本，并只重启必需服务。仅当新版本执行了不兼容或造成损害的迁移时才恢复数据库；不必要的数据库恢复可能丢弃提升后完成的有效工作。

回滚后，验证构建身份、健康状态、schema 兼容性、活动工作流和终端。保留失败候选版本和日志，直到查明根本原因。

## 边界

本地部署权限不授予发布包、推送远程、创建 tag/release 或公开原始服务端口的许可。这些仍是独立的所有者决策。
