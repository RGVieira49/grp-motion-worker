"""Trabalhador RunPod de motion (Hyperframes: HTML+GSAP -> vídeo), CPU.

Entrada do job (input):
    acao            "render" (padrão) ou "bench"

  acao = "render"
    projeto_url     link https de um .tar.gz da pasta do projeto (index.html na raiz ou numa única subpasta)
    projeto_b64     alternativa: o .tar.gz em base64
    formato         mp4 (padrão) | mov | webm
    qualidade       opcional: draft | looks | delivery | standard | high
    workers         opcional: número de workers do Hyperframes (ou "auto")
    upload_url      opcional: link https; o vídeo vai por PUT. Sem ele, volta em video_b64 (só se < 15 MB)
  Saída: ok, segundos_render, duracao_video, bytes, nproc, cpu_modelo [+ video_b64 | enviado] ; erro legível em falha.

  acao = "bench"
    workers         opcional: testa paralelismo
  Renderiza as composições embutidas em /app/bench e devolve tempos, ffprobe resumido, nproc, cpu_modelo, RAM.

Uso fora do RunPod:  python handler.py --bench [workers]
"""
import base64, io, json, os, re, shutil, signal, subprocess, sys, tarfile, tempfile, time, urllib.request

MAX_TAR = 50 * 1024 * 1024          # tamanho máximo do .tar.gz recebido
MAX_EXTRAIDO = 300 * 1024 * 1024    # proteção contra tar-bomba
MAX_B64_SAIDA = 15 * 1024 * 1024    # acima disso, só por upload_url
TIMEOUT_RENDER = 15 * 60            # segundos, total por job
BENCH_DIR = os.environ.get('MOTION_BENCH_DIR', '/app/bench')
FORMATOS = {'mp4': 'video/mp4', 'mov': 'video/quicktime', 'webm': 'video/webm'}
QUALIDADES = {'draft', 'looks', 'delivery', 'standard', 'high'}


# ---------- máquina ----------
def cpu_modelo():
    try:
        for linha in open('/proc/cpuinfo'):
            if linha.lower().startswith('model name'):
                return linha.split(':', 1)[1].strip()
    except OSError:
        pass
    return 'desconhecido'


def cpus_efetivas():
    """CPUs que o container realmente pode usar (--cpus / cgroup), além do nproc do host."""
    try:
        q, p = open('/sys/fs/cgroup/cpu.max').read().split()
        if q != 'max':
            return round(int(q) / int(p), 2)
    except (OSError, ValueError):
        pass
    try:
        q, p = int(open('/sys/fs/cgroup/cpu/cpu.cfs_quota_us').read()), int(open('/sys/fs/cgroup/cpu/cpu.cfs_period_us').read())
        if q > 0:
            return round(q / p, 2)
    except (OSError, ValueError):
        pass
    return None


def ram_mb():
    try:
        for linha in open('/proc/meminfo'):
            if linha.startswith('MemTotal'):
                return int(linha.split()[1]) // 1024
    except OSError:
        pass
    return None


def maquina():
    return {'nproc': len(os.sched_getaffinity(0)), 'cpus_limite_container': cpus_efetivas(), 'cpu_modelo': cpu_modelo(), 'ram_mb': ram_mb()}


# ---------- utilidades ----------
class Entrada(ValueError):
    """Erro de validação de entrada (mensagem já legível)."""


def ultimas_linhas(texto, n=30):
    return '\n'.join(texto.strip().splitlines()[-n:])


def sonda(caminho):
    p = subprocess.run(['ffprobe', '-v', 'error', '-print_format', 'json', '-show_format', '-show_streams', caminho],
                       capture_output=True, text=True, timeout=60)
    if p.returncode != 0:
        raise RuntimeError(f'ffprobe falhou: {p.stderr[-300:]}')
    d = json.loads(p.stdout)
    v = next((s for s in d.get('streams', []) if s.get('codec_type') == 'video'), {})
    a = next((s for s in d.get('streams', []) if s.get('codec_type') == 'audio'), None)
    return {'duracao': round(float(d['format'].get('duration', 0)), 3), 'bytes': int(d['format'].get('size', 0)),
            'codec': v.get('codec_name'), 'largura': v.get('width'), 'altura': v.get('height'),
            'fps': v.get('r_frame_rate'), 'pix_fmt': v.get('pix_fmt'), 'audio': a.get('codec_name') if a else None}


