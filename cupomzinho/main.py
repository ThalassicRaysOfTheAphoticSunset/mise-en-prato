#!/usr/bin/env python3
"""O servidor do cupomzinho: a API e a PWA, no mesmo processo.

    uvicorn cupomzinho.main:app --host 0.0.0.0 --port 8090

Escuta só no IP da tailnet (quem publica a porta é o compose), então todo mundo
que chega já passou pelo Tailscale — falta apenas perguntar ao tailscaled quem
é. Ver identidade.py.
"""
import json
import sqlite3
from contextlib import asynccontextmanager
from decimal import Decimal
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import cupons, relatorios
from .banco import conectar
from .config import config
from .identidade import NaoIdentificado, quem_e
from .nfce_pr import buscar, chave_da_url, interpretar

WEB = Path(__file__).parent / "web"


class Codificador(json.JSONEncoder):
    """Decimal vira string no JSON, não float.

    Float aqui reintroduziria justamente o erro de centavo que o banco em
    inteiros existe para evitar; a tela formata a string como quiser.
    """

    def default(self, o):
        return str(o) if isinstance(o, Decimal) else super().default(o)


class RespostaJSON(JSONResponse):
    def render(self, content) -> bytes:
        return json.dumps(content, cls=Codificador, ensure_ascii=False).encode("utf-8")


def responder(dados) -> RespostaJSON:
    """Devolve a resposta JÁ SERIALIZADA, e é de propósito.

    Se a rota devolvesse um dict, o FastAPI passaria antes pelo jsonable_encoder
    dele — que transforma Decimal em FLOAT. Isso reintroduziria, na última
    curva, exatamente o erro de centavo que o banco em inteiros existe para
    evitar. Objeto Response o FastAPI entrega intacto.
    """
    return RespostaJSON(dados)


@asynccontextmanager
async def ciclo_de_vida(app: FastAPI):
    cfg = config()
    cfg.pasta_cupons.mkdir(parents=True, exist_ok=True)
    cfg.pasta_backups.mkdir(parents=True, exist_ok=True)
    app.state.con = conectar(cfg.banco)
    yield
    app.state.con.close()


app = FastAPI(title="cupomzinho", default_response_class=RespostaJSON, lifespan=ciclo_de_vida)


def banco(request: Request) -> sqlite3.Connection:
    return request.app.state.con


async def morador(request: Request) -> str:
    """Quem está pedindo. 403 para quem não é morador — inclusive visita da tailnet."""
    cliente = request.client
    if cliente is None:
        raise HTTPException(403, "origem desconhecida")
    try:
        return await quem_e(cliente.host, cliente.port)
    except NaoIdentificado as erro:
        raise HTTPException(403, str(erro)) from erro


# Apelidos das dependências. Além de deixarem a assinatura das rotas curta,
# são o estilo que o FastAPI recomenda hoje — Depends() no valor padrão do
# argumento funciona, mas é uma armadilha conhecida de Python.
Banco = Annotated[sqlite3.Connection, Depends(banco)]
Morador = Annotated[str, Depends(morador)]


# ── envio de cupom ───────────────────────────────────────────────────────────
class EnvioCupom(BaseModel):
    url: str = ""      # a URL inteira do QR Code (é dela que sai o hash de segurança)
    chave: str = ""    # só serve para reconsultar cupom já guardado


def _registrar(con, url: str, quem: str, origem: str) -> dict:
    """Busca, interpreta e grava. Idempotente pela chave de acesso."""
    chave = chave_da_url(url)
    if not chave:
        raise HTTPException(400, "não encontrei uma chave de acesso de 44 dígitos nesse link")

    if cupons.existe(con, chave):
        guardado = cupons.ler(con, chave)
        return {"novo": False, "cupom": guardado}

    try:
        html = buscar(url)
    except Exception as erro:
        raise HTTPException(502, f"a SEFAZ não respondeu: {erro}") from erro

    # O HTML cru é gravado ANTES de interpretar. Quando a SEFAZ mexer no
    # layout, o reprocessamento roda em cima disto e nenhum cupom se perde.
    (config().pasta_cupons / f"{chave}.html").write_text(html, encoding="utf-8")

    cupom = interpretar(html)
    if cupom.chave != chave:
        cupom.avisos.append(f"a chave da página ({cupom.chave}) difere da chave do QR ({chave})")
        cupom.chave = chave
    cupons.gravar(con, cupom, morador=quem, origem=origem, url_qr=url)
    return {"novo": True, "cupom": cupons.ler(con, chave)}


