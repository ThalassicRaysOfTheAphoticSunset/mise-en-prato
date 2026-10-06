/* A PWA do cupomzinho. Sem framework e sem build: o que está aqui é o que roda.
   Valor monetário chega da API como string (o servidor não emite float de
   propósito) e é formatado, nunca recalculado no navegador. */
'use strict';

const $ = (s) => document.querySelector(s);
const dinheiro = (v) =>
  v == null ? '—' : Number(v).toLocaleString('pt-BR', { style: 'currency', currency: 'BRL' });
const quantia = (q) => {
  const n = Number(q);
  return Number.isInteger(n) ? String(n) : n.toFixed(3).replace(/0+$/, '').replace('.', ',');
};
/* "6 Un" / "0,876 KG" / "—" quando as unidades divergem entre compras. */
const quanto = (q, u) => (q == null ? '' : `${quantia(q)} ${u || ''}`.trim());
const dia = (iso) => (iso ? iso.slice(8, 10) + '/' + iso.slice(5, 7) + '/' + iso.slice(0, 4) : '—');
const mesLongo = (m) => {
  const [a, x] = m.split('-');
  return ['jan', 'fev', 'mar', 'abr', 'mai', 'jun',
          'jul', 'ago', 'set', 'out', 'nov', 'dez'][Number(x) - 1] + '/' + a.slice(2);
};

async function api(caminho, opcoes) {
  const r = await fetch(caminho, opcoes);
  const corpo = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(corpo.detail || `erro ${r.status}`);
  return corpo;
}

function aviso(texto, ruim) {
  const caixa = $('#envio-estado');
  caixa.textContent = texto;
  caixa.className = 'estado' + (ruim ? ' ruim' : '');
  caixa.hidden = !texto;
}

/* ── abas ─────────────────────────────────────────────────────────────────── */
const carregadores = {};
function mudarAba(nome) {
  document.querySelectorAll('#abas button').forEach((b) => b.classList.toggle('ativa', b.dataset.aba === nome));
  ['enviar', 'gastos', 'produtos', 'lista', 'revisao'].forEach((n) => {
    const el = $('#aba-' + n);
    if (el) el.hidden = n !== nome;
  });
  carregadores[nome]?.();
}
window.mudarAba = mudarAba;

document.querySelectorAll('#abas button').forEach((botao) => {
  botao.onclick = () => mudarAba(botao.dataset.aba);
});

/* ── enviar ───────────────────────────────────────────────────────────────── */
function cartaoCupom(c, selo) {
  const itens = c.itens.map((i) => `
    <tr><td>${i.descricao}<div class="fraca">${quantia(i.quantidade)} ${i.unidade || ''} ×
        ${dinheiro(i.valor_unitario)}</div></td>
        <td class="num">${dinheiro(i.valor_total)}</td></tr>`).join('');
  return `
    <div class="cartao" style="margin-top:.9rem">
      <div class="linha"><strong>${c.loja}</strong>
        <span class="selo">${selo}</span></div>
      <div class="fraca">${c.municipio || ''} · ${dia(c.emitido_em)} · ${c.forma_pagamento || ''}</div>
      ${c.avisos.length ? `<div class="estado ruim" style="margin:.7rem 0">
          ${c.avisos.map((a) => `• ${a}`).join('<br>')}</div>` : ''}
      ${c.foto ? `
        <div style="margin:.7rem 0">
          <button type="button" class="acao" onclick="mudarAba('revisao');abrirFotoDestaque('${c.foto}','Foto do cupom','${c.chave}')"
                  style="font-size:.82rem;padding:.35rem .7rem">
            📷 Consultar foto salva (${c.foto})
          </button>
        </div>` : ''}
      <table style="margin-top:.7rem">${itens}</table>
      <div class="linha" style="margin-top:.7rem;border-top:1px solid var(--borda);padding-top:.6rem">
        <span>Total pago</span><strong class="num">${dinheiro(c.valor_pago)}</strong></div>
      ${Number(c.desconto) > 0
        ? `<div class="linha fraca"><span>desconto</span><span class="num">−${dinheiro(c.desconto)}</span></div>`
        : ''}
    </div>`;
}

