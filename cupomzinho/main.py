#!/usr/bin/env python3
"""O servidor do cupomzinho: a API e a PWA, no mesmo processo.

    uvicorn cupomzinho.main:app --host 0.0.0.0 --port 8090

Escuta só no IP da tailnet (quem publica a porta é o compose), então todo mundo
que chega já passou pelo Tailscale — falta apenas perguntar ao tailscaled quem
é. Ver identidade.py.
"""
import asyncio
import json
import logging
import sqlite3
import sys
from contextlib import asynccontextmanager
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import (
    cupons,
    enriquecimento,
    listas,
    menor_preco,
    relatorios,
    tarefas,
    unificacao,
)
from .banco import conectar
from .config import config
from .identidade import NaoIdentificado, quem_e
from .nfce_pr import buscar, chave_da_url, interpretar
from .qr import ler_qr

WEB = Path(__file__).parent / "web"
log = logging.getLogger("cupomzinho.main")


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


def _guardar_falha(bruto: bytes, nome: str) -> Path:
    """Guarda a imagem que não deu QR, para virar caso de teste."""
    pasta = config().dados / "falhas"
    pasta.mkdir(parents=True, exist_ok=True)
    sufixo = Path(nome).suffix or ".bin"
    alvo = pasta / f"{datetime.now():%Y-%m-%d_%H%M%S}{sufixo}"
    alvo.write_bytes(bruto)
    return alvo


def responder(dados) -> RespostaJSON:
    """Devolve a resposta JÁ SERIALIZADA, e é de propósito.

    Se a rota devolvesse um dict, o FastAPI passaria antes pelo jsonable_encoder
    dele — que transforma Decimal em FLOAT. Isso reintroduziria, na última
    curva, exatamente o erro de centavo que o banco em inteiros existe para
    evitar. Objeto Response o FastAPI entrega intacto.
    """
    return RespostaJSON(dados)


def configurar_log() -> None:
    """Faz o log da app aparecer no `docker logs`.

    O uvicorn monta a configuração de log dele e não adota logger de terceiros,
    então sem isto todo `log.info` da app some — e some justamente na hora de
    diagnosticar, que foi o que aconteceu com a primeira foto que falhou.
    """
    log = logging.getLogger("cupomzinho")
    if log.handlers:
        return
    saida = logging.StreamHandler(sys.stdout)
    saida.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s",
                                         "%H:%M:%S"))
    log.addHandler(saida)
    log.setLevel(logging.INFO)
    log.propagate = False


@asynccontextmanager
async def ciclo_de_vida(app: FastAPI):
    cfg = config()
    configurar_log()
    cfg.pasta_cupons.mkdir(parents=True, exist_ok=True)
    cfg.pasta_backups.mkdir(parents=True, exist_ok=True)
    app.state.con = conectar(cfg.banco)
    # O backup vive no ciclo de vida do servidor: sobe junto, desce junto.
    app.state.tarefas = [
        asyncio.create_task(tarefas.laco_de_backup(app.state.con, cfg.pasta_backups)),
    ]
    yield
    for tarefa in app.state.tarefas:
        tarefa.cancel()
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
    bruto = await foto.read()
    try:
        achados = ler_qr(bruto)
    except ValueError as erro:
        _guardar_falha(bruto, foto.filename or "foto")
        raise HTTPException(400, str(erro)) from erro

    for conteudo in achados:
        if chave_da_url(conteudo):
            return responder(_registrar(con, conteudo, quem, "foto"))

    # A foto que não deu certo fica guardada. Consertar leitor de código de
    # barras sem o arquivo que falhou é adivinhação; com ele, é teste.
    guardada = _guardar_falha(bruto, foto.filename or "foto")
    log.warning("QR não encontrado; foto guardada em %s", guardada)

    if achados:
        # Achou QR, mas não era de cupom — o cupom tem outros códigos impressos.
        raise HTTPException(
            422, "achei um código nessa foto, mas não é o QR da nota fiscal. "
                 "O certo é o quadradinho embaixo, perto da chave de acesso.")
    raise HTTPException(
        422, "não achei nenhum QR Code nessa foto. A foto ficou guardada no "
             "servidor para eu descobrir por quê — tente também colar o link.")


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


# ── unificar produtos ────────────────────────────────────────────────────────
class Unir(BaseModel):
    manter: int
    juntar: int


class Recusa(BaseModel):
    desc_a: str
    desc_b: str


@app.get("/api/unificar")
async def ver_candidatos(_: Morador, con: Banco):
    """Pares que parecem o mesmo produto. SUGESTÃO — nada acontece sozinho."""
    return responder({"candidatos": unificacao.candidatos(con)})


@app.post("/api/unificar")
async def unir(uniao: Unir, _: Morador, con: Banco):
    try:
        return responder(unificacao.unificar(con, uniao.manter, uniao.juntar))
    except ValueError as erro:
        raise HTTPException(400, str(erro)) from erro


@app.post("/api/unificar/recusar")
async def recusar_par(recusa: Recusa, _: Morador, con: Banco):
    """"Não são a mesma coisa" — para o par não voltar a ser sugerido."""
    unificacao.recusar(con, recusa.desc_a, recusa.desc_b)
    return responder({"ok": True})


