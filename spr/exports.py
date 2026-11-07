"""Standalone comparison reports with shared data for HTML and Excel."""
import html
import json
from itertools import groupby
from datetime import datetime, timezone
from typing import NamedTuple
from urllib.parse import urlencode
from xml.etree.ElementTree import Element, SubElement, tostring

from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter


class Section(NamedTuple):
    name: str
    columns: tuple
    rows: object
    note: str = ''
    percent: tuple = ()
    bands: tuple = ()


class Percent(float):
    pass


class Formula(str):
    pass


class ReportLink(NamedTuple):
    text: str
    href: str


FACT_DIMS = ('PREFIX', 'CLASS', 'SUFFIX', 'ARRAY_KEY', 'KEY', 'VALUE_ADJUSTED', 'DIFF_FLAG', 'SOURCE')
FACT_COLUMNS = (*FACT_DIMS, 'CNT')
FACT_SIDES = {'BEFORE': 'BEFORE (MASTER)', 'AFTER': 'AFTER (BETA)'}
PIVOT_SLICES = [(flag, side) for flag in (0, 1) for side in FACT_SIDES.values()]
LABELS = {
    'BEFORE': 'До изменений', 'AFTER': 'После изменений',
    'APPROVE': 'Одобрено', 'REJECT': 'Отказано', 'UNKNOWN': 'Не определено',
    'ABSENT': 'Ответ отсутствует', 'PASS': 'Пройдена', 'FAIL': 'Есть отклонение',
    'ERROR': 'Ошибка выполнения', 'INFO': 'Справочно', 'HIGH': 'Высокая', 'LOW': 'Низкая',
    'parsed': 'Прочитан', 'missing': 'Ответ отсутствует', 'duplicate': 'Дубликат',
    'parse_error': 'Ошибка чтения', 'schema_error': 'Ошибка структуры', 'id_conflict': 'Номер не совпадает',
    'checksum_error': 'Контрольная сумма не совпала', 'VERIFICATION': 'На проверку',
    'VALUE_CHANGED': 'Значение изменилось', 'ATTRIBUTE_NEW': 'Поле появилось', 'ATTRIBUTE_LOST': 'Поле исчезло',
}
CHECK_FIELDS = ('LABEL', 'SOURCE', 'ARGUMENT', 'CNT', 'TOTAL', 'METRIC', 'RESULT', 'CONFIDENCE', 'APP_IDS')
CHECK_COLUMNS = ('Проверка', 'Версия', 'Аргумент', 'Заявок', 'База заявок', 'Метрика',
                 'Результат', 'Уверенность', 'Заявки из результата')


def label(value):
    return LABELS.get(value, value)


def fact_values(row, *, json_types=False):
    return {**row, 'SOURCE': FACT_SIDES.get(row['SOURCE'], row['SOURCE']),
            'DIFF_FLAG': str(row['DIFF_FLAG']) if json_types else row['DIFF_FLAG']}


def pivot_sections(facts, *, excel=False):
    rows = [tuple(row[k] for k in FACT_COLUMNS) for row in map(fact_values, facts)]
    rows.sort(key=lambda row: (tuple((value is not None, value or '') for value in row[:6]), row[6], row[7]))
    def pivot_rows():
        start = 4  # first data row on «Исходные данные»
        for index, (key, group) in enumerate(groupby(rows, key=lambda row: row[:6]), 5):
            items = list(group)
            end = start + len(items) - 1
            if excel:
                first, last = (4, len(rows)+3) if len(rows)<=1000 else (start, end)
                def formula(col):
                    def source(letter):
                        return f"'Исходные данные'!${letter}${first}:${letter}${last}"
                    terms = [f'EXACT({source(letter)}&"",${letter}{index}&"")' for letter in 'ABCDEF']
                    terms += [f'({source("G")}={col}$3)', f'({source("H")}={col}$4)', source('I')]
                    return Formula('=SUMPRODUCT(' + '*'.join(terms) + ')')
                yield (*key, *(formula(col) for col in 'GHIJ'))
            else:
                counts = {(flag, side): count for *_, flag, side, count in items}
                yield (*key, *(counts.get(pair, 0) for pair in PIVOT_SLICES))
            start = end + 1
    note = 'Значение ячейки — количество уникальных заявок. Суммы по разным атрибутам пересекаются.'
    if excel and len(rows)>1000:
        note += ' При правке большого отчёта сохраняйте порядок и группы исходных строк.'
    columns = (*FACT_DIMS[:6], *(side for _, side in PIVOT_SLICES))
    bands = (None, None, None, None, None, 'DIFF_FLAG', *(flag for flag, _ in PIVOT_SLICES))
    if not excel:
        columns = (*FACT_DIMS[:6], *(f'{side} · DIFF_FLAG {flag}' for flag, side in PIVOT_SLICES))
    yield Section('Сводная таблица', columns, pivot_rows(), note, bands=bands if excel else ())
    if excel:
        yield Section('Исходные данные', FACT_COLUMNS, rows, 'Факты, по которым пересчитывается сводная таблица.')


