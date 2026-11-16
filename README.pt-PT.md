<p align="center"><img src="maestro-mark.svg" width="84" alt=""/></p>
<h1 align="center">Maestro AI</h1>
<p align="center"><b>Uma só porta de entrada para todos os agentes de IA que pagas.</b><br/>Encaminha cada pedido para o motor certo, divide os trabalhos grandes num plano multiagente e mantém cada modelo dentro da quota.</p>
<p align="center"><a href="README.md">🇬🇧 English</a> · <a href="#funcionalidades">Funcionalidades</a> · <a href="#o-painel-estratégia">Painel Estratégia</a> · <a href="#temas-mundos">Temas</a> · <a href="#início-rápido">Arranque rápido</a> · <a href="ORCHESTRATION.md">Desenho</a></p>
<p align="center"><img alt="Python 3.11+" src="https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white"/> <img alt="Licença" src="https://img.shields.io/badge/licen%C3%A7a-PolyForm%20NC%201.0-blue"/> <img alt="Motores" src="https://img.shields.io/badge/motores-Claude%20%C2%B7%20Codex%20%C2%B7%20Gemini%20%C2%B7%20DeepSeek-8A2BE2"/> <img alt="PWA" src="https://img.shields.io/badge/PWA-computador%20%C2%B7%20tablet%20%C2%B7%20telem%C3%B3vel-0A7"/></p>

<p align="center"><img src="docs/screenshots/maestro-desktop.jpg" width="900" alt="Maestro AI"/></p>

> **Licença:** código disponível, não open source. Gratuito para uso pessoal, estudo, investigação e lazer sob [PolyForm Noncommercial 1.0.0](LICENSE). O uso comercial exige licença paga — ver [COMMERCIAL.md](COMMERCIAL.md) (preço de referência: 100 € na Europa).

## Porquê

As subscrições e os créditos de API esgotam-se em alturas diferentes, cada CLI tem os seus limites e uma tarefa longa pode gastar uma semana de quota numa tarde. O Maestro trata-os como uma frota: sabe o que resta a cada motor, encaminha por domínio e dimensão e muda sozinho para outro quando um se esgota.

## Como funciona

```text
pedido ─► triagem ─► simples? ─► um motor ─► resposta
          (domínio,   │
           dimensão,  └─ grande? ─► plano (DAG em JSON)
           quota)                  ├─ scout        (só leitura, barato)
                                   ├─ especialista (escreve código) ┐ em paralelo
                                   └─ revisor      (verifica)       ┘ onde independentes
                                              │
                              gate de QA ◄────┘ ─► resposta + painel Estratégia
```

## Funcionalidades

| | |
|---|---|
| **Encaminhamento e fallback** | Classifica por domínio e dimensão e muda por quota (ex.: Codex → Antigravity → Claude → DeepSeek). |
| **Fluxos multiagente** | Pedidos complexos viram um grafo de dependências (scout → especialista → revisão), com tarefas de leitura em paralelo. Planos e resultados ficam guardados e podem ser retomados. |
| **Painel Estratégia** | Ao lado do chat: o raciocínio do orquestrador, o grafo de agentes, o estado de cada nó, o motor/modelo usado e a cronologia de decisões. Acompanha o pedido que estás a ler; sem nenhum à vista mostra o último ou o plano ao vivo. |
| **Orçamentos de quota** | As quotas semanais dividem-se em orçamentos diários; o DeepSeek tem um orçamento em dinheiro por ciclo, com barra diária e linhas amarela/vermelha para uso extra justificado. |
| **Scouts só de leitura** | Um modo restrito da API DeepSeek que só lista pastas, lê excertos e faz grep dentro do projeto. |
| **Estúdio web** | Chats e pastas com arquivo, fila de pedidos, quotas e frota ao vivo, seis mundos visuais, interface PT/EN e PWA instalável em computador, tablet e telemóvel. |
| **Respostas interativas** | As respostas podem mostrar HTML, tabelas, gráficos e cartões de perguntas e respostas passo a passo por plataforma. |
| **Integrações opcionais** | Média do NAS, Home Assistant e impressoras 3D vivem em `maestro_features.py`; configuras tu. |

