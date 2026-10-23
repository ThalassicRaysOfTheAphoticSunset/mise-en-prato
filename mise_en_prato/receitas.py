#!/usr/bin/env python3
"""Módulo de gestão de receitas culinárias e ingredientes no mise-en-prato.

Permite planejar e registrar receitas da casa integrando os ingredientes com
o histórico de compras e códigos de barras (GTIN/EAN).

Cálculos e quantidades nunca usam ponto flutuante (float):
- No banco: quantidade vive como INTEGER de centavos (escala 100), garantindo
  armazenamento exato e sem perda de precisão.
- No Python: quantidade trafega estritamente como Decimal (ou int),
  permitindo medições exatas (ex: 250 g, 1.5 xícara, 3 unidades).
"""

import io
import re
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from PIL import Image, ImageOps


def centavos_quantidade(quantidade: Decimal | int | str) -> int:
    """Converte quantidade Decimal/int/str para centavos inteiros (INTEGER).

    Lança TypeError caso um valor float seja fornecido, assegurando que
    erros de precisão de ponto flutuante sejam impedidos na raiz.
    """
    if isinstance(quantidade, float):
        raise TypeError("Valores float não são permitidos; use Decimal ou int para precisão.")
    if isinstance(quantidade, (int, str, Decimal)):
        dec = Decimal(str(quantidade))
        if dec <= Decimal("0"):
            raise ValueError("A quantidade deve ser estritamente maior que zero.")
        return int((dec * 100).quantize(Decimal("1")))
    raise TypeError(f"Tipo não suportado para quantidade: {type(quantidade).__name__}")


def decimal_quantidade(centavos: int) -> Decimal:
    """Converte centavos inteiros do banco para Decimal sem ponto flutuante.

    Se o valor representar um número inteiro (ex: 25000 -> 250), devolve
    Decimal("250"). Se contiver casas fracionárias (ex: 150 -> 1.5), normaliza
    mantendo a representação decimal enxuta.
    """
    val = Decimal(centavos) / Decimal(100)
    if val == val.to_integral():
        return Decimal(int(val))
    return val.normalize()


def formatar_fracao_culinaria(quantidade: Decimal | int | str) -> str:
    """Formata quantidade Decimal para frações culinárias amigáveis na tela.

    Exemplos:
      Decimal("0.5")  -> "½"
      Decimal("1.5")  -> "1 ½"
      Decimal("0.25") -> "¼"
      Decimal("0.75") -> "¾"
      Decimal("2")    -> "2"
      Decimal("250")  -> "250"
    """
    dec = Decimal(str(quantidade))
    inteiro = int(dec)
    fracao = (dec - inteiro).quantize(Decimal("0.01"))

    mapa_fracoes = {
        Decimal("0.50"): "½",
        Decimal("0.25"): "¼",
        Decimal("0.75"): "¾",
        Decimal("0.33"): "⅓",
        Decimal("0.67"): "⅔",
    }
    simbolo_fracao = mapa_fracoes.get(fracao)

    if simbolo_fracao:
        return f"{inteiro} {simbolo_fracao}".strip() if inteiro > 0 else simbolo_fracao
    if dec == dec.to_integral():
        return str(int(dec))
    return str(dec.normalize()).replace(".", ",")


@dataclass
class Ingrediente:
    id: int | None
    receita_id: int
    nome_ingrediente: str
    quantidade: Decimal
    unidade: str
    gtin_associado: str | None = None

    def __post_init__(self) -> None:
        if isinstance(self.quantidade, float):
            raise TypeError("Floats não são permitidos para quantidade; use Decimal ou int.")
        if not isinstance(self.quantidade, Decimal):
            self.quantidade = Decimal(str(self.quantidade))
        if self.quantidade <= Decimal("0"):
            raise ValueError("A quantidade deve ser maior que zero.")
        self.nome_ingrediente = self.nome_ingrediente.strip()
        if not self.nome_ingrediente:
            raise ValueError("O nome do ingrediente não pode ser vazio.")
        self.unidade = self.unidade.strip()
        if not self.unidade:
            raise ValueError("A unidade não pode ser vazia.")

    @property
    def quantidade_centavos(self) -> int:
        """Retorna a quantidade em centavos inteiros (ex: Decimal('1.5') -> 150)."""
        return int((self.quantidade * 100).quantize(Decimal("1")))

    @property
    def quantidade_formatada(self) -> str:
        """Retorna a quantidade formatada em fração culinária legível."""
        return formatar_fracao_culinaria(self.quantidade)

    def __getitem__(self, item: str) -> Any:
        return getattr(self, item)