def check_results(saved_checks):
    return [row for row in saved_checks if row.get('RESULT') in ('PASS', 'FAIL') and not is_bucket(row)]


def report_buckets(mart, saved_checks):
    options = mart.analysis.options
    if not options.filtered and options.basis == 'historical':
        saved = [r for r in saved_checks if is_bucket(r) and r.get('REPORT_VERSION') == 2]
        if saved:
            return saved
    return mart.bucket_report()


def report_sections(mart, run, saved_checks, pivot=None, fact_filters=None):
    scope = mart.analysis
    summary = scope.summary()
    paired = mart.query("""SELECT count(*) n,
        count(*) FILTER (WHERE DECISION_BEFORE='APPROVE') before,
        count(*) FILTER (WHERE DECISION_AFTER='APPROVE') after
        FROM segments SEMI JOIN original_segments USING (APP_ID)
        WHERE DECISION_BEFORE IN ('APPROVE','REJECT') AND DECISION_AFTER IN ('APPROVE','REJECT')""")[0]
    n = paired['n']
    approval_before = paired['before'] / n if n else None
    approval_after = paired['after'] / n if n else None
    delta = approval_after - approval_before if n else None
    yield Section('Обзор', ('Показатель', 'Значение'), (
        ('Заявок в выборке', summary['discovered']),
        ('Валидных пар', summary['valid_pairs']),
        ('Смен решений', summary['decision_changed']),
        ('Заявок с изменениями полей', summary['changed_apps']),
        ('Исключено из сравнения полей', summary['excluded']),
        ('Появились после изменений', summary['added']),
        ('Отсутствуют после изменений', summary['removed']),
        ('Пар с известным решением в обеих версиях', n),
        ('Доля одобрений до изменений', Percent(approval_before) if n else None),
        ('Доля одобрений после изменений', Percent(approval_after) if n else None),
        ('Разница долей одобрения', f'{delta*100:+.2f} п. п.' if n else None),
    ), 'Доли одобрения рассчитаны по одним и тем же валидным парам с известным решением в обеих версиях.')
    yield Section('Переходы решений', ('До изменений', 'После изменений', 'Заявок', 'Доля выборки'),
        [(label(before), label(after), count, count / summary['discovered'])
         for before, after, count in mart.con.execute("""SELECT DECISION_BEFORE, DECISION_AFTER, count(*) CNT
             FROM segments GROUP BY ALL ORDER BY CNT DESC, DECISION_BEFORE, DECISION_AFTER""").fetchall()],
        'Включены заявки с пропущенными и неопределёнными решениями.', (3,))
    yield Section('Заявки', ('Номер заявки', 'До изменений', 'После изменений', 'Решение изменилось', 'Валидная пара'),
        [(app, label(before), label(after), 'Да' if changed else 'Нет', 'Да' if valid else 'Нет')
         for app, before, after, changed, valid in mart.con.execute("""SELECT s.APP_ID, s.DECISION_BEFORE,
             s.DECISION_AFTER, s.DECISION_CHANGED, o.APP_ID IS NOT NULL
             FROM segments s LEFT JOIN original_segments o USING (APP_ID)
             ORDER BY s.DECISION_CHANGED DESC, s.APP_ID""").fetchall()])
    yield Section('Качество', ('Версия', 'Статус', 'Заявок'),
        [(label(source), label(status), count) for source, status, count in mart.con.execute("""SELECT SOURCE,
            STATUS, count(DISTINCT APP_ID) FROM quality GROUP BY ALL ORDER BY SOURCE, STATUS""").fetchall()],
        'Одна заявка может встречаться в нескольких статусах, поэтому суммы по статусам могут пересекаться.')
    yield Section('Ошибки и исключения', ('Номер заявки', 'Версия', 'Статус', 'Имя файла'),
        [(app, label(source), label(status), name) for app, source, status, name in mart.con.execute("""SELECT DISTINCT
            APP_ID, SOURCE, STATUS, NAME FROM quality WHERE STATUS <> 'parsed' ORDER BY APP_ID, SOURCE, STATUS, NAME""").fetchall()])
    yield Section('Изменения полей', ('Поле', 'Изменение', 'Заявок', 'Строк'),
        [(path, label(change), count, rows) for path, change, count, rows in mart.con.execute("""SELECT PATH,
            CHANGE_TYPE, count(DISTINCT APP_ID) CNT, count(*) FROM comparison WHERE DIFF_FLAG=1
            GROUP BY ALL ORDER BY CNT DESC, PATH, CHANGE_TYPE""").fetchall()],
        'Количество заявок считается отдельно для каждого поля. Совпадение изменений не доказывает причину.')
    yield Section('Скоркарты', ('Модель', 'Продукт', 'Версия', 'Заявок', 'Мин. Score', 'Средний Score',
                               'Макс. Score', 'Мин. порог', 'Макс. порог'),
        [(model, product, label(source), *stats) for model, product, source, *stats in mart.con.execute("""SELECT
            MODEL, PRODUCT, SOURCE, count(DISTINCT APP_ID), min(SCORE), avg(SCORE), max(SCORE),
            min(CUTOFF), max(CUTOFF) FROM products GROUP BY ALL ORDER BY MODEL, PRODUCT, SOURCE""").fetchall()])
    yield from check_sections(saved_checks)
    buckets = report_buckets(mart, saved_checks)
    yield Section('Бакеты', ('Вид', 'Поле', 'Версия', 'Категория или переход', 'Строк', 'База', 'Доля'),
        (('Переход' if r['LABEL'] == 'BUCKET_TRANSITION' else 'Распределение',
          r.get('PATH'), label(r.get('SOURCE')), r.get('ARGUMENT'), r.get('CNT'), r.get('TOTAL'), r.get('METRIC'))
         for r in buckets), 'База и доля рассчитаны по строкам внутри одного поля.', (6,))
    if pivot:
        dims, filters, rows = pivot
        yield Section('Сводная', (*dims, 'Уникальных заявок'), ([r.get(k) for k in (*dims, 'CNT')] for r in rows),
            'Заявка может входить в несколько групп. Условия сводной указаны в параметрах.')
    yield Section('Детали изменений', ('Номер заявки', 'Поле', 'Ключ', 'Изменение', 'Значение до', 'Значение после'),
        ((app, path, key, label(change), before, after) for app, path, key, change, before, after in
         query_batches(mart, '''SELECT APP_ID, PATH, KEY_JSON, CHANGE_TYPE, VALUE_BEFORE, VALUE_AFTER
             FROM comparison WHERE DIFF_FLAG=1 ORDER BY APP_ID, PATH, KEY_JSON''')),
        'Здесь перечислены изменившиеся поля валидных пар. Пустое значение может означать отсутствие поля.')
    options = scope.options.model_dump(mode='json', exclude_defaults=True)
    yield Section('Параметры', ('Параметр', 'Значение'), (
        ('Набор данных', run.snapshot.get('dataset_name') or 'Набор данных'),
        ('Сравнение создано', datetime.fromtimestamp(run.created, timezone.utc).strftime('%d.%m.%Y %H:%M UTC')),
        ('Код сравнения', run.id),
        ('Решения', 'Сценарные' if scope.options.basis == 'scenario' else 'Исторические'),
        ('Условия выборки', json.dumps(options, ensure_ascii=False) if options else 'Весь набор'),
        ('Условия сводной', json.dumps(fact_filters, ensure_ascii=False) if fact_filters else 'Без дополнительных условий'),
    ))


