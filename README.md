# 映流 · YingFlow

Windows 本地视频创作 App，把**每日选题 → 文稿与审核 → 分镜 → 素材与配音 → MP4 成片**串在同一个工作台里。重点是完整、可保存恢复的创作流程，而不是让用户手工搬运文件、建立目录或逐项操作 01—09 技术节点。

当前版本 **0.3.0**，采用 Python 3.12、PySide6 和 SQLite，已制作 Windows x64 免安装开发版。

现有程序、构建产物和数据目录暂时保留 `VideoWorkbench`／`VideoMaker` 名称，避免影响已安装版本及历史作品。

## 直接使用 App

使用已制作的 Windows 免安装 ZIP 时，**完整解压后双击 `VideoWorkbench.exe`**。主程序、`VideoWorkbenchWorker.exe` 与 `_internal/` 必须放在一起；不要只复制 EXE，也不要在压缩包内运行。免安装版无需另装 Python、Qt 或 FFmpeg。

源码仓库不包含 `dist/` 安装包；从源码制作免安装版见下文「Windows 打包」。已有打包版的使用说明见 [免安装版说明](docs/portable-app.md)。升级前先保存并关闭旧窗口，再打开新版。

### 从选题到视频

1. **配置连接**：在「设置」检测本机 CLI，通过官方方式登录；打开「Gemini 连接助手」，在 App 专用 Chrome 窗口登录并检查连接。
2. **选择选题**：「每日推荐」展示三项内容，包含标题、切入角度、推荐理由和内容要点。点击「用这个选题」，App 自动建立作品和日期子目录，连续准备初稿与审核稿。
3. **确认文稿与分镜**：在「创作工作台」修改、保存和确认；双击镜头可编辑完整方案。需要自己的画面时，在「素材」导入图片或视频，并点击「用于此镜头」。
4. **选择声音**：进入「设置 → 配音设置」或「成片 → 配音设置」，保存来源、音色与语速。在「视频设置」选择清晰度。
5. **生成视频**：进入「成片 → 生成视频」。完成后在 App 内预览、拖动进度，或打开成片目录取得 MP4、字幕与旁白。

已有确认文稿的作品可以继续制作，不用重新选题。手工创作入口是「我的作品 → 自己写一个选题」；文稿资料导出是可选中间产物，不是视频制作的终点。

每日推荐默认面向嵌入式技术讲解，参考时长约 **230 秒、4:3**；可在「推荐偏好」修改。同日打开使用缓存，「换一批推荐」才重新生成；App 保持打开跨日时切换新日期，关闭时不运行后台推荐任务。推荐基于领域生成，不冒充经过检索的当日新闻。

## 连接与账号配置

| 用途 | 当前入口 | 本机准备 |
| --- | --- | --- |
| 自动选题、初稿 | Antigravity CLI | 安装 CLI 并通过官方方式登录；设置中检测或选择程序路径 |
| 文稿审核 | Gemini 网页 | 安装 Chrome，在连接助手创建的专用窗口登录 |
| 可选 GPT 润色 | Codex CLI | 安装并官方登录；在「创作进度」勾选 GPT 润色 |
| 本地成片 | App 自带渲染与编码组件 | 配置所选语音来源，确认文稿和分镜 |

审核目标模式默认为 **4 Argon**，发送前会选择并核对；不可用时暂停，不退回默认 Flash。模式名称可在设置中修改。这里保存的是网页显示名称，不是 API 模型 ID。

连接助手会寻找本机 Chrome、建立 App 专用资料目录，并自动避让被占用端口。出现 `BROWSER_PORT_IN_USE` 时，在助手中切回自动端口；不要强行接管其他浏览器。专用窗口的登录资料保留在本机，换电脑后重新配置和登录，不复制 Cookie、密码或 CLI 登录文件。

## 配音与画面

「配音设置」保存后作为**后续生成的默认值**，重开 App 后保留，已有视频不变；成片页显示下次使用的声音和参数。