function desenharCupom(resposta) {
  $('#cupom-lido').innerHTML = cartaoCupom(resposta.cupom, resposta.novo ? 'novo' : 'já registrado');
}

/* Abrir um cupom já guardado. Sem isto o histórico é só uma linha com o total —
   os itens e as quantidades existem no banco e ficavam sem porta na tela. */
window.verCupom = async (chave) => {
  const c = await api('/api/cupom/' + chave);
  $('#cupom-lido').innerHTML = cartaoCupom(c, dia(c.emitido_em));
  $('#cupom-lido').scrollIntoView({ behavior: 'smooth', block: 'start' });
};

async function enviar(promessa, textoOcupado) {
  aviso(textoOcupado);
  try {
    const resposta = await promessa;
    aviso(resposta.novo ? 'Cupom registrado.' : 'Esse cupom já estava guardado.');
    desenharCupom(resposta);
    carregadores.enviar();
    return true;
  } catch (erro) {
    aviso(erro.message, true);
    return false;
  }
}

$('#foto').onchange = (ev) => {
  const arquivo = ev.target.files[0];
  if (!arquivo) return;
  const corpo = new FormData();
  corpo.append('foto', arquivo);
  enviar(api('/api/cupom/foto', { method: 'POST', body: corpo }), 'Lendo o QR Code da foto…');
  ev.target.value = '';
};

const inputHtml = $('#input-html');
if (inputHtml) {
  inputHtml.onchange = (ev) => {
    const arquivo = ev.target.files[0];
    if (!arquivo) return;
    const corpo = new FormData();
    corpo.append('arquivo', arquivo);
    enviar(api('/api/cupom/html', { method: 'POST', body: corpo }), 'Lendo o arquivo HTML da SEFAZ…');
    ev.target.value = '';
  };
}

$('#form-link').onsubmit = (ev) => {
  ev.preventDefault();
  const url = $('#link').value.trim();
  enviar(api('/api/cupom', {
    method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ url }),
  }), 'Consultando a SEFAZ…');
  $('#link').value = '';
};

/* ── entrar pela chave, quando o QR está perdido ─────────────────────────────
   O captcha é do portal do estado e quem o resolve é quem está olhando a tela.
   A imagem só é pedida quando alguém abre a gaveta: captcha tem validade curta,
   e buscar um a cada carregamento da tela seria incomodar o portal à toa. */
let desafioAberto = null;

async function abrirDesafio() {
  $('#captcha-caixa').hidden = false;
  $('#captcha-img').removeAttribute('src');
  $('#captcha').value = '';
  desafioAberto = null;
  try {
    const { desafio, imagem } = await api('/api/chave/desafio');
    desafioAberto = desafio;
    $('#captcha-img').src = imagem;
  } catch (erro) {
    aviso(erro.message, true);
  }
}

$('#alt-chave').ontoggle = () => {
  if ($('#alt-chave').open && !desafioAberto) abrirDesafio();
};
$('#captcha-outro').onclick = abrirDesafio;

$('#form-chave').onsubmit = async (ev) => {
  ev.preventDefault();
  if (!desafioAberto) {
    await abrirDesafio();
    aviso('Leia as letras da imagem e mande de novo.');
    return;
  }
  const corpo = {
    chave: $('#chave').value.trim(),
    desafio: desafioAberto,
    captcha: $('#captcha').value.trim(),
  };
  /* O captcha vale uma vez só: usado, certo ou errado, não volta. Por isso a
     imagem é trocada em qualquer desfecho, e não só quando as letras erram. */
  const deuCerto = await enviar(
    api('/api/cupom/chave', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify(corpo),
    }), 'Consultando o portal pela chave…');
  desafioAberto = null;
  if (deuCerto) {
    $('#chave').value = '';
    $('#captcha-caixa').hidden = true;
  } else {
    abrirDesafio();
  }
};

$('#manual-data').valueAsDate = new Date();

$('#form-manual').onsubmit = async (ev) => {
  ev.preventDefault();
  const ok = await enviar(api('/api/cupom/manual', {
    method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({
      chave: $('#chave').value.trim(),
      total: $('#manual-total').value.trim(),
      emitido_em: $('#manual-data').value,
    }),
  }), 'Registrando o gasto…');
  if (ok) {
    $('#chave').value = '';
    $('#manual-total').value = '';
  }
};

