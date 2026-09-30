"""Local input cleanup and conservative prompt parameter recognition; no API calls."""
import re


def parse_keys(text):
    # Copying from web pages can introduce invisible formatting characters.
    text = re.sub('[\u200b\u200c\u200d\u2060\ufeff]', '', text)
    text = re.sub(r'(?im)(?:authorization\s*:\s*)?\bbearer\s+', '', text)
    text = re.sub(r'(?im)(?:api[_ -]?key|powertokens_api_key)\s*[:=]\s*', '', text)
    tokens = [t.strip('\'"`“”‘’') for t in re.split(r'[\s,;，；]+', text.strip()) if t]
    tokens = [t for t in tokens if t]
    if not tokens:
        raise ValueError('请粘贴 API Key。')
    if any(not re.fullmatch(r'[A-Za-z0-9._~+/=-]+', t) for t in tokens):
        raise ValueError('输入包含说明文字或不支持的符号。请只复制完整 Key；也支持 Bearer Key。')
    return list(dict.fromkeys(tokens))


def number(text):
    if re.fullmatch(r'\d+(?:\.\d+)?', text):
        return float(text)
    digits = dict(zip('零一二三四五六七八九', range(10)))
    text = text.replace('两', '二')
    if text == '半':
        return .5
    if '百' in text:
        first, rest = text.split('百', 1)
        return digits.get(first, 1) * 100 + (number(rest.lstrip('零')) if rest.lstrip('零') else 0)
    if '十' in text:
        first, last = text.split('十', 1)
        return (digits.get(first, 1) * 10) + digits.get(last, 0)
    return digits.get(text, 0)


MIN_DURATION, MAX_DURATION = 2, 30  # Wan 3.0 API limits (whole seconds).


def seconds(match, group=1):
    value = number(match.group(group))
    return value * 60 if match.group(match.re.groups).startswith(('分', 'min')) else value


