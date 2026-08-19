# cupomzinho

Cupom fiscal, gasto de mercado e caça a preço — para o Paraná.

A cada compra o cupom traz um QR Code. Ele aponta para a consulta pública de
NFC-e da SEFAZ-PR, que devolve a compra **item a item**, com descrição,
quantidade e valor unitário. É o extrato exato, sem OCR e sem digitar nada.

O `cupomzinho` guarda isso num sqlite local e responde três perguntas:

- **quanto a casa gastou** — por mês, por mercado, por morador;
- **quanto você paga por cada coisa** — a série do seu próprio preço;
- **onde está mais barato agora** — cruzando com a API pública do
  [Menor Preço Nota Paraná](https://menorpreco.notaparana.pr.gov.br/).

Roda no servidor de casa, acessível só pela tailnet. Nada sai da máquina além
das duas consultas públicas ao estado.

> Estado atual: cupom entra, gasto e histórico de preço funcionam. O
> enriquecimento pelo Menor Preço (código de barras, categoria, comparação
> entre mercados) está desenhado e ainda não ligado — ver **O que falta**.

---

## Como sobe

```bash
cp .env.exemplo .env      # e preencha MORADORES
docker compose up -d --build
```

Depois, no navegador: `http://100.122.128.48:8090`.

O `.env` tem três coisas que importam:

| Variável | O que é |
|---|---|
| `MORADORES` | `login@dominio=Nome`, separado por vírgula. **É o e-mail inteiro** que o `tailscale whois` devolve, não o apelido |
| `LOCAL` | centro da busca de preço, `"lat,lon"` |
| `TETO_CONSULTAS_HORA` | teto de consultas ao Menor Preço — leia a seção sobre a API antes de subir |

### Não há senha, e é de propósito

O servidor escuta **só no IP da tailnet**. Quem chega já passou pelo Tailscale,
então basta perguntar ao `tailscaled` quem é aquele IP — é o que a LocalAPI faz,
pelo socket unix, sem CLI dentro do contêiner e sem privilégio nenhum.

Quem está na tailnet mas não está em `MORADORES` é visita de share do nó, e
leva 403. Visita não registra compra da casa.

---

## Mandar cupom do celular

O Safari do iPhone **não tem `BarcodeDetector`**, então quem decodifica o QR é o
servidor, não o navegador. Isso deixa o caminho igual em qualquer aparelho.

1. **Fotografar** (principal) — abra a PWA, toque em *Fotografar o QR do cupom*,
   enquadre o quadradinho. Um toque.
2. **Colar o link** — o app Câmera do iPhone lê o QR e mostra a notificação;
   copie o endereço e cole na PWA.
3. **Atalho do iOS** (um toque, sem abrir a PWA):
   - Atalhos → novo atalho → **Ler código QR**
   - → **Obter conteúdo do URL**: endereço `http://100.122.128.48:8090/api/cupom`,
     método `POST`, corpo `JSON` com o campo `url` = *Código QR* (a saída do
     passo anterior)
   - dê um nome, e **Adicionar à Tela de Início**.

Mandar o mesmo cupom duas vezes não duplica nada: a chave de acesso é a chave
primária, e reenvio é o caso comum, não a exceção.

---

## A API do Menor Preço mente quando acha que está sendo raspada

Isto é o que mais importa saber antes de mexer neste repositório.

A API é pública, sem chave, e responde bem. Mas depois de **~30 consultas em 25
minutos** ela para de dizer a verdade — **sem nunca devolver erro**. Continua
respondendo HTTP 200, com o JSON no formato certo, e conteúdo fabricado.

Medido em 19/08/2026, com estas duas provas:

```
termo="ZZQQXX FOOBAR 999G"     (termo que não existe em produto nenhum)
  -> total=439, e 439 "produtos" com desc EXATAMENTE "ZZQQXX FOOBAR 999G",
     preços aleatórios, lojas "TOCE MANIA", "CONDOR SUVER CENTZR LTDA"

gtin=7891167011779             (40 minutos antes: o atum certo, no LAR, R$ 10,97)
  -> total=419, desc "PASEA DE GENTE COLOAT", município "CAYOEINO DE IVAPEQIRIK"
```

Os nomes de loja e de município são **corrupções de caractere** de nomes reais, e
a `desc` é o eco literal da consulta. Por isso:

- toda resposta passa por `cupomzinho/sanidade.py` **antes** de virar linha no
  banco. Resposta suspeita não grava nada, entra em backoff longo e sai no log;
- `TETO_CONSULTAS_HORA` começa baixo e não deve ser aumentado sem medir de novo;
- o casamento de produto **nunca** é aproximado. No mesmo cupom convivem
  `ATUM ... RALADO NAT 170G` e `ATUM ... RALADO DEF 170G`, com código de barras
  diferente e **exatamente o mesmo preço**. Um fuzzy match pegaria o errado sem
  fazer barulho.

Outros limites medidos: a janela `data=` satura em ~7 dias, e `data=30` pendura
a conexão. A API **não devolve CNPJ** — o estabelecimento vem com um código
opaco, e o pareamento com a loja do cupom é por nome + município.

---

## O que o cupom traz (e o que não traz)

O HTML da consulta pública é inválido em três pontos que derrubam um parser
ingênuo, todos vistos no cupom real que serve de fixture:

- `id="linhaTotal"` **se repete** em todos os totais. Ler por id devolveria o
  primeiro valor para todos os campos — errado, e em silêncio. Os totais são
  lidos pelo texto do `<label>`.
- `id="Item + 1"` tem **espaço** no id, o que nenhum seletor de id aceita.
- `<br></br>` espalhado pelo documento.

É por isso que se usa lxml aqui, e não o `html.parser` da stdlib.

**O cupom não traz código de barras nem NCM.** Traz a descrição e um código
interno da loja. Daí o casamento de produto em três degraus (`cupomzinho/cupons.py`):

1. `apelido_loja` — se `(loja, texto)` já foi resolvido, acabou. É o que faz o
   segundo cupom da mesma loja ser instantâneo;
2. enriquecimento pelo Menor Preço — só aceita candidato com descrição
   **idêntica** após normalizar, **mesma loja** e **mesmo preço**;
3. fila "unificar produtos", na mão.

### Privacidade

O rodapé do cupom traz `Operador(a): <matrícula>-<nome completo do caixa>`. É
dado pessoal de um funcionário da loja e não serve ao controle de gasto da casa:
**não é lido nem guardado**. A fixture de teste entra no repositório com o nome
removido e com chave de acesso e protocolo mascarados.

---

## Desenvolvimento

O host do casa-srv não tem `pip` nem `python3-venv` (Debian, PEP 668), e não vai
ter — **o contêiner é o ambiente de desenvolvimento**:

```bash
docker build --target dev -t cupomzinho:dev .

# testes e linter, com o código montado de fora (não precisa reconstruir)
docker run --rm -v "$PWD:/app" -w /app cupomzinho:dev pytest -q
docker run --rm -v "$PWD:/app" -w /app cupomzinho:dev ruff check .

# o parser na mão, contra a fixture ou contra um cupom novo
docker run --rm -v "$PWD:/app" -w /app cupomzinho:dev \
  python -m cupomzinho.nfce_pr tests/fixtures/cupom_lar_2026-08-14.html
```

Reconstruir a imagem só é preciso quando muda dependência.

### Onde fica cada coisa

| Arquivo | Papel |
|---|---|
| `cupomzinho/nfce_pr.py` | busca e interpreta o cupom no portal da SEFAZ-PR |
| `cupomzinho/cupons.py` | grava o cupom e casa item com produto |
| `cupomzinho/relatorios.py` | as consultas de gasto e de histórico |
| `cupomzinho/identidade.py` | quem é o morador, pelo `whois` do tailscaled |
| `cupomzinho/banco.py` | conexão sqlite (WAL) e migrações numeradas |
| `cupomzinho/main.py` | a API e a PWA, no mesmo processo |
| `cupomzinho/web/` | a PWA, sem framework e sem build |

Dinheiro é `INTEGER` de **centavos** no banco e `Decimal` no python; quantidade é
`INTEGER` de **milésimos** (o cupom pesa com três casas). Não há float em ponto
nenhum do caminho — inclusive no JSON, onde valor sai como string. Somar gasto
tem de fechar no centavo.

Um processo só escreve no banco. É por isso que o vigia de preços vai rodar como
tarefa de fundo do próprio servidor, e não em contêiner separado: dois
escritores em sqlite é o caminho curto para `database is locked`.

---

## O que falta

- `sanidade.py` — o detector de dado envenenado, com as duas respostas reais
  (limpa e fabricada) como fixture;
- `menor_preco.py` + tarefa de fundo — enriquecer produto com código de barras e
  NCM, e acompanhar preço na região;
- abas **Ofertas** e **Lista de compras** (qual mercado sai mais barato para a
  lista, mostrando junto a cobertura — loja barata que só cobre metade da lista
  não pode ganhar em silêncio);
- backup diário do banco (`VACUUM INTO`, guardando 7);
- OCR do cupom inteiro, para comércio que não emite NFC-e. O lugar está
  reservado: `cupom.origem` já aceita `'foto'` e o Pillow já está na imagem.
