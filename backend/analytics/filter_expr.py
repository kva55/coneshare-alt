"""A small display-filter language in the spirit of Wireshark's, for the interaction log.

    event == "print_attempt" && email contains "guest"
    attempt && !(datastore == "Legal") || ip startswith "10."
    event in {print_attempt save_attempt devtools_key} and time >= -24h

* comparison:  ``field == value``, ``!=``, ``contains``, ``startswith``, ``>``, ``>=``, ``<``, ``<=``,
  ``field in {a b c}``
* logic:       ``&&`` / ``and``, ``||`` / ``or``, ``!`` / ``not``, parentheses
* a bare boolean field (``attempt``) stands on its own
* values: ``"quoted text"``, ``'quoted'`` or a bare word (``guest@lab.local``, ``10.0.0.1``, ``-24h``)

This module only parses (into a small tuple tree) and validates field names; turning the tree into
a database query is done in ``interactions.py`` with a fixed whitelist, so a filter can never
reach anything but those fields. No regular expressions are accepted from users.
"""
import re

FIELDS = ('event', 'category', 'source', 'email', 'ip', 'datastore', 'file', 'link', 'time', 'attempt')
BOOLEAN_FIELDS = ('attempt',)
TEXT_OPS = ('==', '!=', 'contains', 'startswith')
ORDER_OPS = ('>', '>=', '<', '<=')
MAX_LENGTH = 500
MAX_NODES = 40


class FilterError(ValueError):
    def __init__(self, message, position=None):
        super().__init__(message)
        self.message = message
        self.position = position


_TOKEN = re.compile(r'''
    (?P<ws>\s+)
  | (?P<op>==|!=|>=|<=|&&|\|\||>|<|!|\(|\)|\{|\})
  | (?P<string>"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')
  | (?P<word>[^\s(){}"'!=<>&|]+)
''', re.X)
_WORD_OPS = {'and': '&&', 'or': '||', 'not': '!', 'contains': 'contains', 'startswith': 'startswith', 'in': 'in'}


def tokenize(text):
    if len(text) > MAX_LENGTH:
        raise FilterError(f'The filter is too long (at most {MAX_LENGTH} characters).')
    tokens, pos = [], 0
    while pos < len(text):
        match = _TOKEN.match(text, pos)
        if not match:
            raise FilterError(f'Unexpected character {text[pos]!r}.', pos)
        kind, value = match.lastgroup, match.group()
        if kind == 'string':
            value = re.sub(r'\\(.)', r'\1', value[1:-1])
            tokens.append(('value', value, pos))
        elif kind == 'word':
            word_op = _WORD_OPS.get(value.lower())
            tokens.append(('op', word_op, pos) if word_op else ('word', value, pos))
        elif kind == 'op':
            tokens.append(('op', value, pos))
        pos = match.end()
    return tokens


class _Parser:
    def __init__(self, tokens):
        self.tokens, self.i, self.nodes = tokens, 0, 0

    def peek(self):
        return self.tokens[self.i] if self.i < len(self.tokens) else (None, None, None)

    def take(self):
        token = self.peek()
        self.i += 1
        return token

    def node(self):
        self.nodes += 1
        if self.nodes > MAX_NODES:
            raise FilterError('The filter is too complicated.')

    def accept_op(self, *ops):
        kind, value, _ = self.peek()
        if kind == 'op' and value in ops:
            self.i += 1
            return value
        return None

    def parse(self):
        if not self.tokens:
            return None
        tree = self.or_()
        if self.peek()[0] is not None:
            raise FilterError(f'Unexpected {self.peek()[1]!r}.', self.peek()[2])
        return tree

    def or_(self):
        left = self.and_()
        while self.accept_op('||'):
            self.node()
            left = ('or', left, self.and_())
        return left

    def and_(self):
        left = self.not_()
        while self.accept_op('&&'):
            self.node()
            left = ('and', left, self.not_())
        return left

    def not_(self):
        if self.accept_op('!'):
            self.node()
            return ('not', self.not_())
        return self.atom()

    def atom(self):
        if self.accept_op('('):
            inner = self.or_()
            if not self.accept_op(')'):
                raise FilterError("Missing ')'.", self.peek()[2])
            return inner
        kind, value, pos = self.take()
        if kind != 'word':
            raise FilterError('Expected a field name.' if kind is not None else 'The filter ends too early.', pos)
        field = value.lower()
        if field not in FIELDS:
            raise FilterError(f"Unknown field '{value}'. Fields: {', '.join(FIELDS)}.", pos)
        self.node()
        op = self.accept_op('==', '!=', 'contains', 'startswith', '>', '>=', '<', '<=', 'in')
        if op is None:
            if field in BOOLEAN_FIELDS:
                return ('flag', field)
            raise FilterError(f"'{field}' needs a comparison, e.g. {field} == \"value\".", self.peek()[2])
        if field in BOOLEAN_FIELDS:
            raise FilterError(f"'{field}' stands on its own (use ! to negate it).", pos)
        if op in ORDER_OPS and field != 'time':
            raise FilterError(f"'{op}' only works with time.", pos)
        if field == 'time' and op in ('contains', 'startswith', 'in'):
            raise FilterError(f"time takes ==, !=, >, >=, < or <=.", pos)
        if op == 'in':
            return ('in', field, self.value_list())
        return ('cmp', field, op, self.value())

    def value(self):
        kind, value, pos = self.take()
        if kind in ('value', 'word'):
            return value
        raise FilterError('Expected a value.', pos)

    def value_list(self):
        if not self.accept_op('{'):
            raise FilterError("Expected '{' after 'in'.", self.peek()[2])
        values = []
        while not self.accept_op('}'):
            kind, value, pos = self.take()
            if kind not in ('value', 'word'):
                raise FilterError("Missing '}'." if kind is None else 'Expected a value.', pos)
            values.append(value)
            if len(values) > 30:
                raise FilterError('At most 30 values in a list.', pos)
        if not values:
            raise FilterError('The list is empty.')
        return values


def parse(text):
    """Parse ``text`` into a tree, or raise FilterError. Empty text gives None (match everything)."""
    return _Parser(tokenize(text or '')).parse()
