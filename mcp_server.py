"""Optional MCP adapter. Install mcp separately; the desktop app does not need it."""
import json
import os
from pathlib import Path
import subprocess
import sys
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


@mcp.tool(description=t('mcp_estimate'))
def estimate_cost(duration_s: int = 5, resolution: str = '720p') -> str:
    return run(['estimate', '-d', str(duration_s), '-r', resolution])


@mcp.tool(description=t('mcp_generate'))
def generate_video(prompt: str, duration_s: int = 5, resolution: str = '720p', output: str = '') -> str:
    args = ['generate', '-p', prompt, '-d', str(duration_s), '-r', resolution]
    if output:
        args += ['-o', output]
    return run(args)


@mcp.tool(description=t('mcp_resume'))
def resume_video(task_id: str, output: str, key_index: int = 1) -> str:
    return run(['resume', task_id, '-o', output, '--key-index', str(key_index)])


if __name__ == '__main__':
    mcp.run()
