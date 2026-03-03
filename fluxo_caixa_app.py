"""
Dashboard Fluxo de Caixa - Newcore
==================================
Lê despesas do Google Sheets e recebíveis do MySQL.
Não altera nenhuma estrutura de banco de dados.

Requisitos:
    pip install streamlit pandas gspread google-auth mysql-connector-python plotly python-dotenv

Uso:
    streamlit run fluxo_caixa_app.py
"""

import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime, timedelta
import mysql.connector
import gspread
from google.oauth2.service_account import Credentials
import os
from dotenv import load_dotenv

# Carrega variáveis de ambiente do .env
load_dotenv()

# ============================================
# CONFIGURAÇÕES (via .env ou Streamlit Secrets)
# ============================================

# Google Sheets
GOOGLE_SHEET_ID = os.getenv('GOOGLE_SHEET_ID') or st.secrets.get('gsheets', {}).get('sheet_id')
CREDENTIALS_PATH = os.getenv('GOOGLE_CREDENTIALS_PATH', 'credentials.json')

# MySQL (somente leitura)
MYSQL_CONFIG = {
    'host': os.getenv('MYSQL_HOST') or st.secrets.get('mysql', {}).get('host'),
    'port': int(os.getenv('MYSQL_PORT', 3306)),
    'user': os.getenv('MYSQL_USER') or st.secrets.get('mysql', {}).get('user'),
    'password': os.getenv('MYSQL_PASSWORD') or st.secrets.get('mysql', {}).get('password'),
    'database': os.getenv('MYSQL_DATABASE', 'newcore')
}

# Paleta de cores consistente
CORES = {
    'recebiveis': '#2ecc71',
    'despesas': '#e74c3c',
    'saldo': '#3498db',
    'orcado': '#3498db',
    'realizado': '#2ecc71',
    'alerta': '#f39c12',
    'neutro': '#95a5a6',
}

MESES_NOME = {1: 'Jan', 2: 'Fev', 3: 'Mar', 4: 'Abr', 5: 'Mai', 6: 'Jun',
              7: 'Jul', 8: 'Ago', 9: 'Set', 10: 'Out', 11: 'Nov', 12: 'Dez'}

PLOTLY_LAYOUT = dict(
    template='plotly_dark',
    paper_bgcolor='rgba(0,0,0,0)',
    plot_bgcolor='rgba(0,0,0,0)',
    font=dict(color='#fafafa'),
    legend=dict(orientation='h', yanchor='bottom', y=1.02),
)

# ============================================
# FUNÇÕES DE LEITURA
# ============================================

def _get_gsheets_client():
    """Retorna cliente gspread autenticado (reutilizável)"""
    scopes = [
        'https://www.googleapis.com/auth/spreadsheets.readonly',
        'https://www.googleapis.com/auth/drive.readonly'
    ]
    # Streamlit Cloud: credentials via st.secrets
    if os.path.exists(CREDENTIALS_PATH):
        creds = Credentials.from_service_account_file(CREDENTIALS_PATH, scopes=scopes)
    else:
        creds = Credentials.from_service_account_info(
            dict(st.secrets['gcp_service_account']), scopes=scopes
        )
    return gspread.authorize(creds)


