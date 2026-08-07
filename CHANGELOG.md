# 更新记录

## v0.2.2（2026-08-07）

- 新增 API Key 安全记忆：Windows 使用系统凭据管理器，macOS 使用系统钥匙串。
- 再次打开应用或切换服务商时，会自动回填对应服务商的 API Key。
- 新增“安全记住 API Key”开关和“清除已保存 Key”按钮。
- API Key 仍不会写入 `state.json`、项目源码或 GitHub 仓库。

## v0.2.1（2026-08-06）

- 修复 Kimi 测试连接时报 `invalid temperature` 的问题。
- Kimi 请求不再显式发送温度参数，由模型接口自动采用合法默认值。
- DeepSeek、千问、智谱 GLM 和自定义接口继续使用创作任务指定的温度参数。

## v0.2.0（2026-08-06）

- 新增 DeepSeek 官方接口预设，默认模型为 `deepseek-v4-flash`。
- 新增千问（阿里云百炼）接口预设，默认模型为 `qwen-plus`。
- 新增智谱 GLM 接口预设，默认模型为 `glm-5.2`。
- 新增 Kimi 接口预设，默认模型为 `kimi-k3`。
- 新增服务商切换、分服务商内存 Key、环境变量读取与“测试连接”功能。
- 继续支持 OpenAI 和自定义 OpenAI Chat Completions 兼容接口。
- Windows 与 macOS 构建产物改用版本号命名，避免覆盖旧版本。

## v0.1.0

- 首个桌面版：视频混剪、音频与字幕导入、剪映草稿生成、小说改文。