@dataclass
class Receita:
    id: int | None
    nome: str
    porcoes: int = 1
    tempo_preparo_min: int | None = None
    instrucoes: str | None = None
    criada_em: str = field(
        default_factory=lambda: datetime.now().isoformat(sep=" ", timespec="seconds")
    )
    categoria: str | None = None
    foto_caminho: str | None = None
    ingredientes: list[Ingrediente] = field(default_factory=list)

    def __post_init__(self) -> None:
        if isinstance(self.porcoes, float):
            raise TypeError("Porções devem ser um número inteiro, sem float.")
        self.porcoes = int(self.porcoes)
        if self.porcoes <= 0:
            raise ValueError("O número de porções deve ser maior que zero.")

        if self.tempo_preparo_min is not None:
            if isinstance(self.tempo_preparo_min, float):
                raise TypeError("Tempo de preparo deve ser um número inteiro em minutos.")
            self.tempo_preparo_min = int(self.tempo_preparo_min)
            if self.tempo_preparo_min < 0:
                raise ValueError("Tempo de preparo não pode ser negativo.")

        self.nome = self.nome.strip()
        if not self.nome:
            raise ValueError("O nome da receita não pode ser vazio.")

        if self.categoria:
            self.categoria = self.categoria.strip() or None

        if self.foto_caminho:
            self.foto_caminho = self.foto_caminho.strip() or None

    def __getitem__(self, item: str) -> Any:
        return getattr(self, item)


# ── Parser de Ingredientes (Texto Livre -> Estruturado) ──────────────────────

UNIDADES_CONHECIDAS = {
    # Volume / Medidas Culinárias
    "xicara": "xícara",
    "xicaras": "xícara",
    "xícara": "xícara",
    "xícaras": "xícara",
    "xic": "xícara",
    "xic.": "xícara",
    "copo": "copo",
    "copos": "copo",
    "colher de sopa": "colher de sopa",
    "colheres de sopa": "colher de sopa",
    "colher (sopa)": "colher de sopa",
    "c. sopa": "colher de sopa",
    "cs": "colher de sopa",
    "colher de cha": "colher de chá",
    "colheres de cha": "colher de chá",
    "colher de chá": "colher de chá",
    "colheres de chá": "colher de chá",
    "colher (cha)": "colher de chá",
    "colher (chá)": "colher de chá",
    "c. cha": "colher de chá",
    "c. chá": "colher de chá",
    "colher de sobremesa": "colher de sobremesa",
    "colheres de sobremesa": "colher de sobremesa",
    "colher de cafe": "colher de café",
    "colher de café": "colher de café",
    # Massa / Peso
    "kg": "kg",
    "kilo": "kg",
    "kilos": "kg",
    "quilo": "kg",
    "quilos": "kg",
    "g": "g",
    "gr": "g",
    "grs": "g",
    "grama": "g",
    "gramas": "g",
    "mg": "mg",
    # Volume métrico
    "l": "litro",
    "litro": "litro",
    "litros": "litro",
    "ml": "ml",
    "mls": "ml",
    # Unidades contáveis / Formatos
    "unidade": "unidade",
    "unidades": "unidade",
    "un": "unidade",
    "un.": "unidade",
    "und": "unidade",
    "dente": "dente",
    "dentes": "dente",
    "fatia": "fatia",
    "fatias": "fatia",
    "pitada": "pitada",
    "pitadas": "pitada",
    "lata": "lata",
    "latas": "lata",
    "caixa": "caixa",
    "caixas": "caixa",
    "pct": "pacote",
    "pacote": "pacote",
    "pacotes": "pacote",
    "folha": "folha",
    "folhas": "folha",
    "ramo": "ramo",
    "ramos": "ramo",
    "a gosto": "a gosto",
}

