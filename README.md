# motion-worker

Imagem Docker (linux/amd64, CPU) que renderiza composições Hyperframes 0.9.94 (HTML + GSAP -> mp4/mov/webm).
Serve como RunPod Serverless e também roda com `docker run` em qualquer servidor (benchmark). Sem telemetria
(`HYPERFRAMES_NO_TELEMETRY=1`), sem segredos, Chrome e fontes embutidos no build, usuário não-root.

Imagem pública: `ghcr.io/rgvieira49/grp-motion-worker` (tags `latest` e o sha do commit). Repo: `RGVieira49/grp-motion-worker`.

## Entrada (RunPod `input`)

`acao: "render"` (padrão)
- `projeto_url` (https, .tar.gz) **ou** `projeto_b64` (.tar.gz em base64): pasta do projeto com `index.html` na raiz (ou numa única subpasta). Máx. 50 MB, sem caminho absoluto, `..` ou links.
- `formato`: `mp4` (padrão), `mov`, `webm`. `qualidade`: `draft|looks|delivery|standard|high`. `workers`: número ou `auto`.
- `upload_url` (https): o vídeo vai por PUT. Sem ele volta `video_b64` (só se < 15 MB).
- Timeout total de render: 15 min.

Saída: `ok`, `segundos_render`, `duracao_video`, `bytes`, `nproc`, `cpus_limite_container`, `cpu_modelo`, `ram_mb`, `resumo_render`, e `video_b64` ou `enviado`. Em falha: `ok: false` e `erro` (últimas 30 linhas do log).

`acao: "bench"` (+ `workers` opcional): renderiza `teste-1` (6 s), `comparacao-intro` (4,2 s) e `bench-30s` (30 s, 1080p30, várias cenas) embutidas na imagem e devolve segundos, duração, ffprobe e a máquina.

## Benchmark em qualquer servidor

```
docker run --rm --cpus 4 ghcr.io/rgvieira49/grp-motion-worker bench        # workers auto
docker run --rm --cpus 4 ghcr.io/rgvieira49/grp-motion-worker bench 4      # 4 workers
```
Cada worker abre um Chrome (~256 MB de RAM). Use `--shm-size=1g` se o Chrome reclamar de /dev/shm.

## Publicar

Push em `main` do repo `RGVieira49/grp-motion-worker` dispara o GitHub Actions (`.github/workflows/imagem.yml`), que publica `ghcr.io/rgvieira49/grp-motion-worker:<sha>` e `:latest`. O pacote precisa estar **público** (a RunPod não tem credencial de registro). No endpoint RunPod, use a tag do sha.

Build local: `docker buildx build --platform linux/amd64 -t motion-worker .`
