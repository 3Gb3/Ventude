"""Gerador de QR Code em Python puro (modo byte/UTF-8, versões 1–40, correção L/M/Q/H).

Segue a norma ISO/IEC 18004: codifica os dados, calcula a correção de erros (Reed–Solomon em GF(256)),
intercala os blocos, desenha os padrões fixos, posiciona os bits em zigue-zague e escolhe a máscara
de menor penalidade. O resultado é uma matriz de booleanos (True = módulo escuro).
"""

from __future__ import annotations

from collections.abc import Callable

# Índice: nível de correção (L, M, Q, H) e versão (o índice 0 não é usado).
ECC_CODEWORDS_PER_BLOCK = (
    (-1, 7, 10, 15, 20, 26, 18, 20, 24, 30, 18, 20, 24, 26, 30, 22, 24, 28, 30, 28, 28,
     28, 28, 30, 30, 26, 28, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30),
    (-1, 10, 16, 26, 18, 24, 16, 18, 22, 22, 26, 30, 22, 22, 24, 24, 28, 28, 26, 26, 26,
     26, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28, 28),
    (-1, 13, 22, 18, 26, 18, 24, 18, 22, 20, 24, 28, 26, 24, 20, 30, 24, 28, 28, 26, 30,
     28, 30, 30, 30, 30, 28, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30),
    (-1, 17, 28, 22, 16, 22, 28, 26, 26, 24, 28, 24, 28, 22, 24, 24, 30, 28, 28, 26, 28,
     30, 24, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30, 30),
)  # fmt: skip
NUM_ERROR_CORRECTION_BLOCKS = (
    (-1, 1, 1, 1, 1, 1, 2, 2, 2, 2, 4, 4, 4, 4, 4, 6, 6, 6, 6, 7, 8,
     8, 9, 9, 10, 12, 12, 12, 13, 14, 15, 16, 17, 18, 19, 19, 20, 21, 22, 24, 25),
    (-1, 1, 1, 1, 2, 2, 4, 4, 4, 5, 5, 5, 8, 9, 9, 10, 10, 11, 13, 14, 16,
     17, 17, 18, 20, 21, 23, 25, 26, 28, 29, 31, 33, 35, 37, 38, 40, 43, 45, 47, 49),
    (-1, 1, 1, 2, 2, 4, 4, 6, 6, 8, 8, 8, 10, 12, 16, 12, 17, 16, 18, 21, 20,
     23, 23, 25, 27, 29, 34, 34, 35, 38, 40, 43, 45, 48, 51, 53, 56, 59, 62, 65, 68),
    (-1, 1, 1, 2, 4, 4, 4, 5, 6, 8, 8, 11, 11, 16, 16, 18, 16, 19, 21, 25, 25,
     25, 34, 30, 32, 35, 37, 40, 42, 45, 48, 51, 54, 57, 60, 63, 66, 70, 74, 77, 81),
)  # fmt: skip
LEVELS = {"L": 0, "M": 1, "Q": 2, "H": 3}
FORMAT_BITS = {0: 1, 1: 0, 2: 3, 3: 2}  # L=01, M=00, Q=11, H=10
MASKS: tuple[Callable[[int, int], int], ...] = (
    lambda x, y: (x + y) % 2,
    lambda x, y: y % 2,
    lambda x, y: x % 3,
    lambda x, y: (x + y) % 3,
    lambda x, y: (x // 3 + y // 2) % 2,
    lambda x, y: x * y % 2 + x * y % 3,
    lambda x, y: (x * y % 2 + x * y % 3) % 2,
    lambda x, y: ((x + y) % 2 + x * y % 3) % 2,
)


class QrError(ValueError):
    pass


# ---------- Reed–Solomon ----------
def rs_multiply(x: int, y: int) -> int:
    z = 0
    for i in reversed(range(8)):
        z = (z << 1) ^ ((z >> 7) * 0x11D)
        z ^= ((y >> i) & 1) * x
    return z


def rs_divisor(degree: int) -> list[int]:
    result = [0] * (degree - 1) + [1]
    root = 1
    for _ in range(degree):
        for j in range(degree):
            result[j] = rs_multiply(result[j], root)
            if j + 1 < degree:
                result[j] ^= result[j + 1]
        root = rs_multiply(root, 0x02)
    return result


def rs_remainder(data: list[int], divisor: list[int]) -> list[int]:
    result = [0] * len(divisor)
    for byte in data:
        factor = byte ^ result.pop(0)
        result.append(0)
        for i, coef in enumerate(divisor):
            result[i] ^= rs_multiply(coef, factor)
    return result


# ---------- capacidade ----------
def raw_data_modules(version: int) -> int:
    result = (16 * version + 128) * version + 64
    if version >= 2:
        align = version // 7 + 2
        result -= (25 * align - 10) * align - 55
        if version >= 7:
            result -= 36
    return result


def data_codewords(version: int, level: int) -> int:
    return (
        raw_data_modules(version) // 8
        - ECC_CODEWORDS_PER_BLOCK[level][version] * NUM_ERROR_CORRECTION_BLOCKS[level][version]
    )


def char_count_bits(version: int) -> int:
    return 8 if version <= 9 else 16


def choose_version(size: int, level: int) -> int:
    for version in range(1, 41):
        if 4 + char_count_bits(version) + 8 * size <= data_codewords(version, level) * 8:
            return version
    raise QrError("Texto grande demais para um QR Code")


def format_bits(level: int, mask: int) -> int:
    data = FORMAT_BITS[level] << 3 | mask
    rem = data
    for _ in range(10):
        rem = (rem << 1) ^ ((rem >> 9) * 0x537)
    return (data << 10 | rem) ^ 0x5412


def version_bits(version: int) -> int:
    rem = version
    for _ in range(12):
        rem = (rem << 1) ^ ((rem >> 11) * 0x1F25)
    return version << 12 | rem


def alignment_positions(version: int) -> list[int]:
    if version == 1:
        return []
    size = version * 4 + 17
    count = version // 7 + 2
    step = (version * 8 + count * 3 + 5) // (count * 4 - 4) * 2
    return list(reversed([size - 7 - i * step for i in range(count - 1)] + [6]))


# ---------- dados ----------
def encode_codewords(data: bytes, version: int, level: int) -> list[int]:
    bits: list[int] = []

    def put(value: int, length: int) -> None:
        bits.extend((value >> i) & 1 for i in reversed(range(length)))

    put(0b0100, 4)  # modo byte
    put(len(data), char_count_bits(version))
    for byte in data:
        put(byte, 8)
    capacity = data_codewords(version, level) * 8
    put(0, min(4, capacity - len(bits)))
    put(0, (-len(bits)) % 8)
    pad = 0xEC
    while len(bits) < capacity:
        put(pad, 8)
        pad ^= 0xEC ^ 0x11
    return [int("".join(map(str, bits[i : i + 8])), 2) for i in range(0, len(bits), 8)]


def add_ecc_and_interleave(data: list[int], version: int, level: int) -> list[int]:
    blocks_count = NUM_ERROR_CORRECTION_BLOCKS[level][version]
    ecc_len = ECC_CODEWORDS_PER_BLOCK[level][version]
    raw = raw_data_modules(version) // 8
    short_blocks = blocks_count - raw % blocks_count
    short_len = raw // blocks_count
    divisor = rs_divisor(ecc_len)
    blocks: list[list[int]] = []
    k = 0
    for i in range(blocks_count):
        chunk = data[k : k + short_len - ecc_len + (0 if i < short_blocks else 1)]
        k += len(chunk)
        ecc = rs_remainder(chunk, divisor)
        if i < short_blocks:
            chunk = [*chunk, 0]
        blocks.append(chunk + ecc)
    result: list[int] = []
    for i in range(len(blocks[0])):
        for j, block in enumerate(blocks):
            if i != short_len - ecc_len or j >= short_blocks:  # pula o enchimento dos blocos curtos
                result.append(block[i])
    return result


# ---------- matriz ----------
class _Matrix:
    def __init__(self, version: int) -> None:
        self.version = version
        self.size = version * 4 + 17
        self.dark = [[False] * self.size for _ in range(self.size)]
        self.fixed = [[False] * self.size for _ in range(self.size)]

    def set_fixed(self, x: int, y: int, dark: bool) -> None:
        self.dark[y][x] = dark
        self.fixed[y][x] = True

    def draw_patterns(self, level: int) -> None:
        size = self.size
        for i in range(size):
            self.set_fixed(6, i, i % 2 == 0)
            self.set_fixed(i, 6, i % 2 == 0)
        for cx, cy in ((3, 3), (size - 4, 3), (3, size - 4)):
            for dy in range(-4, 5):
                for dx in range(-4, 5):
                    x, y = cx + dx, cy + dy
                    if 0 <= x < size and 0 <= y < size:
                        self.set_fixed(x, y, max(abs(dx), abs(dy)) not in (2, 4))
        positions = alignment_positions(self.version)
        last = len(positions) - 1
        for i, ax in enumerate(positions):
            for j, ay in enumerate(positions):
                if (i, j) in ((0, 0), (0, last), (last, 0)):
                    continue
                for dy in range(-2, 3):
                    for dx in range(-2, 3):
                        self.set_fixed(ax + dx, ay + dy, max(abs(dx), abs(dy)) != 1)
        self.draw_format(level, 0)  # reserva a área (o valor certo entra depois da máscara)
        if self.version >= 7:
            bits = version_bits(self.version)
            for i in range(18):
                bit = bool((bits >> i) & 1)
                a, b = size - 11 + i % 3, i // 3
                self.set_fixed(a, b, bit)
                self.set_fixed(b, a, bit)

    def draw_format(self, level: int, mask: int) -> None:
        bits = format_bits(level, mask)
        size = self.size

        def bit(i: int) -> bool:
            return bool((bits >> i) & 1)

        for i in range(6):
            self.set_fixed(8, i, bit(i))
        self.set_fixed(8, 7, bit(6))
        self.set_fixed(8, 8, bit(7))
        self.set_fixed(7, 8, bit(8))
        for i in range(9, 15):
            self.set_fixed(14 - i, 8, bit(i))
        for i in range(8):
            self.set_fixed(size - 1 - i, 8, bit(i))
        for i in range(8, 15):
            self.set_fixed(8, size - 15 + i, bit(i))
        self.set_fixed(8, size - 8, True)  # módulo sempre escuro

    def zigzag(self) -> list[tuple[int, int]]:
        """Ordem das posições livres onde os bits de dados entram."""
        order: list[tuple[int, int]] = []
        size = self.size
        right = size - 1
        while right >= 1:
            if right == 6:
                right = 5
            for vert in range(size):
                for j in range(2):
                    x = right - j
                    upward = ((right + 1) & 2) == 0
                    y = size - 1 - vert if upward else vert
                    if not self.fixed[y][x]:
                        order.append((x, y))
            right -= 2
        return order

    def draw_data(self, codewords: list[int]) -> None:
        bits = [(byte >> (7 - i)) & 1 for byte in codewords for i in range(8)]
        for index, (x, y) in enumerate(self.zigzag()):
            self.dark[y][x] = bool(bits[index]) if index < len(bits) else False

    def apply_mask(self, mask: int) -> None:
        rule = MASKS[mask]
        for y in range(self.size):
            for x in range(self.size):
                if not self.fixed[y][x] and rule(x, y) == 0:
                    self.dark[y][x] = not self.dark[y][x]


def penalty(dark: list[list[bool]]) -> int:
    """Penalidade da norma (regras 1 a 4): quanto menor, mais fácil de ler."""
    size = len(dark)
    score = 0
    lines = [row[:] for row in dark] + [[dark[y][x] for y in range(size)] for x in range(size)]
    pattern_a = [True, False, True, True, True, False, True, False, False, False, False]
    pattern_b = pattern_a[::-1]
    for line in lines:
        run, color = 0, None
        for module in line:
            if module == color:
                run += 1
                if run == 5:
                    score += 3
                elif run > 5:
                    score += 1
            else:
                color, run = module, 1
        for i in range(size - 10):
            window = line[i : i + 11]
            if window == pattern_a or window == pattern_b:
                score += 40
    for y in range(size - 1):
        for x in range(size - 1):
            c = dark[y][x]
            if c == dark[y][x + 1] == dark[y + 1][x] == dark[y + 1][x + 1]:
                score += 3
    total = sum(sum(row) for row in dark)
    k = (abs(total * 20 - size * size * 10) + size * size - 1) // (size * size) - 1
    return score + max(0, k) * 10


def encode(text: str | bytes, level: str = "M", mask: int | None = None) -> list[list[bool]]:
    """Matriz do QR Code (sem a borda clara; desenhe pelo menos 4 módulos de margem)."""
    data = text.encode("utf-8") if isinstance(text, str) else bytes(text)
    lvl = LEVELS[level]
    version = choose_version(len(data), lvl)
    for better in (LEVELS["Q"], LEVELS["H"]):  # sobe a correção se ainda couber na mesma versão
        if (
            better > lvl
            and 4 + char_count_bits(version) + 8 * len(data) <= data_codewords(version, better) * 8
        ):
            lvl = better
    codewords = add_ecc_and_interleave(encode_codewords(data, version, lvl), version, lvl)
    matrix = _Matrix(version)
    matrix.draw_patterns(lvl)
    matrix.draw_data(codewords)
    best, best_score = 0, None
    choices = range(8) if mask is None else (mask,)
    for candidate in choices:
        matrix.apply_mask(candidate)
        matrix.draw_format(lvl, candidate)
        score = penalty(matrix.dark)
        if best_score is None or score < best_score:
            best, best_score = candidate, score
        matrix.apply_mask(candidate)  # desfaz (XOR)
    matrix.apply_mask(best)
    matrix.draw_format(lvl, best)
    return matrix.dark
