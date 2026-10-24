# mise-en-prato

Da nota fiscal à bancada da cozinha — controle de gastos de mercado, caça a preço e gestão culinária com Modo Cozinha.

---

O **mise-en-prato** é a evolução do `cupomzinho`: uma aplicação doméstica e de portfólio que une a extração e análise de cupons fiscais de mercado (via QR Code / NFC-e da SEFAZ-PR) com um ecossistema culinário prático — gestão de receitas com fotos de capa, **Modo Cozinha** dedicado para bancada, integração imediata com a **Lista de Compras** e inteligência de preços por produto e por mercado.

A premissa continua a mesma: a cada compra no mercado, o cupom fiscal traz um QR Code que aponta para a consulta pública da SEFAZ-PR. O servidor extrai o extrato exato **item a item** (descrição, quantidade, valor unitário e total), sem OCR e sem digitação manual. A partir desse histórico de abastecimento real, o `mise-en-prato` conecta o que você comprou ao que você cozinha.

---

## O que o mise-en-prato faz

```
  [Cupom Fiscal / NFC-e]             [Despensa & Preços]              [Cozinha & Preparo]
  • QR Code / Foto Celular            • Histórico por produto         • Catálogo de Receitas
  • Chave de 44 dígitos               • Menor Preço (CELEPAR)         • Modo Cozinha (Wake Lock)
  • Upload de HTML cru                • Cobertura entre mercados      • Multiplicador de porções
           │                                   │                               │
           ▼                                   ▼                               ▼
    ┌──────────────┐                   ┌──────────────┐                ┌──────────────┐
    │  Ingestão &  │ ─── catálogo ───> │   Lista de   │ <── faltantes ─┤   Módulo de  │
    │  Unificação  │                   │   Compras    │                │   Receitas   │
    └──────────────┘                   └──────────────┘                └──────────────┘
```

### 1. Livro de Receitas & Entrada Híbrida
- **Catálogo Culinário da Casa:** Organização de receitas por categorias (*Prato Principal*, *Massas*, *Sobremesas*, etc.), tempo de preparo e rendimento em porções.
- **Entrada Híbrida Inteligente:** Permite colar receitas completas em texto corrido com parser regex inteligente (identifica ingredientes, frações, unidades de medida e nomes) ou ajustar campo a campo.
- **Vínculo com o Catálogo:** Ingredientes podem ser associados diretamente a produtos do histórico de compras da casa.
- **Fotos de Capa em WebP:** Processamento local via Pillow com compressão leve em WebP (max 1200px, sem metadados EXIF), mantendo a navegação instantânea e arquivos compactos (< 200 KB).

### 2. Modo Cozinha (Hands-Free na Bancada)
Projetado especificamente para o uso com *"mãos na massa / dedos com azeite"* em tablets e celulares apoiados na bancada:
- **Tela Cheia com Wake Lock Ativo:** Utiliza a Screen Wake Lock API (`navigator.wakeLock`) para manter o display do celular ou tablet **sempre aceso** enquanto você cozinha, sem precisar tocar na tela com as mãos sujas.
- **Escala Dinâmica de Porções:** Alterne rapidamente entre frações e múltiplos (`½x`, `1x`, `2x`) com recálculo instantâneo de todos os ingredientes em frações culinárias amigáveis (`½`, `¼`, `1 ½`).
- **Checklist de *Mise-en-place*:** Touch targets generosos (≥ 48px) para riscar ingredientes já separados, medidos e cortados antes de acender o fogo.
- **Tipografia e Contraste Otimizados:** Passos numerados em destaque com fontes legíveis a metros de distância.

### 3. Lista de Compras Inteligente
Responde à pergunta que importa: **"levando ESTA lista, qual mercado sai mais barato?"**
- **Envio Direto da Receita:** Selecione os ingredientes faltantes no detalhe da receita e envie-os para a lista de compras com um clique.
- **Comparação com Cobertura Real:** Cruza os itens com os preços da casa e a API do Menor Preço, informando a **cobertura** (quantos itens da lista aquela loja realmente vende) para evitar que uma loja vença o comparativo apenas por ter preço de meia dúzia de itens.
- **Modo Mercado:** Checkboxes amplos alinhados à esquerda para marcação ágil com uma mão só no corredor do supermercado, movendo itens marcados para *"No carrinho"*.

