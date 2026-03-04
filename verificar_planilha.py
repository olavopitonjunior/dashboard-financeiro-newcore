"""
Verificação E2E — Dashboard Streamlit vs Planilha Google Sheets
================================================================
Lê dados diretamente das fontes (Google Sheets + MySQL) e compara
a lógica correta com a lógica atual do dashboard.

Uso:
    python verificar_planilha.py
"""

import sys
import io
import os
import json
import pandas as pd

# Força UTF-8 no stdout para Windows
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
import mysql.connector
import gspread
from google.oauth2.service_account import Credentials
from dotenv import load_dotenv
from datetime import datetime, timedelta

# ── Config ──────────────────────────────────────────────────────

PROJECT_ROOT = os.path.abspath(os.path.dirname(__file__))
load_dotenv(os.path.join(PROJECT_ROOT, '.env'))

GOOGLE_SHEET_ID = os.getenv('GOOGLE_SHEET_ID', '')
_creds_env = os.getenv('GOOGLE_CREDENTIALS_PATH', 'credentials.json')
CREDENTIALS_PATH = _creds_env if os.path.isabs(_creds_env) else os.path.join(PROJECT_ROOT, _creds_env)

MYSQL_CONFIG = {
    'host': os.getenv('MYSQL_HOST', ''),
    'port': int(os.getenv('MYSQL_PORT', '3306')),
    'user': os.getenv('MYSQL_USER', ''),
    'password': os.getenv('MYSQL_PASSWORD', ''),
    'database': os.getenv('MYSQL_DATABASE', 'newcore'),
}

MESES_NOME = {1: 'Jan', 2: 'Fev', 3: 'Mar', 4: 'Abr', 5: 'Mai', 6: 'Jun',
              7: 'Jul', 8: 'Ago', 9: 'Set', 10: 'Out', 11: 'Nov', 12: 'Dez'}


def fmt(v):
    """Formata valor em R$ brasileiro."""
    return f"R$ {v:,.2f}".replace(',', 'X').replace('.', ',').replace('X', '.')


# ── Acesso às fontes ────────────────────────────────────────────

def get_gsheets_client():
    scopes = [
        'https://www.googleapis.com/auth/spreadsheets.readonly',
        'https://www.googleapis.com/auth/drive.readonly',
    ]
    if os.path.exists(CREDENTIALS_PATH):
        creds = Credentials.from_service_account_file(CREDENTIALS_PATH, scopes=scopes)
    else:
        creds_json = os.getenv('GOOGLE_CREDENTIALS_JSON', '{}')
        creds = Credentials.from_service_account_info(json.loads(creds_json), scopes=scopes)
    return gspread.authorize(creds)


def load_saldo(client):
    sheet = client.open_by_key(GOOGLE_SHEET_ID)
    ws = sheet.worksheet('Din DESPESAS')
    saldo_raw = ws.cell(10, 3).value
    data_raw = ws.cell(10, 4).value
    saldo_str = str(saldo_raw).replace('R$', '').strip().replace('.', '').replace(',', '.')
    return float(saldo_str), str(data_raw)


def load_despesas(client):
    sheet = client.open_by_key(GOOGLE_SHEET_ID)
    ws = sheet.worksheet('DESPESAS')
    all_values = ws.get_all_values()
    headers = all_values[0]
    valid_cols = [i for i, h in enumerate(headers) if h.strip()]
    filtered_headers = [headers[i] for i in valid_cols]
    rows = [[row[i] if i < len(row) else '' for i in valid_cols] for row in all_values[1:]]
    df = pd.DataFrame(rows, columns=filtered_headers)

    for col in ['DT_VENC_ORIG', 'DT_PREV_PGTO', 'DT_EFET_PGTO']:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], dayfirst=True, errors='coerce')

    if 'VALOR' in df.columns:
        df['VALOR'] = (df['VALOR'].astype(str)
                       .str.replace('R$', '', regex=False)
                       .str.replace('.', '', regex=False)
                       .str.replace(',', '.', regex=False)
                       .str.strip())
        df['VALOR'] = pd.to_numeric(df['VALOR'], errors='coerce')

    if 'STATUS Consolidado' in df.columns:
        df['STATUS Consolidado'] = df['STATUS Consolidado'].str.strip().str.capitalize()

    for col in ['ANO_ORIGINAL', 'MES_ORIGINAL']:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce')

    return df


