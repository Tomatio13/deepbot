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

exec "$@"
