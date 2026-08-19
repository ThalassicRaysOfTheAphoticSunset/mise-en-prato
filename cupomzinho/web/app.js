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
document.querySelectorAll('#abas button').forEach((botao) => {
  botao.onclick = () => {
    document.querySelectorAll('#abas button').forEach((b) => b.classList.toggle('ativa', b === botao));
    ['enviar', 'gastos', 'produtos'].forEach((nome) => {
      $('#aba-' + nome).hidden = nome !== botao.dataset.aba;
    });
    carregadores[botao.dataset.aba]?.();
  };
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
      <div class="fraca">${c.municipio} · ${dia(c.emitido_em)} · ${c.forma_pagamento || ''}</div>
      ${c.avisos.length ? `<div class="estado ruim" style="margin:.7rem 0">
          ${c.avisos.map((a) => `• ${a}`).join('<br>')}</div>` : ''}
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
  } catch (erro) {
    aviso(erro.message, true);
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

$('#form-link').onsubmit = (ev) => {
  ev.preventDefault();
  const url = $('#link').value.trim();
  enviar(api('/api/cupom', {
    method: 'POST', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ url }),
  }), 'Consultando a SEFAZ…');
  $('#link').value = '';
};

carregadores.enviar = async () => {
  const { cupons, revisao } = await api('/api/cupons');
  $('#revisao').innerHTML = revisao.length ? `
    <div class="estado ruim" style="margin-bottom:.6rem">
      ${revisao.length} cupom(ns) entraram pela metade e esperam revisão.</div>` : '';
  $('#lista-cupons').innerHTML = cupons.length
    ? cupons.map((c) => `
        <div class="linha clicavel" onclick="verCupom('${c.chave}')">
          <div>${c.loja}<div class="fraca">${dia(c.emitido_em)} · ${c.itens} itens · ${c.morador || '—'}</div></div>
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

carregadores.produtos = async () => {
  $('#produto-detalhe').innerHTML = '';
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

/* ── partida ──────────────────────────────────────────────────────────────── */
api('/api/eu').then((r) => { $('#quem').textContent = r.morador; })
  .catch(() => { $('#quem').textContent = 'não identificado'; });
carregadores.enviar();
if ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js').catch(() => {});