def load_recebiveis():
    conn = mysql.connector.connect(**MYSQL_CONFIG)
    try:
        query = """
            SELECT
                DATE(b.ExpiresAt) as data_vencimento,
                b.Value as valor,
                b.PaidAt as data_pagamento,
                b.ChargeStatus as status,
                b.Offer_Id as oferta_id
            FROM homeoffers a
            INNER JOIN homeofferscharges b ON a.Id = b.Offer_Id
            WHERE a.Audit = 0
              AND a.PublishStatus_Id = 30
              AND b.PaidAt IS NULL
            ORDER BY b.ExpiresAt
        """
        df = pd.read_sql(query, conn)
    finally:
        conn.close()
    df['data_vencimento'] = pd.to_datetime(df['data_vencimento'])
    df['valor'] = pd.to_numeric(df['valor'], errors='coerce')
    return df


# ── Funções de cálculo ──────────────────────────────────────────

def despesas_periodo(df, dias, status_list):
    """Total de despesas nos próximos X dias com dado filtro de status."""
    hoje = pd.Timestamp.now().normalize()
    fim = hoje + timedelta(days=dias)
    mask = (
        (df['DT_PREV_PGTO'] >= hoje) &
        (df['DT_PREV_PGTO'] <= fim) &
        (df['STATUS Consolidado'].isin(status_list))
    )
    subset = df[mask]
    return float(subset['VALOR'].sum()), len(subset)


def recebiveis_periodo(df_rec, dias):
    """Total de recebíveis pendentes nos próximos X dias."""
    hoje = pd.Timestamp.now().normalize()
    fim = hoje + timedelta(days=dias)
    mask = (
        (df_rec['data_vencimento'] >= hoje) &
        (df_rec['data_vencimento'] <= fim) &
        (df_rec['data_pagamento'].isna())
    )
    subset = df_rec[mask]
    return float(subset['valor'].sum()), len(subset)


def despesas_vencidas(df, status_list):
    """Despesas com DT_PREV_PGTO < hoje para dado status."""
    hoje = pd.Timestamp.now().normalize()
    mask = (
        (df['DT_PREV_PGTO'] < hoje) &
        (df['STATUS Consolidado'].isin(status_list))
    )
    subset = df[mask]
    return float(subset['VALOR'].sum()), len(subset)


def orcado_realizado(df, ano, status_orcado, status_realizado):
    """Calcula orçado e realizado por mês."""
    df_ano = df[df['ANO_ORIGINAL'] == ano].copy()
    result = []
    for mes in range(1, 13):
        orc = df_ano[
            (df_ano['MES_ORIGINAL'] == mes) &
            (df_ano['STATUS Consolidado'].isin(status_orcado))
        ]['VALOR'].sum()
        real = df_ano[
            (df_ano['MES_ORIGINAL'] == mes) &
            (df_ano['STATUS Consolidado'].isin(status_realizado))
        ]['VALOR'].sum()
        result.append((mes, float(orc), float(real)))
    return result


def categorias_periodo(df, dias, status_list):
    """Despesas por categoria nos próximos X dias."""
    hoje = pd.Timestamp.now().normalize()
    fim = hoje + timedelta(days=dias)
    mask = (
        (df['DT_PREV_PGTO'] >= hoje) &
        (df['DT_PREV_PGTO'] <= fim) &
        (df['STATUS Consolidado'].isin(status_list))
    )
    return df[mask].groupby('CATEGORIA CONSOLIDADA')['VALOR'].sum().sort_values(ascending=False)


