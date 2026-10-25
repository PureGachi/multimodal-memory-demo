# 工具分类索引

更新：2026-10-25。保留现有文件名和命令，方便继续学习；文件多不代表做同一件事。
从项目根目录执行 `.\.conda\python.exe -X utf8 scripts/文件名.py ...`。不要批量运行所有脚本：有些会联网、计费或改数据。

## 查看与本地实验

| 入口 | 用途 | 费用和写入范围 |
| --- | --- | --- |
| `lab.py` | 本机工作台：查看已有报告，或由用户启动新实验 | 查看免费；新实验成本随模式变化；只监听 127.0.0.1:8010 |
| `evaluate_mem_gallery.py` | 完整历史入库，再测试选择的问题；输出 JSON/Markdown/图片资源与断点 | 按配置可能调用付费模型；报告和检查点在 data/evaluations/mem-gallery |
| `compare_rrf.py` | 在完成的检查点上只读复用查询向量与媒体，对比预设 RRF 权重 | 不读 .env、不调用模型；另外保存对照报告，不覆盖原报告 |
| `evaluate.py` | 最初的极小合成样本，用来理解三条检索路线 | 使用本地 Ollama 和临时数据库；不是正式数据集评测的替代品 |

工作台：

```powershell
.\.conda\python.exe -X utf8 scripts/lab.py
```

打开 http://127.0.0.1:8010/。修改后端后重启，必要时 Ctrl+Shift+R 刷新；仅有检查点不等于已经产生完整报告。工作台的新实验图像入口使用本地 OCR，配置视觉 API 的图文实验按评测 CLI 参数明确启动。

## 模型、图片与数据准备

| 入口 | 用途 | 费用和写入范围 |
| --- | --- | --- |
| `embedding_smoke.py` | 查 embedding 配置或用极小样本测试 Add/Search | `--check-only` 不联网；实际远程测试需 `--allow-remote`，可能收费 |
| `inspect_image.py` | 检查一张图的描述/文字与检索链路 | 按当前配置调用视觉/embedding；远程需确认；只用临时库，保存单图报告 |
| `ocr_smoke.py` | 三张合成文字图的真实本地 OCR 与关键词召回 | 需要 Tesseract；保存 OCR 报告，不是复杂照片准确率证明 |
| `network_probe.py` | 检查视觉服务的连接路径 | 联网但不发 Key、图片或模型请求；不改代理，不证明推理可用 |
| `download_mem_gallery_images.py` | 下载该场景历史图及提问图，校验后保存原图 | 公开数据下载；不是模型请求；不覆盖已有原图 |
| `backfill_images.py` | 给已有指定用户的图片补分析并更新派生索引 | 默认预览；`--apply` 改真实数据，按配置可能收费，需停服/备份；不是全量 embedding 迁移工具 |
| `enable_ocr.ps1` | 启用本机 OCR 的 PowerShell 会话配置 | 只设置当前会话，不等于安装 OCR 或改写 .env |
| `enable_competition.ps1` | 设置参赛模式和相应会话配置 | 先阅读脚本及示例配置；不是租机、公网部署或官方资格确认 |

`--limit` 减少的是题目数，不是历史索引量。不要因为只测几题就以为不会重新处理历史图片或向量。

## HTTP 与安装包验收：三个 smoke 不是重复

| 入口 | 对象 | 为什么保留 |
| --- | --- | --- |
| `smoke.py` | 已启动的目标服务 | 验证实际配置的 HTTP 链路；会留下随机测试用户；启用模型时需 `--allow-model-calls`，可能收费；不是官方 Smoke |
| `deployment_smoke.py` | 自己创建的临时本机服务，模型关闭 | 测试真正的 HTTP、鉴权、图文预算及重启持久化；零模型调用，不碰已有服务数据库 |
| `package_smoke.py` | 构建并安装后的 wheel | 复用上面的 HTTP 验收，额外检查打包/安装；零模型调用，依赖复用本机，不能证明 Linux/Docker 已验收 |
| `docker_smoke.py` | 已构建的 Linux 镜像 | 合成数据验证非 root、HTTP、媒体、容器重建同卷持久化和配置边界；不读 .env、不调用模型，仅删除带自己归属标签的临时资源 |
| `linux_tests.py` | 开发测试镜像与只读脱敏源码快照 | 关闭外网跑完整 pytest；不挂载真实 .env/data；测试镜像和生产镜像分开，见部署验收清单 |
| `demo.py` | 开发时已启动的服务 | 最初的故事演示，固定 demo-local 用户；不是效果评测，目标服务启用模型时可能调用模型 |

`package_smoke` 调用 `deployment_smoke` 是组合复用，并不是维护两份相同的 HTTP 测试。
`deployment_smoke` 和 `docker_smoke` 共用 `_http_checks.py` 中的同一组 HTTP 断言，只替换进程启动/容器重建方式；这个下划线文件是内部帮助模块，不单独执行。

## 数据治理与发布

| 入口 | 用途 | 安全边界 |
| --- | --- | --- |
| `retention.py` | 正式任务的登记、完成日期、清理预览、检查和显式删除 | 只处理预登记的 official/run-id；apply 有真实删除风险，按 [流程](../docs/DATA_RETENTION.md) 操作，不能当普通 smoke 运行 |
| `release_check.py` | 候选文件/本地 Git 历史、版本和私有路径检查 | 只读检查；报告存忽略目录；不自动 Commit、push、改历史或删除密钥，见 [流程](../docs/RELEASING.md) |

## 代码应该放哪里

操作入口留在 scripts，复用的计分、报告、媒体和断点能力放在 `src/memory_app/evaluation/`。新增工具先判断它属于哪类，不另起一份近似功能的脚本。
核心业务源码不导入 scripts。原评测脚本保留计分函数的导入别名，仅供兼容，唯一实现已经在 evaluation 包里。更完整的依赖关系见 [当前结构](../docs/ARCHITECTURE.md)。
