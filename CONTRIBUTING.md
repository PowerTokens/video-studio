# Contributing to PowerTokens Video Studio

Thanks for helping make Video Studio better! Bug reports, ideas, prompt examples and pull requests are all welcome.

Please follow our [Code of Conduct](CODE_OF_CONDUCT.md). For security issues, see [SECURITY.md](SECURITY.md) instead of opening a public issue.

## Reporting a bug

Open a [bug report](../../issues/new?template=bug_report.yml) and include:

- The app version (shown in the window title, e.g. `v1.11`) and whether you use the EXE or run from source.
- Your Windows version and, when running from source, your Python version (`python --version`).
- What you did, what you expected and what happened instead. Screenshots and the exact error text help a lot.
- For generation problems: the prompt, duration, resolution and aspect ratio.

**Before posting, blur or remove your API key and any Task IDs** in text, screenshots and logs. We will never ask for your full key. If a Task ID is needed to look into a problem, we will ask you to share it privately.

Questions and quick help: [Discord](https://discord.gg/JtgtRdhJVS) and the [PowerTokens docs](https://docs.powertokens.ai/?utm_source=github&utm_medium=oss&utm_campaign=video-studio).

## Run from source on Windows

1. Install [Python 3.11 or newer for Windows](https://www.python.org/downloads/windows/). In the installer, tick **Add python.exe to PATH** and keep **tcl/tk and IDLE** selected (the app uses Tkinter).
2. Clone the repository:

   ```bat
   git clone https://github.com/PowerTokens/video-studio.git
   cd video-studio
   ```

3. Start the app with `Start.bat`, or run `python app.py`.

The desktop app uses only the Python standard library, so no `pip install` is needed. The optional MCP server (`mcp_server.py`) needs `pip install mcp`.

Your data (task records, saved keys, settings) lives in `%LOCALAPPDATA%\PowerTokensWan`. To experiment without touching it, set `LOCALAPPDATA` to a temporary folder in that terminal before starting the app.

## Run the tests

```bat
python -m unittest discover -s tests -v
```

- Tests use mocked responses and never call the real API, so no API key is needed and nothing is charged.
- The native Tk widget tests run on Windows; on other systems they are skipped unless you set `PT_GUI_TESTS=1`.
- `Build-EXE.bat` runs the same tests before building `dist\PowerTokensVideoStudio.exe`.

## Pull requests

- For larger changes, open an issue first so we can agree on the approach.
- Keep each pull request focused on one fix or feature, and add or update tests for behavior changes.
- Run the full test suite before pushing. CI runs it on Windows with Python 3.12 and also builds the EXE and captures UI screenshots in both languages.
- Interface text lives in `i18n.py`. Add every new string to both `ZH` and `EN`, and check that labels and buttons still fit in both languages.
- Never resubmit a task automatically when its outcome is uncertain, and never send the API key to any host other than the configured API host. These protect users from paying twice and from leaking keys.
- Don't commit API keys, Task IDs, generated videos, `keys.json` or `cli-config.json`.
- Add a line under **Unreleased** in [CHANGELOG.md](CHANGELOG.md) for user-visible changes.

By contributing, you agree that your contributions are licensed under the [MIT License](LICENSE).