# ── lista de compras ─────────────────────────────────────────────────────────
class NovaLista(BaseModel):
    nome: str = "compras"


class ItemDaLista(BaseModel):
    produto_id: int
    quantidade: Decimal = Decimal("1")


@app.get("/api/listas")
async def ver_listas(_: Morador, con: Banco):
    return responder({"listas": listas.listar(con), "sugestoes": listas.sugerir(con)})


@app.post("/api/listas")
async def nova_lista(dados: NovaLista, quem: Morador, con: Banco):
    return responder({"id": listas.criar(con, dados.nome, quem)})


@app.get("/api/lista/{lista_id}")
async def ver_lista(lista_id: int, _: Morador, con: Banco):
    return responder({"itens": listas.itens(con, lista_id),
                      "comparacao": listas.comparar(con, lista_id)})


@app.post("/api/lista/{lista_id}/item")
async def por_item(lista_id: int, item: ItemDaLista, _: Morador, con: Banco):
    listas.por_produto(con, lista_id, item.produto_id, item.quantidade)
    return responder({"ok": True})


@app.delete("/api/lista/{lista_id}/item/{produto_id}")
async def tirar_item(lista_id: int, produto_id: int, _: Morador, con: Banco):
    listas.tirar(con, lista_id, produto_id)
    return responder({"ok": True})


# Rota SÍNCRONA de propósito: o cliente do Menor Preço espaça as consultas com
# sleep, e num handler async isso travaria o laço de eventos inteiro. Sendo
# `def`, o FastAPI a roda numa thread e a PWA continua respondendo.
@app.post("/api/lista/{lista_id}/pesquisar")
def pesquisar_precos(lista_id: int, request: Request):
    """Consulta o Menor Preço para os itens da lista. **Só a pedido de gente.**

    É a única rota que fala com a API do estado, e ela existe porque alguém
    apertou um botão. Antes do lote vai o canário: se a API estiver servindo
    base de teste, descobrir aqui custa uma consulta em vez do lote inteiro.
    """
    con = request.app.state.con
    cliente = menor_preco.MenorPreco(con)
    relatorio = {"consultados": 0, "guardados": 0, "recusados": [], "sem_resultado": []}

    if not cliente.canario():
        raise HTTPException(
            503, "a API do Menor Preço está devolvendo base de teste agora — "
                 "nada foi gravado. Tente mais tarde.")

    for item in listas.itens(con, lista_id):
        try:
            achados = cliente.buscar(gtin=item["gtin"] or "",
                                     termo="" if item["gtin"] else item["descricao"])
        except menor_preco.TetoAtingido as erro:
            relatorio["teto"] = str(erro)
            break
        except menor_preco.RespostaSuspeita as erro:
            relatorio["recusados"].append({"item": item["descricao"], "porque": str(erro)})
            continue
        relatorio["consultados"] += 1
        if not achados:
            relatorio["sem_resultado"].append(item["descricao"])
            continue
        relatorio["guardados"] += menor_preco.guardar(con, item["produto_id"], achados)

    relatorio["comparacao"] = listas.comparar(con, lista_id)
    return responder(relatorio)


# ── descobrir código de barras ───────────────────────────────────────────────
@app.get("/api/enriquecer")
async def ver_pendentes(_: Morador, con: Banco):
    return responder({"pendentes": enriquecimento.pendentes(con)})


# Síncrona pelo mesmo motivo da pesquisa de preços: o cliente espaça as
# consultas com sleep, e num handler async isso travaria o laço de eventos.
@app.post("/api/enriquecer")
def enriquecer_produtos(request: Request, quantos: int = 5):
    """Descobre o código de barras dos produtos mais comprados. **A pedido.**

    `quantos` é baixo de propósito: cada produto custa uma consulta, e o teto
    por hora é o que mantém esta app do lado certo do termo de uso.
    """
    con = request.app.state.con
    cliente = menor_preco.MenorPreco(con)
    if not cliente.canario():
        raise HTTPException(
            503, "a API do Menor Preço está devolvendo base de teste agora — "
                 "nada foi gravado. Tente mais tarde.")

    relatorio = {"identificados": [], "sem_certeza": []}
    for pendente in enriquecimento.pendentes(con, quantos):
        try:
            r = enriquecimento.enriquecer(con, cliente, pendente["produto_id"])
        except menor_preco.TetoAtingido as erro:
            relatorio["teto"] = str(erro)
            break
        except menor_preco.RespostaSuspeita as erro:
            relatorio["sem_certeza"].append(
                {"descricao": pendente["descricao"], "motivo": f"resposta recusada: {erro}"})
            continue
        alvo = "identificados" if r["novo"] else "sem_certeza"
        relatorio[alvo].append({"descricao": r["descricao"], "gtin": r.get("gtin"),
                                "motivo": r["motivo"]})
    relatorio["restantes"] = len(enriquecimento.pendentes(con, 999))
    return responder(relatorio)


# ── a PWA ────────────────────────────────────────────────────────────────────
@app.get("/")
async def raiz():
    return FileResponse(WEB / "index.html")


app.mount("/", StaticFiles(directory=WEB), name="web")
