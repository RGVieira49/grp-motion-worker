# Trabalhador de render de motion do Estúdio GRP (Hyperframes: HTML+GSAP -> vídeo), CPU, linux/amd64.
# RunPod Serverless (handler.py) ou `docker run ... bench` em qualquer servidor. Nenhum dado/segredo na imagem:
# o projeto chega por URL/base64 a cada job. Sem rede em tempo de render (Chrome e fontes vêm no build).
FROM node:22-bookworm-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    HYPERFRAMES_NO_TELEMETRY=1 \
    HYPERFRAMES_NO_UPDATE_CHECK=1 \
    HYPERFRAMES_SKIP_SKILLS=1 \
    DO_NOT_TRACK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl unzip ffmpeg python3 python3-venv fontconfig \
        libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 libxkbcommon0 \
        libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libgbm1 libasound2 libpango-1.0-0 \
        libcairo2 libx11-6 libxext6 libxcb1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Fontes locais (Inter variável + Anton): as composições também as carregam por @font-face
COPY fonts/ /usr/local/share/fonts/grp/
RUN fc-cache -f

RUN npm install -g hyperframes@0.9.94 && npm cache clean --force

RUN python3 -m venv /opt/venv && /opt/venv/bin/pip install --no-cache-dir runpod==1.12.0

# Usuário não-root; o Chrome fixado do Hyperframes é baixado AGORA (build), no cache desse usuário
RUN useradd -m -u 1001 motion
USER motion
ENV HOME=/home/motion
RUN hyperframes browser ensure && hyperframes browser path > /home/motion/.browser_path && cat /home/motion/.browser_path

USER root
COPY bench /app/bench
COPY handler.py bench.sh /app/
RUN chmod +x /app/bench.sh && chown -R motion:motion /app
USER motion
WORKDIR /app

ENTRYPOINT ["/app/bench.sh"]
CMD []