@app.post("/api/cupom")
async def enviar_cupom(envio: EnvioCupom, quem: Morador, con: Banco):
    if envio.url:
        return responder(_registrar(con, envio.url, quem, "qrcode"))
    if envio.chave:
        guardado = cupons.ler(con, envio.chave.replace(" ", ""))
        if not guardado:
            raise HTTPException(
                404,
                "não tenho esse cupom. A chave sozinha não basta para consultar a SEFAZ: "
                "o portal exige o link inteiro do QR Code, com o hash de segurança.")
        return responder({"novo": False, "cupom": guardado})
    raise HTTPException(400, "mande a url do QR Code ou a chave de um cupom já guardado")


@app.post("/api/cupom/foto")
async def enviar_foto(quem: Morador, con: Banco, foto: Annotated[UploadFile, File()]):
    """Foto do QR Code do cupom. Quem decodifica é o servidor.

    É isto que faz o iPhone funcionar: o Safari não tem BarcodeDetector, então
    depender do navegador deixaria metade da casa de fora.
    """
    from io import BytesIO

    from PIL import Image
    from pyzbar.pyzbar import decode

    try:
        imagem = Image.open(BytesIO(await foto.read()))
    except Exception as erro:
        raise HTTPException(400, f"não consegui abrir a imagem: {erro}") from erro

    achados = [c.data.decode("utf-8", "replace") for c in decode(imagem)]
    for conteudo in achados:
        if chave_da_url(conteudo):
            return responder(_registrar(con, conteudo, quem, "foto"))
    raise HTTPException(
        422, "não achei um QR Code de NFC-e nessa foto — tente enquadrar só o quadradinho")


# ── consulta ─────────────────────────────────────────────────────────────────
@app.get("/api/saude")
async def saude(con: Banco):
    """Sem autenticação: é o que o Uptime Kuma bate. Não devolve dado da casa."""
    return responder({"ok": True,
                      "cupons": con.execute("SELECT COUNT(*) FROM cupom").fetchone()[0]})


@app.get("/api/eu")
async def eu(quem: Morador):
    return responder({"morador": quem})


@app.get("/api/cupons")
async def listar_cupons(_: Morador, con: Banco, limite: int = 20):
    return responder({"cupons": cupons.recentes(con, limite),
                      "revisao": cupons.fila_de_revisao(con)})


@app.get("/api/cupom/{chave}")
async def ver_cupom(chave: str, _: Morador, con: Banco):
    guardado = cupons.ler(con, chave)
    if not guardado:
        raise HTTPException(404, "cupom não encontrado")
    return responder(guardado)


@app.get("/api/gastos")
async def gastos(_: Morador, con: Banco, mes: str = ""):
    return responder({"resumo": relatorios.resumo_do_mes(con, mes or None),
                      "meses": relatorios.meses_com_gasto(con)})


@app.get("/api/produtos")
async def listar_produtos(_: Morador, con: Banco, busca: str = ""):
    return responder({"produtos": relatorios.produtos(con, busca)})


@app.get("/api/produto/{produto_id}")
async def ver_produto(produto_id: int, _: Morador, con: Banco):
    achado = relatorios.historico_do_produto(con, produto_id)
    if not achado:
        raise HTTPException(404, "produto não encontrado")
    return responder(achado)


# ── a PWA ────────────────────────────────────────────────────────────────────
@app.get("/")
async def raiz():
    return FileResponse(WEB / "index.html")


app.mount("/", StaticFiles(directory=WEB), name="web")
