# 多 Agent 协作架构方案

## 一、Profile 定义

| Profile | 角色 | 使用方式 | 模型策略 | 记忆需求 |
|---|---|---|---|---|
| `main` | 主力工作台 | 日常手动对话 | 主力模型（DeepSeek-v4） | 积累全部长期记忆 |
| `manager` | 任务管理 | 仅 kanban 调度 | 轻量/便宜模型 | 无记忆 |
| `developer` | 开发执行 | 仅 kanban 调度 | 强模型 | 无记忆（靠 skill 获取经验） |
| `auditor` | 审计审查 | 仅 kanban 调度 | 中等+稳重 | 无记忆 |
| `chronicler` | 总结沉淀 | 仅 kanban 调度 | 中等 | 只写不入 |

## 二、5 个 Profile 的推荐配置

### main — 日常主力

```
模型:   DeepSeek V4 Flash (当前)
工具:   全开 (terminal, file, web, browser, memory, skills, ...)
记忆:   开启，积累全部经验
Skills: 按需加载
用途:   日常开发、对话、问题排查、手动启动 kanban 任务
```

### manager — 任务管理

```
模型:   低成本模型 (DeepSeek V3 / Gemini 2.0 Flash / GPT-4o-mini)
工具:   受限 (skills, web, file)
        不开放 terminal — 不写代码
记忆:   关闭
Skills: 仅加载 kanban 相关
用途:   创建任务、拆解需求、分配任务、审核完成结果
```

### developer — 开发执行

```
模型:   主力模型 (DeepSeek V4 / Claude Sonnet / GPT-4o)
工具:   terminal, file, web, browser
记忆:   关闭（所有经验由 chronicler 汇总后写成 skill 再加载）
Skills: 运行前由 manager 指定加载哪些经验 skill
用途:   写代码、修 bug、实现功能
```

### auditor — 审计审查

```
模型:   稳重模型 (Claude Sonnet / GPT-4o)
工具:   terminal, file, vision (可读截图)
记忆:   关闭
Skills: 加载审计规则 checklist
用途:   Code Review、安全扫描、质量门禁
```

### chronicler — 总结沉淀

```
模型:   中等模型 (GPT-4o-mini / DeepSeek V3)
工具:   file (读写知识库), skills (创建/更新 skill)
记忆:   关闭（它只写给别人用）
Skills: 加载知识库操作模板
用途:   读取 developer 产出 + auditor 报告 → 写 skill / 写知识库文件
```

## 三、Kanban 任务流转 + Profile 调度

### 标准化流水线

```
                    manager
                      │
                      │ 创建任务 (--assignee developer)
                      ▼
                  developer
                      │
                      │ 完成任务 (自动 done)
                      ▼
                    manager
                      │
                      │ 创建审计子任务 (--parent <dev-task> --assignee auditor)
                      ▼
                    auditor
                      │
                 ┌────┴────┐
                 通过       不通过
                 │           │
                 │           └──→ manager 退回 developer 修改
                 ▼
               manager
                 │
                 │ 创建总结子任务 (--parent <audit-task> --assignee chronicler)
                 ▼
              chronicler
                 │
                 │ 写入 skill / 知识库 (自动 done)
                 ▼
               manager 确认、归档
```

### 每个任务的生命周期

```
ready ──→ running (profile 被 dispatcher 拉起)
            │
            │ worker 在独立进程中执行
            │
         done ──→ 触发下游任务变为 ready
```

## 四、知识传递机制

```
developer 完成的工作
        │
        ▼
auditor 的审查报告
        │
        ▼
chronicler 读取两者产出，提取关键经验
        │
        ├──→ 写入 ~/.hermes/skills/  (长期可复用)
        │     命名规范: project-<项目名>/SKILL.md
        │
        └──→ 写入 ~/.hermes/knowledge-base/  (可全文检索)
              按日期/模块归档的 markdown 文件

developer 在下一次被调度时，
manager 通过 --skill 加载相关经验:
  kanban create "实现登录功能" \
    --assignee developer \
    --skill "project-auth/经验总结"
```

## 五、创建和配置命令

```bash
# 1. 创建 5 个 profile
hermes profile create main
hermes profile create manager
hermes profile create developer
hermes profile create auditor
hermes profile create chronicler

# 2. 设置 main 为默认 profile
hermes profile use main

# 3. 各 profile 配置不同的模型
hermes -p manager   model   # 选便宜模型
hermes -p developer model   # 选强模型
hermes -p auditor   model   # 选稳重模型
hermes -p chronicler model  # 选中档模型

# 4. 各 profile 配置工具集
hermes -p manager   tools   # 关闭 terminal
hermes -p developer tools   # 开启 terminal, file, web
hermes -p auditor   tools   # 开启 terminal, file, vision
hermes -p chronicler tools  # 开启 file, skills

# 5. 初始化 kanban
hermes kanban init

# 6. 启动 gateway 让 dispatcher 跑起来
hermes gateway start
```

## 六、使用守则

| 场景 | 怎么做 |
|---|---|
| 日常开发、写代码 | `hermes` → 进 main profile |
| 发起一个新任务 | 在 main 里用 kanban 命令创建任务，指定 `--assignee developer` |
| 查看任务进度 | `hermes kanban list` |
| 查看某个任务的详情和日志 | `hermes kanban show <id>` / `hermes kanban log <id>` |
| 手动触发 dispatch | `hermes kanban dispatch` |
| 某个 developer 任务卡住了 | 手动切过去排查：`hermes -p developer chat`（非日常操作） |
| 查看 chronicler 沉淀的知识 | `hermes skills list` / 查看 `knowledge-base/` 目录 |
| 在 main 里加载之前的经验 | `/skill project-auth/经验总结` |

## 七、关键原则

1. **main 是唯一的日常入口**，其他 4 个 profile 只被 kanban 调度
2. **记忆只积累在 main**，kanban worker 不关心记忆
3. **知识沉淀 = skills**，不是 memory，天然跨 profile 共享
4. **模型各取所长**，manager 便宜、developer 强、auditor 稳重
5. **工具按需开放**，manager 不碰 terminal 防止误操作