def infer_prompt(text):
    """Return duration/ratio, blocking warnings and informational notes. Leave the prompt intact.

    Duration priority:
      1. Declared totals: 总时长/时长/duration/total N 秒, or "生成一段 N 秒".
      2. A duration at the very start of the prompt, e.g. "16秒，16:9横屏。…".
      3. Other standalone durations (not part of an "a-b秒" range).
      4. The end of a shot timeline such as "0-5秒 … 5-10秒 … 10-16秒".
    Values are clamped to 2–30 s with a note. Conflicting totals are refused.
    """
    result = {'duration': None, 'ratio': None, 'warnings': [], 'notes': [], 'duration_source': None}
    text = text.lower().replace('：', ':')
    quantity = r'(\d+(?:\.\d+)?|[零一二两三四五六七八九十百半]+)'
    unit = r'\s*[-－]?\s*(秒钟?|seconds?(?![a-z])|secs?(?![a-z])|s(?![a-z])|分钟|minutes?(?![a-z])|mins?(?![a-z]))'
    duration = re.compile(quantity + unit)
    range_re = re.compile(quantity + r'\s*(?:[-–—~～至到]|to)\s*' + quantity + unit)
    total_re = re.compile(r'(?:总时长|全片时长|视频时长|时长|total\s+(?:duration|length)|total|duration|length)'
                          r'\s*(?:of\s+)?[:=为约是]?\s*' + quantity + unit)
    intro_re = re.compile(r'(?:生成|制作|创作|拍摄|做)(?:一个|一段|一条|个|段|条)?\s*' + quantity + unit)
    lead_re = re.compile(r'\s*' + quantity + unit)
    ranges = list(range_re.finditer(text))

    def in_range(match):
        return any(r.start() <= match.start() < r.end() for r in ranges)

    totals = [m for m in total_re.finditer(text) if not in_range(m)]
    intros = [m for m in intro_re.finditer(text) if not in_range(m)]
    lead = lead_re.match(text)
    if lead and in_range(lead):
        lead = None
    standalone = [m for m in duration.finditer(text) if not in_range(m)]
    spans = [(number(r.group(1)) * (60 if r.group(3).startswith(('分', 'min')) else 1), seconds(r, 2))
             for r in ranges]
    # A shot timeline starts at 0 or chains segments (end of one == start of the next).
    # A lone "5-10秒" reads as a duration range and stays ambiguous.
    starts = {a for a, _ in spans}
    timeline = bool(spans) and (0 in starts or any(b in starts for _, b in spans))
    timeline_end = max((b for _, b in spans), default=None)

    duration_warning = None
    values, source = set(), None
    declared = totals or intros  # A declared total outranks shot timings and a leading number.
    if declared:
        values, source = {seconds(m) for m in declared}, 'declared'
    elif lead:
        value = seconds(lead)
        values, source = {value}, 'lead'
        # A later standalone duration longer than the leading total contradicts it.
        if any(seconds(m) > value for m in standalone):
            values = {value} | {seconds(m) for m in standalone}
    elif standalone:
        values, source = {seconds(m) for m in standalone}, 'standalone'
        if ranges and not (timeline and len(values) == 1 and min(values) >= timeline_end):
            duration_warning = '检测到分镜时间段和其他时长，请写明“总时长 16 秒”，或关闭自动识别后手动设置。'
    elif timeline:
        values, source = {timeline_end}, 'timeline'
    elif ranges:
        duration_warning = '检测到时长范围，请写明“总时长 10 秒”，或关闭自动识别后手动设置。'

    if duration_warning:
        pass
    elif len(values) > 1:
        duration_warning = '检测到多个时长，请写明“总时长 20 秒”，或关闭自动识别后手动选择。'
    elif values:
        value = values.pop()
        if value != int(value):
            duration_warning = '识别到时长 %g 秒；此工具支持 2–30 秒整数，请修改或关闭自动识别。' % value
        else:
            value = int(value)
            result['duration'] = min(MAX_DURATION, max(MIN_DURATION, value))
            result['duration_source'] = source
            if source == 'timeline':
                result['notes'].append('时长根据分镜时间轴推断（最后一段结束于 %d 秒）' % value)
            if result['duration'] != value:
                result['notes'].append('识别到 %d 秒，超出 2–30 秒范围，已按 %d 秒设置' % (value, result['duration']))
    result['field_warnings'] = {}
    if duration_warning:
        result['warnings'].append(duration_warning)
        result['field_warnings']['duration'] = duration_warning
    ratios = set()
    for match in re.finditer(r'(?<![\d.])(16\s*[:/x×]\s*9|9\s*[:/x×]\s*16|1\s*[:/x×]\s*1)(?!\d)', text):
        ratios.add(re.sub(r'\s*[:/x×]\s*', ':', match.group(1)))
    for pattern, value in [(r'竖屏|竖版|纵向|\bportrait\b|\bvertical\b', '9:16'),
                           (r'横屏|横版|横向|\blandscape\b|\bhorizontal\b', '16:9'),
                           (r'方形|正方形|\bsquare\b', '1:1')]:
        if re.search(pattern, text):
            ratios.add(value)
    unsupported = re.search(r'(?:比例|aspect\s*ratio|ratio)\s*[:=为是]?\s*(\d+)\s*[:/x×]\s*(\d+)', text)
    if unsupported and ':'.join(unsupported.groups()) not in ('16:9', '9:16', '1:1'):
        result['warnings'].append('识别到不支持的比例，请选择 16:9、9:16 或 1:1。')
    elif len(ratios) > 1:
        result['warnings'].append('检测到不同画面比例，请保留一种，或关闭自动识别后手动选择。')
    elif ratios:
        result['ratio'] = ratios.pop()
    if len(result['warnings']) > (1 if duration_warning else 0):
        result['field_warnings']['ratio'] = result['warnings'][-1]
    return result