Detalhes de desenho: [ORCHESTRATION.md](ORCHESTRATION.md). Modelo de custos: [DEEPSEEK_PRICING.md](DEEPSEEK_PRICING.md).

## O painel Estratégia

<p align="center"><img src="docs/screenshots/strategy-panel.jpg" width="380" alt="Painel Estratégia"/></p>

Cada pedido multiagente tem um plano ao vivo ao lado do chat:

- **Pensamento do orquestrador** — porque é que o trabalho foi dividido assim.
- **Grafo de agentes** — scout → especialista → revisor, com estado, motor e modelo por nó.
- **Cronologia de decisões** — início, fim e orientações com hora.
- **Sempre presente** — acompanha o pedido que estás a ler, mostra o plano ao vivo enquanto corre e passa a gaveta deslizante em janelas estreitas e telemóveis.

## Capturas

O **painel Estratégia** (à direita do chat) mostra o plano que o orquestrador montou para o pedido: o raciocínio, quem faz o quê e o que está concluído.

<p align="center"><img src="docs/screenshots/maestro-tablet.jpg" width="260" alt="Tablet (PWA)"/> &nbsp; <img src="docs/screenshots/maestro-mobile.jpg" width="170" alt="Phone (PWA)"/></p>

*Tablet e telemóvel usam a mesma interface como PWA instalável; a app Windows é a mesma UI numa janela própria.*

### Temas («mundos»)

Cada mundo muda cor, tipo de letra e efeitos de toda a app. Escolhe no menu do logótipo.

<table><tr>
<td align="center"><img src="docs/screenshots/theme-matrix.jpg" width="300" alt="Matrix"/><br/><sub>Matrix</sub></td>
<td align="center"><img src="docs/screenshots/theme-scifi.jpg" width="300" alt="Sci-Fi HUD"/><br/><sub>Sci-Fi HUD</sub></td>
<td align="center"><img src="docs/screenshots/theme-cyberpunk.jpg" width="300" alt="Cyberpunk"/><br/><sub>Cyberpunk</sub></td>
</tr><tr>
<td align="center"><img src="docs/screenshots/theme-hesperia.jpg" width="300" alt="Hesperia"/><br/><sub>Hesperia</sub></td>
<td align="center"><img src="docs/screenshots/theme-dune.jpg" width="300" alt="Dune · Arrakis"/><br/><sub>Dune · Arrakis</sub></td>
<td align="center"><img src="docs/screenshots/theme-dungen.jpg" width="300" alt="Hubert Dungen"/><br/><sub>Hubert Dungen</sub></td>
</tr></table>

*As capturas usam um espaço de demonstração com conversas fictícias; as barras de quota mostram valores de demonstração.*

## Requisitos

- Linux (desenvolvido num NAS doméstico), Python 3.11+
- As CLIs de agentes que queres usar, já com sessão iniciada: `claude`, `codex`, `agy` e/ou uma chave da API DeepSeek
- Opcional, para exportar GIF: `pip install -r requirements-gif.txt` para `.gif-deps/`

## Início rápido

```bash
git clone https://github.com/hubertdungen/maestro-ai.git
cd maestro-ai
python3 maestro.py --status                # quotas, resets, saldos
python3 maestro.py --plan "o teu pedido"   # pré-visualizar o plano
python3 maestro.py "o teu pedido"          # executar
python3 maestro_web.py                     # estúdio web na porta 8109
python3 -m unittest discover -s tests -q
```

As credenciais (chaves, tokens) lêem-se do ambiente ou do estado local em `~/.local/state/maestro/`. Nunca as faças commit.

## Status

Projeto pessoal, usado diariamente pelo autor. As interfaces mudam sem aviso. Issues são bem-vindos (ver [CONTRIBUTING.pt-PT.md](CONTRIBUTING.pt-PT.md)); o suporte é feito na medida do possível, salvo licença comercial.

## Licença

Copyright © 2026 Hubert Dungen. [PolyForm Noncommercial 1.0.0](LICENSE) para uso não comercial; [licença comercial](COMMERCIAL.md) nos restantes casos. Não é aconselhamento jurídico.
