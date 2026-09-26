"""
Radar Licitações: diagnóstico.
Testa o acesso ao PNCP a partir do GitHub, mede o volume de licitações de SP
e descobre como os itens desertos e as republicações aparecem nos dados.
"""
import json
import os
import time
from collections import Counter
from datetime import date, timedelta

import requests

CONSULTA = "https://pncp.gov.br/api/consulta/v1"
PNCP = "https://pncp.gov.br/api/pncp/v1"
UF = "SP"
MODALIDADES = {4: "Concorrência eletrônica", 5: "Concorrência presencial", 6: "Pregão eletrônico",
               7: "Pregão presencial", 8: "Dispensa", 9: "Inexigibilidade"}
H = {"Accept": "application/json", "User-Agent": "Mozilla/5.0 (radar-licitacoes)"}
linhas = []


def out(*a):
    t = " ".join(str(x) for x in a)
    print(t, flush=True)
    linhas.append(t)


def get(url, params=None):
    for tentativa in range(3):
        try:
            r = requests.get(url, params=params, headers=H, timeout=60)
            if r.status_code == 204:
                return None
            r.raise_for_status()
            return r.json()
        except Exception as e:
            ultimo = e
            time.sleep(3 * (tentativa + 1))
    raise ultimo


hoje = date.today()
d1, d7 = (hoje - timedelta(days=1)).strftime("%Y%m%d"), (hoje - timedelta(days=7)).strftime("%Y%m%d")
fim = hoje.strftime("%Y%m%d")

# 1. Acesso e volume por modalidade
amostras = []
for cod, nome in MODALIDADES.items():
    for tipo in ("publicacao", "atualizacao"):
        try:
            j = get(f"{CONSULTA}/contratacoes/{tipo}", {"dataInicial": d7, "dataFinal": fim, "uf": UF,
                                                      "codigoModalidadeContratacao": cod, "pagina": 1,
                                                      "tamanhoPagina": 50})
            total = (j or {}).get("totalRegistros", 0)
            out(f"{tipo:11} | {nome:24} | 7 dias SP: {total}")
            if j and tipo == "atualizacao":
                amostras += j.get("data", [])[:8]
            if j and tipo == "publicacao" and cod == 6 and j.get("data"):
                out("CAMPOS DA CONTRATAÇÃO:", ", ".join(sorted(j["data"][0].keys())))
                out("EXEMPLO:", json.dumps(j["data"][0], ensure_ascii=False)[:1500])
        except Exception as e:
            out(f"ERRO {tipo} {nome}:", str(e)[:300])

# 2. Situação dos itens (onde aparece "Deserto")
situ_itens, situ_compras = Counter(), Counter()
exemplo_item = None
for c in amostras[:40]:
    situ_compras[c.get("situacaoCompraNome")] += 1
    try:
        cnpj = c["orgaoEntidade"]["cnpj"]
        itens = get(f"{PNCP}/orgaos/{cnpj}/compras/{c['anoCompra']}/{c['sequencialCompra']}/itens",
                    {"pagina": 1, "tamanhoPagina": 50}) or []
        for it in itens:
            situ_itens[it.get("situacaoCompraItemNome")] += 1
            if exemplo_item is None:
                exemplo_item = it
        time.sleep(0.3)
    except Exception as e:
        situ_itens[f"ERRO {str(e)[:60]}"] += 1
out("\nSITUAÇÃO DAS COMPRAS (amostra):", dict(situ_compras))
out("SITUAÇÃO DOS ITENS (amostra):", dict(situ_itens))
if exemplo_item:
    out("CAMPOS DO ITEM:", ", ".join(sorted(exemplo_item.keys())))

# 3. Palavras-chave no texto: republicação, deserta, fracassada
palavras = ["desert", "republica", "reabert", "repetic", "fracassad", "nova data"]
achados = Counter()
exemplos = []
for cod in (6, 4, 8):
    for pagina in range(1, 6):
        try:
            j = get(f"{CONSULTA}/contratacoes/publicacao", {"dataInicial": d7, "dataFinal": fim, "uf": UF,
                                                            "codigoModalidadeContratacao": cod,
                                                            "pagina": pagina, "tamanhoPagina": 50})
        except Exception as e:
            out("ERRO busca texto:", str(e)[:200])
            break
        if not j or not j.get("data"):
            break
        for c in j["data"]:
            texto = " ".join(str(c.get(k) or "") for k in ("objetoCompra", "informacaoComplementar", "processo")).lower()
            for p in palavras:
                if p in texto:
                    achados[(MODALIDADES[cod], p)] += 1
                    if len(exemplos) < 12:
                        exemplos.append(f"[{MODALIDADES[cod]}] {c.get('orgaoEntidade', {}).get('razaoSocial')} · "
                                        f"{(c.get('objetoCompra') or '')[:160]}")
        time.sleep(0.3)
out("\nPALAVRAS-CHAVE (até 250 por modalidade, 7 dias):", {f"{m} / {p}": n for (m, p), n in achados.items()})
for e in exemplos:
    out("  ", e)

with open("diagnostico.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(linhas))
token, chat = os.environ["TELEGRAM_TOKEN"], os.environ["TELEGRAM_CHAT_ID"]
with open("diagnostico.txt", "rb") as f:
    requests.post(f"https://api.telegram.org/bot{token}/sendDocument",
                  data={"chat_id": chat, "caption": "🏛️ Radar Licitações: diagnóstico. Mande este arquivo para o Claude."},
                  files={"document": ("diagnostico-licitacoes.txt", f, "text/plain")}, timeout=120)
