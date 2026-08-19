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

Por isso o contêiner usa **`network_mode: host`**, e quem prende a porta é o
uvicorn (env `ESCUTAR`), não a publicação de porta do Docker. Em rede bridge o
Docker mascara o IP de origem dos clientes externos, e o servidor recebe o
gateway da bridge (`172.x`) no lugar do IP de quem pediu — aí ninguém é morador.
O detalhe cruel é que, testado do próprio host, funciona: ali a origem já é o
IP da tailnet. Só aparece de outro aparelho.

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

## Menor Preço: consulta sob demanda, com o cliente identificado

O `cupomzinho` consulta a API pública do Menor Preço **só quando uma pessoa
pede** — nunca em laço, nunca agendado. As regras abaixo não são estilo, são a
razão de a coisa ser aceitável.

### O que o Termo de Uso proíbe

O Termo (CELEPAR, em `menorpreco.notaparana.pr.gov.br/termo`) é específico:

> "Você se compromete a não utilizar qualquer sistema automatizado, inclusive,
> mas sem se limitar a 'robôs', 'spiders' ou 'agentes automatizados', que
> acessem a Plataforma de maneira a efetuar diversas solicitações aos
> servidores da CELEPAR em um dado período de tempo, que seja considerado
> humanamente impossível de ser realizado no mesmo período por meio de um
> navegador convencional ou de um aplicativo."

> "É vedada também a utilização da Plataforma para aplicações de 'Pricing', ou
> verificação de preços da concorrência e a utilização abusiva da Plataforma."

E a Plataforma destina-se a "fins informativos e de uso próprio, excluindo
qualquer utilização comercial ou publicitária".

Uso doméstico, sob demanda, para decidir onde a casa faz a compra da semana
cabe nisso. Um vigia de preços rodando de hora em hora não cabe — e foi
justamente o vigia que a primeira versão do plano previa, por decisão minha e
não por pedido de ninguém.

### As cinco regras

1. **Só disparado por gente.** Nenhuma tarefa de fundo, nenhum agendamento.
2. **User-Agent honesto**, `cupomzinho/<versão> (uso pessoal...)`. Nunca imitar
   navegador. A diferença não é de volume, é de honestidade: um cliente que se
   identifica pode ser reconhecido e barrado pela CELEPAR quando ela quiser —
   copiar os cabeçalhos do Chrome tira dela essa escolha.
3. **Uma consulta por vez**, com espera entre elas, e teto por hora (50 por
   padrão: uma a cada ~72 segundos, ritmo de gente).
4. **Toda resposta validada** antes de virar linha no banco (ver abaixo).
5. **Cache no banco.** O preço de ontem responde a pergunta de hoje; não se
   pergunta duas vezes a mesma coisa.

### Os parâmetros da API, medidos

`GET /api/v1/produtos?local=&raio=&data=&offset=&ordem=&termo=|gtin=[&categoria=]`

| Parâmetro | O que se sabe |
|---|---|
| `local` | aceita `lat,lon` **e** geohash (`6g98151gk`); o front usa geohash |
| `gtin` | busca exata por código de barras — é o caminho bom quando se tem |
| `termo` | busca por texto; buscar o nome exato do produto é legítimo e comum |
| `data` | **não é janela estrita**: com `data=7` vieram observações de 14 dias antes. `data=-1` devolveu resultado IDÊNTICO a `data=7`. `data=30` pendura a conexão |
| `categoria` | filtro adicional que o front oferece depois de `/api/v1/categorias`. Sem serventia aqui: buscando por gtin ou nome exato a precisão já é alta |
| `raio` | km a partir de `local` |

Como `data` não é janela estrita, **a app não filtra de novo por data ao
comparar** — filtrar em 7 dias do nosso lado descartava preço que a própria
fonte deu por vigente, e fazia loja com preço válido sumir da comparação como
se não vendesse o item. A janela nossa é larga (30 dias) e a tela mostra a data
da observação mais antiga de cada loja, para quem lê julgar.

