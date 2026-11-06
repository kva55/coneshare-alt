"""Log-only detection of probing and attack requests, organised like the OWASP Core Rule Set.

The rule groups use the CRS family numbers (911 method enforcement, 913 scanners, 920 protocol,
921 protocol attacks, 930 LFI, 931 RFI, 932 RCE, 933 PHP, 934 generic, 941 XSS, 942 SQLi,
943 session fixation, 944 Java) and, where the CRS ships one, its data lists (see crs/NOTICE.md).
It is a small readable subset in Python, not ModSecurity: nothing here blocks a request.

Plain Python on purpose (no Django imports), so the rules can be tested on their own.
"""
import html
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qsl, unquote

DATA_DIR = Path(__file__).parent / 'crs'
MAX_ARGS = 100
MAX_TEXT = 2000
MAX_BODY = 8192


def _load(name, min_len=3):
    out = []
    for line in (DATA_DIR / name).read_text(encoding='utf-8', errors='ignore').splitlines():
        line = line.strip()
        if line and not line.startswith('#') and len(line) >= min_len:
            out.append(line)
    return out


SCANNER_AGENTS = [a.lower() for a in _load('scanners-user-agents.data', 4)]
RESTRICTED_FILES = [f.lower() for f in _load('restricted-files.data', 4)]
# `node_modules/` is in the CRS list but is a normal URL on the Vite dev server
OS_FILES = [f.lower() for f in _load('lfi-os-files.data', 6) if f.lower() not in ('node_modules/',)]
UNIX_SHELL = [s.lower() for s in _load('unix-shell.data', 5)]
UNIX_BUILTINS = [b.lower() for b in _load('unix-shell-builtins.data', 2)]
SSRF_TARGETS = [s.lower() for s in _load('ssrf.data', 8)]
PHP_FUNCTIONS = [f.lower() for f in _load('php-function-names-933150.data', 4)]
PHP_VARIABLES = [v for v in _load('php-variables.data', 4)]

# crs-setup.conf.example (tx.restricted_extensions / tx.restricted_headers_basic)
RESTRICTED_EXTENSIONS = frozenset(
    '.ani .asa .asax .ascx .back .backup .bak .bck .bk .bkp .bat .cdx .cer .cfg .cnf .cmd .com .compositefont '
    '.config .conf .copy .crt .cs .csproj .csr .dat .db .dbf .dist .dll .dos .dpkg-dist .drv .gadget .hta .htr '
    '.htw .ida .idc .idq .inc .inf .ini .jks .jse .key .licx .lnk .log .mdb .msc .ocx .old .pass .pdb .pfx .pif '
    '.pem .pol .prf .printer .pwd .rdb .rdp .reg .resources .resx .sav .save .scr .sct .sh .shs .sql .sqlite '
    '.sqlite3 .swap .swo .swp .sys .temp .tfstate .tlb .tmp .vb .vbe .vbs .vbproj .vsdisco .vxd .webinfo .ws '
    '.wsc .wsf .wsh .xsd .xsx'.split())
RESTRICTED_HEADERS = frozenset(
    'content-encoding lock-token content-range if x-http-method-override x-http-method x-method-override '
    'x-middleware-subrequest'.split())
SCANNER_HEADERS = frozenset(
    'acunetix-product acunetix-scanning-agreement acunetix-aspect acunetix-aspect-password '
    'acunetix-aspect-queries x-scanner x-ratproxy-loop x-wipp'.split())
ALLOWED_METHODS = frozenset('GET HEAD POST PUT PATCH DELETE OPTIONS'.split())

