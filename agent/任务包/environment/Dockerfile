FROM python:3.12-slim

# 中文材料 + 可能要出 PDF/图表时需要，先装上
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl fonts-noto-cjk \
 && rm -rf /var/lib/apt/lists/*

# 这个任务用到的：读写 csv/xlsx。verifier 只用标准库，但 agent 可能想用 pandas
RUN pip install --no-cache-dir pandas==2.3.1 openpyxl==3.1.5

# Claude Code（agent 侧）。如果你用别的 agent，把这层换掉即可
RUN apt-get update && apt-get install -y --no-install-recommends nodejs npm \
 && npm install -g @anthropic-ai/claude-code \
 && apt-get purge -y npm && rm -rf /var/lib/apt/lists/*

WORKDIR /app
