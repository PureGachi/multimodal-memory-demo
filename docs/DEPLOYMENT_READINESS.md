# 部署验收与剩余事项

更新：2026-10-29（北京时间）。本页区分本地证据、目标服务器验证和官方验收，不以“镜像构建成功”代替参赛通过。

## 目标服务器现状（2026-10-29）

部署交接与提交字段见 [服务器交接与参赛材料](SERVER_HANDOFF_2026-10-29.md)。Ubuntu 22.04、4 vCPU/8 GiB 的 ECS 上，宝塔已导入 `memory-demo:verify-20261026-6883f9c4` 并运行 `memory` 容器。服务器本机与 Windows 客户端分别确认 `/health`；一次独立文本 Add/Search 和一次合成图片 Add/Search 已通过。`MEMORY_PROFILE=competition`、`text-embedding-v4`、`gpt-4o-mini` 已在运行容器内核对。服务器本机和 Windows 公网 HTTPS 的证书验证成功，公网无密钥 Search 返回 401。这些是运行与链路证据，不是官方 Smoke 或 Full。

目前反代为 `https://8.163.101.129` → 宝塔 Nginx → `127.0.0.1:32769` → 容器 8000；数据目录为 `/data/server-smoke/runtime`，`/data` 绑定至原匿名 Docker 卷的宿主目录。不要按本页下方的旧模板直接运行 Compose、切换卷或覆盖现有环境变量。IP 证书有效期约 6 天，宝塔页面称默认自动续签，但尚未观察到实际续期；两条待验证订单暂保留。需要确认评测数据的专用登记、服务容量、提供商数据处理合规与至少 30 天公网运行条件。

## 已完成的本地验收

| 项目 | 实际结果 | 证据 |
| --- | --- | --- |
| Docker/Compose | Windows Docker Desktop 的 Linux amd64 引擎正常；Compose 文件可解析，不加载私有 env 文件 | 本机 CLI；`config --no-env-resolution --no-interpolate --quiet` |
| Linux 生产镜像 | 构建成功；UID 10001；安装后的 Python 包；包含 MIT 许可证；不包含项目 .env、数据、Conda、测试或脚本目录 | 下方 Docker 报告 |
| HTTP/媒体 | 鉴权、同步 Add、重复请求、用户隔离、Top K=100/0、冲突、请求体上限、PNG/JPEG/WebP、图片查询与图文顺序通过 | 共用 `_http_checks.py`，真实 Linux HTTP |
| 持久化 | 删除并重建测试容器后，挂相同的独立卷，消息 ID 和原始图文保持一致 | `recreated-container-same-volume-durability` |
| 容器运维 | 健康检查、Linux pip check、本机端口映射通过；主测试无外网出口，无宿主机端口 | Docker 报告 |
| 参赛配置启动 | 假密钥/保留域名、无外网情况下，启动、隐藏文档、鉴权、空 K 查询及输入拒绝通过 | 不是实际模型推理验收 |
| 全套回归 | Linux **258 passed**；Windows **257 passed、1 skipped** | Linux 报告；Windows 跳过为符号链接权限夹具 |
| 资源清理 | 本次的容器、合成数据卷、测试网络和临时源码快照已删除；镜像与报告保留 | 报告 cleanup 字段；没有清理用户数据或正式卷 |

Docker 报告：`data/evaluations/deployment/20261025T164252Z-docker-0a8533bb.json`。
Linux 报告：`data/evaluations/deployment/20261025T164828Z-linux-tests-3d2d1bab.json`。
补齐工具守卫后的复核也通过：Docker `20261025T165753Z-docker-6e525427.json`；Linux `20261025T165733Z-linux-tests-4f52e184.json`，仍为 258 passed。
文件名使用 UTC，所以 10 月 26 日北京时间的记录可能以 `20261025T16...Z` 开头，不是跑错了日期。

Linux 测试使用单独的开发镜像，才安装 Git、Node、Tesseract；生产镜像没有因此加入这些系统工具。只读源码快照不包含真实 .env、.git、数据或 Conda；测试关闭外网，不调用付费模型。

## 镜像与归档

