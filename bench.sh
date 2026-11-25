#!/bin/sh
# Entrypoint da imagem.
#   docker run --rm --cpus N <imagem> bench [workers]   -> imprime o JSON do bench e sai
#   (sem argumento)                                      -> modo RunPod Serverless (handler.py)
if [ "$1" = "bench" ]; then
  exec /opt/venv/bin/python -u /app/handler.py --bench "${2:-}"
fi
exec /opt/venv/bin/python -u /app/handler.py