def baixar(url, destino):
    if not url.startswith('https://'):
        raise Entrada('projeto_url precisa ser um link https')
    total = 0
    with urllib.request.urlopen(url, timeout=120) as r, open(destino, 'wb') as f:
        while bloco := r.read(1 << 20):
            total += len(bloco)
            if total > MAX_TAR:
                raise Entrada('o projeto passa de 50 MB')
            f.write(bloco)


def extrair(tar_path, destino):
    """Extrai só arquivos e pastas; recusa caminho absoluto, '..', links e dispositivos."""
    total = 0
    try:
        with tarfile.open(tar_path, 'r:gz') as t:
            membros = t.getmembers()
            for m in membros:
                partes = m.name.replace('\\', '/').split('/')
                if m.name.startswith('/') or '..' in partes or re.match(r'^[A-Za-z]:', m.name):
                    raise Entrada(f'caminho não permitido no tar: {m.name}')
                if not (m.isfile() or m.isdir()):
                    raise Entrada(f'tipo não permitido no tar (links/dispositivos): {m.name}')
                total += m.size
                if total > MAX_EXTRAIDO:
                    raise Entrada('o projeto extraído passa de 300 MB')
            for m in membros:
                m.mode = 0o755 if m.isdir() else 0o644
                m.uid = m.gid = 0
                t.extract(m, destino)
    except tarfile.TarError as e:
        raise Entrada(f'tar.gz inválido: {e}')
    if os.path.isfile(os.path.join(destino, 'index.html')):
        return destino
    subs = [d for d in os.listdir(destino) if os.path.isdir(os.path.join(destino, d))]
    if len(subs) == 1 and os.path.isfile(os.path.join(destino, subs[0], 'index.html')):
        return os.path.join(destino, subs[0])
    raise Entrada('index.html não encontrado na raiz do projeto')


def renderizar(projeto, saida, formato, qualidade, workers, timeout):
    """Roda `hyperframes render`. Devolve (segundos, resumo_do_log). Levanta RuntimeError com o fim do log em falha."""
    cmd = ['hyperframes', 'render', projeto, '-o', saida, '--format', formato]
    if qualidade:
        cmd += ['--quality', qualidade]
    if workers:
        cmd += ['--workers', str(workers)]
    t0 = time.time()
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, start_new_session=True)
    try:
        log, _ = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(p.pid, signal.SIGKILL)
        log, _ = p.communicate()
        raise RuntimeError(f'render passou do limite de {timeout // 60} min e foi cancelado\n{ultimas_linhas(log or "")}')
    seg = round(time.time() - t0, 2)
    if p.returncode != 0 or not os.path.isfile(saida):
        raise RuntimeError(f'render falhou (código {p.returncode})\n{ultimas_linhas(log)}')
    # linha de resumo do Hyperframes (modo de captura e tempos por etapa)
    resumo = next((l.strip() for l in reversed(log.splitlines()) if ' · ' in l and ('capture' in l or 'encode' in l)), None)
    return seg, resumo


