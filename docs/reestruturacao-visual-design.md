# Design e Especificação — Reestruturação Visual e Navegação (mise-en-prato)

## 1. Visão Geral e Resumo de Entendimento

O projeto **mise-en-prato** (evolução do *cupomzinho*) é uma aplicação doméstica e de portfólio que une a gestão de receitas culinárias, planejamento de compras/ingredientes e controle financeiro de mercado via NFC-e/QR Code.

Esta especificação define a repaginação completa do front-end, transformando o aplicativo de uma interface utilitária espartana em uma experiência refinada, ergonômica e memorável, focada no uso em smartphones e tablets na bancada da cozinha e no supermercado.

### 1.1. Metas Principais
- **Transição de Navegação:** Saída do modelo de abas fixas no topo para um **Dock Flutuante (*Floating Dock*) Mobile-First** no rodapé com **FAB Central (+)** e **Bottom Sheet** de ações rápidas.
- **Identidade Estética:** Estilo **Editorial Gastrônomo / Warm Modernist** — inspirado em revistas contemporâneas de gastronomia e livros de receitas de chef.
- **Assinatura Visual:** Cartões em formato de *Ficha Técnica / Recipe Index*, com tipografia display clássica, micro-pílulas funcionais e paleta quente de especiarias (oliva, terracota, papel manteiga, sálvia e mostarda).
- **Ergonomia Prática:** Suporte a *"mãos na massa / dedos com azeite"* no Modo Cozinha (touch targets ampliados e tela acesa) e *"uso com uma mão"* na Lista de Compras no supermercado.

---

## 2. Premissas, Restrições e Não-Escopo

### 2.1. Premissas e Restrições Técnicas (Estritas)
- **Zero Build Step:** 100% Vanilla JS, HTML5 semântico e CSS nativo moderno. É proibida a adição de Node, Vite, React ou bundlers.
- **CSP Fechada e Offline:** Sem dependências de CDNs externas ou Google Fonts. Tipografia baseada em pilhas de fontes nativas de alta qualidade e ícones SVG inline ultraleves.
- **Preservação Funcional:** Zero quebra de contratos de API FastAPI ou lógica de `app.js` e `receitas.js`. Os IDs essenciais e endpoints permanecem intactos.
- **Multi-ambiente (`APP_MODE`):** Suporte coerente a `dev`, `home` e `demo` (com selo indicativo e bloqueios elegantes no modo portfólio público).
- **Validação Docker:** Toda validação no ambiente de desenvolvimento do host deve ser feita estritamente via Docker (`pytest` e `ruff`).

### 2.2. Não-Escopo
- Modificações na API backend do FastAPI ou migrações do banco de dados SQLite WAL.
- Alteração dos algoritmos de parser fiscal de NFC-e / OCR da SEFAZ.

---

## 3. Registro de Decisões (Decision Log)

| Decisão | Alternativas Consideradas | Motivo da Escolha |
|---|---|---|
| **Dock Flutuante “Atelier Gourmet”** | Barra inferior edge-to-edge ou Sidebar tradicional | Proporciona visual moderno de alta craft, leveza visual e adaptação natural tanto em celulares quanto em tablets na bancada. |
| **Estética Editorial Gastrônomo** | Minimalismo neutro (SaaS cinza) ou Industrial brutalista | Cria identidade calorosa e memorável condizente com gastronomia afetiva e organização doméstica. |
| **4 Abas Principais + FAB Central** | 6 abas horizontais ou menu hambúrguer | Foco nas 4 tarefas mais frequentes: *Receitas*, *Lista*, *Produtos* e *Gastos*, reservando o FAB para ações imediatas. |
| **Bottom Sheet para Ações Rápidas** | Speed dial circular ou disparo direto de câmera | Permite acesso rápido em um toque a *Escanear QR*, *Nova Receita* e *Item na Lista* sem congestionar o layout. |
| **Tipografia Nativa Híbrida** | Fontes remotas via Webfonts/CDN | Respeita a CSP fechada, garante carregamento instantâneo offline e combina a elegância da serifa com a clareza da sans. |
| **Touch Targets ≥ 48px no Modo Cozinha** | Controles padrão de formulário desktop | Essencial para permitir interação com o polegar ou com mãos engorduradas durante o preparo no fogão. |

---

## 4. Design Tokens e Sistema Visual

### 4.1. Variáveis CSS (`estilo.css`)

