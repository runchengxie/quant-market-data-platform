# Historical industry labels

[中文页面](historical-industry-labels.md)

`market_data_platform.industry_history.expand_effective_industry_to_panel_dates` expands industry labels with `effective_date` and optional `end_date` onto panel trading dates. It normalizes security/date columns and configured mappings, performs a backward match by security, and filters dates outside the label's validity interval.

This transformation handles the temporal validity of a data asset; it does not implement strategy selection or model logic. The caller loads the industry file and panel, then passes the result to its feature or portfolio workflow.
