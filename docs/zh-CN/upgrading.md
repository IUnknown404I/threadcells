---
slug: upgrading
source: docs/UPGRADING.md
source_sha256: sha256:d7a341b2fcc77c17c78707bbc4e7fc1ace10a8005faf393366c284017eef80cd
---
# 升级 ThreadCells

升级是带有已验证回滚的受控候选版本提升，而不是原地覆盖碰巧正在运行的任何文件。

## 升级前

- 阅读发行说明和[限制](LIMITATIONS.md)。
- 确认当前健康状态以及活动/回滚构建身份。
- 让关键的提供商/重型操作到达安全边界。
- 检查开放工作流和已投递结果。
- 创建一致备份并运行数据库完整性检查。
- 保留当前候选版本作为回滚。

## 构建与验证

从预期源提交执行：

```bash
python3 scripts/build_local_candidate.py --output "$PWD/threadcells-candidate"
candidate="$PWD/threadcells-candidate/threadcells-0.3.0a3-local"
python3 scripts/verify_local_candidate.py --candidate "$candidate"
```

如果候选版本身份与已审查提交不同，或 Docs/Web/构建检查失败，请勿提升。

## 暂存与提升

使用规范本地部署工具暂存候选版本，而不改变活动指针。验证已暂存文件，然后原子提升，并只重启使用该发布版本的 ThreadCells 服务。

预期结果：Settings → About、Docs 页脚和发布元数据标识同一个候选修订版本。

## 升级后检查

1. `curl -fsS http://127.0.0.1:9889/health`
2. 打开 Home 并检查容量/磁盘状态。
3. 打开现有 Agents/Flows，并确认持久化关系仍存在。
4. 比较 Settings 和 Spawn 中的提供商就绪状态。
5. 确认操作员授权已配置，且受保护变更在解锁前仍保持锁定。
6. 打开 Statistics，并确认刷新/重启不会重复用量。
7. 打开 Docs 路由并验证打包构建身份。
8. 检查终端流/重新连接。
9. 验证 PWA manifest 和 service worker 不缓存动态请求。
10. 打开 Settings → Telegram 并确认其安全配置状态；若原生凭据已配置，则运行显式连接和测试消息检查。
11. 对于跨越提升操作的开放代理，确认任意控制连接重新初始化只完成一次，且同一持久化工作流在没有所有者唤醒或重复子项/效果的情况下继续。

## 历史修复

升级可能包含有边界的数据修复。仅在源证据具有确定性时运行它，使其保持幂等，并记录前后计数。缺失的提供商遥测必须继续保持缺失；绝不可虚构历史用量。

## 回滚

如果验收出现实质失败：

1. 保留失败候选版本和相关安全日志；
2. 将规范活动指针切换到已验证回滚候选版本；
3. 只重启必需服务；
4. 验证回滚构建和核心表面；
5. 仅在 schema/数据兼容性要求时恢复升级前数据库。

不要使用破坏性的 Git reset，也不要删除较新的运行时证据来模拟回滚。

明确确认的 Full Cleanup 是普通本地发布保留策略的例外：它会移除所有已证实不活跃的发布版本，包括部署期间选定的回滚，并且只留下活跃的不可变发布版本。升级验收期间或任何智能体正在执行时都不要运行它。Full Cleanup 成功后，只能通过暂存另一个已验证的不可变发布版本来恢复回滚能力；绝不要从未验证目录中重建回滚。

请参阅[本地部署](DEPLOYMENT.md)和[备份与恢复](BACKUP_AND_RESTORE.md)。