# Paths scanners ask for (nmap http-enum, ZAP/Nikto style forced browsing). Matched on the decoded path.
PROBE_PATHS = (
    '/phpmyadmin', '/pma/', '/myadmin', '/wp-admin', '/wp-login', '/wp-content', '/wp-includes', '/xmlrpc.php',
    '/administrator/', '/joomla', '/drupal', '/cgi-bin', '/manager/html', '/host-manager', '/solr/', '/actuator',
    '/server-status', '/server-info', '/phpinfo', '/info.php', '/test.php', '/shell.', '/webshell', '/c99', '/r57',
    '/db.sql', '/dump.sql', '/backup', '/jenkins', '/jmx-console', '/axis2', '/struts', '/hnap1', '/boaform',
    '/vendor/phpunit', '/_ignition', '/telescope', '/horizon', '/nmaplowercheck', '/evox/about', 'nice ports',
    'trinity.txt.bak', '/@fs/', 'nessus_is_probing_you_', 'acunetix', '/console/', '/.well-known/../')

CRLF = re.compile(r'[\n\r][^0-9a-z_]*?(?:content-(?:type|length)|set-cookie|location):\s*\w', re.I)


@dataclass
class Request:
    method: str
    raw_uri: str
    headers: dict = field(default_factory=dict)          # lower-case name -> value
    body_args: list = field(default_factory=list)        # [(name, value)] from a small JSON/form body
    # derived
    path_raw: str = ''
    query_raw: str = ''
    args: list = field(default_factory=list)             # [(name, value)] decoded once

    def __post_init__(self):
        self.method = (self.method or 'GET').upper()
        uri = self.raw_uri or '/'
        self.path_raw, _, self.query_raw = uri.partition('?')
        self.headers = {str(k).lower(): str(v)[:1000] for k, v in (self.headers or {}).items()}
        try:
            pairs = parse_qsl(self.query_raw, keep_blank_values=True, max_num_fields=MAX_ARGS, errors='replace')
        except ValueError:
            pairs = [(self.query_raw, '')]
        self.args = (pairs + list(self.body_args))[:MAX_ARGS]


def normalize(value: str) -> str:
    """Decode twice, drop SQL comments, collapse whitespace, lower-case (CRS: urlDecode, removeComments, lowercase)."""
    text = unquote(unquote(str(value)[:MAX_TEXT]))
    text = re.sub(r'/\*.*?\*/', ' ', text, flags=re.S)
    return re.sub(r'\s+', ' ', text).strip().lower()


def body_arguments(content_type: str, body: bytes):
    """Name/value pairs from a small JSON or form body (anything else is not inspected)."""
    if not body or len(body) > MAX_BODY:
        return []
    ctype = (content_type or '').split(';')[0].strip().lower()
    text = body.decode('utf-8', errors='ignore')
    if ctype == 'application/x-www-form-urlencoded':
        try:
            return parse_qsl(text, keep_blank_values=True, max_num_fields=MAX_ARGS)
        except ValueError:
            return []
    if ctype == 'application/json':
        try:
            data = json.loads(text)
        except ValueError:
            return []
        out = []

        def walk(node, name='json'):
            if len(out) >= MAX_ARGS:
                return
            if isinstance(node, dict):
                for key, value in node.items():
                    out.append((str(key), ''))
                    walk(value, str(key))
            elif isinstance(node, list):
                for value in node[:50]:
                    walk(value, name)
            elif isinstance(node, str):
                out.append((name, node))
        walk(data)
        return out
    return []


class _Context:
    """Request pieces prepared once for all rules."""

    def __init__(self, req: Request):
        self.req = req
        self.path = normalize(req.path_raw)
        self.basename = self.path.rsplit('/', 1)[-1]
        self.args = [(f'arg:{n[:40]}', normalize(n), normalize(v)) for n, v in req.args]
        self.user_agent = req.headers.get('user-agent', '').lower()
        # headers an attacker controls and applications often log or reflect
        self.header_values = [(f'header:{k}', normalize(v)) for k, v in req.headers.items()
                              if k in ('user-agent', 'referer', 'x-forwarded-for', 'x-forwarded-host', 'origin',
                                       'x-original-url', 'x-rewrite-url', 'accept-language')]
        self.cookie_values = [(f'cookie:{name.strip()[:30]}', normalize(value)) for name, _, value in
                              (c.partition('=') for c in req.headers.get('cookie', '').split(';')) if name.strip()]

    def injectable(self):
        """Everything a payload could sit in: path, argument names and values, some headers, cookies."""
        yield 'path', self.path
        for where, name, value in self.args:
            yield where + ':name', name
            yield where, value
        yield from self.header_values
        yield from self.cookie_values

    def arg_values(self):
        for where, _name, value in self.args:
            yield where, value

    def arg_texts(self):
        """Names as well as values: `a=1&type %SYSTEMROOT%` splits a payload into a *name*."""
        for where, name, value in self.args:
            yield where + ':name', name
            yield where, value


