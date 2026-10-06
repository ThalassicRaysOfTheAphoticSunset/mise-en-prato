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
let modoMercadoAtivo = false;
let wakeLockSentinela = null;

async function ativarWakeLock() {
  if ('wakeLock' in navigator) {
    try {
      wakeLockSentinela = await navigator.wakeLock.request('screen');
      wakeLockSentinela.addEventListener('release', () => {
        atualizarStatusWakeLock();
      });
      atualizarStatusWakeLock();
    } catch (err) {
      console.warn('Wake Lock não pôde ser ativado:', err);
      atualizarStatusWakeLock();
    }
  }
}

async function desativarWakeLock() {
  if (wakeLockSentinela) {
    try {
      await wakeLockSentinela.release();
    } catch (_) {}
    wakeLockSentinela = null;
    atualizarStatusWakeLock();
  }
}

function atualizarStatusWakeLock() {
  const badge = $('#mercado-status-wake');
  if (!badge) return;
  if (wakeLockSentinela && !wakeLockSentinela.released) {
    badge.textContent = '💡 Tela acesa';
    badge.className = 'badge-mercado ativo';
    badge.hidden = false;
  } else if ('wakeLock' in navigator) {
    badge.textContent = '💡 Tela normal';
    badge.className = 'badge-mercado';
    badge.hidden = false;
  } else {
    badge.hidden = true;
  }
}

function atualizarStatusRede() {
  const badge = $('#mercado-status-rede');
  const online = navigator.onLine;
  if (badge) {
    badge.hidden = online;
    if (!online) {
      badge.textContent = '⚠️ Offline (Tailnet)';
    }
  }
}

window.addEventListener('online', atualizarStatusRede);
window.addEventListener('offline', atualizarStatusRede);

document.addEventListener('visibilitychange', async () => {
  if (document.visibilityState === 'visible' && modoMercadoAtivo && !$('#aba-lista').hidden) {
    await ativarWakeLock();
  }
});

