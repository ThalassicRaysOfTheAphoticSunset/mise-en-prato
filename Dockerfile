# Imagem própria porque esta app TEM dependências — e é justamente por isso que
# elas moram aqui dentro: o host do casa-srv continua sem nada instalado, que é
# a razão de ser da regra "só stdlib" de lá.
FROM python:3.13-slim AS base

# libzbar0 é exigência do pyzbar, que decodifica o QR do cupom a partir da foto.
# É o que faz o iPhone funcionar: o Safari não tem BarcodeDetector, então quem
# lê o código é o servidor, não o navegador.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libzbar0 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# As dependências antes do código: mexer no código não invalida esta camada.
COPY pyproject.toml ./
COPY cupomzinho/__init__.py ./cupomzinho/
RUN pip install --no-cache-dir .

# ── dev: o mesmo ambiente, mais o ferramental de teste ───────────────────────
# É aqui que os testes rodam. O host não tem python3-venv nem pip, então o
# contêiner é o ambiente de desenvolvimento, não só o de produção.
FROM base AS dev
RUN pip install --no-cache-dir pytest pytest-asyncio ruff
CMD ["pytest", "-q"]

# ── produção ─────────────────────────────────────────────────────────────────
FROM base AS producao
COPY cupomzinho/ ./cupomzinho/
# uid 1000 = miguel, o mesmo dono de /mnt/data
USER 1000:1000
CMD ["uvicorn", "cupomzinho.main:app", "--host", "0.0.0.0", "--port", "8090"]
