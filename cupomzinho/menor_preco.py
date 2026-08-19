#!/usr/bin/env python3
"""Cliente da API pública do Menor Preço — sob demanda, e identificado.

    python3 -m cupomzinho.menor_preco --termo "leite frimesa"
    python3 -m cupomzinho.menor_preco --gtin 7891167011779

As cinco regras, e nenhuma delas é estilo:

1. **Só quando alguém pede.** Nenhuma tarefa de fundo, nenhum agendamento. O
   Termo de Uso da CELEPAR proíbe sistema automatizado que faça, num dado
   período, mais solicitações do que seria "humanamente possível por meio de um
   navegador convencional", e proíbe uso para "aplicações de Pricing".
2. **User-Agent honesto.** `cupomzinho/<versão> (uso pessoal...)`, nunca
   imitação de navegador. Um cliente que se identifica pode ser reconhecido e
   barrado pela CELEPAR quando ela quiser; copiar cabeçalho de Chrome tira dela
   essa escolha. A diferença não é de volume, é de honestidade.
3. **Uma consulta por vez**, com espera entre elas e teto por hora.
4. **Toda resposta passa pelo sanidade.py** antes de virar linha no banco.
5. **Cache**: o preço de ontem responde a pergunta de hoje.

Medido em 19/08/2026: com o User-Agent padrão do Python a API devolve uma base
de teste com HTTP 200. Com o cliente identificado, dado limpo. Não era bloqueio
de IP — o navegador da mesma casa recebia dado bom o tempo todo.
"""
import json
import logging
import sqlite3
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

from .banco import centavos
from .config import config
from .sanidade import Veredito, avaliar, canario_passou

log = logging.getLogger("cupomzinho.menor_preco")

VERSAO = "0.1"
API = "https://menorpreco.notaparana.pr.gov.br/api/v1/produtos"
UA = f"cupomzinho/{VERSAO} (uso pessoal, consulta sob demanda; casa-srv)"

# Termo que não existe em produto nenhum. Resposta sadia devolve zero; resposta
# degradada devolve dezenas de "produtos" com esse nome exato.
CANARIO = "ZZQQXX FOOBAR 999G"

ESPERA_ENTRE_CONSULTAS = 2.0    # segundos


class TetoAtingido(Exception):
    """O teto de consultas por hora foi alcançado. Não é erro: é a regra 3."""


class RespostaSuspeita(Exception):
    """A API respondeu 200 com dado que não passa no sanidade.py."""

    def __init__(self, veredito: Veredito):
        super().__init__("; ".join(veredito.motivos))
        self.veredito = veredito


