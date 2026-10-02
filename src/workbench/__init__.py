"""Paper workbench: one place that builds papers for the consumer repositories.

Inputs come from the paper's own repository (its ``ghai.project.yaml``), every corpus
operation goes through the Knowledge API, and the outputs (manuscript, tables, figures,
bibliography, reviews) are delivered back into that repository, where the human commits.

    python -m src.workbench.decommission ...    # P6: freeze, inventory and the deletion gate
"""