def _re(pattern, flags=re.I | re.S):
    return re.compile(pattern, flags)


def _first(regex, items):
    for where, text in items:
        m = regex.search(text)
        if m:
            return where, m.group(0)[:80]
    return None


def _phrase(entries, items):
    for where, text in items:
        for entry in entries:
            if entry in text:
                return where, entry
    return None


# ---------------------------------------------------------------------------- rule definitions
RULES = []
GROUPS = {}


def group(gid, name, description, default=True):
    GROUPS[gid] = {'id': gid, 'name': name, 'description': description, 'default_enabled': default}


def rule(rid, gid, description, severity='critical'):
    def wrap(fn):
        RULES.append({'id': rid, 'group': gid, 'description': description, 'severity': severity, 'fn': fn})
        return fn
    return wrap


group('911', 'Method enforcement', 'Requests using HTTP methods the application never uses')
group('913', 'Scanner detection', 'Security scanners, by user agent, headers and the paths they ask for')
group('920', 'Protocol enforcement', 'Malformed requests, restricted file types and headers')
group('921', 'Protocol attacks', 'Header injection, response splitting, request smuggling')
group('930', 'Local file inclusion', 'Path traversal and access to operating system or configuration files')
group('931', 'Remote file inclusion', 'Parameters that carry a URL to fetch')
group('932', 'Remote command execution', 'Shell commands and expressions in input, Shellshock')
group('933', 'PHP injection', 'PHP code, streams and functions (this stack is not PHP)', default=False)
group('934', 'Generic injection', 'Template and expression injection, prototype pollution, requests to internal addresses')
group('941', 'Cross-site scripting', 'Script, markup and event handlers in input')
group('942', 'SQL injection', 'SQL fragments in input')
group('943', 'Session fixation', 'Session identifiers supplied in the URL')
group('944', 'Java injection', 'Java classes and JNDI lookups (this stack is not Java)', default=False)
group('APP', 'This application', 'Requests for places that do not exist')


# 911 ---------------------------------------------------------------------------------------
@rule('method_not_allowed', '911', 'HTTP method the application does not use (TRACE, CONNECT, PROPFIND, ...)', 'warning')
def _method(ctx):
    return ('method', ctx.req.method) if ctx.req.method not in ALLOWED_METHODS else None


# 913 ---------------------------------------------------------------------------------------
@rule('scanner_user_agent', '913', 'User agent of a known security scanner (CRS list)')
def _scanner_ua(ctx):
    for agent in SCANNER_AGENTS:
        if agent in ctx.user_agent:
            return 'header:user-agent', agent
    return None


@rule('scanner_header', '913', 'Request header used by a scanner (Acunetix, Ratproxy, ...)')
def _scanner_header(ctx):
    for name in ctx.req.headers:
        if name in SCANNER_HEADERS:
            return f'header:{name}', name
    return None


@rule('known_probe_path', '913', 'Path that scanners and nmap ask for (admin panels, backups, shells)', 'warning')
def _probe_path(ctx):
    for needle in PROBE_PATHS:
        if needle in ctx.path:
            return 'path', needle
    return None