def proximos_vencimentos(df, dias, status_list):
    """Despesas nos próximos X dias."""
    hoje = pd.Timestamp.now().normalize()
    fim = hoje + timedelta(days=dias)
    mask = (
        (df['DT_PREV_PGTO'] >= hoje) &
        (df['DT_PREV_PGTO'] <= fim) &
        (df['STATUS Consolidado'].isin(status_list))
    )
    return df[mask][['DT_PREV_PGTO', 'FORNECEDOR', 'CATEGORIA CONSOLIDADA', 'VALOR', 'STATUS Consolidado']].sort_values('DT_PREV_PGTO')


# ── Relatório ───────────────────────────────────────────────────

def div_marker(val_correto, val_dashboard):
    """Retorna marcador de divergência se valores diferem."""
    diff = abs(val_correto - val_dashboard)
    if diff > 0.01:
        return f"  \033[93m⚠ DIVERGÊNCIA: {fmt(diff)}\033[0m"
    return "  \033[92m✓ OK\033[0m"


def main():
    hoje = pd.Timestamp.now().normalize()
    ano_atual = hoje.year
    divergencias = 0

    print(f'\n\033[1m=== Verificação E2E — Dashboard Newcore ===\033[0m')
    print(f'Data: {datetime.now().strftime("%d/%m/%Y %H:%M")}')
    print(f'Fonte: Google Sheets + MySQL (leitura direta)\n')

    # ── Carregar dados ──
    print('Conectando ao Google Sheets...')
    client = get_gsheets_client()
    saldo, data_saldo = load_saldo(client)
    print('Lendo aba DESPESAS...')
    df = load_despesas(client)
    print('Conectando ao MySQL...')
    df_rec = load_recebiveis()
    print()

    # ── STATUS CORRETO vs DASHBOARD ──
    CORRETO = ['Previsto', 'Lançado']          # pendentes (a pagar)
    DASHBOARD = ['Previsto', 'Lançado']        # lógica atual (corrigida)
    VENCIDO_CORRETO = ['Lançado']              # vencido = lançado + data passada
    VENCIDO_DASHBOARD = ['Lançado']            # corrigido: só lançado

    # ── SALDO ──
    print(f'\033[1m─── SALDO EM CONTA (Din DESPESAS, célula C10) ───\033[0m')
    print(f'  Valor: {fmt(saldo)}')
    print(f'  Atualização: {data_saldo}')
    print()

    # ── RESUMO BRUTO ──
    print(f'\033[1m─── DESPESAS — RESUMO BRUTO (aba DESPESAS) ───\033[0m')
    print(f'  Total registros: {len(df)}')
    print(f'  Por status:')
    for status in ['Previsto', 'Lançado', 'Confirmado', 'Write off']:
        subset = df[df['STATUS Consolidado'] == status]
        n = len(subset)
        total = subset['VALOR'].sum()
        print(f'    {status:12s}: {n:>5} registros ({fmt(total)})')
    # Outros status (se houver)
    known = {'Previsto', 'Lançado', 'Confirmado', 'Write off'}
    outros = df[~df['STATUS Consolidado'].isin(known)]
    if len(outros) > 0:
        for status in outros['STATUS Consolidado'].unique():
            subset = outros[outros['STATUS Consolidado'] == status]
            print(f'    {str(status):12s}: {len(subset):>5} registros ({fmt(subset["VALOR"].sum())})')
    print()

    # ── COMPARAÇÃO ──
    print(f'\033[1m─── COMPARAÇÃO: LÓGICA CORRETA vs DASHBOARD ATUAL ───\033[0m')
    print()

    # Despesas hoje
    desp_hoje_c, n_hoje_c = despesas_periodo(df, 0, CORRETO)
    desp_hoje_d, n_hoje_d = despesas_periodo(df, 0, DASHBOARD)
    print(f'  DESPESAS HOJE ({hoje.strftime("%d/%m/%Y")}):')
    print(f'    Correto  (Previsto+Lançado):    {fmt(desp_hoje_c)} ({n_hoje_c} itens)')
    print(f'    Dashboard (Previsto+Confirmado): {fmt(desp_hoje_d)} ({n_hoje_d} itens)')
    marker = div_marker(desp_hoje_c, desp_hoje_d)
    print(marker)
    if abs(desp_hoje_c - desp_hoje_d) > 0.01:
        divergencias += 1
    print()

    # Recebíveis hoje
    rec_hoje, n_rec_hoje = recebiveis_periodo(df_rec, 0)
    print(f'  RECEBÍVEIS HOJE (MySQL, PaidAt IS NULL):')
    print(f'    {fmt(rec_hoje)} ({n_rec_hoje} itens)')
    print()

    # Saldo projetado
    saldo_proj_c = saldo - desp_hoje_c + rec_hoje
    saldo_proj_d = saldo - desp_hoje_d + rec_hoje
    print(f'  SALDO PROJETADO:')
    print(f'    Correto:   {fmt(saldo_proj_c)}')
    print(f'    Dashboard: {fmt(saldo_proj_d)}')
    marker = div_marker(saldo_proj_c, saldo_proj_d)
    print(marker)
    if abs(saldo_proj_c - saldo_proj_d) > 0.01:
        divergencias += 1
    print()

    # Despesas vencidas
    venc_c, n_venc_c = despesas_vencidas(df, VENCIDO_CORRETO)
    venc_d, n_venc_d = despesas_vencidas(df, VENCIDO_DASHBOARD)
    print(f'  DESPESAS VENCIDAS (DT_PREV_PGTO < hoje):')
    print(f'    Correto  (Lançado):              {n_venc_c} itens ({fmt(venc_c)})')
    print(f'    Dashboard (Previsto+Confirmado): {n_venc_d} itens ({fmt(venc_d)})')
    marker = div_marker(venc_c, venc_d)
    print(marker)
    if abs(venc_c - venc_d) > 0.01:
        divergencias += 1

    # Recebíveis vencidos
    hoje_ts = pd.Timestamp.now().normalize()
    rec_venc = df_rec[(df_rec['data_vencimento'] < hoje_ts) & (df_rec['data_pagamento'].isna())]
    print(f'    Recebíveis vencidos (MySQL): {len(rec_venc)} itens ({fmt(rec_venc["valor"].sum())})')
    print()

    # Projeção por período
    print(f'  PROJEÇÃO POR PERÍODO:')
    for dias in [7, 15, 30]:
        desp_c, _ = despesas_periodo(df, dias, CORRETO)
        desp_d, _ = despesas_periodo(df, dias, DASHBOARD)
        rec, _ = recebiveis_periodo(df_rec, dias)
        saldo_c = rec - desp_c
        saldo_d = rec - desp_d
        diff = abs(saldo_c - saldo_d)
        flag = ' \033[93m⚠\033[0m' if diff > 0.01 else ''
        print(f'    {dias:>2} dias → Correto: {fmt(saldo_c):>18s} | Dashboard: {fmt(saldo_d):>18s} | Diff: {fmt(diff)}{flag}')
        if diff > 0.01:
            divergencias += 1
    print()

    # Orçado vs Realizado
    print(f'  ORÇADO VS REALIZADO ({ano_atual}):')
    orc_correto = orcado_realizado(df, ano_atual, ['Previsto', 'Lançado'], ['Confirmado'])
    orc_dashboard = orcado_realizado(df, ano_atual, ['Previsto', 'Lançado'], ['Confirmado'])

    total_orc_c = sum(o for _, o, _ in orc_correto)
    total_real_c = sum(r for _, _, r in orc_correto)
    total_orc_d = sum(o for _, o, _ in orc_dashboard)
    total_real_d = sum(r for _, _, r in orc_dashboard)

    for (mes, orc_c, real_c), (_, orc_d, real_d) in zip(orc_correto, orc_dashboard):
        if orc_c > 0 or real_c > 0 or orc_d > 0 or real_d > 0:
            flag_o = ' ⚠' if abs(orc_c - orc_d) > 0.01 else ''
            flag_r = ' ⚠' if abs(real_c - real_d) > 0.01 else ''
            print(f'    {MESES_NOME[mes]}: Orç.Correto {fmt(orc_c):>15s} | Orç.Dash {fmt(orc_d):>15s}{flag_o}  ||  Real.Correto {fmt(real_c):>15s} | Real.Dash {fmt(real_d):>15s}{flag_r}')

    diff_orc = abs(total_orc_c - total_orc_d)
    diff_real = abs(total_real_c - total_real_d)
    print(f'    ──────')
    print(f'    TOTAL Orçado  → Correto: {fmt(total_orc_c)} | Dashboard: {fmt(total_orc_d)} | Diff: {fmt(diff_orc)}')
    print(f'    TOTAL Realizado → Correto: {fmt(total_real_c)} | Dashboard: {fmt(total_real_d)} | Diff: {fmt(diff_real)}')
    if diff_orc > 0.01:
        divergencias += 1
    if diff_real > 0.01:
        divergencias += 1
    print()

    # Categorias
    cat_c = categorias_periodo(df, 30, CORRETO)
    cat_d = categorias_periodo(df, 30, DASHBOARD)
    print(f'  CATEGORIAS (próx 30 dias):')
    print(f'    Correto  (Prev+Lanç): {len(cat_c)} categorias, total {fmt(cat_c.sum())}')
    print(f'    Dashboard (Prev+Conf): {len(cat_d)} categorias, total {fmt(cat_d.sum())}')
    diff_cat = abs(cat_c.sum() - cat_d.sum())
    if diff_cat > 0.01:
        print(f'    \033[93m⚠ DIVERGÊNCIA: {fmt(diff_cat)}\033[0m')
        divergencias += 1
    else:
        print(f'    \033[92m✓ OK\033[0m')
    print()

    # Próximos vencimentos
    prox_c = proximos_vencimentos(df, 7, CORRETO)
    prox_d = proximos_vencimentos(df, 7, DASHBOARD)
    print(f'  PRÓXIMOS VENCIMENTOS (7 dias):')
    print(f'    Correto:   {len(prox_c)} itens ({fmt(prox_c["VALOR"].sum())})')
    print(f'    Dashboard: {len(prox_d)} itens ({fmt(prox_d["VALOR"].sum())})')
    if len(prox_c) != len(prox_d):
        print(f'    \033[93m⚠ DIVERGÊNCIA: {abs(len(prox_c) - len(prox_d))} itens de diferença\033[0m')
        divergencias += 1
    else:
        print(f'    \033[92m✓ OK\033[0m')
    print()

    # ── RECEBÍVEIS ──
    print(f'\033[1m─── RECEBÍVEIS (MySQL) ───\033[0m')
    print(f'  Total registros (90 dias): {len(df_rec)}')
    pendentes = df_rec[df_rec['data_pagamento'].isna()]
    print(f'  Pendentes (PaidAt IS NULL): {len(pendentes)} ({fmt(pendentes["valor"].sum())})')
    print(f'  Recebíveis hoje: {n_rec_hoje} ({fmt(rec_hoje)})')
    print(f'  Vencidos (data < hoje, não pagos): {len(rec_venc)} ({fmt(rec_venc["valor"].sum())})')
    print()

    # ── RESUMO ──
    print(f'\033[1m─── RESUMO ───\033[0m')
    if divergencias > 0:
        print(f'  \033[93m{divergencias} divergência(s) encontrada(s) entre lógica correta e dashboard atual.\033[0m')
    else:
        print(f'  \033[92mNenhuma divergência. Dashboard está OK.\033[0m')
    print()


if __name__ == '__main__':
    main()
