import eventlet
eventlet.monkey_patch()

from flask import Flask, render_template, request, jsonify, redirect, url_for, flash, send_from_directory, session, send_file, make_response
from flask_cors import CORS
from flask_socketio import SocketIO, Namespace, emit
import sqlite3
import os
from datetime import datetime
from werkzeug.utils import secure_filename
import logging
import json
from threading import Lock
import socket
# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})  # Allow CORS for all routes
app.config['SECRET_KEY'] = 'sua_chave_secreta_aqui'
app.config['UPLOAD_FOLDER'] = 'fotos_cadastro'
app.config['FOTOS_INSUMOS_FOLDER'] = 'fotos_insumos'  # Nova pasta para fotos de insumos
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB limit
app.config['JSON_AS_ASCII'] = False  # garante acentuação correta no JSON
app.config['JSONIFY_MIMETYPE'] = 'application/json; charset=utf-8'
DATABASE = 'gestao.db'

# Initialize SocketIO with eventlet
socketio = SocketIO(app, async_mode='eventlet', cors_allowed_origins="*", logger=True, engineio_logger=True)

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
db_lock = Lock()  # Thread lock for database operations

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def get_db_connection():
    try:
        conn = sqlite3.connect(DATABASE)
        conn.row_factory = sqlite3.Row
        conn.text_factory = str  # garante unicode
        return conn
    except sqlite3.Error as e:
        logger.error(f"Error connecting to database: {str(e)}")
        raise

def init_db():
    with db_lock:
        with app.app_context():  # Ensure application context
            conn = get_db_connection()
            cursor = conn.cursor()

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS itens_cadastro (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tipo_item TEXT NOT NULL,
                    codigo_fabricacao TEXT,
                    codigo_interno TEXT NOT NULL UNIQUE,
                    nome_descricao TEXT NOT NULL,
                    foto TEXT,
                    categoria TEXT,
                    material TEXT,
                    maquina TEXT,
                    altura_min REAL,
                    altura_max REAL,
                    rpm INTEGER,
                    avanco REAL,
                    data_cadastro TEXT NOT NULL
                )
            ''')

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS composicao_ferramentas (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ferramenta_id INTEGER,
                    insumo_id INTEGER,
                    quantidade INTEGER DEFAULT 1,
                    FOREIGN KEY (ferramenta_id) REFERENCES itens_cadastro (id),
                    FOREIGN KEY (insumo_id) REFERENCES itens_cadastro (id)
                )
            ''')

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS itens_cadastro_deleted (
                    id INTEGER PRIMARY KEY,
                    tipo_item TEXT NOT NULL,
                    codigo_fabricacao TEXT,
                    codigo_interno TEXT NOT NULL,
                    nome_descricao TEXT NOT NULL,
                    foto TEXT,
                    categoria TEXT,
                    material TEXT,
                    maquina TEXT,
                    altura_min REAL,
                    altura_max REAL,
                    rpm INTEGER,
                    avanco REAL,
                    data_cadastro TEXT NOT NULL,
                    deleted_at TEXT NOT NULL
                )
            ''')

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS composicao_ferramentas_deleted (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ferramenta_id INTEGER,
                    insumo_id INTEGER,
                    quantidade INTEGER DEFAULT 1,
                    deleted_at TEXT NOT NULL,
                    FOREIGN KEY (ferramenta_id) REFERENCES itens_cadastro (id),
                    FOREIGN KEY (insumo_id) REFERENCES itens_cadastro (id)
                )
            ''')

            cursor.execute('''
                CREATE TABLE IF NOT EXISTS ocorrencias (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    titulo TEXT NOT NULL,
                    descricao TEXT,
                    tipo TEXT,
                    prioridade TEXT,
                    data TEXT,
                    status TEXT
                )
            ''')

            # Localização por Células (opcional, mÃºltiplas por item)
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS itens_celulas (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    item_id INTEGER NOT NULL,
                    celula TEXT NOT NULL,
                    FOREIGN KEY (item_id) REFERENCES itens_cadastro (id)
                )
            ''')

            # Máquinas por item (múltiplas)
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS itens_maquinas (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    item_id INTEGER NOT NULL,
                    maquina TEXT NOT NULL,
                    FOREIGN KEY (item_id) REFERENCES itens_cadastro (id)
                )
            ''')

            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = [t[0] for t in cursor.fetchall()]
            
            if 'insumos' not in tables:
                cursor.execute('''
                    CREATE TABLE insumos (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        item_id INTEGER,
                        nome TEXT NOT NULL,
                        operador TEXT,
                        maquina TEXT,
                        quantidade INTEGER,
                        urgencia TEXT,
                        justificativa TEXT,
                        data TEXT,
                        status TEXT,
                        codigo_interno TEXT,
                        fotos TEXT,
                        sem_fotos INTEGER DEFAULT 0,
                        data_atendimento TEXT,
                        atendida_por TEXT,
                        FOREIGN KEY (item_id) REFERENCES itens_cadastro (id)
                    )
                ''')
            else:
                cursor.execute("PRAGMA table_info(insumos)")
                columns = [col['name'] for col in cursor.fetchall()]
                
                # Verificar e adicionar colunas necessárias
                if 'item_id' not in columns:
                    cursor.execute('ALTER TABLE insumos ADD COLUMN item_id INTEGER')
                if 'codigo_interno' not in columns:
                    cursor.execute('ALTER TABLE insumos ADD COLUMN codigo_interno TEXT')
                if 'fotos' not in columns:
                    cursor.execute('ALTER TABLE insumos ADD COLUMN fotos TEXT')
                if 'sem_fotos' not in columns:
                    cursor.execute('ALTER TABLE insumos ADD COLUMN sem_fotos INTEGER DEFAULT 0')
                if 'data_atendimento' not in columns:
                    cursor.execute('ALTER TABLE insumos ADD COLUMN data_atendimento TEXT')
                if 'atendida_por' not in columns:
                    cursor.execute('ALTER TABLE insumos ADD COLUMN atendida_por TEXT')
                    
            conn.commit()
            # Admin auth table (for gestão)
            cursor = conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS admin (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL,
                    password TEXT NOT NULL
                )
            ''')
            cursor.execute('SELECT COUNT(*) FROM admin')
            if cursor.fetchone()[0] == 0:
                cursor.execute('INSERT INTO admin (username, password) VALUES (?, ?)', (
                    'ADMINISTRADOR', 'tooltag12345'
                ))
            conn.commit()
            # Migrate itens_cadastro and itens_cadastro_deleted for new columns if needed
            cursor = conn.cursor()
            cursor.execute("PRAGMA table_info(itens_cadastro)")
            ic_cols = [col['name'] for col in cursor.fetchall()]
            if 'maquina' not in ic_cols:
                cursor.execute('ALTER TABLE itens_cadastro ADD COLUMN maquina TEXT')

            cursor.execute("PRAGMA table_info(itens_cadastro_deleted)")
            icd_cols = [col['name'] for col in cursor.fetchall()]
            if 'maquina' not in icd_cols:
                cursor.execute('ALTER TABLE itens_cadastro_deleted ADD COLUMN maquina TEXT')
            conn.commit()
            conn.close()

# Define the /gestao namespace for SocketIO
class GestaoNamespace(Namespace):
    def on_connect(self):
        logger.info("Client connected to /gestao namespace")

    def on_disconnect(self):
        logger.info("Client disconnected from /gestao namespace")

    def on_new_solicitation(self, data):
        logger.info(f"Received new_solicitation: {data}")
        emit('new_solicitation', data, broadcast=True, namespace='/gestao')

# Register the namespace
socketio.on_namespace(GestaoNamespace('/gestao'))

@app.route('/')
def index():
    logger.info("Rendering index.html")
    return render_template('index.html')

@app.route('/busca')
def busca():
    logger.info("Rendering busca.html")
    return render_template('busca.html')

@app.route('/qrcode')
def qrcode():
    logger.info("Rendering qrcode.html")
    return render_template('qrcode.html')

@app.route('/solicitar_insumo', methods=['GET', 'POST'])
def solicitar_insumo():
    if request.method == 'POST':
        logger.info(f"Received POST request to /solicitar_insumo: {request.form}")
        with db_lock:
            with app.app_context():  # Ensure application context
                conn = None
                try:
                    conn = get_db_connection()
                    cursor = conn.cursor()
                    item_id = request.form.get('item_id')
                    nome = request.form.get('nome')
                    operador = request.form.get('operador')
                    maquina = request.form.get('maquina')
                    quantidade = request.form.get('quantidade')
                    # Aceitar tanto 'urgencia' quanto variantes com acento oriundas do front
                    urgencia = request.form.get('urgencia')
                    if not urgencia:
                        for k in request.form.keys():
                            try:
                                if 'urg' in k.lower():
                                    urgencia = request.form.get(k)
                                    if urgencia:
                                        break
                            except Exception:
                                continue
                    justificativa = request.form.get('justificativa')

                    if not all([item_id, nome, operador, maquina, quantidade, urgencia, justificativa]):
                        logger.error("Missing required fields in solicitation")
                        flash('Todos os campos obrigatórios devem ser preenchidos.', 'error')
                        return redirect(url_for('solicitar_insumo'))

                    quantidade = int(quantidade)
                    if quantidade <= 0:
                        logger.error("Invalid quantity: must be greater than zero")
                        flash('Quantidade deve ser maior que zero.', 'error')
                        return redirect(url_for('solicitar_insumo'))

                    cursor.execute('SELECT id, tipo_item FROM itens_cadastro WHERE id = ?', (item_id,))
                    item = cursor.fetchone()
                    if not item or item['tipo_item'] not in ['insumo', 'ferramenta']:
                        logger.error(f"Invalid or non-existent item_id: {item_id}")
                        flash('Item selecionado inválido ou não cadastrado.', 'error')
                        return redirect(url_for('solicitar_insumo'))

                    data_criacao = datetime.now().strftime('%d/%m/%Y %H:%M')
                    cursor.execute('''
                        INSERT INTO insumos (item_id, nome, operador, maquina, quantidade, urgencia, justificativa, data, status)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        item_id,
                        nome,
                        operador,
                        maquina,
                        quantidade,
                        urgencia,
                        justificativa,
                        data_criacao,
                        'Pendente'
                    ))
                    insumo_id = cursor.lastrowid
                    conn.commit()

                    # Fetch the inserted insumo for emitting
                    cursor.execute('SELECT i.*, ic.nome_descricao AS nome_item, ic.tipo_item AS tipo FROM insumos i LEFT JOIN itens_cadastro ic ON i.item_id = ic.id WHERE i.id = ?', (insumo_id,))
                    new_insumo = dict(cursor.fetchone())
                    conn.close()

                    # Emit WebSocket event to all connected clients
                    logger.info(f"Emitting new_solicitation with id: {insumo_id}, data: {new_insumo}")
                    socketio.emit('new_solicitation', new_insumo, namespace='/gestao')

                    flash('Solicitação enviada com sucesso!', 'success')
                    return jsonify({'id': insumo_id})  # Return JSON for client-side handling
                except sqlite3.Error as e:
                    if conn is not None:
                        conn.rollback()
                        conn.close()
                    logger.error(f"Database error in /solicitar_insumo: {str(e)}")
                    flash(f'Erro ao enviar solicitação: {str(e)}', 'error')
                    return jsonify({'error': str(e)}), 500
                except ValueError as e:
                    if conn is not None:
                        conn.close()
                    logger.error(f"Validation error in /solicitar_insumo: {str(e)}")
                    flash(f'Erro de validação: {str(e)}', 'error')
                    return jsonify({'error': str(e)}), 400
    logger.info("Rendering solicitar_insumo.html")
    return render_template('solicitar_insumo.html')

