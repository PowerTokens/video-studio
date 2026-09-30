"""Local input cleanup and conservative prompt parameter recognition; no API calls."""
import re

from i18n import t


def parse_keys(text):
    # Copying from web pages can introduce invisible formatting characters.
    text = re.sub('[\u200b\u200c\u200d\u2060\ufeff]', '', text)
    text = re.sub(r'(?im)(?:authorization\s*:\s*)?\bbearer\s+', '', text)
    text = re.sub(r'(?im)(?:api[_ -]?key|powertokens_api_key)\s*[:=]\s*', '', text)
    tokens = [t.strip('\'"`“”‘’') for t in re.split(r'[\s,;，；]+', text.strip()) if t]
    tokens = [t for t in tokens if t]
    if not tokens:
        raise ValueError(t('paste_key'))
    if any(not re.fullmatch(r'[A-Za-z0-9._~+/=-]+', t) for t in tokens):
        raise ValueError(t('key_invalid_chars'))
    return list(dict.fromkeys(tokens))


EN_NUMBERS = {word: value for value, word in enumerate(
    'zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen '
    'sixteen seventeen eighteen nineteen twenty'.split())}
EN_NUMBERS['thirty'] = 30


def number(text):
    if re.fullmatch(r'\d+(?:\.\d+)?', text):
        return float(text)
    if text in EN_NUMBERS:
        return EN_NUMBERS[text]
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


# Timecodes such as 00:03, 0:03 or 01:00:03 (optional fraction). "16:9" / "9:16" are not timecodes
# on their own; only a pair joined by a range separator counts ("00:00-00:03").
TIMECODE = r'(?<![\d:.])(?:(\d{1,2}):)?(\d{1,2}):(\d{2})(?:\.\d+)?(?![\d:])'
RANGE_SEPARATOR = r'\s*(?:[-–—~～至到]|to)\s*'
TIMECODE_RANGE = re.compile(TIMECODE + RANGE_SEPARATOR + TIMECODE)
# "1980s", "the 80s", "'90s", "80s-style": decades, not durations.
DECADE = re.compile(r"(?:['’]\d0s|(?<![\d.])(?:1\d|20)\d0s|\b(?:the|early|mid|late)[\s-]+\d0s)(?![a-z])"
                    r"|\b\d0s(?=[\s-]+(?:style|era|vibes?|look|aesthetic|music|fashion|retro|film|movie|tv|sitcom|disco|pop|rock)\b)")


def timecode_seconds(hours, minutes, secs):
    return int(hours or 0) * 3600 + int(minutes) * 60 + int(secs)


def infer_prompt(text):
    """Return duration/ratio, blocking warnings and informational notes. Leave the prompt intact.

    Duration priority:
      1. Declared totals: 总时长/时长/duration/total length N 秒/s, "生成一段 N 秒", "make a 10s video",
         "15 seconds total".
      2. A duration at the very start of the prompt, e.g. "16秒，16:9横屏。…" or "16s, 16:9 …".
      3. Other standalone durations (not part of an "a-b秒" / "0-3s" range).
      4. The end of a shot timeline such as "0-5秒 … 10-16秒", "0s-3s … 3s-8s" or "00:00-00:03 …".
    Values are clamped to 2–30 s with a note. Conflicting totals are refused.
    """
    result = {'duration': None, 'ratio': None, 'warnings': [], 'notes': [], 'duration_source': None}
    text = text.lower().replace('：', ':')
    timecode_spans = []
    for match in TIMECODE_RANGE.finditer(text):
        groups = match.groups()
        timecode_spans.append((timecode_seconds(*groups[:3]), timecode_seconds(*groups[3:])))
    # Blank out timecode ranges and decades (same length, so positions stay valid).
    text = TIMECODE_RANGE.sub(lambda m: ' ' * len(m.group()), text)
    text = DECADE.sub(lambda m: ' ' * len(m.group()), text)
    quantity = (r'(\d+(?:\.\d+)?|[零一二两三四五六七八九十百半]+|\b(?:' + '|'.join(EN_NUMBERS) + r')\b)')
    unit_words = r'(秒钟?|seconds?(?![a-z])|secs?(?![a-z])|s(?![a-z])|分钟|minutes?(?![a-z])|mins?(?![a-z]))'
    unit = r'\s*[-－]?\s*' + unit_words
    duration = re.compile(quantity + unit)
    # "0-3s", "0s-3s", "0 to 3 seconds", "5到10秒".
    range_re = re.compile(quantity + r'(?:\s*' + unit_words.replace('(', '(?:', 1) + r')?' + RANGE_SEPARATOR + quantity + unit)
    about = r'(?:(?:about|around|approximately|approx\.?|roughly)\s*)?'
    total_re = re.compile(r'(?:总时长|全片时长|视频时长|时长|total\s+(?:duration|length|runtime|running\s+time|time)'
                          r'|total|duration|length|runtime|running\s+time)'
                          r'\s*(?:of\s+|is\s+)?[:=为约是~]?\s*' + about + quantity + unit)
    total_suffix_re = re.compile(quantity + unit + r'\s+(?:in\s+)?(?:total|overall|long)\b')
    intro_re = re.compile(r'(?:生成|制作|创作|拍摄|做)(?:一个|一段|一条|个|段|条)?\s*' + quantity + unit)
    intro_en_re = re.compile(r'\b(?:generate|create|make|produce|shoot|film|render)\s+(?:(?:a|an|one)\s+)?' + quantity + unit)
    lead_re = re.compile(r'\s*' + quantity + unit)
    ranges = list(range_re.finditer(text))

    def in_range(match):
        return any(r.start() <= match.start() < r.end() for r in ranges)

    totals = [m for m in total_re.finditer(text) if not in_range(m)]
    totals += [m for m in total_suffix_re.finditer(text) if not in_range(m)]
    intros = [m for pattern in (intro_re, intro_en_re) for m in pattern.finditer(text) if not in_range(m)]
    lead = lead_re.match(text)
    if lead and in_range(lead):
        lead = None
    standalone = [m for m in duration.finditer(text) if not in_range(m)]
    spans = [(number(r.group(1)) * (60 if r.group(3).startswith(('分', 'min')) else 1), seconds(r, 2))
             for r in ranges] + timecode_spans
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
        if spans and not (timeline and len(values) == 1 and min(values) >= timeline_end):
            duration_warning = t('warn_timeline_and_other')
    elif timeline:
        values, source = {timeline_end}, 'timeline'
    elif spans:
        duration_warning = t('warn_range')

    if duration_warning:
        pass
    elif len(values) > 1:
        duration_warning = t('warn_multiple')
    elif values:
        value = values.pop()
        if value != int(value):
            duration_warning = t('warn_fraction', value)
        else:
            value = int(value)
            result['duration'] = min(MAX_DURATION, max(MIN_DURATION, value))
            result['duration_source'] = source
            if source == 'timeline':
                result['notes'].append(t('note_timeline', value))
            if result['duration'] != value:
                result['notes'].append(t('note_clamped', value, result['duration']))
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
        result['warnings'].append(t('warn_ratio_unsupported'))
    elif len(ratios) > 1:
        result['warnings'].append(t('warn_ratio_conflict'))
    elif ratios:
        result['ratio'] = ratios.pop()
    if len(result['warnings']) > (1 if duration_warning else 0):
        result['field_warnings']['ratio'] = result['warnings'][-1]
    return result