@st.cache_data(ttl=300)  # Cache de 5 minutos
def load_despesas_from_gsheets():
    """Lê aba DESPESAS do Google Sheets"""
    try:
        client = _get_gsheets_client()

        sheet = client.open_by_key(GOOGLE_SHEET_ID)
        worksheet = sheet.worksheet('DESPESAS')
        
        all_values = worksheet.get_all_values()
        headers = all_values[0]
        # Filtra colunas com header vazio (planilha tem colunas extras)
        valid_cols = [i for i, h in enumerate(headers) if h.strip()]
        filtered_headers = [headers[i] for i in valid_cols]
        rows = [[row[i] if i < len(row) else '' for i in valid_cols] for row in all_values[1:]]
        df = pd.DataFrame(rows, columns=filtered_headers)
        
        # Converte datas (formato DD/MM/YYYY da planilha)
        for col in ['DT_VENC_ORIG', 'DT_PREV_PGTO', 'DT_EFET_PGTO']:
            if col in df.columns:
                df[col] = pd.to_datetime(df[col], dayfirst=True, errors='coerce')

        # Converte valor (formato BR: "R$ 1.234,56")
        if 'VALOR' in df.columns:
            df['VALOR'] = (df['VALOR'].astype(str)
                           .str.replace('R$', '', regex=False)
                           .str.replace('.', '', regex=False)
                           .str.replace(',', '.', regex=False)
                           .str.strip())
            df['VALOR'] = pd.to_numeric(df['VALOR'], errors='coerce')

        # Normaliza STATUS (Lançado e LANÇADO → Lançado)
        if 'STATUS Consolidado' in df.columns:
            df['STATUS Consolidado'] = df['STATUS Consolidado'].str.strip().str.capitalize()

        # Converte ANO/MES para numérico
        for col in ['ANO_ORIGINAL', 'MES_ORIGINAL']:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')

        # Valida colunas obrigatórias
        required = ['DT_PREV_PGTO', 'VALOR', 'STATUS Consolidado',
                     'CATEGORIA CONSOLIDADA', 'FORNECEDOR', 'ANO_ORIGINAL', 'MES_ORIGINAL']
        missing = [c for c in required if c not in df.columns]
        if missing:
            st.error(f"Colunas ausentes na planilha: {', '.join(missing)}")
            return pd.DataFrame()

        return df
    
    except Exception as e:
        st.error(f"Erro ao ler Google Sheets: {e}")
        return pd.DataFrame()


@st.cache_data(ttl=300)
def load_saldo_from_gsheets():
    """Lê saldo em conta da aba Din DESPESAS"""
    try:
        client = _get_gsheets_client()

        sheet = client.open_by_key(GOOGLE_SHEET_ID)
        worksheet = sheet.worksheet('Din DESPESAS')
        
        # Saldo está na célula C10 (linha 10, coluna 3)
        saldo = worksheet.cell(10, 3).value
        saldo_str = str(saldo).replace('R$', '').strip()
        saldo_str = saldo_str.replace('.', '').replace(',', '.')
        saldo = float(saldo_str)
        
        # Data de atualização está em D10
        data_str = worksheet.cell(10, 4).value
        
        return saldo, data_str
    
    except Exception as e:
        st.error(f"Erro ao ler saldo: {e}")
        return 0, "N/A"


@st.cache_data(ttl=300)
def load_recebiveis_from_mysql():
    """Lê recebíveis do MySQL (somente SELECT)"""
    try:
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

    except Exception as e:
        st.error(f"Erro ao ler MySQL: {e}")
        return pd.DataFrame()


@st.cache_data(ttl=300)
def load_orcamento_consolidado_from_gsheets():
    """Lê aba Orçamento Consolidado do Google Sheets"""
    try:
        client = _get_gsheets_client()
        sheet = client.open_by_key(GOOGLE_SHEET_ID)
        ws = sheet.worksheet('Orçamento Consolidado')
        all_values = ws.get_all_values()

        # Linha 3 tem os códigos de mês (0125_JAN, 0225_FEV, etc.)
        headers_mes = all_values[2]

        # Mapeamento de códigos para nomes legíveis
        mapa_meses = {
            '0125_JAN': 'Jan/25', '0225_FEV': 'Fev/25', '0325_MAR': 'Mar/25',
            '0425_ABR': 'Abr/25', '0525_MAI': 'Mai/25', '0625_JUN': 'Jun/25',
            '0725_JUL': 'Jul/25', '0825_AGO': 'Ago/25', '0925_SET': 'Set/25',
            '1024_OUT': 'Out/24', '1025_OUT': 'Out/25',
            '1124_NOV': 'Nov/24', '1125_NOV': 'Nov/25',
            '1224_DEZ': 'Dez/24', '1225_DEZ': 'Dez/25',
        }

        # Montar colunas: Categoria + meses + Total 2025 + Total geral
        colunas = ['Categoria']
        col_indices = [0]  # índice 0 = categoria

        for i, h in enumerate(headers_mes):
            h_clean = h.strip()
            if h_clean in mapa_meses:
                colunas.append(mapa_meses[h_clean])
                col_indices.append(i)

        # Adicionar Total 2025 e Total geral (últimas duas colunas com dados)
        if len(all_values[1]) >= 2:
            # Encontrar posição de "2025 Total" e "Total geral"
            for i, h in enumerate(all_values[1]):
                if '2025 Total' in h:
                    colunas.append('Total 2025')
                    col_indices.append(i)
                elif 'Total geral' in h:
                    colunas.append('Total Geral')
                    col_indices.append(i)

        # Extrair dados (linhas 4 a 24, ou seja, índices 3 a 23)
        rows = []
        for row in all_values[3:]:
            categoria = row[0].strip() if row[0].strip() else None
            if categoria is None:
                # Linha 4 na planilha não tem nome de categoria mas tem dados
                # Verificar se há valores
                has_data = any(row[i].strip() for i in col_indices[1:] if i < len(row))
                if not has_data:
                    continue
                categoria = '(Sem categoria)'

            valores = [categoria]
            for idx in col_indices[1:]:
                val = row[idx].strip() if idx < len(row) else ''
                # Converter formato BR para numérico
                if val:
                    val_clean = val.replace('R$', '').replace('.', '').replace(',', '.').strip()
                    try:
                        valores.append(float(val_clean))
                    except ValueError:
                        valores.append(0)
                else:
                    valores.append(0)
            rows.append(valores)

        df = pd.DataFrame(rows, columns=colunas)
        return df

    except Exception as e:
        st.error(f"Erro ao ler Orçamento Consolidado: {e}")
        return pd.DataFrame()


