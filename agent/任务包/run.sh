#!/bin/bash
# 跑一组：run.sh no_skill | with_skill
#
# 需要两个环境变量：
#   ANTHROPIC_BASE_URL   anthropic 协议端点
#   ANTHROPIC_AUTH_TOKEN 对应的 token
# 可选：
#   MODEL   模型名，默认 claude-sonnet-4-6
#   IMAGE   镜像名，默认 exam-task:local（不存在时用 environment/Dockerfile 构建）
#
# 工作区挂载为可写：任务要求「在现成的空表上填」。
set -euo pipefail

S="$(cd "$(dirname "$0")" && pwd)"
ARM="${1:?用法: run.sh no_skill|with_skill}"
: "${ANTHROPIC_BASE_URL:?请设置 ANTHROPIC_BASE_URL}"
: "${ANTHROPIC_AUTH_TOKEN:?请设置 ANTHROPIC_AUTH_TOKEN}"
IMAGE="${IMAGE:-exam-task:local}"
MODEL="${MODEL:-claude-sonnet-4-6}"

docker image inspect "$IMAGE" >/dev/null 2>&1 || {
  echo "构建镜像 $IMAGE ..."; docker build -q -t "$IMAGE" "$S/environment"; }

W="$S/runs/$ARM"; rm -rf "$W"; mkdir -p "$W"; cp "$S"/材料/* "$W"/
P=$(cat "$S/任务书.md")

if [ "$ARM" = "with_skill" ]; then
  [ -f "$S/skill/SKILL.md" ] || { echo "缺 skill/SKILL.md，先补上再跑 with_skill" >&2; exit 2; }
  mkdir -p "$W/skill"; cp -r "$S"/skill/* "$W/skill/"
  P="$P

（工作区 skill/ 下有一份工作方法，开工前先读它，按它说的做。）"
fi

docker run --rm --user "$(id -u):$(id -g)" -e HOME=/tmp \
  -e ANTHROPIC_BASE_URL -e ANTHROPIC_AUTH_TOKEN \
  -e ANTHROPIC_MODEL="$MODEL" -e ANTHROPIC_SMALL_FAST_MODEL="$MODEL" \
  -v "$W:/app" -w /app "$IMAGE" \
  claude -p "$P" --allowedTools Bash Read Grep Glob Write \
  > "$W/_transcript.txt" 2>&1 || true

echo "产物已落在 $W/"

# verifier 是你要补的，补好之后这里会自动打分
if [ -f "$S/verifier/verify.py" ]; then
  echo -n "$ARM  "; python3 "$S/verifier/verify.py" "$W"
else
  echo "（未发现 verifier/verify.py，跳过打分）"
fi
