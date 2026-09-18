
import logging
import re

import requests

_logger = logging.getLogger(__name__)

_ENDPOINT = 'https://api.igi.org/ReportDetail.php'
_TIMEOUT = 15  # seconds
_HEADER_KEY = 'augmont'
_CONFIG_PARAM = 'igi.header_token'

_KEY_MAP = {
    'REPORT NUMBER':   'report_number',
    'REPORT DATE':     'report_date',
    'DESCRIPTION':     'description',
    'SHAPE AND CUT':   'shape',
    'CARAT WEIGHT':    'carat',
    'COLOR GRADE':     'color',
    'CLARITY GRADE':   'clarity',
    'CUT GRADE':       'cut',
    'POLISH':          'polish',
    'SYMMETRY':        'symmetry',
    'FLUORESCENCE':    'fluorescence',
    'Measurements':    'measurements',
    'Table Size':      'table_pct',
    'Total Depth':     'depth_pct',
    'Crown Height':    'crown_height',
    'Pavilion Depth':  'pavilion_depth',
    'Girdle Thickness':'girdle_thickness',
    'Culet':           'culet',
    'COMMENTS':        'comments',
}

_MEAS_XYZ = re.compile(r'([\d.]+)\s*x\s*([\d.]+)\s*x\s*([\d.]+)', re.IGNORECASE)
_MEAS_RANGE = re.compile(r'([\d.]+)\s*-\s*([\d.]+)\s*x\s*([\d.]+)', re.IGNORECASE)


def fetch_by_report_number(env, report_no):
    if not report_no:
        return None
    token = env['ir.config_parameter'].sudo().get_param(_CONFIG_PARAM)
    if not token:
        _logger.warning("IGI: missing ir.config_parameter '%s'", _CONFIG_PARAM)
        return {'error': 'unavailable'}

    try:
        resp = requests.get(
            _ENDPOINT,
            params={'Printno': str(report_no).strip()},
            headers={_HEADER_KEY: token, 'Accept': 'application/json'},
            timeout=_TIMEOUT,
            allow_redirects=True,
        )
    except requests.RequestException as exc:
        _logger.warning("IGI: transport error for %s: %s", report_no, exc)
        return {'error': 'unavailable'}

    if resp.status_code == 400:
        return {'error': 'not_found'}
    if resp.status_code != 200:
        _logger.warning("IGI: HTTP %s for %s; head=%r",
                        resp.status_code, report_no, resp.text[:200])
        return {'error': 'unavailable'}

    body = resp.text or ''
    if '<b>' in body[:100] or '<html' in body[:100].lower():
        _logger.warning("IGI: non-JSON body for %s; head=%r", report_no, body[:200])
        return {'error': 'unavailable'}

    try:
        payload = resp.json()
    except ValueError:
        _logger.warning("IGI: JSON decode failed for %s; head=%r", report_no, body[:200])
        return {'error': 'unavailable'}

    if not payload or not isinstance(payload, list):
        return {'error': 'not_found'}

    return _normalise(payload[0])


def _normalise(raw):
    out = {'raw': raw}
    for igi_key, py_key in _KEY_MAP.items():
        val = raw.get(igi_key)
        if val is not None and val != '':
            out[py_key] = val

    out['carat_value'] = _parse_carat(out.get('carat'))
    out['clarity_norm'] = _norm_clarity(out.get('clarity'))
    out['table_pct_value'] = _parse_pct(out.get('table_pct'))
    out['depth_pct_value'] = _parse_pct(out.get('depth_pct'))

    l, w, d = _parse_measurements(out.get('measurements'))
    out['length_mm'] = l
    out['width_mm'] = w
    out['depth_mm'] = d

    out['is_lab_grown'] = _is_lab_grown(out.get('description'),
                                        out.get('report_number'))
    out['is_fancy_color'] = bool(
        out.get('color') and out['color'].strip().upper().startswith('FANCY')
    )
    # Growth treatment (CVD / HPHT / None) parsed from the report comments,
    # and fluorescence split into intensity + colour (IGI returns one string,
    # e.g. "MEDIUM BLUE"). All default to "None" so downstream columns never render blank.
    out['treatment'] = _derive_treatment(out)
    out['fluorescence_intensity'], out['fluorescence_color'] = \
        _split_fluorescence(out.get('fluorescence'))
    return out


def _parse_carat(val):
    if not val:
        return None
    m = re.search(r'([\d.]+)', val)
    return float(m.group(1)) if m else None


def _parse_pct(val):
    if not val:
        return None
    m = re.search(r'([\d.]+)', val)
    return float(m.group(1)) if m else None


def _norm_clarity(val):
    return val.replace(' ', '') if val else val


def _parse_measurements(val):
    if not val:
        return None, None, None
    m = _MEAS_XYZ.search(val)
    if m:
        return float(m.group(1)), float(m.group(2)), float(m.group(3))
    m = _MEAS_RANGE.search(val)
    if m:
        return float(m.group(1)), float(m.group(2)), float(m.group(3))
    return None, None, None


def _is_lab_grown(description, report_number):
    if description and 'LABORATORY GROWN' in description.upper():
        return True
    if report_number and str(report_number).upper().startswith('LG'):
        return True
    return False


_FLUOR_INTENSITIES = ('VERY STRONG', 'STRONG', 'MEDIUM', 'FAINT', 'NONE')
_FLUOR_COLORS = ('BLUE', 'YELLOW', 'GREEN', 'ORANGE', 'RED', 'WHITE', 'VIOLET', 'PINK')

def _derive_treatment(out):
    """CVD / HPHT / None, parsed from the report comments + description."""
    text = ' '.join(str(out.get(k) or '')
                    for k in ('comments', 'description')).upper()
    if 'CVD' in text or 'CHEMICAL VAPOR' in text or 'CHEMICAL VAPOUR' in text:
        return 'CVD'
    if 'HPHT' in text or 'HIGH PRESSURE HIGH TEMPERATURE' in text:
        return 'HPHT'
    return 'None'

def _split_fluorescence(raw):
    """Split an IGI fluorescence string (e.g. "MEDIUM BLUE") into
    (intensity, colour). Both fall back to "None"."""
    text = (raw or '').strip().upper()
    if not text:
        return 'None', 'None'
    intensity = next((i for i in _FLUOR_INTENSITIES if i in text), None)
    colour = next((c for c in _FLUOR_COLORS if c in text), None)
    return (intensity.title() if intensity else 'None',
            colour.title() if colour else 'None')
