# 部署准备与验收（2026-10-25）

这里区分“本地已经验收”和“服务器/官方尚待验收”，不能把本地通过写成已经参赛成功。

## 先看结论

- 服务入口是 `memory_app.main:create_app`，不是工作台。
- 服务默认端口 8000；工作台默认 8010，仅留在自己的电脑上。
- SQLite 和原图必须一起持久化；`.env`、密钥、评测资料不得进入镜像或 Git。
- v0.3.0 新增 [数据保留工具](../docs/DATA_RETENTION.md) 与 [固定版本流程](../docs/RELEASING.md)。默认运行目录现在是卷内 `/data/official/evaluation/runtime`；已有数据不自动迁移。正式首启前登记空目录，不要把改配置当成迁移成功。
- v0.3.0 的最新离线安装包验收：`data/evaluations/deployment/20261025T111044Z-package-3dd79578.json`，SHA-256 `b8ca2300a0b84043b1e23b7b144e06b129593b1a11bc3c6ffbd32505d9ac1a42`，零模型调用。下文 v0.2.0 哈希和成绩保留为历史记录。
- 当前默认 RRF 仍等权，没有把第一场景的调参结果直接用于正式服务。
- 已有真实模型完整实验见 `data/evaluations/mem-gallery/20261025T021732Z-0d2107a9.json`。
- 新增验收使用临时合成数据、零模型调用，不是官方 Smoke，也不是新场景模型效果测试。

### 本轮验收证据

**后续源码变更提醒：** 本日新增图片“对话背景”索引。下面 Architecture 成绩属于旧的仅图片分析版本，不是新提示词效果；新版本尚未付费评测。新的离线 wheel/HTTP 验收报告为 `data/evaluations/deployment/20261025T102056Z-package-3f7961cc.json`，SHA-256 为 `9b51f688395675d8f6605f61c31a4f359aa863ea93dacb286ea9c1e8d0c3877e`。旧 wheel 哈希仅作历史记录。新版本与旧检查点分析指纹不同，请勿强行混用。
新增上下文功能后全套离线回归为 **216 passed**；下表 206 为之前部署准备阶段的历史记录。

截至 2026-10-25，本地准备阶段通过以下检查，但不意味着公网部署或官方验收完成：

| 项目 | 结果 | 证据 |
| --- | --- | --- |
| 离线回归 | 206 passed | `python -m pytest -q` |
| 安装 wheel 后真实 HTTP、重启持久化 | 通过；零模型调用 | `data/evaluations/deployment/20261025T084517Z-package-7f9c7e0e.json` |
| Architecture 全历史与全部问题 | 186 轮、372 条消息、74/74 题 | `data/evaluations/mem-gallery/20261025T053720Z-a987edf0.json` |
| 历史图与提问图 | 30 张历史图、6 张提问附图；两张大图使用明确记录的压缩副本 | 同上，未混入数据集自带图片描述 |
| 工作台后端 | 报告列表、报告读取及全部 32 个不同的被引用图片资源均正常 | 本机 TestClient 零模型检查；不是浏览器视觉验收 |
| 预先选定 RRF 权重比较 | 原基线一致，零模型调用 | `data/evaluations/mem-gallery/20261025T085204Z-rrf-bb56f6d4.json` |

本次 wheel SHA-256：`289ba7c61d006472b89d47c07b273bba0dc0866431724e09615ea1744fd4e948`。源码后续再修改时，需要重新构建和验收，不能继续套用这个哈希。

建筑场景等权混合 Hit@5=86.7%、Recall@5=67.6%（60 道有轮次标注题）；图片 Hit@5=52.6%、Recall@5=47.6%（38 道有图片计分依据题）。这只是本项目的检索诊断，不是官方回答正确率，也没有据此宣称达到官方通过线。
关键词:向量=0.25:1 时轮次 Hit@5 不变、Recall@5=68.0%、MRR@5 从 0.649 升到 0.680；但图片 Hit@5 没提高，而 0.5:1 在图片 Hit@5 上更好。两场景指标存在取舍，暂不改变生产默认等权；不是证明等权最优。