carregadores.enviar = async () => {
  const { cupons, revisao } = await api('/api/cupons');
  const btnRevisao = $('#btn-revisao');
  if (btnRevisao) {
    btnRevisao.textContent = revisao.length ? `Revisão (${revisao.length})` : 'Revisão';
  }
  $('#revisao').innerHTML = revisao.length ? `
    <div class="estado ruim clicavel" onclick="mudarAba('revisao')" style="margin-bottom:.6rem;cursor:pointer">
      ⚠️ <strong>${revisao.length} cupom(ns) entraram pela metade</strong>. Toque para consultar a imagem e revisar →
    </div>` : '';
  $('#lista-cupons').innerHTML = cupons.length
    ? cupons.map((c) => `
        <div class="linha clicavel" onclick="verCupom('${c.chave}')">
          <div>${c.loja}<div class="fraca">${dia(c.emitido_em)} · ${c.itens} itens · ${c.morador || '—'}${c.completo ? '' : ' · <span style="color:var(--alerta)">incompleto</span>'}</div></div>
          <strong class="num">${dinheiro(c.valor_pago)}</strong>
        </div>`).join('')
    : '<div class="fraca">Nenhum cupom ainda. Fotografe o QR do próximo.</div>';
};

/* ── gastos ───────────────────────────────────────────────────────────────── */
function barras(itens, rotulo) {
  const maior = Math.max(...itens.map((i) => Number(i.total)), 0.01);
  return itens.map((i) => `
    <div class="barra">
      <span>${i[rotulo]}</span><span class="num">${dinheiro(i.total)}</span>
      <div class="trilho"><div class="preenchida" style="width:${(Number(i.total) / maior) * 100}%"></div></div>
    </div>`).join('') || '<div class="fraca">sem dados</div>';
}

/* Gráfico de colunas em SVG puro. Barra do mês em foco fica destacada; o resto
   fica apagado, para o olho achar o mês sem precisar de legenda. */
function grafico(meses, emFoco) {
  if (!meses.length) return '<div class="fraca">sem dados</div>';
  const dados = meses.slice().reverse();
  const maior = Math.max(...dados.map((m) => Number(m.total)), 0.01);
  const largura = 100 / dados.length;
  const colunas = dados.map((m, k) => {
    const altura = (Number(m.total) / maior) * 78;
    return `<rect class="${m.mes === emFoco ? '' : 'apagada'}"
                  x="${k * largura + largura * 0.15}%" y="${88 - altura}%"
                  width="${largura * 0.7}%" height="${altura}%" rx="2"/>
            <text x="${k * largura + largura * 0.5}%" y="99%" text-anchor="middle">${mesLongo(m.mes)}</text>`;
  }).join('');
  return `<svg class="grafico" viewBox="0 0 100 100" preserveAspectRatio="none"
               role="img" aria-label="gasto por mês">${colunas}</svg>`;
}

carregadores.gastos = async () => {
  const { resumo, meses } = await api('/api/gastos');
  const delta = Number(resumo.variacao);
  $('#gastos-topo').innerHTML = `
    <div class="cartao">
      <div class="fraca">${mesLongo(resumo.mes)} · ${resumo.cupons} cupons</div>
      <div class="grande num">${dinheiro(resumo.total)}</div>
      <div class="fraca">
        ${delta === 0 ? 'igual ao mês anterior'
          : `<span class="${delta > 0 ? 'sobe' : 'desce'}">${delta > 0 ? '▲' : '▼'}
             ${dinheiro(Math.abs(delta))}</span> contra ${mesLongo(resumo.mes_anterior)}
             (${dinheiro(resumo.total_anterior)})`}
      </div>
    </div>`;
  $('#grafico-meses').innerHTML = grafico(meses, resumo.mes);
  $('#por-loja').innerHTML = barras(resumo.por_loja, 'loja');
  $('#por-morador').innerHTML = barras(resumo.por_morador, 'morador');
  $('#maiores').innerHTML = resumo.maiores.length
    ? `<table>${resumo.maiores.map((p) => `
        <tr class="clicavel" onclick="verProduto(${p.produto_id})">
          <td>${p.descricao}<div class="fraca">${quanto(p.quantidade, p.unidade)}
            ${p.compras > 1 ? `em ${p.compras} compras` : 'em 1 compra'}</div></td>
          <td class="num">${dinheiro(p.total)}</td></tr>`).join('')}</table>`
    : '<div class="fraca">sem dados</div>';
};