def is_bucket(row):
    return row.get('LABEL') in ('BUCKET_DISTRIBUTION', 'BUCKET_TRANSITION')


def query_batches(mart, sql):
    cursor = mart.con.execute(sql)
    while batch := cursor.fetchmany(2048):
        yield from batch


def check_values(row):
    return (row.get('LABEL'), label(row.get('SOURCE')), row.get('ARGUMENT'), row.get('CNT'),
            row.get('TOTAL'), row.get('METRIC'), label(row.get('RESULT')), label(row.get('CONFIDENCE')),
            ', '.join(map(str, row.get('APP_IDS', []))))


def check_sections(saved_checks, report_url=None):
    def rows():
        for index, row in enumerate(saved_checks):
            if row.get('RESULT') not in ('PASS', 'FAIL') or is_bucket(row):
                continue
            values = check_values(row)
            if report_url and row.get('APP_IDS'):
                link = ReportLink(values[-1], f'{report_url}&check={index}')
                yield (*values[:-1], link)
            else:
                yield values

    yield Section('Проверки прогона', CHECK_COLUMNS, rows(),
        'Результаты сохранены при публикации исходного прогона и относятся ко всему набору. В таблице приведены идентификаторы, которые вернула проверка.', (5,))
    yield Section('Ошибки проверок', ('Проверка', 'Сообщение'),
        ((r.get('LABEL'), r.get('ERROR')) for r in saved_checks if r.get('RESULT') == 'ERROR'))


