"""Built-in discipline profiles.

These are product defaults, not user memory. Users and projects can copy or
override them in ResearchProfile / ProjectProfile.
"""
from __future__ import annotations

from paperagent.domain.entities import DisciplineProfile


BUILTIN_DISCIPLINES: dict[str, DisciplineProfile] = {
    "management": DisciplineProfile(
        discipline_id="management",
        name="Management / Information Systems",
        keywords=["management", "information systems", "organization"],
        theories=["institutional theory", "resource-based view", "TAM", "UTAUT"],
        methods=["survey", "case study", "SEM", "regression", "interview"],
        quality_checks=["construct_validity", "method_design_alignment"],
        figure_conventions=["theoretical model", "hypothesis path diagram"],
    ),
    "computer_science": DisciplineProfile(
        discipline_id="computer_science",
        name="Computer Science",
        keywords=["algorithm", "system", "benchmark", "model"],
        methods=["benchmark experiment", "ablation", "complexity analysis"],
        datasets=["public benchmark", "custom dataset"],
        quality_checks=["reproducibility", "benchmark_validity"],
        figure_conventions=["system architecture", "pipeline diagram", "metric curves"],
        formula_conventions=["define variables before equations", "state complexity"],
    ),
    "medicine": DisciplineProfile(
        discipline_id="medicine",
        name="Medicine / Clinical Research",
        keywords=["clinical", "patient", "trial", "diagnosis"],
        methods=["RCT", "cohort study", "case-control", "systematic review"],
        quality_checks=["ethics_traceability", "clinical_reporting_standard"],
        figure_conventions=["CONSORT flow diagram", "forest plot", "survival curve"],
    ),
    "education": DisciplineProfile(
        discipline_id="education",
        name="Education",
        keywords=["learning", "teaching", "classroom", "intervention"],
        methods=["quasi-experiment", "survey", "interview", "classroom observation"],
        instruments=["learning scale", "rubric", "observation protocol"],
        quality_checks=["intervention_fidelity", "measurement_validity"],
        figure_conventions=["intervention flow", "learning outcome chart"],
    ),
    "economics": DisciplineProfile(
        discipline_id="economics",
        name="Economics",
        keywords=["econometrics", "policy", "market", "identification"],
        methods=["DID", "IV", "RDD", "panel regression", "structural model"],
        quality_checks=["identification_validity", "robustness_traceability"],
        figure_conventions=["coefficient plot", "event study", "trend comparison"],
        formula_conventions=["state model assumptions", "define error terms"],
    ),
    "materials": DisciplineProfile(
        discipline_id="materials",
        name="Materials Science",
        keywords=["synthesis", "characterization", "performance", "structure-property"],
        methods=["materials synthesis", "characterization", "performance testing"],
        instruments=["XRD", "SEM", "TEM", "FTIR", "Raman"],
        quality_checks=["experimental_repeatability", "figure_claim_alignment"],
        figure_conventions=["spectra", "microscopy panel", "performance curve"],
    ),
}


def list_builtin_disciplines() -> list[DisciplineProfile]:
    return list(BUILTIN_DISCIPLINES.values())


def get_builtin_discipline(discipline_id: str) -> DisciplineProfile | None:
    return BUILTIN_DISCIPLINES.get(discipline_id)