/* ── produtos ─────────────────────────────────────────────────────────────── */
let buscaAgendada;
$('#busca').oninput = () => {
  clearTimeout(buscaAgendada);
  buscaAgendada = setTimeout(carregadores.produtos, 250);
};

/* Sugestões de unificação. O botão que junta fica à direita e o "não são" à
   esquerda de propósito: juntar é a ação destrutiva, e não deve ser a que o
   polegar acerta sem querer. */
async function desenharUnificacao() {
  const { candidatos } = await api('/api/unificar');
  $('#unificar').innerHTML = !candidatos.length ? '' : `
    <h2>Parecem o mesmo produto</h2>
    <div class="lista">${candidatos.map((c) => `
      <div>
        <div><strong>${c.manter.descricao}</strong></div>
        <div><strong>${c.juntar.descricao}</strong></div>
        <div class="fraca" style="margin:.3rem 0 .6rem">${c.motivo}</div>
        <div style="display:flex;gap:.5rem;justify-content:space-between">
          <button class="acao" style="background:transparent;color:var(--fraca);
            border:1px solid var(--borda)"
            onclick="recusarPar(${JSON.stringify(c.manter.descricao).replace(/"/g, '&quot;')},
                                ${JSON.stringify(c.juntar.descricao).replace(/"/g, '&quot;')})">
            não são</button>
          <button class="acao" onclick="unirPar(${c.manter.id}, ${c.juntar.id})">
            juntar em “${c.manter.descricao.slice(0, 22)}”</button>
        </div>
      </div>`).join('')}</div>`;
}

window.unirPar = async (manter, juntar) => {
  await api('/api/unificar', {
    method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ manter, juntar }),
  });
  carregadores.produtos();
};

window.recusarPar = async (desc_a, desc_b) => {
  await api('/api/unificar/recusar', {
    method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ desc_a, desc_b }),
  });
  carregadores.produtos();
};

async function desenharEnriquecimento() {
  const { pendentes } = await api('/api/enriquecer');
  $('#enriquecer').innerHTML = !pendentes.length ? '' : `
    <div class="cartao">
      <div class="linha"><strong>${pendentes.length} produtos sem código de barras</strong></div>
      <div class="fraca" style="margin:.3rem 0 .6rem">Sem ele, o mesmo item escrito
        diferente em cada loja não se reconhece. Consultar o Menor Preço identifica
        os mais comprados — uma consulta por produto, só quando você pedir.</div>
      <button class="acao" onclick="descobrirCodigos()">Descobrir os 5 mais comprados</button>
      <div id="enriquecer-estado"></div>
    </div>`;
}

window.descobrirCodigos = async () => {
  const caixa = $('#enriquecer-estado');
  caixa.innerHTML = '<div class="estado">Consultando, um produto por vez…</div>';
  try {
    const r = await api('/api/enriquecer?quantos=5', { method: 'POST' });
    caixa.innerHTML = `
      <div class="estado">
        ${r.identificados.length} identificados${r.teto ? ` · ${r.teto}` : ''}
        · ${r.restantes} ainda sem código
      </div>
      ${r.identificados.map((p) => `<div class="linha"><span>${p.descricao}</span>
        <span class="selo">${p.gtin}</span></div>`).join('')}
      ${r.sem_certeza.map((p) => `<div class="linha fraca"><span>${p.descricao}</span>
        <span>${p.motivo}</span></div>`).join('')}`;
    desenharUnificacao();
  } catch (erro) {
    caixa.innerHTML = `<div class="estado ruim">${erro.message}</div>`;
  }
};

