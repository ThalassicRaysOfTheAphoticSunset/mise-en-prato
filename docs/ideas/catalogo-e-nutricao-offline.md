# Catálogo Satélite Offline e Perfil Nutricional da Despensa

## Problem Statement
Como poderíamos enriquecer e identificar instantaneamente os produtos da casa (GTIN, marca, canônico e nutrição) a partir de dumps abertos locais sem depender das cotas da CELEPAR, transformando o histórico de compras em inteligência alimentar e de consumo da família?

## Recommended Direction
Adotar uma arquitetura **Local-First com Banco Satélite (`dados/catalogo.db`)**. Em vez de fazer chamadas lentas de rede ou sobrecarregar o banco transacional principal [`dados/cupomzinho.db`](cupomzinho/banco.py), o sistema mantém uma base de referência local gerada e atualizada sob demanda via CLI (`python -m cupomzinho.catalogo`).

Essa base satélite compila o dump oficial do **Open Food Facts (Brasil)** e a tabela pública da **CMED / ANVISA** (medicamentos), indexando produtos por EAN-13/GTIN e descrição normalizada. O processamento do Open Food Facts é feito via streaming em `gzip` do arquivo JSONL oficial, filtrando registros pertinentes ao Brasil sem estourar a memória RAM do servidor doméstico. O acesso ao banco satélite é feito por conexão SQLite independente e em modo leitura, eliminando qualquer contenção de lock com o banco de dados principal.

Na ingestão de cupons fiscais — especialmente em conjunto com a extração de `cEAN` da Issue #2 —, o casamento do produto ocorre em `< 1ms` offline. Os produtos são enriquecidos não apenas com nome padronizado e marca, mas também com metadados de saúde: **Nutri-Score** (A a E) e **Classificação NOVA** (1 a 4: alimentos in natura a ultraprocessados), permitindo que a aplicação informe tanto o gasto financeiro quanto o perfil nutricional da cesta de compras da casa.

## Key Assumptions to Validate
- [ ] **Cobertura real de marcas locais:** O dump do Open Food Facts Brasil cobre ao menos 60% dos produtos recorrentes do banco da casa (validar rodando um script de teste cruzando GTINs já existentes em `dados/cupomzinho.db` com o dump do OFF).
- [ ] **Pegada de memória e disco:** O script de download e filtragem em stream do OFF Brasil consegue rodar no servidor doméstico gerando um `catalogo.db` com menos de 100 MB e sem exigir mais de 512 MB de RAM durante o parse.
- [ ] **Densidade de dados nutricionais:** O volume de produtos nacionais com `nova_group` e `nutriscore` preenchidos no OFF é suficiente para gerar relatórios relevantes, sem que a maioria dos itens caia em "Não Informado".

## MVP Scope

### O que entra (In):
1. **CLI e Módulo de Ingestão de Catálogo (`cupomzinho/catalogo.py`)**:
   - Rotina para processar em stream JSONL (.jsonl.gz) do Open Food Facts e CSV/XLS da CMED gerando `dados/catalogo.db`.
   - Tabelas indexadas por `gtin` e índice de busca textual para descrições.
2. **Cascata de Resolução em [`cupomzinho/enriquecimento.py`](cupomzinho/enriquecimento.py)**:
   - **Degrau 0:** Memória da casa (mesma loja / código local).
   - **Degrau 1:** Lookup local no `catalogo.db` satélite (busca offline imediata por GTIN/EAN).
   - **Degrau 2:** Menor Preço (CELEPAR) sob demanda, acionado apenas se o item não for resolvido e houver requisição na interface.
3. **Migração de Schema (`008_produto_nutricao.sql`)**:
   - Adicionar colunas na tabela `produto` do banco principal: `marca TEXT`, `nutriscore TEXT`, `nova_group INTEGER`, `foto_url TEXT`.
4. **Perfil Nutricional e Exibição**:
   - Badges de Nutri-Score e classificação NOVA na visualização dos itens.
   - Resumo nutricional mensal em `relatorios.py` (proporção de ultraprocessados vs in natura).

## Not Doing (and Why)
- **Download em massa de fotos para o disco local:** Usar URLs de CDN diretas do Open Food Facts para poupar armazenamento e tráfego desnecessário no servidor doméstico.
- **Atualização diária automática via cron:** Sincronização sob demanda via CLI é suficiente, pois cadastros de produtos industriais têm alta perenidade.
- **Integração com WebService CCG/SEFAZ (SVRS) com Certificado Digital:** Complexidade operacional e de certificados desproporcional para uso doméstico.
- **Alertas reativos de alérgenos por morador:** Evitar falsos positivos antes de consolidar a precisão cadastral básica.
- **Ingestão síncrona de APIs externas pagas/comerciais no upload de cupons:** Preservar a garantia de zero latência e resiliência offline.

## Decisões Tomadas
1. **Formato do Dump:** Processamento em stream com descompactação em tempo real de JSONL compactado (`.jsonl.gz`), permitindo iterar milhões de linhas com uso constante e mínimo de memória (< 100MB RAM).
2. **Conexão SQLite Independente:** O banco `dados/catalogo.db` é acessado por uma conexão dedicada separada em modo somente-leitura (`file:dados/catalogo.db?mode=ro`), garantindo total desacoplamento e ausência de lock com `dados/cupomzinho.db`.
