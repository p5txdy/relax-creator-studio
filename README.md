# 解压创作工坊

一个本地 Windows/macOS 桌面应用，包含“视频混剪”和“小说改文”两个工作台。

当前版本：`v0.2.3`

## 当前功能

### 视频混剪

- 批量添加、排序和删除视频素材
- 自动读取并显示每段素材的真实原片时长，不再默认只取 5 秒
- 为每段素材设置截取起点与时长，或一键恢复完整时长
- 导入主音频，并将它作为整条时间线的时长基准
- 默认“均衡混剪”，根据主音频时长让所有素材尽量平均出现
- 可切换“顺序完整播放”；素材不足时循环，且只截断时间线末段
- 导入 SRT 字幕到剪映独立字幕轨，越过音频结尾的字幕自动裁短
- 输出 `9:16`、`16:9`、`1:1`、`4:5` 画幅
- 支持淡化、擦除、滑动、圆形等转场
- 添加循环背景音乐并控制音量
- 一键生成剪映专业版草稿并启动剪映，在时间线中继续编辑
- 使用 FFmpeg 输出 H.264 MP4
- 调用 AI 生成小红书、抖音、视频号、B站或微博文案

### 小说改文

- 导入 UTF-8/GB18030 TXT、Markdown 和 DOCX
- 可不导入文件，直接在“原文章节”编辑框中粘贴正文并开始改写
- 自动识别“第×章”或 `Chapter N`；无章节标题时按长度分段
- 轻度润色、深度改写、扩写、精简及影视化改写
- 管理人物、世界观、剧情时间线和禁改项
- 原文与改写稿并排对照
- 逐章或批量调用 AI，随时人工修改
- 导出 UTF-8 BOM 的 TXT 改写稿

### AI 模型

- DeepSeek：官方接口，默认 `deepseek-v4-flash`
- 千问（阿里云百炼）：官方 OpenAI 兼容接口，默认 `qwen-plus`
- 智谱 GLM：官方接口，默认 `glm-5.2`
- Kimi：官方接口，默认 `kimi-k3`
- OpenAI 与其他自定义 Chat Completions 兼容接口
- 一键切换服务商、自动填写接口地址、为各服务商安全记住 API Key、测试连接

## Windows 运行

双击仓库根目录下的 `启动解压创作工坊.cmd`，或直接运行打包后的单文件程序：

```text
dist\解压创作工坊-v0.2.3.exe
```

开发模式可在本目录执行：

```powershell
python .\app.py
```

开发运行需要 Python 3.10+；首次准备环境可执行：

```powershell
python -m pip install -r requirements-windows.txt
```

项目状态保存在：

```text
%APPDATA%\RelaxCreatorStudio\state.json
```

API Key 不会写入这个文件；启用“安全记住”后，Windows 版存入系统凭据管理器。

## macOS 运行与打包

Mac 版使用相同的项目文件和功能，最终生成原生应用与安装镜像：

```text
dist-macos/解压创作工坊-v0.2.3.app
dist-macos/解压创作工坊-v0.2.3-macOS.dmg
```

在 Mac 上准备 Python 3.11 与 Homebrew，然后双击 `打包Mac版.command`。脚本会创建独立环境、安装 Mac 原生依赖、打包 `.app`、执行自检并生成 `.dmg`。第一次运行脚本时，系统若提示没有执行权限，可在终端执行：

```zsh
cd /项目所在位置/creator_studio
chmod +x 打包Mac版.command 启动Mac开发版.command
./打包Mac版.command
```

本地配置在 Mac 上保存在：

```text
~/Library/Application Support/RelaxCreatorStudio/state.json
```

Mac 版会识别 `/Applications` 中的剪映 `.app`，并检查以下常见草稿位置，也可以在“模型与工具”中手动选择剪映应用和草稿目录：

```text
~/Movies/JianyingPro Drafts
~/Movies/JianyingPro/User Data/Projects/com.lveditor.draft
~/Movies/CapCut/User Data/Projects/com.lveditor.draft
```

## 配置 AI

