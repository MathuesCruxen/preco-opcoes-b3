import math
import os
from datetime import datetime
from functools import cache

import matplotlib.pyplot as plt
import numpy as np
import requests
import yfinance as yf
from bcb import sgs
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

passos = 500

def precoOpcao(vol, prazo, strike, spot, riskFree, passos, tipo="call", americana=False):
    dt = prazo / passos
    up = math.exp(vol * math.sqrt(dt))
    down = 1 / up
    probQ = (math.exp(riskFree * dt) - down) / (up - down)
    desconto = math.exp(-riskFree * dt)
    sinal = 1 if tipo == "call" else -1

    # payoff em cada nó do vencimento (j = número de subidas)
    valores = [max(sinal * (spot * up**j * down**(passos - j) - strike), 0) for j in range(passos + 1)]

    # volta na árvore até hoje, descontando o valor esperado sob Q
    for i in range(passos - 1, -1, -1):
        for j in range(i + 1):
            valores[j] = desconto * (probQ * valores[j + 1] + (1 - probQ) * valores[j])
            if americana:
                exercicio = sinal * (spot * up**j * down**(i - j) - strike)
                valores[j] = max(valores[j], exercicio)

    return valores[0]


# sessão HTTP para o opcoes.net.br: tenta de novo sozinha em caso de bloqueio (429) ou falha do servidor (5xx),
# esperando cada vez mais entre as tentativas
sessao = requests.Session()
sessao.headers["User-Agent"] = "Mozilla/5.0"
sessao.mount("https://", HTTPAdapter(max_retries=Retry(total=4, backoff_factor=5,
                                                       status_forcelist=[429, 500, 502, 503, 504])))


@cache  # guarda as respostas já baixadas, para não repetir a mesma consulta na mesma execução
def baixarJson(url):
    resposta = sessao.get(url, timeout=30)
    resposta.raise_for_status()
    return resposta.json()


def historicoAcao(acao):
    # fechamentos diários dos últimos 12 meses (Yahoo Finance), uma linha por data ("2026-10-23")
    historico = yf.download(f"{acao}.SA", period="1y", auto_adjust=False, multi_level_index=False, progress=False)
    if historico.empty:
        raise ValueError(f"o Yahoo Finance não retornou dados para {acao}.SA")
    historico = historico[["Close", "Adj Close"]].dropna()
    historico.index = historico.index.strftime("%Y-%m-%d")
    return historico


def volatilidadeHistorica(historico):
    # desvio padrão dos retornos diários ln(hoje / ontem), anualizado por 252 dias úteis
    retornos = np.log(historico["Adj Close"]).diff()
    return retornos.std() * math.sqrt(252)


def taxaSelic():
    # meta Selic (% a.a., série 432 do Banco Central), convertida para taxa contínua
    selic = sgs.get({"selic": 432}, last=1)
    return math.log(1 + selic["selic"].iloc[-1] / 100)


def compararComMercado(acao, vencimento, passos, faixa=0.15):
    acao = acao.upper()
    # aceita a data como "19/11/2026" ou "2026-11-19"
    if "/" in vencimento:
        vencimento = datetime.strptime(vencimento, "%d/%m/%Y").date().isoformat()
    historico = historicoAcao(acao)
    vol = volatilidadeHistorica(historico)
    riskFree = taxaSelic()

    # opções da ação e últimos prêmios negociados (opcoes.net.br)
    url = f"https://opcoes.net.br/listaopcoes/completa?idAcao={acao}&cotacoes=true"
    vencimentos = baixarJson(url + "&listarVencimentos=true")["data"]["vencimentos"]
    disponiveis = [v["value"] for v in vencimentos]
    if vencimento not in disponiveis:
        raise ValueError(f"{acao} não tem opções com vencimento em {vencimento}. Datas disponíveis: {', '.join(disponiveis)}")
    escolhido = vencimentos[disponiveis.index(vencimento)]
    # o site só filtra pelo vencimento pedido quando listarVencimentos=false
    dados = baixarJson(url + f"&listarVencimentos=false&vencimentos={vencimento}")["data"]
    coluna = {c["name"]: c["index"] for c in dados["columns"]}
    prazo = int(escolhido["dataAttributes"]["du"]) / 252

    # só as opções negociadas no último pregão, para o prêmio ser comparável ao preço da ação
    negociadas = [o for o in dados["cotacoesOpcoes"] if o[coluna["ultimo"]] and o[coluna["data/hora"]]]
    pregao = max(o[coluna["data/hora"]][:10] for o in negociadas)
    negociadas = [o for o in negociadas if o[coluna["data/hora"]][:10] == pregao]
    fechamentos = historico["Close"]
    spot = fechamentos[pregao] if pregao in fechamentos.index else fechamentos.iloc[-1]
    # e só strikes próximos do preço da ação (faixa=0.15 -> 15% para cima e para baixo)
    negociadas = [o for o in negociadas if abs(o[coluna["strike"]] / spot - 1) <= faixa]

    print(f"{acao}  spot {spot:.2f} ({pregao})  vol 12m {vol:.2%}  Selic {math.exp(riskFree) - 1:.2%}")
    print(f"vencimento {escolhido['value']}  ({escolhido['dataAttributes']['du']} dias úteis)")
    print(f"{'opção':<10}{'tipo':<6}{'mod':<5}{'strike':>8}{'mercado':>10}{'teórico':>10}{'dif %':>9}")

    resultados = []
    for opcao in sorted(negociadas, key=lambda o: (o[coluna["tipo"]], o[coluna["strike"]])):
        ticker = opcao[coluna["ticker"]].split("_")[0]
        tipo = opcao[coluna["tipo"]].lower()
        modalidade = opcao[coluna["mod."]]
        strike = opcao[coluna["strike"]]
        mercado = opcao[coluna["ultimo"]]
        teorico = precoOpcao(vol, prazo, strike, spot, riskFree, passos, tipo, modalidade == "A")
        diferenca = teorico / mercado - 1
        print(f"{ticker:<10}{tipo:<6}{modalidade:<5}{strike:>8.2f}{mercado:>10.2f}{teorico:>10.2f}{diferenca:>9.1%}")
        resultados.append({"opcao": ticker, "tipo": tipo, "modalidade": modalidade, "strike": strike,
                           "mercado": mercado, "teorico": teorico, "diferenca": diferenca})
    return resultados


