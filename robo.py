"""
Radar Licitações: robô diário.
Consulta o PNCP e manda pelo Telegram, todo dia, as licitações do estado de SP com:
1) itens desertos ou fracassados; 2) republicações; 3) dispensas por licitação deserta.
Também manda uma planilha CSV com tudo.
"""
import csv
import os
import re
import time
from datetime import date, timedelta

import requests

# ================== CONFIGURAÇÃO (pode editar) ==================
UF = "SP"
DIAS = 1                       # olha as publicações e atualizações do último dia
MODALIDADES_LICITACAO = {4: "Concorrência eletrônica", 5: "Concorrência presencial",
                         6: "Pregão eletrônico", 7: "Pregão presencial"}
MODALIDADE_DISPENSA = 8
MAX_COMPRAS_ITENS = 900        # limite de licitações checadas item por item por dia
PAUSA = 0.8                    # segundos entre consultas (o PNCP limita a velocidade)
PALAVRAS_REPUBLICACAO = ["desert", "fracassad", "republica", "repetição", "repeticao",
                         "reabertura", "reaberto", "remanescente"]
# ================================================================

CONSULTA = "https://pncp.gov.br/api/consulta/v1"
PNCP = "https://pncp.gov.br/api/pncp/v1"
H = {"Accept": "application/json", "User-Agent": "Mozilla/5.0 (radar-licitacoes)"}
ETAPA = "início"


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def get(url, params=None):
    espera = 5
    for _ in range(8):
        try:
            r = requests.get(url, params=params, headers=H, timeout=60)
            if r.status_code == 204:
                return None
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(espera)
                espera = min(espera * 2, 90)
                continue
            if r.status_code == 404:
                return None
            r.raise_for_status()
            time.sleep(PAUSA)
            return r.json()
        except requests.RequestException:
            time.sleep(espera)
            espera = min(espera * 2, 90)
    return None


def paginar(tipo, modalidade, d_ini, d_fim):
    pagina, todos = 1, []
    while True:
        j = get(f"{CONSULTA}/contratacoes/{tipo}", {"dataInicial": d_ini, "dataFinal": d_fim, "uf": UF,
                                                    "codigoModalidadeContratacao": modalidade,
                                                    "pagina": pagina, "tamanhoPagina": 50})
        if not j or not j.get("data"):
            break
        todos += j["data"]
        if pagina >= j.get("totalPaginas", 1):
            break
        pagina += 1
    return todos


def link(c):
    o = c.get("orgaoEntidade") or {}
    return f"https://pncp.gov.br/app/editais/{o.get('cnpj')}/{c.get('anoCompra')}/{c.get('sequencialCompra')}"


def linha(c, tipo, detalhe=""):
    o, u = c.get("orgaoEntidade") or {}, c.get("unidadeOrgao") or {}
    return {"tipo": tipo, "orgao": (o.get("razaoSocial") or "").title(), "municipio": u.get("municipioNome") or "",
            "modalidade": c.get("modalidadeNome") or "", "numero": c.get("numeroCompra") or "",
            "objeto": re.sub(r"\s+", " ", c.get("objetoCompra") or "").strip(),
            "valor_estimado": c.get("valorTotalEstimado") or 0, "detalhe": detalhe,
            "abertura_propostas": (c.get("dataAberturaProposta") or "")[:10],
            "encerramento_propostas": (c.get("dataEncerramentoProposta") or "")[:10],
            "link": link(c)}


def texto_de(c):
    amparo = c.get("amparoLegal") or {}
    partes = [c.get("objetoCompra"), c.get("informacaoComplementar"), c.get("processo"),
              amparo.get("nome") if isinstance(amparo, dict) else amparo]
    return " ".join(str(p or "") for p in partes).lower()


def brl(v):
    try:
        return "R$ " + f"{float(v):,.0f}".replace(",", ".")
    except (TypeError, ValueError):
        return "valor não informado"


