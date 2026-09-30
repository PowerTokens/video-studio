# Changelog

All notable changes to PowerTokens Video Studio. Versions before 1.10 were released as "PowerTokens Wan".

## 1.11

- **English interface.** The whole app (all tabs, dialogs, status messages, batch states, the CLI and the MCP tool descriptions) is now available in English as well as Chinese. Switch with the **中文 / English** toggle in the top-right corner; it applies immediately (your prompt, keys and imported batch are kept) and is remembered in `%LOCALAPPDATA%\PowerTokensWan\settings.json`. On first launch the app uses Chinese when the Windows display language is Chinese, English otherwise. The Chinese wording is unchanged.
- English subtitle "Wan 3.0 batch video generation"; the discount badge reads "Wan 3.0 limited-time discount until Oct 7" and still hides itself after 2026-10-07.
- **English duration detection**: "10s", "10 sec", "10 seconds", "a ten-second clip", "total length 15s", "15 seconds total", "make a 12s video", and shot timelines such as "0-3s", "0s-3s" or "00:00-00:03". The same priority applies as before (stated total > duration at the start > other standalone duration > end of the shot timeline; clamped to 2–30 s). Timecode timelines ("00:00-00:03 …") and "0秒-3秒" style ranges now also work in Chinese prompts. Decades such as "1980s" or "the 80s" are not mistaken for durations.
- **English spreadsheets**: batch import accepts English column headers (`Wan 3.0 Prompt` / `Prompt`, `Duration (s)` / `Duration` / `Length`, `Resolution`, `Aspect ratio` / `Ratio`, `Title` / `Name`, `ID`) and duration cells such as "8 sec". New English sample `short-drama-batch-template.xlsx`; **Save sample template…** on the Batch import tab saves the sample in the current interface language (both samples are bundled in the EXE).
- A shared character setting written in English is added as "[Series characters] … [This episode] …"; anything containing Chinese keeps the original 【全剧固定人物设定】 labels. The batch identity is computed the same way for both, so re-importing an existing batch never resubmits finished rows.
- Batch table columns widen to fit their headings.
- CI: a **UI screenshots** job launches the app in both languages on the Windows runner (dummy key, throw-away data folder), captures every tab and reports any clipped button or label (`tools/capture_screenshots.py`). The build job checks that both sample spreadsheets are bundled.
- English screenshots in README.md; the Chinese screenshots in README.zh-CN.md are refreshed for 1.11 (showing the language toggle).

## 1.10

Fixes after the first 1.10 build:

- Main tabs: the selected tab (生成视频, 批量导入, 任务记录 / 恢复, API Key) no longer shrinks and drops below the others. The clam theme's own selected-tab padding (6 4 6 2) overrode ours; all tab states now use the same padding (20×12) and font, and the selected tab stands out only by its teal text on a light-teal background.
- Prompt duration detection: a total at the start of the prompt ("16秒，16:9横屏…"), "时长：16秒" or "total 16s" is now used even when the prompt also contains shot ranges ("0-5秒 … 10-16秒"). Previously such prompts were refused. With only shot ranges, the duration is taken from the end of the timeline and the hint says so. Values outside 2–30 s are clamped with a note. Conflicting totals are still refused.
- The recognition hint now reads e.g. "已识别：16 秒 · 16:9".
- The Wan 3.0 discount ($0.04/s 720P, $0.08/s 1080P) is applied through 2026-10-07 (local date). From 2026-10-08 the desktop, batch and CLI estimates switch to the regular price ($0.10 / $0.20), the "原价" note disappears and the discount badge is hidden. The badge reads "Wan 3.0 限时折扣至 10 月 7 日".

- Renamed to **PowerTokens Video Studio**; subtitle "Wan 3.0 视频批量生成". EXE is now `PowerTokensVideoStudio.exe`.
- Redesigned interface (light theme, cards, responsive two-column layout, wrapping action buttons).
- Official PowerTokens logo in the header, as the window icon and as the EXE icon.
- Batch table rows are colored by status (completed / running / failed / pending).
- Cost estimates use the current PowerTokens Wan 3.0 price (checked 2026-09-30) and show the regular price during the discount.
- The API address is defined once (`API_BASE`) and can be overridden with the `POWERTOKENS_API_BASE` environment variable (HTTPS only). The API Key is only ever sent to that host.
- User-Agent is now `PowerTokensVideoStudio/1.10`; the MCP server is named `powertokens-video-studio`.
- Default output folder for new videos is `Videos\PowerTokensVideoStudio`. The data folder `%LOCALAPPDATA%\PowerTokensWan` is unchanged, so existing task records and saved keys keep working.
- Wording: "停止等待" replaces "停止本地等待"; the media note mentions image / video / audio links.
- Added MIT license, English and Chinese READMEs, and a rewritten user guide.

## 1.9

- Optional shared character setting for batch imports (added in front of every episode prompt; remembered per spreadsheet).
- "Register / get API Key" button that opens the PowerTokens website.
- Unified cost estimate across desktop, CLI and MCP.
- Three-episode sample spreadsheet `短剧批量示例模板.xlsx`.

## 1.8

- Status queries always use the key that submitted the task.
- In batches, a task whose status query returns HTTP 403 is set aside and queried once more with its original key after the other rows finish. A second 403 pauses the row; it is never resubmitted.

## 1.7

- Multi-select and copy Task IDs (Ctrl+C, "copy selected", "copy all") in the history and batch tables.
- When a finished task returns `metadata.url`, the video is downloaded from that link automatically.

## 1.6

- Recognizes `completed`, `succeeded` and `success` as finished states.
- Failed status queries (429, 5xx, network errors, missing status) are counted and retried with 10 / 20 / 40 / 60 s backoff; after 6 consecutive failures polling pauses and the Task ID is kept.

## 1.5

- Resuming a download no longer discards the partial file when switching to a direct video link; the 64 KB overlap is compared before appending.
- "Resume from video link" in the task history page.

## 1.4

- Resumable downloads (`.part` file, HTTP Range, ETag / Last-Modified / size checks).
- Separate download concurrency (1–4) for batches.

## 1.3

- Storyboard-aware duration detection ("总时长" and "生成一段 10 秒" outrank shot ranges such as 0-2秒).
- Batch import from `.xlsx` / `.csv` with bounded concurrency, key rotation, pause / resume and CSV export.

## 1.2

- Every new request sends `generate_audio: true` (native audio).

## 1.1

- Any key format accepted; pasted `Bearer` prefixes, quotes and invisible characters are cleaned.
- Duration and aspect ratio can be detected from the prompt.

## 1.0

- First Windows desktop release: single video generation, task history and resume by Task ID.
- Task IDs are saved before polling; uncertain submissions are never retried automatically, and keys are switched only after an explicit rejection before a Task ID exists.
- Streamed downloads in 1 MB chunks with MP4 header and length checks; the API Key is not sent to other hosts or across redirects.