def checks_page_url(base_url, runid):
    return base_url + '/?' + urlencode({'run': runid, 'mode': 'advanced', 'tab': 'checks'})


def write_xml(stream, mart, run, facts, saved_checks, fact_filters):
    def item(tag, row):
        root = Element(tag)
        for key, value in row.items():
            node = SubElement(root, key)
            if isinstance(value, list):
                for ident in value:
                    SubElement(node, 'APP_ID').text = clean(ident)
            elif value is not None:
                node.text = clean(value)
        stream.write(tostring(root, encoding='utf-8'))
        stream.write(b'\n')

    def clean(value):
        return ILLEGAL_CHARACTERS_RE.sub('', str(value)).replace('\uffff', '').replace('\ufffe', '')

    stream.write(b'<?xml version="1.0" encoding="UTF-8"?>\n<scorifyReport version="1">\n')
    item('run', {'ID': run.id, 'DATASET': run.snapshot.get('dataset_name'),
                 'ANALYSIS': mart.analysis.options.model_dump_json(exclude_defaults=True),
                 'FACT_FILTERS': json.dumps(fact_filters, ensure_ascii=False)})
    item('summary', {key.upper(): value for key, value in mart.analysis.summary().items()})
    stream.write(b'<facts unit="applications">\n')
    for fact in facts:
        item('fact', fact_values(fact))
    stream.write(b'</facts>\n<checks unit="applications" scope="published_run">\n')
    for row in check_results(saved_checks):
        item('check', {k: row.get(k) for k in CHECK_FIELDS})
    stream.write(b'</checks>\n<statistics unit="attribute_rows">\n')
    bucket_fields = ('LABEL', 'PATH', 'SOURCE', 'ARGUMENT', 'CNT', 'TOTAL', 'METRIC',
                     'BUCKET_KEY', 'BUCKET_KEY_BEFORE', 'BUCKET_KEY_AFTER', 'APP_IDS')
    for row in report_buckets(mart, saved_checks):
        item('bucket', {k: row[k] for k in bucket_fields if k in row})
    stream.write(b'</statistics>\n<checkErrors>\n')
    for row in saved_checks:
        if row.get('RESULT') == 'ERROR':
            item('error', {k: row.get(k) for k in ('LABEL', 'ERROR')})
    stream.write(b'</checkErrors>\n</scorifyReport>\n')