UNICODE_FRACOES = {
    "½": Decimal("0.5"),
    "¼": Decimal("0.25"),
    "¾": Decimal("0.75"),
    "⅓": Decimal("0.33"),
    "⅔": Decimal("0.67"),
    "⅛": Decimal("0.125"),
}


def _extrair_numero(texto: str) -> tuple[Decimal | None, str]:
    """Extrai valor numérico (inteiro, decimal, fração composta ou unicode) do início do texto."""
    texto = texto.strip()

    # Fração unicode isolada ou composta: "1 ½" ou "½"
    for uni, val in UNICODE_FRACOES.items():
        if texto.startswith(uni):
            return val, texto[len(uni) :].strip()
        m_comp = re.match(r"^(\d+)\s*" + re.escape(uni) + r"(.*)$", texto)
        if m_comp:
            inteiro = Decimal(m_comp.group(1))
            resto = m_comp.group(2).strip()
            return inteiro + val, resto

    # Fração composta textual: "1 1/2" ou "1-1/2"
    m_frac_comp = re.match(r"^(\d+)[\s-]+(\d+)/(\d+)(.*)$", texto)
    if m_frac_comp:
        inteiro = Decimal(m_frac_comp.group(1))
        num = Decimal(m_frac_comp.group(2))
        den = Decimal(m_frac_comp.group(3))
        resto = m_frac_comp.group(4).strip()
        if den > 0:
            return (inteiro + (num / den)).quantize(Decimal("0.01")), resto

    # Fração simples: "1/2" ou "3/4"
    m_frac = re.match(r"^(\d+)/(\d+)(.*)$", texto)
    if m_frac:
        num = Decimal(m_frac.group(1))
        den = Decimal(m_frac.group(2))
        resto = m_frac.group(3).strip()
        if den > 0:
            return (num / den).quantize(Decimal("0.01")), resto

    # Número decimal ou inteiro (com ponto ou vírgula): "1,5", "250", "0.5"
    m_num = re.match(r"^(\d+(?:[.,]\d+)?)(.*)$", texto)
    if m_num:
        num_str = m_num.group(1).replace(",", ".")
        resto = m_num.group(2).strip()
        try:
            return Decimal(num_str), resto
        except Exception:
            pass

    return None, texto


def parse_linha_ingrediente(linha: str) -> dict[str, Any] | None:
    """Interpreta uma linha de texto livre extraindo quantidade, unidade e nome.

    Exemplos:
      "2 xícaras de farinha de trigo" -> quantidade=2, unidade="xícara", nome="farinha de trigo"
      "500g de carne moída"           -> quantidade=500, unidade="g", nome="carne moída"
      "3 ovos"                        -> quantidade=3, unidade="unidade", nome="ovos"
      "Sal e pimenta a gosto"         -> quantidade=1, unidade="a gosto", nome="Sal e pimenta"
    """
    # Remove marcadores de lista como "-", "*", "•" ou numeração como "1.", "1)"
    linha_limpa = re.sub(r"^\s*(?:[-*•]|\d+[.)-])\s*", "", linha).strip()
    if not linha_limpa:
        return None

    # Caso especial: "a gosto" ou "q.b."
    if re.search(r"\b(a gosto|q\.?b\.?)\b", linha_limpa, flags=re.IGNORECASE):
        nome = re.sub(r"\b(a gosto|q\.?b\.?)\b", "", linha_limpa, flags=re.IGNORECASE).strip()
        nome = re.sub(r"\s+", " ", nome).strip(" -:,")
        return {
            "nome_ingrediente": nome or linha_limpa,
            "quantidade": Decimal("1"),
            "unidade": "a gosto",
            "gtin_associado": None,
        }

    # Verifica se quantidade e unidade curta estão coladas sem espaço (ex: "500g", "1kg", "200ml")
    m_colado = re.match(r"^(\d+(?:[.,]\d+)?)([a-zA-Z]+)(.*)$", linha_limpa)
    if m_colado:
        pos_num = m_colado.group(1).replace(",", ".")
        pos_unid = m_colado.group(2).lower()
        resto = m_colado.group(3).strip()
        if pos_unid in ("g", "gr", "kg", "ml", "l", "mg"):
            nome = re.sub(r"^(de|da|do|dos|das)\s+", "", resto, flags=re.IGNORECASE).strip()
            return {
                "nome_ingrediente": nome or linha_limpa,
                "quantidade": Decimal(pos_num),
                "unidade": UNIDADES_CONHECIDAS[pos_unid],
                "gtin_associado": None,
            }

    # Extrai o valor numérico inicial
    qtd, resto = _extrair_numero(linha_limpa)

    if qtd is None:
        # Nenhum número detectado: assume 1 unidade
        return {
            "nome_ingrediente": linha_limpa,
            "quantidade": Decimal("1"),
            "unidade": "unidade",
            "gtin_associado": None,
        }

    # Procura unidade de medida conhecida logo após o número
    unidade_encontrada = "unidade"
    nome_final = resto

    # Testa as unidades mais longas primeiro (ex: "colher de sopa" antes de "colher")
    unidades_ordenadas = sorted(UNIDADES_CONHECIDAS.keys(), key=len, reverse=True)
    resto_lower = resto.lower()

    for u_chave in unidades_ordenadas:
        padrao = r"^" + re.escape(u_chave) + r"(?:\b|\s|$)"
        if re.search(padrao, resto_lower):
            unidade_encontrada = UNIDADES_CONHECIDAS[u_chave]
            nome_final = resto[len(u_chave) :].strip()
            break

    # Remove preposições de ligação: "de", "da", "do"
    nome_final = re.sub(r"^(de|da|do|dos|das)\s+", "", nome_final, flags=re.IGNORECASE).strip()
    nome_final = re.sub(r"\s+", " ", nome_final).strip(" -:,")

    return {
        "nome_ingrediente": nome_final or linha_limpa,
        "quantidade": qtd,
        "unidade": unidade_encontrada,
        "gtin_associado": None,
    }