@app.route('/ocorrencias', methods=['GET', 'POST'])
def ocorrencias_page():
    # Protege a página com o mesmo login da gestão
    if not session.get('gestao_logged'):
        return redirect(url_for('login', next=request.path))
    if request.method == 'POST':
        with db_lock:
            with app.app_context():  # Ensure application context
                conn = None
                try:
                    conn = get_db_connection()
                    cursor = conn.cursor()
                    cursor.execute('''
                        INSERT INTO ocorrencias (titulo, descricao, tipo, prioridade, data, status)
                        VALUES (?, ?, ?, ?, ?, ?)
                    ''', (
                        request.form.get('titulo'),
                        request.form.get('descricao'),
                        request.form.get('tipo'),
                        request.form.get('prioridade'),
                        datetime.now().strftime('%d/%m/%Y %H:%M'),
                        'Aberta'
                    ))
                    conn.commit()
                    conn.close()
                    logger.info("Ocorrência registrada com sucesso")
                    flash('Ocorrência registrada com sucesso!', 'success')
                    return redirect(url_for('ocorrencias_page'))
                except sqlite3.Error as e:
                    if conn is not None:
                        conn.rollback()
                        conn.close()
                    logger.error(f"Erro ao registrar ocorrência: {str(e)}")
                    flash(f'Erro ao registrar ocorrência: {str(e)}', 'error')
                    return redirect(url_for('ocorrencias_page'))
    with app.app_context():  # Ensure application context
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM ocorrencias ORDER BY id DESC')
        ocorrencias = [dict(row) for row in cursor.fetchall()]
        conn.close()
        logger.info(f"Rendering ocorrencias.html com {len(ocorrencias)} ocorrências")
        return render_template('ocorrencias.html', ocorrencias=ocorrencias)