carregadores.produtos = async () => {
  $('#produto-detalhe').innerHTML = '';
  desenharEnriquecimento();
  desenharUnificacao();
  const { produtos } = await api('/api/produtos?busca=' + encodeURIComponent($('#busca').value));
  $('#lista-produtos').innerHTML = produtos.length
    ? produtos.map((p) => `
        <div class="linha clicavel" onclick="verProduto(${p.id})">
          <div>${p.descricao}
            <div class="fraca">${quanto(p.quantidade, p.unidade)} em ${p.compras}
              ${p.compras > 1 ? 'compras' : 'compra'} · último ${dinheiro(p.ultimo)}
              ${p.gtin ? '' : '· <span class="selo">sem código de barras</span>'}</div>
          </div>
          <strong class="num">${dinheiro(p.gasto)}</strong>
        </div>`).join('')
    : '<div class="fraca">nada encontrado</div>';
};

window.verProduto = async (id) => {
  const p = await api('/api/produto/' + id);
  const compras = p.compras.map((c) => `
    <tr><td>${dia(c.data)}<div class="fraca">${c.loja}</div></td>
        <td class="num">${dinheiro(c.valor_unitario)}<div class="fraca">${quantia(c.quantidade)} ${c.unidade || ''}</div></td></tr>`).join('');
  $('#produto-detalhe').innerHTML = `
    <div class="cartao" style="margin-top:.9rem">
      <div class="linha"><strong>${p.descricao}</strong>
        ${p.gtin ? `<span class="selo">${p.gtin}</span>` : ''}</div>
      <div class="linha" style="margin-top:.6rem">
        <span class="fraca">comprado</span>
        <span class="num">${quanto(p.quantidade_total, p.unidade) || '—'}
          em ${p.total_compras} ${p.total_compras > 1 ? 'compras' : 'compra'}</span></div>
      <div class="linha">
        <span class="fraca">menor pago</span><span class="num">${dinheiro(p.menor_pago)}</span></div>
      <div class="linha"><span class="fraca">mediana</span>
        <span class="num">${dinheiro(p.mediana_paga)}</span></div>
      <div class="linha"><span class="fraca">maior pago</span>
        <span class="num">${dinheiro(p.maior_pago)}</span></div>
      <h2>Suas compras</h2>
      <table>${compras || '<tr><td class="fraca">sem compras</td></tr>'}</table>
    </div>`;
  $('#produto-detalhe').scrollIntoView({ behavior: 'smooth', block: 'start' });
};

/* ── lista de compras ─────────────────────────────────────────────────────── */
let listaAtual = null;

carregadores.lista = async () => {
  const { listas, sugestoes } = await api('/api/listas');
  if (!listas.length) {
    $('#lista-cabeca').innerHTML = `<div class="cartao">
      <div class="fraca">Nenhuma lista ainda.</div>
      <button class="acao" style="margin-top:.6rem" onclick="novaLista()">Criar lista</button>
    </div>`;
    $('#lista-itens').innerHTML = $('#lista-acao').innerHTML = '';
    $('#lista-comparacao').innerHTML = '';
  } else {
    if (!listas.some((l) => l.id === listaAtual)) listaAtual = listas[0].id;
    $('#lista-cabeca').innerHTML = `
      <div class="linha">
        <select id="qual-lista" onchange="listaAtual=Number(this.value);carregadores.lista()">
          ${listas.map((l) => `<option value="${l.id}" ${l.id === listaAtual ? 'selected' : ''}>
            ${l.nome} (${l.itens})</option>`).join('')}
        </select>
        <button class="acao" onclick="novaLista()">nova</button>
      </div>`;
    await desenharLista();
  }
  $('#lista-sugestoes').innerHTML = sugestoes.map((s) => `
    <div class="linha clicavel" onclick="porNaLista(${s.produto_id})">
      <div>${s.descricao}<div class="fraca">${s.compras}
        ${s.compras > 1 ? 'compras' : 'compra'} · último ${dinheiro(s.ultimo)}</div></div>
      <span class="acao" style="padding:.3rem .7rem">+</span>
    </div>`).join('');
};

async function desenharLista() {
  const { itens, comparacao } = await api('/api/lista/' + listaAtual);
  $('#lista-itens').innerHTML = itens.length
    ? itens.map((i) => `
        <div class="linha">
          <div>${i.descricao}<div class="fraca">${quantia(i.quantidade)}
            ${i.gtin ? '· com código de barras' : ''}</div></div>
          <span class="clicavel fraca" onclick="tirarDaLista(${i.produto_id})">remover</span>
        </div>`).join('')
    : '<div class="fraca">Lista vazia. Escolha abaixo o que a casa costuma comprar.</div>';
  $('#lista-acao').innerHTML = itens.length ? `
    <button class="acao" style="width:100%;margin-top:.8rem" onclick="pesquisarPrecos()">
      Pesquisar preços na região</button>
    <p class="dica">Consulta o Menor Preço do Paraná, um item por vez. Só acontece
      quando você aperta — a app nunca consulta sozinha.</p>` : '';
  desenharComparacao(comparacao);
}

