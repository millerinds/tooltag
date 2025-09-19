from pathlib import Path
path = Path('templates/ocorrencias.html')
text = path.read_text(encoding='utf-8')
old_block = "                <div class=\"filter-group\">\n                    <label>Data de Atendimento</label>\n\n                    <div class=\"date-range\">\n\n                        <input type=\"date\" id=\"filterDataInicio\">\n\n                        <span>atǸ</span>\n\n                        <input type=\"date\" id=\"filterDataFim\">\n\n                </div>\n\n"
if old_block not in text:
    old_block = "                <div class=\"filter-group\">\n                    <label>Data de Atendimento</label>\n\n                    <div class=\"date-range\">\n\n                        <input type=\"date\" id=\"filterDataInicio\">\n\n                        <span>até</span>\n\n                        <input type=\"date\" id=\"filterDataFim\">\n\n                </div>\n\n"
new_block = "                <div class=\"filter-group\">\n                    <label>Data de Atendimento</label>\n                    <div class=\"date-range\">\n                        <input type=\"date\" id=\"filterDataInicio\">\n                        <span>até</span>\n                        <input type=\"date\" id=\"filterDataFim\">\n                    </div>\n                </div>\n"
if old_block in text:
    text = text.replace(old_block, new_block, 1)
else:
    raise SystemExit('date filter block not found for normalization')
path.write_text(text, encoding='utf-8')
