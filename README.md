# 多模态记忆 Demo v0.3.0

发布与固定版本：[版本流程](docs/RELEASING.md)；正式评测数据：[保留与删除流程](docs/DATA_RETENTION.md)；方法与引用：[说明](docs/METHOD.md)。
许可证为 MIT。数据集、外部模型与依赖保持各自许可，不随本项目代码一并获得授权。
v0.3.0 包含“图片对话背景”索引及治理工具；新提示词仅通过离线测试，尚未完成真实付费效果对照，不宣称分数提升或官方验收通过。

部署准备入口：[部署与验收指南](deploy/README.md)。新增零模型费用的真实 HTTP/重启测试
`scripts/deployment_smoke.py` 和离线 wheel 安装验收 `scripts/package_smoke.py`。
`scripts/smoke.py` 对启用模型的目标服务要求显式 `--allow-model-calls`，服务密钥支持从指定 `.env` 读取。
API 增加请求体上限、并发保护和接收超时，均明确返回错误、不截断输入；competition 模式关闭交互文档。

按功能组织的 Python Add/Search 服务。SQLite 保存原始证据与向量，本地目录保存原图；使用 Ollama `nomic-embed-text:latest` 做文本向量检索，加中文字符/双字词与英文词的 BM25 召回，再以 RRF 融合。

V0.2 新增可选的“图片 → 描述／可见文字 → 文本检索”：可配置远程视觉 API、本地 Tesseract OCR，或两者组合。保存派生结果及来源，不覆盖原始消息；文字可命中图片描述，再返回原图。图片查询保留完全相同图片的哈希匹配，并可生成临时描述参与检索。没有直接图片 embedding。

默认关闭图片分析，仍可运行原来的文本／图片哈希 baseline，不产生视觉 API 费用。2026-10-13 已完成本机 OCR 安装及三张清晰文字图的真实检索检查；复杂照片的 OCR 效果与远程视觉仍待验证。自动回归测试的受控结果不能证明识图准确率。

2026-10-15：远程图片理解已改用官方 `openai` Python SDK，仍读取 `VISION_BASE_URL`、`VISION_API_KEY`、`VISION_MODEL`，通过 `MEMORY_VISION=compatible` 启用。使用独立客户端、90 秒超时、关闭自动重试；不需要修改 Codex 配置，不需要示例里的 `x-foo` 请求头。更换 SDK 不会解决账户额度不足。参赛模型配置、运行步骤与本次测试记录见 [10 月 15 日学习笔记](./笔记/2026-10-15-学习笔记.md)。

## 安装（PowerShell / Conda）

在项目根目录执行：

```powershell
conda create --prefix .\.conda python=3.11 pip -y
conda activate .\.conda
python -m pip install -r requirements-lock.txt
python -m pip install -e . --no-deps
```

当前工作区已创建环境时，不必重复创建。未初始化 Conda 的终端可以直接使用 `.\.conda\python.exe`。

`requirements-lock.txt` 记录本次验证环境的依赖版本；更新依赖时可使用 `python -m pip install -e '.[dev]'`，验证后再更新版本记录。

## 启动

先启动已有 Ollama，确保模型标签为 `nomic-embed-text:latest`。默认地址是 `http://localhost:11434`。

```powershell
.\.conda\python.exe -m uvicorn memory_app.main:create_app --factory --host 127.0.0.1 --port 8000
```

交互接口文档：<http://127.0.0.1:8000/docs>。默认只绑定本机，未设置鉴权；停止服务按 Ctrl+C。Health 检查服务，不代表 Ollama 实时可用；默认启动时会检查模型存在。

离线词法模式：

```powershell
$env:MEMORY_EMBEDDINGS = 'none'
.\.conda\python.exe -m uvicorn memory_app.main:create_app --factory --host 127.0.0.1 --port 8000
```

