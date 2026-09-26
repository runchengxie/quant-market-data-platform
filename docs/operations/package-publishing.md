# Python 包发布

范围：说明如何把 `market-data-platform` 构建为可发布的 Python 包，供
`strategy-pipeline` 等下游仓库按版本安装。数据资产发布仍使用数据平台
资产发布流程，不通过 Python 包直接分发平台数据资产。

## 构建

本地构建命令：

```bash
uv build --clear
```

当前仓库没有活动 `Package` workflow，构建必须显式在本地执行。停用模板不能作为已经运行
或已经验证的 CI 证据。如果以后恢复自动构建，应重新验证 wheel / sdist 和发布凭据契约。

当前包内容策略：

- sdist 包含 `docs/` 和 `tests/`，便于下游拿到完整文档和源码级验证样例。
- wheel 只包含 `market_data_platform` 运行包，不包含 `docs/` 和 `tests/`。
- 暂不发布 `py.typed`。扩大 `ty` 覆盖并完成下游类型契约验证后再对外承诺类型面。

## 发布

当前没有自动发布 job。恢复 `Package` workflow 时，只允许在以下情况发布：

1. 推送 `v*` tag。
1. 手动运行 `Package` workflow，并把 `publish` 输入设为 `true`。

发布目标必须是兼容 PyPI 的上传入口。先在 `market-data-platform`
仓库的 GitHub secrets 中配置：

| Secret | 用途 |
| --- | --- |
| `MDP_PUBLISH_URL` | package registry 的 upload endpoint |
| `MDP_PUBLISH_TOKEN` | token 认证。与 username/password 二选一 |
| `MDP_PUBLISH_USERNAME` | username/password 认证的用户名 |
| `MDP_PUBLISH_PASSWORD` | username/password 认证的密码或 token |

发布前先确认 `pyproject.toml` 中的版本号已经递增。不要用同一个版本号重复发布
不同内容。

## 下游依赖尚未发布 registry 的 owner commit

registry 尚未配置时，下游可以直接安装本仓已经合并的 commit。依赖应锁定完整 commit
SHA，不使用浮动的 `main`、开发分支或未合并的 PR 分支。这样可以在 registry 准备期间先完成
跨仓绑定，同时避免生产环境依赖某个开发者本地 checkout。

下游 `pyproject.toml` 示例：

```toml
[project]
dependencies = ["market-data-platform[research-features,duckdb]>=0.2.0"]

[tool.uv.sources]
market-data-platform = { git = "https://github.com/runchengxie/quant-market-data-platform.git", rev = "<完整的已合并 commit SHA>" }
```

根据下游的 import 范围选择 extras。契约模块的导入链未使用 pandas、Parquet 或 DuckDB 时，
可以省略相应 extras。修改后在下游运行 `uv lock` 并提交 `uv.lock`，再执行 `uv sync --locked` 和
公开导入、行为契约检查。升级时先合并并验证本仓改动，再将下游来源更新到新的完整 commit
SHA。

## 下游切换

`strategy-pipeline` 目前仍通过相邻目录的 editable source 使用本仓库源码，用于在
package registry 尚未配置前保持本地联调可运行。后续可先使用上面的不可变 Git commit，完成
首次 registry 发布后再切换到包源：

1. 在下游验证环境中配置可访问的包源和读取 token。
1. 在下游仓库运行 `uv lock --no-sources`，确认能从 registry 解析
   `market-data-platform>=0.1.0`。
1. 从下游 `pyproject.toml` 移除 `market-data-platform` 的 local path source。
1. 删除普通验证流程中对 `market-data-platform` 的源码签出步骤，只保留专门的
   platform contract 检查来验证跨仓边界。

如果 `uv lock --no-sources` 报 `market-data-platform was not found in the package
registry`，说明包尚未发布到下游可读的 registry，不能继续删除下游验证环境的源码
checkout。
