# 文档写作与生命周期规则

> status: active
> owner: market-data-platform
> audience: human and agent
> last_verified: 2026-09-06
> source_of_truth: yes
> superseded_by: n/a

## 文档状态

主动维护的文档在文件开头记录以下元数据：

```text
status: active | migration-only | historical | archived | superseded
owner: 当前维护项目或团队
audience: human | agent | human and agent
last_verified: YYYY-MM-DD
source_of_truth: yes | no
superseded_by: 替代文档路径或 n/a
```

状态含义：

| 状态 | 用途 |
| --- | --- |
| `active` | 当前能力、当前命令或当前治理规则 |
| `migration-only` | 迁移、恢复或历史交接期间仍需使用的入口 |
| `historical` | 带日期的历史研究、审计或决策记录 |
| `archived` | 已完成归档、仅用于历史复现的材料 |
| `superseded` | 已有明确替代页面，正文只保留迁移指针 |

只有存在明确替代页面时才使用 `superseded`。历史文档保留当时的事实、日期和结论，不改写成当前状态。

## 中文文风

- 先写结论，再写必要背景和操作步骤。
- 使用自然、直接的中文，句子尽量短。
- 中文正文使用中文逗号、句号、括号、冒号和问号。
- 命令、路径、配置键、包名、API 名称和字段名保留行内代码格式。
- 技术细节放在 `docs/`，根目录 README 负责定位、边界、快速开始和导航。
- 一个段落只表达一个主要观点，避免重复解释同一条规则。
- 直接写当前结论，减少无必要的否定前置和转折。

## 历史与迁移说明

迁移说明要同时写清楚当前状态、目标入口、保留原因和删除条件。旧命令、旧路径和兼容入口需要有事实来源，不能只根据文件名推断状态。

完成文档移动后，旧路径保留短指针，包含 `status: superseded` 和 `superseded_by`。如果旧文档仍承载历史事实，则使用 `historical` 或 `archived`，不要删除正文。