| 配音来源 | 可设置内容 | 使用条件 |
| --- | --- | --- |
| Windows 系统语音 | 已安装声音、语速 | 离线使用；缺少中文声音时点击「管理 Windows 语音」安装 |
| Edge 在线语音 | 音色、语速、音调 | 需要联网；内置云健 `+10% / -5 Hz`、云希 `+10% / 0 Hz` 预设 |
| Google Cloud | 音色、语速、配额项目 | 需要自己的 Cloud Text-to-Speech 服务、计费项目和官方 ADC 本机授权 |

Google 旧工程预设为 `cmn-CN-Wavenet-B`、**1.10 倍**语速。Cloud 授权与 Gemini 网页登录分开，入口为「如何配置 Google 语音」；每次实际生成前单独确认可能计费的请求。未迁入旧脚本密钥。

保存设置不发起语音请求；「试听历史音色」播放随 App 保存的旧样本，不为当前文稿生成新声音。来源失败时明确报错，不自动切回系统声音；Google 请求结果不确定时不自动重发，应先核对云端用量。

当前画面支持基础技术示意动画，以及明确应用到镜头的本地图片／视频：

- 技术动画包括开漏电路、RC 上升曲线、阻值取舍、采样和流程图，画面标注为原理示意。
- 图片和视频等比完整显示，不裁切；视频原声静音，短片延长末帧，长片截取前段。
- 仅导入素材或填写预览路径不会入片，须点击「用于此镜头」。应用会创建已确认的分镜新版本，旧版本和旧成片保留。
- 配音按句段生成，字幕使用实际语音片段长度；成片时长可能不同于分镜计划，原计划时长仍保留。
- 导入的音频目前只支持预览，尚未参与旁白对齐与合成。

## 源码运行

开发环境使用 **Windows x64、Python 3.12**。在工程根目录打开 PowerShell：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock.txt
.\.venv\Scripts\python.exe -m pip install --no-deps -e .
.\.venv\Scripts\python.exe -m video_app
```

安装后也可双击根目录的 `启动工作台.cmd`。不要直接复制另一台电脑的 `.venv/`。

指定独立数据目录：

```powershell
.\.venv\Scripts\python.exe -m video_app --data-root "E:\VideoMakerData"
```

通常在 App 内配置即可。开发时可将 `settings.example.json` 复制为已忽略的 `local.settings.json`，填写本机路径；也可用 `--settings 配置文件.json` 显式指定。App 已保存的显式配置优先；免安装版不会自动读取开发目录的 `local.settings.json`。

只检查程序路径和版本，不调用模型：

```powershell
.\.venv\Scripts\python.exe -m video_app preflight --output evidence/preflight.json
```

该检查不证明登录成功或生成可用；未配置可选 Codex 也会报告对应入口缺失。

### 工程结构

```text
src/video_app/
  __main__.py        桌面启动与路径检测
  config.py          本机配置和程序发现
  core/              项目、SQLite、版本、工作流、任务与成片调度
  providers/         Antigravity、Gemini 网页、Codex 适配器
  render/            语音、技术动画、素材读取与视频工作进程
  ui/                PySide6 界面、连接助手、预览和设置
  assets/speech/     历史音色试听样本
packaging/           Windows 桌面／工作进程入口和 PyInstaller 配置
tools/               打包、连接检查和定向验收工具
docs/                产品约定、参考画面与免安装版说明
requirements.lock.txt   固定的运行依赖
requirements-build.txt  独立的打包依赖
```

## 数据保存与恢复

默认数据目录为 `%LOCALAPPDATA%\VideoMaker`，侧栏「打开数据目录」可查看实际位置；作品不保存在 App 安装文件夹中。

```text
VideoMaker/
  app.db                         项目、配置、版本与任务状态
  jobs/                          版本、请求、原始输出与导入素材
  browser/                       App 专用浏览器资料
  runtimes/                      受控 CLI 工作目录
  daily/YYYY-MM-DD/
    recommendations.json         当日推荐
    推荐选题.md
    选题标题-创作ID/
      选题.json
      video-project-导出版本/    文稿、分镜与项目记录
      video-成片版本/
        video.mp4
        captions.srt
        narration.wav
        render.json
        timeline.json