/* A cobertura vem SEMPRE colada no total. Loja que só tem metade da lista
   parece a mais barata, e deixá-la ganhar calada seria mentir com número
   verdadeiro. */
function desenharComparacao(c) {
  if (!c || !c.lojas.length) {
    $('#lista-comparacao').innerHTML = c && c.sem_preco && c.sem_preco.length
      ? `<div class="estado">Ainda não há preço para: ${c.sem_preco.join(', ')}.</div>` : '';
    return;
  }
  $('#lista-comparacao').innerHTML = `
    <h2>Onde sai mais barato</h2>
    <div class="lista">${c.lojas.map((l, k) => `
      <div>
        <div class="linha">
          <div><strong>${k === 0 && l.completa ? '★ ' : ''}${l.loja}</strong>
            <div class="fraca">${l.distancia_km != null ? `${l.distancia_km} km · ` : ''}
              ${l.cobertura} de ${l.de} itens
              ${l.mais_antigo ? `· desde ${dia(l.mais_antigo)}` : ''}
              ${l.completa ? '' : `· falta ${l.falta.join(', ')}`}</div></div>
          <strong class="num">${dinheiro(l.total)}</strong>
        </div>
      </div>`).join('')}</div>
    ${c.sem_preco.length ? `<div class="estado" style="margin-top:.7rem">
      Sem preço em lugar nenhum: ${c.sem_preco.join(', ')}.</div>` : ''}
    <p class="dica">Preços dos últimos ${c.janela_dias} dias, do que a casa pagou
      e do que a região praticou.</p>`;
}

window.novaLista = async () => {
  const nome = prompt('Nome da lista', 'compras da semana');
  if (!nome) return;
  const r = await api('/api/listas', {
    method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ nome }),
  });
  listaAtual = r.id;
  carregadores.lista();
};

window.porNaLista = async (produto_id) => {
  if (!listaAtual) { await window.novaLista(); if (!listaAtual) return; }
  await api(`/api/lista/${listaAtual}/item`, {
    method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ produto_id, quantidade: '1' }),
  });
  desenharLista();
};

window.tirarDaLista = async (produto_id) => {
  await api(`/api/lista/${listaAtual}/item/${produto_id}`, { method: 'DELETE' });
  desenharLista();
};

window.pesquisarPrecos = async () => {
  const caixa = $('#lista-estado');
  caixa.hidden = false;
  caixa.className = 'estado';
  caixa.textContent = 'Consultando o Menor Preço, um item por vez…';
  try {
    const r = await api(`/api/lista/${listaAtual}/pesquisar`, { method: 'POST' });
    const partes = [`${r.consultados} itens consultados`, `${r.guardados} preços novos`];
    if (r.teto) partes.push(r.teto);
    if (r.recusados.length) partes.push(`${r.recusados.length} respostas recusadas`);
    if (r.sem_resultado.length) partes.push(`sem resultado: ${r.sem_resultado.join(', ')}`);
    caixa.textContent = partes.join(' · ');
    desenharComparacao(r.comparacao);
  } catch (erro) {
    caixa.className = 'estado ruim';
    caixa.textContent = erro.message;
  }
};

/* ── revisão e consulta de fotos ─────────────────────────────────────────── */
let estadoFotoDestaque = { rotacao: 0, ampliada: false, arquivo: null };

function formatarChave(chave) {
  return chave ? chave.replace(/(\d{4})(?=\d)/g, '$1 ') : '';
}

function atualizarTransformacaoFoto() {
  const img = $('#img-destaque');
  if (!img) return;
  const { rotacao, ampliada } = estadoFotoDestaque;
  img.style.transform = `rotate(${rotacao}deg)`;
  img.classList.toggle('ampliada', ampliada);
  if (!ampliada && (rotacao === 90 || rotacao === 270)) {
    img.style.maxWidth = '60vh';
    img.style.maxHeight = '80vw';
  } else if (!ampliada) {
    img.style.maxWidth = '100%';
    img.style.maxHeight = '60vh';
  }
}

