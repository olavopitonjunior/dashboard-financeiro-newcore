"""
Relatório Diário por Email - Fluxo de Caixa Newcore
====================================================
Envia resumo diário do fluxo de caixa por email.

Uso:
    python relatorio_email.py

Configuração (.env):
    SMTP_HOST=smtp.gmail.com
    SMTP_PORT=587
    SMTP_USER=email@empresa.com
    SMTP_PASSWORD=app_password
    EMAIL_DESTINATARIOS=financeiro@empresa.com,diretoria@empresa.com
"""

import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
import pandas as pd
import mysql.connector
import gspread
from google.oauth2.service_account import Credentials
import os
from dotenv import load_dotenv

load_dotenv()

# ============================================
# CONFIGURAÇÕES
# ============================================

GOOGLE_SHEET_ID = os.getenv('GOOGLE_SHEET_ID')
CREDENTIALS_PATH = os.getenv('GOOGLE_CREDENTIALS_PATH', 'credentials.json')

MYSQL_CONFIG = {
    'host': os.getenv('MYSQL_HOST'),
    'port': int(os.getenv('MYSQL_PORT', 3306)),
    'user': os.getenv('MYSQL_USER'),
    'password': os.getenv('MYSQL_PASSWORD'),
    'database': os.getenv('MYSQL_DATABASE', 'newcore')
}

SMTP_HOST = os.getenv('SMTP_HOST', 'smtp.gmail.com')
SMTP_PORT = int(os.getenv('SMTP_PORT', 587))
SMTP_USER = os.getenv('SMTP_USER')
SMTP_PASSWORD = os.getenv('SMTP_PASSWORD')
EMAIL_DESTINATARIOS = os.getenv('EMAIL_DESTINATARIOS', '').split(',')

# ============================================
# FUNÇÕES DE LEITURA (mesmas do dashboard)
# ============================================

def _get_gsheets_client():
    scopes = [
        'https://www.googleapis.com/auth/spreadsheets.readonly',
        'https://www.googleapis.com/auth/drive.readonly'
    ]
    creds = Credentials.from_service_account_file(CREDENTIALS_PATH, scopes=scopes)
    return gspread.authorize(creds)


def load_despesas():
    client = _get_gsheets_client()
    sheet = client.open_by_key(GOOGLE_SHEET_ID)
    worksheet = sheet.worksheet('DESPESAS')

    all_values = worksheet.get_all_values()
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

    return df


def load_saldo():
    client = _get_gsheets_client()
    sheet = client.open_by_key(GOOGLE_SHEET_ID)
    worksheet = sheet.worksheet('Din DESPESAS')
    saldo = worksheet.cell(10, 3).value
    saldo_str = str(saldo).replace('R$', '').strip()
    saldo_str = saldo_str.replace('.', '').replace(',', '.')
    return float(saldo_str)


def load_recebiveis():
    conn = mysql.connector.connect(**MYSQL_CONFIG)
    try:
        query = """
            SELECT
                DATE(ExpiresAt) as data_vencimento,
                Value as valor,
                PaidAt as data_pagamento,
                ChargeStatus as status,
                Offer_Id as oferta_id
            FROM homeofferscharges
            WHERE ExpiresAt >= CURDATE() - INTERVAL 90 DAY
            ORDER BY ExpiresAt
        """
        df = pd.read_sql(query, conn)
    finally:
        conn.close()

    df['data_vencimento'] = pd.to_datetime(df['data_vencimento'])
    df['valor'] = pd.to_numeric(df['valor'], errors='coerce')
    return df


# ============================================
# GERAÇÃO DO EMAIL
# ============================================