打开“模型与工具”，在“模型服务商”中选择 DeepSeek、千问、智谱 GLM 或 Kimi。应用会自动填写官方 Base URL 和默认模型，也允许手动修改模型名称。

默认勾选“安全记住 API Key”。点击“保存模型设置”或成功完成“测试连接”后，Windows 会将 Key 存入系统凭据管理器，macOS 会存入系统钥匙串；下次打开应用会自动回填。Key 不会写入 `state.json`、源码或 GitHub，也可以随时点击“清除已保存 Key”。

如果不想保存，可取消勾选；也可以在启动应用前设置对应环境变量：

| 服务商 | 环境变量 | 默认模型 |
| --- | --- | --- |
| DeepSeek | `DEEPSEEK_API_KEY` | `deepseek-v4-flash` |
| 千问 | `DASHSCOPE_API_KEY` | `qwen-plus` |
| 智谱 GLM | `ZHIPUAI_API_KEY` 或 `ZAI_API_KEY` | `glm-5.2` |
| Kimi | `MOONSHOT_API_KEY` | `kimi-k3` |
| OpenAI | `OPENAI_API_KEY` | `gpt-4.1-mini` |

点击“测试连接”可用一条最短请求检查 Key、模型名称和网络。测试与正式生成都会产生服务商侧的少量 API 用量。

官方接口资料：[DeepSeek](https://api-docs.deepseek.com/)、[阿里云百炼](https://help.aliyun.com/zh/model-studio/)、[智谱开放平台](https://docs.bigmodel.cn/)、[Kimi API](https://platform.kimi.com/docs/overview)。

## 配置 FFmpeg

视频导出需要 FFmpeg，读取素材时长建议同时配置 FFprobe。Windows 安装后可在“模型与工具”中选择 `.exe` 路径；Mac 可执行 `brew install ffmpeg`，应用会自动检查 PATH。

不配置 FFmpeg 时，仍可添加、排列、裁剪素材并保存项目，只是不能生成 MP4。

## 打开剪映草稿

应用会自动识别剪映专业版程序和当前草稿目录。视频工作台中点击“生成并打开剪映”后：

1. 应用读取原片真实时长，并按当前截取区间生成剪映主视频轨道。
2. “均衡混剪”会让全部素材尽量平均出现；“顺序完整播放”会优先播完前一段。
3. 如果导入了主音频，视频会在不足时循环并仅截断末段，时间线长度严格等于主音频。
4. 主音频、可选背景音乐和 SRT 字幕会进入各自独立轨道。
5. 画幅、帧率和转场会写入草稿，剪映专业版随后自动启动。
6. 在剪映首页的“本地草稿”中打开同名项目；列表未立即刷新时，重新进入首页即可。

此功能不依赖 FFmpeg。当前草稿格式使用 `pyJianYingDraft 0.3.0` 生成，支持视频、音频、字幕轨道和转场结构。

### Mac 剪映兼容边界

Mac 应用本身可以正常生成草稿，但草稿库上游当前仍标注：macOS 支持“草稿生成”，生成后的草稿建议在 Windows 版剪映中打开并导出。剪映 Mac 10.3 也有过打开新生成草稿时报“内容已损坏”的公开问题。因此，Mac 版应用内保留“生成草稿”和“启动剪映”，但不承诺所有剪映 Mac 版本都能直接读取；正式工作流建议用 Windows 版剪映完成最终打开与导出。

## 测试与打包

```powershell
python -m unittest discover -s tests -v
.\打包Windows版.cmd
```

Windows 打包脚本会自动安装 PyInstaller，并在缺少 `vendor` 目录时准备剪映草稿与 MediaInfo 依赖。打包产物位于 `dist\解压创作工坊-v0.2.3.exe`，是可独立运行的单个 EXE。不同版本使用不同文件名，不会覆盖旧版本。

Mac 打包需在 macOS 上双击 `打包Mac版.command`；PyInstaller 不支持在 Windows 上交叉生成 macOS `.app`。

## 内容权利

请只上传、剪辑和改写你拥有版权、获得授权或依法可使用的素材。AI 改写用于提高原创表达和编辑效率，不应被用于冒充他人作品。
