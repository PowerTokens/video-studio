"""Optional MCP adapter. Install mcp separately; the desktop app does not need it."""
import json
import os
from pathlib import Path
import subprocess
import sys
try:  # mcp 2.x renamed FastMCP to MCPServer
    from mcp.server.mcpserver import MCPServer as FastMCP
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP
from i18n import t

mcp = FastMCP('powertokens-video-studio')


def run(args):
    result = subprocess.run([sys.executable, str(Path(__file__).with_name('pt_wan.py'))] + args,
        capture_output=True, text=True, encoding='utf-8', errors='replace',
        env=dict(os.environ, PYTHONIOENCODING='utf-8'))
    try:
        body = json.loads(result.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        body = {'error': t('mcp_incomplete')}
    return json.dumps({'ok': result.returncode == 0, 'returncode': result.returncode, 'result': body}, ensure_ascii=False)


@mcp.tool(description=t('mcp_check_key'))
def check_key() -> str:
    return run(['check'])


@mcp.tool(description=t('mcp_list_models'))
def list_models() -> str:
    return run(['list-models'])


@mcp.tool(description=t('mcp_estimate'))
def estimate_cost(duration_s: int = 5, resolution: str = '720p', model: str = 'wan3.0-video') -> str:
    return run(['estimate', '-d', str(duration_s), '-r', resolution, '-m', model])


@mcp.tool(description=t('mcp_generate'))
def generate_video(prompt: str, duration_s: int = 5, resolution: str = '720p', output: str = '',
                   model: str = 'wan3.0-video') -> str:
    args = ['generate', '-p', prompt, '-d', str(duration_s), '-r', resolution, '-m', model]
    if output:
        args += ['-o', output]
    return run(args)


@mcp.tool(description=t('mcp_resume'))
def resume_video(task_id: str, output: str, key_index: int = 1) -> str:
    return run(['resume', task_id, '-o', output, '--key-index', str(key_index)])



@mcp.tool(description=t('mcp_compare'))
def compare_videos(prompt: str, models: str, duration_s: int = 5, resolution: str = '720p',
                   ratio: str = '16:9', output_dir: str = '', name: str = '') -> str:
    args = ['compare', '--models', models, '-p', prompt, '-d', str(duration_s), '-r', resolution,
            '--ratio', ratio]
    if output_dir:
        args += ['-o', output_dir]
    if name:
        args += ['--name', name]
    return run(args)


@mcp.tool(description=t('cli_storyboard_help'))
def storyboard(script: str, output: str, clips: int = 0, duration_s: int = 8,
               model: str = 'wan3.0-video', text_model: str = 'glm-4.7-flash',
               ratio: str = '9:16', resolution: str = '720p',
               chain: bool = False) -> str:
    # Write script to a temp file path via args expects a file — pass through env by writing in CLI.
    import tempfile
    from pathlib import Path as P
    tmp = tempfile.NamedTemporaryFile('w', encoding='utf-8', suffix='.txt', delete=False)
    tmp.write(script)
    tmp.close()
    args = ['storyboard', '--script', tmp.name, '--out', output or 'storyboard.xlsx',
            '--clips', str(clips), '-d', str(duration_s), '-m', model, '--text-model', text_model,
            '--ratio', ratio, '-r', resolution]
    if chain:
        args.append('--chain')
    try:
        return run(args)
    finally:
        try:
            P(tmp.name).unlink(missing_ok=True)
        except OSError:
            pass

if __name__ == '__main__':
    mcp.run()