window.abrirFotoDestaque = (arquivo, titulo, chave) => {
  estadoFotoDestaque = {
    rotacao: arquivo.includes('2026-09-26') ? 90 : 0,
    ampliada: false,
    arquivo,
  };
  const caixa = $('#revisao-destaque-foto');
  $('#destaque-foto-titulo').textContent = titulo || arquivo;
  $('#destaque-foto-selo').textContent = arquivo;
  $('#destaque-foto-subtitulo').textContent = chave ? `Chave: ${formatarChave(chave)}` : '';
  const img = $('#img-destaque');
  img.src = '/api/falha/' + arquivo;
  atualizarTransformacaoFoto();

  let acoesHtml = '';
  if (chave) {
    acoesHtml = `
      <div class="linha" style="gap:.5rem;flex-wrap:wrap">
        <button type="button" class="acao" onclick="navigator.clipboard.writeText('${chave}');aviso('Chave copiada!')">
          📋 Copiar chave
        </button>
        <button type="button" class="acao" onclick="preencherChave('${chave}')">
          ⚡ Consultar pela chave
        </button>
        <label class="acao" style="cursor:pointer;display:inline-flex;align-items:center;gap:.3rem;margin:0">
          📄 Enviar HTML
          <input type="file" accept=".html,.htm" hidden onchange="enviarHtmlCupom(this)">
        </label>
        <button type="button" class="acao" onclick="preencherManual('${chave}')">
          ✍ Registrar total
        </button>
      </div>`;
  }
  $('#destaque-foto-acoes').innerHTML = acoesHtml;
  caixa.hidden = false;
  caixa.scrollIntoView({ behavior: 'smooth', block: 'start' });
};

$('#btn-girar-destaque').onclick = () => {
  estadoFotoDestaque.rotacao = (estadoFotoDestaque.rotacao + 90) % 360;
  atualizarTransformacaoFoto();
};

$('#btn-zoom-destaque').onclick = () => {
  estadoFotoDestaque.ampliada = !estadoFotoDestaque.ampliada;
  $('#btn-zoom-destaque').textContent = estadoFotoDestaque.ampliada ? '🔍 Ajustar' : '🔍 Ampliar';
  atualizarTransformacaoFoto();
};

$('#img-destaque').onclick = () => {
  estadoFotoDestaque.ampliada = !estadoFotoDestaque.ampliada;
  $('#btn-zoom-destaque').textContent = estadoFotoDestaque.ampliada ? '🔍 Ajustar' : '🔍 Ampliar';
  atualizarTransformacaoFoto();
};

$('#btn-fechar-destaque').onclick = () => {
  $('#revisao-destaque-foto').hidden = true;
};

window.preencherChave = async (chave) => {
  mudarAba('enviar');
  const det = $('#alt-chave');
  if (det) det.open = true;
  $('#chave').value = chave;
  await abrirDesafio();
  $('#captcha').focus();
  $('#alt-chave').scrollIntoView({ behavior: 'smooth', block: 'start' });
};

window.preencherManual = (chave, emitidoEm) => {
  mudarAba('enviar');
  const detChave = $('#alt-chave');
  if (detChave) detChave.open = true;
  $('#chave').value = chave;
  if (emitidoEm) {
    $('#manual-data').value = emitidoEm.slice(0, 10);
  }
  const detManual = $('#form-manual').closest('details');
  if (detManual) detManual.open = true;
  $('#manual-total').focus();
  $('#manual-total').scrollIntoView({ behavior: 'smooth', block: 'start' });
};

window.enviarHtmlCupom = (input) => {
  const arquivo = input.files?.[0];
  if (!arquivo) return;
  const corpo = new FormData();
  corpo.append('arquivo', arquivo);
  enviar(api('/api/cupom/html', { method: 'POST', body: corpo }), 'Lendo o arquivo HTML da SEFAZ…');
  input.value = '';
};