# ============================================
# FUNÇÕES DE ANÁLISE
# ============================================

def calcular_despesas_periodo(df, dias):
    """Calcula total de despesas para os próximos X dias"""
    hoje = pd.Timestamp.now().normalize()
    fim = hoje + timedelta(days=dias)
    
    mask = (
        (df['DT_PREV_PGTO'] >= hoje) & 
        (df['DT_PREV_PGTO'] <= fim) &
        (df['STATUS Consolidado'].isin(['Previsto', 'Confirmado']))
    )
    
    return df[mask]['VALOR'].sum()


def calcular_recebiveis_periodo(df, dias):
    """Calcula total de recebíveis para os próximos X dias"""
    hoje = pd.Timestamp.now().normalize()
    fim = hoje + timedelta(days=dias)
    
    mask = (
        (df['data_vencimento'] >= hoje) & 
        (df['data_vencimento'] <= fim) &
        (df['data_pagamento'].isna())  # Ainda não pago
    )
    
    return df[mask]['valor'].sum()


def gerar_fluxo_diario(df_despesas, df_recebiveis, dias=30):
    """Gera fluxo de caixa diário consolidado"""
    hoje = pd.Timestamp.now().normalize()
    datas = pd.date_range(hoje, periods=dias, freq='D')
    
    fluxo = []
    
    for data in datas:
        # Despesas do dia
        desp = df_despesas[
            (df_despesas['DT_PREV_PGTO'] == data) &
            (df_despesas['STATUS Consolidado'].isin(['Previsto', 'Confirmado']))
        ]['VALOR'].sum()
        
        # Recebíveis do dia
        receb = df_recebiveis[
            (df_recebiveis['data_vencimento'] == data) &
            (df_recebiveis['data_pagamento'].isna())
        ]['valor'].sum()
        
        fluxo.append({
            'Data': data,
            'Despesas': desp,
            'Recebíveis': receb,
            'Saldo Projetado': receb - desp
        })
    
    return pd.DataFrame(fluxo)


# ============================================
# INTERFACE STREAMLIT
# ============================================

