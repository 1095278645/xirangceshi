# 更新日志

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/) 与 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

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
