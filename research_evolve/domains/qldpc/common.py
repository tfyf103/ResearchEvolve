from __future__ import annotations

import itertools
from math import comb
from typing import Any, Iterable

MAX_BENCHMARK_SIZE = 7


def validate_candidate(candidate: dict[str, Any]) -> tuple[bool, str]:
    size = candidate.get("size")
    if not isinstance(size, int) or isinstance(size, bool):
        return False, "size must be an integer"
    if not 3 <= size <= MAX_BENCHMARK_SIZE:
        return False, f"size must be in [3, {MAX_BENCHMARK_SIZE}] for the v0.2 benchmark"
    for key in ("a_shifts", "b_shifts"):
        shifts = candidate.get(key)
        if not isinstance(shifts, list) or not shifts:
            return False, f"{key} must be a non-empty list"
        if len(shifts) > size:
            return False, f"{key} is longer than the circulant size"
        if any(not isinstance(shift, int) or isinstance(shift, bool) for shift in shifts):
            return False, f"{key} must contain integers"
        if any(shift < 0 or shift >= size for shift in shifts):
            return False, f"{key} entries must be in [0, size)"
        if len(set(shifts)) != len(shifts):
            return False, f"{key} must not contain duplicate shifts"
    representation = candidate.get("representation", "circulant")
    if representation not in {"circulant", "polynomial"}:
        return False, "representation must be circulant or polynomial"
    return True, "ok"


def circulant_rows(size: int, shifts: Iterable[int]) -> list[int]:
    rows: list[int] = []
    normalized = list(shifts)
    for row in range(size):
        bits = 0
        for shift in normalized:
            bits |= 1 << ((row + shift) % size)
        rows.append(bits)
    return rows


def transpose_shifts(size: int, shifts: Iterable[int]) -> list[int]:
    return [(-shift) % size for shift in shifts]


def build_css_rows(candidate: dict[str, Any]) -> tuple[list[int], list[int], int]:
    size = int(candidate["size"])
    a = circulant_rows(size, candidate["a_shifts"])
    b = circulant_rows(size, candidate["b_shifts"])
    at = circulant_rows(size, transpose_shifts(size, candidate["a_shifts"]))
    bt = circulant_rows(size, transpose_shifts(size, candidate["b_shifts"]))
    hx = [a_row | (b_row << size) for a_row, b_row in zip(a, b)]
    hz = [bt_row | (at_row << size) for bt_row, at_row in zip(bt, at)]
    return hx, hz, 2 * size


def css_commutes(hx: Iterable[int], hz: Iterable[int]) -> bool:
    return all(((x & z).bit_count() % 2) == 0 for x in hx for z in hz)


def gf2_basis(rows: Iterable[int]) -> dict[int, int]:
    basis: dict[int, int] = {}
    for row in rows:
        value = int(row)
        while value:
            pivot = value.bit_length() - 1
            if pivot in basis:
                value ^= basis[pivot]
            else:
                basis[pivot] = value
                break
    return basis


def gf2_rank(rows: Iterable[int]) -> int:
    return len(gf2_basis(rows))


def reduce_with_basis(vector: int, basis: dict[int, int]) -> int:
    value = int(vector)
    while value:
        pivot = value.bit_length() - 1
        if pivot not in basis:
            break
        value ^= basis[pivot]
    return value


def in_rowspace(vector: int, rows: Iterable[int]) -> bool:
    return reduce_with_basis(vector, gf2_basis(rows)) == 0


def in_kernel(vector: int, parity_checks: Iterable[int]) -> bool:
    return all(((vector & row).bit_count() % 2) == 0 for row in parity_checks)


def _vectors_of_weight(n: int, weight: int):
    for positions in itertools.combinations(range(n), weight):
        vector = 0
        for position in positions:
            vector |= 1 << position
        yield vector


def logical_distance(parity_checks: list[int], stabilizer_rows: list[int], n: int) -> int | None:
    """Exact small-code distance by increasing-weight enumeration.

    This is intentionally limited to the tiny v0.2 benchmark (n <= 14). It is
    a correctness-oriented reference evaluator, not a production qLDPC
    distance algorithm.
    """

    stabilizer_basis = gf2_basis(stabilizer_rows)
    for weight in range(1, n + 1):
        for vector in _vectors_of_weight(n, weight):
            if in_kernel(vector, parity_checks) and reduce_with_basis(vector, stabilizer_basis) != 0:
                return weight
    return None


def code_parameters(candidate: dict[str, Any]) -> dict[str, float]:
    hx, hz, n = build_css_rows(candidate)
    rank_hx = gf2_rank(hx)
    rank_hz = gf2_rank(hz)
    k = n - rank_hx - rank_hz
    row_weight_x = sum(row.bit_count() for row in hx) / len(hx)
    row_weight_z = sum(row.bit_count() for row in hz) / len(hz)
    return {
        "n": float(n),
        "k": float(k),
        "rate": float(k / n),
        "rank_hx": float(rank_hx),
        "rank_hz": float(rank_hz),
        "row_weight_x": float(row_weight_x),
        "row_weight_z": float(row_weight_z),
    }


def exact_distance_metrics(candidate: dict[str, Any]) -> dict[str, float] | None:
    hx, hz, n = build_css_rows(candidate)
    rank_hx = gf2_rank(hx)
    rank_hz = gf2_rank(hz)
    if n - rank_hx - rank_hz <= 0:
        return None
    dx = logical_distance(hz, hx, n)
    dz = logical_distance(hx, hz, n)
    if dx is None or dz is None:
        return None
    distance = min(dx, dz)
    return {"distance": float(distance), "d_x": float(dx), "d_z": float(dz)}


def search_cost_upper_bound(candidate: dict[str, Any]) -> int:
    n = 2 * int(candidate["size"])
    return 2 * sum(comb(n, weight) for weight in range(1, n + 1))
