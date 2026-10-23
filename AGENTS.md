# [AGENTS.md](http://AGENTS.md) — mise-en-prato

## 1. Visão Geral do Projeto

O **mise-en-prato** é a evolução do `cupomzinho`: uma aplicação doméstica e de portfólio que une a extração e análise de cupons fiscais de mercado (via QR Code / NFC-e) com a gestão de receitas, estoque/ingredientes (com fotos) e planejamento de cardápio semanal.

## 2. Stack Tecnológica (Estrita)

- **Backend:** Python 3.13 + FastAPI.

- **Banco de Dados:** SQLite em modo WAL, utilizando migrações SQL numeradas sequencialmente.

- **Frontend:** PWA em JavaScript puro (Vanilla JS), HTML5 e CSS moderno, **sem etapa de build** (proibido introduzir Node, Vite, React ou bundlers).

- **Qualidade e Testes:** `ruff` (linter/formatter) e `pytest` (testes automatizados).

## 3. Topologia de Ambientes (`APP_MODE`)

O código deve suportar a variável de ambiente `APP_MODE` (`dev` | `home` | `demo`):

1. **`dev` (MiguelPC):** Ambiente local de desenvolvimento e execução de testes via Docker.

2. **`home` (casa-srv):** Servidor doméstico de produção. Todas as funcionalidades ativas (parser de NFC-e/QR Code, consulta sob demanda ao Menor Preço, escrita persistente no SQLite e upload de fotos).

3. **`demo` (Portfólio Público):** Ambiente público de demonstração.

   - **Bloqueios obrigatórios em `demo`:** Desativar completamente chamadas externas ao Menor Preço e raspagem de notas reais na SEFAZ (retornar HTTP 403 com mensagem clara na UI explicando que a função está desativada na demonstração pública).

   - **Dados de Demonstração:** Utiliza um banco SQLite pré-populado e anonimizado (`demo.db`), mantendo receitas, fotos, cardápios e histórico de preços reais, mas sem chaves de NFC-e, URLs fiscais ou dados pessoais.

## 4. Ambiente de Execução e Validação no MiguelPC (Docker Obrigatório)

O host de desenvolvimento (`MiguelPC`) não executa Python/pip diretamente. **NUNCA** rode `pip`, `pytest` ou `ruff` fora do Docker. Use sempre o serviço `dev`:

- **Linter (Obrigatório antes do commit):**
  ```bash
  docker compose run --rm cupomzinho ruff check .

- **Testes (Obrigatório antes do commit):**
  ```bash
  docker compose run --rm cupomzinho pytest -q
  ```

  Assim que rodar os comandos do Docker acima, me avise se o `pytest` terminou 100% verde ou se apontou alguma falha.