# Governance and protocol lifecycle

Generic organizations keep Relic's lifecycle: work friction creates episodes;
reflection produces wishes, wishes promote proposals, governance approves
changes, and adopted tools or protocols can be used, enforced, amended, or retired.

Start from `configs/custom-governance.yaml`. Choose `approval_mode`, explicit
`approver_members` or `approver_roles`, `quorum`, and
`proposal_review_delay_ticks` in `governance`. Role permissions and decision
visibility are configured in the same section. Adoption and amendment thresholds
control which evaluated proposals qualify; deadlock and retirement settings
control unresolved or obsolete rules.

```yaml
governance:
  approval_mode: agent
  approver_members: [researcher, reviewer]
  quorum: 2
  proposal_review_delay_ticks: 2
protocols:
  initial:
    - id: evidence-first
      name: Evidence before completion
      description: Attach evidence before marking shared work complete.
      trigger: complete_task
      action: require_evidence
      rules: [Record a deliverable and supporting evidence.]
```

Set `protocols.initial: []` to start with no initial rules. Load a protocol package
through `protocols.packages`. Public trace identifies protocol origins as
`initial`, `loaded`, or `emergent`, and records revision and retirement events.

`learning.reflection` controls cadence, per-agent cooldown and salience.
`learning.wish` controls caps and deduplication; `learning.proposal` controls
promotion and caps; `learning.protocol` controls protocol lifecycle parameters.
Top-level learning switches enable profile conditioning, capability learning,
institutionalization, wish extraction, proposal generation, protocol formation,
company skill memory, governance approval, executable workflows, and external
signals. These settings are independent of the paper's B0–B3 condition names.

Inspector's overview shows effective governance and feature switches; its
reflection, proposal, governance, and protocol panels expose safe structural
lineage. Private reflection text is never needed to follow those relationships.