const carregadores = {};
function mudarAba(nome) {
  document.querySelectorAll('#abas button').forEach((b) => b.classList.toggle('ativa', b.dataset.aba === nome));
  ['enviar', 'gastos', 'produtos', 'lista', 'revisao'].forEach((n) => {
    const el = $('#aba-' + n);
    if (el) el.hidden = n !== nome;
  });
  if (nome !== 'lista' && modoMercadoAtivo) {
    desativarWakeLock();
  } else if (nome === 'lista' && modoMercadoAtivo) {
    ativarWakeLock();
  }
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
let timerDesafio = null;
let segundosRestantesDesafio = 300;

function calcularDVCliente(chave43) {
  let soma = 0;
  let peso = 2;
  for (let i = chave43.length - 1; i >= 0; i--) {
    soma += peso * Number(chave43[i]);
    peso = peso === 9 ? 2 : peso + 1;
  }
  const resto = soma % 11;
  return (resto === 0 || resto === 1) ? '0' : String(11 - resto);
}

function validarChaveCliente(chave) {
  const digitos = chave.replace(/\D/g, '');
  if (digitos.length !== 44) return false;
  return digitos[43] === calcularDVCliente(digitos.slice(0, 43));
}

function atualizarStatusChave() {
  const input = $('#chave');
  const statusEl = $('#chave-status');
  const btnSubmit = $('#btn-submit-chave');
  if (!input || !statusEl) return;

  const digitos = input.value.replace(/\D/g, '').slice(0, 44);
  const blocos = digitos.match(/.{1,4}/g)?.join(' ') || '';
  if (input.value !== blocos) {
    input.value = blocos;
  }

  if (digitos.length === 0) {
    statusEl.textContent = '';
    if (btnSubmit) btnSubmit.disabled = false;
    return;
  }

  if (digitos.length < 44) {
    statusEl.style.color = 'var(--fraca)';
    statusEl.textContent = `${digitos.length} de 44 dígitos digitados`;
    if (btnSubmit) btnSubmit.disabled = true;
    return;
  }

  const dvCalculado = calcularDVCliente(digitos.slice(0, 43));
  if (digitos[43] === dvCalculado) {
    statusEl.style.color = 'var(--verde)';
    statusEl.textContent = '✓ Dígito verificador válido!';
    if (btnSubmit) btnSubmit.disabled = false;
  } else {
    statusEl.style.color = 'var(--alerta)';
    statusEl.textContent = `⚠️ Dígito verificador incorreto (esperado ${dvCalculado}, digitado ${digitos[43]}). Confira o número no cupom.`;
    if (btnSubmit) btnSubmit.disabled = true;
  }
}

const inputChave = $('#chave');
if (inputChave) {
  inputChave.oninput = atualizarStatusChave;
}

function iniciarTimerDesafio() {
  clearInterval(timerDesafio);
  segundosRestantesDesafio = 300;
  const timerEl = $('#captcha-timer');

  const atualizarVisor = () => {
    if (!timerEl) return;
    const min = String(Math.floor(segundosRestantesDesafio / 60)).padStart(2, '0');
    const seg = String(segundosRestantesDesafio % 60).padStart(2, '0');
    timerEl.textContent = `⏱️ Válido por ${min}:${seg}`;
    timerEl.style.color = segundosRestantesDesafio <= 30 ? 'var(--alerta)' : 'var(--fraca)';
  };

  atualizarVisor();
  timerDesafio = setInterval(() => {
    segundosRestantesDesafio -= 1;
    if (segundosRestantesDesafio <= 0) {
      clearInterval(timerDesafio);
      aviso('Desafio expirado no portal. Gerando nova imagem…', true);
      abrirDesafio();
    } else {
      atualizarVisor();
    }
  }, 1000);
}

async function abrirDesafio() {
  $('#captcha-caixa').hidden = false;
  const img = $('#captcha-img');
  img.removeAttribute('src');
  img.classList.remove('ampliado');
  $('#captcha').value = '';
  desafioAberto = null;
  clearInterval(timerDesafio);
  try {
    const { desafio, imagem } = await api('/api/chave/desafio');
    desafioAberto = desafio;
    img.src = imagem;
    iniciarTimerDesafio();
    setTimeout(() => { $('#captcha')?.focus(); }, 80);
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
  const digitos = $('#chave').value.replace(/\D/g, '');
  if (digitos.length !== 44 || !validarChaveCliente(digitos)) {
    aviso('Chave de 44 dígitos inválida. Confira o dígito verificador.', true);
    return;
  }

  if (!desafioAberto) {
    await abrirDesafio();
    aviso('Leia as letras da imagem e mande de novo.');
    return;
  }
  const corpo = {
    chave: digitos,
    desafio: desafioAberto,
    captcha: $('#captcha').value.trim().toUpperCase(),
  };

  const deuCerto = await enviar(
    api('/api/cupom/chave', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify(corpo),
    }), 'Consultando o portal pela chave…');
  desafioAberto = null;
  clearInterval(timerDesafio);
  if (deuCerto) {
    $('#chave').value = '';
    $('#chave-status').textContent = '';
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

  const nutri = resumo.perfil_nutricional;
  const caixaNutri = $('#perfil-nutricional-secao');
  if (caixaNutri) {
    if (nutri && Number(nutri.total_classificado) > 0) {
      const totalClass = Number(nutri.total_classificado);
      const fatias = nutri.por_nova.map((g) => {
        const pct = (Number(g.total) / totalClass) * 100;
        return `<div class="fatia-nova fatia-nova-${g.grupo}" style="width:${pct.toFixed(1)}%" title="${g.rotulo}: ${pct.toFixed(0)}%"></div>`;
      }).join('');

      const linhasGrupos = nutri.por_nova.map((g) => {
        const pct = ((Number(g.total) / totalClass) * 100).toFixed(0);
        return `
          <div class="linha" style="font-size:.85rem;margin-top:.35rem">
            <span><span class="badge-nova nova-${g.grupo}">NOVA ${g.grupo}</span> ${g.rotulo}</span>
            <span class="num">${dinheiro(g.total)} <span class="fraca">(${pct}%)</span></span>
          </div>`;
      }).join('');

      caixaNutri.innerHTML = `
        <div class="cartao">
          <div class="linha">
            <strong>Qualidade Alimentar da Cesta</strong>
            <span class="selo ${nutri.percentual_ultraprocessados > 40 ? 'aviso' : ''}">
              ${nutri.percentual_ultraprocessados}% ultraprocessados
            </span>
          </div>
          <div class="barra-nutri-progresso">${fatias}</div>
          <div class="fraca" style="font-size:.78rem;margin-bottom:.6rem">
            Classificação NOVA · ${dinheiro(nutri.total_classificado)} de compras identificadas
          </div>
          ${linhasGrupos}
        </div>`;
    } else {
      caixaNutri.innerHTML = '';
    }
  }

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
  let statusCat = { disponivel: false };
  try {
    statusCat = await api('/api/catalogo/status');
  } catch (_) {}

  $('#enriquecer').innerHTML = !pendentes.length ? '' : `
    <div class="cartao">
      <div class="linha"><strong>${pendentes.length} produtos sem código de barras</strong></div>
      <div class="fraca" style="margin:.3rem 0 .6rem">
        Sem código de barras, itens iguais com escritas diferentes na nota não se reconhecem.
        ${statusCat.disponivel
          ? `O catálogo satélite local tem ${statusCat.total} produtos prontos para casamento offline instantâneo.`
          : 'Dumps do Open Food Facts e CMED podem resolver os itens offline.'}
      </div>
      <div style="display:flex;gap:.5rem;flex-wrap:wrap">
        ${statusCat.disponivel ? `
          <button class="acao" onclick="enriquecerPeloCatalogoLocal()">
            Resolver offline no Catálogo (${statusCat.total})
          </button>` : ''}
        <button class="${statusCat.disponivel ? 'btn-secundario' : 'acao'}" onclick="descobrirCodigos()">
          Consultar Menor Preço (5 por vez)
        </button>
      </div>
      <div id="enriquecer-estado"></div>
    </div>`;
}

window.enriquecerPeloCatalogoLocal = async () => {
  const caixa = $('#enriquecer-estado');
  caixa.innerHTML = '<div class="estado">Buscando no catálogo satélite (offline/Open Food Facts)…</div>';
  try {
    const r = await api('/api/enriquecer/local?quantos=100', { method: 'POST' });
    caixa.innerHTML = `
      <div class="estado">
        ${r.metadados_atualizados ? `<strong>${r.metadados_atualizados} produtos enriquecidos com nutrição/marca</strong> · ` : ''}
        ${r.identificados.length} novos GTINs · ${r.restantes} pendentes
      </div>
      ${r.identificados.map((p) => `<div class="linha"><span>${p.descricao}</span>
        <span class="selo">${p.gtin}</span></div>`).join('')}`;
    carregadores.produtos();
  } catch (erro) {
    caixa.innerHTML = `<div class="estado ruim">${erro.message}</div>`;
  }
};

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
          <div>
            <div style="display:flex;align-items:center;gap:.4rem;flex-wrap:wrap">
              <span>${p.descricao}</span>
              ${p.marca ? `<span class="fraca" style="font-size:.78rem">· ${p.marca}</span>` : ''}
              ${p.nutriscore ? `<span class="badge-nutri nutri-${p.nutriscore.toLowerCase()}">${p.nutriscore}</span>` : ''}
              ${p.nova_group ? `<span class="badge-nova nova-${p.nova_group}">NOVA ${p.nova_group}</span>` : ''}
            </div>
            <div class="fraca">${quanto(p.quantidade, p.unidade)} em ${p.compras}
              ${p.compras > 1 ? 'compras' : 'compra'} · último ${dinheiro(p.ultimo)}
              ${p.gtin ? '' : '· <span class="selo">sem código de barras</span>'}</div>
          </div>
          <strong class="num">${dinheiro(p.gasto)}</strong>
        </div>`).join('')
    : '<div class="fraca">nada encontrado</div>';
};

function calcularCaminhoSuave(coords) {
  if (coords.length === 2) {
    return `M ${coords[0].x.toFixed(1)},${coords[0].y.toFixed(1)} L ${coords[1].x.toFixed(1)},${coords[1].y.toFixed(1)}`;
  }
  let d = `M ${coords[0].x.toFixed(1)},${coords[0].y.toFixed(1)}`;
  for (let i = 0; i < coords.length - 1; i++) {
    const p0 = coords[i === 0 ? i : i - 1];
    const p1 = coords[i];
    const p2 = coords[i + 1];
    const p3 = coords[i + 2 < coords.length ? i + 2 : i + 1];

    const cp1x = p1.x + (p2.x - p0.x) / 6;
    const cp1y = p1.y + (p2.y - p0.y) / 6;
    const cp2x = p2.x - (p3.x - p1.x) / 6;
    const cp2y = p2.y - (p3.y - p1.y) / 6;

    d += ` C ${cp1x.toFixed(1)},${cp1y.toFixed(1)} ${cp2x.toFixed(1)},${cp2y.toFixed(1)} ${p2.x.toFixed(1)},${p2.y.toFixed(1)}`;
  }
  return d;
}

function renderizarGraficoHistorico(p) {
  if (!p.compras || !p.compras.length) {
    return '<div class="fraca" style="margin:.6rem 0">Nenhuma compra registrada ainda.</div>';
  }
  if (p.compras.length === 1) {
    const u = p.compras[0];
    return `
      <div class="grafico-fallback-um">
        <div class="fraca">
          Apenas 1 compra registrada (${dia(u.data)} por <strong>${dinheiro(u.valor_unitario)}</strong> no ${u.loja || 'mercado'}).
          O gráfico de tendência de preço será desenhado a partir da 2ª compra da casa.
        </div>
      </div>`;
  }

  const pontos = p.compras.slice().reverse();
  const valores = pontos.map((pt) => Number(pt.valor_unitario));
  const minVal = Math.min(...valores);
  const maxVal = Math.max(...valores);
  const mediana = Number(p.mediana_paga ?? valores[Math.floor(valores.length / 2)]);

  const W = 500;
  const H = 200;
  const padLeft = 45;
  const padRight = 30;
  const padTop = 30;
  const padBottom = 35;
  const plotW = W - padLeft - padRight;
  const plotH = H - padTop - padBottom;

  const range = maxVal === minVal ? (maxVal || 1) * 0.2 : (maxVal - minVal);
  const yMin = Math.max(0, minVal - range * 0.15);
  const yMax = maxVal + range * 0.15;
  const ySpan = (yMax - yMin) || 1;

  const coords = pontos.map((pt, i) => {
    const x = padLeft + (i / (pontos.length - 1)) * plotW;
    const y = padTop + plotH - ((Number(pt.valor_unitario) - yMin) / ySpan) * plotH;
    return { x, y, pt, i };
  });

  const dLinha = calcularCaminhoSuave(coords);
  const bottomY = padTop + plotH;
  const dArea = `${dLinha} L ${coords[coords.length - 1].x.toFixed(1)},${bottomY} L ${coords[0].x.toFixed(1)},${bottomY} Z`;

  const yMed = padTop + plotH - ((mediana - yMin) / ySpan) * plotH;

  let menorIdx = 0;
  let maiorIdx = 0;
  pontos.forEach((pt, i) => {
    const v = Number(pt.valor_unitario);
    if (v < Number(pontos[menorIdx].valor_unitario)) menorIdx = i;
    if (v > Number(pontos[maiorIdx].valor_unitario)) maiorIdx = i;
  });

  const pontosSvg = coords.map((c) => {
    let cor = 'var(--verde)';
    let raio = 3.5;
    let classe = 'ponto-grafico';
    if (c.i === menorIdx && menorIdx !== maiorIdx) {
      cor = '#22c55e';
      raio = 5.5;
      classe += ' ponto-menor';
    } else if (c.i === maiorIdx && menorIdx !== maiorIdx) {
      cor = '#ef4444';
      raio = 5.5;
      classe += ' ponto-maior';
    }
    return `<circle cx="${c.x.toFixed(1)}" cy="${c.y.toFixed(1)}" r="${raio}" fill="${cor}" stroke="var(--painel)" stroke-width="2" class="${classe}" data-idx="${c.i}"/>`;
  }).join('');

  return `
    <div class="grafico-historico-container">
      <div id="tooltip-grafico-historico" class="tooltip-grafico" hidden></div>
      <svg id="svg-grafico-historico" class="grafico-historico" viewBox="0 0 ${W} ${H}"
           preserveAspectRatio="xMidYMid meet" role="img" aria-label="histórico de preços do produto"
           style="touch-action:none;user-select:none">
        <defs>
          <linearGradient id="grad-historico-preco" x1="0%" y1="0%" x2="0%" y2="100%">
            <stop offset="0%" stop-color="var(--verde)" stop-opacity="0.32"/>
            <stop offset="100%" stop-color="var(--verde)" stop-opacity="0.0"/>
          </linearGradient>
        </defs>

        <!-- Eixos de referência -->
        <line x1="${padLeft}" y1="${padTop}" x2="${padLeft}" y2="${bottomY}" stroke="var(--borda)" stroke-width="1"/>
        <line x1="${padLeft}" y1="${bottomY}" x2="${W - padRight}" y2="${bottomY}" stroke="var(--borda)" stroke-width="1"/>

        <!-- Linha da mediana (pontilhada) -->
        <line x1="${padLeft}" y1="${yMed.toFixed(1)}" x2="${W - padRight}" y2="${yMed.toFixed(1)}"
              stroke="var(--fraca)" stroke-dasharray="4,4" stroke-width="1.2" opacity="0.6"/>
        <text x="${W - padRight}" y="${(yMed - 5).toFixed(1)}" text-anchor="end" font-size="10" fill="var(--fraca)">
          mediana ${dinheiro(mediana)}
        </text>

        <!-- Rótulos do eixo Y -->
        <text x="${padLeft - 6}" y="${(padTop + 4).toFixed(1)}" text-anchor="end" font-size="9" fill="var(--fraca)">
          ${dinheiro(maxVal)}
        </text>
        <text x="${padLeft - 6}" y="${(bottomY).toFixed(1)}" text-anchor="end" font-size="9" fill="var(--fraca)">
          ${dinheiro(minVal)}
        </text>

        <!-- Rótulos do eixo X (datas) -->
        <text x="${coords[0].x.toFixed(1)}" y="${H - 10}" text-anchor="start" font-size="9" fill="var(--fraca)">
          ${dia(pontos[0].data)}
        </text>
        <text x="${coords[coords.length - 1].x.toFixed(1)}" y="${H - 10}" text-anchor="end" font-size="9" fill="var(--fraca)">
          ${dia(pontos[pontos.length - 1].data)}
        </text>

        <!-- Área sombreada sob a curva -->
        <path d="${dArea}" fill="url(#grad-historico-preco)"/>

        <!-- Linha suave de preço -->
        <path d="${dLinha}" fill="none" stroke="var(--verde)" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>

        <!-- Linha vertical de scrubbing (oculta inicialmente) -->
        <line id="scrub-linha-guia" y1="${padTop}" y2="${bottomY}" stroke="var(--tinta)" stroke-width="1.2" stroke-dasharray="3,3" opacity="0.6" hidden/>

        <!-- Pontos -->
        ${pontosSvg}

        <!-- Ponto em foco do scrubbing (oculto inicialmente) -->
        <circle id="scrub-ponto-foco" r="6.5" fill="var(--tinta)" stroke="var(--painel)" stroke-width="2.5" hidden/>
      </svg>
      <div class="grafico-legenda" style="display:flex;gap:.8rem;font-size:.78rem;color:var(--fraca);margin-top:.4rem;align-items:center;flex-wrap:wrap">
        <span><span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:#22c55e;margin-right:4px"></span>menor pago</span>
        <span><span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:#ef4444;margin-right:4px"></span>maior pago</span>
        <span><span style="display:inline-block;width:12px;border-bottom:1px dashed var(--fraca);margin-right:4px;vertical-align:middle"></span>mediana</span>
        <span style="margin-left:auto;font-style:italic">Arraste o dedo no gráfico para detalhes</span>
      </div>
    </div>`;
}

function ativarInteracaoGraficoHistorico(p) {
  const svg = document.getElementById('svg-grafico-historico');
  const tooltip = document.getElementById('tooltip-grafico-historico');
  const linhaGuia = document.getElementById('scrub-linha-guia');
  const pontoFoco = document.getElementById('scrub-ponto-foco');
  if (!svg || !tooltip || !linhaGuia || !pontoFoco || !p || !p.compras || p.compras.length < 2) return;

  const pontos = p.compras.slice().reverse();
  const W = 500;
  const H = 200;
  const padLeft = 45;
  const padRight = 30;
  const padTop = 30;
  const padBottom = 35;
  const plotW = W - padLeft - padRight;
  const plotH = H - padTop - padBottom;

  const valores = pontos.map((pt) => Number(pt.valor_unitario));
  const minVal = Math.min(...valores);
  const maxVal = Math.max(...valores);
  const range = maxVal === minVal ? (maxVal || 1) * 0.2 : (maxVal - minVal);
  const yMin = Math.max(0, minVal - range * 0.15);
  const yMax = maxVal + range * 0.15;
  const ySpan = (yMax - yMin) || 1;

  const coords = pontos.map((pt, i) => {
    const x = padLeft + (i / (pontos.length - 1)) * plotW;
    const y = padTop + plotH - ((Number(pt.valor_unitario) - yMin) / ySpan) * plotH;
    return { x, y, pt, i };
  });

  let ultimoIdx = -1;

  function atualizarScrub(e) {
    const rect = svg.getBoundingClientRect();
    const clientX = e.clientX ?? (e.touches && e.touches[0]?.clientX);
    if (clientX == null) return;
    const svgX = ((clientX - rect.left) / rect.width) * W;

    let closest = coords[0];
    let menorDist = Math.abs(coords[0].x - svgX);
    for (let i = 1; i < coords.length; i++) {
      const d = Math.abs(coords[i].x - svgX);
      if (d < menorDist) {
        menorDist = d;
        closest = coords[i];
      }
    }

    if (closest.i !== ultimoIdx) {
      navigator.vibrate?.(10);
      ultimoIdx = closest.i;
    }

    linhaGuia.setAttribute('x1', closest.x.toFixed(1));
    linhaGuia.setAttribute('x2', closest.x.toFixed(1));
    linhaGuia.hidden = false;

    pontoFoco.setAttribute('cx', closest.x.toFixed(1));
    pontoFoco.setAttribute('cy', closest.y.toFixed(1));
    pontoFoco.hidden = false;

    const pt = closest.pt;
    tooltip.hidden = false;
    tooltip.innerHTML = `
      <div style="font-weight:700;font-size:.95rem;color:var(--verde)">${dinheiro(pt.valor_unitario)}</div>
      <div style="font-size:.8rem;color:var(--tinta);margin-top:2px">${dia(pt.data)} · ${pt.loja || 'loja não informada'}</div>
      <div style="font-size:.75rem;color:var(--fraca)">Qtd: ${quantia(pt.quantidade)} ${pt.unidade || ''} (total ${dinheiro(pt.valor_total)})</div>`;

    const pctX = (closest.x / W) * 100;
    const pctY = (closest.y / H) * 100;
    tooltip.style.left = `${pctX}%`;
    tooltip.style.top = `${Math.max(8, pctY - 18)}%`;
    if (pctX < 25) {
      tooltip.style.transform = 'translate(0%, -105%)';
    } else if (pctX > 75) {
      tooltip.style.transform = 'translate(-100%, -105%)';
    } else {
      tooltip.style.transform = 'translate(-50%, -105%)';
    }
  }

  function finalizarScrub() {
    linhaGuia.hidden = true;
    pontoFoco.hidden = true;
    tooltip.hidden = true;
    ultimoIdx = -1;
  }

  svg.addEventListener('pointerdown', (e) => {
    svg.setPointerCapture?.(e.pointerId);
    atualizarScrub(e);
  });
  svg.addEventListener('pointermove', (e) => {
    if (e.buttons > 0 || e.pointerType === 'touch') {
      atualizarScrub(e);
    }
  });
  svg.addEventListener('pointerup', finalizarScrub);
  svg.addEventListener('pointercancel', finalizarScrub);
  svg.addEventListener('mouseleave', finalizarScrub);
}

window.verProduto = async (id) => {
  const p = await api('/api/produto/' + id);
  const compras = p.compras.map((c) => `
    <tr><td>${dia(c.data)}<div class="fraca">${c.loja}</div></td>
        <td class="num">${dinheiro(c.valor_unitario)}<div class="fraca">${quantia(c.quantidade)} ${c.unidade || ''}</div></td></tr>`).join('');
  const graficoHtml = renderizarGraficoHistorico(p);
  $('#produto-detalhe').innerHTML = `
    <div class="cartao" style="margin-top:.9rem">
      <div style="display:flex;gap:.8rem;align-items:center;margin-bottom:.6rem">
        ${p.foto_url ? `<img src="${p.foto_url}" alt="${p.descricao}" class="produto-thumb" loading="lazy">` : ''}
        <div style="flex:1">
          <div class="linha">
            <strong>${p.descricao}</strong>
            ${p.gtin ? `<span class="selo">${p.gtin}</span>` : ''}
          </div>
          <div style="display:flex;gap:.4rem;align-items:center;margin-top:.25rem;flex-wrap:wrap">
            ${p.marca ? `<span class="fraca" style="font-size:.82rem">${p.marca}</span>` : ''}
            ${p.nutriscore ? `<span class="badge-nutri nutri-${p.nutriscore.toLowerCase()}">Nutri-Score ${p.nutriscore}</span>` : ''}
            ${p.nova_group ? `<span class="badge-nova nova-${p.nova_group}">NOVA ${p.nova_group}</span>` : ''}
          </div>
        </div>
      </div>
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
      ${!p.gtin && !p.is_granel ? `
        <div class="cartao" style="margin-top:.8rem;border-left:3px solid var(--laranja,#f59e0b);background:rgba(245,158,11,0.04)">
          <div class="linha">
            <strong>Código de barras não associado</strong>
          </div>
          <div class="fraca" style="margin:.3rem 0 .5rem">
            Sem o código GTIN, este produto não se unifica com outros mercados e não possui perfil nutricional.
          </div>
          <div style="display:flex;gap:.5rem;flex-wrap:wrap">
            <button class="acao" onclick="buscarCandidatosGtin(${p.id})">Buscar candidatos (Menor Preço)</button>
            <button class="btn-secundario" onclick="mostrarInputGtinManual(${p.id})">Digitar código da embalagem</button>
          </div>
          <div id="painel-candidatos-${p.id}" style="margin-top:.6rem"></div>
        </div>
      ` : ''}
      <h2>Histórico de preços</h2>
      ${graficoHtml}
      <h2>Suas compras</h2>
      <table>${compras || '<tr><td class="fraca">sem compras</td></tr>'}</table>
    </div>`;
  ativarInteracaoGraficoHistorico(p);
  $('#produto-detalhe').scrollIntoView({ behavior: 'smooth', block: 'start' });
};

window.buscarCandidatosGtin = async (id) => {
  const painel = $(`#painel-candidatos-${id}`);
  painel.innerHTML = '<div class="estado">Consultando candidatos…</div>';
  try {
    const { candidatos } = await api(`/api/produto/${id}/candidatos`);
    if (!candidatos || !candidatos.length) {
      painel.innerHTML = '<div class="fraca">Nenhum código candidato encontrado. Digite manualmente se tiver a embalagem.</div>';
      return;
    }
    painel.innerHTML = `
      <div class="fraca" style="margin-bottom:.4rem">Selecione o produto correspondente:</div>
      ${candidatos.map((c) => `
        <div class="linha" style="margin-bottom:.4rem;padding:.5rem;background:var(--painel);border:1px solid var(--borda);border-radius:6px">
          <div style="flex:1">
            <div><strong style="font-family:monospace">${c.gtin}</strong> · ${c.descricao}</div>
            <div class="fraca">${c.preco ? dinheiro(c.preco) : ''} ${c.loja ? `· ${c.loja}` : ''}</div>
          </div>
          <button class="acao" onclick="atribuirGtin(${id}, '${c.gtin}')">Usar este</button>
        </div>
      `).join('')}
    `;
  } catch (err) {
    painel.innerHTML = `<div class="estado ruim">${err.message}</div>`;
  }
};

window.mostrarInputGtinManual = (id) => {
  const painel = $(`#painel-candidatos-${id}`);
  painel.innerHTML = `
    <div style="display:flex;gap:.5rem;margin-top:.4rem;align-items:center">
      <input type="text" id="input-gtin-${id}" placeholder="Código EAN/GTIN (ex: 789...)" style="flex:1;padding:.4rem;font-family:monospace">
      <button class="acao" onclick="atribuirGtin(${id}, $('#input-gtin-${id}').value)">Salvar</button>
    </div>
  `;
};

window.atribuirGtin = async (id, gtin) => {
  const painel = $(`#painel-candidatos-${id}`);
  painel.innerHTML = '<div class="estado">Salvando e buscando dados nutricionais…</div>';
  try {
    await api(`/api/produto/${id}/gtin`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ gtin }),
    });
    verProduto(id);
    carregadores.produtos();
  } catch (err) {
    painel.innerHTML = `<div class="estado ruim">${err.message}</div>`;
  }
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
      <div class="linha" style="align-items:center;flex-wrap:wrap;gap:.5rem">
        <select id="qual-lista" style="flex:1;min-width:140px" onchange="listaAtual=Number(this.value);carregadores.lista()">
          ${listas.map((l) => `<option value="${l.id}" ${l.id === listaAtual ? 'selected' : ''}>
            ${l.nome} (${l.itens})</option>`).join('')}
        </select>
        <button class="acao" onclick="novaLista()">nova</button>
        <button type="button" id="btn-modo-mercado" class="btn-modo-mercado" onclick="alternarModoMercado()">
          ${modoMercadoAtivo ? '✓ No Mercado' : '🛒 No Mercado'}
        </button>
      </div>`;
    await desenharLista();
  }
  $('#lista-sugestoes').innerHTML = sugestoes.map((s) => `
    <div class="linha clicavel" onclick="porNaLista(${s.produto_id})">
      <div>${s.descricao}
        <div class="fraca">
          ${s.is_granel ? '<span class="selo">⚖️ Granel</span> · ' : ''}
          ${s.compras} ${s.compras > 1 ? 'compras' : 'compra'} · último ${dinheiro(s.ultimo)}
        </div>
      </div>
      <span class="acao" style="padding:.3rem .7rem">+</span>
    </div>`).join('');
};

const _itensListaMap = {};
const _debounceQtd = {};

async function desenharLista() {
  const painelMercado = $('#mercado-painel');
  const secaoSugestoes = $('#secao-sugestoes');
  const listaAcao = $('#lista-acao');
  const listaComparacao = $('#lista-comparacao');

  const { itens, comparacao } = await api('/api/lista/' + listaAtual);
  for (const k in _itensListaMap) delete _itensListaMap[k];
  itens.forEach((i) => { _itensListaMap[i.produto_id] = i; });

  if (modoMercadoAtivo) {
    if (painelMercado) painelMercado.hidden = false;
    if (secaoSugestoes) secaoSugestoes.hidden = true;
    if (listaAcao) listaAcao.hidden = true;
    if (listaComparacao) listaComparacao.hidden = true;

    const chaveStorage = 'cupomzinho_carrinho_' + listaAtual;
    let carrinhoArr = [];
    try {
      carrinhoArr = JSON.parse(localStorage.getItem(chaveStorage) || '[]');
    } catch (_) {}
    const carrinhoIds = new Set(carrinhoArr);

    const aPegar = itens.filter((i) => !carrinhoIds.has(i.produto_id));
    const noCarrinho = itens.filter((i) => carrinhoIds.has(i.produto_id));

    const totalEstimadoCarrinho = noCarrinho.reduce((acc, i) =>
      acc + (Number(i.quantidade) * Number(i.ultimo_preco || 0)), 0);
    const pct = itens.length ? Math.round((noCarrinho.length / itens.length) * 100) : 0;

    if (painelMercado) {
      painelMercado.innerHTML = `
        <div class="mercado-header">
          <div class="mercado-totalizador">
            Carrinho: <strong>${noCarrinho.length}</strong> de <strong>${itens.length}</strong> itens · <strong>${dinheiro(totalEstimadoCarrinho)}</strong> estimados
          </div>
          <div class="mercado-status-badges">
            <span id="mercado-status-wake" class="badge-mercado"></span>
            <span id="mercado-status-rede" class="badge-mercado alerta" hidden>⚠️ Offline</span>
            <button type="button" class="btn-sair-mercado" onclick="alternarModoMercado()">Sair</button>
          </div>
        </div>
        <div class="barra-progresso-fundo">
          <div id="mercado-barra-progresso" class="barra-progresso-preenchimento" style="width:${pct}%"></div>
        </div>`;
    }
    atualizarStatusWakeLock();
    atualizarStatusRede();

    if (!itens.length) {
      $('#lista-itens').innerHTML = '<div class="fraca">Sua lista está vazia. Adicione itens antes de ir às compras!</div>';
      return;
    }

    let html = '';
    if (aPegar.length) {
      html += `<div class="mercado-secao-titulo">A PEGAR (${aPegar.length})</div>`;
      html += aPegar.map((i) => {
        const granel = Boolean(i.is_granel);
        const precoEst = i.ultimo_preco ? dinheiro(Number(i.quantidade) * Number(i.ultimo_preco)) : null;
        return `
          <div class="cartao-mercado" onclick="toggleItemCarrinho(${i.produto_id})" role="checkbox" aria-checked="false">
            <div class="check-circulo" aria-hidden="true"></div>
            <div class="cartao-mercado-corpo">
              <div class="cartao-mercado-nome">${i.descricao}</div>
              <div class="cartao-mercado-meta">
                <span class="chip-mercado-qtd">${quantia(i.quantidade)}${granel ? ' kg' : ' un'}</span>
                ${granel ? '<span class="selo">⚖️ Granel</span> ' : ''}
                ${precoEst ? `<span class="cartao-mercado-preco">~${precoEst}</span>` : ''}
              </div>
            </div>
          </div>`;
      }).join('');
    }

    if (noCarrinho.length) {
      html += `
        <div class="mercado-secao-titulo" style="margin-top:1.4rem">
          <span>NO CARRINHO (${noCarrinho.length})</span>
          <button type="button" class="clicavel fraca" style="background:none;border:0;text-decoration:underline;cursor:pointer" onclick="limparCarrinho()">desmarcar todos</button>
        </div>`;
      html += noCarrinho.map((i) => {
        const granel = Boolean(i.is_granel);
        const precoEst = i.ultimo_preco ? dinheiro(Number(i.quantidade) * Number(i.ultimo_preco)) : null;
        return `
          <div class="cartao-mercado marcado" onclick="toggleItemCarrinho(${i.produto_id})" role="checkbox" aria-checked="true">
            <div class="check-circulo checado" aria-hidden="true">✓</div>
            <div class="cartao-mercado-corpo">
              <div class="cartao-mercado-nome">${i.descricao}</div>
              <div class="cartao-mercado-meta">
                <span class="chip-mercado-qtd">${quantia(i.quantidade)}${granel ? ' kg' : ' un'}</span>
                ${granel ? '<span class="selo">⚖️ Granel</span> ' : ''}
                ${precoEst ? `<span class="cartao-mercado-preco">~${precoEst}</span>` : ''}
              </div>
            </div>
          </div>`;
      }).join('');
    }

    $('#lista-itens').innerHTML = html;
  } else {
    if (painelMercado) {
      painelMercado.hidden = true;
      painelMercado.innerHTML = '';
    }
    if (secaoSugestoes) secaoSugestoes.hidden = false;
    if (listaAcao) listaAcao.hidden = false;
    if (listaComparacao) listaComparacao.hidden = false;

    $('#lista-itens').innerHTML = itens.length
      ? itens.map((i) => {
          const granel = Boolean(i.is_granel);
          const passo = granel ? 0.25 : 1;
          const sufixo = granel ? ' kg' : '';
          const rotuloQtd = `${quantia(i.quantidade)}${sufixo}`;
          return `
          <div class="item-lista-linha" id="item-lista-${i.produto_id}">
            <div class="item-lista-info">
              <div><strong>${i.descricao}</strong></div>
              <div class="fraca">
                ${granel ? '<span class="selo">⚖️ Granel</span> ' : ''}
                ${i.gtin ? '<span class="selo">código de barras</span> ' : ''}
                <span class="clicavel fraca" style="margin-left:.4rem;text-decoration:underline" onclick="tirarDaLista(${i.produto_id})">remover</span>
              </div>
            </div>
            <div class="stepper">
              <button type="button" class="btn-step" onclick="passoQtd(${i.produto_id}, -${passo})" aria-label="Diminuir">−</button>
              <button type="button" class="btn-qtd-chip" onclick="abrirAjusteQtd(${i.produto_id})" id="chip-qtd-${i.produto_id}" title="Ajustar quantidade">${rotuloQtd}</button>
              <button type="button" class="btn-step" onclick="passoQtd(${i.produto_id}, +${passo})" aria-label="Aumentar">+</button>
            </div>
          </div>`;
        }).join('')
      : '<div class="fraca">Lista vazia. Escolha abaixo o que a casa costuma comprar.</div>';
    if (listaAcao) {
      listaAcao.innerHTML = itens.length ? `
        <button class="acao" style="width:100%;margin-top:.8rem" onclick="pesquisarPrecos()">
          Pesquisar preços na região</button>
        <p class="dica">Consulta o Menor Preço do Paraná, um item por vez. Só acontece
          quando você aperta — a app nunca consulta sozinha.</p>` : '';
    }
    desenharComparacao(comparacao);
  }
}

window.alternarModoMercado = async () => {
  modoMercadoAtivo = !modoMercadoAtivo;
  if (modoMercadoAtivo) {
    await ativarWakeLock();
  } else {
    await desativarWakeLock();
  }
  const btn = $('#btn-modo-mercado');
  if (btn) btn.innerHTML = modoMercadoAtivo ? '✓ No Mercado' : '🛒 No Mercado';
  await desenharLista();
};

window.toggleItemCarrinho = (produto_id) => {
  const chaveStorage = 'cupomzinho_carrinho_' + listaAtual;
  let carrinhoArr = [];
  try {
    carrinhoArr = JSON.parse(localStorage.getItem(chaveStorage) || '[]');
  } catch (_) {}
  const carrinhoIds = new Set(carrinhoArr);
  if (carrinhoIds.has(produto_id)) {
    carrinhoIds.delete(produto_id);
    navigator.vibrate?.(15);
  } else {
    carrinhoIds.add(produto_id);
    navigator.vibrate?.(25);
  }
  localStorage.setItem(chaveStorage, JSON.stringify([...carrinhoIds]));
  desenharLista();
};

window.limparCarrinho = () => {
  if (!confirm('Desmarcar todos os itens do carrinho?')) return;
  const chaveStorage = 'cupomzinho_carrinho_' + listaAtual;
  localStorage.removeItem(chaveStorage);
  desenharLista();
};

window.passoQtd = (produto_id, delta) => {
  const item = _itensListaMap[produto_id];
  if (!item) return;
  const atual = Number(item.quantidade);
  const nova = Math.max(0, Math.round((atual + delta) * 1000) / 1000);
  if (nova <= 0) {
    window.tirarDaLista(produto_id);
    return;
  }
  item.quantidade = nova;
  const chip = document.getElementById('chip-qtd-' + produto_id);
  if (chip) {
    chip.textContent = quantia(nova) + (item.is_granel ? ' kg' : '');
  }
  navigator.vibrate?.(12);

  clearTimeout(_debounceQtd[produto_id]);
  _debounceQtd[produto_id] = setTimeout(async () => {
    await api(`/api/lista/${listaAtual}/item`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ produto_id, quantidade: String(nova) }),
    });
    const { comparacao } = await api('/api/lista/' + listaAtual);
    desenharComparacao(comparacao);
  }, 350);
};

window.abrirAjusteQtd = (produto_id) => {
  const item = _itensListaMap[produto_id];
  if (!item) return;
  const modalAntigo = document.getElementById('modal-ajuste-qtd');
  if (modalAntigo) modalAntigo.remove();

  const granel = Boolean(item.is_granel);
  const presets = granel
    ? [0.25, 0.5, 0.75, 1, 1.5, 2]
    : [1, 2, 3, 4, 6, 12];

  const modal = document.createElement('div');
  modal.id = 'modal-ajuste-qtd';
  modal.className = 'modal-fundo';
  modal.onclick = (e) => { if (e.target === modal) modal.remove(); };
  modal.innerHTML = `
    <div class="modal-gaveta" onclick="event.stopPropagation()">
      <div class="linha">
        <strong>${item.descricao}</strong>
        <span class="clicavel fraca" onclick="document.getElementById('modal-ajuste-qtd').remove()">✕</span>
      </div>
      <div class="fraca" style="margin-top:.2rem">Ajustar quantidade ${granel ? '(em kg)' : '(unidades)'}</div>
      <div class="presets-grid">
        ${presets.map((p) => `<button type="button" class="btn-preset" onclick="definirQtdDireta(${produto_id}, ${p})">${quantia(p)}${granel ? ' kg' : ''}</button>`).join('')}
      </div>
      <form id="form-ajuste-qtd" onsubmit="event.preventDefault(); salvarInputQtd(${produto_id});" style="display:flex;gap:.5rem;margin-top:.6rem">
        <input type="number" id="input-qtd-modal" step="${granel ? '0.001' : '1'}" min="0.001" value="${item.quantidade}" required autofocus style="flex:1">
        <button type="submit" class="acao">Salvar</button>
      </form>
    </div>`;
  document.body.appendChild(modal);
  setTimeout(() => {
    const inp = document.getElementById('input-qtd-modal');
    if (inp) { inp.focus(); inp.select(); }
  }, 50);
};

window.definirQtdDireta = async (produto_id, valor) => {
  const modal = document.getElementById('modal-ajuste-qtd');
  if (modal) modal.remove();
  const item = _itensListaMap[produto_id];
  if (item) item.quantidade = valor;
  await api(`/api/lista/${listaAtual}/item`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ produto_id, quantidade: String(valor) }),
  });
  desenharLista();
};

window.salvarInputQtd = async (produto_id) => {
  const inp = document.getElementById('input-qtd-modal');
  if (!inp) return;
  const valor = Number(inp.value.replace(',', '.'));
  if (isNaN(valor) || valor <= 0) return;
  window.definirQtdDireta(produto_id, valor);
};


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
  const chaveStorage = 'cupomzinho_carrinho_' + listaAtual;
  try {
    let carrinhoArr = JSON.parse(localStorage.getItem(chaveStorage) || '[]');
    carrinhoArr = carrinhoArr.filter((id) => id !== produto_id);
    localStorage.setItem(chaveStorage, JSON.stringify(carrinhoArr));
  } catch (_) {}
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

let chaveAtualRevisao = null;
let fotoBancadaEstado = { escala: 1, transX: 0, transY: 0, rot: 0 };
let catalogoCarregado = false;

async function carregarCatalogoDatalist() {
  if (catalogoCarregado) return;
  try {
    const { produtos } = await api('/api/produtos');
    const dl = $('#catalogo-produtos-datalist');
    if (dl && produtos) {
      dl.innerHTML = produtos.map((p) => `<option value="${p.descricao}">`).join('');
      catalogoCarregado = true;
    }
  } catch (_) {}
}

window.fecharBancadaRevisao = () => {
  $('#revisao-bancada').hidden = true;
  chaveAtualRevisao = null;
};

window.abrirBancadaRevisao = async (chave) => {
  chaveAtualRevisao = chave;
  await carregarCatalogoDatalist();
  const cupom = await api('/api/cupom/' + chave);
  if (!cupom) return;

  $('#bancada-loja').textContent = `Revisando: ${cupom.loja}`;
  $('#bancada-meta').textContent = `${cupom.emitido_em ? dia(cupom.emitido_em) : ''} · Chave ${formatarChave(chave)}`;

  const blocoFoto = $('#bancada-foto-bloco');
  if (cupom.foto) {
    blocoFoto.hidden = false;
    const img = $('#img-bancada-foto');
    img.src = '/api/falha/' + cupom.foto;
    resetarFotoBancada();
    initGestosFoto();
  } else {
    blocoFoto.hidden = true;
  }

  $('#bancada-desconto').value = cupom.desconto != null ? cupom.desconto : '0.00';
  $('#bancada-total-pago').value = cupom.valor_pago != null ? cupom.valor_pago : (cupom.total || '0.00');

  const listaEl = $('#bancada-itens-lista');
  listaEl.innerHTML = '';
  if (cupom.itens && cupom.itens.length) {
    cupom.itens.forEach((it) => {
      listaEl.appendChild(criarElementoLinhaItem(it));
    });
  } else {
    listaEl.appendChild(criarElementoLinhaItem({ descricao: '', quantidade: 1, unidade: 'UN', valor_unitario: '0.00', valor_total: '0.00' }));
  }

  recalcularCentavosBancada();
  const bancada = $('#revisao-bancada');
  bancada.hidden = false;
  bancada.scrollIntoView({ behavior: 'smooth', block: 'start' });
};

function criarElementoLinhaItem(it) {
  const row = document.createElement('div');
  row.className = 'bancada-linha-item';
  const desc = it.descricao || '';
  const qtd = it.quantidade != null ? it.quantidade : 1;
  const un = (it.unidade || 'UN').toUpperCase();
  const unit = it.valor_unitario != null ? it.valor_unitario : '0.00';
  const tot = it.valor_total != null ? it.valor_total : '0.00';

  row.innerHTML = `
    <div style="display:flex;gap:.4rem;align-items:center">
      <input type="text" class="item-desc" list="catalogo-produtos-datalist"
             placeholder="Descrição do produto" value="${desc}" required style="flex:1">
      <button type="button" class="btn-remover-item" onclick="removerLinhaItemBancada(this)" title="Remover item">✕</button>
    </div>
    <div class="bancada-linha-campos">
      <input type="number" class="item-qtd" step="0.001" min="0.001" value="${qtd}"
             style="width:4.8rem" title="Quantidade" oninput="atualizarSubtotalLinha(this)">
      <select class="item-un" style="width:3.8rem">
        ${['UN', 'KG', 'G', 'LT', 'PC', 'CX', 'PCT', 'PT'].map((u) => `<option value="${u}" ${u === un ? 'selected' : ''}>${u}</option>`).join('')}
      </select>
      <input type="number" class="item-unit" step="0.01" min="0" value="${unit}"
             style="width:5.2rem" placeholder="Unitário" oninput="atualizarSubtotalLinha(this)">
      <span class="fraca" style="font-size:.8rem">=</span>
      <input type="number" class="item-tot" step="0.01" min="0" value="${tot}"
             style="width:5.6rem;font-weight:600" placeholder="Total" oninput="recalcularCentavosBancada()">
    </div>`;
  return row;
}

window.adicionarLinhaItemBancada = () => {
  const listaEl = $('#bancada-itens-lista');
  const nova = criarElementoLinhaItem({ descricao: '', quantidade: 1, unidade: 'UN', valor_unitario: '0.00', valor_total: '0.00' });
  listaEl.appendChild(nova);
  const input = nova.querySelector('.item-desc');
  if (input) input.focus();
  recalcularCentavosBancada();
};

window.removerLinhaItemBancada = (btn) => {
  const row = btn.closest('.bancada-linha-item');
  if (row) {
    row.remove();
    recalcularCentavosBancada();
  }
};

window.atualizarSubtotalLinha = (input) => {
  const row = input.closest('.bancada-linha-item');
  if (!row) return;
  const qtd = Number(row.querySelector('.item-qtd').value.replace(',', '.')) || 0;
  const unit = Number(row.querySelector('.item-unit').value.replace(',', '.')) || 0;
  const totInput = row.querySelector('.item-tot');
  if (totInput) {
    totInput.value = (Math.round(qtd * unit * 100) / 100).toFixed(2);
  }
  recalcularCentavosBancada();
};

window.recalcularCentavosBancada = () => {
  const linhas = document.querySelectorAll('.bancada-linha-item');
  let somaItensCentavos = 0;
  linhas.forEach((l) => {
    const totInput = l.querySelector('.item-tot');
    if (totInput) {
      const v = Number(totInput.value.replace(',', '.')) || 0;
      somaItensCentavos += Math.round(v * 100);
    }
  });

  const descVal = Number($('#bancada-desconto').value.replace(',', '.')) || 0;
  const descCentavos = Math.round(descVal * 100);
  const pagoVal = Number($('#bancada-total-pago').value.replace(',', '.')) || 0;
  const pagoCentavos = Math.round(pagoVal * 100);

  const esperadoCentavos = somaItensCentavos - descCentavos;
  const difCentavos = esperadoCentavos - pagoCentavos;

  const barra = $('#bancada-barra-centavos');
  const btnAprovar = $('#btn-aprovar-cupom');

  if (Math.abs(difCentavos) <= 2) {
    barra.className = 'barra-centavos verde';
    barra.innerHTML = `✓ <strong>Soma confere:</strong> R$ ${(somaItensCentavos / 100).toFixed(2)} = Total pago`;
    btnAprovar.disabled = false;
  } else {
    barra.className = 'barra-centavos alerta';
    const difFmt = (difCentavos / 100).toFixed(2);
    const sinal = difCentavos > 0 ? '+' : '';
    barra.innerHTML = `⚠️ <strong>Diferença de ${sinal}R$ ${difFmt}</strong> (Itens: R$ ${(somaItensCentavos / 100).toFixed(2)} | Cupom: R$ ${(pagoCentavos / 100).toFixed(2)})`;
  }
};

window.salvarBancadaRevisao = async (aprovar) => {
  if (!chaveAtualRevisao) return;
  const linhas = document.querySelectorAll('.bancada-linha-item');
  const itens = [];
  linhas.forEach((l) => {
    const desc = l.querySelector('.item-desc').value.trim();
    if (!desc) return;
    const qtd = l.querySelector('.item-qtd').value || '1';
    const un = l.querySelector('.item-un').value || 'UN';
    const unit = l.querySelector('.item-unit').value || '0.00';
    const tot = l.querySelector('.item-tot').value || '0.00';
    itens.push({ descricao: desc, quantidade: qtd, unidade: un, valor_unitario: unit, valor_total: tot });
  });

  const descVal = $('#bancada-desconto').value || '0.00';
  const pagoVal = $('#bancada-total-pago').value || '0.00';

  try {
    const r = await api(`/api/cupom/${chaveAtualRevisao}/revisar`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({
        itens,
        desconto: descVal,
        valor_pago: pagoVal,
        aprovar,
      }),
    });
    if (r.ok) {
      aviso(aprovar ? 'Cupom aprovado e concluído!' : 'Rascunho salvo com sucesso!');
      if (aprovar) {
        fecharBancadaRevisao();
        carregadores.revisao();
        carregadores.enviar();
      } else {
        recalcularCentavosBancada();
      }
    }
  } catch (err) {
    aviso('Erro ao salvar revisão: ' + err.message);
  }
};

window.girarFotoBancada = () => {
  fotoBancadaEstado.rot = (fotoBancadaEstado.rot + 90) % 360;
  aplicarTransformFotoBancada();
};

window.resetarFotoBancada = () => {
  fotoBancadaEstado = { escala: 1, transX: 0, transY: 0, rot: 0 };
  aplicarTransformFotoBancada();
};

function aplicarTransformFotoBancada() {
  const img = $('#img-bancada-foto');
  if (img) {
    img.style.transform = `translate(${fotoBancadaEstado.transX}px, ${fotoBancadaEstado.transY}px) scale(${fotoBancadaEstado.escala}) rotate(${fotoBancadaEstado.rot}deg)`;
  }
}

let gestosFotoInit = false;
function initGestosFoto() {
  if (gestosFotoInit) return;
  gestosFotoInit = true;
  const container = $('#area-foto-interativa');
  if (!container) return;

  let arrastando = false;
  let ultimoX = 0;
  let ultimoY = 0;
  let toqueDistInicial = 0;
  let toqueEscalaInicial = 1;

  container.addEventListener('wheel', (e) => {
    e.preventDefault();
    const delta = e.deltaY < 0 ? 0.15 : -0.15;
    fotoBancadaEstado.escala = Math.max(0.5, Math.min(5, fotoBancadaEstado.escala + delta));
    aplicarTransformFotoBancada();
  }, { passive: false });

  container.addEventListener('mousedown', (e) => {
    arrastando = true;
    ultimoX = e.clientX;
    ultimoY = e.clientY;
  });

  window.addEventListener('mousemove', (e) => {
    if (!arrastando) return;
    fotoBancadaEstado.transX += e.clientX - ultimoX;
    fotoBancadaEstado.transY += e.clientY - ultimoY;
    ultimoX = e.clientX;
    ultimoY = e.clientY;
    aplicarTransformFotoBancada();
  });

  window.addEventListener('mouseup', () => { arrastando = false; });

  container.addEventListener('touchstart', (e) => {
    if (e.touches.length === 1) {
      arrastando = true;
      ultimoX = e.touches[0].clientX;
      ultimoY = e.touches[0].clientY;
    } else if (e.touches.length === 2) {
      arrastando = false;
      toqueDistInicial = Math.hypot(
        e.touches[0].clientX - e.touches[1].clientX,
        e.touches[0].clientY - e.touches[1].clientY
      );
      toqueEscalaInicial = fotoBancadaEstado.escala;
    }
  }, { passive: true });

  container.addEventListener('touchmove', (e) => {
    if (e.touches.length === 1 && arrastando) {
      fotoBancadaEstado.transX += e.touches[0].clientX - ultimoX;
      fotoBancadaEstado.transY += e.touches[0].clientY - ultimoY;
      ultimoX = e.touches[0].clientX;
      ultimoY = e.touches[0].clientY;
      aplicarTransformFotoBancada();
    } else if (e.touches.length === 2 && toqueDistInicial > 0) {
      const dist = Math.hypot(
        e.touches[0].clientX - e.touches[1].clientX,
        e.touches[0].clientY - e.touches[1].clientY
      );
      const ratio = dist / toqueDistInicial;
      fotoBancadaEstado.escala = Math.max(0.5, Math.min(5, toqueEscalaInicial * ratio));
      aplicarTransformFotoBancada();
    }
  }, { passive: true });

  container.addEventListener('touchend', () => {
    arrastando = false;
    toqueDistInicial = 0;
  });
}

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
          📷 Foto salva (${c.foto})
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
            <button type="button" class="acao" onclick="abrirBancadaRevisao('${c.chave}')"
                    style="background:var(--verde);color:#fff;font-weight:600">
              🛠️ Revisar cupom
            </button>
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
