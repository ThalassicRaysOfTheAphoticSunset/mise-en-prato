# Design e Especificação — Módulo de Receitas e Modo Cozinha

## 1. Visão Geral e Objetivo

O **Módulo de Receitas** do **mise-en-prato** transforma o aplicativo no assistente central de cozinha da casa. O objetivo é fornecer uma experiência rápida de cadastro, um **Modo Cozinha** dedicado (com tela sempre acesa, tipografia grande e checkboxes de *mise-en-place*) e envio integrado de ingredientes para a **Lista de Compras**.

---

## 2. Resumo de Entendimento e Escopo

- **O que estamos construindo:**
  - Catálogo de receitas da casa com fotos de capa em WebP.
  - Cadastro ágil com suporte híbrido (colar texto corrido com parser inteligente ou ajuste campo a campo).
  - Modo Cozinha em tela cheia com Wake Lock (tela acesa), checklist de ingredientes e multiplicador dinâmico de porções (frações amigáveis).
  - Integração com a Lista de Compras com checklist rápido de itens faltantes.
- **Público-alvo:** Moradores da casa utilizando smartphones e tablets na bancada da cozinha.
- **Não-escopo desta etapa:** Controle de estoque de despensa automático, cardápio semanal completo (segunda a domingo) e cálculo de depreciação de temperos.

---

## 3. Registro de Decisões (Decision Log)

| Decisão | Alternativas Consideradas | Motivo da Escolha |
|---|---|---|
| **Foco no Preparo e Compras** | Foco contábil/estoque ou Cardápio Semanal | Prioriza a utilidade prática no fogão e a dor mais frequente da rotina doméstica. |
| **Modo Cozinha Fullscreen** | Gaveta lateral ou página comum | Legibilidade à distância, tela sempre acesa (*Wake Lock*) e checkboxes de *mise-en-place*. |
| **Entrada Híbrida de Ingredientes** | Cadastro puramente campo a campo ou texto livre | Permite colar receitas da web mantendo a estruturação para escala de porções. |
| **Foto de Capa Única em WebP** | Múltiplas fotos ou sem fotos | Alta qualidade visual com arquivos pequenos (< 200 KB) e zero sobrecarga na tailnet. |
| **Arquitetura Modular (receitas.js)** | Monolítico em `app.js` ou Server-Driven HTML | Mantém a API REST limpa e evita inchar `app.js`, sem introduzir bundlers. |

---

## 4. Arquitetura e Componentes

### 4.1. Banco de Dados (SQLite WAL)
- **Migração `012_receitas_fotos.sql`:**
  - Adiciona `foto_caminho TEXT` e `categoria TEXT` na tabela `receitas`.
  - Índice `idx_receitas_categoria`.

### 4.2. Backend e Mídia (FastAPI + Pillow)
- **Endpoints REST (`main.py`):**
  - `GET /api/receitas`: listagem com filtros de busca e categoria.
  - `GET /api/receitas/{id}`: detalhe com ingredientes e foto.
  - `POST /api/receitas`: cadastro completo (transacional).
  - `PUT /api/receitas/{id}`: atualização.
  - `DELETE /api/receitas/{id}`: remoção da receita e da foto no disco.
  - `POST /api/receitas/{id}/foto`: upload multipart com compressão WebP (max 1200px) via Pillow.
  - `DELETE /api/receitas/{id}/foto`: remoção da foto de capa.
  - `POST /api/receitas/{id}/enviar-para-lista`: inclusão dos ingredientes selecionados na lista de compras.
- **Segurança e Modos:**
  - `APP_MODE=demo`: bloqueio de escrita (retorna HTTP 403).
  - Upload restrito a imagens válidas (max 10 MB bruto, sem EXIF).

### 4.3. Frontend PWA (Vanilla JS)
- Arquivo dedicado: `mise_en_prato/web/receitas.js`.
- Aba `Receitas` em `#abas` no `index.html`.
- **Modo Cozinha:**
  - Ativa `navigator.wakeLock` ao abrir e libera ao fechar.
  - Escala dinâmica de porções no cliente (ex: `½`, `1 ½`, `300 g`).
  - Checkboxes interativos com riscado visual para *mise-en-place*.
- **Modal de Cadastro Híbrido:**
  - Parser regex de texto corrido em tempo real.
  - Autocomplete com produtos históricos da casa.

---

## 5. Estratégia de Testes e Validação
- Testes unitários do parser de ingredientes e manipulação de fotos em `tests/test_receitas.py`.
- Testes de integração de API em `tests/test_api_receitas.py` com `TestClient`.
- Validação no Docker: `ruff check .` e `pytest -q`.