# 920 ---------------------------------------------------------------------------------------
@rule('restricted_extension', '920', 'File type that should never be requested (.bak, .sql, .log, .config, ...)', 'warning')
def _ext(ctx):
    name = ctx.basename
    if '.' in name:
        ext = '.' + name.rsplit('.', 1)[-1]
        if ext in RESTRICTED_EXTENSIONS:
            return 'path', ext
    return None


@rule('restricted_header', '920', 'Request header the application does not accept (Proxy-*, Lock-Token, method override, ...)', 'warning')
def _restricted_header(ctx):
    for name in ctx.req.headers:
        if name in RESTRICTED_HEADERS or name.startswith('proxy-'):
            return f'header:{name}', name
    return None


@rule('body_on_get', '920', 'GET or HEAD request that carries a body', 'warning')
def _body_on_get(ctx):
    length = ctx.req.headers.get('content-length', '0').strip()
    if ctx.req.method in ('GET', 'HEAD') and length not in ('', '0'):
        return 'header:content-length', length
    return None


@rule('range_abuse', '920', 'Range header with many fields (denial of service probe)', 'warning')
def _range(ctx):
    value = ctx.req.headers.get('range', '')
    return ('header:range', value[:80]) if re.match(r'^bytes=(?:(?:\d+)?-(?:\d+)?\s*,?\s*){6}', value, re.I) else None