def write_excel(stream, sections):
    book = Workbook(write_only=True)
    dark = PatternFill('solid', fgColor='123D35')
    light = PatternFill('solid', fgColor='EDF6F2')
    top = Alignment(vertical='top')
    header_font = Font(name='Calibri', bold=True, color='FFFFFF')
    for section in sections:
        sheet = book.create_sheet(section.name)
        heading_row = 4 if section.bands else 3
        sheet.freeze_panes = f'A{heading_row+1}'
        sheet.sheet_view.showGridLines = False
        sheet.sheet_properties.pageSetUpPr.fitToPage = True
        sheet.print_title_rows = f'1:{heading_row}'
        sheet.page_setup.fitToWidth = 1
        sheet.page_setup.fitToHeight = 0
        last_column = get_column_letter(len(section.columns))
        for i, col in enumerate(section.columns, 1):
            wide = col in ('Значение', 'Значение до', 'Значение после', 'Поле', 'Аргумент',
                           'Категория или переход', 'Сообщение', 'Заявки из результата')
            sheet.column_dimensions[get_column_letter(i)].width = 50 if wide else max(17, min(43, len(col)+5))
        title = WriteOnlyCell(sheet, value=section.name)
        title.font = Font(name='Calibri', size=17, bold=True, color='123D35')
        sheet.append([title])
        sheet.append([section.note])
        if section.bands:
            sheet.append(section.bands)
        header = []
        for label_text in section.columns:
            cell = WriteOnlyCell(sheet, value=label_text)
            cell.fill, cell.font = dark, header_font
            header.append(cell)
        sheet.append(header)
        end = heading_row
        for end, row in enumerate(section.rows, heading_row+1):
            values = []
            for col, value in enumerate(row):
                formula = isinstance(value, Formula)
                if isinstance(value, str) and not formula:
                    value = ILLEGAL_CHARACTERS_RE.sub('', value)
                    if len(value) > 32767:
                        value = value[:32757] + ' …'
                cell = WriteOnlyCell(sheet, value=value)
                if isinstance(value, str) and not formula:
                    cell.data_type = 's'
                if col in section.percent or isinstance(value, Percent):
                    cell.number_format = '0.00%'
                cell.alignment = top
                if end % 2 == 0:
                    cell.fill = light
                values.append(cell)
            sheet.append(values)
        sheet.auto_filter.ref = f'A{heading_row}:{last_column}{end}'
    book.save(stream)


CSS = """
:root{color:#14342d;background:#eff5f2;font:16px/1.55 system-ui,sans-serif}
*{box-sizing:border-box}body{margin:0}.wrap{max-width:1180px;margin:auto;padding:24px}
header{background:#143d35;color:white;padding:26px 0 34px}
.brand{letter-spacing:.12em;font-size:.84rem;font-weight:700;color:#bde6d2}
h1{font-size:clamp(2rem,5vw,3.7rem);margin:.4rem 0;line-height:1.05;overflow-wrap:anywhere}
header p{margin:.6rem 0;color:#ddf1e7}a{color:#14654f}header a{color:#dcf7ea}
nav{display:flex;gap:8px;flex-wrap:wrap;margin:0 0 26px}
nav a{border:1px solid #c6ddd3;background:white;border-radius:24px;padding:6px 12px;text-decoration:none}
section{background:white;border:1px solid #d3e5dd;border-radius:16px;margin:0 0 16px;padding:18px 20px;scroll-margin-top:10px}
h2{margin:0 0 8px;font-size:1.32rem}.note{color:#3d6458;margin:.45rem 0 1rem}
.scroll{overflow:auto;max-height:520px;border:1px solid #dbe9e3;border-radius:8px}
table{border-collapse:separate;border-spacing:0;width:100%;font-size:.92rem;text-align:left}
th,td{padding:8px 12px;border-bottom:1px solid #dbe9e3;vertical-align:top;overflow-wrap:anywhere}
th{background:#e2f0e9;color:#143d35;position:sticky;top:0;z-index:1}tr:nth-child(even){background:#f5faf7}
td:first-child{font-weight:600}summary{cursor:pointer;color:#14654f;font-weight:600;margin:0 0 9px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(155px,1fr));gap:10px;margin:0 0 18px}
.kpi{border-radius:12px;background:#e8f6ee;padding:13px 16px}.kpi b{display:block;font-size:1.65rem}
footer{color:#3d6458} a:focus-visible,summary:focus-visible{outline:2px solid #24836b;outline-offset:2px}
@media(max-width:600px){.wrap{padding:16px}section{padding:14px}th,td{min-width:105px}}
@media print{:root{background:white}header{background:white;color:#143d35;padding:0}
.brand,header p,header a{color:#143d35}.scroll{max-height:none;overflow:visible}
nav{display:none}th{position:static}thead{display:table-header-group}tr{break-inside:avoid}}
"""