def build_relatorio_pdf(filtered, q_titulo, q_prioridade, q_atendida):
    """Gera PDF com Platypus (layout moderno):
    - Cabeçalho com barra e filtros, sem poluição
    - Tabela com textos formatados e espaçamentos coerentes
    - Fotos em linha abaixo de cada solicitação
    - Página de gráfico sem cabeçalho de colunas
    """
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import (BaseDocTemplate, PageTemplate, Frame,
                                    Table, TableStyle, Paragraph, Spacer, Image,
                                    PageBreak, NextPageTemplate)
    from reportlab.lib import colors
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    import io, os

    width, height = A4
    buf = io.BytesIO()

    # Document with two page templates: Table and Chart
    doc = BaseDocTemplate(
        buf, pagesize=A4,
        leftMargin=36, rightMargin=36, topMargin=130, bottomMargin=36
    )
    frame = Frame(doc.leftMargin, doc.bottomMargin,
                  doc.width, doc.height, id='normal')

    info = f"Filtros: titulo: '{q_titulo or 'Todos'}' | Prioridade: '{q_prioridade or 'Todas'}' | Atendida por: '{q_atendida or 'Todos'}'"
    occ_count = sum(1 for it in filtered if (it.get('source') or '').lower() == 'ocorrencia')

    def header_common(c):
        c.setFillColorRGB(0.16, 0.22, 0.31)
        c.rect(0, height-60, width, 60, fill=1, stroke=0)
        c.setFillColor(colors.white)
        c.setFont('Helvetica-Bold', 16)
        c.drawString(40, height - 40, 'Relatorio de Ocorrencias e Atendidos')
        # Filtros resumidos
        c.setFillColor(colors.black)
        c.setFont('Helvetica', 10)
        c.drawString(40, height - 75, info)
        c.drawString(40, height - 90, f"Total atendidos: {len(filtered)}")

    def header_table(c, _doc):
        header_common(c)
        # Cabeçalho das colunas com cantos levemente arredondados
        y = height - 110
        c.setFillColorRGB(0.91, 0.95, 0.99)
        try:
            c.roundRect(36, y-4, width-72, 20, 6, fill=1, stroke=0)
        except Exception:
            c.rect(36, y-4, width-72, 20, fill=1, stroke=0)
        c.setFillColor(colors.black)
        c.setFont('Helvetica-Bold', 9)
        avail = _doc.width
        col1, col3, col4, col5 = 60, 80, 80, 80
        col2 = max(120, avail - (col1 + col3 + col4 + col5))
        xs = [40, 40 + col1, 40 + col1 + col2, 40 + col1 + col2 + col3, 40 + col1 + col2 + col3 + col4]
        headers = ['Tipo', 'Solicitante/Titulo', 'Prioridade', 'Data', 'Atendida por']
        for i, h in enumerate(headers):
            c.drawString(xs[i], y, h)

    def header_chart(c, _doc):
        # Apenas barra e filtros; sem cabeçalho de colunas
        header_common(c)

    doc.addPageTemplates([
        PageTemplate(id='Table', frames=[frame], onPage=header_table),
        PageTemplate(id='Chart', frames=[frame], onPage=header_chart),
    ])

    styles = getSampleStyleSheet()
    small = ParagraphStyle('small', parent=styles['Normal'], fontName='Helvetica', fontSize=9, leading=12)
    meta = ParagraphStyle('meta', parent=styles['Normal'], fontName='Helvetica', fontSize=9, leading=12, textColor=colors.gray)
    meta_bold = ParagraphStyle('meta_bold', parent=meta, fontName='Helvetica-Bold')

    story = []
    # Tabela (linhas) – usa PageTemplate 'Table'
    story.append(NextPageTemplate('Table'))

    avail = doc.width
    col1, col3, col4, col5 = 60, 80, 80, 80
    col2 = max(120, avail - (col1 + col3 + col4 + col5))

    for idx, it in enumerate(filtered):
        fonte = (it.get('source') or '').title()
        raw_title = it.get('titulo') or ''
        primary = (raw_title.split(' - ')[0] or raw_title)
        prioridade = (it.get('prioridade') or '').title()
        data_str = it.get('data_atendimento') or it.get('data_original') or ''
        atendida = it.get('atendida_por') or '-'

        row = [[Paragraph(fonte, small), Paragraph(primary, small), Paragraph(prioridade, small), Paragraph(data_str, small), Paragraph(atendida, small)]]
        t = Table(row, colWidths=[col1, col2, col3, col4, col5])
        bg = colors.whitesmoke if (idx % 2 == 0) else colors.white
        t.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), bg),
            ('VALIGN', (0, 0), (-1, 0), 'TOP'),
            ('FONTNAME', (0, 0), (-1, 0), 'Helvetica'),
            ('FONTSIZE', (0, 0), (-1, 0), 9),
            ('LEFTPADDING', (0, 0), (-1, 0), 6),
            ('RIGHTPADDING', (0, 0), (-1, 0), 6),
            ('TOPPADDING', (0, 0), (-1, 0), 6),
            ('BOTTOMPADDING', (0, 0), (-1, 0), 6),
            ('BOX', (0, 0), (-1, -1), 0.3, colors.HexColor('#e6e9ef')),
            ('INNERGRID', (0, 0), (-1, -1), 0.25, colors.HexColor('#eef1f6')),
        ]))
        story.append(t)

        # Sub-informações (Maquina e Item)
        sub_parts = []
        if (it.get('maquina') or '').strip():
            sub_parts.append(f"Maquina: {it.get('maquina')}")
        if (it.get('codigo_interno') or '').strip():
            sub_parts.append(f"Item: {it.get('codigo_interno')}")
        if sub_parts:
            story.append(Spacer(1, 2))
            story.append(Paragraph(' | '.join(sub_parts), meta))

        # Descricao / Justificativa
        if (it.get('descricao') or '').strip():
            story.append(Spacer(1, 2))
            story.append(Paragraph(f"<b>Descricao:</b> {(it.get('descricao') or '')}", meta))

        # Fotos em linha (apenas insumo)
        fotos = it.get('fotos') or []
        if ((it.get('source') or '').lower() == 'insumo') and fotos:
            thumb = 70
            gap = 6
            per_row = max(1, int((avail + gap) // (thumb + gap)))
            rows, row_imgs = [], []
            for name in fotos:
                try:
                    path = os.path.join(app.root_path, app.config['FOTOS_INSUMOS_FOLDER'], name)
                    if not os.path.exists(path):
                        continue
                    row_imgs.append(Image(path, width=thumb, height=thumb))
                    if len(row_imgs) >= per_row:
                        rows.append(row_imgs)
                        row_imgs = []
                except Exception:
                    continue
            if row_imgs:
                rows.append(row_imgs)
            if rows:
                story.append(Spacer(1, 4))
                pt = Table(rows, colWidths=[thumb] * max(1, max(len(r) for r in rows)), hAlign='LEFT')
                pt.setStyle(TableStyle([
                    ('LEFTPADDING', (0, 0), (-1, -1), 0),
                    ('RIGHTPADDING', (0, 0), (-1, -1), 6),
                    ('TOPPADDING', (0, 0), (-1, -1), 0),
                    ('BOTTOMPADDING', (0, 0), (-1, -1), 0),
                ]))
                story.append(pt)

        story.append(Spacer(1, 10))

    # Página de gráfico – muda o template para não desenhar os cabeçalhos de colunas
    story.append(NextPageTemplate('Chart'))
    story.append(PageBreak())
    try:
        from collections import Counter
        import matplotlib.pyplot as plt
        counts = Counter([(it.get('prioridade') or 'baixa').lower() for it in filtered])
        if counts:
            labels = list(counts.keys())
            values = [counts[l] for l in labels]
            fig, ax = plt.subplots(figsize=(6.2, 3.8), dpi=150)
            bars = ax.bar(labels, values, color=['#27ae60', '#f1c40f', '#e67e22', '#e74c3c', '#8e44ad', '#3498db'])
            ax.set_title('Itens por Prioridade')
            ax.set_xlabel('Prioridade')
            ax.set_ylabel('Quantidade')
            for bar in bars:
                h = bar.get_height()
                ax.annotate(f'{int(h)}', xy=(bar.get_x()+bar.get_width()/2, h), xytext=(0, 5),
                            textcoords='offset points', ha='center', va='bottom', fontsize=9)
            fig.tight_layout()
            import io as _io
            img_buf = _io.BytesIO()
            fig.savefig(img_buf, format='png', dpi=160)
            plt.close(fig)
            img_buf.seek(0)
            story.append(Image(img_buf, width=doc.width, height=doc.width * 0.6))
    except Exception:
        pass

    doc.build(story)
    buf.seek(0)
    return buf

@app.route('/relatorio/ocorrencias')
def relatorio_ocorrencias():
    # Exige login para gerar relatório
    if not session.get('gestao_logged'):
        return redirect(url_for('login', next=request.path))
    with app.app_context():
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute('''
                SELECT i.*, ic.nome_descricao AS nome_item, ic.tipo_item AS tipo, ic.codigo_interno AS codigo_interno_item
                FROM insumos i
                LEFT JOIN itens_cadastro ic ON i.item_id = ic.id
                WHERE lower(ifnull(i.status,'')) = 'atendido'
                ORDER BY COALESCE(i.data_atendimento, i.data) DESC, i.id DESC
            ''')
            insumos_rows = cursor.fetchall()
            atendidos = []
            for row in insumos_rows:
                r = dict(row)
                # Parse fotos JSON from DB
                fotos_list = []
                try:
                    if r.get('fotos'):
                        fotos_list = json.loads(r.get('fotos'))
                        if not isinstance(fotos_list, list):
                            fotos_list = []
                except Exception:
                    fotos_list = []
                atendidos.append({
                    'id': r.get('id'),
                    'source': 'insumo',
                    'titulo': ((r.get('operador') or r.get('nome') or 'Insumo') + ((" - " + r.get('maquina')) if (r.get('operador') and r.get('maquina')) else '')),
                    'descricao': r.get('justificativa') or '',
                    'tipo': (r.get('tipo') or 'insumo'),
                    'prioridade': r.get('urgencia') or 'baixa',
                    'status_original': r.get('status') or 'Atendido',
                    'data_atendimento': r.get('data_atendimento') or r.get('data'),
                    'data_original': r.get('data'),
                    'atendida_por': r.get('atendida_por') or '',
                    'maquina': r.get('maquina') or '',
                    'observacoes_atendimento': '',
                    'nome_item': r.get('nome_item') or '',
                    'codigo_interno': r.get('codigo_interno') or r.get('codigo_interno_item'),
                    'fotos': fotos_list,
                    'fotos_urls': [f"/fotos_insumos/{name}" for name in fotos_list]
                })
            cursor.execute('''
                SELECT * FROM ocorrencias
                WHERE lower(ifnull(status,'')) IN ('fechada','atendida')
                ORDER BY data DESC, id DESC
            ''')
            ocorr_rows = cursor.fetchall()
            for row in ocorr_rows:
                r = dict(row)
                atendidos.append({
                    'id': r.get('id'),
                    'source': 'ocorrencia',
                    'titulo': r.get('titulo') or 'Ocorrência',
                    'descricao': r.get('descricao') or '',
                    'tipo': r.get('tipo') or 'ocorrencia',
                    'prioridade': r.get('prioridade') or 'baixa',
                    'status_original': r.get('status') or 'Fechada',
                    'data_atendimento': r.get('data'),
                    'data_original': r.get('data'),
                    'atendida_por': '',
                    'observacoes_atendimento': ''
                })
            conn.close()

            q_titulo = (request.args.get('titulo') or '').strip().lower()
            q_prioridade = (request.args.get('prioridade') or '').strip().lower()
            q_atendida = (request.args.get('atendida_por') or '').strip().lower()
            filtered = []
            for it in atendidos:
                if q_titulo:
                    titulo_l = (it.get('titulo') or '').lower()
                    nome_item_l = (it.get('nome_item') or '').lower()
                    cod_int_l = str(it.get('codigo_interno') or '').lower()
                    if q_titulo not in titulo_l and q_titulo not in nome_item_l and q_titulo not in cod_int_l:
                        continue
                if q_prioridade and (it.get('prioridade') or '').lower() != q_prioridade:
                    continue
                if q_atendida and q_atendida not in (it.get('atendida_por') or '').lower():
                    continue
                filtered.append(it)

            # Gerar via Platypus (layout moderno)
            try:
                buf = build_relatorio_pdf(filtered, q_titulo, q_prioridade, q_atendida)
                return send_file(buf, as_attachment=True, download_name='relatorio_ocorrencias.pdf', mimetype='application/pdf')
            except Exception as _e:
                logger.error(f"Falha no Platypus: {str(_e)}; usando layout legado.")
            try:
                from reportlab.lib.pagesizes import A4
                from reportlab.pdfgen import canvas
                from reportlab.lib import colors
                from reportlab.lib.utils import ImageReader
                import io
                width, height = A4
                buf = io.BytesIO()
                c = canvas.Canvas(buf, pagesize=A4)

                # Header bar
                c.setFillColorRGB(0.16, 0.22, 0.31)  # #2c3e50
                c.rect(0, height-60, width, 60, fill=1, stroke=0)
                c.setFillColor(colors.white)
                c.setFont('Helvetica-Bold', 16)
                c.drawString(40, height - 40, 'Relatório de Ocorrências / Atendidos')

                # Sub header with filters
                c.setFillColor(colors.black)
                c.setFont('Helvetica', 10)
                info = f"Filtros: tÃ­tulo='{q_titulo or 'Todos'}' â€¢ prioridade='{q_prioridade or 'Todas'}' â€¢ atendida_por='{q_atendida or 'Todos'}'"
                c.drawString(40, height - 75, info)
                if not (q_titulo or q_prioridade or q_atendida):
                    c.setFillColorRGB(0.10, 0.60, 0.40)
                    c.drawString(40, height - 90, 'Sem filtros aplicados â€” listando todos os registros.')
                    c.setFillColor(colors.black)

                # Resumo
                occ_count = sum(1 for it in filtered if (it.get('source') or '').lower() == 'ocorrencia')
                c.setFont('Helvetica', 10)
                c.drawString(40, height - 100, f"Total atendidos: {len(filtered)}  |  Ocorrências atendidas: {occ_count}")

                # Table header
                y = height - 110
                col_x = [40, 100, 340, 420, 500]  # Fonte, TÃ­tulo, Prioridade, Data, Atendida
                col_w = [60, 240, 80, 80, 80]
                headers = ['Fonte', 'TÃ­tulo', 'Prioridade', 'Data', 'Atendida por']
                c.setFillColorRGB(0.91, 0.95, 0.99)
                c.rect(36, y-4, width-72, 20, fill=1, stroke=0)
                c.setFillColor(colors.black)
                c.setFont('Helvetica-Bold', 9)
                for i, h in enumerate(headers):
                    c.drawString(col_x[i], y, h)
                y -= 16

                # Rows with zebra striping
                c.setFont('Helvetica', 9)
                row_bg = (0.98, 0.98, 0.98)
                for idx, it in enumerate(filtered):
                    # Space calculation for row height (with optional secondary line)
                    has_secondary = ((it.get('source') or '').lower() == 'insumo') and (((it.get('maquina') or '').strip()) or (it.get('codigo_interno') or '').strip())
                    space_needed = 30 if has_secondary else 18
                    if y - space_needed < 80:
                        c.showPage()
                        # repeat header on new page
                        c.setFillColorRGB(0.16, 0.22, 0.31)
                        c.rect(0, height-60, width, 60, fill=1, stroke=0)
                        c.setFillColor(colors.white)
                        c.setFont('Helvetica-Bold', 16)
                        c.drawString(40, height - 40, 'Relatório de Ocorrências / Atendidos')
                        c.setFillColor(colors.black)
                        c.setFont('Helvetica', 10)
                        c.drawString(40, height - 75, info)
                        c.setFont('Helvetica', 10)
                        occ_count = sum(1 for it2 in filtered if (it2.get('source') or '').lower() == 'ocorrencia')
                        c.drawString(40, height - 100, f"Total atendidos: {len(filtered)}  |  Ocorrências atendidas: {occ_count}")
                        y = height - 110
                        c.setFillColorRGB(0.91, 0.95, 0.99)
                        c.rect(36, y-4, width-72, 20, fill=1, stroke=0)
                        c.setFillColor(colors.black)
                        c.setFont('Helvetica-Bold', 9)
                        for i, h in enumerate(headers):
                            c.drawString(col_x[i], y, h)
                        y -= 16
                        c.setFont('Helvetica', 9)

                    if idx % 2 == 0:
                        c.setFillColorRGB(*row_bg)
                        c.rect(36, y-2, width-72, 18, fill=1, stroke=0)
                        c.setFillColor(colors.black)

                    fonte = (it.get('source') or '').title()
                    # Exibir titulo com destaque e incluir maquina em linha secundaria quando aplicavel
                    titulo = (it.get('titulo') or '')
                    if (it.get('source') or '').lower() == 'insumo' and (it.get('maquina') or ''):
                        titulo = f"{(titulo.split(' - ')[0] or titulo)[:60]}"
                    prioridade = (it.get('prioridade') or '').title()
                    data_str = it.get('data_atendimento') or it.get('data_original') or ''
                    atendida = it.get('atendida_por') or '-'

                    c.drawString(col_x[0], y, fonte)
                    # Titulo na primeira linha
                    c.drawString(col_x[1], y, (titulo or '')[:60])
                    # Linha secundaria com maquina e/ou item
                    y_secondary = y - 12
                    subparts = []
                    if (it.get('maquina') or ''):
                        subparts.append(f"Maquina: {it.get('maquina')}")
                    if (it.get('codigo_interno') or ''):
                        subparts.append(f"Item: {it.get('codigo_interno')}")
                    if subparts:
                        c.setFillColorRGB(0.30, 0.30, 0.30)
                        c.drawString(col_x[1], y_secondary, '  |  '.join(subparts)[:90])
                        c.setFillColor(colors.black)
                    c.drawString(col_x[2], y, prioridade)
                    c.drawString(col_x[3], y, data_str)
                    c.drawString(col_x[4], y, atendida)
                    # Ajusta altura da linha de acordo com conteudo secundario
                    y -= (30 if subparts else 18)

                    # Sem grade de fotos para manter layout limpo

                # Charts page
                try:
                    import matplotlib.pyplot as plt
                    from collections import Counter
                    # Estilo moderno
                    try:
                        plt.style.use('ggplot')
                    except Exception:
                        pass
                    counts = Counter([(it.get('prioridade') or 'baixa').lower() for it in filtered])
                    if counts:
                        labels = list(counts.keys())
                        values = [counts[l] for l in labels]
                        fig, ax = plt.subplots(figsize=(6.2, 3.8), dpi=150)
                        fig.patch.set_facecolor('white')
                        ax.set_facecolor('white')
                        palette = ['#27ae60', '#f1c40f', '#e67e22', '#e74c3c', '#8e44ad', '#3498db']
                        colors = [palette[i % len(palette)] for i in range(len(labels))]
                        bars = ax.bar(labels, values, color=colors, edgecolor='none')
                        # Valores no topo de cada barra
                        for bar in bars:
                            height = bar.get_height()
                            ax.annotate(f'{int(height)}',
                                        xy=(bar.get_x() + bar.get_width() / 2, height),
                                        xytext=(0, 6),
                                        textcoords='offset points',
                                        ha='center', va='bottom', fontsize=9, color='#2c3e50')
                        # Eixos e grade sutis
                        for spine in ['top', 'right']:
                            ax.spines[spine].set_visible(False)
                        ax.grid(True, axis='y', linestyle='--', alpha=0.25)
                        ax.set_axisbelow(True)
                        ax.set_title('Itens por Prioridade', fontsize=12, color='#2c3e50', pad=10)
                        ax.set_xlabel('Prioridade', fontsize=10)
                        ax.set_ylabel('Quantidade', fontsize=10)
                        fig.tight_layout()
                        import io as _io
                        img_buf = _io.BytesIO()
                        plt.savefig(img_buf, format='png', dpi=160)
                        plt.close(fig)
                        img_buf.seek(0)
                        img = ImageReader(img_buf)
                        c.showPage()
                        c.drawImage(img, 40, 160, width=520, height=380, preserveAspectRatio=True)
                except Exception:
                    pass

                c.save(); buf.seek(0)
                return send_file(buf, as_attachment=True, download_name='relatorio_ocorrencias.pdf', mimetype='application/pdf')
            except Exception as e:
                logger.error(f"Falha ao gerar PDF: {str(e)}")
                return make_response("Instale dependências: pip install reportlab matplotlib", 500)
        except Exception as e:
            logger.error(f"Erro no relatório: {str(e)}")
            return make_response(f"Erro ao gerar relatório: {str(e)}", 500)

@app.route('/cadastro')
def cadastro():
    logger.info("Rendering cadastro.html")
    return render_template('cadastro.html')

@app.route('/cadastro', methods=['POST'])
def cadastro_post():
    with db_lock:
        with app.app_context():
            conn = None
            try:
                conn = get_db_connection()
                cursor = conn.cursor()
                tipo_item = request.form.get('tipo_item')
                codigo_interno = request.form.get('codigo_interno', '').strip()
                nome_descricao = request.form.get('nome_descricao', '').strip()
                
                if not tipo_item or not codigo_interno or not nome_descricao:
                    conn.close()
                    logger.error("Missing required fields in cadastro")
                    return jsonify({'message': 'Por favor, preencha todos os campos obrigatórios.'}), 400
                
                if len(codigo_interno) < 2 or len(nome_descricao) < 3:
                    conn.close()
                    logger.error("Invalid input length for codigo_interno or nome_descricao")
                    return jsonify({'message': 'Código interno deve ter pelo menos 2 caracteres e nome/descrição pelo menos 3 caracteres.'}), 400
                
                cursor.execute('SELECT id FROM itens_cadastro WHERE lower(codigo_interno) = lower(?)', (codigo_interno,))
                if cursor.fetchone():
                    conn.close()
                    logger.error(f"Código interno {codigo_interno} já existe")
                    return jsonify({'message': 'Código interno já existe! Use um código diferente.'}), 400
                
                altura_min = request.form.get('altura_min')
                altura_min = float(altura_min) if altura_min and altura_min.strip() else None
                altura_max = request.form.get('altura_max')
                altura_max = float(altura_max) if altura_max and altura_max.strip() else None
                if altura_max is not None and altura_min is not None and altura_min > altura_max:
                    conn.close()
                    logger.error("Altura mÃ­nima maior que altura máxima")
                    return jsonify({'message': 'Altura mÃ­nima não pode ser maior que altura máxima.'}), 400
                
                rpm = request.form.get('rpm')
                rpm = int(rpm) if rpm and rpm.strip() else None
                avanco = request.form.get('avanco')
                avanco = float(avanco) if avanco and avanco.strip() else None
                
                categoria = request.form.get('categoria')
                categoria = categoria if categoria and categoria.strip() else None
                material = request.form.get('material')
                material = material if material and material.strip() else None
                
                foto_filename = None
                if 'foto' in request.files:
                    file = request.files['foto']
                    if file and file.filename != '' and allowed_file(file.filename):
                        if not os.path.exists(app.config['UPLOAD_FOLDER']):
                            os.makedirs(app.config['UPLOAD_FOLDER'])
                        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                        filename = f"{codigo_interno}_{timestamp}_{secure_filename(file.filename)}"
                        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                        file.save(filepath)
                        foto_filename = filename
                    elif file and file.filename != '':
                        conn.close()
                        logger.error("Invalid file format for foto")
                        return jsonify({'message': 'Formato de arquivo inválido. Use: PNG, JPG, JPEG, GIF, WEBP.'}), 400
                
                # Tipo de máquina (apenas para ferramenta)
                maquina = None
                if tipo_item == 'ferramenta':
                    maquina = request.form.get('ferramenta_tipo') or request.form.get('maquina') or None
                    if maquina:
                        maquina = maquina.strip() or None

                cursor.execute('''
                    INSERT INTO itens_cadastro (
                        tipo_item, codigo_fabricacao, codigo_interno, nome_descricao,
                        foto, categoria, material, maquina, altura_min, altura_max, rpm, avanco, data_cadastro
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    tipo_item,
                    request.form.get('codigo_fabricacao', ''),
                    codigo_interno,
                    nome_descricao,
                    foto_filename,
                    categoria,
                    material,
                    maquina,
                    altura_min,
                    altura_max,
                    rpm,
                    avanco,
                    datetime.now().strftime('%d/%m/%Y %H:%M')
                ))
                
                ferramenta_id = cursor.lastrowid
                
                if tipo_item == 'ferramenta':
                    insumos_ids = request.form.getlist('composicao_insumos')
                    quantidades = request.form.getlist('composicao_quantidades')
                    
                    if not insumos_ids:
                        conn.close()
                        logger.error("Ferramentas devem ter pelo menos um insumo na composição")
                        return jsonify({'message': 'Ferramentas devem ter pelo menos um insumo na composição.'}), 400
                    
                    for i, insumo_id in enumerate(insumos_ids):
                        if insumo_id and insumo_id.isdigit():
                            quantidade = int(quantidades[i]) if i < len(quantidades) and quantidades[i].isdigit() else 1
                            if quantidade <= 0:
                                conn.close()
                                logger.error("Invalid insumo quantity")
                                return jsonify({'message': 'Quantidade de insumo deve ser maior que zero.'}), 400
                            cursor.execute('''
                                INSERT INTO composicao_ferramentas (ferramenta_id, insumo_id, quantidade)
                                VALUES (?, ?, ?)
                            ''', (ferramenta_id, int(insumo_id), quantidade))
                
                conn.commit()
                
                # Salvar células (opcionais)
                try:
                    celulas_vals = request.form.getlist('celulas')
                    if len(celulas_vals) == 1 and celulas_vals[0] and celulas_vals[0].strip().startswith('['):
                        # Caso venha como JSON string
                        parsed = json.loads(celulas_vals[0])
                        celulas = [str(c).strip() for c in parsed if isinstance(c, (str, bytes)) and str(c).strip()]
                    else:
                        celulas = [c.strip() for c in celulas_vals if c and c.strip()]
                except Exception:
                    celulas = []

                if celulas:
                    for cel in celulas:
                        cursor.execute('INSERT INTO itens_celulas (item_id, celula) VALUES (?, ?)', (ferramenta_id, cel))
                    conn.commit()

                # Salvar máquinas cadastradas (opcionais)
                try:
                    maquinas_vals = request.form.getlist('maquinas')
                    if len(maquinas_vals) == 1 and maquinas_vals[0] and maquinas_vals[0].strip().startswith('['):
                        parsed_m = json.loads(maquinas_vals[0])
                        maquinas = [str(m).strip() for m in parsed_m if isinstance(m, (str, bytes)) and str(m).strip()]
                    else:
                        maquinas = [m.strip() for m in maquinas_vals if m and m.strip()]
                except Exception:
                    maquinas = []

                if maquinas:
                    for maq in maquinas:
                        cursor.execute('INSERT INTO itens_maquinas (item_id, maquina) VALUES (?, ?)', (ferramenta_id, maq))
                    conn.commit()

                conn.close()
                logger.info(f"Item {ferramenta_id} cadastrado com sucesso: {tipo_item}")
                return jsonify({'message': f'{tipo_item.title()} cadastrado(a) com sucesso!'})
                
            except sqlite3.IntegrityError as e:
                if conn is not None:
                    conn.close()
                logger.error(f"Erro de integridade ao cadastrar item: {str(e)}")
                return jsonify({'message': 'Código interno já existe! Use um código diferente.'}), 400
            except ValueError as e:
                if conn is not None:
                    conn.close()
                logger.error(f"Erro de validação ao cadastrar item: {str(e)}")
                return jsonify({'message': f'Erro de validação: {str(e)}'}), 400
            except Exception as e:
                if conn is not None:
                    conn.rollback()
                    conn.close()
                logger.error(f"Erro inesperado ao cadastrar item: {str(e)}")
                return jsonify({'message': f'Erro ao cadastrar: {str(e)}'}), 500

@app.route('/api/verificar_codigo_interno', methods=['GET'])
def verificar_codigo_interno():
    codigo = request.args.get('codigo')
    if not codigo or not isinstance(codigo, str) or codigo.strip() == '':
        logger.error("Código interno inválido ou não fornecido")
        return jsonify({"error": "Código interno inválido ou não fornecido"}), 400
    
    with app.app_context():  # Ensure application context
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute('SELECT 1 FROM itens_cadastro WHERE lower(codigo_interno) = lower(?)', (codigo,))
            exists = cursor.fetchone() is not None
            conn.close()
            logger.info(f"Verificação de código interno {codigo}: {'existe' if exists else 'não existe'}")
            return jsonify({"exists": exists})
        except sqlite3.Error as e:
            logger.error(f"Erro ao verificar código interno: {str(e)}")
            return jsonify({"error": f"Erro no banco de dados: {str(e)}"}), 500
            

@app.route('/gestao', methods=['GET', 'POST'])
def gestao():
    if not session.get('gestao_logged'):
        return redirect(url_for('login'))
    with app.app_context():  # Ensure application context
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute('SELECT i.*, ic.nome_descricao AS nome_item FROM insumos i LEFT JOIN itens_cadastro ic ON i.item_id = ic.id ORDER BY i.id DESC LIMIT 5')
            insumos_recentes = [dict(row) for row in cursor.fetchall()]
            cursor.execute('SELECT * FROM ocorrencias ORDER BY id DESC LIMIT 5')
            ocorrencias_recentes = [dict(row) for row in cursor.fetchall()]
            cursor.execute('SELECT COUNT(*) FROM insumos')
            total_insumos = cursor.fetchone()[0]
            cursor.execute('SELECT COUNT(*) FROM ocorrencias')
            total_ocorrencias = cursor.fetchone()[0]
            cursor.execute('SELECT COUNT(*) FROM itens_cadastro')
            total_itens_cadastro = cursor.fetchone()[0]
            conn.close()
            logger.info(f"Rendering gestao.html com {total_insumos} insumos, {total_ocorrencias} ocorrências, {total_itens_cadastro} itens")
            return render_template('gestao.html', 
                                total_insumos=total_insumos,
                                total_ocorrencias=total_ocorrencias,
                                total_itens_cadastro=total_itens_cadastro,
                                insumos_recentes=insumos_recentes,
                                ocorrencias_recentes=ocorrencias_recentes)
        except sqlite3.Error as e:
            logger.error(f"Erro ao carregar página de gestão: {str(e)}")
            return render_template('error.html', code=500, message=f"Erro no banco de dados: {str(e)}"), 500

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = (request.form.get('gestao_username') or request.form.get('username') or '').strip()
        password = (request.form.get('gestao_password') or request.form.get('password') or '').strip()
        next_url = (request.form.get('next') or request.args.get('next') or '').strip()
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute('SELECT username, password FROM admin LIMIT 1')
            row = cursor.fetchone()
            conn.close()
            if row and username == row['username'] and password == row['password']:
                session['gestao_logged'] = True
                session['gestao_user'] = username
                # Redireciona para o destino requisitado (ocorrencias/gestao/etc.)
                if next_url and next_url.startswith('/'):
                    return redirect(next_url)
                return redirect(url_for('gestao'))
            else:
                return render_template('login.html', error='Usuário ou senha inválidos')
        except Exception as e:
            logger.error(f"Erro no login: {str(e)}")
            return render_template('login.html', error='Erro ao processar login')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))

@app.route('/logout_beacon', methods=['POST'])
def logout_beacon():
    try:
        session.clear()
        return ('', 204)
    except Exception:
        return ('', 204)

@app.route('/gestao/reset', methods=['GET', 'POST'])
def gestao_reset():
    if request.method == 'POST':
        fab_login = request.form.get('fab_login', '').strip()
        fab_password = request.form.get('fab_password', '').strip()
        new_user = request.form.get('new_username', '').strip()
        new_pass = request.form.get('new_password', '').strip()

        if fab_login != 'TOOLTAG' or fab_password != '7001749':
            return render_template('reset_gestao.html', error='Credenciais do fabricante inválidas')
        if not new_user or not new_pass:
            return render_template('reset_gestao.html', error='Informe novo usuário e nova senha')

        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute('SELECT id FROM admin LIMIT 1')
            row = cursor.fetchone()
            if row:
                cursor.execute('UPDATE admin SET username = ?, password = ? WHERE id = ?', (new_user, new_pass, row['id']))
            else:
                cursor.execute('INSERT INTO admin (username, password) VALUES (?, ?)', (new_user, new_pass))
            conn.commit()
            conn.close()
            return render_template('reset_gestao.html', success='Usuário e senha atualizados com sucesso')
        except Exception as e:
            logger.error(f"Erro ao redefinir credenciais: {str(e)}")
            return render_template('reset_gestao.html', error='Erro ao atualizar credenciais')

    return render_template('reset_gestao.html')

# REMOVIDA a rota /visualocorrencia

@app.route('/visualinsumo')
def visual_insumo():
    id = request.args.get('id')
    logger.info(f"Acessando visualinsumo com id={id}")
    if not id or not id.isdigit():
        logger.error("ID de insumo inválido")
        return jsonify({'error': 'ID de insumo inválido'}), 400
    
    with app.app_context():  # Ensure application context
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute('SELECT i.*, ic.nome_descricao AS nome_item FROM insumos i LEFT JOIN itens_cadastro ic ON i.item_id = ic.id WHERE i.id = ?', (id,))
            insumo = cursor.fetchone()
            conn.close()
            
            if not insumo:
                logger.error(f"Insumo com id={id} não encontrado")
                return jsonify({'error': 'Insumo não encontrado'}), 404
            
            logger.info(f"Insumo encontrado: {dict(insumo)}")
            # Agora usar o HTML modificado para atender insumo
            return render_template('atender_insumo.html', insumo=dict(insumo))
        except Exception as e:
            logger.error(f"Erro na rota /visualinsumo: {str(e)}")
            return jsonify({'error': f'Erro interno: {str(e)}'}), 500

# Nova rota para API de insumo especÃ­fico
@app.route('/api/insumo/<int:id>', methods=['GET'])
def get_insumo(id):
    with app.app_context():
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute('SELECT i.*, ic.nome_descricao AS nome_item FROM insumos i LEFT JOIN itens_cadastro ic ON i.item_id = ic.id WHERE i.id = ?', (id,))
            insumo = cursor.fetchone()
            conn.close()
            
            if not insumo:
                logger.error(f"Insumo com id={id} não encontrado")
                return jsonify({"error": "Insumo não encontrado"}), 404
            
            insumo_dict = dict(insumo)
            # Converter fotos de JSON string para lista se existir
            if insumo_dict.get('fotos'):
                try:
                    insumo_dict['fotos'] = json.loads(insumo_dict['fotos'])
                except:
                    insumo_dict['fotos'] = []
            else:
                insumo_dict['fotos'] = []
                
            logger.info(f"Insumo retornado para id={id}: {insumo_dict}")
            return jsonify(insumo_dict)
        except sqlite3.Error as e:
            logger.error(f"Erro ao obter insumo id={id}: {str(e)}")
            return jsonify({"error": str(e)}), 500

# Nova rota para atender insumo (salvar fotos e atualizar status)
@app.route('/api/insumo/<int:id>/atender', methods=['PUT'])
def atender_insumo(id):
    with db_lock:
        with app.app_context():
            conn = None
            try:
                conn = get_db_connection()
                cursor = conn.cursor()
                
                # Verificar se o insumo existe
                cursor.execute('SELECT * FROM insumos WHERE id = ?', (id,))
                insumo = cursor.fetchone()
                if not insumo:
                    conn.close()
                    logger.error(f"Insumo com id={id} não encontrado")
                    return jsonify({'error': 'Insumo não encontrado'}), 404
                
                # Obter dados do formulário
                status = request.form.get('status', 'Pendente')
                sem_fotos = request.form.get('sem_fotos') == 'true'
                codigo_interno = request.form.get('codigo_interno', '')

                # Fotos existentes no banco (para preservar ao anexar novas)
                existing_fotos = []
                try:
                    raw = dict(insumo).get('fotos')
                    if raw:
                        existing_fotos = json.loads(raw)
                        if not isinstance(existing_fotos, list):
                            existing_fotos = []
                except Exception:
                    existing_fotos = []
                
                # Criar pasta para fotos de insumos se não existir
                if not os.path.exists(app.config['FOTOS_INSUMOS_FOLDER']):
                    os.makedirs(app.config['FOTOS_INSUMOS_FOLDER'])
                
                fotos_salvas = []
                
                # Processar fotos se status for Atendido e não marcou "sem fotos"
                if status == 'Atendido' and not sem_fotos:
                    for key in request.files:
                        if key.startswith('foto_'):
                            file = request.files[key]
                            if file and file.filename != '' and allowed_file(file.filename):
                                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                                filename = f"insumo_{id}_{timestamp}_{secure_filename(file.filename)}"
                                filepath = os.path.join(app.config['FOTOS_INSUMOS_FOLDER'], filename)
                                file.save(filepath)
                                fotos_salvas.append(filename)
                                logger.info(f"Foto salva: {filename}")
                
                # Atualizar no banco de dados (preservando fotos antigas)
                prev_status = (dict(insumo).get('status') or '').strip().lower()
                prev_data_at = dict(insumo).get('data_atendimento')
                if status == 'Atendido':
                    if prev_status != 'atendido' or not prev_data_at:
                        data_atendimento = datetime.now().strftime('%d/%m/%Y %H:%M')
                    else:
                        # Não alterar a data se já estava atendido
                        data_atendimento = prev_data_at
                else:
                    data_atendimento = None
                combined_fotos = (existing_fotos or []) + (fotos_salvas or [])
                # Remover duplicadas mantendo ordem
                seen = set(); combined_unique = []
                for f in combined_fotos:
                    if f not in seen:
                        combined_unique.append(f); seen.add(f)
                fotos_json = json.dumps(combined_unique)

                # Preservar codigo_interno atual se não foi enviado
                current_codigo_interno = (dict(insumo).get('codigo_interno') or '')
                final_codigo_interno = codigo_interno if (codigo_interno is not None and len(codigo_interno.strip()) > 0) else current_codigo_interno
                # Nome de quem atendeu (opcional)
                atendida_por = request.form.get('atendida_por', '').strip()
                
                cursor.execute('''
                    UPDATE insumos SET 
                        status = ?, 
                        codigo_interno = ?, 
                        fotos = ?, 
                        sem_fotos = ?, 
                        data_atendimento = ?,
                        atendida_por = COALESCE(NULLIF(?, ''), atendida_por)
                    WHERE id = ?
                ''', (status, final_codigo_interno, fotos_json, 1 if sem_fotos else 0, data_atendimento, atendida_por, id))
                
                conn.commit()
                conn.close()
                
                logger.info(f"Insumo {id} atualizado com sucesso - Status: {status}, Fotos: {len(fotos_salvas)}")
                return jsonify({
                    'message': 'Insumo atualizado com sucesso!',
                    'status': status,
                    'fotos_count': len(fotos_salvas),
                    'codigo_interno': final_codigo_interno,
                    'fotos': combined_unique,
                    'fotos_urls': [f"/fotos_insumos/{name}" for name in combined_unique]
                })
                
            except sqlite3.Error as e:
                if conn is not None:
                    conn.rollback()
                    conn.close()
                logger.error(f"Erro no banco de dados ao atender insumo {id}: {str(e)}")
                return jsonify({'error': f'Erro no banco de dados: {str(e)}'}), 500
            except Exception as e:
                if conn is not None:
                    conn.rollback()
                    conn.close()
                logger.error(f"Erro inesperado ao atender insumo {id}: {str(e)}")
                return jsonify({'error': f'Erro inesperado: {str(e)}'}), 500

@app.route('/api/insumo/<int:id>/foto', methods=['DELETE'])
def delete_insumo_foto(id):
    """Remove uma foto persistida de um insumo e atualiza o array de fotos.
    ParÃ¢metro: name (querystring) com o nome do arquivo.
    """
    with db_lock:
        with app.app_context():
            conn = None
            try:
                name = request.args.get('name')
                if not name:
                    return jsonify({'error': 'Nome da foto não informado'}), 400
                conn = get_db_connection()
                cursor = conn.cursor()
                cursor.execute('SELECT fotos FROM insumos WHERE id = ?', (id,))
                row = cursor.fetchone()
                if not row:
                    if conn:
                        conn.close()
                    return jsonify({'error': 'Insumo não encontrado'}), 404
                fotos = []
                try:
                    if row['fotos']:
                        fotos = json.loads(row['fotos'])
                        if not isinstance(fotos, list):
                            fotos = []
                except Exception:
                    fotos = []
                # Remove a foto solicitada
                fotos = [f for f in fotos if f != name]
                cursor.execute('UPDATE insumos SET fotos = ? WHERE id = ?', (json.dumps(fotos), id))
                conn.commit()
                conn.close()
                # Tentar remover o arquivo do disco (opcional)
                try:
                    path = os.path.join(app.config['FOTOS_INSUMOS_FOLDER'], name)
                    if os.path.exists(path):
                        os.remove(path)
                except Exception:
                    pass
                return jsonify({'message': 'Foto removida', 'fotos': fotos, 'fotos_urls': [f"/fotos_insumos/{n}" for n in fotos]})
            except sqlite3.Error as e:
                if conn is not None:
                    conn.rollback()
                    conn.close()
                return jsonify({'error': f'Erro no banco de dados: {str(e)}'}), 500

@app.route('/api/itens_cadastro', methods=['GET'])
def api_itens_cadastro():
    with app.app_context():  # Ensure application context
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM itens_cadastro ORDER BY nome_descricao")
            itens = [dict(row) for row in cursor.fetchall()]
            for item in itens:
                cursor.execute('''
                    SELECT i.id, i.nome_descricao AS nome
                    FROM composicao_ferramentas cf
                    JOIN itens_cadastro i ON cf.insumo_id = i.id
                    WHERE cf.ferramenta_id = ?
                ''', (item['id'],))
                item['composicao'] = [dict(row) for row in cursor.fetchall()]
                cursor.execute('''
                    SELECT DISTINCT maquina
                    FROM itens_maquinas
                    WHERE item_id = ? AND maquina IS NOT NULL AND TRIM(maquina) != ''
                    ORDER BY maquina
                ''', (item['id'],))
                item['maquinas'] = [row['maquina'] for row in cursor.fetchall()]

            conn.close()
            logger.info(f"API /api/itens_cadastro retornou {len(itens)} itens")
            return jsonify(itens)
        except sqlite3.Error as e:
            logger.error(f"Erro na API /api/itens_cadastro: {str(e)}")
            return jsonify({"error": str(e)}), 500

@app.route('/api/itens_cadastro/<int:id>', methods=['GET'])
def get_item(id):
    with app.app_context():  # Ensure application context
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM itens_cadastro WHERE id = ?', (id,))
            item = cursor.fetchone()
            if not item:
                conn.close()
                logger.error(f"Item com id={id} não encontrado")
                return jsonify({"error": "Item não encontrado"}), 404
            
            item_dict = dict(item)
            cursor.execute('''
                SELECT i.id, i.nome_descricao AS nome
                FROM composicao_ferramentas cf
                JOIN itens_cadastro i ON cf.insumo_id = i.id
                WHERE cf.ferramenta_id = ?
            ''', (id,))
            item_dict['composicao'] = [dict(row) for row in cursor.fetchall()]

            cursor.execute('''
                SELECT DISTINCT maquina
                FROM itens_maquinas
                WHERE item_id = ? AND maquina IS NOT NULL AND TRIM(maquina) != ''
                ORDER BY maquina
            ''', (id,))
            item_dict['maquinas'] = [row['maquina'] for row in cursor.fetchall()]

            # Células
            cursor.execute('SELECT celula FROM itens_celulas WHERE item_id = ?', (id,))
            item_dict['celulas'] = [row['celula'] for row in cursor.fetchall()]

            conn.close()
            logger.info(f"Item retornado para id={id}: {item_dict}")
            return jsonify(item_dict)
        except sqlite3.Error as e:
            logger.error(f"Erro ao obter item id={id}: {str(e)}")
            return jsonify({"error": str(e)}), 500

@app.route('/api/itens_cadastro/codigo/<string:codigo_interno>', methods=['GET'])
def get_item_by_codigo(codigo_interno):
    with app.app_context():
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM itens_cadastro WHERE codigo_interno = ?', (codigo_interno,))
            item = cursor.fetchone()
            if not item:
                conn.close()
                logger.error(f"Item com codigo_interno={codigo_interno} não encontrado")
                return jsonify({"error": "Item não encontrado"}), 404
            
            item_dict = dict(item)
            cursor.execute('''
                SELECT i.id, i.nome_descricao AS nome
                FROM composicao_ferramentas cf
                JOIN itens_cadastro i ON cf.insumo_id = i.id
                WHERE cf.ferramenta_id = ?
            ''', (item_dict['id'],))
            item_dict['composicao'] = [dict(row) for row in cursor.fetchall()]

            cursor.execute('''
                SELECT DISTINCT maquina
                FROM itens_maquinas
                WHERE item_id = ? AND maquina IS NOT NULL AND TRIM(maquina) != ''
                ORDER BY maquina
            ''', (item_dict['id'],))
            item_dict['maquinas'] = [row['maquina'] for row in cursor.fetchall()]

            # Células
            cursor.execute('SELECT celula FROM itens_celulas WHERE item_id = ?', (item_dict['id'],))
            item_dict['celulas'] = [row['celula'] for row in cursor.fetchall()]

            conn.close()
            logger.info(f"Item retornado para codigo_interno={codigo_interno}: {item_dict}")
            return jsonify(item_dict)
        except sqlite3.Error as e:
            logger.error(f"Erro ao obter item codigo_interno={codigo_interno}: {str(e)}")
            return jsonify({"error": str(e)}), 500

@app.route('/api/itens_cadastro/<int:id>', methods=['PUT'])
def update_item(id):
    with db_lock:
        with app.app_context():  # Ensure application context
            conn = None
            try:
                conn = get_db_connection()
                cursor = conn.cursor()

                # Check if the request is for undoing a deletion
                if request.is_json and request.get_json().get('undo') == True:
                    cursor.execute('SELECT * FROM itens_cadastro_deleted WHERE id = ?', (id,))
                    deleted_item = cursor.fetchone()
                    if not deleted_item:
                        conn.close()
                        logger.error(f"Item excluÃ­do com id={id} não encontrado")
                        return jsonify({'message': 'Item excluÃ­do não encontrado.'}), 404

                    cursor.execute('SELECT id FROM itens_cadastro WHERE lower(codigo_interno) = lower(?)', (deleted_item['codigo_interno'],))
                    if cursor.fetchone():
                        conn.close()
                        logger.error(f"Não foi possÃ­vel desfazer: código interno {deleted_item['codigo_interno']} já está em uso")
                        return jsonify({'message': 'Não foi possÃ­vel desfazer: o código interno já está em uso.'}), 409

                    cursor.execute('''
                        INSERT INTO itens_cadastro (
                            id, tipo_item, codigo_fabricacao, codigo_interno, nome_descricao, foto,
                            categoria, material, maquina, altura_min, altura_max, rpm, avanco, data_cadastro
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        deleted_item['id'], deleted_item['tipo_item'], deleted_item['codigo_fabricacao'],
                        deleted_item['codigo_interno'], deleted_item['nome_descricao'], deleted_item['foto'],
                        deleted_item['categoria'], deleted_item['material'], deleted_item.get('maquina'), deleted_item['altura_min'],
                        deleted_item['altura_max'], deleted_item['rpm'], deleted_item['avanco'],
                        deleted_item['data_cadastro']
                    ))

                    cursor.execute('''
                        INSERT INTO composicao_ferramentas (ferramenta_id, insumo_id, quantidade)
                        SELECT ferramenta_id, insumo_id, quantidade
                        FROM composicao_ferramentas_deleted WHERE ferramenta_id = ?
                    ''', (id,))

                    cursor.execute('DELETE FROM composicao_ferramentas_deleted WHERE ferramenta_id = ?', (id,))
                    cursor.execute('DELETE FROM itens_cadastro_deleted WHERE id = ?', (id,))

                    conn.commit()
                    conn.close()
                    logger.info(f"Item {id} restaurado com sucesso")
                    return jsonify({'message': 'Item restaurado com sucesso!'})

                # Existing update logic
                form_data = request.form
                cursor.execute('SELECT * FROM itens_cadastro WHERE id = ?', (id,))
                existing_item = cursor.fetchone()
                if not existing_item:
                    conn.close()
                    logger.error(f"Item com id={id} não encontrado")
                    return jsonify({'message': 'Item não encontrado.'}), 404

                codigo_interno = form_data.get('codigo_interno', '').strip()
                if not codigo_interno:
                    conn.close()
                    logger.error("Código interno não fornecido")
                    return jsonify({'message': 'Código interno é obrigatório.'}), 400

                cursor.execute('SELECT id FROM itens_cadastro WHERE lower(codigo_interno) = lower(?) AND id != ?', (codigo_interno, id))
                if cursor.fetchone():
                    conn.close()
                    logger.error(f"Código interno {codigo_interno} já existe")
                    return jsonify({'message': 'Código interno já existe! Use um código diferente.'}), 400

                altura_min = form_data.get('altura_min')
                altura_min = float(altura_min) if altura_min and altura_min.strip() else None
                altura_max = form_data.get('altura_max')
                altura_max = float(altura_max) if altura_max and altura_max.strip() else None
                if altura_max is not None and altura_min is not None and altura_min > altura_max:
                    conn.close()
                    logger.error("Altura mÃ­nima maior que altura máxima")
                    return jsonify({'message': 'Altura mÃ­nima não pode ser maior que altura máxima.'}), 400

                rpm = form_data.get('rpm')
                rpm = int(rpm) if rpm and rpm.strip() else None
                avanco = form_data.get('avanco')
                avanco = float(avanco) if avanco and avanco.strip() else None

                categoria = form_data.get('categoria')
                categoria = categoria if categoria and categoria.strip() else None
                material = form_data.get('material')
                material = material if material and material.strip() else None

                foto_filename = existing_item['foto']
                if 'foto' in request.files:
                    file = request.files['foto']
                    if file and file.filename != '' and allowed_file(file.filename):
                        if not os.path.exists(app.config['UPLOAD_FOLDER']):
                            os.makedirs(app.config['UPLOAD_FOLDER'])
                        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                        filename = f"{codigo_interno}_{timestamp}_{secure_filename(file.filename)}"
                        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                        file.save(filepath)
                        if existing_item['foto'] and os.path.exists(os.path.join(app.config['UPLOAD_FOLDER'], existing_item['foto'])):
                            os.remove(os.path.join(app.config['UPLOAD_FOLDER'], existing_item['foto']))
                        foto_filename = filename
                    elif file and file.filename != '':
                        conn.close()
                        logger.error("Invalid file format for foto")
                        return jsonify({'message': 'Formato de arquivo inválido. Use: PNG, JPG, JPEG, GIF, WEBP.'}), 400

                if form_data.get('remove_foto') == 'true' and existing_item['foto']:
                    if os.path.exists(os.path.join(app.config['UPLOAD_FOLDER'], existing_item['foto'])):
                        os.remove(os.path.join(app.config['UPLOAD_FOLDER'], existing_item['foto']))
                    foto_filename = None

                maquina_upd = form_data.get('maquina') or form_data.get('ferramenta_tipo') or existing_item['maquina']
                maquina_upd = (maquina_upd or '').strip() or None

                cursor.execute('''
                    UPDATE itens_cadastro SET
                        tipo_item = ?,
                        codigo_fabricacao = ?,
                        codigo_interno = ?,
                        nome_descricao = ?,
                        foto = ?,
                        categoria = ?,
                        material = ?,
                        maquina = ?,
                        altura_min = ?,
                        altura_max = ?,
                        rpm = ?,
                        avanco = ?,
                        data_cadastro = ?
                    WHERE id = ?
                ''', (
                    form_data.get('tipo_item', ''),
                    form_data.get('codigo_fabricacao', ''),
                    codigo_interno,
                    form_data.get('nome_descricao', ''),
                    foto_filename,
                    categoria,
                    material,
                    maquina_upd,
                    altura_min,
                    altura_max,
                    rpm,
                    avanco,
                    datetime.now().strftime('%d/%m/%Y %H:%M'),
                    id
                ))

                cursor.execute('DELETE FROM composicao_ferramentas WHERE ferramenta_id = ?', (id,))
                if form_data.get('tipo_item') == 'ferramenta':
                    insumos_ids = json.loads(form_data.get('composicao', '[]'))
                    for insumo in insumos_ids:
                        if insumo.get('id'):
                            cursor.execute('''
                                INSERT INTO composicao_ferramentas (ferramenta_id, insumo_id, quantidade)
                                VALUES (?, ?, ?)
                            ''', (id, int(insumo['id']), 1))

                # Atualizar máquinas cadastradas
                maquinas_list = []
                try:
                    maquinas_vals = request.form.getlist('maquinas')
                    if len(maquinas_vals) == 1 and maquinas_vals[0] and maquinas_vals[0].strip().startswith('['):
                        parsed_m = json.loads(maquinas_vals[0])
                        maquinas_list = [str(m).strip() for m in parsed_m if isinstance(m, (str, bytes)) and str(m).strip()]
                    else:
                        maquinas_list = [m.strip() for m in maquinas_vals if m and m.strip()]
                except Exception:
                    maquinas_list = []

                cursor.execute('DELETE FROM itens_maquinas WHERE item_id = ?', (id,))
                if maquinas_list:
                    cursor.executemany('INSERT INTO itens_maquinas (item_id, maquina) VALUES (?, ?)', [(id, maq) for maq in maquinas_list])

                # Atualizar Células
                celulas_vals = request.form.getlist('celulas')
                celulas_list = []
                if len(celulas_vals) == 1 and celulas_vals[0] and celulas_vals[0].strip().startswith('['):
                    try:
                        parsed = json.loads(celulas_vals[0])
                        celulas_list = [str(c).strip() for c in parsed if isinstance(c, (str, bytes)) and str(c).strip()]
                    except Exception:
                        celulas_list = []
                else:
                    celulas_list = [c.strip() for c in celulas_vals if c and c.strip()]

                cursor.execute('DELETE FROM itens_celulas WHERE item_id = ?', (id,))
                if celulas_list:
                    for cel in celulas_list:
                        cursor.execute('INSERT INTO itens_celulas (item_id, celula) VALUES (?, ?)', (id, cel))

                conn.commit()
                conn.close()
                logger.info(f"Item {id} atualizado com sucesso")
                return jsonify({'message': 'Item atualizado com sucesso!'})
            except sqlite3.Error as e:
                if conn is not None:
                    conn.rollback()
                    conn.close()
                logger.error(f"Erro no banco de dados ao atualizar item {id}: {str(e)}")
                return jsonify({'message': f'Erro no banco de dados: {str(e)}'}), 500
            except ValueError as e:
                if conn is not None:
                    conn.close()
                logger.error(f"Erro de validação ao atualizar item {id}: {str(e)}")
                return jsonify({'message': f'Erro de validação: {str(e)}'}), 400
            except Exception as e:
                if conn is not None:
                    conn.rollback()
                    conn.close()
                logger.error(f"Erro inesperado ao atualizar item {id}: {str(e)}")
                return jsonify({'message': f'Erro inesperado: {str(e)}'}), 500

@app.route('/api/itens_cadastro/<int:id>', methods=['DELETE'])
def delete_item(id):
    with db_lock:
        with app.app_context():  # Ensure application context
            conn = None
            try:
                conn = get_db_connection()
                cursor = conn.cursor()
                
                cursor.execute('SELECT * FROM itens_cadastro WHERE id = ?', (id,))
                item = cursor.fetchone()
                if not item:
                    conn.close()
                    logger.error(f"Item com id={id} não encontrado")
                    return jsonify({"error": "Item não encontrado"}), 404
                
                cursor.execute('''
                    INSERT INTO itens_cadastro_deleted (
                        id, tipo_item, codigo_fabricacao, codigo_interno, nome_descricao, foto,
                        categoria, material, maquina, altura_min, altura_max, rpm, avanco, data_cadastro, deleted_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    item['id'],
                    item['tipo_item'],
                    item['codigo_fabricacao'],
                    item['codigo_interno'],
                    item['nome_descricao'],
                    item['foto'],
                    item['categoria'],
                    item['material'],
                    item['maquina'] if 'maquina' in item.keys() else None,
                    item['altura_min'],
                    item['altura_max'],
                    item['rpm'],
                    item['avanco'],
                    item['data_cadastro'],
                    datetime.now().strftime('%d/%m/%Y %H:%M')
                ))
                
                cursor.execute('''
                    INSERT INTO composicao_ferramentas_deleted (ferramenta_id, insumo_id, quantidade, deleted_at)
                    SELECT ferramenta_id, insumo_id, quantidade, ?
                    FROM composicao_ferramentas WHERE ferramenta_id = ?
                ''', (datetime.now().strftime('%d/%m/%Y %H:%M'), id))
                
                cursor.execute('DELETE FROM composicao_ferramentas WHERE ferramenta_id = ?', (id,))
                cursor.execute('DELETE FROM itens_cadastro WHERE id = ?', (id,))
                
                conn.commit()
                conn.close()
                logger.info(f"Item {id} excluído com sucesso")
                return jsonify({"message": "Item excluído com sucesso"})
            except sqlite3.Error as e:
                if conn is not None:
                    conn.rollback()
                    conn.close()
                logger.error(f"Erro ao excluir item {id}: {str(e)}")
                return jsonify({"error": str(e)}), 500

@app.route('/api/insumos', methods=['GET'])
def api_insumos():
    with app.app_context():  # Ensure application context
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            all_flag = (request.args.get('all') or '').lower() in ('1', 'true', 'yes')
            if all_flag:
                cursor.execute('''
                    SELECT i.*, ic.nome_descricao AS nome_item, ic.tipo_item AS tipo, ic.codigo_interno AS codigo_interno_item
                    FROM insumos i
                    LEFT JOIN itens_cadastro ic ON i.item_id = ic.id
                    ORDER BY i.data DESC
                ''')
            else:
                cursor.execute('''
                    SELECT i.*, ic.nome_descricao AS nome_item, ic.tipo_item AS tipo, ic.codigo_interno AS codigo_interno_item
                    FROM insumos i
                    LEFT JOIN itens_cadastro ic ON i.item_id = ic.id
                    WHERE lower(ifnull(i.status,'')) <> 'atendido'
                    ORDER BY i.data DESC
                ''')
            insumos = [dict(row) for row in cursor.fetchall()]
            conn.close()
            logger.info(f"API /api/insumos retornou {len(insumos)} insumos")
            return jsonify(insumos)
        except sqlite3.Error as e:
            logger.error(f"Erro na API /api/insumos: {str(e)}")
            return jsonify({"error": str(e)}), 500

@app.route('/api/insumos_cadastro', methods=['GET'])
def api_insumos_cadastro():
    with app.app_context():  # Ensure application context
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT id, nome_descricao, codigo_interno FROM itens_cadastro WHERE tipo_item = 'insumo' ORDER BY nome_descricao")
            insumos = [dict(row) for row in cursor.fetchall()]
            conn.close()
            logger.info(f"API /api/insumos_cadastro retornou {len(insumos)} insumos")
            return jsonify(insumos)
        except sqlite3.Error as e:
            logger.error(f"Erro na API /api/insumos_cadastro: {str(e)}")
            return jsonify({"error": str(e)}), 500

@app.route('/api/ocorrencias', methods=['GET'])
def api_ocorrencias():
    with app.app_context():  # Ensure application context
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM ocorrencias ORDER BY data DESC')
            ocorrencias = [dict(row) for row in cursor.fetchall()]
            conn.close()
            logger.info(f"API /api/ocorrencias retornou {len(ocorrencias)} ocorrências")
            return jsonify(ocorrencias)
        except sqlite3.Error as e:
            logger.error(f"Erro na API /api/ocorrencias: {str(e)}")
            return jsonify({"error": str(e)}), 500

@app.route('/api/atendidos', methods=['GET'])
def api_atendidos():
    """Retorna itens marcados como atendidos/resolvidos para a página de histórico.

    - Insumos com status 'Atendido' (usa data_atendimento)
    - Ocorrências com status 'Fechada' ou 'Atendida' (se existirem)
    """
    with app.app_context():
        try:
            conn = get_db_connection()
            cursor = conn.cursor()

            # Insumos atendidos
            cursor.execute('''
                SELECT i.*, ic.nome_descricao AS nome_item, ic.tipo_item AS tipo, ic.codigo_interno AS codigo_interno_item
                FROM insumos i
                LEFT JOIN itens_cadastro ic ON i.item_id = ic.id
                WHERE lower(ifnull(i.status,'')) = 'atendido'
                ORDER BY COALESCE(i.data_atendimento, i.data) DESC, i.id DESC
            ''')
            insumos_rows = cursor.fetchall()

            atendidos = []
            for row in insumos_rows:
                r = dict(row)
                # Parse fotos JSON for each insumo (API /api/atendidos)
                fotos_list = []
                try:
                    if r.get('fotos'):
                        fotos_list = json.loads(r.get('fotos'))
                        if not isinstance(fotos_list, list):
                            fotos_list = []
                except Exception:
                    fotos_list = []
                atendidos.append({
                    'id': r.get('id'),
                    'source': 'insumo',
                    'titulo': r.get('operador') or r.get('nome') or 'Insumo',
                    'nome_item': (
                        f"{r.get('nome_item')} (" + (r.get('codigo_interno') or r.get('codigo_interno_item') or 'Sem código') + ")"
                        if r.get('nome_item') else (r.get('codigo_interno') or r.get('codigo_interno_item'))
                    ),
                    'descricao': r.get('justificativa') or '',
                    'tipo': (r.get('tipo') or 'insumo'),
                    'prioridade': r.get('urgencia') or 'baixa',
                    'status_original': r.get('status') or 'Atendido',
                    'data_atendimento': r.get('data_atendimento') or r.get('data'),
                    'data_original': r.get('data'),
                    'maquina': r.get('maquina') or '',
                    'atendida_por': r.get('atendida_por') or '',
                    'observacoes_atendimento': '',
                    'codigo_interno': r.get('codigo_interno') or r.get('codigo_interno_item'),
                    'fotos': fotos_list,
                    'fotos_urls': [f"/fotos_insumos/{name}" for name in fotos_list]
                })

            # Ocorrências resolvidas (opcional, se houver)
            cursor.execute('''
                SELECT * FROM ocorrencias
                WHERE lower(ifnull(status,'')) IN ('fechada','atendida')
                ORDER BY data DESC, id DESC
            ''')
            ocorr_rows = cursor.fetchall()
            for row in ocorr_rows:
                r = dict(row)
                atendidos.append({
                    'id': r.get('id'),
                    'source': 'ocorrencia',
                    'titulo': r.get('titulo') or 'Ocorrência',
                    'descricao': r.get('descricao') or '',
                    'tipo': r.get('tipo') or 'ocorrencia',
                    'prioridade': r.get('prioridade') or 'baixa',
                    'status_original': r.get('status') or 'Fechada',
                    'data_atendimento': r.get('data'),
                    'data_original': r.get('data'),
                    'atendida_por': '',
                    'observacoes_atendimento': '',
                })

            conn.close()

            # Ordena por data de atendimento, decrescente
            def parse_dt(dt):
                try:
                    if not dt or dt == 'Data não informada':
                        return datetime.min
                    d, t = dt.split(' ')
                    dd, mm, yy = d.split('/')
                    return datetime(int(yy), int(mm), int(dd))
                except Exception:
                    return datetime.min

            atendidos.sort(
                key=lambda x: (parse_dt(x.get('data_atendimento') or ''), x.get('id') or 0),
                reverse=True
            )

            logger.info(f"API /api/atendidos retornou {len(atendidos)} registros")
            return jsonify(atendidos)
        except sqlite3.Error as e:
            logger.error(f"Erro na API /api/atendidos: {str(e)}")
            return jsonify({"error": str(e)}), 500

@app.route('/api/insumo/<int:id>', methods=['DELETE'])
def delete_insumo(id):
    with db_lock:
        with app.app_context():  # Ensure application context
            logger.info(f"Tentando excluir insumo com id={id}")
            try:
                conn = get_db_connection()
                cursor = conn.cursor()
                cursor.execute('SELECT id, nome FROM insumos WHERE id = ?', (id,))
                insumo = cursor.fetchone()
                if not insumo:
                    conn.close()
                    logger.warning(f"Insumo com id={id} não encontrado")
                    return jsonify({'error': 'Insumo não encontrado'}), 404
                
                logger.info(f"Insumo encontrado: id={insumo['id']}, nome={insumo['nome']}")
                cursor.execute('DELETE FROM insumos WHERE id = ?', (id,))
                conn.commit()
                conn.close()
                logger.info(f"Insumo id={id} excluído com sucesso")
                return jsonify({'message': 'Insumo excluído com sucesso!'})
            except sqlite3.Error as e:
                if conn is not None:
                    conn.rollback()
                    conn.close()
                logger.error(f"Erro na API /api/insumo/{id} (DELETE): {str(e)}")
    return jsonify({"error": str(e)}), 500


@app.route('/api/maquinas/item/<int:item_id>', methods=['GET'])
def api_maquinas_item(item_id):
    """Retorna as máquinas cadastradas para um item específico"""
    with app.app_context():
        conn = None
        try:
            conn = get_db_connection()
            cursor = conn.cursor()

            cursor.execute(
                """
                SELECT maquina
                FROM itens_maquinas
                WHERE item_id = ? AND maquina IS NOT NULL AND TRIM(maquina) != ''
                ORDER BY maquina
                """,
                (item_id,)
            )
            maquinas = [row['maquina'] for row in cursor.fetchall()]

            if not maquinas:
                cursor.execute('SELECT maquina FROM itens_cadastro WHERE id = ?', (item_id,))
                row = cursor.fetchone()
                if row and row['maquina']:
                    maquinas = [row['maquina']]

            conn.close()
            return jsonify(maquinas)
        except sqlite3.Error as e:
            if conn is not None:
                conn.close()
            logger.error(f"Erro ao buscar máquinas do item {item_id}: {str(e)}")
            return jsonify({'error': str(e)}), 500


@app.route('/api/maquinas', methods=['GET'])
def api_maquinas():
    """Retorna lista de todas as máquinas cadastradas no sistema"""
    with app.app_context():
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            # Busca máquinas únicas da tabela itens_maquinas
            cursor.execute('''
                SELECT DISTINCT maquina 
                FROM itens_maquinas 
                WHERE maquina IS NOT NULL AND maquina != ''
                ORDER BY maquina
            ''')
            maquinas_cadastro = [row['maquina'] for row in cursor.fetchall()]
            
            # Busca máquinas dos insumos solicitados também
            cursor.execute('''
                SELECT DISTINCT maquina 
                FROM insumos 
                WHERE maquina IS NOT NULL AND maquina != ''
                ORDER BY maquina
            ''')
            maquinas_insumos = [row['maquina'] for row in cursor.fetchall()]
            
            # Combina e remove duplicatas mantendo ordem
            todas_maquinas = list(set(maquinas_cadastro + maquinas_insumos))
            todas_maquinas.sort()
            
            conn.close()
            logger.info(f"API /api/maquinas retornou {len(todas_maquinas)} máquinas")
            return jsonify(todas_maquinas)
        except sqlite3.Error as e:
            logger.error(f"Erro na API /api/maquinas: {str(e)}")
            return jsonify({"error": str(e)}), 500

@app.route('/api/maquinas/search', methods=['GET'])
def search_maquinas():
    """Busca máquinas por termo (para autocomplete no frontend)"""
    termo = request.args.get('q', '').strip().lower()
    limit = request.args.get('limit', '10')
    
    try:
        limit = int(limit)
        if limit > 50:
            limit = 50
    except:
        limit = 10
    
    with app.app_context():
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            
            if termo:
                # Busca em itens_maquinas
                cursor.execute('''
                    SELECT DISTINCT maquina 
                    FROM itens_maquinas 
                    WHERE lower(maquina) LIKE ? AND maquina IS NOT NULL AND maquina != ''
                    ORDER BY maquina
                    LIMIT ?
                ''', (f'%{termo}%', limit))
                maquinas_cadastro = [row['maquina'] for row in cursor.fetchall()]
                
                # Busca em insumos
                cursor.execute('''
                    SELECT DISTINCT maquina 
                    FROM insumos 
                    WHERE lower(maquina) LIKE ? AND maquina IS NOT NULL AND maquina != ''
                    ORDER BY maquina
                    LIMIT ?
                ''', (f'%{termo}%', limit))
                maquinas_insumos = [row['maquina'] for row in cursor.fetchall()]
                
                # Combina resultados
                maquinas = list(set(maquinas_cadastro + maquinas_insumos))
                maquinas.sort()
                maquinas = maquinas[:limit]  # Aplica limite final
            else:
                cursor.execute('''
                    SELECT DISTINCT maquina 
                    FROM itens_maquinas 
                    WHERE maquina IS NOT NULL AND maquina != ''
                    ORDER BY maquina
                    LIMIT ?
                ''', (limit,))
                maquinas = [row['maquina'] for row in cursor.fetchall()]
                
            conn.close()
            return jsonify(maquinas)
        except sqlite3.Error as e:
            logger.error(f"Erro na busca de máquinas: {str(e)}")
            return jsonify({"error": str(e)}), 500

@app.route('/api/itens/por-maquina/<string:maquina_nome>', methods=['GET'])
def get_itens_por_maquina(maquina_nome):
    """Retorna todos os itens compatíveis com uma máquina específica"""
    with app.app_context():
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute('''
                SELECT ic.*, im.maquina
                FROM itens_cadastro ic
                JOIN itens_maquinas im ON ic.id = im.item_id
                WHERE im.maquina = ?
                ORDER BY ic.nome_descricao
            ''', (maquina_nome,))
            itens = [dict(row) for row in cursor.fetchall()]
            
            # Adiciona informações de composição para ferramentas
            for item in itens:
                if item['tipo_item'] == 'ferramenta':
                    cursor.execute('''
                        SELECT i.id, i.nome_descricao AS nome, cf.quantidade
                        FROM composicao_ferramentas cf
                        JOIN itens_cadastro i ON cf.insumo_id = i.id
                        WHERE cf.ferramenta_id = ?
                    ''', (item['id'],))
                    item['composicao'] = [dict(row) for row in cursor.fetchall()]
                else:
                    item['composicao'] = []
                
                # Adiciona todas as máquinas do item
                cursor.execute('SELECT maquina FROM itens_maquinas WHERE item_id = ?', (item['id'],))
                item['maquinas'] = [row['maquina'] for row in cursor.fetchall()]
            
            conn.close()
            logger.info(f"Encontrados {len(itens)} itens para a máquina '{maquina_nome}'")
            return jsonify(itens)
        except sqlite3.Error as e:
            logger.error(f"Erro ao buscar itens da máquina '{maquina_nome}': {str(e)}")
            return jsonify({"error": str(e)}), 500
# Reabrir (marcar como não atendida) um insumo já atendido
@app.route('/api/insumo/<int:id>/reabrir', methods=['PUT'])
def reabrir_insumo(id):
    with db_lock:
        with app.app_context():
            conn = None
            try:
                conn = get_db_connection()
                cursor = conn.cursor()
                cursor.execute('SELECT * FROM insumos WHERE id = ?', (id,))
                insumo = cursor.fetchone()
                if not insumo:
                    if conn:
                        conn.close()
                    return jsonify({'error': 'Insumo não encontrado'}), 404

                # Reabre: volta status para Pendente e limpa data_atendimento
                cursor.execute('''
                    UPDATE insumos SET
                        status = ?,
                        data_atendimento = NULL
                    WHERE id = ?
                ''', ('Pendente', id))
                conn.commit()
                conn.close()
                return jsonify({'message': 'Insumo reaberto (marcado como não atendido)'}), 200
            except sqlite3.Error as e:
                if conn is not None:
                    conn.rollback()
                    conn.close()
                return jsonify({'error': f'Erro no banco de dados: {str(e)}'}), 500

# Reabrir (marcar como não atendida) uma ocorrência fechada
@app.route('/api/ocorrencia/<int:id>/reabrir', methods=['PUT'])
def reabrir_ocorrencia(id):
    with db_lock:
        with app.app_context():
            conn = None
            try:
                conn = get_db_connection()
                cursor = conn.cursor()
                cursor.execute('SELECT * FROM ocorrencias WHERE id = ?', (id,))
                ocorr = cursor.fetchone()
                if not ocorr:
                    if conn:
                        conn.close()
                    return jsonify({'error': 'Ocorrência não encontrada'}), 404

                cursor.execute('''
                    UPDATE ocorrencias SET
                        status = 'Aberta'
                    WHERE id = ?
                ''', (id,))
                conn.commit()
                conn.close()
                return jsonify({'message': 'Ocorrência reaberta (marcada como não atendida)'}), 200
            except sqlite3.Error as e:
                if conn is not None:
                    conn.rollback()
                    conn.close()
                return jsonify({'error': f'Erro no banco de dados: {str(e)}'}), 500

@app.route('/api/ocorrencia/<int:id>', methods=['DELETE'])
def delete_ocorrencia(id):
    with db_lock:
        with app.app_context():  # Ensure application context
            logger.info(f"Tentando excluir ocorrência com id={id}")
            try:
                conn = get_db_connection()
                cursor = conn.cursor()
                cursor.execute('SELECT id, titulo FROM ocorrencias WHERE id = ?', (id,))
                ocorrencia = cursor.fetchone()
                if not ocorrencia:
                    conn.close()
                    logger.warning(f"Ocorrência com id={id} não encontrada")
                    return jsonify({'error': 'Ocorrência não encontrada'}), 404
                
                logger.info(f"Ocorrência encontrada: id={ocorrencia['id']}, titulo={ocorrencia['titulo']}")
                cursor.execute('DELETE FROM ocorrencias WHERE id = ?', (id,))
                conn.commit()
                conn.close()
                logger.info(f"Ocorrência id={id} excluída com sucesso")
                return jsonify({'message': 'Ocorrência excluída com sucesso!'})
            except sqlite3.Error as e:
                if conn is not None:
                    conn.rollback()
                    conn.close()
                logger.error(f"Erro na API /api/ocorrencia/{id} (DELETE): {str(e)}")
                return jsonify({"error": str(e)}), 500

@app.route('/fotos_cadastro/<filename>')
def uploaded_file(filename):
    with app.app_context():  # Ensure application context
        try:
            logger.info(f"Servindo arquivo {filename}")
            return send_from_directory(app.config['UPLOAD_FOLDER'], filename)
        except Exception as e:
            logger.error(f"Erro ao servir arquivo {filename}: {str(e)}")
            return jsonify({"error": "Arquivo não encontrado"}), 404

@app.route('/fotos_insumos/<filename>')
def uploaded_insumo_file(filename):
    with app.app_context():
        try:
            logger.info(f"Servindo foto de insumo {filename}")
            return send_from_directory(app.config['FOTOS_INSUMOS_FOLDER'], filename)
        except Exception as e:
            logger.error(f"Erro ao servir foto de insumo {filename}: {str(e)}")
            return jsonify({"error": "Arquivo não encontrado"}), 404

@app.route('/editor')
def editor_page():
    item_id = request.args.get('id')
    return render_template('editor.html', item_id=item_id)

@app.route('/ficha')
def ficha():
    """Página de ficha técnica de um item"""
    codigo_interno = request.args.get('codigo_interno')
    
    if not codigo_interno:
        logger.error("Código interno do item não fornecido na rota /ficha")
        return render_template('error.html', code=400, message="Código interno do item não fornecido"), 400
    
    with app.app_context():  # Ensure application context
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute('SELECT * FROM itens_cadastro WHERE codigo_interno = ?', (codigo_interno,))
            item = cursor.fetchone()
            conn.close()
            
            if not item:
                logger.error(f"Item com codigo_interno={codigo_interno} não encontrado")
                return render_template('error.html', code=404, message="Item não encontrado no banco de dados"), 404
            
            logger.info(f"Item encontrado para codigo_interno={codigo_interno}: {dict(item)}")
            return render_template('ficha.html', item=dict(item))
        except sqlite3.Error as e:
            logger.error(f"Erro ao buscar item com codigo_interno={codigo_interno}: {str(e)}")
            return render_template('error.html', code=500, message=f"Erro no banco de dados: {str(e)}"), 500    

@app.template_filter('title_case')
def title_case_filter(text):
    return text.title() if text else ''

@app.template_filter('status_color')
def status_color_filter(status):
    status_classes = {
        'pendente': 'pendente',
        'aberta': 'aberta',
        'fechada': 'fechada',
        'em_andamento': 'em_andamento',
        'cadastrado': 'cadastrado',
        'atendido': 'atendido'
    }
    return status_classes.get(status.lower().replace(' ', '_'), 'default')

@app.route('/test/populate/<int:id>', methods=['POST'])
def test_populate_item(id):
    with db_lock:
        with app.app_context():  # Ensure application context
            conn = None
            try:
                conn = get_db_connection()
                cursor = conn.cursor()
                cursor.execute('''
                    UPDATE itens_cadastro SET
                        categoria = ?,
                        material = ?,
                        altura_min = ?,
                        altura_max = ?,
                        rpm = ?,
                        avanco = ?
                    WHERE id = ?
                ''', (
                    'Teste Categoria',
                    'Teste Material',
                    10.5,
                    20.5,
                    1000,
                    0.5,
                    id
                ))
                conn.commit()
                conn.close()
                logger.info(f"Item {id} populado com dados de teste")
                return jsonify({'message': f'Item {id} populated with test data'})
            except sqlite3.Error as e:
                if conn is not None:
                    conn.rollback()
                    conn.close()
                logger.error(f"Erro ao popular item {id}: {str(e)}")
                return jsonify({'error': str(e)}), 500

# Error handler for Socket.IO bad requests
@app.errorhandler(400)
def handle_bad_request(e):
    logger.error(f"Bad request error: {str(e)}")
    return jsonify({"error": str(e)}), 400

# Ensure database schema exists when running under WSGI and recreate if file is missing
_db_bootstrapped = False

@app.before_request
def _ensure_db_ready():
    global _db_bootstrapped
    try:
        # Run init once per process (covers WSGI where __main__ block doesn't run)
        if not _db_bootstrapped:
            logger.info("Bootstrapping database structure (first request)...")
            init_db()
            _db_bootstrapped = True
        # If file was deleted at runtime, recreate structure
        elif not os.path.exists(DATABASE):
            logger.warning("Database file not found. Recreating structure...")
            init_db()
    except Exception as e:
        logger.error(f"Failed to ensure database readiness: {e}")

def guess_local_ip():
    """Descobre o IP local sem depender de DNS e, se houver eventlet,
    usa o socket ORIGINAL (não monkey-patched) para evitar greendns."""
    try:
        try:
            import eventlet  # pode não existir
            socket_mod = eventlet.patcher.original('socket')
        except Exception:
            import socket as socket_mod

        s = socket_mod.socket(socket_mod.AF_INET, socket_mod.SOCK_DGRAM)
        try:
            # não envia nada; só força escolha da interface
            s.connect(('8.8.8.8', 80))
            ip = s.getsockname()[0]
        finally:
            s.close()
        return ip
    except Exception:
        return None
    
if __name__ == '__main__':
    init_db()
    host = '0.0.0.0'
    port = 5000

    print(f"Local:   http://127.0.0.1:{port}")
    ip = guess_local_ip()
    if ip and ip not in ('127.0.0.1', '0.0.0.0'):
        print(f"Na rede: http://{ip}:{port}")
    else:
        print("Na rede: não consegui detectar o IP automaticamente. Use o IP do 'ipconfig'.")

    # Evita duplicar prints em modo debug no Windows
    socketio.run(app, host=host, port=port, debug=True, use_reloader=False)