@rule('invalid_characters', '920', 'Null byte or control characters in the request', 'critical')
def _invalid(ctx):
    raw = ctx.req.raw_uri
    decoded = unquote(unquote(raw))
    m = re.search(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', decoded)
    return ('path', repr(m.group(0))) if m else None


@rule('non_standard_characters', '920', 'Characters outside printable ASCII in the path', 'notice')
def _non_ascii(ctx):
    decoded = unquote(ctx.req.path_raw)
    m = re.search(r'[^\x20-\x7e]', decoded)
    return ('path', repr(m.group(0))) if m else None


@rule('encoding_abuse', '920', 'Broken or IIS-style percent encoding (%u0041, %zz)', 'warning')
def _encoding(ctx):
    m = re.search(r'%u[0-9a-f]{4}|%(?![0-9a-f]{2})', ctx.req.raw_uri, re.I)
    return ('uri', m.group(0)) if m else None


# 921 ---------------------------------------------------------------------------------------
@rule('header_injection', '921', 'Line breaks that smuggle in headers (response splitting)')
def _crlf(ctx):
    # the normalised text has its line breaks collapsed, so look at the decoded request itself
    texts = [('uri', unquote(unquote(ctx.req.raw_uri)))]
    texts += [(f'arg:{n[:40]}', unquote(v)) for n, v in ctx.req.args]
    texts += [(f'header:{k}', unquote(v)) for k, v in ctx.req.headers.items() if k != 'cookie']
    for where, text in texts:
        if CRLF.search(text):
            return where, 'CR/LF + header'
    for n, _v in ctx.req.args:
        if re.search(r'[\n\r]', unquote(n)):
            return f'arg:{n[:40]}', 'CR/LF in a parameter name'
    return None


@rule('request_smuggling', '921', 'Both Content-Length and Transfer-Encoding, or an HTTP request inside a parameter')
def _smuggling(ctx):
    h = ctx.req.headers
    if 'content-length' in h and 'transfer-encoding' in h:
        return 'header:transfer-encoding', 'Content-Length and Transfer-Encoding'
    return _first(_re(r'(?:get|post|put|head|options|delete|trace)\s+\S+\s+http/\d'), ctx.arg_values())


# 930 ---------------------------------------------------------------------------------------
_TRAVERSAL = _re(r'(?:\.\.[/\\])|(?:[/\\]\.\.(?:[/\;]|$))|(?:\.\.;/)|(?:\.{3,}[/\\]{2})|%c0%af|%c1%9c')


@rule('path_traversal', '930', 'Directory traversal (../ in any encoding)')
def _traversal(ctx):
    raw = ctx.req.raw_uri.lower()
    hit = _TRAVERSAL.search(ctx.path)
    if hit:
        return 'path', hit.group(0)
    for where, text in ctx.arg_values():
        hit = _TRAVERSAL.search(text)
        if hit:
            return where, hit.group(0)
    return ('uri', '..') if re.search(r'%2e%2e|%252e|\.\.%5c|\.\.%2f', raw) else None


@rule('os_file_access', '930', 'Operating system files (/etc/passwd, win.ini, /proc/self, ...)')
def _os_files(ctx):
    return _phrase(OS_FILES, [('path', ctx.path)] + list(ctx.arg_texts()))


@rule('restricted_file_access', '930', 'Hidden, backup and configuration files (.git, .env, .htaccess, ...)', 'warning')
def _restricted_files(ctx):
    for entry in RESTRICTED_FILES:
        if entry in ctx.path:
            return 'path', entry
    return None


# 931 ---------------------------------------------------------------------------------------
_RFI_IP = _re(r'^(?:f(?:ile|tps?)|https?|ssh)://(?:\[?[0-9a-f]+:[0-:a-f]+\]?|\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})')
_RFI_TRAILING = _re(r'^(?:file|ftps?|https?).*?\?+$')


@rule('rfi_url_with_address', '931', 'Parameter that is a URL to a raw IP address', 'warning')
def _rfi_ip(ctx):
    return _first(_RFI_IP, ctx.arg_values())


@rule('rfi_url_with_question_mark', '931', 'Parameter that is a URL ending in ? (classic include trick)', 'warning')
def _rfi_q(ctx):
    return _first(_RFI_TRAILING, ctx.arg_values())


# 932 ---------------------------------------------------------------------------------------
_COMMANDS = ('cat|ls|id|whoami|uname|pwd|wget|curl|nc|ncat|netcat|bash|sh|zsh|python[23]?|perl|ruby|php|ping|nslookup|dig|'
             'ifconfig|ipconfig|netstat|ps|kill|rm|cp|mv|chmod|chown|echo|printf|sleep|touch|tee|head|tail|grep|find|env|'
             'printenv|sudo|su|telnet|ssh|scp|ftp|tftp|base64|xxd|nmap|powershell|cmd')
_SEP_COMMAND = _re(r'(?:[;|&`\n]|\$\(|\|\|)\s*(?:/?(?:[\w.-]+/)*)?(?:' + _COMMANDS + r')\b(?:\s+[-/\w"\'$.~*]|\s*$|\s*\)|\s*;)')
_BUILTIN = _re(r'(?:[;|&`\n]|\$\()\s*(?:' + '|'.join(re.escape(b) for b in UNIX_BUILTINS) + r')\s+[\w$"\'`-]')
# a command at the very start of a value is only suspicious when it takes unmistakable arguments
_BARE_COMMAND = _re(r'^\s*(?:wget|curl|nc|ncat|netcat|nmap|powershell|bash|sh|zsh)\s+(?:-|https?://|/)')
_SUBSHELL = _re(r'\$\([^)]{1,80}\)|`[^`]{1,60}`|<\([^)]+\)')
_WINDOWS = _re(r'\bcmd(?:\.exe)?\s*/[ck]\b|\bpowershell(?:\.exe)?\s+-|\bnet\s+(?:user|localgroup|share)\b|%systemroot%|'
               r'c:\\+windows|\bcertutil\s+-|\bbitsadmin\b|\bmshta\b|\bwscript\b|\bcscript\b|\btype\s+%')
_SHELLSHOCK = _re(r'^\s*\(\s*\)\s*\{|\(\s*\)\s*\{\s*[^}]*;\s*\}')


@rule('unix_shell_command', '932', 'Shell command after a separator (; | & $( `) or a known shell path')
def _unix_cmd(ctx):
    texts = list(ctx.arg_texts())
    hit = _first(_SEP_COMMAND, texts) or _first(_BUILTIN, texts) or _first(_BARE_COMMAND, texts)
    if hit:
        return hit
    return _phrase(UNIX_SHELL, [('path', ctx.path)] + texts)


@rule('unix_shell_expression', '932', 'Command substitution: $(...), backticks, <(...)')
def _subshell(ctx):
    return _first(_SUBSHELL, ctx.arg_texts())


@rule('windows_command', '932', 'Windows command line (cmd /c, powershell -, net user, %SYSTEMROOT%)')
def _windows(ctx):
    return _first(_WINDOWS, [('path', ctx.path)] + list(ctx.arg_texts()))


@rule('shellshock', '932', 'Shellshock: () { ... } in a header or parameter (CVE-2014-6271)')
def _shellshock(ctx):
    items = [(f'header:{k}', unquote(v)) for k, v in ctx.req.headers.items()] + list(ctx.arg_values())
    return _first(_SHELLSHOCK, items)


# 933 (PHP) ---------------------------------------------------------------------------------
@rule('php_open_tag', '933', 'PHP open tag <?php')
def _php_tag(ctx):
    return _first(_re(r'<\?(?:php[\s=]|=|xml\s)|\[/?php\]|\{/?php\}'), ctx.injectable())


@rule('php_stream_wrapper', '933', 'PHP stream wrapper (php://input, data://, zip://, phar://)')
def _php_stream(ctx):
    return _first(_re(r'php://(?:std(?:in|out|err)|(?:in|out)put|f(?:d|ilter)|memory|temp)|(?:zip|phar|expect|data)://'), ctx.arg_values())


@rule('php_function_call', '933', 'Call to a dangerous PHP function (eval, system, passthru, ...)')
def _php_func(ctx):
    builtin = ('eval', 'system', 'exec', 'shell_exec', 'passthru', 'popen', 'proc_open', 'assert', 'create_function',
               'call_user_func', 'call_user_func_array', 'unserialize', 'phpinfo', 'file_put_contents', 'move_uploaded_file')
    names = '|'.join(re.escape(f) for f in sorted(set(builtin) | {f for f in PHP_FUNCTIONS if f.isidentifier()}))
    return _first(_re(r'\b(?:' + names + r')\s*\('), ctx.arg_values())


@rule('php_variable', '933', 'PHP superglobal ($_GET, $_SERVER, ...)')
def _php_var(ctx):
    for where, text in ctx.arg_values():
        for var in PHP_VARIABLES:
            if var.lower() in text:
                return where, var
    return None


# 934 ---------------------------------------------------------------------------------------
@rule('template_injection', '934', 'Template or expression injection: {{ }}, ${ }, #{ }, <%= %>, JNDI')
def _template(ctx):
    return _first(_re(r'\{\{[^}]{1,80}\}\}|\$\{[^}]{1,80}\}|#\{[^}]{1,80}\}|<%[=\-@]?|\{%\s*\w+'), ctx.injectable())


@rule('prototype_pollution', '934', 'JavaScript prototype pollution (__proto__, constructor.prototype)')
def _proto(ctx):
    items = list(ctx.arg_values()) + [(w, n) for w, n, _ in ((a[0] + ':name', a[1], a[2]) for a in ctx.args)]
    return _first(_re(r'__proto__|constructor\s*(?:\.|\]?\[)\s*prototype'), items)


@rule('nodejs_injection', '934', 'Server-side JavaScript: eval(, child_process, Function(')
def _node(ctx):
    return _first(_re(r'\beval\s*\(|new\s+function\s*\(|child_process|process\.(?:mainmodule|binding|env)|module\.exports\s*=|require\s*\(\s*[\'"]'), ctx.arg_values())


@rule('ssrf_target', '934', 'Parameter that points at a cloud metadata or internal address (CRS list)')
def _ssrf(ctx):
    return _phrase(SSRF_TARGETS, list(ctx.arg_values()))


# 941 ---------------------------------------------------------------------------------------
def _decoded(items):
    for where, text in items:
        yield where, html.unescape(text)


_EVENTS = ('abort|afterprint|animation(?:start|end|iteration)|auxclick|beforeprint|beforeunload|beforetoggle|begin|blur|'
           'canplay|change|click|contextmenu|copy|cut|dblclick|drag(?:start|end|over|enter|leave)?|drop|end|ended|error|'
           'focus(?:in|out)?|formdata|hashchange|input|invalid|key(?:down|up|press)|load(?:start|eddata|edmetadata)?|message|'
           'mouse(?:down|up|over|out|move|enter|leave)|pagehide|pageshow|paste|pause|play(?:ing)?|pointer(?:down|up|over|'
           'enter|move)|popstate|readystatechange|reset|resize|scroll|search|select|show|submit|toggle|touch(?:start|end|move)|'
           'transitionend|unload|wheel')


@rule('xss_script_tag', '941', 'Script tag')
def _xss_script(ctx):
    return _first(_re(r'<\s*/?\s*script\b'), _decoded(ctx.injectable()))


@rule('xss_event_handler', '941', 'HTML event handler attribute (onerror=, onload=, ...)')
def _xss_events(ctx):
    return _first(_re(r'(?:^|[\s"\'`/;<>])on(?:' + _EVENTS + r')\s*='), _decoded(ctx.injectable()))


@rule('xss_script_uri', '941', 'javascript:, vbscript: or data:text/html URI')
def _xss_uri(ctx):
    return _first(_re(r'\b(?:javascript|vbscript|livescript)\s*:|data:\s*text/html|\bsrcdoc\s*=|expression\s*\('), _decoded(ctx.injectable()))


@rule('xss_html_injection', '941', 'Markup that should not appear in input (<iframe>, <svg>, <img>, <object>, ...)')
def _xss_markup(ctx):
    return _first(_re(r'<\s*(?:iframe|object|embed|applet|svg|math|meta|link|base|body|form|img|video|audio|source|details|'
                      r'marquee|isindex|frameset|style|input|button|textarea|template)\b'), _decoded(ctx.injectable()))


# 942 ---------------------------------------------------------------------------------------
@rule('sqli_union_select', '942', 'UNION SELECT')
def _sqli_union(ctx):
    return _first(_re(r'\bunion\b\s+(?:all\s+|distinct\s+)?select\b'), ctx.injectable())


@rule('sqli_tautology', '942', "Always-true conditions (' or 1=1, and 2=2, ' or 'a'='a)")
def _sqli_taut(ctx):
    pattern = _re(r"\b(?:or|and|xor)\b\s+(\d+)\s*=\s*\1\b|\b(?:or|and)\b\s+\d+\s*=\s*\d+\s*(?:--|#|$)|"
                  r"['\"]\s*(?:or|and|xor)\s*['\"]?\w+['\"]?\s*(?:=|like)\s*['\"]?\w+|"
                  r"['\"]\s*(?:or|and)\s+\d+\s*=\s*\d+|\b(?:or|and)\b\s*\(\s*\d+\s*=\s*\d+\s*\)|\|\|\s*\d+\s*=\s*\d+")
    return _first(pattern, ctx.injectable())


@rule('sqli_time_based', '942', 'Time-delay functions (sleep, benchmark, pg_sleep, waitfor delay)')
def _sqli_time(ctx):
    return _first(_re(r'\b(?:sleep|benchmark|pg_sleep|dbms_lock\.sleep|dbms_pipe\.receive_message)\s*\(|\bwaitfor\s+delay\b'), ctx.injectable())


@rule('sqli_metadata_or_stacked', '942', 'Database internals, stacked queries, file functions (information_schema, ; drop, load_file)')
def _sqli_meta(ctx):
    return _first(_re(r'\binformation_schema\b|\bsysobjects\b|\bsyscolumns\b|\bsqlite_master\b|\bpg_catalog\b|\bxp_cmdshell\b|'
                      r'\bload_file\s*\(|\binto\s+(?:out|dump)file\b|\butl_http\b|\bextractvalue\s*\(|\bupdatexml\s*\(|@@version|'
                      r';\s*(?:drop|alter|truncate|insert|update|delete|create|exec|execute|declare|shutdown|select)\b|'
                      r'\bselect\b\s+[\w*,.@`\'"() ]{1,80}?\s+\bfrom\b\s+[\w`.\[\]]+'), ctx.injectable())


@rule('sqli_quote_comment', '942', "Quote followed by a comment or terminator ('--, '#, ';)", 'warning')
def _sqli_comment(ctx):
    return _first(_re(r"['\"]\s*(?:--|#|/\*|;)\s*(?:$|[\w(])"), ctx.arg_values())


# 943 ---------------------------------------------------------------------------------------
@rule('session_id_in_url', '943', 'Session identifier passed as a parameter (jsessionid, phpsessid, ...)', 'warning')
def _session(ctx):
    pattern = _re(r'^(?:json\.)?(?:jsessionid|phpsessid|aspsessionid|asp\.net_sessionid|session[-_]?id|sid|cfid|cftoken)$')
    for where, name, _value in ctx.args:
        if pattern.match(name):
            return where, name
    return ('path', ';jsessionid') if re.search(r';jsessionid=', ctx.path) else None


# 944 (Java) --------------------------------------------------------------------------------
@rule('java_class_or_jndi', '944', 'Java runtime classes, OGNL or JNDI lookups (Log4Shell)')
def _java(ctx):
    return _first(_re(r'\$\{\s*(?:jndi|ctx|[^}]{0,15}\$\{)|java\.lang\.(?:runtime|processbuilder|class)|ognl|#_memberaccess|'
                      r'%\{\(#|\bclass\.module\.classloader|getruntime\(\)'), ctx.injectable())


# APP ---------------------------------------------------------------------------------------
KNOWN_PREFIXES = ('/api/', '/admin/', '/static/', '/media/', '/files/', '/api-auth/')
# front-end routes and Vite internals, for requests seen at the edge (before the app answers)
EDGE_KNOWN = ('/', '/login', '/documents', '/datarooms', '/analytics', '/settings', '/admin/', '/trash', '/automations',
              '/interactions', '/view/', '/checkout', '/assets/', '/locales/', '/src/', '/@vite/', '/@react-refresh',
              '/@id/', '/node_modules/', '/favicon', '/robots.txt', '/manifest', '/coneshare_logo')


def is_unknown_area(path: str, edge: bool = False) -> bool:
    if path == '/':
        return False
    prefixes = KNOWN_PREFIXES + (EDGE_KNOWN if edge else ())
    return not path.startswith(prefixes)


@rule('unknown_area', 'APP', 'Request for a place that does not exist (404)', 'notice')
def _unknown(ctx):
    return None   # decided by the caller: it needs the response status (or the edge's route list)


# ---------------------------------------------------------------------------------------------
RULE_IDS = [r['id'] for r in RULES]
DEFAULT_DISABLED = [r['id'] for r in RULES if not GROUPS[r['group']]['default_enabled']]
SEVERITY_ORDER = {'critical': 0, 'warning': 1, 'notice': 2}


def catalogue():
    """Groups with their rules, for the settings page."""
    out = []
    for gid, meta in GROUPS.items():
        rules = [{'id': r['id'], 'description': r['description'], 'severity': r['severity']}
                 for r in RULES if r['group'] == gid]
        out.append({**meta, 'rules': rules})
    return out


def evaluate(req: Request, disabled=()):
    """Run every enabled rule; returns [{rule, group, severity, where, matched}] (one per rule)."""
    ctx = _Context(req)
    hits = []
    for r in RULES:
        if r['id'] in disabled or r['id'] == 'unknown_area':
            continue
        try:
            found = r['fn'](ctx)
        except Exception:   # a faulty rule must never stop the others, or the request
            continue
        if found:
            where, matched = found
            hits.append({'rule': r['id'], 'group': r['group'], 'severity': r['severity'],
                         'where': where, 'matched': str(matched)[:80]})
    return hits
