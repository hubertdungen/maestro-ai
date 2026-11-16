# Contribuir para o Maestro AI

Obrigado pelo interesse. O Maestro AI é um projeto pessoal, usado diariamente pelo autor, por isso as alterações são revistas com o tempo disponível.

## Antes de começar

- Abre primeiro uma issue para qualquer coisa maior do que uma correção pequena, para acertarmos o rumo.
- Licença: as contribuições são aceites nos termos da [PolyForm Noncommercial 1.0.0](LICENSE) do repositório; o autor pode também oferecer o código sob a [licença comercial](COMMERCIAL.md).
- Nunca faças commit de credenciais (chaves de API, tokens, webhooks). São lidas do ambiente, de `~/.local/state/maestro/` ou de `~/.config/maestro/ha.env`.

## Fluxo de trabalho

```bash
git clone https://github.com/hubertdungen/maestro-ai.git
cd maestro-ai
git checkout -b a-minha-alteracao
python3 -m unittest discover -s tests -q   # tem de passar
```

1. Cada pull request trata de uma só alteração.
2. Acrescenta ou atualiza um teste em `tests/` quando mudas o comportamento.
3. Não incluas ficheiros de cópia (`*.bak*`), `__pycache__/` nem estado local.
4. Abre o pull request contra `main` e descreve o que mudou e porquê.

## Língua

O código e a documentação estão em inglês britânico (`README.md`) e português europeu (`README.pt-PT.md`). Mantém os dois em sintonia quando alterares o README.