def parse_texto_ingredientes(texto: str) -> list[dict[str, Any]]:
    """Recebe bloco de texto com múltiplos ingredientes e devolve a lista parseada."""
    itens = []
    for linha in texto.strip().splitlines():
        item = parse_linha_ingrediente(linha)
        if item:
            itens.append(item)
    return itens


# ── Processamento de Fotos (Pillow -> WebP) ──────────────────────────────────


def processar_foto(conteudo_bytes: bytes, max_dim: int = 1200, qualidade: int = 82) -> bytes:
    """Valida, corrige orientação EXIF, redimensiona e converte imagem para WebP."""
    try:
        img = Image.open(io.BytesIO(conteudo_bytes))
    except Exception as e:
        raise ValueError(f"Arquivo enviado não é uma imagem válida: {e}") from e

    # Corrige orientação de smartphones
    img = ImageOps.exif_transpose(img)

    # Converte para RGB se necessário
    if img.mode in ("RGBA", "LA", "P"):
        fundo = Image.new("RGB", img.size, (255, 255, 255))
        if img.mode == "P":
            img = img.convert("RGBA")
        fundo.paste(img, mask=img.split()[-1] if "A" in img.mode else None)
        img = fundo
    elif img.mode != "RGB":
        img = img.convert("RGB")

    # Redimensiona proporcionalmente mantendo aspecto
    img.thumbnail((max_dim, max_dim), Image.Resampling.LANCZOS)

    saida = io.BytesIO()
    img.save(saida, format="WEBP", quality=qualidade, method=6)
    return saida.getvalue()


def salvar_foto_receita(
    con: sqlite3.Connection,
    pasta_fotos: Path,
    receita_id: int,
    conteudo_bytes: bytes,
) -> str:
    """Processa e salva foto de capa da receita em WebP, atualizando o banco."""
    rec = obter_receita(con, receita_id)
    if rec is None:
        raise ValueError(f"Receita {receita_id} não encontrada.")

    pasta_fotos.mkdir(parents=True, exist_ok=True)

    # Remove foto anterior se existir
    if rec.foto_caminho:
        foto_antiga = pasta_fotos / Path(rec.foto_caminho).name
        if foto_antiga.exists():
            try:
                foto_antiga.unlink()
            except OSError:
                pass

    dados_webp = processar_foto(conteudo_bytes)
    ts = int(datetime.now().timestamp())
    nome_arquivo = f"{receita_id}_{ts}.webp"
    caminho_final = pasta_fotos / nome_arquivo
    caminho_final.write_bytes(dados_webp)

    foto_relativa = f"receitas/fotos/{nome_arquivo}"
    with con:
        con.execute(
            "UPDATE receitas SET foto_caminho = ? WHERE id = ?",
            (foto_relativa, receita_id),
        )

    return foto_relativa


