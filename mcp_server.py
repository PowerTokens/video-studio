"""Optional MCP adapter. Install mcp separately; the desktop app does not need it."""
import json
import os
from pathlib import Path
import subprocess
import sys
from mcp.server.fastmcp import FastMCP

mcp = FastMCP('powertokens-video-studio')


def run(args):
    result = subprocess.run([sys.executable, str(Path(__file__).with_name('pt_wan.py'))] + args,
        capture_output=True, text=True, encoding='utf-8', errors='replace',
        env=dict(os.environ, PYTHONIOENCODING='utf-8'))
    try:
        body = json.loads(result.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        body = {'error': '执行未完成，请查本地任务记录'}
    return json.dumps({'ok': result.returncode == 0, 'returncode': result.returncode, 'result': body}, ensure_ascii=False)


@mcp.tool()
def check_key() -> str:
    """检查 CLI Key 池配置（不验证远程权限）。"""
    return run(['check'])


@mcp.tool()
def estimate_cost(duration_s: int = 5, resolution: str = '720p') -> str:
    """按 PT 公示单价估算，实际以平台账单为准。"""
    return run(['estimate', '-d', str(duration_s), '-r', resolution])


@mcp.tool()
def generate_video(prompt: str, duration_s: int = 5, resolution: str = '720p', output: str = '') -> str:
    """提交生成；可能需等待一小时。结果不明确时先查原任务，不可自动重新生成。"""
    args = ['generate', '-p', prompt, '-d', str(duration_s), '-r', resolution]
    if output:
        args += ['-o', output]
    return run(args)


@mcp.tool()
def resume_video(task_id: str, output: str, key_index: int = 1) -> str:
    """用原 Key 查询并下载已有任务，不提交新生成。"""
    return run(['resume', task_id, '-o', output, '--key-index', str(key_index)])


if __name__ == '__main__':
    mcp.run()