```

日期按北京时间划分。重复选择同一推荐会打开已有作品；重新生成的推荐单独保留版本。

「保存新版本」保存草稿，「确认本阶段」才更新下游输入。改动已确认上游后，相关下游提示需更新；旧版本和旧成片不被覆盖。运行记录可查看错误、事件和尝试；取消或失败不把半成品标记为完成。

外部请求超时且结果不确定时进入 `UNKNOWN`。「核对未知结果」只读已有结果；明确确认重做后才再次发送，避免重复消耗额度。关闭后重开不会自动重发未知请求。

**备份前先退出 App，再复制整个数据目录，以及另行配置的专用浏览器目录。** 文稿／成片导出不等于完整备份；当前没有跨机器项目导入界面。登录资料只作本人本机保管，不纳入源码或分发包。

## Windows 打包

在上述 Windows x64 开发环境中，按需执行：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.\.venv\Scripts\python.exe tools/build_windows.py
```

输出为 `dist/VideoWorkbench-版本-win64-时间/` 及同名 ZIP，每次建立新目录，不覆盖旧包。包内含 Python／Qt、FFmpeg、Playwright 驱动、语音组件、历史试听样本和第三方许可文件；不包含开发者配置、作品、浏览器登录资料或凭据。Chrome 和所需 CLI 仍在目标电脑配置。

如需验收，运行 `tools/verify_windows_bundle.py` 并传入本次 ZIP 路径。它会解压到新的隔离目录、打开打包后的窗口，使用 Windows 本机语音实际生成视频并检查播放与导出；不调用模型或在线 TTS。**构建成功不等于运行验收通过。**

## 当前验证与边界

截至 **2026-10-19** 的开发机留存记录：

- 最终 Windows x64 ZIP 已在隔离目录解压，并在移除 Python、Node 和项目工具 PATH 后打开原生窗口。
- 打包版已验证声音选择与持久保存、历史试听、Playwright 驱动启动停止，以及独立视频工作进程。
- 使用 Windows 中文声音实际生成 **9.667 秒、960×720、4:3、30 fps、H.264/AAC** 混排 MP4，完整解码、App 内播放／跳转和带视频导出通过；该次没有模型或在线 TTS 请求。
- 文稿链路、每日推荐、浏览器端口避让、Argon 模式拦截及本地素材入片另有定向记录，详见 [产品约定与交付记录](docs/product-brief.md)。

开发机证据位于已排除出 Git 的 `evidence/`，关键入口为 `windows-package-acceptance.json`、`video-production-acceptance.json`、`imported-visuals-acceptance.json` 和 `gemini-mode-acceptance.json`；新克隆不会自带这些本机报告。

仍待完善或实际验收：

- Google／Edge 新配音的在线生成与听感，以及另一台实体电脑首次配置。
- 自由描述驱动的复杂定制动画、外部视频生成，以及导入配音的字幕对齐与合成。
- 长时间运行、浏览器完整重启后的登录保持。
- 代码签名和正式安装器；当前为未签名的 Windows x64 免安装开发版。

`tools/probe_provider.py`、`tools/run_live_pipeline.py`、`tools/run_live_ui.py` 和 `tools/verify_daily_ui.py --live` 会调用真实服务、消耗对应账号额度，不应作为普通离线检查运行。

## 版本管理范围

Git 保存源码、文档、依赖清单、打包配置与必要的试听／参考资源。`.venv/`、`build/`、`dist/`、`output/`、`evidence/`、本机配置和运行数据不提交；免安装 ZIP 与实际作品单独保管。

不要将账号密钥、`auth.json`、Google ADC 文件或浏览器 Cookie 加入仓库。