# ---------- ações ----------
def acao_render(e):
    formato = str(e.get('formato') or 'mp4').lower()
    if formato not in FORMATOS:
        raise Entrada('formato precisa ser mp4, mov ou webm')
    qualidade = e.get('qualidade')
    if qualidade is not None and qualidade not in QUALIDADES:
        raise Entrada('qualidade precisa ser draft, looks, delivery, standard ou high')
    workers = validar_workers(e.get('workers'))
    if bool(e.get('projeto_url')) == bool(e.get('projeto_b64')):
        raise Entrada('envie exatamente um entre projeto_url e projeto_b64')
    upload = e.get('upload_url')
    if upload is not None and not str(upload).startswith('https://'):
        raise Entrada('upload_url precisa ser um link https')

    pasta = tempfile.mkdtemp(prefix='motion-')
    try:
        tar_path = os.path.join(pasta, 'projeto.tar.gz')
        if e.get('projeto_url'):
            baixar(str(e['projeto_url']), tar_path)
        else:
            if len(e['projeto_b64']) > MAX_TAR * 4 // 3 + 16:
                raise Entrada('o projeto passa de 50 MB')
            try:
                dados = base64.b64decode(e['projeto_b64'], validate=True)
            except Exception:
                raise Entrada('projeto_b64 não é base64 válido')
            if len(dados) > MAX_TAR:
                raise Entrada('o projeto passa de 50 MB')
            open(tar_path, 'wb').write(dados)
        projeto = extrair(tar_path, os.path.join(pasta, 'src'))
        os.remove(tar_path)
        saida = os.path.join(pasta, f'saida.{formato}')
        seg, resumo = renderizar(projeto, saida, formato, qualidade, workers, TIMEOUT_RENDER)
        info = sonda(saida)
        r = {'ok': True, 'segundos_render': seg, 'duracao_video': info['duracao'], 'bytes': info['bytes'], 'formato': formato,
             'resumo_render': resumo, **maquina()}
        if upload:
            with open(saida, 'rb') as f:
                req = urllib.request.Request(str(upload), data=f, method='PUT',
                                             headers={'Content-Type': FORMATOS[formato], 'Content-Length': str(info['bytes'])})
                urllib.request.urlopen(req, timeout=600).close()
            r['enviado'] = True
        elif info['bytes'] < MAX_B64_SAIDA:
            r['video_b64'] = base64.b64encode(open(saida, 'rb').read()).decode()
        else:
            return {'ok': False, 'erro': f'o vídeo tem {info["bytes"] // 1048576} MB (limite 15 MB sem upload_url); envie upload_url',
                    'segundos_render': seg, 'bytes': info['bytes']}
        return r
    finally:
        shutil.rmtree(pasta, ignore_errors=True)


def validar_workers(w):
    if w in (None, '', 'auto'):
        return None
    try:
        n = int(w)
    except (TypeError, ValueError):
        raise Entrada('workers precisa ser um número entre 1 e 64 ou "auto"')
    if not 1 <= n <= 64:
        raise Entrada('workers precisa ser um número entre 1 e 64 ou "auto"')
    return n


def acao_bench(e):
    workers = validar_workers(e.get('workers'))
    if not os.path.isdir(BENCH_DIR):
        return {'ok': False, 'erro': f'pasta de bench não encontrada: {BENCH_DIR}'}
    resultados = {}
    t_total = time.time()
    for nome in sorted(os.listdir(BENCH_DIR)):
        origem = os.path.join(BENCH_DIR, nome)
        if not os.path.isfile(os.path.join(origem, 'index.html')):
            continue
        pasta = tempfile.mkdtemp(prefix='bench-')
        try:
            proj = os.path.join(pasta, nome)
            shutil.copytree(origem, proj)
            saida = os.path.join(pasta, 'saida.mp4')
            try:
                seg, resumo = renderizar(proj, saida, 'mp4', None, workers, TIMEOUT_RENDER)
                info = sonda(saida)
                resultados[nome] = {'ok': True, 'segundos_render': seg, 'duracao_video': info['duracao'],
                                    'razao_tempo_real': round(info['duracao'] / seg, 2), 'ffprobe': info, 'resumo_render': resumo}
            except Exception as ex:
                resultados[nome] = {'ok': False, 'erro': str(ex)[-2500:]}
        finally:
            shutil.rmtree(pasta, ignore_errors=True)
    return {'ok': all(r['ok'] for r in resultados.values()) and bool(resultados), 'workers': workers or 'auto',
            'segundos_total': round(time.time() - t_total, 2), 'composicoes': resultados, **maquina()}


def handler(job):
    e = job.get('input') or {}
    try:
        acao = e.get('acao', 'render')
        if acao == 'render':
            return acao_render(e)
        if acao == 'bench':
            return acao_bench(e)
        return {'ok': False, 'erro': 'acao precisa ser "render" ou "bench"'}
    except Entrada as ex:
        return {'ok': False, 'erro': str(ex)}
    except Exception as ex:
        return {'ok': False, 'erro': str(ex)[-3000:]}


if __name__ == '__main__':
    if len(sys.argv) > 1 and sys.argv[1] == '--bench':
        w = sys.argv[2] if len(sys.argv) > 2 and sys.argv[2] else None
        print(json.dumps(handler({'input': {'acao': 'bench', 'workers': w}}), ensure_ascii=False, indent=2))
    else:
        import runpod
        runpod.serverless.start({'handler': handler})