### 4. Gestão Financeira e Caça a Preço
- **Extrato Exato de Mercado:** Gastos mensais consolidados por estabelecimento e morador da casa.
- **Série Histórica Própria:** Acompanhe a evolução do preço que a sua própria residência pagou ao longo dos meses.
- **Menor Preço sob Demanda:** Consulta ética à base estadual da CELEPAR para descobrir promoções e cotações recentes na sua região geográfica.

---

## Repaginação Visual: Ferro Fundido & Sal Marinho

A interface foi inteiramente redesenhada para uma estética **Editorial Gastrônomo / Warm Modernist**, inspirada em cadernos de receitas clássicos e na culinária afetiva:

- **Paleta Bistrô:** Tons de ardósia mineral e ferro forjado escuro (`#191f24`), superfícies salinas claras (`#f4f5f5`), toques de ardósia azul-petróleo profunda (`--oliva-primario`), acentos quentes em terracota / cobre martelado (`--terracota`) e latão mostarda (`--mostarda`).
- **Tema Escuro Nativo:** Suporte automático via `prefers-color-scheme`, mantendo o contraste e conforto visual à noite.
- **Dock Flutuante (*Floating Dock*) Mobile-First:** Barra de navegação inferior com cantos curvos e acabamento translúcido (*glassmorphism* com `backdrop-filter`), garantindo alcance ergonômico em smartphones e tablets. Organizada em 4 seções principais:
  1. 📖 **Receitas**
  2. 🛒 **Lista**
  3. 🥕 **Produtos**
  4. 📊 **Gastos** (com a fila de revisão de cupons integrada)
- **FAB Central (+) & Bottom Sheet:** No centro do dock, um botão de ação destacado em cobre martelado abre uma folha inferior (*bottom sheet*) para ações rápidas:
  - 📷 *Fotografar QR do Cupom* (dispara a câmera nativa do smartphone diretamente)
  - ✍️ *Cadastrar Nova Receita* (abre o formulário híbrido)
  - 📝 *Item na Lista de Compras* (inclusão ágil no mercado)
  - 🔗 *Opções Manuais* (digitar chave de 44 números, colar URL da SEFAZ ou subir arquivo HTML)
- **Zero Build Step & CSP Estrita:** 100% Vanilla JS, HTML5 semântico e CSS nativo moderno. **Sem Node, Vite, React ou bundlers**. Tipografia elegante com pilhas de fontes nativas do sistema operacional e ícones vetoriais SVG inline ultraleves. Zero chamadas a CDNs externas, carregamento instantâneo e funcionamento pleno offline via Service Worker.

---

## Topologia de Ambientes (`APP_MODE`)

O comportamento do sistema é orquestrado pela variável de ambiente `APP_MODE`:

| Modo | Ambiente Típico | Características & Regras de Isolamento |
|---|---|---|
| **`home`** | Servidor doméstico (`casa-srv`) | **Produção real da residência.** Todas as funções ativas: ingestão de cupons da SEFAZ-PR, consulta sob demanda ao Menor Preço, escrita persistente em `/mnt/data/mise-en-prato`, upload de fotos WebP e autenticação transparente via Tailscale LocalAPI. |
| **`dev`** | Máquina local (`MiguelPC`) | **Desenvolvimento e testes via Docker.** `EXIGIR_TAILNET=false`, morador padrão injetado (`dev@local.test=Dev`), portas expostas em `0.0.0.0:8090` e montagem do código em tempo real (`.:/app`). |
| **`demo`** | Servidor de portfólio público | **Demonstração pública segura.** Apresenta badge discreto *"Demo"* no cabeçalho.<br>• **Bloqueio de chamadas externas:** Chamadas de raspagem à SEFAZ e ao Menor Preço são terminantemente bloqueadas (retornando HTTP 403 com aviso amigável na interface).<br>• **Banco Anonimizado (`demo.db`):** Preserva o catálogo de receitas, fotos de pratos, cardápios e histórico de preços reais, expurgando chaves de NFC-e, URLs fiscais, nomes de operadores e dados sensíveis dos moradores. |