def write_html(stream, sections, run, base_url, *, checks_only=False):
    esc = html.escape

    def write(markup):
        stream.write(markup.encode('utf-8'))

    name = esc(str(run.snapshot.get('dataset_name') or 'Сравнение'))
    link = esc(base_url + '/?' + urlencode({'run': run.id, 'mode': 'advanced', 'tab': 'pivot'}), quote=True)
    checks_url = esc(checks_page_url(base_url, run.id), quote=True)
    title = 'ОТЧЁТ ПРОВЕРОК' if checks_only else 'ОТЧЁТ СРАВНЕНИЯ'
    sections = list(sections)
    links = ''.join(f'<a href="#section-{i}">{esc(s.name)}</a>' for i, s in enumerate(sections))
    write(f'<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
          f'<title>Скорифай · {name}</title><style>{CSS}</style></head><body><header><div class="wrap">'
          f'<div class="brand">СКОРИФАЙ · {title}</div><h1>{name}</h1>'
          f'<p><a href="{link}">Открыть сводную таблицу</a> · '
          f'<a href="{checks_url}">Открыть отчёт проверок в Скорифае</a></p>'
          f'</div></header><main class="wrap"><nav aria-label="Содержание">{links}</nav>')
    for i, section in enumerate(sections):
        write(f'<section id="section-{i}"><h2>{esc(section.name)}</h2>')
        if section.note:
            write(f'<p class="note">{esc(section.note)}</p>')
        if section.name == 'Обзор':
            kpis = ''.join(f'<div class="kpi"><b>{value:,}</b>{esc(title)}</div>'.replace(',', '&#8239;')
                           for title, value in section.rows[:4])
            write(f'<div class="kpis">{kpis}</div>')
        collapsed = section.name in ('Заявки', 'Бакеты', 'Ошибки и исключения', 'Сводная', 'Сводная таблица', 'Детали изменений')
        if collapsed:
            write('<details><summary>Открыть таблицу</summary>')
        headers = ''.join(f'<th scope="col">{esc(column)}</th>' for column in section.columns)
        write(f'<div class="scroll"><table><thead><tr>{headers}</tr></thead><tbody>')
        has_rows = False
        for row in section.rows:
            has_rows = True
            cells = []
            for j, value in enumerate(row):
                if isinstance(value, ReportLink):
                    cells.append(f'<a href="{esc(value.href, quote=True)}">{esc(value.text)}</a>')
                elif isinstance(value, Percent) or j in section.percent and isinstance(value, (int, float)):
                    cells.append(f'{value:.2%}')
                else:
                    cells.append(esc(str(value)) if value is not None and value != '' else '—')
            write('<tr>' + ''.join(f'<td>{cell}</td>' for cell in cells) + '</tr>')
        if not has_rows:
            write(f'<tr><td colspan="{len(section.columns)}">Нет строк для этой выборки</td></tr>')
        write('</tbody></table></div>')
        if collapsed:
            write('</details>')
        write('</section>')
    write('</main></body></html>')