def remover_foto_receita(
    con: sqlite3.Connection,
    pasta_fotos: Path,
    receita_id: int,
) -> bool:
    """Remove a foto da receita do disco e limpa o registro no banco."""
    rec = obter_receita(con, receita_id)
    if not rec or not rec.foto_caminho:
        return False

    arquivo = pasta_fotos / Path(rec.foto_caminho).name
    if arquivo.exists():
        try:
            arquivo.unlink()
        except OSError:
            pass

    with con:
        con.execute("UPDATE receitas SET foto_caminho = NULL WHERE id = ?", (receita_id,))
    return True


# ── Operações de Banco de Dados ──────────────────────────────────────────────


def criar_receita(
    con: sqlite3.Connection,
    nome: str | Receita,
    porcoes: int = 1,
    tempo_preparo_min: int | None = None,
    instrucoes: str | None = None,
    criada_em: str | datetime | None = None,
    categoria: str | None = None,
    foto_caminho: str | None = None,
    ingredientes: list[Ingrediente | dict[str, Any]] | None = None,
) -> int:
    """Cria uma nova receita no banco de dados e retorna seu ID."""
    if isinstance(nome, Receita):
        obj = nome
        nome_str = obj.nome
        porcoes = obj.porcoes
        tempo_preparo_min = obj.tempo_preparo_min
        instrucoes = obj.instrucoes
        criada_em_str = obj.criada_em
        categoria_str = obj.categoria
        foto_caminho_str = obj.foto_caminho
        ingredientes_lista = obj.ingredientes or ingredientes or []
    else:
        if isinstance(porcoes, float) or isinstance(tempo_preparo_min, float):
            raise TypeError("Valores numéricos de porções e tempo não podem ser float.")
        nome_str = str(nome).strip()
        if not nome_str:
            raise ValueError("O nome da receita não pode ser vazio.")
        if porcoes <= 0:
            raise ValueError("O número de porções deve ser maior que zero.")
        if tempo_preparo_min is not None and tempo_preparo_min < 0:
            raise ValueError("Tempo de preparo não pode ser negativo.")

        if criada_em is None:
            criada_em_str = datetime.now().isoformat(sep=" ", timespec="seconds")
        elif isinstance(criada_em, datetime):
            criada_em_str = criada_em.isoformat(sep=" ", timespec="seconds")
        else:
            criada_em_str = str(criada_em)

        categoria_str = categoria.strip() if categoria else None
        foto_caminho_str = foto_caminho.strip() if foto_caminho else None
        ingredientes_lista = ingredientes or []

    with con:
        cursor = con.execute(
            """
            INSERT INTO receitas (
                nome, porcoes, tempo_preparo_min, instrucoes, criada_em, categoria, foto_caminho
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                nome_str,
                porcoes,
                tempo_preparo_min,
                instrucoes,
                criada_em_str,
                categoria_str,
                foto_caminho_str,
            ),
        )
        receita_id = cursor.lastrowid
        if receita_id is None:
            raise RuntimeError("Falha ao obter ID da receita criada.")

        for item in ingredientes_lista:
            if isinstance(item, Ingrediente):
                adicionar_ingrediente(
                    con,
                    receita_id,
                    item.nome_ingrediente,
                    item.quantidade,
                    item.unidade,
                    item.gtin_associado,
                )
            elif isinstance(item, dict):
                adicionar_ingrediente(
                    con,
                    receita_id,
                    item["nome_ingrediente"],
                    item["quantidade"],
                    item["unidade"],
                    item.get("gtin_associado"),
                )

    return receita_id


def atualizar_receita(
    con: sqlite3.Connection,
    receita_id: int,
    nome: str,
    porcoes: int = 1,
    tempo_preparo_min: int | None = None,
    instrucoes: str | None = None,
    categoria: str | None = None,
    ingredientes: list[Ingrediente | dict[str, Any]] | None = None,
) -> Receita:
    """Atualiza dados cadastrais de uma receita e sincroniza seus ingredientes."""
    rec = obter_receita(con, receita_id)
    if rec is None:
        raise ValueError(f"Receita {receita_id} não encontrada.")

    if isinstance(porcoes, float) or isinstance(tempo_preparo_min, float):
        raise TypeError("Valores numéricos de porções e tempo não podem ser float.")
    nome_str = str(nome).strip()
    if not nome_str:
        raise ValueError("O nome da receita não pode ser vazio.")
    if porcoes <= 0:
        raise ValueError("O número de porções deve ser maior que zero.")
    if tempo_preparo_min is not None and tempo_preparo_min < 0:
        raise ValueError("Tempo de preparo não pode ser negativo.")

    categoria_str = categoria.strip() if categoria else None

    with con:
        con.execute(
            """
            UPDATE receitas
               SET nome = ?, porcoes = ?, tempo_preparo_min = ?, instrucoes = ?, categoria = ?
             WHERE id = ?
            """,
            (
                nome_str,
                int(porcoes),
                int(tempo_preparo_min) if tempo_preparo_min is not None else None,
                instrucoes,
                categoria_str,
                receita_id,
            ),
        )

        if ingredientes is not None:
            con.execute("DELETE FROM ingredientes WHERE receita_id = ?", (receita_id,))
            for item in ingredientes:
                if isinstance(item, Ingrediente):
                    adicionar_ingrediente(
                        con,
                        receita_id,
                        item.nome_ingrediente,
                        item.quantidade,
                        item.unidade,
                        item.gtin_associado,
                    )
                elif isinstance(item, dict):
                    adicionar_ingrediente(
                        con,
                        receita_id,
                        item["nome_ingrediente"],
                        item["quantidade"],
                        item["unidade"],
                        item.get("gtin_associado"),
                    )

    resultado = obter_receita(con, receita_id)
    if resultado is None:
        raise RuntimeError("Erro ao obter receita atualizada.")
    return resultado


def adicionar_ingrediente(
    con: sqlite3.Connection,
    receita_id: int,
    nome_ingrediente: str | Ingrediente,
    quantidade: Decimal | int | str | None = None,
    unidade: str | None = None,
    gtin_associado: str | None = None,
) -> int:
    """Adiciona um ingrediente a uma receita existente."""
    if isinstance(nome_ingrediente, Ingrediente):
        ing = nome_ingrediente
        nome_str = ing.nome_ingrediente
        qtd_centavos = ing.quantidade_centavos
        unidade_str = ing.unidade
        gtin = ing.gtin_associado
    else:
        if quantidade is None:
            raise ValueError("A quantidade do ingrediente deve ser informada.")
        if unidade is None:
            raise ValueError("A unidade do ingrediente deve ser informada.")

        nome_str = str(nome_ingrediente).strip()
        if not nome_str:
            raise ValueError("O nome do ingrediente não pode ser vazio.")
        unidade_str = str(unidade).strip()
        if not unidade_str:
            raise ValueError("A unidade não pode ser vazia.")

        qtd_centavos = centavos_quantidade(quantidade)
        gtin = gtin_associado.strip() if gtin_associado else None

    with con:
        cursor = con.execute(
            """
            INSERT INTO ingredientes (
                receita_id, nome_ingrediente, quantidade, unidade, gtin_associado
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (receita_id, nome_str, qtd_centavos, unidade_str, gtin),
        )
        ingrediente_id = cursor.lastrowid
        if ingrediente_id is None:
            raise RuntimeError("Falha ao registrar ingrediente.")
        return ingrediente_id


def obter_receita(con: sqlite3.Connection, receita_id: int) -> Receita | None:
    """Obtém uma receita por ID acompanhada da lista completa de ingredientes."""
    row = con.execute(
        """
        SELECT id, nome, porcoes, tempo_preparo_min, instrucoes, criada_em, categoria, foto_caminho
          FROM receitas
         WHERE id = ?
        """,
        (receita_id,),
    ).fetchone()

    if row is None:
        return None

    ing_rows = con.execute(
        """
        SELECT id, receita_id, nome_ingrediente, quantidade, unidade, gtin_associado
          FROM ingredientes
         WHERE receita_id = ?
         ORDER BY id ASC
        """,
        (receita_id,),
    ).fetchall()

    ingredientes = [
        Ingrediente(
            id=i["id"],
            receita_id=i["receita_id"],
            nome_ingrediente=i["nome_ingrediente"],
            quantidade=decimal_quantidade(i["quantidade"]),
            unidade=i["unidade"],
            gtin_associado=i["gtin_associado"],
        )
        for i in ing_rows
    ]

    return Receita(
        id=row["id"],
        nome=row["nome"],
        porcoes=row["porcoes"],
        tempo_preparo_min=row["tempo_preparo_min"],
        instrucoes=row["instrucoes"],
        criada_em=row["criada_em"],
        categoria=row["categoria"],
        foto_caminho=row["foto_caminho"],
        ingredientes=ingredientes,
    )


def listar_receitas(
    con: sqlite3.Connection, categoria: str | None = None
) -> list[Receita]:
    """Lista todas as receitas cadastradas com seus respectivos ingredientes."""
    campos_receita = (
        "id, nome, porcoes, tempo_preparo_min, instrucoes, criada_em, categoria, foto_caminho"
    )

    if categoria:
        rec_rows = con.execute(
            f"""
            SELECT {campos_receita}
              FROM receitas
             WHERE categoria = ?
             ORDER BY criada_em DESC, id DESC
            """,
            (categoria.strip(),),
        ).fetchall()
    else:
        rec_rows = con.execute(
            f"""
            SELECT {campos_receita}
              FROM receitas
             ORDER BY criada_em DESC, id DESC
            """
        ).fetchall()

    if not rec_rows:
        return []

    ids = [r["id"] for r in rec_rows]
    placeholders = ",".join("?" * len(ids))
    ing_rows = con.execute(
        f"""
        SELECT id, receita_id, nome_ingrediente, quantidade, unidade, gtin_associado
          FROM ingredientes
         WHERE receita_id IN ({placeholders})
         ORDER BY id ASC
        """,
        ids,
    ).fetchall()

    ingredientes_por_receita: dict[int, list[Ingrediente]] = defaultdict(list)
    for i in ing_rows:
        ingredientes_por_receita[i["receita_id"]].append(
            Ingrediente(
                id=i["id"],
                receita_id=i["receita_id"],
                nome_ingrediente=i["nome_ingrediente"],
                quantidade=decimal_quantidade(i["quantidade"]),
                unidade=i["unidade"],
                gtin_associado=i["gtin_associado"],
            )
        )

    return [
        Receita(
            id=r["id"],
            nome=r["nome"],
            porcoes=r["porcoes"],
            tempo_preparo_min=r["tempo_preparo_min"],
            instrucoes=r["instrucoes"],
            criada_em=r["criada_em"],
            categoria=r["categoria"],
            foto_caminho=r["foto_caminho"],
            ingredientes=ingredientes_por_receita[r["id"]],
        )
        for r in rec_rows
    ]


def buscar_receitas(
    con: sqlite3.Connection, termo: str, categoria: str | None = None
) -> list[Receita]:
    """Busca receitas pelo nome correspondente, com filtro opcional por categoria."""
    termo = termo.strip()
    if not termo and not categoria:
        return listar_receitas(con)

    campos_receita = (
        "id, nome, porcoes, tempo_preparo_min, instrucoes, criada_em, categoria, foto_caminho"
    )

    padrao = f"%{termo}%"
    if categoria:
        rec_rows = con.execute(
            f"""
            SELECT {campos_receita}
              FROM receitas
             WHERE nome LIKE ? AND categoria = ?
             ORDER BY nome ASC
            """,
            (padrao, categoria.strip()),
        ).fetchall()
    else:
        rec_rows = con.execute(
            f"""
            SELECT {campos_receita}
              FROM receitas
             WHERE nome LIKE ?
             ORDER BY nome ASC
            """,
            (padrao,),
        ).fetchall()

    if not rec_rows:
        return []

    ids = [r["id"] for r in rec_rows]
    placeholders = ",".join("?" * len(ids))
    ing_rows = con.execute(
        f"""
        SELECT id, receita_id, nome_ingrediente, quantidade, unidade, gtin_associado
          FROM ingredientes
         WHERE receita_id IN ({placeholders})
         ORDER BY id ASC
        """,
        ids,
    ).fetchall()

    ingredientes_por_receita: dict[int, list[Ingrediente]] = defaultdict(list)
    for i in ing_rows:
        ingredientes_por_receita[i["receita_id"]].append(
            Ingrediente(
                id=i["id"],
                receita_id=i["receita_id"],
                nome_ingrediente=i["nome_ingrediente"],
                quantidade=decimal_quantidade(i["quantidade"]),
                unidade=i["unidade"],
                gtin_associado=i["gtin_associado"],
            )
        )

    return [
        Receita(
            id=r["id"],
            nome=r["nome"],
            porcoes=r["porcoes"],
            tempo_preparo_min=r["tempo_preparo_min"],
            instrucoes=r["instrucoes"],
            criada_em=r["criada_em"],
            categoria=r["categoria"],
            foto_caminho=r["foto_caminho"],
            ingredientes=ingredientes_por_receita[r["id"]],
        )
        for r in rec_rows
    ]


def excluir_receita(
    con: sqlite3.Connection,
    receita_id: int,
    pasta_fotos: Path | None = None,
) -> bool:
    """Exclui uma receita. Ingredientes somem em cascata e foto é apagada do disco."""
    if pasta_fotos:
        remover_foto_receita(con, pasta_fotos, receita_id)

    with con:
        cur = con.execute("DELETE FROM receitas WHERE id = ?", (receita_id,))
        return cur.rowcount > 0


def remover_ingrediente(con: sqlite3.Connection, ingrediente_id: int) -> bool:
    """Remove um ingrediente específico por ID."""
    with con:
        cur = con.execute("DELETE FROM ingredientes WHERE id = ?", (ingrediente_id,))
        return cur.rowcount > 0


def listar_categorias(con: sqlite3.Connection) -> list[str]:
    """Retorna lista de categorias distintas cadastradas."""
    rows = con.execute(
        """
        SELECT DISTINCT categoria
          FROM receitas
         WHERE categoria IS NOT NULL AND TRIM(categoria) != ''
         ORDER BY categoria ASC
        """
    ).fetchall()
    return [r["categoria"] for r in rows]


def escalar_receita(receita: Receita, novas_porcoes: int) -> Receita:
    """Escala as quantidades dos ingredientes para novas porções usando Decimal puro.

    Sem aproximações binárias de float: o fator de escala é calculado
    como razão exata e aplicado com precisão decimal.
    """
    if isinstance(novas_porcoes, float):
        raise TypeError("O número de novas porções deve ser um inteiro, sem float.")
    if novas_porcoes <= 0:
        raise ValueError("O número de novas porções deve ser maior que zero.")
    if receita.porcoes <= 0:
        raise ValueError("A receita de origem deve ter porções maiores que zero.")

    fator = Decimal(novas_porcoes) / Decimal(receita.porcoes)
    novos_ingredientes = []

    for ing in receita.ingredientes:
        nova_qtd = (ing.quantidade * fator).quantize(Decimal("0.01"))
        if nova_qtd == nova_qtd.to_integral():
            nova_qtd_final = Decimal(int(nova_qtd))
        else:
            nova_qtd_final = nova_qtd.normalize()

        novos_ingredientes.append(
            Ingrediente(
                id=ing.id,
                receita_id=ing.receita_id,
                nome_ingrediente=ing.nome_ingrediente,
                quantidade=nova_qtd_final,
                unidade=ing.unidade,
                gtin_associado=ing.gtin_associado,
            )
        )

    return Receita(
        id=receita.id,
        nome=receita.nome,
        porcoes=novas_porcoes,
        tempo_preparo_min=receita.tempo_preparo_min,
        instrucoes=receita.instrucoes,
        criada_em=receita.criada_em,
        categoria=receita.categoria,
        foto_caminho=receita.foto_caminho,
        ingredientes=novos_ingredientes,
    )