---

## Como Rodar

### 1. Servidor Doméstico de Produção (`home`)

No servidor residencial (ex: `casa-srv` rodando Tailscale):

```bash
# Clone e configuração
cp .env.example .env
# Edite o .env configurando MORADORES, LOCAL e caminhos

# Subida do contêiner de produção
docker compose up -d --build app
```

Acesse através do IP da tailnet: `http://100.122.128.48:8090`.

### 2. Desenvolvimento Local no MiguelPC (`dev`)

O host local não possui Python/pip instalados diretamente; todo o ciclo de desenvolvimento e execução é feito **estritamente dentro do Docker**:

```bash
# Subir a aplicação em modo dev com live reload:
docker compose run --rm --service-ports dev

# Executar a suíte de testes automatizados:
docker compose run --rm dev pytest -q

# Executar o linter e analisador de código:
docker compose run --rm dev ruff check .
```

---

## Variáveis de Ambiente (`.env`)

| Variável | Padrão | Descrição |
|---|---|---|
| `APP_MODE` | `home` | Modo de operação: `home`, `dev` ou `demo`. |
| `DADOS` | `/dados` | Diretório onde residem o SQLite (`mise_en_prato.db`), fotos de receitas e cupons HTML crus. |
| `MORADORES` | `""` | Mapeamento `login_tailscale=Nome,login_tailscale2=Nome2`. O e-mail exato fornecido pelo `tailscale whois`. |
| `ESCUTAR` | `0.0.0.0` | IP em que o Uvicorn prende o socket. Em produção, prende no IP da Tailnet. |
| `PORTA` | `8090` | Porta HTTP da aplicação. |
| `LOCAL` | `-25.2947,-54.0940` | Coordenadas de latitude/longitude usadas como centro para busca de preços no Menor Preço. |
| `RAIO_KM` | `15` | Raio em km para busca de preços locais. |
| `TETO_CONSULTAS_HORA`| `50` | Limite ético de consultas horárias à API da CELEPAR (ritmo humano). |
| `EXIGIR_TAILNET` | `true` | Quando `true`, consulta o socket do Tailscaled para identificar o morador. Em `dev` fica `false`. |

### Autenticação Transparente via Tailscale (Zero Senhas)

No modo `home`, o servidor não possui tela de login ou banco de senhas:
- O contêiner roda com `network_mode: host` e acessa o socket Unix do Tailscale (`/var/run/tailscale/tailscaled.sock`).
- Ao receber uma requisição no IP da tailnet, o backend pergunta diretamente à LocalAPI do daemon quem é o dono daquele IP de origem.
- Se o morador constar em `MORADORES`, a requisição é autenticada e as compras e listas são atribuídas àquela pessoa.
- IPs desconhecidos na tailnet (ex: compartilhamento de nó) recebem HTTP 403.
- O uso de `network_mode: host` é indispensável: se estivesse em rede de bridge comum, o Docker mascararia o IP de origem com o gateway interno (`172.x`), quebrando a identificação da Tailscale LocalAPI.

---

## Ingestão de Cupons Fiscais

Como o Safari do iOS não possui a API nativa `BarcodeDetector`, o `mise-en-prato` processa a decodificação no servidor, padronizando a experiência em qualquer navegador móvel:

1. **Fotografar o QR Code (Principal):** Abra a PWA, toque no FAB central e escolha *Fotografar QR do Cupom*. O servidor recebe a imagem e extrai a URL fiscal da SEFAZ-PR.
2. **Colar o Link Fiscal:** O app de câmera nativo do celular lê o QR e sugere o link; basta colar no campo da aplicação.
3. **Atalho do iOS:** Disparo em um toque diretamente da tela inicial do iPhone:
   - App Atalhos → Novo atalho → **Ler código QR**
   - → **Obter conteúdo do URL**: `POST` para `http://100.122.128.48:8090/api/cupom` com corpo JSON `{"url": "..."}`.
4. **Consulta pela Chave de Acesso (44 dígitos + Captcha):** Quando o cupom impresso em papel térmico está amassado ou manchado e o QR Code é ilegível, utiliza-se a segunda porta do portal da Fazenda. O morador digita os 44 números, a aplicação busca o desafio de imagem (captcha da SEFAZ) e o exibe na tela para validação humana.