def main():
    st.set_page_config(
        page_title="Fluxo de Caixa - Newcore",
        page_icon="💰",
        layout="wide",
        initial_sidebar_state="expanded"
    )

    # CSS customizado
    st.markdown("""
    <style>
        /* Texto global branco */
        .stApp, .stApp p, .stApp span, .stApp label, .stApp div {
            color: #fafafa;
        }
        /* Cards de métricas */
        div[data-testid="stMetric"] {
            background: linear-gradient(135deg, #1a1f2e 0%, #141824 100%);
            border: 1px solid #2a3040;
            border-radius: 12px;
            padding: 16px 20px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.3);
        }
        div[data-testid="stMetric"] label {
            font-size: 0.85rem !important;
            color: #e74c3c !important;
        }
        div[data-testid="stMetric"] div[data-testid="stMetricValue"] {
            font-size: 1.6rem !important;
            color: #ffffff !important;
        }
        div[data-testid="stMetric"] div[data-testid="stMetricDelta"] {
            color: #cccccc !important;
        }
        /* Subtítulos de seção */
        h3 {
            color: #3498db !important;
            border-bottom: 2px solid #2a3040;
            padding-bottom: 8px;
        }
        h4, h5, h6 {
            color: #fafafa !important;
        }
        /* Sidebar */
        section[data-testid="stSidebar"] {
            background: #0a0e16;
            border-right: 1px solid #1a1f2e;
        }
        section[data-testid="stSidebar"] * {
            color: #fafafa !important;
        }
        section[data-testid="stSidebar"] .stMarkdown p,
        section[data-testid="stSidebar"] .stMarkdown span,
        section[data-testid="stSidebar"] label,
        section[data-testid="stSidebar"] .stCaption,
        section[data-testid="stSidebar"] small {
            color: #fafafa !important;
        }
        /* Expanders */
        div[data-testid="stExpander"] {
            border: 1px solid #2a3040;
            border-radius: 8px;
        }
        div[data-testid="stExpander"] summary,
        div[data-testid="stExpander"] p,
        div[data-testid="stExpander"] span {
            color: #fafafa !important;
        }
        /* Captions e textos auxiliares */
        .stCaption, small, .stMarkdown small {
            color: #b0b8c8 !important;
        }
        /* Markdown bold/strong */
        strong, b {
            color: #ffffff !important;
        }
        /* Separador mais sutil */
        hr {
            border-color: #1a1f2e !important;
            margin: 1.5rem 0 !important;
        }
        /* Info/Warning/Error boxes */
        div[data-testid="stAlert"] p {
            color: inherit !important;
        }
    </style>
    """, unsafe_allow_html=True)

    # ==========================================
    # SIDEBAR
    # ==========================================

    with st.sidebar:
        st.markdown("### NEWCORE")
        st.caption("Fluxo de Caixa")
        st.markdown("---")

        if st.button("Atualizar dados", use_container_width=True):
            st.cache_data.clear()
            st.rerun()

    # Carrega dados
    with st.spinner("Carregando dados..."):
        df_despesas = load_despesas_from_gsheets()
        saldo_conta, data_saldo = load_saldo_from_gsheets()
        df_recebiveis = load_recebiveis_from_mysql()

    hoje = pd.Timestamp.now().normalize()

    if df_despesas.empty:
        st.warning("Não foi possível carregar as despesas. Verifique as configurações.")
        return

    # Sidebar: filtros e info
    with st.sidebar:
        st.markdown("---")
        st.markdown("**Filtros**")

        data_selecionada = st.date_input(
            "Dia (detalhamento)",
            value=hoje.date(),
            min_value=hoje.date(),
            max_value=(hoje + timedelta(days=29)).date()
        )

        anos_disponiveis = sorted(df_despesas['ANO_ORIGINAL'].dropna().unique(), reverse=True)
        ano_selecionado = st.selectbox("Ano", anos_disponiveis, index=0) if anos_disponiveis else datetime.now().year

        categorias_disponiveis = sorted(df_despesas['CATEGORIA CONSOLIDADA'].dropna().unique())
        categorias_selecionadas = st.multiselect("Categorias", categorias_disponiveis, default=[])

        st.markdown("---")
        st.metric("Saldo em Conta", f"R$ {saldo_conta:,.2f}")
        st.caption(f"Atualizado: {data_saldo}")

    data_sel = pd.Timestamp(data_selecionada).normalize()

    # ==========================================
    # KPIs PRINCIPAIS
    # ==========================================

    st.markdown(f"#### Fluxo de Caixa — {hoje.strftime('%d/%m/%Y')}")

    col1, col2, col3, col4 = st.columns(4)

    despesas_hoje = calcular_despesas_periodo(df_despesas, 0)
    recebiveis_hoje = calcular_recebiveis_periodo(df_recebiveis, 0)
    saldo_projetado = saldo_conta - despesas_hoje + recebiveis_hoje

    with col1:
        st.metric(
            label="Saldo em Conta",
            value=f"R$ {saldo_conta:,.2f}",
            help=f"Atualizado em: {data_saldo}"
        )

    with col2:
        st.metric(
            label="Despesas Hoje",
            value=f"R$ {despesas_hoje:,.2f}",
            delta=f"-R$ {despesas_hoje:,.2f}" if despesas_hoje > 0 else None,
            delta_color="inverse"
        )

    with col3:
        st.metric(
            label="Recebíveis Hoje",
            value=f"R$ {recebiveis_hoje:,.2f}",
            delta=f"+R$ {recebiveis_hoje:,.2f}" if recebiveis_hoje > 0 else None,
            delta_color="normal"
        )

    with col4:
        delta_val = recebiveis_hoje - despesas_hoje
        st.metric(
            label="Saldo Projetado",
            value=f"R$ {saldo_projetado:,.2f}",
            delta=f"R$ {delta_val:,.2f}",
            delta_color="normal" if delta_val >= 0 else "inverse"
        )

    # ==========================================
    # ALERTAS DE VENCIMENTO
    # ==========================================

    df_desp_vencidas = df_despesas[
        (df_despesas['DT_PREV_PGTO'] < hoje) &
        (df_despesas['STATUS Consolidado'].isin(['Previsto', 'Confirmado']))
    ].copy()

    df_rec_vencidos = df_recebiveis[
        (df_recebiveis['data_vencimento'] < hoje) &
        (df_recebiveis['data_pagamento'].isna())
    ].copy()

    total_alertas = len(df_desp_vencidas) + len(df_rec_vencidos)

    if total_alertas > 0:
        valor_desp_vencidas = df_desp_vencidas['VALOR'].sum()
        valor_rec_vencidos = df_rec_vencidos['valor'].sum()

        msg = f"**{total_alertas} itens vencidos** — "
        partes = []
        if len(df_desp_vencidas) > 0:
            partes.append(f"{len(df_desp_vencidas)} despesas (R$ {valor_desp_vencidas:,.2f})")
        if len(df_rec_vencidos) > 0:
            partes.append(f"{len(df_rec_vencidos)} recebíveis (R$ {valor_rec_vencidos:,.2f})")
        msg += " | ".join(partes)

        if total_alertas >= 3:
            st.error(msg)
        else:
            st.warning(msg)

        with st.expander("Ver itens vencidos"):
            col_v1, col_v2 = st.columns(2)

            with col_v1:
                st.markdown("**Despesas vencidas**")
                if not df_desp_vencidas.empty:
                    df_desp_vencidas['Dias em atraso'] = (hoje - df_desp_vencidas['DT_PREV_PGTO']).dt.days
                    df_show = df_desp_vencidas[['DT_PREV_PGTO', 'FORNECEDOR', 'CATEGORIA CONSOLIDADA', 'VALOR', 'Dias em atraso']].sort_values('Dias em atraso', ascending=False)
                    df_show.columns = ['Data', 'Fornecedor', 'Categoria', 'Valor', 'Dias atraso']
                    st.dataframe(
                        df_show,
                        column_config={
                            'Data': st.column_config.DateColumn('Data', format='DD/MM/YYYY'),
                            'Valor': st.column_config.NumberColumn('Valor', format='R$ %.2f'),
                        },
                        use_container_width=True,
                        hide_index=True
                    )
                else:
                    st.info("Nenhuma despesa vencida.")

            with col_v2:
                st.markdown("**Recebíveis vencidos**")
                if not df_rec_vencidos.empty:
                    df_rec_vencidos['Dias em atraso'] = (hoje - df_rec_vencidos['data_vencimento']).dt.days
                    df_show_r = df_rec_vencidos[['data_vencimento', 'oferta_id', 'valor', 'status', 'Dias em atraso']].sort_values('Dias em atraso', ascending=False)
                    df_show_r.columns = ['Data', 'Oferta', 'Valor', 'Status', 'Dias atraso']
                    st.dataframe(
                        df_show_r,
                        column_config={
                            'Data': st.column_config.DateColumn('Data', format='DD/MM/YYYY'),
                            'Valor': st.column_config.NumberColumn('Valor', format='R$ %.2f'),
                        },
                        use_container_width=True,
                        hide_index=True
                    )
                else:
                    st.info("Nenhum recebível vencido.")

    # ==========================================
    # PROJEÇÃO POR PERÍODO
    # ==========================================

    st.markdown("---")
    st.subheader("Projeção por Período")

    col1, col2, col3 = st.columns(3)

    for col, dias in zip([col1, col2, col3], [7, 15, 30]):
        with col:
            desp = calcular_despesas_periodo(df_despesas, dias)
            receb = calcular_recebiveis_periodo(df_recebiveis, dias)
            saldo = receb - desp

            st.metric(
                label=f"Próximos {dias} dias",
                value=f"R$ {saldo:,.2f}",
                delta=f"Desp: R$ {desp:,.2f} | Rec: R$ {receb:,.2f}",
                delta_color="off"
            )

    # ==========================================
    # GRÁFICO DE FLUXO DIÁRIO
    # ==========================================

    st.markdown("---")
    st.subheader("Fluxo de Caixa Diário (30 dias)")

    df_fluxo = gerar_fluxo_diario(df_despesas, df_recebiveis, 30)
    df_fluxo['Saldo Projetado Acumulado'] = saldo_conta + df_fluxo['Saldo Projetado'].cumsum()

    hover_brl = "R$ %{y:,.2f}<extra></extra>"

    fig = go.Figure()

    fig.add_trace(go.Bar(
        x=df_fluxo['Data'],
        y=df_fluxo['Recebíveis'],
        name='Recebíveis',
        marker_color=CORES['recebiveis'],
        hovertemplate="Recebíveis: R$ %{y:,.2f}<extra></extra>"
    ))

    fig.add_trace(go.Bar(
        x=df_fluxo['Data'],
        y=df_fluxo['Despesas'],
        name='Despesas',
        marker_color=CORES['despesas'],
        hovertemplate="Despesas: R$ %{y:,.2f}<extra></extra>"
    ))

    fig.add_trace(go.Scatter(
        x=df_fluxo['Data'],
        y=df_fluxo['Saldo Projetado Acumulado'],
        name='Saldo Projetado Acumulado',
        line=dict(color=CORES['saldo'], width=3),
        yaxis='y2',
        hovertemplate="Saldo Projetado: R$ %{y:,.2f}<extra></extra>"
    ))

    fig.update_layout(
        **PLOTLY_LAYOUT,
        barmode='group',
        xaxis=dict(tickformat='%d/%m', dtick='D1', gridcolor='#1a1f2e'),
        yaxis=dict(title='Movimentação (R$)', gridcolor='#1a1f2e'),
        yaxis2=dict(title='Saldo Projetado Acumulado (R$)', overlaying='y', side='right', gridcolor='#1a1f2e'),
        height=420,
    )

    st.plotly_chart(fig, use_container_width=True)

    # ==========================================
    # DETALHAMENTO DIÁRIO
    # ==========================================

    st.markdown("---")
    st.subheader(f"Detalhamento — {data_sel.strftime('%d/%m/%Y')}")

    # Tabela consolidada
    df_resumo = df_fluxo[['Data', 'Despesas', 'Recebíveis', 'Saldo Projetado']].copy()

    st.dataframe(
        df_resumo,
        column_config={
            'Data': st.column_config.DateColumn('Data', format='DD/MM/YYYY'),
            'Despesas': st.column_config.NumberColumn('Despesas', format='R$ %.2f'),
            'Recebíveis': st.column_config.NumberColumn('Recebíveis', format='R$ %.2f'),
            'Saldo Projetado': st.column_config.NumberColumn('Saldo Projetado', format='R$ %.2f'),
        },
        use_container_width=True,
        hide_index=True,
        height=350
    )

    # KPIs do dia selecionado
    desp_dia_total = df_despesas[
        (df_despesas['DT_PREV_PGTO'] == data_sel) &
        (df_despesas['STATUS Consolidado'].isin(['Previsto', 'Confirmado']))
    ]['VALOR'].sum()

    receb_dia_total = df_recebiveis[
        (df_recebiveis['data_vencimento'] == data_sel) &
        (df_recebiveis['data_pagamento'].isna())
    ]['valor'].sum()

    col_res1, col_res2, col_res3 = st.columns(3)
    with col_res1:
        st.metric("Despesas", f"R$ {desp_dia_total:,.2f}")
    with col_res2:
        st.metric("Recebíveis", f"R$ {receb_dia_total:,.2f}")
    with col_res3:
        saldo_dia_sel = receb_dia_total - desp_dia_total
        st.metric("Saldo Projetado", f"R$ {saldo_dia_sel:,.2f}")

    # Tabelas de detalhe
    col_desp, col_rec = st.columns(2)

    with col_desp:
        st.markdown("**Despesas do dia**")
        df_desp_dia = df_despesas[
            (df_despesas['DT_PREV_PGTO'] == data_sel) &
            (df_despesas['STATUS Consolidado'].isin(['Previsto', 'Confirmado']))
        ][['FORNECEDOR', 'CATEGORIA CONSOLIDADA', 'VALOR', 'STATUS Consolidado']].sort_values('VALOR', ascending=False)

        if not df_desp_dia.empty:
            df_desp_dia.columns = ['Fornecedor', 'Categoria', 'Valor', 'Status']
            st.dataframe(
                df_desp_dia,
                column_config={
                    'Valor': st.column_config.NumberColumn('Valor', format='R$ %.2f'),
                },
                use_container_width=True,
                hide_index=True
            )
        else:
            st.info("Nenhuma despesa prevista para esta data.")

    with col_rec:
        st.markdown("**Recebíveis do dia**")
        df_rec_dia = df_recebiveis[
            (df_recebiveis['data_vencimento'] == data_sel) &
            (df_recebiveis['data_pagamento'].isna())
        ][['oferta_id', 'valor', 'status']].sort_values('valor', ascending=False)

        if not df_rec_dia.empty:
            df_rec_dia.columns = ['Oferta', 'Valor', 'Status']
            st.dataframe(
                df_rec_dia,
                column_config={
                    'Valor': st.column_config.NumberColumn('Valor', format='R$ %.2f'),
                },
                use_container_width=True,
                hide_index=True
            )
        else:
            st.info("Nenhum recebível pendente para esta data.")

    # ==========================================
    # DESPESAS POR CATEGORIA + PROXIMOS VENCIMENTOS
    # ==========================================

    st.markdown("---")

    col1, col2 = st.columns(2)

    with col1:
        st.subheader("Despesas por Categoria")

        fim_mes = hoje + timedelta(days=30)

        df_cat = df_despesas[
            (df_despesas['DT_PREV_PGTO'] >= hoje) &
            (df_despesas['DT_PREV_PGTO'] <= fim_mes) &
            (df_despesas['STATUS Consolidado'].isin(['Previsto', 'Confirmado']))
        ].groupby('CATEGORIA CONSOLIDADA')['VALOR'].sum().sort_values(ascending=False)

        if not df_cat.empty:
            fig_cat = px.pie(
                values=df_cat.values,
                names=df_cat.index,
                hole=0.4
            )
            fig_cat.update_traces(textinfo='percent+label')
            fig_cat.update_layout(**PLOTLY_LAYOUT, height=380, showlegend=False)
            st.plotly_chart(fig_cat, use_container_width=True)
        else:
            st.info("Nenhuma despesa prevista para os próximos 30 dias.")

    with col2:
        st.subheader("Próximos Vencimentos")

        df_proximos = df_despesas[
            (df_despesas['DT_PREV_PGTO'] >= hoje) &
            (df_despesas['DT_PREV_PGTO'] <= hoje + timedelta(days=7)) &
            (df_despesas['STATUS Consolidado'].isin(['Previsto', 'Confirmado']))
        ][['DT_PREV_PGTO', 'FORNECEDOR', 'CATEGORIA CONSOLIDADA', 'VALOR']].sort_values('DT_PREV_PGTO')

        if not df_proximos.empty:
            df_proximos.columns = ['Data', 'Fornecedor', 'Categoria', 'Valor']
            st.dataframe(
                df_proximos,
                column_config={
                    'Data': st.column_config.DateColumn('Data', format='DD/MM'),
                    'Valor': st.column_config.NumberColumn('Valor', format='R$ %.2f'),
                },
                use_container_width=True,
                hide_index=True
            )
        else:
            st.info("Nenhum vencimento nos próximos 7 dias.")

    # ==========================================
    # ORÇADO VS REALIZADO
    # ==========================================

    st.markdown("---")
    st.subheader(f"Orçado vs Realizado ({ano_selecionado})")

    df_ano = df_despesas[df_despesas['ANO_ORIGINAL'] == ano_selecionado].copy()
    if categorias_selecionadas:
        df_ano = df_ano[df_ano['CATEGORIA CONSOLIDADA'].isin(categorias_selecionadas)]

    df_orcado = df_ano[
        df_ano['STATUS Consolidado'].isin(['Previsto', 'Confirmado'])
    ].groupby('MES_ORIGINAL')['VALOR'].sum().reset_index()
    df_orcado.columns = ['Mês', 'Orçado']

    df_realizado = df_ano[
        df_ano['STATUS Consolidado'] == 'Lançado'
    ].groupby('MES_ORIGINAL')['VALOR'].sum().reset_index()
    df_realizado.columns = ['Mês', 'Realizado']

    df_comparativo = pd.DataFrame({'Mês': range(1, 13)})
    df_comparativo = df_comparativo.merge(df_orcado, on='Mês', how='left').merge(df_realizado, on='Mês', how='left')
    df_comparativo = df_comparativo.fillna(0)
    df_comparativo['Mês Nome'] = df_comparativo['Mês'].map(MESES_NOME)
    df_comparativo['Diferença'] = df_comparativo['Realizado'] - df_comparativo['Orçado']

    fig_comp = go.Figure()
    fig_comp.add_trace(go.Bar(
        x=df_comparativo['Mês Nome'],
        y=df_comparativo['Orçado'],
        name='Orçado (Previsto)',
        marker_color=CORES['orcado'],
        text=df_comparativo['Orçado'].apply(lambda x: f"R$ {x:,.0f}" if x > 0 else ""),
        textposition='outside',
        hovertemplate="Orçado: R$ %{y:,.2f}<extra></extra>"
    ))
    fig_comp.add_trace(go.Bar(
        x=df_comparativo['Mês Nome'],
        y=df_comparativo['Realizado'],
        name='Realizado (Lançado)',
        marker_color=CORES['realizado'],
        text=df_comparativo['Realizado'].apply(lambda x: f"R$ {x:,.0f}" if x > 0 else ""),
        textposition='outside',
        hovertemplate="Realizado: R$ %{y:,.2f}<extra></extra>"
    ))
    fig_comp.update_layout(**PLOTLY_LAYOUT, barmode='group', height=380)
    st.plotly_chart(fig_comp, use_container_width=True)

    # Tabela resumo
    df_tabela = df_comparativo[df_comparativo[['Orçado', 'Realizado']].sum(axis=1) > 0].copy()
    if not df_tabela.empty:
        df_tabela['% Desvio'] = df_tabela.apply(
            lambda r: (r['Diferença'] / r['Orçado'] * 100) if r['Orçado'] > 0 else 0, axis=1
        )
        st.dataframe(
            df_tabela[['Mês Nome', 'Orçado', 'Realizado', 'Diferença', '% Desvio']],
            column_config={
                'Mês Nome': st.column_config.TextColumn('Mês'),
                'Orçado': st.column_config.NumberColumn('Orçado', format='R$ %.2f'),
                'Realizado': st.column_config.NumberColumn('Realizado', format='R$ %.2f'),
                'Diferença': st.column_config.NumberColumn('Diferença', format='R$ %.2f'),
                '% Desvio': st.column_config.NumberColumn('% Desvio', format='%.1f%%'),
            },
            use_container_width=True,
            hide_index=True
        )

    # ==========================================
    # ORÇAMENTO CONSOLIDADO
    # ==========================================

    st.markdown("---")
    st.subheader("Orçamento Consolidado")

    df_orcamento = load_orcamento_consolidado_from_gsheets()

    if not df_orcamento.empty:
        # Configurar colunas de valores com formato monetário
        col_config_orc = {
            'Categoria': st.column_config.TextColumn('Categoria', width='medium'),
        }
        for col in df_orcamento.columns:
            if col != 'Categoria':
                col_config_orc[col] = st.column_config.NumberColumn(
                    col, format='R$ %.2f'
                )

        st.dataframe(
            df_orcamento,
            column_config=col_config_orc,
            use_container_width=True,
            hide_index=True,
            height=700
        )
    else:
        st.info("Não foi possível carregar o Orçamento Consolidado.")

    # ==========================================
    # RODAPÉ
    # ==========================================

    st.markdown("---")
    st.caption(f"Dashboard Fluxo de Caixa — Newcore | {datetime.now().strftime('%d/%m/%Y %H:%M')}")


if __name__ == "__main__":
    main()