运行配置见 `.env.example`。从项目根目录启动时，API 和调用 `Settings.from_env()` 的脚本会自动读取根目录 `.env`；示例文件 `.env.example` 不会自动加载。优先级：终端环境变量 > `.env` > 代码默认值。文件内容不会写入进程的全局环境变量；不会向上搜索其他目录，不展开 `${...}`。切换 embedding 模型需要重建向量；词法索引仍可用。向量版本包含模型与预处理版本，避免混用不同模型。

本地图片测试可在 `.env` 填写以下内容（真实密钥只填本地 `.env`，不要填示例文件）：

```dotenv
MEMORY_PROFILE=local
MEMORY_EMBEDDINGS=none
MEMORY_OCR=none
MEMORY_VISION=compatible
VISION_BASE_URL=https://novaapi.ai/v1
VISION_MODEL=gpt-4o-mini
VISION_API_KEY=在本机填写新密钥
```

`.env` 和 `.env.*` 私有变体已加入 Git 忽略，保留 `.env.example` 和 `.env.competition.example` 两个可提交的示例。模型名与地址并不保密，密钥才是秘密；更换服务商要同时核对地址、密钥和准确模型 ID。此示例只是配置方法，不代表对服务商的推荐、图片能力或参赛合规认证。

修改 `.env` 后重新运行脚本或重启服务。如果之前用 `$env:VISION_API_KEY=...` 等设置过旧值，建议打开一个新的终端再从项目目录执行，或用 `Remove-Item Env:VISION_API_KEY -ErrorAction SilentlyContinue` 清除相应的进程变量。即使环境变量为空，它仍然优先于文件。不要打印完整 Settings 或配置文件来检查密钥；也不需要把密钥贴到聊天中。

## 一次文本写入与查询

### 先接通 text-embedding-v4

阿里云的 DashScope SDK 和 OpenAI 兼容 HTTP 接口是两种调用方式，本项目使用后者，不需要额外安装 `dashscope`。两者都可指定 `model=text-embedding-v4` 和输入文字，但地址与响应格式不能混用。请从阿里云控制台复制所选地域/业务空间的 **OpenAI 兼容 base URL**（通常以 `/compatible-mode/v1` 结尾，不带 `/embeddings`），不要把原生 `/api/v1` 地址直接填入。

在本地 `.env` 修改/填写：

```dotenv
MEMORY_EMBEDDINGS=compatible
EMBEDDING_BASE_URL=从阿里云控制台复制实际兼容接口基础地址
EMBEDDING_API_KEY=在本机填写阿里云对应地域的密钥
EMBEDDING_MODEL=text-embedding-v4
EMBEDDING_DIMENSIONS=1024
EMBEDDING_REVISION=1
```

识图仍使用单独的 `VISION_*` 配置。默认不复用识图中转站的地址或密钥，也不要求 Ollama 下载 text-embedding-v4。

先检查配置（只显示有没有密钥，不发送请求），再明确同意小额远程调用：

```powershell
.\.conda\python.exe -X utf8 scripts/embedding_smoke.py --check-only
.\.conda\python.exe -X utf8 scripts/embedding_smoke.py --allow-remote
```

小测试用临时数据库调用真实 Add/Search：一次向量化 3 条测试文字，一次向量化 1 个查询，正常共 2 次 API 请求，不调用图片理解。输出维度、纯向量/融合排序与用户隔离结果，报告保存到 `data/evaluations/embeddings/`。查询时向量失败会判为测试失败，不把 BM25 降级伪装为向量成功。这个小测试不是准确率评估；报告暂为独立 JSON，不在工作台报告列表中展示。

工作台已新增远程 text-embedding-v4 路线，必须勾选费用确认；它仍始终索引所选场景的全部历史，减少问题数不会减少历史索引费用。查看报告不调用模型，工作台图片按钮仍明确使用本地 OCR，不会因为 `.env` 已启用视觉 API 就静默发送图片。重启工作台进程并刷新页面后才能加载新入口。

正式切换时使用新数据目录并重新导入，旧 nomic 向量不能直接转换或混用；同 request_id 重放不会自动重建已有向量。小测试本身不会改旧数据库。

