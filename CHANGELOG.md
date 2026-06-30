# 更改记录 / Changelog

本文件记录 Token Dashboard 项目的所有修改。每次修改需：
1. **隐私检查**：扫描代码中是否包含绝对路径、API Key、Token、密码等敏感信息
2. **推送 GitHub**：通过 git 提交并推送至远程仓库
3. **记录至此文件**：注明日期和修改内容

---

## 2026-06-30

### Added
- 新增 CHANGELOG.md 修改记录文件（本文件）
- 新增 `scripts/check_privacy.py` 隐私检查脚本，用于提交前扫描敏感信息
  - 检查硬编码的 Windows/Mac/Linux 绝对路径
  - 检查 API Key（`sk-...` 格式）等敏感字符串
  - 检查 `password`/`secret`/`token`/`api_key` 等敏感变量赋值
  - 检查 `mimo_cookies.json`、`deepseek_key.txt` 等已知敏感文件是否被暂存

### Changed
- `.gitignore` 添加 `.temp_prompt.txt` 排除临时文件

### Refactored
- `hermes_token_dash/server.py` | `hermes_token_dash/proxy_db.py` — proxy provider/mapping 管理重构 (#7489a24)

---

## 2026-06-28

### Added
- CHANGELOG 机制初始化（本次提交创建）
- 时间筛选支持时区偏移 (#af26620)
- 汇总添加请求数和平均 token，重命名列标题 (#0dab601)
- 模型统计添加平均每次输入 token 数列 (#c84340a)
- 模型统计添加平均/请求和平均/输入两列 (#e90c460)
- 使用 Hermes api_call_count 作为实际请求数 (#123c056)
- 平均 token 列仅 Hermes 筛选下显示 (#9751934)
- flatpickr 日历 UI 优化 + deepseek-chat 合并为 deepseek-v4-flash + 默认 30 天 (#d9a2dfe)
- flatpickr 改为本地文件，移除 CDN 依赖 (#f1de973)

### Fixed
- 供应商统计使用 api_call_count (#baec449)
- 供应商成功率使用 api_call_count 与请求数一致 (#56062c8)
- 活跃会话使用当前时间显示 (#e832660)
- 模型列表使用 api_call_count 而非会话数 (#6eaf878, #c97727f)
- timezone() 需要 timedelta 对象，api_trends 添加 tz 参数 (#60fbf37)
- 模型筛选时反向映射别名 (#14b0f80)
- 所有 fetch 函数使用反向映射，user_inputs 支持时间筛选 (#a9d62ff)
- merged 计算属性添加 user_inputs 字段 (#d4761eb)
- 时区允许设置为 0(UTC) (#fe2d0d5)
- 请求平均 token 始终显示，用户请求平均仅 Hermes (#1179936)

### Style
- 汇总调整顺序，Hermes 添加用户请求数 (#66910d0)
- 汇总添加总 token 消耗，调整输入输出顺序 (#4ae3a05)
- 汇总请求数/平均 token/缓存写入调暗 (#1e378bc)

---

## 2026-06-27

### Added
- 自动发现数据源 + agent 筛选 (#49b9b9f)
- cache-read pricing 在成本公式中生效 + UI 优化 (#c9f6df1)
- 模型定价列 + 更深的中间绿色 (#65c1c81)
- 分离 USD/CNY 定价 — CNY 模型不受汇率影响 (#86f62b3)
- 更新定价，添加汇率切换 + 设置面板 (#774665b)
- 添加价格每百万 token 列到模型统计 (#d7a6626)
- 图表 tooltip 显示所有堆叠值 (#dde3129)
- 添加 GPT-5.5 缓存读取价格 (#5531744)
- 独立 codex parser (#4789354)

### Fixed
- 前缀 Hermes agent 为 'hermes:' 避免与 Claude Code agent 冲突 (#99505af)
- 隐藏零成本 provider (#cd22509)
- 合并模型统计行按模型名 (#48872a8)
- 标准化 Claude Code input_tokens 包含 cache_read (#a2b154c)
- 标准化 Hermes input_tokens 包含 cache_read (#c17e6e7)
- 应用汇率到所有成本计算 (#442e0e4)
- 使用 models.EXCHANGE_RATE 引用而非导入值 (#3e5639e)
- 添加 UTC 偏移到时区选择器标签 (#d3ab20e)
- 修正所有模型定价来自官方源 (#dc46b10)
- MiMo 定价与 DeepSeek 对标 (#5548966, #939b2bf)
- round() 元组错误在 api_logs 成本计算 (#898a237)
- 图表 tooltip 标签颜色与实际图表颜色同步 (#4170e90)
- 删除 chart code 中的转义反斜杠-n (#1916f8c)
- 从 token chart 移除 barPercentage 统一条形宽度 (#63d6e6d)
- lastRefresh 时区一致性与 timezoneFilter (#d5f990b)

### Style / Refactor
- 中英双语 README (#6ec6069)
- 侧边栏重构：输入拆分为缓存命中/未命中行 (#7534de1, #03be089, #6568fb8)
- 侧边栏样式：绿色 io/cost，灰白缓存 (#2013c0c)
- 图表实心填充 — 移除边框 (#19abcee)
- 多个图表绿色色调调整迭代
- Chart bar 宽度限制 maxBarThickness:50 (#4c20197)
- 设置图标改为交替齿轮 (U+26ED) (#3dc6dec)
- 定价列拆分为 3 列：非命中/命中/输出 (#0fcd242, #4fab82e)
- 按钮样式柔和 + gear 图标 (#ec34711)
- 默认刷新间隔改为 30s (#3dd9fdb)

---

## 2026-06-26

### Added
- Codex CLI 解析器 — 解析 ~/.codex/sessions/ rollout JSONL 文件 (#4789354)
- parser 单元测试 50 个，98% 覆盖率 (#a962fc0)
- server 单元测试 55 个，93% 覆盖率 (#4a25ac7)
- 跨模块集成测试 (#0bcfdc7)

### UI
- 移除 TOTAL 行/日期列，添加时区选择器，限制图表宽度，修复刷新 (#15e5212)
- 添加 profile 切换器和 /api/profiles 端点 (#7f12223)
- 后端添加 profile 字段和 API 筛选 (#6d748f3)

### Fixed
- timer 在间隔变化时重启，空数据时图表清空 (#9dbb78d)

---

## 2026-06-25

### Added
- v1.4: 基于最小验证重建完整仪表盘，单文件压缩版
- 双图表布局：左 Token 消耗堆叠柱状图 + 右价格柱状图
- 中文界面、`<synthetic>` 过滤、柱状图堆叠、工具筛选器、去除请求 ID 列
- 添加前端错误捕获
- 桌面启动等待服务器就绪再开窗口
- 加载优化：服务端预加载缓存 + 前端加载骨架屏
- 添加 README 含安装和 API 文档
- v1.3: CC Switch 风格使用统计仪表盘（初始版本）

### Fixed
- CDN 脚本移至 body 末尾消除阻塞，回退骨架屏
- 回退 index.html 到双图表正常版本，仅保留 try/catch 错误捕获

---

## 隐私检查清单

提交前请运行以下检查：

```bash
python scripts/check_privacy.py
```

扫描项：
- ❌ 硬编码的 Windows 绝对路径（`C:\Users\...`、`D:\...`）
- ❌ 硬编码的 macOS/Linux 绝对路径（`/Users/...`、`/home/...`）
- ❌ API Key 模式（`sk-...`、`api_key`、`api-key`、`apikey`）
- ❌ Token / Secret / Password 变量赋值
- ❌ 敏感文件（mimo_cookies.json、deepseek_key.txt、*.key）是否在 git 跟踪中