def _buscar_http(parametros: dict, tempo_limite: int = 45) -> dict:
    """A única função que fala com a rede. Injetável nos testes."""
    url = API + "?" + urllib.parse.urlencode(parametros)
    pedido = urllib.request.Request(
        url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(pedido, timeout=tempo_limite) as resposta:
        return json.load(resposta)


class MenorPreco:
    """Cliente com disciplina. `buscar` é a única porta de entrada."""

    def __init__(self, con: sqlite3.Connection, *, http=_buscar_http, dormir=time.sleep):
        self.con = con
        self.http = http
        self.dormir = dormir
        self.cfg = config()
        self._ultima = 0.0
        self._ultima_resposta: dict = {}

    # ── teto e diário ────────────────────────────────────────────────────────
    def consultas_na_ultima_hora(self) -> int:
        limite = (datetime.now() - timedelta(hours=1)).isoformat(sep=" ", timespec="seconds")
        return self.con.execute(
            "SELECT COUNT(*) FROM consulta_menor_preco WHERE feita_em > ?", (limite,)
        ).fetchone()[0]

    def _registrar(self, alvo: str, veredito: Veredito) -> None:
        with self.con:
            self.con.execute(
                "INSERT INTO consulta_menor_preco (feita_em, alvo, confiavel, motivos)"
                " VALUES (?,?,?,?)",
                (datetime.now().isoformat(sep=" ", timespec="seconds"), alvo,
                 1 if veredito.confiavel else 0,
                 "; ".join(veredito.motivos) or None))

    # ── a consulta ───────────────────────────────────────────────────────────
    def buscar(self, *, termo: str = "", gtin: str = "") -> list[dict]:
        """Uma consulta, validada. Levanta TetoAtingido ou RespostaSuspeita."""
        if self.consultas_na_ultima_hora() >= self.cfg.teto_consultas_hora:
            raise TetoAtingido(
                f"{self.cfg.teto_consultas_hora} consultas na última hora — "
                "o teto existe para esta app não virar o robô que o termo proíbe")

        parametros = {"local": self.cfg.local, "raio": self.cfg.raio_km,
                      "data": self.cfg.dias_janela, "offset": 0, "ordem": 0}
        parametros["gtin" if gtin else "termo"] = gtin or termo

        # espaça as consultas mesmo quando quem chama não espaçou
        atraso = ESPERA_ENTRE_CONSULTAS - (time.monotonic() - self._ultima)
        if atraso > 0:
            self.dormir(atraso)

        resposta = self.http(parametros)
        self._ultima = time.monotonic()
        self._ultima_resposta = resposta

        veredito = avaliar(resposta, gtin=gtin)
        self._registrar(gtin or termo, veredito)
        if not veredito:
            log.warning("resposta suspeita para %r: %s", gtin or termo, veredito.motivos)
            raise RespostaSuspeita(veredito)
        return resposta.get("produtos") or []

    def canario(self) -> bool:
        """Uma consulta que não colhe nada e diz se a API está sã.

        Vale a pena gastar uma requisição nisto antes de um lote: se a API está
        servindo base de teste, descobrir no canário custa uma consulta;
        descobrir item a item custa o lote inteiro e ainda suja o log.

        Aqui a regra é outra e mais dura que a de `buscar`: com um termo que
        não existe em produto nenhum, resposta sadia devolve ZERO. Qualquer
        resultado já é sinal.
        """
        try:
            self.buscar(termo=CANARIO)
        except RespostaSuspeita:
            return False
        resposta = self._ultima_resposta
        veredito = canario_passou(resposta, CANARIO)
        if not veredito:
            log.warning("canário reprovou: %s", veredito.motivos)
        return bool(veredito)


# ── gravação ─────────────────────────────────────────────────────────────────
def _cnpj_da_loja(con: sqlite3.Connection, nome: str, municipio: str) -> str | None:
    """Casa a loja do Menor Preço com uma em que a casa já comprou.

    A API não devolve CNPJ, então o pareamento é por nome + município. Só vale
    para as lojas dos cupons; para as outras o preço fica com o nome mesmo.
    """
    achado = con.execute(
        "SELECT cnpj FROM estabelecimento WHERE upper(razao) = ? AND upper(municipio) = ?",
        ((nome or "").upper(), (municipio or "").upper())).fetchone()
    return achado["cnpj"] if achado else None


def guardar(con: sqlite3.Connection, produto_id: int, produtos: list[dict]) -> int:
    """Grava os preços observados. Devolve quantas linhas entraram."""
    novas = 0
    with con:
        for p in produtos:
            e = p.get("estabelecimento") or {}
            cnpj = _cnpj_da_loja(con, e.get("nm_emp", ""), e.get("mun", ""))
            if cnpj:
                con.execute(
                    "UPDATE estabelecimento SET codigo_menor_preco = ?"
                    " WHERE cnpj = ? AND codigo_menor_preco IS NULL", (e.get("codigo"), cnpj))
            cursor = con.execute(
                """INSERT OR IGNORE INTO preco
                       (produto_id, cnpj, unitario_centavos, observado_em, fonte,
                        loja, municipio, codigo_menor_preco, distancia_km)
                   VALUES (?,?,?,?, 'menor_preco', ?,?,?,?)""",
                (produto_id, cnpj, centavos(p["valor"]), (p.get("datahora") or "")[:19],
                 e.get("nm_emp"), e.get("mun"), e.get("codigo"),
                 float(p["distkm"]) if p.get("distkm") else None))
            novas += cursor.rowcount
    return novas


def _main(argumentos: list[str]) -> int:
    import argparse
    from pathlib import Path

    from .banco import conectar

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--termo")
    parser.add_argument("--gtin")
    parser.add_argument("--canario", action="store_true")
    args = parser.parse_args(argumentos)

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    con = conectar(Path(config().banco))
    cliente = MenorPreco(con)

    if args.canario:
        print("API sadia" if cliente.canario() else "API DEGRADADA — servindo base de teste")
        return 0
    if not (args.termo or args.gtin):
        parser.error("use --termo, --gtin ou --canario")

    try:
        achados = cliente.buscar(termo=args.termo or "", gtin=args.gtin or "")
    except RespostaSuspeita as erro:
        print("resposta recusada pelo sanidade.py:", erro)
        return 1
    except TetoAtingido as erro:
        print("teto:", erro)
        return 1

    for p in achados[:20]:
        e = p["estabelecimento"]
        print(f"  R$ {p['valor']:>8}  {p['distkm']:>7} km  {p['desc'][:36]:<38}"
              f" {e['nm_emp'][:32]}")
    print(f"\n  {len(achados)} resultados · consultas na última hora: "
          f"{cliente.consultas_na_ultima_hora()}")
    return 0


if __name__ == "__main__":
    import sys
    raise SystemExit(_main(sys.argv[1:]))
