# Design e Especificação — Migração do cupomzinho para mise-en-prato no casa-srv

## 1. Visão Geral e Objetivo

Este documento define o plano operacional para substituir a aplicação `cupomzinho` pelo `mise-en-prato` no servidor doméstico de produção (`casa-srv`), garantindo a preservação integral do histórico de cupons fiscais, compras e catálogo de produtos, validando as novas tabelas e colunas de receitas e fotos ([`011_receitas.sql`](file:///C:/Users/Miguel/orca/workspaces/mise-en-prato/brainstorm/mise_en_prato/migracoes/011_receitas.sql) e [`012_receita_fotos.sql`](file:///C:/Users/Miguel/orca/workspaces/mise-en-prato/brainstorm/mise_en_prato/migracoes/012_receita_fotos.sql)) em sandbox local antes de tocar no servidor.

---

## 2. Resumo de Entendimento e Escopo

- **O que estamos realizando:**
  - Migração de produção da stack `cupomzinho` para `mise-en-prato` no `casa-srv`.
  - Transição de dados do SQLite (`cupomzinho.db` -> `mise_en_prato.db`) e do volume (`/mnt/data/cupomzinho` -> `/mnt/data/mise-en-prato`).
  - Execução e validação das migrações 011 e 012 sobre os dados históricos reais.
  - Testes funcionais completos no `MiguelPC` antes de qualquer alteração definitiva no servidor.
- **Público-alvo:** Moradores da residência acessando via Tailscale (`100.122.128.48:8090`).
- **Não-objetivos:** Manter código descartável de retrocompatibilidade de nomes de banco de dados; alterar portas ou mecanismos de autenticação Tailscale em produção; modificar cupons ou histórico prévio.

---

## 3. Premissas e Requisitos Não-Funcionais

1. **Confiabilidade e Rollback:** A pasta original `/mnt/data/cupomzinho` permanece intacta como backup frio no `casa-srv`. Em caso de qualquer falha, o retorno ao estado anterior leva menos de 1 minuto.
2. **Segurança e Isolamento:** No ambiente `dev` local (MiguelPC), `EXIGIR_TAILNET=false` e `APP_MODE=dev` garantem que não haja disparos externos para SEFAZ ou Menor Preço durante os testes. No `casa-srv`, mantém-se a montagem do socket do tailscaled e checagem de moradores.
3. **Desempenho da Migração:** Migrações 011 e 012 executam em milissegundos sem lock prolongado do SQLite.
4. **Permissões de Arquivos:** Manter UID/GID `1000:1000` (`miguel`) como proprietário dos volumes no `casa-srv`.
5. **Acesso Operacional:** Conexão SSH ativa e funcional entre o MiguelPC e o `casa-srv`.

---

## 4. Registro de Decisões (Decision Log)

| Decisão | Alternativas Consideradas | Motivo da Escolha |
|---|---|---|
| **Migração em 2 Fases (Local Sandbox -> Servidor)** | Homologação paralela no servidor ou substituição direta sem teste local | Garante risco zero: valida 100% da integridade dos dados e das migrações antes de tocar na produção. |
| **Renomeação Limpa de Dados (`mise_en_prato.db`)** | Código de detecção automática com fallback ou manter o nome legado `cupomzinho.db` | Mantém o código 100% limpo e sem débitos técnicos ou branches legadas (*YAGNI*). |
| **Extração com Parada Breve do Contêiner** | Cópia a quente com risco de WAL aberto ou snapshot SQLite via CLI | Garante que o SQLite descarregue o arquivo `-wal` e feche o banco de forma consistente; downtime real menor que 15 segundos. |
| **Deploy via Git (`main`) no casa-srv** | Transferência manual de arquivos via rsync ou interface web pura | Rastreabilidade total de versões, histórico limpo e padrão consistente com o restante da infraestrutura da casa. |

---

## 5. Desenho Técnico e Especificação Detalhada

### Fase 1: Extração Segura dos Dados para o MiguelPC

1. **Parada temporária do cupomzinho no `casa-srv`:**
   ```bash
   # No casa-srv (via SSH na pasta da stack):
   docker compose stop cupomzinho
   ```

2. **Download do volume para o MiguelPC:**
   ```bash
   # No MiguelPC (PowerShell ou terminal):
   scp -r usuario@casa-srv:/mnt/data/cupomzinho ./dados_teste
   ```

3. **Reativação imediata do cupomzinho em produção:**
   ```bash
   # No casa-srv:
   docker compose up -d cupomzinho
   ```

4. **Adequação estrutural local no MiguelPC:**
   - Renomear `./dados_teste/cupomzinho.db` para `./dados_teste/mise_en_prato.db`.
   - Excluir eventuais arquivos temporários residuais (`cupomzinho.db-wal` e `cupomzinho.db-shm`).
   - Criar o diretório de fotos de receitas:
     ```powershell
     New-Item -ItemType Directory -Force -Path "./dados_teste/receitas/fotos"
     ```

---

### Fase 2: Execução e Validação no Sandbox Local (MiguelPC)

1. **Executar a aplicação montando os dados de teste:**
   ```powershell
   docker compose run --rm --service-ports `
     -v "${PWD}/dados_teste:/dados" `
     -e DADOS=/dados `
     dev python -m mise_en_prato
   ```

2. **Verificação técnica:**
   - Conferir nos logs da inicialização a execução de:
     - `011_receitas.sql`
     - `012_receita_fotos.sql`
   - Rodar a suíte completa de testes no Docker:
     ```powershell
     docker compose run --rm dev pytest -q
     ```

3. **Validação funcional (`http://localhost:8090`):**
   - **Histórico e Relatórios:** Listar compras anteriores e checar totais, itens e estabelecimentos.
   - **Catálogo de Produtos:** Buscar produtos e verificar persistência de preços médios.
   - **Módulo de Receitas:** Cadastrar uma receita de teste vinculando ingredientes ao catálogo, enviar para a lista de compras, carregar uma foto de capa e testar o Modo Cozinha.

---

### Fase 3: Virada Definitiva no casa-srv

1. **Merge e Publicação:**
   - Realizar merge das branches de receitas e visual para a branch `main`.
   - `git push origin main`.

2. **Preparação e Migração no `casa-srv`:**
   ```bash
   # 1. Parar a stack antiga
   cd ~/srv/cupomzinho
   docker compose down

   # 2. Criar o novo volume a partir do backup frio
   sudo cp -a /mnt/data/cupomzinho /mnt/data/mise-en-prato
   sudo mv /mnt/data/mise-en-prato/cupomzinho.db /mnt/data/mise-en-prato/mise_en_prato.db
   sudo mkdir -p /mnt/data/mise-en-prato/receitas/fotos
   sudo chown -R 1000:1000 /mnt/data/mise-en-prato

   # 3. Atualizar o código e subir a nova stack
   git pull origin main
   docker compose build --no-cache app
   docker compose up -d app

   # 4. Acompanhar os logs
   docker compose logs -f app
   ```

---

## 6. Plano de Rollback e Contingência

Se for identificada qualquer anomalia crítica durante a subida em produção:

1. Parar o contêiner `mise-en-prato`:
   ```bash
   docker compose down
   ```
2. Reverter para a versão anterior ou restaurar o `compose.yaml` apontando para `/mnt/data/cupomzinho`.
3. Subir novamente o `cupomzinho`:
   ```bash
   docker compose up -d
   ```
Como o diretório original `/mnt/data/cupomzinho` foi mantido sem nenhuma alteração, a restauração é 100% imediata e sem perda de dados.
