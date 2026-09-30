"""Read plain XLSX/CSV tables without Excel or third-party dependencies."""
import csv
import hashlib
import io
import json
from pathlib import Path
import posixpath
import re
import xml.etree.ElementTree as ET
import zipfile
from input_helpers import infer_prompt
from wan_core import TaskError, estimate_cost, payload

NS = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
REL = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id'


def read_xlsx(path):
    with zipfile.ZipFile(path) as z:
        if sum(i.file_size for i in z.infolist()) > 128 * 1024 * 1024:
            raise ValueError('表格过大，请拆分为较小的批次。')
        def xml(name):
            return ET.fromstring(z.read(name))
        strings = []
        if 'xl/sharedStrings.xml' in z.namelist():
            strings = [''.join(t.text or '' for t in si.findall('.//s:t', NS))
                       for si in xml('xl/sharedStrings.xml').findall('s:si', NS)]
        links = {r.attrib['Id']: r.attrib['Target'] for r in xml('xl/_rels/workbook.xml.rels')
                 if r.attrib.get('TargetMode') != 'External'}
        for sheet in xml('xl/workbook.xml').findall('s:sheets/s:sheet', NS):
            target = links.get(sheet.attrib[REL])
            if not target:
                continue
            name = target.lstrip('/') if target.startswith('/') else posixpath.normpath('xl/' + target)
            rows = []
            for index, row in enumerate(xml(name).findall('s:sheetData/s:row', NS), 1):
                values = {}
                for c in row.findall('s:c', NS):
                    ref = c.attrib.get('r', '')
                    letters = re.match('[A-Z]+', ref)
                    if not letters:
                        continue
                    column = 0
                    for char in letters.group():
                        column = column * 26 + ord(char) - 64
                    if column > 256:
                        continue
                    v = c.find('s:v', NS)
                    value = v.text if v is not None and v.text else ''
                    if c.find('s:f', NS) is not None:
                        value = '=FORMULA_UNSUPPORTED'
                    elif c.attrib.get('t') == 's':
                        value = strings[int(value)] if value else ''
                    elif c.attrib.get('t') == 'inlineStr':
                        value = ''.join(t.text or '' for t in c.findall('.//s:t', NS))
                    values[column] = value
                if values:
                    rows.append((int(row.attrib.get('r', index)), [values.get(i, '') for i in range(1, max(values) + 1)]))
            yield sheet.attrib['name'], rows


def read_tables(path):
    path = Path(path)
    if path.suffix.lower() == '.xlsx':
        return list(read_xlsx(path))
    if path.suffix.lower() == '.csv':
        raw = path.read_bytes()
        try:
            text = raw.decode('utf-8-sig')
        except UnicodeDecodeError:
            text = raw.decode('gb18030')
        return [(path.stem, list(enumerate(csv.reader(io.StringIO(text)), 1)))]
    raise ValueError('请选择 .xlsx 或 .csv 文件（不支持旧版 .xls）。')


def normalize_header(value):
    return re.sub(r'[\s_()（）-]', '', str(value)).lower()


def header_kind(value):
    value = normalize_header(value)
    if value == 'prompt' or ('prompt' in value and ('完整' in value or 'wan' in value)) or value in ('提示词', '完整提示词', '视频提示词'):
        return 'prompt'
    if value in ('时长', '时长秒', '视频时长', '视频时长秒', 'duration', 'durations', 'seconds'):
        return 'duration'
    if value in ('分辨率', 'resolution', 'size'):
        return 'resolution'
    if value in ('比例', '画面比例', 'ratio', 'aspectratio'):
        return 'ratio'
    if value in ('名称', '标题', '视频标题', 'title', 'name'):
        return 'title'
    if value in ('编号', '序号', 'id'):
        return 'id'
    return None


def build_job(sheet, row_number, columns, cells, character_setting=''):
    def get(kind):
        index = columns.get(kind)
        return str(cells[index]).strip() if index is not None and index < len(cells) else ''
    source_prompt = get('prompt')
    character_setting = character_setting.strip()
    prompt = ('【全剧固定人物设定】\n%s\n\n【本集剧情】\n%s' %
              (character_setting, source_prompt)) if character_setting and source_prompt else source_prompt
    title = get('title') or get('id') or '%s_第%d行' % (sheet, row_number)
    job = dict(id=hashlib.sha256(('%s:%s' % (sheet, row_number)).encode()).hexdigest()[:16],
               sheet=sheet, row=row_number, title=title, prompt=prompt, character_setting=character_setting,
               state='queued', note='', record=None)
    try:
        if not prompt:
            raise ValueError('缺少 prompt')
        if any(get(k).startswith('=') for k in columns):
            raise ValueError('生成参数不支持公式，请先在 Excel 中粘贴为值。')
        detected = infer_prompt(source_prompt)
        duration_text = get('duration')
        if duration_text:
            raw = re.sub(r'\s*(秒|s|seconds?)\s*$', '', duration_text, flags=re.I)
            numeric = float(raw)
            if not numeric.is_integer():
                raise ValueError('时长必须是整数秒')
            duration = int(numeric)
        else:
            if 'duration' in detected['field_warnings']:
                raise ValueError(detected['field_warnings']['duration'])
            duration = detected['duration'] or 5
        ratio = get('ratio').replace('：', ':').replace(' ', '')
        if not ratio:
            if 'ratio' in detected['field_warnings']:
                raise ValueError(detected['field_warnings']['ratio'])
            ratio = detected['ratio'] or '16:9'
        resolution = get('resolution').lower()
        if not resolution:
            matches = set(re.findall(r'(?<!\d)(720p|1080p)(?![a-z0-9])', source_prompt.lower()))
            if len(matches) > 1:
                raise ValueError('prompt 中分辨率不明确，请填写分辨率列')
            resolution = next(iter(matches), '720p')
        if resolution in ('720', '1080'):
            resolution += 'p'
        job['payload'] = payload(prompt, duration, resolution, ratio)
        job['cost'] = estimate_cost(duration, resolution)
        notes = [] if duration_text else detected.get('notes', [])
        job['note'] = '；'.join(['表格参数优先；缺省值：5秒 / 720p / 16:9'] + notes)
    except (ValueError, TaskError, OverflowError) as exc:
        job.update(state='invalid', note=str(exc), payload=None, cost=0)
    return job


def import_jobs(path, character_setting=''):
    jobs, skipped = [], []
    for sheet, rows in read_tables(path):
        header_index, columns = None, {}
        for index, (_, cells) in enumerate(rows[:20]):
            found = {header_kind(value): i for i, value in enumerate(cells) if header_kind(value)}
            if 'prompt' in found:
                header_index, columns = index, found
                break
        if header_index is None:
            skipped.append(sheet)
            continue
        for row_number, cells in rows[header_index + 1:]:
            if not any(str(v).strip() for v in cells):
                continue
            jobs.append(build_job(sheet, row_number, columns, cells, character_setting))
    if not jobs:
        raise ValueError('未找到任务。需要包含“完整 Wan 3.0 Prompt”或“提示词/Prompt”表头。')
    if len(jobs) > 2000:
        raise ValueError('每批最多 2000 行，请拆分文件。')
    return jobs, skipped


def batch_identity(jobs):
    stable = [(j['sheet'], j['row'], j['prompt'], j['payload']) for j in jobs]
    return hashlib.sha256(json.dumps(stable, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:24]
