# PowerTokens Video Studio

[English](README.md) | **简体中文**

基于 Wan 3.0 的 Windows 批量 / 连续剧集 AI 视频生成工具：Excel 导入、安全恢复、不重复扣费。

<!-- promo:start （活动结束后删除此段） -->
> **Wan 3.0 限时折扣至 2026 年 10 月 7 日。** 当前价格见 [Wan 3.0 模型页](https://powertokens.ai/zh-Hans/models/wan3.0-video?utm_source=github&utm_medium=oss&utm_campaign=video-studio)。
<!-- promo:end -->

![演示：Wan 3.0 生成的片段](docs/images/demo.gif)

PowerTokens Video Studio 通过 [PowerTokens](https://powertokens.ai/zh-Hans?utm_source=github&utm_medium=oss&utm_campaign=video-studio) API 调用 Wan 3.0 视频模型（`wan3.0-video`），适合需要一次生成大量片段（例如短剧分集），并希望中断后能恢复、不重复付费的用户。

**目前仅支持 Wan 3.0 视频模型。** 需要其他模型，欢迎在 [Issues](../../issues) 中提出。

## 功能

- **单条生成**：提示词、时长（2–30 秒）、720p / 1080p、16:9 / 9:16 / 1:1，可选首帧、尾帧及参考图片 / 视频 / 音频链接和随机种子；可从提示词识别时长与比例（如“16秒，16:9横屏…”或分镜时间轴“0-5秒…10-16秒”）；默认开启原生音频。
- **Excel / CSV 批量生成**：一行一条，限定并发（生成 1–8、下载 1–4），按状态显示行颜色，可导出结果 CSV。
- **连续剧集的全剧人物设定**：一段人物设定自动加在每集 Prompt 前，保持人物一致。
- **按任务 ID 恢复**：轮询前先保存任务 ID；恢复只查询和下载，不会重新提交。
- **断点续传下载**：`.part` 临时文件，HTTP Range，并校验交界内容和文件大小。
- **Key 池**：可添加多个 Key，批量时按行轮换首选 Key。
- **不重复扣费**：只有在尚未获得任务 ID 且提交被明确拒绝时才换 Key；超时、5xx、结果不明确时都不会自动重试。
- **Key 加密保存**：默认只在内存中；勾选记住后使用 Windows DPAPI 加密，绑定当前 Windows 账户。
- 可选命令行（`pt_wan.py`）和 MCP 服务（`mcp_server.py`）。

## 界面预览

| 生成视频 | 批量导入 | API Key |
|---|---|---|
| ![生成视频页](docs/images/screenshot-generate.png) | ![批量导入页](docs/images/screenshot-batch.png) | ![API Key 页](docs/images/screenshot-apikey.png) |

## 快速开始（无需 Python）

1. 从 [Releases](../../releases/latest) 页面下载 `PowerTokensVideoStudio.exe`。
2. 双击运行。EXE 没有代码签名，Windows SmartScreen 可能提示“Windows 已保护你的电脑”，点击「更多信息」→「仍要运行」。
3. 在「API Key」页粘贴 PowerTokens Key，点击「添加到 Key 池」。
4. 在「生成视频」页填写提示词，或在「批量导入」页导入表格。

附带三集示例表格：`短剧批量示例模板.xlsx`。

## 获取 API Key

在 [PowerTokens](https://powertokens.ai/zh-Hans/api-keys?utm_source=github&utm_medium=oss&utm_campaign=video-studio) 注册并创建 Key。工具内的「注册 / 获取 API Key」按钮会打开同一页面。

## 从源码运行

需要 Windows 和 Python 3.11+（安装时保留 Tcl/Tk，并勾选 “Add python.exe to PATH”），无需第三方库。双击 `Start.bat`，或运行 `python app.py`。

## 构建 EXE

在 Windows 上运行 `Build-EXE.bat`：创建构建环境、安装 PyInstaller、运行测试，生成带 PowerTokens 图标、已打包 `assets` 目录的 `dist\PowerTokensVideoStudio.exe`。

GitHub Actions 工作流 **Build Windows executable**（`.github/workflows/build-windows.yml`）会在推送 `v*` 标签或手动触发时构建同一个 EXE。

## 费用

PowerTokens 按视频秒数计费，价格与分辨率有关。工具按界面上标注的检查日期时的价格估算，实际扣费以 PowerTokens 账单为准。2026 年 10 月 7 日（本机日期）及之前按折扣价估算并同时显示原价；10 月 8 日起自动改按原价估算。最新价格请查看 [PT 的 Wan 3.0 价格页](https://powertokens.ai/zh-Hans/models/wan3.0-video?utm_source=github&utm_medium=oss&utm_campaign=video-studio)。

## 安全说明

- API Key 只发送到配置的 API 主机（默认 `https://api.powertokens.ai`），跳转时会移除，不会发送到 CDN / 存储域名。
- 任务记录只保存 Key 指纹（SHA-256 前 16 位）和末 4 位，不保存完整 Key。
- 桌面端 Key 默认只在内存中；勾选记住后用 Windows DPAPI 加密保存。
- **CLI 配置以明文保存 Key。** `python pt_wan.py config --add-key` 会把 Key 不加密地写入 `%LOCALAPPDATA%\PowerTokensWan\cli-config.json`（或 `PT_WAN_CONFIG` 指定的路径），在 Windows 上无法限制文件权限。建议使用环境变量 `POWERTOKENS_API_KEY` / `POWERTOKENS_API_KEYS`，并且不要把该文件提交到版本库。
- 带签名的视频下载链接会保存在本机任务记录中，下载完成后清空；如果下载一直没有完成，链接会保留，但链接本身会过期。
- 如需通过其他 HTTPS 网关访问，可设置 `POWERTOKENS_API_BASE`（仅限 HTTPS）。

本机数据位于 `%LOCALAPPDATA%\PowerTokensWan`（沿用旧版目录名，已有记录可继续使用）。

## 常见问题

**状态查询返回 HTTP 403？**
工具会继续用提交该任务的原 Key 查询，并尝试任务的下载地址。批量时该行先暂缓，其余行处理完后再用原 Key 查询一次。工具不会因 403 删除 Key。若持续出现，请在 PowerTokens 控制台查看该任务，稍后从任务记录继续。

**生成超时？**
每个任务本地最多等待 1 小时，到时会再尝试一次下载；无论结果如何都会保留任务 ID。在「任务记录 / 恢复」页选中任务，点击「继续查询选中任务」即可。请不要直接重新生成，以免重复扣费。

**点了「停止等待」，任务取消了吗？**
没有。只是停止本机等待，任务仍在云端继续并可能计费，可在任务记录里找回。

**批量行显示「提交结果未知」？**
工具无法确认任务是否已创建，因此不会自动重新提交。请在 PowerTokens 控制台找到该任务 ID，选中该行后点击「关联已有任务 ID」。

完整说明见 [使用说明.md](使用说明.md)，版本历史见 [CHANGELOG.md](CHANGELOG.md)。

## 开发

```bash
python -m unittest discover -s tests -v
```

测试使用模拟响应，不会调用真实接口。界面测试在 Windows 上运行，其他系统可设置 `PT_GUI_TESTS=1`。

## 许可证与社区

MIT，见 [LICENSE](LICENSE)。

问题与反馈：[Discord](https://discord.gg/JtgtRdhJVS) 或 [Issues](../../issues)。
