# Quant repository boundaries

[中文页面](quant-repo-boundaries.md)

`market-data-platform` is an independent data platform. It produces, governs, and publishes market-data assets, exposing stable contracts, manifests, and quality receipts to downstream systems.

```text
market-data-platform: ingestion, production, PIT, quality, versioning, publication
        ↓ published assets, manifests, receipts, schemas
quant-platform: general backtests, portfolios, risk, costs, capacity, execution simulation
        ↓ public APIs and versioned research artifacts
quant-research: strategies, features, machine learning, experiments, research evidence
        ↓ versioned artifacts
market-intel: reports, dashboards, message delivery, operations entry points
```

`research-workspace` is in sunset transition. It remains for historical reproduction, version composition, cross-repository checks, and migration navigation. New business implementations belong in their target repositories.

## Ownership

`market-data-platform` owns provider access and quotas; raw, standardized, and canonical assets; point-in-time semantics and source lineage; data-quality checks and receipts; manifests, versions, and current pointers; DuckDB queries and published-asset readers. It does not own stock selection, model training, portfolio backtests, or broker execution.

`quant-platform` owns general backtests, portfolio construction, risk, costs, capacity, execution simulation, strategy-independent research interfaces, artifact envelopes, and shared contracts. It consumes assets published by the data platform. Platform code must not depend on provider SDKs, real strategy data, strategy-specific parameters, or private research modules.

`quant-research` owns strategy identity and lifecycle, investment hypotheses, PIT features and labels, machine-learning models and selection, experiment protocols, research results, promotion evidence, private configuration, and strategy-specific research such as cashflow selection and scoring. It uses published data and public platform APIs rather than importing internal data-platform implementations.

## Integration rules

Cross-repository integration uses published assets, dataset manifests, quality receipts, versioned schemas, public Python APIs, and versioned research artifacts. Downstream projects must not depend on an upstream working directory, private cache, internal provider modules, or local absolute paths.

During migration, the old `research-workspace` submodule may support historical reproduction and compatibility migration. Migration adapters must have direct tests, a named owner, and a removal condition.