carregadores.revisao = async () => {
  const [{ revisao }, { falhas }] = await Promise.all([
    api('/api/cupons'),
    api('/api/falhas'),
  ]);

  const btn = $('#btn-revisao');
  if (btn) btn.textContent = revisao.length ? `Revisão (${revisao.length})` : 'Revisão';

  if (!revisao.length) {
    $('#revisao-cupons').innerHTML = '<div class="fraca">Nenhum cupom pendente de revisão.</div>';
  } else {
    $('#revisao-cupons').innerHTML = revisao.map((c) => {
      const fotoBtn = c.foto ? `
        <button type="button" class="acao" onclick="abrirFotoDestaque('${c.foto}', '${c.loja}', '${c.chave}')"
                style="background:var(--realce);color:var(--tinta);border:1px solid var(--borda)">
          📷 Consultar foto (${c.foto})
        </button>` : '';
      return `
        <div class="cartao" style="margin-bottom:.8rem">
          <div class="linha">
            <strong>${c.loja}</strong>
            <span class="selo aviso">incompleto</span>
          </div>
          <div class="fraca">${c.cnpj ? 'CNPJ ' + c.cnpj : ''} · ${dia(c.emitido_em || c.criado_em)} · ${c.morador || '—'}</div>
          ${c.numero ? `<div class="fraca">NFC-e nº ${c.numero} · Série ${c.serie || '—'}</div>` : ''}
          ${c.avisos.length ? `<div class="estado ruim" style="margin:.6rem 0">
              ${c.avisos.map((a) => `• ${a}`).join('<br>')}</div>` : ''}
          <div class="linha" style="margin:.6rem 0;font-family:monospace;font-size:.85rem;word-break:break-all">
            <span>${formatarChave(c.chave)}</span>
          </div>
          <div class="linha" style="gap:.4rem;flex-wrap:wrap">
            <button type="button" class="acao" onclick="navigator.clipboard.writeText('${c.chave}');aviso('Chave copiada!')">
              📋 Copiar chave
            </button>
            <button type="button" class="acao" onclick="preencherChave('${c.chave}')">
              ⚡ Consultar pela chave
            </button>
            <label class="acao" style="cursor:pointer;display:inline-flex;align-items:center;gap:.3rem;margin:0">
              📄 Enviar HTML
              <input type="file" accept=".html,.htm" hidden onchange="enviarHtmlCupom(this)">
            </label>
            <button type="button" class="acao" onclick="preencherManual('${c.chave}', '${c.emitido_em || ''}')">
              ✍ Total manual
            </button>
            ${fotoBtn}
          </div>
        </div>`;
    }).join('');

    const primeiroComFoto = revisao.find((c) => c.foto);
    if (primeiroComFoto && $('#revisao-destaque-foto').hidden) {
      abrirFotoDestaque(primeiroComFoto.foto, `Foto de ${primeiroComFoto.loja}`, primeiroComFoto.chave);
    }
  }

  if (!falhas.length) {
    $('#revisao-galeria').innerHTML = '<div class="fraca">Nenhuma foto salva.</div>';
  } else {
    $('#revisao-galeria').innerHTML = falhas.map((f) => {
      const dataFmt = f.data ? dia(f.data) + ' ' + f.data.slice(11, 16) : f.arquivo;
      const temCupom = f.cupom ? `· cupom #${f.cupom.chave.slice(-6)}` : '';
      const chaveArg = f.cupom ? `'${f.cupom.chave}'` : 'null';
      const tituloArg = f.cupom ? `'Foto do cupom (${f.cupom.loja})'` : `'Foto ${f.arquivo}'`;
      return `
        <div class="cartao-falha" onclick="abrirFotoDestaque('${f.arquivo}', ${tituloArg}, ${chaveArg})">
          <img src="/api/falha/${f.arquivo}" alt="${f.arquivo}" loading="lazy">
          <div class="info">
            <div class="data">${dataFmt}</div>
            <div class="meta">${f.tamanho_kb} KB ${temCupom}</div>
          </div>
        </div>`;
    }).join('');
  }
};

/* ── partida ──────────────────────────────────────────────────────────────── */
api('/api/eu').then((r) => { $('#quem').textContent = r.morador; })
  .catch(() => { $('#quem').textContent = 'não identificado'; });
carregadores.enviar();
if ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js').catch(() => {});
