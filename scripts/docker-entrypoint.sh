#!/bin/sh
set -eu

OPENAI_MODEL_PY="$(
python - <<'PY'
import inspect
import strands.models.openai
print(inspect.getsourcefile(strands.models.openai.OpenAIModel) or "")
PY
)"

if [ -n "$OPENAI_MODEL_PY" ] && [ -f "$OPENAI_MODEL_PY" ]; then
  # Z.AI compatibility: remove image_url.detail / image_url.format fields.
  sed -i '/"detail":[[:space:]]*"auto",/d' "$OPENAI_MODEL_PY"
  sed -i '/"format":[[:space:]]*mime_type,/d' "$OPENAI_MODEL_PY"
fi

if [ "$#" -eq 0 ]; then
  set -- deepbot
fi

workspace_dir="${WORKSPACE_DIR:-/workspace}"
bot_rw_dir="${BOT_RW_DIR:-$workspace_dir}"
agent_memory_dir="${AGENT_MEMORY_DIR:-$bot_rw_dir/agent-memory/memory}"
transcript_dir="${DEEPBOT_TRANSCRIPT_DIR:-$bot_rw_dir/transcripts}"
cron_jobs_dir="${CRON_JOBS_DIR:-$bot_rw_dir/jobs}"

for dir in "$workspace_dir" "$bot_rw_dir" "$agent_memory_dir" "$transcript_dir" "$cron_jobs_dir"; do
  mkdir -p "$dir"
done

exec "$@"