- 本地生产镜像标签：`memory-demo:verify-20261026-6883f9c4`。
- 镜像 ID：`sha256:c50d219952fda5e3ff96a361f68c45d0ae055e33fe20474c69fcbd0602574216`，Linux amd64，镜像 Size 为 309,375,988 字节（约 295 MiB）。
- 已核对安装包内 58 个 Python/HTML 运行文件，与当前工作区源码一致；规范化 CRLF 后的集合摘要为 `2750597c31768cd76ae104c6f4292b90d7a550d4c714cfe12d526f201ef964b7`。
- 导出文件：`data/evaluations/deployment/artifacts/20261026-docker-local-6883f9c4/memory-demo-linux-amd64.tar`，70,607,360 字节。
- 归档 SHA-256：`44cc831d5069c0ece76e6074f63989e76f8822589a372d5d34aea584a2eeeb68`。

镜像不包含模型密钥或用户数据，只是运行程序。不要把开发测试镜像 `memory-demo:linux-tests-...` 用作参赛部署。本地候选源代码 Commit 为 `ffe3b7da278126b7eb364fbaef10eab209af35c2`；写入本轮部署文档后的最终 Git Commit 还须再次核对公开远端与服务器镜像来源，不能仅凭镜像标签认定完全一致。

## 如何重复本地检查

先从项目根目录构建自己选定的唯一标签，再运行（下载基础镜像/依赖会联网，但不调用模型）：

```powershell
docker build --tag memory-demo:local-check .
.\.conda\python.exe -X utf8 scripts/docker_smoke.py --image memory-demo:local-check
```

`docker_smoke.py` 不加载 .env、不构建或拉取镜像。它创建唯一命名、有归属标签的临时容器/卷/网络，仅清理自己拥有的资源。大部分测试在内部网络通过容器内真实 HTTP 执行；另起一个关闭模型的容器检查宿主机 127.0.0.1 映射。不会开放公网。

完整 Linux 测试另外运行：

```powershell
docker build -f deploy/Dockerfile.test --build-arg BASE_IMAGE=memory-demo:local-check --tag memory-demo:linux-tests .
.\.conda\python.exe -X utf8 scripts/linux_tests.py --image memory-demo:linux-tests
```

这会额外下载测试工具，占用额外磁盘；不是正式运行必须安装的内容。已有 Docker 资源不会被全局 prune。测试时使用的 512 MiB/2 GiB 容器限额只是小型夹具条件，不是正式服务器最低规格或 Full 容量证明。

## 目标服务器上的后续检查

1. 核对服务器运行镜像 ID、导入归档来源和最终公开 Git Commit；不要移动已发布的旧 `v0.3.0` 标签，也不要把可移动的 `main` 写成固定版本。
2. 观察 IP 证书的自动续签实际成功；同时确保 ECS 试用到期后仍可连续提供官方要求的服务时长。续签前不删除已部署证书及其验证配置。
3. 在正式评测前，按 [数据保留流程](DATA_RETENTION.md) 登记官方运行目录，核对卷、备份和删除责任；当前服务器 smoke 数据不自动迁移。工作台 8010、Docker 管理接口、数据库和报告目录不公开。
4. 测量带宽、内存、存储、请求时长和并发；确认模型供应商与中转的数据处理规则，并向官方核实正式评测资料能否经该中转。
5. 按 [官方流程](https://agentmemories.ai/rules) 提交固定公开版本、方法来源、HTTPS Add/Search 地址、`X-Api-Key` 鉴权和真实容量信息，取得资格并完成官方 Smoke，再申请 Full。这里的本地测试不是官方 Smoke。

新上下文图片提示词仍未做实际付费对照；关联图片补全也不是本轮 Docker 工作解决的。它们属于质量改进，不要把完整回归通过写成 VS 分数提升。
中转服务的模型真实性与正式数据处理合规也不能由镜像、配置模型名或本地接口测试证明，需要官方/供应商确认。

## 尚待外部确认的部分

服务器基本部署和公网链路已通过，但没有官方 Smoke/Full、全负载容量测试、实测证书续期或服务器 30 天可用性的证据。正式数据的真实完成时间未知，不能虚构删除截止日；用户联系身份和官方申请仍须由用户提供/提交。正式评测资料通过第三方模型中转时的许可与数据处理责任尚待确认。公开版本须在发布检查通过后核对，旧 `v0.3.0` 标签不移动。
