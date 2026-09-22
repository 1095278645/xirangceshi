# 更新日志

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/) 与 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

## [1.1.0] - 2026-09-22

从"功能可用"走向"可度量、可解释、可开放、可传播"。

### 新增
- **运行指标看板**（`db_metrics.py` / `metrics.py` / `routers/metrics.py`）：
  `ai.chat` 每次调用旁路记录模型、耗时、token 用量与成败；`GET /api/metrics/ai` 汇总
  调用量/成功率、token、延迟 P50/P95、**估算成本与每单成本**；H5 新增「更多 → 运行指标」页。
- **接口限流**（`ratelimit.py`）：对免鉴权接口（收款页、语音上传）做滑动窗口限流，防刷。
- **开放 API / Webhook**（`notifications.py` 新增 `webhook` 通道）：把 `order_created` 等事件
  以 JSON POST 出去，支持 `url|密钥` 的 HMAC-SHA256 签名，供 ERP / 代账 / 自有看板订阅。
- **结论可解释**：`GET /api/orders/{id}/explain` 返回该笔的借贷分录与"凭什么这么记"。
- **同业基准（示例/仿真）**：`GET /api/store/benchmark` 把本店指标与同业态参考区间对比，
  返回值带 `disclaimer` 明确声明非真实行业数据。
- **口语 / 方言偏好**：设置页可选「普通话/粤语/四川话/英语」，非普通话时提示模型按语义识别。
- **评测报告**（`eval_report.py` + `eval_ai_parse.py --report`）：分维度准确率 + 失败清单，
  输出 JSON 与 Markdown 看板，便于归档/进 CI。
- **PWA**（`manifest.webmanifest` / `sw.js` / 图标）：H5 可"添加到主屏幕"并具备基本离线能力。
- **无障碍**：右下角字号缩放与语音朗读（`a11yFont` / `a11yRead`）。
- **一键复位演示环境**：`scripts/demo_reset.ps1`。
- **云化演进文档**：`docs/deployment-evolution.md` + `ops/litestream.yml`（异地容灾样例）。

### 变更
- `db.SCHEMA_VERSION` 2 → 3（新增 `ai_metrics` 表，老库自动迁移）。
- `ai.chat` 新增 `domain` 参数，用于按业务域统计成本；`scripts/*` 与团队编排调用点已标注。
- `config` 新增 `language` 配置项（环境变量 `SHOP_LANGUAGE` 可覆盖）。

### 测试
- 新增 33 项测试：`test_metrics` / `test_ratelimit` / `test_webhook` / `test_eval_report` / `test_ops_extras`。

## [1.0.0] - 2026-09-22

首个稳定版本：从小程序现场演示走向「可复现、可部署、可协作」。

### 新增
- **许可证**：采用木兰宽松许可证（Mulan PSL v2）。
- **持续集成**（`.github/workflows/ci.yml`）：单元/集成测试、演示自检、接口契约、页面静态检查、网页端渲染试跑、Docker 镜像构建。
- **容器化**：`Dockerfile` + `docker-compose.yml` + `.dockerignore`，一条命令即可起后端。
- **依赖治理**：`requirements.lock`（确定性锁，含传递依赖）、`requirements-dev.txt`、`pyproject.toml`（pytest/coverage 配置）。
- **配置模板**：`server/config.example.json`，明确「密钥仅本地、已 gitignore」。
- **一键演示脚本**：`scripts/demo_up.ps1`（起后端 → 查局域网 IP → 灌数据 → 预热 → 自检）。

### 变更
- 小程序 `appid` 调整为正式 AppID；`app.json` 声明「同声传译」插件，语音「按住说话」可用。
- `server/ai.py`：将 `deepseek-v4.1-flash` 登记为思考模型，默认关闭思考，兼顾速度与多 agent 角色差异化（temperature 生效）。
- README：修正测试数量口径、补充 Docker/依赖锁/许可证说明与徽章。

### 修复
- 无。

### 变更说明（演示数据）
- 演示数据库与 AI Key 均**不入库**（`.gitignore`）。换机后需按 `docs/demo-guide.md` 重新灌数据与配置 Key。

---

## 历史

`1.0.0` 之前为早期迭代（语音记账本地化、多 agent 编排、会计闭环、多店/多用户等），详见 git 提交历史。