> Reenviar o mesmo cupom não duplica registros: a chave de acesso de 44 dígitos é a chave primária imutável da nota.

### A Escada de Escalas do QR Code (Por que fotos de celular falham?)

Câmeras modernas de smartphones entregam fotos em resoluções altíssimas (ex: 4032x3024). O QR Code da NFC-e possui ~45 módulos de lado. Em resoluções elevadas, cada módulo passa a ocupar ~40 pixels. Contudo, em impressoras térmicas de supermercado, o papel não gera quadrados pretos sólidos: gera nuvens de pontos chapiscados com falhas. O decodificador `zbar` tenta ler o ruído microscópico em vez dos módulos e desiste.

O `mise_en_prato/qr.py` implementa uma **escada de escalas descendente com desfoque proporcional**:
```
Resolução original (4032px) ............... falhou
Redução 1600, 1200, 900 ................... falhou
Desfoque gaussiano + Redução para 400-600px  achou em < 1s
```
Se o `zbar` não conseguir decodificar, o pipeline recorre automaticamente ao `zxing-cpp` como segunda opinião.

### A Chave de 44 Dígitos e o Hash do CSC

Por que não podemos simplesmente consultar a rota do QR usando a chave de 44 dígitos?
A URL do QR Code termina com um parâmetro `p=<chave>|2|1|1|<sha1>`, onde o hash SHA-1 é gerado pelo emissor a partir do CSC (Código de Segurança do Contribuinte) — um segredo fiscal não impresso no cupom. Sem o CSC, a rota do QR rejeita a requisição.
Por isso, a consulta por chave utiliza a **Consulta Completa da NFC-e** via formulário com captcha, que é 100% suportada pela aplicação.

Além disso, a chave de acesso é auto-verificável:
- O 44º dígito é calculado via módulo 11 sobre os 43 dígitos precedentes. O backend valida isso antes de gastar tempo com a SEFAZ.
- Os dígitos são estruturados e posicionais: revelam UF, ano/mês de emissão, CNPJ do estabelecimento, série e número da nota fiscal.

---

## A Disciplina com a API do Menor Preço