### O modo degradado existe, e é silencioso

Isto foi medido, e é o motivo de a regra 4 não ser opcional.

Enquanto as consultas saíam com o User-Agent padrão do Python
(`Python-urllib/3.13`), a API respondia **HTTP 200 com uma base de teste**:
`desc` ecoando o termo consultado, lojas com caracteres trocados
(`CONDOR SUVER CENTZR LTDA`, `CAMPO VAGRA` por Campo Largo, uma delas prefixada
`ZTEST`), municípios do Paraná rotulados como PE, `ncm` com 9 dígitos, preços
implausíveis. As distâncias, essas, vinham coerentes com o raio pedido.

Nunca houve bloqueio: o navegador de um desktop na mesma LAN, no mesmo IP
público, recebia dado limpo o tempo todo. O discriminador era o User-Agent.

A fixture `tests/fixtures/menor_preco_envenenado_gtin.json` guarda uma dessas
respostas. Os sinais que a denunciam, do mais barato ao mais caro:

| Sinal | Sadio | Degradado |
|---|---|---|
| canário (termo inventado) | `total: 0` | dezenas de produtos com aquele nome |
| `ncm` | 8 dígitos | 9 dígitos |
| `estabelecimento.uf` | só `PR` | mistura `ES`, `PE` |
| `desc` distintas num lote | várias | uma só, repetida |
| `distkm` | coerente | **também coerente — não serve de sinal** |

## O que o cupom traz (e o que não traz)

O HTML da consulta pública é inválido em três pontos que derrubam um parser
ingênuo, todos vistos no cupom real que serve de fixture:

- `id="linhaTotal"` **se repete** em todos os totais. Ler por id devolveria o
  primeiro valor para todos os campos — errado, e em silêncio. Os totais são
  lidos pelo texto do `<label>`.
- `id="Item + 1"` tem **espaço** no id, o que nenhum seletor de id aceita.
- `<br></br>` espalhado pelo documento.

É por isso que se usa lxml aqui, e não o `html.parser` da stdlib.

### Foto de celular: o problema é resolução DEMAIS

Contra-intuitivo, e custou dois envios falhos até virar medição. Um iPhone
entrega 4032x3024; o QR da NFC-e tem ~45 módulos de lado e ocupa boa parte do
quadro, o que dá uns **40 pixels por módulo**. Só que a impressão térmica é
chapiscada — o módulo não é um quadrado sólido, é uma nuvem de pontos com
falhas. Nessa escala o zbar mede o chapisco em vez do módulo e desiste, mesmo
com a foto bem enquadrada e nítida.

Medido contra a foto real que falhou:

```
reduzida para 1600, 1200, 900, 700 ......... falhou
reduzida para 500, 400, 300 ................ achou
desfoque 4 + reduzida para 1200, 800, 600 .. achou
filtro de mediana, em qualquer tamanho grande  falhou
```

Daí a escada de `cupomzinho/qr.py`: uma varredura de escalas, desfocando na
medida da redução. A foto que falhava passa hoje na segunda tentativa, em ~1 s.

Duas hipóteses foram levantadas e **medidas como falsas** no caminho, e ficam
registradas para ninguém as perseguir de novo: falta de zona de silêncio (o
zbar lê QR sem margem nenhuma) e filtro de mediana para matar o chapisco (não
recupera em resolução alta).

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

- **OCR do cupom inteiro**, para comércio que não emite NFC-e. O lugar está
  reservado: `cupom.origem` já aceita `'foto'` e o Pillow já está na imagem.
- Ajustar quantidade do item direto na lista (hoje entra 1 e edita-se pela API).
- Produto a granel (`CEBOLA NACIONAL KG`) nunca terá código de barras. A fila de
  enriquecimento já os manda para o fim depois da primeira tentativa, mas dava
  para reconhecê-los pela unidade e nem perguntar.
