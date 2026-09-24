# AGENTS.md — 开发约定与必守铁律

> 面向在本仓库工作的 Agent 与人。**「铁律」任何改动都不得违反；「约定」应遵循，例外需在提交说明里写明理由。**
> （本文件是 Agent 的精准入口；README 面向用户与评审。）

---

## 一、必守铁律（5 条）

1. **密钥与账本绝不入库**
   只放 `server/config.local.json` 与 `server/data/`（均已在 `.gitignore`）。
   新增任何敏感文件（证书、导出包、密钥）必须**同步**加进 `.gitignore`；提交前用
   `git check-ignore -v <path>` 复核。

2. **危险操作必须显式确认**
   删除 / 覆盖 / 恢复类接口一律要求 `confirm=true`（见 `routers/backup.py`、
   `routers/evolution.py` 的 approve）。**不允许**出现"缺省即执行破坏动作"的默认值。

3. **数据默认留在本地**
   不得引入"默认上传用户经营数据"的行为；任何外部调用（模型、推送、存储）必须
   可选、可关闭、并在文档中明示。

4. **变更必过验收，禁止"改了就算"**
   ```bash
   cd server && python -m pytest -q                      # 全量测试
   python ../scripts/mp_demo_check.py                    # 演示前自检
   python scripts/check_mp_api.py                        # 小程序接口契约
   python scripts/check_mp_pages.py                      # 小程序页面绑定
   python scripts/check_web_pages.py                     # 网页端事件/路由
   node   scripts/check_web_render.js                    # 网页端渲染试跑
   ```
   改前端必须加跑对应检查；改文档/配置至少要跑测试。

5. **有限取值必须用枚举**
   请求参数、配置项取值有限时使用 `Literal` / 枚举（见 `server/schemas.py`），
   **禁止**自由文本 + 运行期兜底或静默取默认值。例外的开放取值需在代码注释说明理由。

---

## 二、开发约定

- **阈值入 `config.py`**：禁止业务代码里散落魔法数（年份、状态码、进化阈值、提示词预算等）。
- **脚本输出 → LLM 前必须截断 / 摘要**：`ai.chat` 有统一预算护栏兜底
  （`AI_PROMPT_WARN_CHARS` 告警、`AI_PROMPT_MAX_CHARS` 截断并保留 system），
  但**不要依赖它**——各处应先按语义取 Top-N（如 `shop_snapshot` 的 `[:3]/[:4]`）。
- **单文件尽量 ≤ 400 行**：超过时按**既有职责边界**外移（搬家，不新增抽象层）。
- **注释与文档用中文**；接口、参数命名自描述（Agent 与人都要一眼看懂）。
- **文档渐进披露**：低频内容（完整 API 表、目录树、深度排障）放 `docs/`，README 只留指针。
- **双前端共享契约（H5 × 小程序）**：存储键、请求头、HTTP 错误文案、UI 枚举的**唯一真源**是
  `shared/frontend_contract.js`。改这些内容**只改真源**，然后跑
  `python scripts/sync_frontend_contract.py`；两处副本带 `AUTO-GENERATED` 标记、
  **禁止手改**。CI 会跑 `python scripts/check_frontend_contract.py` 校验"副本与真源逐字节一致"
  且"UI 枚举与 `server/schemas.py` 的 `Literal` 一致"，漂移即失败。
- **护栏改动需带测试**：`safe_io`（受保护路径/原子写）、限流、备份恢复、进化验证门等，
  改动必须附单元或 HTTP 测试。
- **多 agent 编排**：新增业务域遵循 `TEAM_DOMAINS` / `BUSINESS_DOMAINS` 声明式注册表，
  调用点在 `ai.chat` 传 `domain=` 以便成本归因。

---

## 三、目录速览

| 路径 | 作用 |
|---|---|
| `server/` | FastAPI 后端（`routers/` 按域拆分，`db*.py` 数据层，`ai*.py` AI 层） |
| `server/static/` | 网页版（手机浏览器直接访问） |
| `miniprogram/` | 微信小程序 |
| `scripts/` | 演示/自检/数据脚本 |
| `docs/` | 演示手册、API 一览、目录结构、部署演进、安全审计 |
| `deliverables/` | 演示与评审材料（已忽略，不入库） |

---

## 四、AI Key 与演示

- 无 Key 也能跑（规则兜底）；配 Key 后启用真实模型与多 agent。
- **演示前两件事**：①「设置 → API 能力档」切到 **完整（full）**；② 灌演示数据
  `python ../scripts/seed_demo_data.py`。详见 `docs/demo-guide.md`。
- 进化层**默认关闭**（`SHOP_ENABLE_EVOLUTION=1` 开启）；开启后候选基因须过验证门
  （证据门 + 可选 `SHOP_EVOLUTION_VERIFY_CMD` 基准门）才能转正。
