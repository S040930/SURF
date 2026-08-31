from app.experiment.r23.analysis import (
    AnalysisRow,
    build_report,
    holm_adjust,
    monotonic_pair_accuracy,
    quadratic_weighted_kappa,
    selectivity_index,
)


def _row(label_x2: int, predicted_x2: int, *, cluster: str = "p") -> AnalysisRow:
    return AnalysisRow(
        model_binding_id="m1",
        dimension="content",
        cluster_id=cluster,
        prompt_sha256=cluster,
        input_sha256=f"{cluster}-{label_x2}",
        label_x2=label_x2,
        content_x2=predicted_x2,
        organization_x2=predicted_x2,
        language_x2=predicted_x2,
        word_count=100 + label_x2,
    )


def test_mpa_tie_qwk_holm_and_si_known_values():
    rows = [_row(2, 2), _row(3, 2), _row(4, 4)]
    assert monotonic_pair_accuracy(rows[:2], "content") == 0.5
    assert monotonic_pair_accuracy(rows, "content") == (0.5 + 1 + 1) / 3
    assert quadratic_weighted_kappa(list(range(2, 11)), list(range(2, 11))) == 1
    adjusted = holm_adjust([0.01, 0.04, 0.03])
    assert adjusted == [0.03, 0.06, 0.06]

    organization = [
        AnalysisRow(
            model_binding_id="m1",
            dimension="organization",
            cluster_id="base",
            prompt_sha256="p",
            input_sha256=f"i-{label}",
            label_x2=label,
            content_x2=6,
            organization_x2=label,
            language_x2=6,
            word_count=100,
        )
        for label in range(2, 11)
    ]
    assert selectivity_index(organization) > 0.99


def test_report_is_reproducible_and_marks_language_exploratory():
    rows = []
    for model in ("model-a",):
        for dimension in ("content", "organization", "language"):
            for cluster in range(3):
                for label in range(2, 11):
                    target = label
                    rows.append(
                        AnalysisRow(
                            model_binding_id=model,
                            dimension=dimension,
                            cluster_id=f"{dimension}-{cluster}",
                            prompt_sha256=f"prompt-{cluster}",
                            input_sha256=f"{dimension}-{cluster}-{label}",
                            label_x2=label,
                            content_x2=target if dimension == "content" else 6,
                            organization_x2=(
                                target if dimension == "organization" else 6
                            ),
                            language_x2=target if dimension == "language" else 6,
                            word_count=90 + label,
                        )
                    )
    first = build_report(rows, bootstrap_replicates=50)
    second = build_report(rows, bootstrap_replicates=50)
    assert first == second
    assert len(first["cells"]) == 3
    assert all(len(cell["trend"]) == 9 for cell in first["cells"])
    assert all(
        cell["evidence_level"] == "exploratory"
        for cell in first["cells"]
        if cell["dimension"] == "language"
    )
    assert all(
        abs(item["mean_channel_negative_control_si"]) < 1e-12
        for item in first["organization_selectivity"]
    )
    assert first["stable_sensitivity"] == {
        "content": False,
        "organization": False,
        "language": False,
    }
