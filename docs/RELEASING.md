# 固定公开版本与发布检查

## v0.3.0 的含义

本次将 Python 包、FastAPI 元数据与 `/health` 版本统一为 **0.3.0**。它表示代码能力版本，不表示已通过正式比赛。
发布候选包含上下文图片索引，但新提示词效果尚未付费验证。默认 RRF 仍为等权。

完整 Git Commit 才能精确定位代码；main 会继续移动，不能只提交 main 地址。若后来改提示词/模型/分块/权重或代码，应创建新 Commit，重跑必要检查，并按官方规则重新确认版本；正式 Full 受理后不可因成绩不好任意换版本。

## 发布前只读检查

```powershell
.\.conda\python.exe -X utf8 scripts/release_check.py --output data/governance/releases/替换为本次唯一报告名.json
.\.conda\python.exe -m pytest -q
.\.conda\python.exe -X utf8 scripts/package_smoke.py
```

退出码 2 通常表示内容待审、缺文件或工作区未提交；不代表程序崩溃。报告不含文件正文或密钥，只显示风险位置、类别及候选文件摘要。
检查未提交候选文件和本机可达 Git 历史，不读取被忽略的 `.env`；发现已追踪私有文件也会报错。`.gitignore` 不会从旧 Commit 中抹掉密钥。若发现真实泄漏，先撤销密钥，再另行审查历史清理；不自动改写历史或强推。
扫描是启发式，不保证没有秘密；笔记里的个人信息、署名、数据许可证和未抓取的远端历史仍需人工审阅。不得为了通过检查盲目屏蔽告警。
已人工核对的合成测试凭据和原仓库架构图记于 `scan-exceptions.json`，限定文件路径、风险类别和完整文件 SHA-256（文本仅规范化 CRLF）；文件内容变化即失效，不能按目录或规则整体跳过。审查结果会单独列出这些例外，而不是声称从未出现告警。

本项目 MIT 许可证由用户明确选择。它覆盖项目代码，不授予外部模型、依赖或数据集许可；引用见 METHOD.md。

## 提交与远端核对

仅在用户授权并检查通过后，将审阅过的源码、脚本、测试、文档、示例配置和许可证暂存；不要直接把私有数据混入 `git add .`。本次保留既有笔记合并和代码改动，不回退用户文件。

提交后重新运行检查并指定完整 hash：

```powershell
git rev-parse HEAD
.\.conda\python.exe -X utf8 scripts/release_check.py --expect-commit 替换为完整Commit --output data/governance/releases/替换为另一个唯一报告名.json
```

固定本地 Commit 的判定需要：内容/本机历史检查通过、必需文件齐全、工作区干净、完整 hash 匹配。检查工具本身不创建 Commit、不推送、不联网，报告里的 `public_remote_verified=false` 需要后续远端证据补齐。

推送不使用强推，不自动修改远端或仓库可见性。如远端出现新提交或冲突，停止并讨论，不覆盖他人工作。GitHub 登录/凭据由用户维护，禁止把 token 写进远端 URL。
核对远端 main 的完整 hash 与本地一致，再检查公开提交页可以访问。公开仓库已存在不代表新 Commit 已公开。

## 私有发布登记

确切 Commit 只能在提交产生后记录，不能把 Commit 自己的 hash 写进同一个 Commit。发布证据存于被忽略的 `data/governance/releases/`；对外提交真实的 GitHub `/commit/<完整hash>` 地址。

部署登记还应包括：包版本、Commit、wheel SHA-256、镜像 digest、部署时间、配置非敏感摘要、模型与提示词版本、分块和 RRF 参数、测试证据、容量声明，以及负责人。
不记录模型密钥或评测专用服务密钥，不导出正式评测内容。当前本地 wheel 验收不证明 Linux/Docker 或公网部署成功；最终镜像构建和模型供应商稳定性须在目标机器验收。

## 收尾清单

- [ ] 候选与本机历史扫描通过，人工审阅笔记/引用与 MIT 许可。
- [ ] 全套离线测试和安装包 HTTP 验收通过。
- [ ] 已产生完整 Commit，提交后工作区干净。
- [ ] 远端该 Commit 可访问，远端 main 与本地一致；如打标签，标签也须指向相同 Commit。
- [ ] 实际部署的代码/镜像来自该 Commit，不只是本地文件夹。
- [ ] 提交材料包含固定版本、接口、鉴权、容量和方法来源；官方 Smoke/Full 另行完成。