本轮遇到过 embedding/视觉连接与 TLS 中断，均从同一检查点续跑；最终完成不证明网络可靠性已解决。上线前必须在目标机器复测两个供应商的连通性、超时与稳定性，不能靠关闭证书校验绕过。

官方规范入口：[Add/Search 接入与参赛说明](https://agentmemories.ai/rules)。需要公网服务、固定版本与鉴权、官方 Smoke 再 Full；接口支持 Top K=100、Base64 有序图文内容，单图 10 MiB、单次媒体 30 MiB。最终以提交当天规范及官方验收为准。

## 本地零费用检查

在项目目录 PowerShell 执行：

```powershell
.\.conda\python.exe -m pytest -q
.\.conda\python.exe -X utf8 scripts/deployment_smoke.py
.\.conda\python.exe -X utf8 scripts/package_smoke.py
```

第二条启动独立的本机子进程，忽略 `.env` 和模型环境配置，模型全部关闭，数据使用临时目录。
验证鉴权、立即可查、重试不重复、图文顺序/三种图片格式、提问图片、用户隔离、Top K=100/0、冲突、请求体上限和重启后持久化。
测试自己的临时数据会在结束后清理，不删除用户的数据库或报告。

第三条在禁止联网的 pip 模式下构建 wheel，检查内容没有数据或密钥，安装到独立 venv 前缀后重复 HTTP 验收。
依赖复用当前机器的已安装包：这不是独立 Linux 环境依赖安装的证明。wheel 及 SHA-256 保存在 `data/evaluations/deployment/` 下。

## 配置与容量

不要覆盖自己的 `.env`。部署配置单独建立 `.env.deploy`（已被 Git 忽略），参考 `.env.competition.example` 逐项填写。

- `MEMORY_API_KEY`：参赛服务的访问密钥；和模型服务的两把密钥不同。
- `EMBEDDING_*`：embedding 服务。`VISION_*`：图片理解服务。服务器需要重新配置，不把本机密钥打进 wheel/镜像。
- competition 配置要求指定模型名、关闭未确认的本地 OCR、开启服务鉴权；只检查配置，不证明中转站实际使用了所声称的模型。
- 正式评测前向官方确认中转站及隐私处理是否允许；不要向未经允许的第三方发送正式评测资料。
- 示例总并发上限 `MEMORY_MAX_INFLIGHT=2`，同时包括正在收包的请求；超出返回 503 和 Retry-After，不触发模型。
- `MEMORY_MAX_REQUEST_BYTES=67108864`：原始 JSON 请求体 64 MiB 的部署容量声明，不是官方全局字符限制。超出返回 413，不静默截断。
- `MEMORY_BODY_TIMEOUT_SECONDS=30`：接收请求体的总时限，不是模型推理或完整 Add 的响应时限。服务器上传带宽不足时需评估调整。
- 健康检查无需鉴权，不消耗模型、不受 Add/Search 的并发槽影响；它不证明外部模型此刻可用。

首次官方/生产容量先保守申报 Add=1、Search=1，总计不超过本服务配置；这只是初始设置，不保证能处理完整官方规模。
先测目标服务器上的真实耗时、内存、带宽与存储，再提高并发。SQLite/JSON 向量线性扫描可能是规模化瓶颈，不宣称高并发生产性能。

## Docker 准备

本机未发现 Docker，可用文件已经提供，但**本机尚未实际构建或运行 Linux 镜像**。上服务器后的构建是必做验收，不要跳过。
当前 lock 来自 Windows/Python 3.11，Linux 依赖可安装性需要额外核验；不能因为文件叫 lock 就认为跨平台都验证过。

镜像仅复制源码、依赖清单和构建元数据；`.dockerignore` 使用白名单，不发送 Conda、数据、笔记、`.env` 或报告。
进程使用非 root UID=10001，数据目录 `/data` 由卷保存；只运行一个 Uvicorn worker，关闭访问日志和 competition 模式的交互文档。

准备好服务器和私有 `.env.deploy` 后，由操作人确认再执行：

```sh
docker compose -f deploy/compose.yaml build
docker compose -f deploy/compose.yaml run --rm --no-deps memory python -m memory_app.governance.retention init --base /data/official --run-id evaluation
docker compose -f deploy/compose.yaml up -d
docker compose -f deploy/compose.yaml ps
```

Compose 默认只绑定服务器的 `127.0.0.1:8000`，不会直接开放公网。需要另外配置 HTTPS 反向代理、证书、防火墙和公网地址，再提交官方。
代理请求体容量应与服务声明一致，业务读取/响应超时要覆盖长 Add；不能用过短的代理响应超时截断正在执行的请求。
不把工作台 8010、数据库、报告目录、模型密钥或 Docker 管理接口开放到公网。

普通 API 启动对应：

```sh
python -m uvicorn memory_app.main:create_app --factory --host 127.0.0.1 --port 8000 --workers 1 --no-access-log
```

以上命令需要事先安装打包后的服务及依赖，不能把启动命令本身当作部署完成。Conda 可以继续用于本地开发；Docker 内用 Python 3.11，不需要另装 Conda 或 GPU。

## 目标服务器验收

1. 从目标机器启动并检查 `/health`；确认模型配置、服务密钥、卷权限，且未暴露笔记/数据。
2. 确认私有 HTTPS 地址与反向代理容量后，运行本项目 HTTP smoke。它会写入独立测试用户；模型启用时必须显式允许调用，可能收费：

   ```sh
   python scripts/smoke.py --url https://替换为实际服务地址 --env-file .env.deploy --allow-model-calls
   ```

3. 停止/重启容器，确认测试用户还能查询到原证据和图片；测试原模型故障如何反馈，以及成功 Add 的重试不会重复写入。
4. 记录镜像 ID/digest、wheel SHA-256、固定 Git commit、配置版本（不含密钥）、容量和成本限制。工作区未提交变更不等于固定参赛版本；本次用户已授权通过检查后创建 Commit 并推送现有 origin，后续发布仍按版本流程确认。
5. 向官方提交 API、鉴权、版本、容量、公开仓库和方法说明，申请官方 Smoke；本项目的 smoke 不能替代官方 Smoke。

API 模型请求不自动重试，超时仍可能计费；并发重复 Add 只保证最终数据幂等，不承诺所有重复请求只有一次模型计费。
正式过程中不要切换 embedding/视觉预处理、擅自调权重或删除数据；旧向量不会因为改模型名字而自动重建。

## 数据保留、备份与回退

正式数据使用独立评测卷和服务密钥，不与当前公开 Mem-Gallery 实验混放。不记录请求/响应正文，不导出官方隐藏样本来调参。
官方规范要求任务完成后 30 天内删除评测资料及派生副本。工具已提供登记、截止日期、只读预览、检查和显式清理；详见数据保留文档。真实完成时间未知时不设置截止日。当前没有安装后台定时任务，也未删除任何现有正式资料；外部快照、云日志和供应商副本仍需操作人核查处理。

备份前先停止写入；用 SQLite backup API 或停止服务后整体复制整个数据卷，不能只复制正在写入的 `memory.sqlite3` 而丢掉 WAL。
备份需加密、限制访问，并采用同样的删除期限。恢复演练必须使用测试数据，核对消息、向量和图片哈希。
当前已验收重启持久化，尚未在目标 Linux 服务器做真实备份恢复演练。

更新前保留旧镜像/软件包版本和测试数据快照；先验证新版本兼容性，再切换。没有明确确认前不运行删卷、批量删除或覆盖 `.env` 的命令。
如果只重建容器，继续挂载相同卷；不要误用删除卷的选项。评测结束后的销毁由操作人按确认过的目标执行，本次自动准备不会删除现有资料。

## 外部仍待确认

- 已完成获准的一次 Architecture 完整图文评测；其他场景、额外付费 smoke 或重复完整模型评测仍需另行确认。
- 服务器/系统/CPU 架构、部署地址与 TLS 方式；尚未租机、公开端口或绑定域名。
- 目标 Linux 镜像构建、带宽/耗时/存储容量、备份恢复及公网访问。
- 中转通道合规与模型真实性、开源仓库固定版本、官方资格与 Smoke/Full。

这些需要新的外部条件或用户确认，不能用本地单元测试推断已经完成。
