# Imagem própria porque esta app TEM dependências — e é justamente por isso que
# elas moram aqui dentro: o host do casa-srv continua sem nada instalado, que é
# a razão de ser da regra "só stdlib" de lá.
FROM python:3.13-slim AS base

# libzbar0 é exigência do pyzbar, que decodifica o QR do cupom a partir da foto.
# tesseract-ocr e tesseract-ocr-por são usados no OCR local de cupons sem NFC-e/QR.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libzbar0 tesseract-ocr tesseract-ocr-por \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# As dependências antes do código: mexer no código não invalida esta camada.
COPY pyproject.toml ./
COPY mise_en_prato/__init__.py ./mise_en_prato/
RUN pip install --no-cache-dir .

# ── dev: o mesmo ambiente, mais o ferramental de teste ───────────────────────
# É aqui que os testes rodam. O host não tem python3-venv nem pip, então o
# contêiner é o ambiente de desenvolvimento, não só o de produção.
FROM base AS dev
# Lê o extra [dev] do pyproject em vez de repetir a lista aqui: manter as duas
# em sincronia na mão já custou um "ModuleNotFoundError" que não era do código.
RUN pip install --no-cache-dir ".[dev]"
CMD ["pytest", "-q"]

# ── produção ─────────────────────────────────────────────────────────────────
FROM base AS producao
COPY mise_en_prato/ ./mise_en_prato/
# uid 1000 = miguel, o mesmo dono de /mnt/data
USER 1000:1000
CMD ["python", "-m", "mise_en_prato"]
