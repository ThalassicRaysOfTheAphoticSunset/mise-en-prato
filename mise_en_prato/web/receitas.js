/* Módulo de Receitas e Modo Cozinha para o mise-en-prato.
   Vanilla JS sem bundler e sem framework. */
'use strict';

(function () {
  let todasReceitas = [];
  let categoriasDisponiveis = [];
  let categoriaAtiva = '';
  let receitaAtiva = null;
  let escalaCozinha = 1;
  let checklistMiseEnPlace = {};

  const $ = (s) => document.querySelector(s);
  const $$ = (s) => document.querySelectorAll(s);

  // Frações culinárias para exibição amigável
  function formatarFracao(quantidade) {
    const n = Number(quantidade);
    if (isNaN(n) || n <= 0) return quantidade || '';
    const inteiro = Math.floor(n);
    const resto = Math.round((n - inteiro) * 100) / 100;

    let fracao = '';
    if (Math.abs(resto - 0.5) < 0.05) fracao = '½';
    else if (Math.abs(resto - 0.25) < 0.05) fracao = '¼';
    else if (Math.abs(resto - 0.75) < 0.05) fracao = '¾';
    else if (Math.abs(resto - 0.33) < 0.05) fracao = '⅓';
    else if (Math.abs(resto - 0.67) < 0.05) fracao = '⅔';

    if (fracao) {
      return inteiro > 0 ? `${inteiro} ${fracao}` : fracao;
    }
    return Number.isInteger(n) ? String(n) : n.toFixed(2).replace(/\.?0+$/, '').replace('.', ',');
  }

  // ── Carregamento de Receitas ──────────────────────────────────────────────
  async function carregarReceitas(filtroQ = '', filtroCat = '') {
    const q = filtroQ || $('#busca-receitas')?.value || '';
    const cat = filtroCat !== undefined ? filtroCat : categoriaAtiva;

    try {
      const url = `/api/receitas?q=${encodeURIComponent(q)}&categoria=${encodeURIComponent(cat)}`;
      const dados = await api(url);
      todasReceitas = dados.receitas || [];
      categoriasDisponiveis = dados.categorias || [];
      renderizarChipsCategorias();
      renderizarCards();
    } catch (err) {
      console.error('Erro ao carregar receitas:', err);
    }
  }

  function renderizarChipsCategorias() {
    const conteiner = $('#chips-categorias-receitas');
    if (!conteiner) return;

    if (!categoriasDisponiveis.length) {
      conteiner.innerHTML = '';
      return;
    }

    let html = `<button type="button" class="chip-sabor ${!categoriaAtiva ? 'ativo' : ''}" onclick="filtrarCategoria('')">Todas</button>`;
    categoriasDisponiveis.forEach((cat) => {
      const ativa = categoriaAtiva === cat ? 'ativo' : '';
      html += `<button type="button" class="chip-sabor ${ativa}" onclick="filtrarCategoria('${cat.replace(/'/g, "\\'")}')">${cat}</button>`;
    });
    conteiner.innerHTML = html;
  }

  window.filtrarCategoria = function (cat) {
    categoriaAtiva = cat;
    renderizarChipsCategorias();
    carregarReceitas('', cat);
  };

  function renderizarCards() {
    const grade = $('#grade-receitas');
    const vazio = $('#receitas-vazio');
    if (!grade) return;

    if (!todasReceitas.length) {
      grade.innerHTML = '';
      if (vazio) vazio.hidden = false;
      return;
    }

    if (vazio) vazio.hidden = true;

    grade.innerHTML = todasReceitas
      .map((r) => {
        const fotoHtml = r.foto_caminho
          ? `<img src="/api/receitas/${r.id}/foto" alt="${r.nome}" class="card-receita-img" loading="lazy">`
          : `<div class="card-receita-sem-foto">🥘</div>`;

        const tempoHtml = r.tempo_preparo_min ? `<span>⏱️ ${r.tempo_preparo_min} min</span>` : '';
        const porcoesHtml = r.porcoes ? `<span>👥 ${r.porcoes} porções</span>` : '';
        const catHtml = r.categoria ? `<span class="badge-categoria">${r.categoria}</span>` : '';

        return `
        <article class="card-receita" onclick="abrirModoCozinha(${r.id})">
          <div class="card-receita-capa">
            ${fotoHtml}
            ${catHtml}
          </div>
          <div class="card-receita-corpo">
            <h3 class="card-receita-titulo">${r.nome}</h3>
            <div class="card-receita-meta fraca">
              ${tempoHtml}
              ${porcoesHtml}
              <span>🥗 ${r.qtd_ingredientes} itens</span>
            </div>
          </div>
          <div class="card-receita-rodape" onclick="event.stopPropagation()">
            <button type="button" class="btn-acao-card" onclick="abrirModalEditarReceita(${r.id})" title="Editar">✏️ Editar</button>
            <button type="button" class="btn-acao-card ruim" onclick="confirmarExcluirReceita(${r.id}, '${r.nome.replace(/'/g, "\\'")}')" title="Excluir">🗑️ Excluir</button>
          </div>
        </article>`;
      })
      .join('');
  }

  // ── Modo Cozinha (Hands-Free Fullscreen) ───────────────────────────────────
  window.abrirModoCozinha = async function (id) {
    try {
      const rec = await api(`/api/receitas/${id}`);
      receitaAtiva = rec;
      escalaCozinha = 1;
      checklistMiseEnPlace = {};

      $('#cozinha-titulo').textContent = rec.nome;
      $('#cozinha-tempo').textContent = rec.tempo_preparo_min ? `⏱️ ${rec.tempo_preparo_min} min` : '';
      $('#cozinha-divisor-meta').hidden = !rec.tempo_preparo_min;

      const badgeCat = $('#cozinha-badge-categoria');
      if (badgeCat) {
        badgeCat.textContent = rec.categoria || '';
        badgeCat.hidden = !rec.categoria;
      }

      const containerFoto = $('#cozinha-foto-container');
      const imgFoto = $('#cozinha-foto');
      if (rec.foto_caminho && containerFoto && imgFoto) {
        imgFoto.src = `/api/receitas/${rec.id}/foto`;
        containerFoto.hidden = false;
      } else if (containerFoto) {
        containerFoto.hidden = true;
      }

      // Instruções
      const instrucoesEl = $('#cozinha-instrucoes-texto');
      if (instrucoesEl) {
        if (rec.instrucoes && rec.instrucoes.trim()) {
          const passos = rec.instrucoes
            .split(/\n+/)
            .map((p) => p.trim())
            .filter(Boolean);
          instrucoesEl.innerHTML = passos.map((p, idx) => `<p><strong>Passo ${idx + 1}:</strong> ${p}</p>`).join('');
        } else {
          instrucoesEl.innerHTML = '<p class="fraca">Nenhuma instrução detalhada cadastrada para esta receita.</p>';
        }
      }

      // Reseta botões de escala
      $$('.seletor-escala-porcoes .btn-escala').forEach((btn) => {
        btn.classList.toggle('ativo', btn.textContent.trim() === '1x');
      });

      renderizarIngredientesCozinha();

      // Ativa WakeLock para manter tela acesa
      if (typeof ativarWakeLock === 'function') {
        ativarWakeLock();
      }

      $('#modal-modo-cozinha').hidden = false;
      document.body.style.overflow = 'hidden';
    } catch (err) {
      console.error('Erro ao abrir Modo Cozinha:', err);
      alert('Falha ao carregar receita.');
    }
  };

  window.fecharModoCozinha = function () {
    $('#modal-modo-cozinha').hidden = true;
    document.body.style.overflow = '';
    if (typeof desativarWakeLock === 'function') {
      desativarWakeLock();
    }
    receitaAtiva = null;
  };

  window.ajustarEscalaCozinha = function (fator) {
    escalaCozinha = fator;
    $$('.seletor-escala-porcoes .btn-escala').forEach((btn) => {
      const txt = btn.textContent.trim();
      const match =
        (fator === 0.5 && txt === '½x') ||
        (fator === 1 && txt === '1x') ||
        (fator === 2 && txt === '2x') ||
        (fator === 3 && txt === '3x') ||
        (fator === 4 && txt === '4x');
      btn.classList.toggle('ativo', match);
    });
    renderizarIngredientesCozinha();
  };

  function renderizarIngredientesCozinha() {
    const lista = $('#cozinha-lista-ingredientes');
    if (!lista || !receitaAtiva) return;

    if (!receitaAtiva.ingredientes.length) {
      lista.innerHTML = '<div class="fraca">Nenhum ingrediente cadastrado.</div>';
      return;
    }

    lista.innerHTML = receitaAtiva.ingredientes
      .map((ing) => {
        const checked = checklistMiseEnPlace[ing.id] ? 'checked' : '';
        const checadoCls = checklistMiseEnPlace[ing.id] ? 'item-checado' : '';

        // Recalcula quantidade
        const qtdOriginal = Number(ing.quantidade) || 0;
        const qtdEscalada = qtdOriginal > 0 ? qtdOriginal * escalaCozinha : null;
        const qtdFormatada = qtdEscalada !== null ? formatarFracao(qtdEscalada) : '';
        const unidadeTexto = ing.unidade && ing.unidade !== 'unidade' ? ing.unidade : '';

        return `
        <label class="item-mise-en-place ${checadoCls}" onclick="toggleChecklistIngrediente(${ing.id})">
          <input type="checkbox" ${checked} onclick="event.stopPropagation(); toggleChecklistIngrediente(${ing.id})">
          <span class="nome-ingrediente">${ing.nome_ingrediente}</span>
          <span class="qtd-ingrediente">${qtdFormatada} ${unidadeTexto}</span>
        </label>`;
      })
      .join('');
  }

  window.toggleChecklistIngrediente = function (id) {
    checklistMiseEnPlace[id] = !checklistMiseEnPlace[id];
    renderizarIngredientesCozinha();
  };

  // ── Enviar para Lista de Compras ──────────────────────────────────────────
  window.abrirModalEnviarLista = async function () {
    if (!receitaAtiva) return;

    const listaEl = $('#enviar-lista-checklist');
    const selectListas = $('#select-destino-lista');
    if (!listaEl || !selectListas) return;

    // Preenche checklist de ingredientes
    listaEl.innerHTML = receitaAtiva.ingredientes
      .map((ing) => {
        return `
        <label style="display:flex;align-items:center;gap:.6rem;cursor:pointer;padding:.3rem 0">
          <input type="checkbox" class="chk-enviar-ingrediente" value="${ing.id}" checked>
          <span><strong>${ing.nome_ingrediente}</strong> (${ing.quantidade_formatada || ing.quantidade} ${ing.unidade})</span>
        </label>`;
      })
      .join('');

    // Busca listas existentes
    try {
      const resp = await api('/api/listas');
      const listas = resp.listas || [];
      let opcoes = '<option value="">+ Criar nova lista com nome da receita</option>';
      listas.forEach((l) => {
        opcoes += `<option value="${l.id}">Adicionar à lista: "${l.nome}" (${l.itens || 0} itens)</option>`;
      });
      selectListas.innerHTML = opcoes;
    } catch (err) {
      selectListas.innerHTML = '<option value="">+ Criar nova lista com nome da receita</option>';
    }

    $('#modal-enviar-lista').hidden = false;
  };

  window.fecharModalEnviarLista = function () {
    $('#modal-enviar-lista').hidden = true;
  };

  window.confirmarEnviarParaLista = async function () {
    if (!receitaAtiva) return;

    const selecionados = Array.from($$('.chk-enviar-ingrediente:checked')).map((cb) => Number(cb.value));
    if (!selecionados.length) {
      alert('Selecione ao menos um ingrediente para enviar.');
      return;
    }

    const listaIdVal = $('#select-destino-lista')?.value;
    const payload = {
      lista_id: listaIdVal ? Number(listaIdVal) : null,
      nome_nova_lista: !listaIdVal ? `Receita: ${receitaAtiva.nome}` : null,
      ingrediente_ids: selecionados,
    };

    try {
      const res = await api(`/api/receitas/${receitaActiveId()}/enviar-para-lista`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });

      fecharModalEnviarLista();
      if (confirm(`✅ ${res.adicionados} ingredientes adicionados à lista de compras!\nDeseja ir para a aba Lista agora?`)) {
        fecharModoCozinha();
        if (typeof mudarAba === 'function') {
          mudarAba('lista');
        }
      }
    } catch (err) {
      alert(`Falha ao enviar para a lista: ${err.message}`);
    }
  };

  function receitaActiveId() {
    return receitaAtiva?.id;
  }

  // ── Cadastro e Edição de Receitas ─────────────────────────────────────────
  window.abrirModalNovaReceita = function () {
    $('#modal-receita-titulo').textContent = 'Nova Receita';
    $('#form-rec-id').value = '';
    $('#form-rec-nome').value = '';
    $('#form-rec-porcoes').value = '4';
    $('#form-rec-tempo').value = '';
    $('#form-rec-categoria').value = '';
    $('#form-rec-instrucoes').value = '';
    $('#form-rec-foto').value = '';
    $('#form-rec-foto-status').textContent = '';
    $('#caixa-colar-ingredientes').hidden = true;
    $('#lista-campos-ingredientes').innerHTML = '';

    // Carrega sugestões de categorias
    atualizarDatalistCategorias();

    // Começa com 2 linhas de ingredientes em branco
    adicionarLinhaIngrediente();
    adicionarLinhaIngrediente();

    $('#modal-receita').hidden = false;
  };

  window.abrirModalEditarReceita = async function (id) {
    try {
      const rec = await api(`/api/receitas/${id}`);
      $('#modal-receita-titulo').textContent = 'Editar Receita';
      $('#form-rec-id').value = rec.id;
      $('#form-rec-nome').value = rec.nome;
      $('#form-rec-porcoes').value = rec.porcoes;
      $('#form-rec-tempo').value = rec.tempo_preparo_min || '';
      $('#form-rec-categoria').value = rec.categoria || '';
      $('#form-rec-instrucoes').value = rec.instrucoes || '';
      $('#form-rec-foto').value = '';
      $('#form-rec-foto-status').textContent = rec.foto_caminho ? '📷 Foto atual preservada' : '';
      $('#caixa-colar-ingredientes').hidden = true;
      $('#lista-campos-ingredientes').innerHTML = '';

      atualizarDatalistCategorias();

      if (rec.ingredientes && rec.ingredientes.length) {
        rec.ingredientes.forEach((ing) => {
          adicionarLinhaIngrediente(ing.quantidade, ing.unidade, ing.nome_ingrediente, ing.gtin_associado);
        });
      } else {
        adicionarLinhaIngrediente();
      }

      $('#modal-receita').hidden = false;
    } catch (err) {
      alert(`Erro ao abrir receita: ${err.message}`);
    }
  };

  function atualizarDatalistCategorias() {
    const dl = $('#lista-sugestoes-categorias');
    if (!dl) return;
    dl.innerHTML = categoriasDisponiveis.map((c) => `<option value="${c}">`).join('');
  }

  window.fecharModalReceita = function () {
    $('#modal-receita').hidden = true;
  };

  window.adicionarLinhaIngrediente = function (qtd = '', unid = 'g', nome = '', gtin = '') {
    const container = $('#lista-campos-ingredientes');
    if (!container) return;

    const row = document.createElement('div');
    row.className = 'linha-ingrediente-form';
    row.innerHTML = `
      <input type="text" class="campo-ing-qtd" placeholder="Qtd" value="${qtd}" style="width:4.8rem" required>
      <input type="text" class="campo-ing-unid" placeholder="Unid" value="${unid}" style="width:5.4rem" list="sugestoes-unidades" required>
      <input type="text" class="campo-ing-nome" placeholder="Nome do ingrediente" value="${nome}" style="flex:1" required>
      <input type="hidden" class="campo-ing-gtin" value="${gtin || ''}">
      <button type="button" class="btn-remover-linha" onclick="this.parentElement.remove()" title="Remover">✕</button>
    `;
    container.appendChild(row);
  };

  // Parser inteligente para colar texto corrido
  window.toggleColarIngredientes = function () {
    const caixa = $('#caixa-colar-ingredientes');
    if (caixa) {
      caixa.hidden = !caixa.hidden;
      if (!caixa.hidden) {
        $('#txt-colar-ingredientes')?.focus();
      }
    }
  };

  window.processarTextoIngredientes = async function () {
    const texto = $('#txt-colar-ingredientes')?.value;
    if (!texto || !texto.trim()) return;

    try {
      const resp = await api('/api/receitas/parse-ingredientes', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ texto }),
      });

      const itens = resp.ingredientes || [];
      if (itens.length) {
        itens.forEach((it) => {
          adicionarLinhaIngrediente(it.quantidade, it.unidade, it.nome_ingrediente, it.gtin_associado);
        });
        $('#txt-colar-ingredientes').value = '';
        $('#caixa-colar-ingredientes').hidden = true;
      }
    } catch (err) {
      alert(`Falha ao processar ingredientes: ${err.message}`);
    }
  };

  // Salvar Receita (Create ou Update)
  $('#form-receita')?.addEventListener('submit', async (e) => {
    e.preventDefault();

    const id = $('#form-rec-id')?.value;
    const nome = $('#form-rec-nome')?.value.trim();
    const porcoes = Number($('#form-rec-porcoes')?.value) || 1;
    const tempo = $('#form-rec-tempo')?.value ? Number($('#form-rec-tempo').value) : null;
    const categoria = $('#form-rec-categoria')?.value.trim() || null;
    const instrucoes = $('#form-rec-instrucoes')?.value.trim() || null;

    // Coleta ingredientes
    const linhas = $$('#lista-campos-ingredientes .linha-ingrediente-form');
    const ingredientes = [];
    linhas.forEach((l) => {
      const qtdStr = l.querySelector('.campo-ing-qtd')?.value.trim().replace(',', '.');
      const unid = l.querySelector('.campo-ing-unid')?.value.trim();
      const n = l.querySelector('.campo-ing-nome')?.value.trim();
      const gtin = l.querySelector('.campo-ing-gtin')?.value.trim() || null;

      if (n && unid) {
        ingredientes.push({
          nome_ingrediente: n,
          quantidade: qtdStr || '1',
          unidade: unid,
          gtin_associado: gtin,
        });
      }
    });

    const payload = {
      nome,
      porcoes,
      tempo_preparo_min: tempo,
      instrucoes,
      categoria,
      ingredientes,
    };

    const btnSalvar = $('#btn-salvar-receita');
    if (btnSalvar) btnSalvar.disabled = true;

    try {
      let receitaSalvaId = id;
      if (!id) {
        const res = await api('/api/receitas', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
        receitaSalvaId = res.id;
      } else {
        await api(`/api/receitas/${id}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
      }

      // Upload de foto se selecionada
      const inputFoto = $('#form-rec-foto');
      if (inputFoto && inputFoto.files && inputFoto.files[0]) {
        const formData = new FormData();
        formData.append('foto', inputFoto.files[0]);
        await fetch(`/api/receitas/${receitaSalvaId}/foto`, {
          method: 'POST',
          body: formData,
        });
      }

      fecharModalReceita();
      await carregarReceitas();
    } catch (err) {
      alert(`Falha ao salvar receita: ${err.message}`);
    } finally {
      if (btnSalvar) btnSalvar.disabled = false;
    }
  });

  window.confirmarExcluirReceita = async function (id, nome) {
    if (!confirm(`Deseja realmente excluir a receita "${nome}"?`)) return;

    try {
      await api(`/api/receitas/${id}`, { method: 'DELETE' });
      await carregarReceitas();
    } catch (err) {
      alert(`Falha ao excluir receita: ${err.message}`);
    }
  };

  // ── Inicialização ─────────────────────────────────────────────────────────
  function init() {
    $('#btn-nova-receita')?.addEventListener('click', abrirModalNovaReceita);

    let debounceTimer;
    $('#busca-receitas')?.addEventListener('input', (e) => {
      clearTimeout(debounceTimer);
      debounceTimer = setTimeout(() => {
        carregarReceitas(e.target.value.trim());
      }, 250);
    });

    // Registra carregador na navegação de abas do app.js
    if (window.carregadores) {
      window.carregadores['receitas'] = carregarReceitas;
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
