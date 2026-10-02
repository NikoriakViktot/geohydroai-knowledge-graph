"""Paper workbench: one place that builds papers for the consumer repositories.

Inputs come from the paper's own repository (its ``ghai.project.yaml``), every corpus
operation goes through the Knowledge API, and the outputs (manuscript, tables, figures,
bibliography, reviews) are delivered back into that repository, where the human commits.

    python -m src.workbench projects | <project_id> status | init | pull | deliver
    python -m src.workbench.decommission freeze | inventory | verify | gate     # P6

Modules: manifest (ghai.project.yaml), registry (project.project), remote (wsl.exe, tar
streams, remote sha256), delivery (plan, checks, verified writes), steps/ (one per step).
"""
