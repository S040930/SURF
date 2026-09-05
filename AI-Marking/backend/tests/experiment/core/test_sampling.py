"""Unit tests for deterministic stratified Hamilton sampling."""

import pytest

from app.experiment.core.sampling import (
    SampleItem,
    hamilton_allocate,
    normalized_prompt,
    select_retest,
    stable_key,
    stratified_select,
)


def _items(counts: dict[int, int], *, forced_stratum: int | None = None) -> list[SampleItem]:
    items = []
    index = 0
    for stratum, count in counts.items():
        for _ in range(count):
            items.append(
                SampleItem(
                    key=f"{index:06d}",
                    stratum=stratum,
                    cell=f"prompt-{index % 3}",
                    forced=stratum == forced_stratum and index % 2 == 0,
                )
            )
            index += 1
    return items


def test_hamilton_allocation_is_exact_and_capped():
    sizes = {"a": 5, "b": 3, "c": 1}
    allocation = hamilton_allocate(sizes, 5)
    assert sum(allocation.values()) == 5
    assert all(allocation[key] <= size for key, size in sizes.items())
    assert hamilton_allocate(sizes, 9) == sizes
    assert hamilton_allocate({"x": 2, "y": 2}, 0) == {"x": 0, "y": 0}


def test_hamilton_allocation_is_deterministic():
    sizes = {f"cell-{i}": size for i, size in enumerate([7, 1, 4, 3, 9, 2])}
    assert hamilton_allocate(sizes, 11) == hamilton_allocate(sizes, 11)


def test_stratified_select_is_rebuildable_and_respects_quotas():
    items = _items({1: 20, 2: 14, 3: 8})
    quotas = {1: 8, 2: 5, 3: 2}
    first = stratified_select(items, quota_by_stratum=quotas, seed="seed-1")
    second = stratified_select(items, quota_by_stratum=quotas, seed="seed-1")
    assert [selection.key for selection in first] == [
        selection.key for selection in second
    ]
    counts: dict[int, int] = {}
    for selection in first:
        counts[selection.stratum] = counts.get(selection.stratum, 0) + 1
    assert counts == quotas


def test_forced_items_always_enter_with_unit_weight():
    items = _items({1: 12}, forced_stratum=1)
    # Half of the stratum-1 items are forced (even keys).
    forced_keys = {item.key for item in items if item.forced}
    assert forced_keys
    selections = stratified_select(items, quota_by_stratum={1: 6}, seed="s")
    selected_keys = {selection.key for selection in selections}
    assert forced_keys <= selected_keys
    for selection in selections:
        if selection.key in forced_keys:
            assert selection.forced
            assert selection.inclusion_probability == 1.0
            assert selection.design_weight == 1.0


def test_inclusion_probabilities_sum_to_quota_per_stratum():
    items = _items({1: 30, 2: 12})
    quotas = {1: 10, 2: 4}
    selections = stratified_select(items, quota_by_stratum=quotas, seed="s")
    for stratum, quota in quotas.items():
        members = [item for item in items if item.stratum == stratum]
        selected_in_stratum = [
            selection for selection in selections if selection.stratum == stratum
        ]
        # First-order design property: summing pi = k_cell / N_cell over every
        # member of each cell contributes exactly k_cell, so the stratum total
        # equals its quota.
        total_pi = 0.0
        for cell_key in {selection.cell_key for selection in selected_in_stratum}:
            n_cell = sum(
                1
                for item in members
                if stable_key("cell", item.cell)[:32] == cell_key
            )
            k_cell = sum(
                1
                for selection in selected_in_stratum
                if selection.cell_key == cell_key
            )
            assert k_cell <= n_cell
            total_pi += k_cell
        assert total_pi == quota


def test_overfull_quota_or_forced_overflow_is_rejected():
    items = _items({1: 5})
    with pytest.raises(ValueError):
        stratified_select(items, quota_by_stratum={1: 6}, seed="s")
    forced = [SampleItem(key=str(i), stratum=1, cell="c", forced=True) for i in range(4)]
    with pytest.raises(ValueError):
        stratified_select(forced, quota_by_stratum={1: 2}, seed="s")
    with pytest.raises(ValueError):
        stratified_select(_items({1: 5, 2: 5}), quota_by_stratum={1: 2}, seed="s")


def test_retest_selection_is_stratified_and_rebuildable():
    items = _items({1: 10, 2: 10})
    selections = stratified_select(
        items, quota_by_stratum={1: 4, 2: 4}, seed="s"
    )
    first = select_retest(selections, quota_by_stratum={1: 1, 2: 2}, seed="s")
    second = select_retest(selections, quota_by_stratum={1: 1, 2: 2}, seed="s")
    assert first == second
    assert len(first) == 3
    strata = {selection.stratum for selection in selections if selection.key in first}
    assert strata == {1, 2}


def test_normalized_prompt_and_stable_key():
    assert normalized_prompt("  Hello   World \n") == "Hello World"
    assert stable_key("a", "b") == stable_key("a", "b")
    assert stable_key("a", "b") != stable_key("ab")
