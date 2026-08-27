/* Service worker mínimo: guarda só o esqueleto da PWA para ela abrir instalada
   e sem rede. Dado NUNCA é cacheado — gasto velho na tela é pior que tela
   vazia, e o banco é a única verdade. */
const CACHE = 'cupomzinho-v2';   // v2: a gaveta da chave de acesso entrou no esqueleto
const ESQUELETO = ['/', '/estilo.css', '/app.js', '/icone.svg', '/manifest.webmanifest'];

self.addEventListener('install', (ev) => {
  ev.waitUntil(caches.open(CACHE).then((c) => c.addAll(ESQUELETO)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (ev) => {
  ev.waitUntil(caches.keys()
    .then((nomes) => Promise.all(nomes.filter((n) => n !== CACHE).map((n) => caches.delete(n))))
    .then(() => self.clients.claim()));
});

self.addEventListener('fetch', (ev) => {
  const url = new URL(ev.request.url);
  if (ev.request.method !== 'GET' || url.pathname.startsWith('/api/')) return;
  ev.respondWith(fetch(ev.request).catch(() => caches.match(ev.request)));
});