参考：[阿里云文本向量同步 API](https://www.alibabacloud.com/help/zh/model-studio/text-embedding-synchronous-api)。

```powershell
$body = @{
  request_id = 'demo-001'; user_id = 'alice'; session_id = 'session-1'
  messages = @(@{role = 'user'; content = '我喜欢蓝色机械键盘'})
} | ConvertTo-Json -Depth 8
Invoke-RestMethod 'http://127.0.0.1:8000/add' -Method Post -ContentType 'application/json; charset=utf-8' -Body ([System.Text.Encoding]::UTF8.GetBytes($body))
$query = @{query = '我喜欢什么键盘？'; user_id = 'alice'; top_k = 3} | ConvertTo-Json
Invoke-RestMethod 'http://127.0.0.1:8000/search' -Method Post -ContentType 'application/json; charset=utf-8' -Body ([System.Text.Encoding]::UTF8.GetBytes($query))
```

图文内容使用官方有序数组：

```json
[{"type":"text","text":"3号机床出现红色告警灯"},
 {"type":"image_url","image_url":{"url":"data:image/png;base64,..."}}]
```

## 验证

### 超大图片：保留原图，显式生成本地测试副本

Architecture 场景有两张源图片超过 10 MiB。下载原图与接口接收限制分开处理：

```powershell
$scene = 'data/benchmarks/mem-gallery/data/dialog/Architecture_Art_Culture_Exhibition_Technology.json'
.\.conda\python.exe -X utf8 scripts/download_mem_gallery_images.py --data $scene --allow-large-originals --workers 1
.\.conda\python.exe -u -X utf8 scripts/evaluate_mem_gallery.py --data $scene --embeddings compatible --with-images --image-analysis configured --compress-oversize --limit 0 --top-k 5
```

下载器默认仍限制 10 MiB，`--allow-large-originals` 仅允许保存最多 64 MiB 的数据集源文件；
校验上游 SHA-256 和图片格式后原子发布，不覆盖已有文件。网络中断和部分瞬时 HTTP 错误最多尝试 3 次；
只重试公开数据下载，不重试模型请求。可通过 `--endpoint https://huggingface.co` 切换下载源。

`--compress-oversize` 是显式本地评测选项：小图保持字节不变，大图在内存中生成 JPEG 副本后保存到独立报告 assets，
不覆盖数据集原图。优先保留像素尺寸，依次降低 JPEG 质量；仍过大才按固定比例缩小。透明区域铺白，应用 EXIF 方向；
拒绝多帧超大图、超过 6000 万像素或超过源文件上限的图片，不取消安全限制。
历史图和提问图使用同一规则。报告记录处理策略/Pillow 版本、源与输出哈希、字节数、尺寸和 JPEG 质量。
工作台标明压缩副本；接口本身仍保持单图 10 MiB、单次媒体 30 MiB 限制。

这不是官方冻结 Adapter 的等价实现，不能把有损处理后的数据说成原图完全不变。
正式参赛时需核对官方预处理方式。该策略进入检查点身份；压缩实验续跑须保留 `--compress-oversize`，
不能混用未压缩实验的检查点，也不应在续跑中途升级 Pillow。
工作台目前可查看压缩报告；发起这类实验请用上面的 CLI，不是工作台的默认 OCR 任务。

### 离线 RRF 权重对比

完整图文报告生成后，可只读复用同一次检查点的历史向量、查询向量缓存和图片理解结果：

```powershell
.\.conda\python.exe -X utf8 scripts/compare_rrf.py `
  --report data/evaluations/mem-gallery/20261025T021732Z-0d2107a9.json
```

不读取 `.env`、不建立模型客户端、不发网络请求、不重建历史索引，不修改源报告或源检查点。
缺少缓存就停止，不自动调用模型补齐。需要保留原始数据、报告图片副本与已完成检查点，
并先核对全部题目的三条基线前 K 个 ID 与原报告一致；不一致则拒绝输出对比结果。
完整深度的候选由只读数据库与查询缓存重新计算，不是简单混合原报告的前五条。

新实验包含关键词、纯向量、等权 RRF、关键词:向量 `0.25:1` 与 `0.5:1`。
RRF 常数仍为 60；图片字节身份通道权重固定 1，纯向量基线不使用这个通道。
生成独立 JSON/Markdown，保留候选池、逐题指标变化、参考与召回图片。输出与源报告位于同一目录，工作台刷新报告列表即可查看。
离线通道时间的 0 是未测量占位值，不是延迟实验；同场景调参不等于验证集结果，也不自动修改正式 Search。

启动工作台：`.\.conda\python.exe -X utf8 scripts/lab.py`，打开 http://127.0.0.1:8010/。
前端代码更新后需重启工作台并按 Ctrl+Shift+R 刷新页面。

### 错误诊断与续跑说明

网络错误现在附带安全的 `diagnostic` 类别（例如 `dns_failure`、`tls_certificate`、
`connection_reset`、`remote_protocol`、`read_timeout`）。仅暴露静态分类与建议，不输出原始异常、密钥或响应正文。
底层原因不可识别时仍显示 `unknown`，不自动重试或关闭证书验证。存储错误也会尽可能区分权限、磁盘和数据库占用。

```powershell
.\.conda\python.exe -X utf8 scripts/network_probe.py
```

该脚本不带 API Key，不发图片或模型参数，只探测视觉接口能否返回 HTTP 响应，分别使用
`trust_env=False/True`。401 表示这次无密钥探测收到了鉴权拒绝，不代表真实图片模型请求已成功。
业务客户端仍忽略环境变量代理；探测不会修改 `.env`、检查点或系统代理。

2026-10-24 查询附图修复：`--with-images` 现在同时读取历史 `input_image` 和题目 `question_image`。
历史图经 Add 建立记忆；提问图经相同图片理解工具生成临时检索文字，只用于 Search，不通过 Add 写入历史。
工作台会分别展示提问附图、应该召回的历史图片、实际召回图片，并可展开实际检索文字。
纯文本模式会明确标注有附图却未读取的题目；旧报告不补算、不覆盖，旧指标不代表完整图文查询表现。

先补下载题目图片（免费公开数据下载，已有文件校验后保留）：

```powershell
.\.conda\python.exe -X utf8 scripts/download_mem_gallery_images.py `
  --data data/benchmarks/mem-gallery/data/dialog/AI_Robotics_Automation_Future_Tech.json
```

随后使用原来的完整评测命令新建实验，不要追加旧版 `--resume`。本次加入了 `query-images-v1` 协议标识，
不会混用修复前遗漏题图的检查点结果；新实验会重新索引，远程模型可能再次收费。新版本产生的检查点仍支持同参数续跑。
`--image-analysis configured` 使用 `.env` 配置的图片理解工具，可能调用付费视觉模型；
`--image-analysis ocr` 仅使用本地 OCR，无字图片可能提取不到有效线索。工作台启动新任务仍使用本地 OCR；查看 CLI 生成的报告不调用模型。
查询图片分析耗时单独记录，不计入各检索路线耗时。检查点可以缓存查询的模型结果以减少续跑重复请求，
但这些缓存不会成为 SQL 中可被召回的历史记忆。

`memory/errors.py` 定义业务异常；`errors.py` 把异常转换为统一、安全的反馈，不负责自动重试。API 仍用 HTTP 状态表示成功/失败，错误正文含 `detail` 和结构化 `error`。`embedding_smoke.py`、`inspect_image.py`、Mem-Gallery CLI 均输出安全的简短错误，不打印完整 traceback 或上游正文。

常见 `error.code`：`timeout`（超时）、`network_error`（连接/TLS 等网络错误）、`http_error`（服务商 HTTP 错误）、`invalid_response`（向量数量/index/维度/数值等不合规）、`invalid_json`、`invalid_schema`、`refused`、`checkpoint_error`。反馈含 component、HTTP 状态、白名单服务商代码、处理建议与 retryable；retryable 只表示可能人工恢复，**不表示会自动重试，也不承诺超时请求未计费**。

Mem-Gallery CLI 从本次版本起自动创建独立检查点，并在调用模型前打印 `Checkpoint:` 目录，例如：

```text
data/evaluations/mem-gallery/_checkpoints/时间戳-随机ID/
  state.json       最后阶段、当前轮次/问题、完成数量与安全错误
  memory.sqlite3   已成功 Add 的原始记忆与向量
  cache.sqlite3    已校验的模型结果、已完成的逐题结果
  media/           已保存原图
```

每个成功 Add 都持久保存；图片分析成功后立即缓存，后面的向量失败也不会丢掉它。向量按输入、query/document 角色及模型版本缓存，每个成功批次保存；问题结果逐题保存。失败不会导出不完整的正常成绩报告，不会自动重跑。没有 checkpoint 参数的直接 `evaluate()` 调用仍使用临时目录，方便隔离单元测试。

续跑必须保留首次相同参数，并追加 `--resume`（填终端实际打印的检查点目录，不是 state.json 文件）：

```powershell
.\.conda\python.exe -u -X utf8 scripts/evaluate_mem_gallery.py `
  --data data/benchmarks/mem-gallery/data/dialog/AI_Robotics_Automation_Future_Tech.json `
  --embeddings compatible --with-images --image-analysis configured --limit 0 --top-k 5 `
  --resume 'data/evaluations/mem-gallery/_checkpoints/替换成实际目录名'
```

以上只是续跑命令示例，真实执行仍可能产生未完成步骤的费用。更换数据、图片、所选问题、K、模型/版本/预处理、视觉输出协议等会拒绝复用；仅更换相同服务的密钥不影响缓存身份。进程锁防止两个进程同时使用一个检查点。强制杀进程后状态可能仍显示 running，但 OS 锁会释放，确认原进程停止后才续跑。

重启工作台后，可在“进度、错误与断点”区域刷新，或只读访问 `/api/checkpoints`。当前通过 CLI 续跑；不要把工作台“开始评测”当作续跑，它会新建任务。续跑报告回到原输出目录，工作台刷新报告列表即可查看。索引耗时仅表示当前尝试，复用的逐题结果保留原测量耗时。

检查点不是零费用/恰好一次执行保证：响应丢失、结果尚未落盘的窗口仍可能在手动续跑时重复请求。服务商内部身份或模型变更也可能不可见，必要时增加模型 revision 并新建实验。旧版失败任务的临时目录已经退出，没有可恢复的模型结果；不会删除其原有报告或图片副本。

检查点含数据和派生结果，不能提交 Git 或公开提供下载；若处理官方评测资料，也要把检查点、媒体和报告副本纳入官方的数据清理时限。

```powershell
.\.conda\python.exe -m pytest -q
# 在另一个终端、服务运行时执行真实 HTTP smoke（也调用本机 Ollama）
.\.conda\python.exe scripts\smoke.py
# 写入三条记忆，展示三个问题找回的原始证据
.\.conda\python.exe scripts\demo.py
# 独立小评测：真实 Ollama、词法/向量/融合对比，使用临时数据
.\.conda\python.exe -X utf8 scripts\evaluate.py
```

测试覆盖持久化重启、用户隔离、同请求重试/冲突、图文顺序与原图恢复、非法输入、top_k、鉴权、并发重试，以及向量故障不提交和查询降级。测试采用受控向量提供者；HTTP smoke 验证实际启动配置。Smoke 使用独立用户并留下少量演示数据。

2026-10-02 扩展检查与修复后共 29 项通过，增加数据库回滚重试、丢图恢复、JPEG/WebP、Ollama 异常返回、长消息尾部与模型版本隔离。原图缺失时 Search 返回明确 503，相同 Add 重试可补回缺失原图。详细发现与真实检索小评测见[学习与修复笔记](笔记/2026-10-02-学习笔记.md#note-09)。

## 文件职责

### V0.2 图片能力怎么启用

API 的 Add/Search 请求与返回格式不变。`/health` 新增 `image_analysis` 开关状态。凭据可通过环境变量或本地 `.env` 设置；`.env.example` 不会自动加载。

远程图片请求默认使用 `VISION_RESPONSE_FORMAT=json_schema`：向服务商发送 strict JSON Schema，要求恰好 `description`、`visible_text` 两个字符串字段。收到后仍校验字段、类型、长度及非空证据，拒绝 Markdown 包裹、截断、拒答或不合规结果，不自动修补或重试。格式及 Schema 纳入缓存版本，避免沿用旧协议的分析缓存。若服务商明确只支持 JSON 模式，可在 `.env` 显式设置 `VISION_RESPONSE_FORMAT=json_object`；此模式只约束 JSON，字段规则仍由本地校验，不会自动降级或重复付费请求。错误诊断只显示输出长度、是否代码块等形态，不记录原始输出正文。

远程视觉模式（服务须支持 Chat Completions 的 Base64 image_url 输入）：

```powershell
$env:MEMORY_VISION = 'compatible'
$env:VISION_BASE_URL = 'https://你的服务地址/v1' # 改成服务文档中的实际 base URL
$env:VISION_MODEL = '你的视觉模型名'
# 在本机安全设置 VISION_API_KEY；不要把真实密钥提交到 Git 或发到聊天中。
# 然后按前面的 uvicorn 命令启动服务。
```

也可以只启用本地 OCR：需安装 Tesseract 和所需语言包，然后设置 `MEMORY_OCR=tesseract`、`TESSERACT_EXECUTABLE`（命令名或 exe 完整路径）、`OCR_LANGUAGES`。它只能读文字，不能描述物体、颜色。未安装时会明确报错，不自动下载。

2026-10-13 本工作区已在 `.conda` 安装 Tesseract 5.5.1 与中英文语言数据。直接在将要启动服务的 PowerShell 执行：

```powershell
. .\scripts\enable_ocr.ps1
# 当前会话启用中文 OCR，明确语言目录，关闭远程视觉；不修改系统配置
# 想先只用 OCR + BM25、不依赖 Ollama，可以再设置：
$env:MEMORY_EMBEDDINGS = 'none'
# 然后按上文的 uvicorn 命令启动；已运行服务需要重启才读取新配置

# 独立的真实 OCR + 检索检查（不需要启动服务，生成三张图片和报告）：
.\.conda\python.exe -X utf8 scripts\ocr_smoke.py
```

初步对照中 chi_sim+eng 把“冷却水泵”误读成英文字母，chi_sim 正确读出中文及 M42，因此当前启动脚本默认 chi_sim，英文数据仍已安装；纯英文可另设 OCR_LANGUAGES=eng。这不是所有图片的最佳配置，需实际对照。真实小样本和修复记录见[10 月 13 日笔记](笔记/2026-10-13-学习笔记.md)。

启用远程视觉会把图片发给配置的服务，可能收费。模型输出必须包含 description、visible_text 两个字符串字段；截断、空描述、非法 JSON、限流、超时均视为失败。无字图片得到空 OCR 是正常成功。Add 分析失败返回 503，不提交消息；Search 分析失败降级为已有文本／图片哈希通道。

```powershell
# 单图检查：使用当前环境配置；临时入库，结果保存在 data/evaluations/images
.\.conda\python.exe scripts\inspect_image.py --image .\你的图片.png --query '图片中的型号是什么'
# 如果启用了远程视觉，需要额外明确允许上传：末尾添加 --allow-remote

# 旧图片不会因重复同一次 Add 自动补分析。先预览某用户的旧图片消息数（无模型调用）：
.\.conda\python.exe scripts\backfill_images.py --user-id alice
# 停止 API 写入、备份数据库与媒体后，按当前配置补处理：
.\.conda\python.exe scripts\backfill_images.py --user-id alice --apply
# 远程模式同样需添加 --allow-remote
```

单图脚本只用于检查提取内容和检索链路：只有一个候选时，向量检索可能照样返回它，不能把“搜到了”当成模型准确。报告不包含 Base64 原图，但包含派生文本，应注意隐私。补处理按消息提交，失败后之前成功的消息保留，可重试；不会重建原始文字向量，更换 embedding 的全量重建仍待实现。

旧数据库在服务首次启动时自动迁移向量表，增加 `source` 字段，旧向量保留为 `text`；原消息缺少 image_artifacts 时按空列表读取。建议升级前先停止写入并备份。详细设计与调用图见[当天笔记的 V0.2 章节](<笔记/2026-10-10-学习笔记Mem-Gallery 本地检索诊断.md#v02-图片能力迭代>)。

### scripts 工具

本地网页实验台：运行 `.\.conda\python.exe scripts\lab.py`，打开 <http://127.0.0.1:8010>。可选历史报告、逐题查看原文和三路排名、筛选漏检题，也可启动 Mem-Gallery 评测并查看日志。默认仅 BM25，可切换本地 Ollama。一次只运行一个实验，不需要另开竞赛服务；按 Ctrl+C 停止。此入口仅绑定本机，不要暴露公网。

| 文件 | 用途 | 需要启动 FastAPI？ | 数据去向 |
| --- | --- | --- | --- |
| `smoke.py` | 实际 HTTP 链路检查：写入、重试、图文还原、用户隔离；不是官方 Smoke | 是 | 服务数据库，随机用户 |
| `demo.py` | 写入三条故事，展示三个查询结果 | 是 | 服务数据库，固定 demo-local 用户 |
| `evaluate.py` | 自建极小样本，比较 BM25、向量、RRF | 否；需要 Ollama | 临时数据库 |
| `evaluate_mem_gallery.py` | Mem-Gallery 逐题检索诊断，输出 Markdown/JSON | 否；默认需要 Ollama | 临时数据库；报告在 data/evaluations/mem-gallery/ |
| `lab.py` | 本地网页查看报告、逐题对照、启动 Mem-Gallery 评测 | 自己启动独立实验台 | 读取历史报告；新实验使用临时数据库 |
| `inspect_image.py` | 单张真实图片的描述／OCR 与文字搜索检查 | 否；使用已配置的分析器与 embedding | 临时数据库；JSON 在 data/evaluations/images/ |
| `backfill_images.py` | 指定用户旧图片补分析；默认只预览 | 否；应用前先停止 API 写入 | 更新 MEMORY_DATA_DIR 中已有数据库 |
| `ocr_smoke.py` | 绘制三张文字图，真实 OCR + BM25 找回原图，保存报告 | 否；需要本地 Tesseract | 临时数据库；图片与报告在 data/evaluations/ocr/ |
| `enable_ocr.ps1` | 在当前 PowerShell 启用项目本地 OCR、关闭远程视觉 | 否 | 只设置当前会话环境变量 |
| `download_mem_gallery_images.py` | 补下载一个场景对话实际引用的原图，校验 LFS SHA-256 | 否 | data/benchmarks/mem-gallery/data/image/ |

```powershell
# 全部历史入库，先测前 10 题；不加入图片或图片描述
.\.conda\python.exe scripts\evaluate_mem_gallery.py
# 不调用模型，只检查 BM25 和数据适配
.\.conda\python.exe scripts\evaluate_mem_gallery.py --embeddings none
# 测试该场景全部题目
.\.conda\python.exe scripts\evaluate_mem_gallery.py --limit 0
# 单独比较借助数据集已有图片描述的效果
.\.conda\python.exe scripts\evaluate_mem_gallery.py --include-captions
# 下载默认场景原图，然后测试完整真实图文；不使用数据集自带描述
.\.conda\python.exe -X utf8 scripts\download_mem_gallery_images.py
.\.conda\python.exe -u -X utf8 scripts\evaluate_mem_gallery.py --with-images --ocr-languages eng --limit 0
```

工作台勾选“真实图片 + 本地 OCR”、题数设 0，可运行完整单场景。新报告逐题显示参考图片与实际召回图，并单独统计图片 Hit/Recall；旧报告没有原图引用，不能自动补出图片。参考图优先取答案中的显式图片 ID，否则取 clue 轮次图片并注明宽松证据含义。图片按轮次附到 assistant 是当前适配假设，写进 metadata。原图模式运行实际 Add、OCR、检索和媒体预算恢复，不上传至远程视觉服务。详见[10 月 14 日记录](笔记/2026-10-14-学习笔记.md)。

用 `--data 路径` 更换场景、`--top-k 5` 设置返回消息数。默认只选原始顺序前 10 题，不是代表性随机样本；无 clue 题不计轮次分。默认不读取原图；开启 --with-images 后真实处理图片并应用媒体输出预算。脚本不模拟时间截止、不生成答案、不经过 HTTP。本地诊断不是官方比赛评分。原指标与工具设计见[10 月 10 日笔记](<笔记/2026-10-10-学习笔记Mem-Gallery 本地检索诊断.md>)，真实图文扩展见 10 月 14 日笔记。

### 应用模块

当前版本详细导读见 [V0.2 离线源码 Wiki（HTML）](笔记/2026-10-11-V0.2-源码导读/index.html)：17 章解释、系统架构图、Add/Search 调用图、图片关联、检索与异常策略。可直接用浏览器打开，或按[文档说明](笔记/2026-10-11-V0.2-源码导读/README.md)启动独立本机 HTTP，不需要模型服务。

详细学习文档与调用图见[2026-10-02 V0 源码导读](笔记/2026-10-02-学习笔记.md#note-10)，已合并到当天学习笔记中，保留导航与七个源码章节，按实际实现定位源码。

`main.py` 负责装配；`api/` 负责 HTTP；`memory/service.py` 对外提供业务入口，`add.py`、`search.py` 协调流程。`storage/` 保存证据及幂等状态，`media/` 保存原图，`embeddings/` 生成向量，`vector_stores/` 查询向量，`retrieval/` 做 BM25 与 RRF。V0.2 的 `vision/` 负责视觉任务、输出校验、OCR 与组合；`llms/` 负责远程视觉 HTTP 调用。`memory/images.py` 关联来源，`memory/indexing.py` 组织原文和派生文字的检索来源。

详见[架构计划](笔记/2026-10-02-学习笔记.md#note-06)。本实现独立编写，功能组织参考 [Mem0](https://github.com/mem0ai/mem0)，未复制其源码。接口参考 [AML 规范](https://agentmemories.ai/rules)，embedding 调用参考 [Ollama 文档](https://docs.ollama.com/api/embed)。

## 已知边界与后续

- 检索在单用户数据中线性扫描，适用于小型本地 Demo，尚未验证 Full 评测容量或中文准确率。向量检索返回相似候选，不等同于可靠的无证据判定。
- Add 默认要求文本向量生成成功才提交，Search 模型故障时降级词法与图片哈希召回。每条消息一个单元；文本按 400 字符分块建立向量，返回完整原消息。
- 单图 10 MiB，Add/返回图像合计 30 MiB；top_k 支持 0–100。暂未提供整体 HTTP 请求体限额或速率限制，服务限本地使用。
- 原始数据与向量同 SQLite 事务提交；图片在提交前按用户范围的哈希路径原子保存。数据库提交失败可能留下未引用图片，需要后续清理机制，不会形成可查询的半成品。并发重复请求可能重复计算向量，但数据库不会重复写入。
- 配置与凭据不进入 Git。尚未实现按用户删除、TTL、Docker、公网部署或官方 smoke；正式提交前补齐，并核对当期模型限制。数据目录仅用于当前本地 demo。
