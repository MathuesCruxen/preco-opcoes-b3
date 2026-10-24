# Preço de opções na B3

Calcula o preço teórico de opções de ações brasileiras com uma árvore binomial (Cox-Ross-Rubinstein) e compara com os últimos prêmios negociados no mercado.

- **Volatilidade:** histórica dos últimos 12 meses (Yahoo Finance), anualizada por 252 dias úteis
- **Taxa livre de risco:** meta Selic (série 432 do Banco Central)
- **Prêmios de mercado:** [opcoes.net.br](https://opcoes.net.br), só opções negociadas no último pregão e com strike até 15% do preço da ação
- Opções americanas (exercício antecipado) e europeias

## Instalação

```bash
pip install -r requirements.txt
```

## Uso

```python
from PrecoOpcao import compararComMercado, graficoCalls, graficoPuts

# tabela com mercado x teórico
compararComMercado("PETR4", "18/12/2026", passos=500)

# gráfico de prêmio por strike, salvo como PNG
graficoCalls("PETR4", "18/12/2026", passos=500)
graficoPuts("PETR4", "18/12/2026", passos=500)
```

Ou rode direto o script: `python PrecoOpcao.py`.

O gráfico mostra o prêmio de mercado e o teórico por strike, junto com a média e o desvio padrão do erro do modelo.