O `mise-en-prato` consulta a API pública do [Menor Preço Nota Paraná](https://menorpreco.notaparana.pr.gov.br/) sob demanda estrita para encontrar cotações na região geográfica:

1. **Exclusivamente Disparado por Pessoas:** Zero cron jobs, zero varreduras automáticas periódicas em laço infinito.
2. **User-Agent Transparente e Honesto:** A aplicação se identifica como `mise-en-prato/<versao> (uso pessoal...)`.
3. **Teto de Consultas:** Máximo configurável de 50 consultas por hora (~uma a cada 72 segundos), simulando a cadência humana.
4. **Cache Local no SQLite:** Preços obtidos respondem consultas subsequentes, reduzindo o volume de chamadas externas.
5. **Filtro Sanitário Anti-Envenenamento (`sanidade.py`):** A API do Menor Preço entra em modo degradado quando detecta requisições de clientes genéricos, retornando códigos HTTP 200 recheados de dados fictícios (lojas `ZTEST`, municípios do PR marcados como PE, NCMs de 9 dígitos). O `sanidade.py` analisa a resposta e descarta imediatamente payloads envenenados.

---

## Arquitetura de Dados & Rigor Matemático

- **Zero Ponto Flutuante (No Floats):**
  - **Valores Monetários:** Armazenados como `INTEGER` em **centavos** no banco e manipulados estritamente como `Decimal` no Python.
  - **Pesos de Mercado:** Armazenados como `INTEGER` em **milésimos** (3 casas decimais).
  - **Ingredientes de Receitas:** Quantidades armazenadas como `INTEGER` em centavos (escala 100) no SQLite e formatadas como `Decimal` ou frações visuais amigáveis (`formatar_fracao_culinaria`).
  - Nenhuma operação financeira ou culinária sofre com perda de precisão binária de ponto flutuante.
- **SQLite em Modo WAL:** Operação concorrente veloz e segura. Um único processo gravador no backend elimina problemas de lock de banco de dados.
- **Migrações Sequenciais:** Todas as alterações de banco de dados utilizam arquivos SQL versionados em `mise_en_prato/migracoes/` (do `001_inicial.sql` ao `012_receita_fotos.sql`).

---

## Estrutura do Código

```
mise-en-prato/
├── compose.yaml                  # Serviços Docker (app em host mode e dev com live-reload)
├── Dockerfile                    # Multi-stage build (producao e dev com pytest/ruff)
├── pyproject.toml                # Dependências Python 3.13 e configurações de ferramentas
├── AGENTS.md                     # Diretrizes de arquitetura, stack e comandos
│
├── mise_en_prato/                # Pacote principal
│   ├── banco.py                  # Conexão SQLite (WAL), conversão de tipos e migrações
│   ├── catalogo.py               # Catálogo satélite offline (Open Food Facts / CMED)
│   ├── config.py                 # Pydantic Settings e detecção de ambiente (APP_MODE)
│   ├── consulta_chave.py         # Consulta à SEFAZ via chave de acesso com captcha
│   ├── cupons.py                 # Ingestão de cupons, deduplicação e catálogo
│   ├── enriquecimento.py         # Cascata de identificação de itens (memória -> satélite -> SEFAZ)
│   ├── identidade.py             # Validação de moradores via socket do Tailscale
│   ├── listas.py                 # Gestão de listas de compras e comparação entre lojas
│   ├── main.py                   # Endpoints REST FastAPI e inicialização do servidor
│   ├── menor_preco.py            # Cliente HTTP do Menor Preço Nota Paraná
│   ├── nfce_pr.py                # Parser lxml do HTML da SEFAZ-PR
│   ├── ocr.py                    # Fallback com Tesseract OCR para cupons não-eletrônicos
│   ├── qr.py                     # Escada de redução de imagens e leitura de QR Codes
│   ├── receitas.py               # Lógica de receitas, cálculo de porções e fotos WebP
│   ├── relatorios.py             # Consultas analíticas de despesas e séries de preços
│   ├── reprocessar.py            # Reprocessamento de cupons arquivados
│   ├── sanidade.py               # Validação de respostas da CELEPAR contra bases de teste
│   ├── tarefas.py                # Fila de tarefas assíncronas em segundo plano
│   ├── unificacao.py             # Deduplicação de produtos e normalização
│   │
│   ├── migracoes/                # Scripts SQL sequenciais (001 a 012)
│   └── web/                      # Frontend PWA (Vanilla JS, sem etapa de build)
│       ├── index.html            # Estrutura HTML5 com dock flutuante e bottom sheet
│       ├── estilo.css            # Design tokens "Ferro Fundido e Sal Marinho"
│       ├── app.js                # Orquestração do cliente, cupons, gastos e listas
│       ├── receitas.js           # Catálogo de receitas, Modo Cozinha e Wake Lock
│       ├── sw.js                 # Service Worker para cache e PWA offline
│       └── manifest.webmanifest  # Configuração de instalação móvel
│
├── tests/                        # Suíte de testes automatizados com pytest
│   ├── test_api.py               # Testes dos endpoints HTTP principais
│   ├── test_api_receitas.py      # Testes de integração das rotas de receitas e fotos
│   ├── test_receitas.py          # Testes unitários do parser de texto e Modo Cozinha
│   ├── test_cupons.py            # Testes de ingestão e casamento de itens
│   ├── test_qr.py                # Testes da escada de decodificação de QR
│   ├── test_nfce_pr.py           # Testes do parser contra HTMLs reais da SEFAZ
│   └── fixtures/                 # Cupons reais arquivados e payloads JSON de teste
└── docs/                         # Documentos de design e arquitetura detalhados
```

---

## Validação e Qualidade de Código

Conforme estabelecido em `AGENTS.md`, o host não deve executar `pip`, `pytest` ou `ruff` diretamente fora dos contêineres:

```bash
# Executar verificação de linting e formatação:
docker compose run --rm dev ruff check .

# Executar suíte completa de testes:
docker compose run --rm dev pytest -q
```

Garantia de 100% dos testes verdes e zero advertências de linting antes de cada commit.
