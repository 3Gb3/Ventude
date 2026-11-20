"""Calculadora da barra de busca: '1450 * 5.85', '2^10', 'raiz(2) * 3' (com segurança, sem eval).

A expressão vira uma árvore (ast) e só números, + - * / // % ** ^, parênteses e algumas funções
(sqrt/raiz, sin/sen, cos, tan, log, ln, abs, round, floor, ceil, pi, e) são aceitos. Nada é executado.
"""

from __future__ import annotations

import ast
import math
import operator
import re

FUNCTIONS = {
    "sqrt": math.sqrt,
    "raiz": math.sqrt,
    "sin": math.sin,
    "sen": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "tg": math.tan,
    "log": math.log10,
    "ln": math.log,
    "abs": abs,
    "round": round,
    "floor": math.floor,
    "ceil": math.ceil,
}
CONSTANTS = {"pi": math.pi, "e": math.e}
OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
}
ALLOWED = re.compile(r"^[\d\s.,+\-*/%^()×÷a-zA-Z]+$")


class CalcError(ValueError):
    pass


def normalize(text: str) -> tuple[str, bool]:
    """Troca ×, ÷ e ^ e entende vírgula decimal ('5,85'). Devolve (expressão, usou vírgula?)."""
    expr = text.strip().replace("×", "*").replace("÷", "/").replace("^", "**")
    comma = bool(re.search(r"\d,\d", expr))
    if comma:
        expr = re.sub(r"(?<=\d)\.(?=\d{3}(?:\D|$))", "", expr)  # 1.450,85 -> 1450,85
        expr = re.sub(r"(?<=\d),(?=\d)", ".", expr)
    return expr, comma


def looks_like_math(text: str) -> bool:
    text = text.strip()
    if not text or not ALLOWED.match(text) or not re.search(r"\d", text):
        return False
    words = re.findall(r"[a-zA-Z]+", text)
    if any(w.lower() not in FUNCTIONS and w.lower() not in CONSTANTS for w in words):
        return False
    return bool(re.search(r"[+\-*/%^×÷]|[a-zA-Z]+\s*\(", text.lstrip("+-")))


def _eval(node: ast.AST) -> float:
    if (
        isinstance(node, ast.Constant)
        and isinstance(node.value, int | float)
        and not isinstance(node.value, bool)
    ):
        return node.value
    if isinstance(node, ast.Name) and node.id.lower() in CONSTANTS:
        return CONSTANTS[node.id.lower()]
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.UAdd | ast.USub):
        value = _eval(node.operand)
        return value if isinstance(node.op, ast.UAdd) else -value
    if isinstance(node, ast.BinOp):
        left, right = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Pow):
            if abs(right) > 10_000 or (abs(left) > 1 and abs(right) * math.log10(abs(left)) > 300):
                raise CalcError("Número grande demais")
            return left**right
        op = OPERATORS.get(type(node.op))
        if op is None:
            raise CalcError("Operação não suportada")
        try:
            return op(left, right)
        except ZeroDivisionError:
            raise CalcError("Divisão por zero") from None
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and not node.keywords:
        fn = FUNCTIONS.get(node.func.id.lower())
        if fn is None or not 1 <= len(node.args) <= 2:
            raise CalcError("Função não suportada")
        try:
            return fn(*(_eval(arg) for arg in node.args))
        except (ValueError, TypeError, OverflowError):
            raise CalcError("Conta inválida") from None
    raise CalcError("Expressão não suportada")


def evaluate(text: str) -> float:
    expr, _comma = normalize(text)
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError:
        raise CalcError("Expressão incompleta") from None
    value = _eval(tree.body)
    if isinstance(value, complex) or not math.isfinite(float(value)):
        raise CalcError("Resultado inválido")
    return float(value)


def format_result(value: float, comma: bool = False) -> str:
    if value.is_integer() and abs(value) < 1e15:
        text = str(int(value))
    else:
        text = f"{value:.10g}"
    return text.replace(".", ",") if comma else text


def calculate(text: str) -> str | None:
    """Resultado formatado (no mesmo separador decimal digitado) ou None se não for conta."""
    if not looks_like_math(text):
        return None
    try:
        value = evaluate(text)
    except CalcError:
        return None
    return format_result(value, normalize(text)[1])