```css
:root {
  /* Cores de Fundo e Superfície (Ferro Fundido & Sal Marinho) */
  --bg-app: #f4f5f5;
  --bg-superficie: #ffffff;
  --bg-elevado: #e9ecee;
  
  /* Tintas e Textos */
  --tinta-forte: #191f24;      /* Ferro forjado escuro */
  --tinta-media: #5e6871;      /* Ardósia mineral */
  --tinta-fraca: #88939c;      /* Metadados e legendas */
  
  /* Acentos de Bistrô e Alta Cozinha */
  --oliva-primario: #24323c;    /* Ardósia azul-petróleo profunda */
  --terracota: #bd4e31;        /* Cobre martelado queimado (FAB) */
  --mostarda: #cca037;         /* Latão dourado / mostarda antiga */
  --alerta-erro: #b91c1c;
  --borda-fina: #e1e4e6;
  
  /* Tipografia Estruturada */
  --fonte-display: ui-serif, "Iowan Old Style", "Apple Garamond", Georgia, "Cambria", serif;
  --fonte-corpo: system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  
  /* Sombras e Elevação */
  --sombra-dock: 0 8px 30px rgba(25, 31, 36, 0.14), 0 2px 8px rgba(25, 31, 36, 0.06);
  --sombra-card: 0 2px 12px rgba(25, 31, 36, 0.06);
}

@media (prefers-color-scheme: dark) {
  :root {
    --bg-app: #111416;
    --bg-superficie: #191e22;
    --bg-elevado: #21292f;
    --tinta-forte: #e8ecef;
    --tinta-media: #929ea7;
    --tinta-fraca: #68737c;
    --oliva-primario: #7ea4b8;
    --terracota: #ea6b4c;
    --mostarda: #e0b44a;
    --borda-fina: #283138;
    --sombra-dock: 0 8px 32px rgba(0, 0, 0, 0.55);
    --sombra-card: 0 2px 10px rgba(0, 0, 0, 0.3);
  }
}
```

---

## 5. Arquitetura de Navegação e Telas

### 5.1. Header Editorial
- **Branding:** Substituição do título espartano pelo logotipo **mise-en-prato** em fonte display serifada.
- **Status Contextual:** Indicação do morador ativo (`#quem`) e badge sutil de contagem de cupons pendentes de revisão.
- **Badge de Demonstração:** Em `APP_MODE=demo`, exibe uma pílula informativa discreta (*"Demonstração"*).

### 5.2. Dock Flutuante de Navegação (`#dock-nav`)
- **Posicionamento:** Fixo no rodapé (`bottom: max(1rem, env(safe-area-inset-bottom))`), centralizado horizontalmente.
- **Acabamento:** Cantos arredondados (`border-radius: 30px`), fundo translúcido com `backdrop-filter: blur(16px)` e borda suave.
- **Itens do Dock:**
  1. 📖 **Receitas** (`data-aba="receitas"`)
  2. 🛒 **Lista** (`data-aba="lista"`)
  3. ➕ **FAB Central** (`#btn-fab-acoes`): Botão elevado em cor terracota com ícone `+`.
  4. 🥕 **Produtos** (`data-aba="produtos"`)
  5. 📊 **Gastos** (`data-aba="gastos"`) — com a seção de **Revisão** e histórico de cupons integrada.

### 5.3. Bottom Sheet de Ações Rápidas (`#sheet-acoes`)
Acionado ao tocar no FAB central:
- 📷 **Fotografar QR do Cupom** — aciona o input de câmera nativa diretamente.
- ✍️ **Nova Receita** — abre o modal de cadastro de receitas.
- 📝 **Adicionar à Lista** — foca o campo de inclusão rápida de compras.
- 🔗 **Opções Manuais** — expande alternativas (colar link da Fazenda, digitar chave de 44 números, upload de HTML da SEFAZ).

---

## 6. Ergonomia e Componentes

### 6.1. Fichas de Receitas
- **Cartões:** Aspect-ratio fotográfico (16:10), bordas suaves e títulos em serifa.
- **Micro-pílulas:** Indicadores de tempo de preparo, porções e categoria em formato de pílulas estilizadas.
- **Grade Responsiva:** 1 coluna em smartphones; 2 a 3 colunas em tablets e monitores maiores.

### 6.2. Modo Cozinha (Hands-Free / Oily-Hands)
- **Overlay Fullscreen:** Foco total no preparo sem distrações.
- **Feedback Wake Lock:** Badge *"💡 Tela acesa"* bem visível no topo.
- **Touch Targets:** Controles de porção (`½x`, `1x`, `2x`) e checkboxes de ingredientes com área de clique mínima de 48px a 52px.
- **Instruções:** Texto grande e espaçado com numeração destacada em terracota para leitura à distância na bancada.

### 6.3. Lista de Compras (Modo Supermercado)
- Checkboxes amplos alinhados à esquerda para marcação rápida com uma mão.
- Itens marcados transitam suavemente para o bloco *"No carrinho"*.

---

## 7. Estratégia de Testes e Validação Técnica

1. **Docker Obrigatório (Host Windows):**
   - Linting antes do commit: `docker compose run --rm cupomzinho ruff check .`
   - Suíte de testes: `docker compose run --rm cupomzinho pytest -q` (garantia de 100% verde).
2. **Validação de Interface e Acessibilidade:**
   - Teste de viewports: 360px (mobile compacto), 390px (iPhone), 768px (iPad de bancada) e desktop.
   - Verificação de contraste de cores (WCAG AA).
   - Suporte a navegação por teclado e fechamento de modais/sheets com a tecla `Esc`.
