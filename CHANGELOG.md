# 更改记录

本文件记录每次提交的简要说明。格式：`YYYY-MM-DD | 修改内容`。
提交前自动运行 `scripts/check_privacy.py` 做隐私检查，通过后方可推送。
新条目追加到末尾，不改动已有内容。

---

2026-06-25 | v1.3 初始版本：CC Switch 风格使用统计仪表盘
2026-06-25 | v1.4 重建：双图表布局、中文界面、筛选器、骨架屏加载、前端错误捕获
2026-06-26 | 时间轴移除 TOTAL 行，添加时区选择器；profile 切换器；默认刷新 30s
2026-06-26 | Codex CLI 解析器；测试套件（parser 50/98%, server 55/93%, integration）
2026-06-27 | chart tooltip 修复（颜色同步、显示全部堆叠值、方形标记）
2026-06-27 | Codex CLI parser 独立；parser/server/integration 测试套件
2026-06-27 | 模型定价 columns；多个样式迭代（绿色色调、图标、列宽、字体）
2026-06-27 | cache-read pricing 公式；汇率切换设置面板；图表实心填充/颜色调整；侧边栏拆分缓存行
2026-06-27 | 自动发现数据源 + agent 筛选；双语 README；价格每百万 token 列；USD/CNY 定价分离
2026-06-28 | 供应商统计使用 api_call_count；时区允许 0(UTC)；汇总/模型筛选 bug 修复
2026-06-28 | 时间筛选支持时区偏移；flatpickr 本地化移除 CDN 依赖；deepseek-chat 合并为 v4-flash
2026-06-28 | 汇总添加请求数和平均 token；模型统计添加平均 token 列；api_call_count 取代会话数
2026-06-30 | proxy provider/mapping 管理重构（server.py, proxy_db.py）
2026-06-30 | 创建 CHANGELOG.md 和 scripts/check_privacy.py；.gitignore 排除 .temp_prompt.txt 和 dist/
2026-06-30 | 适配器模式重构：adapters/ 目录，Hermes 适配器改 config.yaml + .env 环境变量，替换 _toggle_hermes_config
2026-06-30 | fix: Hermes 适配器同时设置 model.base_url（Hermes 实际读取的字段）
2026-06-30 | refactor: 数据源切换为仅代理数据库，移除文件解析器
2026-06-30 | Hermes 适配器多路径支持 + state session 代理 + 请求级 auth 转发（MiMo 路由）
2026-06-30 | UI 提供商/映射表单改为弹窗，请求日志移至顶部行
2026-06-30 | 隐私检查脚本重写：智能误报过滤，新增 SSH/JWT/URL 凭据检测
2026-06-30 | 测试：新增 hermes adapter 单元测试 + 代理透传路由测试
2026-06-30 | 最近请求限制 10 条 + 布局对齐（最近请求跨行与上游提供商/模型映射对齐）
