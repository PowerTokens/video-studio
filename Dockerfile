# MCP server only (mcp_server.py over stdio); the Windows desktop app is not included.
# Build: docker build -t powertokens-video-studio-mcp .
# Run:   docker run -i --rm -e POWERTOKENS_API_KEY=your-key -v "$PWD/videos:/videos" powertokens-video-studio-mcp
# The server starts and lists its tools without a key; generate_video and resume_video then return an AUTH error.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN pip install "mcp>=1.2" \
    && useradd --create-home --uid 1000 app \
    && mkdir /videos && chown app:app /videos

COPY mcp_server.py pt_wan.py wan_core.py i18n.py input_helpers.py LICENSE /app/

USER app
# Videos without an absolute output path are saved here.
WORKDIR /videos
CMD ["python", "/app/mcp_server.py"]