def main():
    global ETAPA
    hoje = date.today()
    d_ini, d_fim = (hoje - timedelta(days=DIAS)).strftime("%Y%m%d"), hoje.strftime("%Y%m%d")
    resultados, vistos = [], set()

    # 1) Republicações: licitações novas com palavras-chave
    ETAPA = "republicações"
    for cod in MODALIDADES_LICITACAO:
        for c in paginar("publicacao", cod, d_ini, d_fim):
            t = texto_de(c)
            chave = next((p for p in PALAVRAS_REPUBLICACAO if p in t), None)
            if chave and link(c) not in vistos:
                vistos.add(link(c))
                resultados.append(linha(c, "Republicada", f"texto cita \"{chave}\""))
    log("Republicadas:", sum(1 for r in resultados if r["tipo"] == "Republicada"))

    # 2) Dispensas por licitação deserta ou fracassada (Lei 14.133, art. 75, III)
    ETAPA = "dispensas"
    for c in paginar("publicacao", MODALIDADE_DISPENSA, d_ini, d_fim):
        t = texto_de(c)
        amparo = c.get("amparoLegal") or {}
        nome_amparo = (amparo.get("nome") or "") if isinstance(amparo, dict) else str(amparo)
        por_amparo = re.search(r"art\.?\s*75.{0,6}\biii\b", nome_amparo.lower())
        por_texto = "desert" in t or "fracassad" in t
        if (por_amparo or por_texto) and link(c) not in vistos:
            vistos.add(link(c))
            resultados.append(linha(c, "Dispensa por deserta", nome_amparo or "texto cita licitação deserta"))
    log("Dispensas por deserta:", sum(1 for r in resultados if r["tipo"] == "Dispensa por deserta"))

    # 3) Itens desertos ou fracassados nas licitações atualizadas
    ETAPA = "itens desertos"
    atualizadas = []
    for cod in MODALIDADES_LICITACAO:
        atualizadas += paginar("atualizacao", cod, d_ini, d_fim)
    log("Licitações atualizadas:", len(atualizadas))
    for c in atualizadas[:MAX_COMPRAS_ITENS]:
        o = c.get("orgaoEntidade") or {}
        itens = get(f"{PNCP}/orgaos/{o.get('cnpj')}/compras/{c.get('anoCompra')}/{c.get('sequencialCompra')}/itens",
                    {"pagina": 1, "tamanhoPagina": 50}) or []
        ruins = [it for it in itens if (it.get("situacaoCompraItemNome") or "").lower() in ("deserto", "fracassado")]
        if ruins and link(c) not in vistos:
            vistos.add(link(c))
            desertos = sum(1 for it in ruins if (it.get("situacaoCompraItemNome") or "").lower() == "deserto")
            valor = sum(float(it.get("valorTotal") or 0) for it in ruins)
            nomes = "; ".join((it.get("descricao") or "")[:60] for it in ruins[:3])
            r = linha(c, "Itens desertos/fracassados",
                      f"{desertos} deserto(s), {len(ruins) - desertos} fracassado(s) de {len(itens)} itens: {nomes}")
            r["valor_estimado"] = valor or r["valor_estimado"]
            resultados.append(r)
    log("Com itens desertos/fracassados:", sum(1 for r in resultados if r["tipo"] == "Itens desertos/fracassados"))

    # 4) Planilha e resumo no Telegram
    ETAPA = "envio"
    campos = ["tipo", "orgao", "municipio", "modalidade", "numero", "objeto", "valor_estimado", "detalhe",
              "abertura_propostas", "encerramento_propostas", "link"]
    saida = f"licitacoes-{hoje.isoformat()}.csv"
    with open(saida, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=campos)
        w.writeheader()
        w.writerows(sorted(resultados, key=lambda r: -float(r["valor_estimado"] or 0)))

    token, chat = os.environ["TELEGRAM_TOKEN"], os.environ["TELEGRAM_CHAT_ID"]
    blocos = [f"🏛️ Radar Licitações SP · {hoje.strftime('%d/%m')}"]
    rotulos = {"Itens desertos/fracassados": "🔴 ITENS DESERTOS OU FRACASSADOS",
               "Republicada": "🔁 REPUBLICADAS", "Dispensa por deserta": "⚡ DISPENSAS POR LICITAÇÃO DESERTA"}
    for tipo, rot in rotulos.items():
        grupo = sorted([r for r in resultados if r["tipo"] == tipo], key=lambda r: -float(r["valor_estimado"] or 0))
        blocos.append(f"\n{rot} ({len(grupo)})")
        for r in grupo[:8]:
            blocos.append(f"• {r['municipio']} · {r['orgao'][:40]} · {brl(r['valor_estimado'])}\n  "
                          f"{r['objeto'][:110]}\n  {r['link']}")
        if len(grupo) > 8:
            blocos.append(f"  … e mais {len(grupo) - 8} na planilha")
    texto = "\n".join(blocos)
    for i in range(0, len(texto), 3900):
        requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                      data={"chat_id": chat, "text": texto[i:i + 3900], "disable_web_page_preview": "true"}, timeout=60)
    with open(saida, "rb") as f:
        requests.post(f"https://api.telegram.org/bot{token}/sendDocument",
                      data={"chat_id": chat, "caption": f"Planilha completa: {len(resultados)} licitações"},
                      files={"document": (saida, f, "text/csv")}, timeout=120)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        try:
            requests.post(f"https://api.telegram.org/bot{os.environ['TELEGRAM_TOKEN']}/sendMessage",
                          data={"chat_id": os.environ["TELEGRAM_CHAT_ID"],
                                "text": f"⚠️ Radar Licitações: parou na etapa \"{ETAPA}\": {str(e)[:3000]}"}, timeout=30)
        except Exception:
            pass
        raise