def gerar_html_relatorio():
    hoje = pd.Timestamp.now().normalize()

    df_despesas = load_despesas()
    saldo_conta = load_saldo()
    df_recebiveis = load_recebiveis()

    # Despesas hoje
    desp_hoje = df_despesas[
        (df_despesas['DT_PREV_PGTO'] == hoje) &
        (df_despesas['STATUS Consolidado'].isin(['Previsto', 'Confirmado']))
    ]
    total_desp_hoje = desp_hoje['VALOR'].sum()

    # Recebíveis hoje
    rec_hoje = df_recebiveis[
        (df_recebiveis['data_vencimento'] == hoje) &
        (df_recebiveis['data_pagamento'].isna())
    ]
    total_rec_hoje = rec_hoje['valor'].sum()

    saldo_projetado = saldo_conta - total_desp_hoje + total_rec_hoje

    # Vencidos
    desp_vencidas = df_despesas[
        (df_despesas['DT_PREV_PGTO'] < hoje) &
        (df_despesas['STATUS Consolidado'].isin(['Previsto', 'Confirmado']))
    ]
    rec_vencidos = df_recebiveis[
        (df_recebiveis['data_vencimento'] < hoje) &
        (df_recebiveis['data_pagamento'].isna())
    ]

    # Próximos 7 dias
    fim_7d = hoje + timedelta(days=7)
    desp_7d = df_despesas[
        (df_despesas['DT_PREV_PGTO'] >= hoje) &
        (df_despesas['DT_PREV_PGTO'] <= fim_7d) &
        (df_despesas['STATUS Consolidado'].isin(['Previsto', 'Confirmado']))
    ]['VALOR'].sum()
    rec_7d = df_recebiveis[
        (df_recebiveis['data_vencimento'] >= hoje) &
        (df_recebiveis['data_vencimento'] <= fim_7d) &
        (df_recebiveis['data_pagamento'].isna())
    ]['valor'].sum()

    # HTML
    cor_saldo = '#2ecc71' if saldo_projetado >= 0 else '#e74c3c'

    # Tabela despesas do dia
    linhas_desp = ""
    if not desp_hoje.empty:
        for _, row in desp_hoje.iterrows():
            linhas_desp += f"<tr><td>{row['FORNECEDOR']}</td><td>{row['CATEGORIA CONSOLIDADA']}</td><td>R$ {row['VALOR']:,.2f}</td></tr>"
    else:
        linhas_desp = "<tr><td colspan='3' style='text-align:center;color:#999;'>Nenhuma despesa hoje</td></tr>"

    # Tabela recebíveis do dia
    linhas_rec = ""
    if not rec_hoje.empty:
        for _, row in rec_hoje.iterrows():
            linhas_rec += f"<tr><td>Oferta #{int(row['oferta_id'])}</td><td>{row['status']}</td><td>R$ {row['valor']:,.2f}</td></tr>"
    else:
        linhas_rec = "<tr><td colspan='3' style='text-align:center;color:#999;'>Nenhum recebível hoje</td></tr>"

    # Alertas
    alertas_html = ""
    if len(desp_vencidas) > 0 or len(rec_vencidos) > 0:
        alertas_html = f"""
        <div style="background:#fff3cd;border:1px solid #ffc107;border-radius:8px;padding:12px;margin:16px 0;">
            <strong>Alertas de vencimento:</strong><br>
            {f"- {len(desp_vencidas)} despesas vencidas (R$ {desp_vencidas['VALOR'].sum():,.2f})<br>" if len(desp_vencidas) > 0 else ""}
            {f"- {len(rec_vencidos)} recebíveis vencidos (R$ {rec_vencidos['valor'].sum():,.2f})<br>" if len(rec_vencidos) > 0 else ""}
        </div>
        """

    html = f"""
    <html>
    <body style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;padding:20px;color:#333;">
        <h2 style="color:#2c3e50;border-bottom:2px solid #3498db;padding-bottom:8px;">
            Fluxo de Caixa - {hoje.strftime('%d/%m/%Y')}
        </h2>

        <div style="display:flex;gap:12px;margin:16px 0;">
            <div style="flex:1;background:#f8f9fa;border-radius:8px;padding:12px;text-align:center;">
                <div style="font-size:12px;color:#666;">Saldo em Conta</div>
                <div style="font-size:20px;font-weight:bold;">R$ {saldo_conta:,.2f}</div>
            </div>
            <div style="flex:1;background:#f8f9fa;border-radius:8px;padding:12px;text-align:center;">
                <div style="font-size:12px;color:#666;">Saldo Projetado</div>
                <div style="font-size:20px;font-weight:bold;color:{cor_saldo};">R$ {saldo_projetado:,.2f}</div>
            </div>
        </div>

        <div style="display:flex;gap:12px;margin:16px 0;">
            <div style="flex:1;background:#ffeaea;border-radius:8px;padding:12px;text-align:center;">
                <div style="font-size:12px;color:#666;">Despesas Hoje</div>
                <div style="font-size:18px;font-weight:bold;color:#e74c3c;">R$ {total_desp_hoje:,.2f}</div>
            </div>
            <div style="flex:1;background:#eafff0;border-radius:8px;padding:12px;text-align:center;">
                <div style="font-size:12px;color:#666;">Recebíveis Hoje</div>
                <div style="font-size:18px;font-weight:bold;color:#2ecc71;">R$ {total_rec_hoje:,.2f}</div>
            </div>
        </div>

        {alertas_html}

        <h3 style="color:#2c3e50;margin-top:24px;">Despesas do dia</h3>
        <table style="width:100%;border-collapse:collapse;">
            <tr style="background:#e74c3c;color:white;">
                <th style="padding:8px;text-align:left;">Fornecedor</th>
                <th style="padding:8px;text-align:left;">Categoria</th>
                <th style="padding:8px;text-align:right;">Valor</th>
            </tr>
            {linhas_desp}
        </table>

        <h3 style="color:#2c3e50;margin-top:24px;">Recebíveis do dia</h3>
        <table style="width:100%;border-collapse:collapse;">
            <tr style="background:#2ecc71;color:white;">
                <th style="padding:8px;text-align:left;">Oferta</th>
                <th style="padding:8px;text-align:left;">Status</th>
                <th style="padding:8px;text-align:right;">Valor</th>
            </tr>
            {linhas_rec}
        </table>

        <h3 style="color:#2c3e50;margin-top:24px;">Projeção 7 dias</h3>
        <div style="display:flex;gap:12px;">
            <div style="flex:1;background:#f8f9fa;border-radius:8px;padding:12px;text-align:center;">
                <div style="font-size:12px;color:#666;">Despesas</div>
                <div style="font-weight:bold;color:#e74c3c;">R$ {desp_7d:,.2f}</div>
            </div>
            <div style="flex:1;background:#f8f9fa;border-radius:8px;padding:12px;text-align:center;">
                <div style="font-size:12px;color:#666;">Recebíveis</div>
                <div style="font-weight:bold;color:#2ecc71;">R$ {rec_7d:,.2f}</div>
            </div>
            <div style="flex:1;background:#f8f9fa;border-radius:8px;padding:12px;text-align:center;">
                <div style="font-size:12px;color:#666;">Saldo</div>
                <div style="font-weight:bold;color:{'#2ecc71' if rec_7d - desp_7d >= 0 else '#e74c3c'};">R$ {rec_7d - desp_7d:,.2f}</div>
            </div>
        </div>

        <p style="color:#999;font-size:11px;margin-top:24px;border-top:1px solid #eee;padding-top:8px;">
            Dashboard Fluxo de Caixa - Newcore | Gerado em {datetime.now().strftime('%d/%m/%Y %H:%M')}
        </p>
    </body>
    </html>
    """
    return html


def enviar_email(html):
    msg = MIMEMultipart('alternative')
    msg['Subject'] = f"Fluxo de Caixa Newcore - {datetime.now().strftime('%d/%m/%Y')}"
    msg['From'] = SMTP_USER
    msg['To'] = ', '.join(EMAIL_DESTINATARIOS)

    msg.attach(MIMEText(html, 'html'))

    with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
        server.starttls()
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.sendmail(SMTP_USER, EMAIL_DESTINATARIOS, msg.as_string())

    print(f"Email enviado para: {', '.join(EMAIL_DESTINATARIOS)}")


if __name__ == '__main__':
    if not SMTP_USER or not SMTP_PASSWORD:
        print("Erro: Configure SMTP_USER e SMTP_PASSWORD no .env")
        print("Exemplo:")
        print("  SMTP_HOST=smtp.gmail.com")
        print("  SMTP_PORT=587")
        print("  SMTP_USER=seu_email@gmail.com")
        print("  SMTP_PASSWORD=sua_app_password")
        print("  EMAIL_DESTINATARIOS=dest1@email.com,dest2@email.com")
        exit(1)

    print("Gerando relatório...")
    html = gerar_html_relatorio()
    print("Enviando email...")
    enviar_email(html)
    print("Concluído!")