def graficoPremios(acao, vencimento, passos, tipo="call", faixa=0.15):
    # prêmio das opções por strike: uma linha para o mercado e outra para o teórico
    opcoes = [r for r in compararComMercado(acao, vencimento, passos, faixa) if r["tipo"] == tipo]
    strikes = [o["strike"] for o in opcoes]

    figura, eixo = plt.subplots(figsize=(10, 5.5), facecolor="#fcfcfb")
    eixo.set_facecolor("#fcfcfb")
    if len(opcoes) < 2:
        print(f"aviso: só {len(opcoes)} {tipo}(s) negociada(s) nesse vencimento e faixa de strike; não dá para traçar uma linha")
    # os marcadores mostram cada opção, inclusive quando há um ponto só
    eixo.plot(strikes, [o["mercado"] for o in opcoes], color="#2a78d6", linewidth=2, marker="o", markersize=5, label="Mercado")
    eixo.plot(strikes, [o["teorico"] for o in opcoes], color="#eb6834", linewidth=2, marker="o", markersize=5, label="Teórico (árvore binomial)")

    eixo.set_title(f"Prêmio das {tipo}s de {acao.upper()} por strike (vencimento {vencimento})",
                   loc="left", color="#0b0b0b", fontsize=13, pad=30)

    # erro do modelo em cada strike (teórico - mercado): média e desvio padrão, em R$ e em %
    erros = np.array([o["teorico"] - o["mercado"] for o in opcoes])
    errosPct = np.array([o["diferenca"] for o in opcoes])
    if len(opcoes) >= 2:
        resumo = (f"Erro do modelo (teórico − mercado): média R$ {erros.mean():+.2f} ({errosPct.mean():+.1%})"
                  f"   ·   desvio padrão R$ {erros.std(ddof=1):.2f} ({errosPct.std(ddof=1):.1%})"
                  f"   ·   {len(opcoes)} opções")
        print(resumo)
        # "\$" evita que o matplotlib leia o texto entre dois "$" como fórmula matemática
        eixo.text(0, 1.02, resumo.replace("$", r"\$"), transform=eixo.transAxes, color="#52514e", fontsize=9.5)
    eixo.set_xlabel("Strike (R$)", color="#52514e")
    eixo.set_ylabel("Prêmio (R$)", color="#52514e")
    eixo.set_ylim(bottom=0)
    eixo.grid(axis="y", color="#e1e0d9", linewidth=0.8)
    eixo.set_axisbelow(True)
    eixo.tick_params(colors="#898781", length=0)
    for lado in ("top", "right", "left"):
        eixo.spines[lado].set_visible(False)
    eixo.spines["bottom"].set_color("#c3c2b7")
    eixo.legend(frameon=False, labelcolor="#52514e")

    arquivo = f"{tipo}s_{acao.upper()}_{vencimento.replace('/', '-')}.png"
    figura.savefig(arquivo, dpi=150, bbox_inches="tight")
    print(f"gráfico salvo em {arquivo}")
    plt.close(figura)
    # abre o PNG no visualizador de imagens do Windows; o script termina sem esperar a janela ser fechada
    os.startfile(arquivo)


def graficoCalls(acao, vencimento, passos, faixa=0.15):
    graficoPremios(acao, vencimento, passos, tipo="call", faixa=faixa)


def graficoPuts(acao, vencimento, passos, faixa=0.15):
    graficoPremios(acao, vencimento, passos, tipo="put", faixa=faixa)


if __name__ == "__main__":
    graficoCalls("PETR4", "18/12/2026", passos=500)
    graficoCalls("BBAS3", "18/12/2026", passos=500)
    graficoCalls("VALE3", "18/12/2026", passos=500)
