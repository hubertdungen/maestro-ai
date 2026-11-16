// node tests/test_pending_work.js — a regex vive em ui/app.js (PENDING_RE); aqui só se extrai e testa.
const src = require('fs').readFileSync(__dirname + '/../ui/app.js', 'utf8');
const m = src.match(/var PENDING_RE = (\/.*\/);/);
const re = eval(m[1]);
const norm = t => t.normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();
const yes = ['Porta de qualidade: reprovada', 'O que falta para dizer feito', 'Ainda não está publicado', 'ainda não foi validado',
  'ficou por fazer', 'Pendente: commit', 'testes em falta', 'Não se pode dizer que está feito', 'still pending', "what's missing"];
const no = ['Tudo feito e verificado.', 'O serviço está a correr na porta 8109.', 'Corri az login no Azure', 'A falta de memória foi resolvida.', 'Alterei o ficheiro e confirmei com curl.'];
let bad = 0;
yes.forEach(t => { if (!re.test(norm(t))) { console.log('FALHA (devia ativar):', t); bad++; } });
no.forEach(t => { if (re.test(norm(t))) { console.log('FALHA (não devia ativar):', t); bad++; } });
if (bad) process.exit(1); console.log('ok', yes.length + no.length);
