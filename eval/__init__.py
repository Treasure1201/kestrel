"""Evaluation harness: gold answers, automatic grading, runner, workbook output and review.

The harness talks to the application only through `kestrel.contracts`, `kestrel.errors`,
`kestrel.config` and `kestrel.service.build_service`. Automatic grades are screening
signals and are always labelled as automatic; the workbook's human assessment column
is filled only from a completed human review file.
"""
