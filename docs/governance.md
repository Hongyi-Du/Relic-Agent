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

Packages are ordinary JSON or YAML files, resolved relative to the organization
configuration. Every run exports `protocols.json` in this reusable form:

```yaml
protocols:
  - id: evidence-first
    name: Evidence before completion
    definition:
      affected_actions: [complete_task]
      required_fields: [supporting-evidence]
      enforcement_action: block
      enforcement_rule: Require supporting-evidence before completing the task.
```

Load that file with `protocols: {packages: [previous-run/protocols.json]}`.
Invalid packages fail validation before a run starts. Configure adoption and
amendment thresholds in `governance`; aliases in `protocols` and
`learning.protocol` normalize to the same value, and conflicting values are
rejected. This lets a configured threshold below the default take effect too.

The generic executable gate supports organization-wide `scope: org` (also
`organization`), `scope: shared tasks` (tasks with `team` or `public`
visibility), or `scope: task:<existing-task-id>`. It does not infer team
membership from a generic `team` scope. `affected_agents` may name
member IDs or `role:<role>`; an empty list applies to everyone in scope.
`trigger_condition` supports an exact generic action or the documented action
aliases, including `before completing a task`. `enforcement_action: block`
rejects a missing task artifact/evidence/review, while `notify` records the
violation without blocking the action. These evidence checks currently run for
task completion and document/PR review, not arbitrary tool or messaging calls.
`required_steps` may carry human-readable checklist prose, but that prose is
not an executable predicate. Unsupported trigger, scope, enforcement mode, or
member selector is rejected on load rather than silently widened to the whole
organization. The public Inspector shows the published rule fields and
enforcement events, not private deliberation text.

`learning.reflection` controls cadence, per-agent cooldown and salience.
`learning.wish` controls caps and deduplication; `learning.proposal` controls
promotion and caps; `learning.protocol` controls protocol lifecycle parameters.
Top-level learning switches enable profile conditioning, capability learning,
institutionalization, wish extraction, proposal generation, protocol formation,
company skill memory, governance approval, executable workflows, and external
signals. These settings are independent of the paper's B0–B3 condition names.

Quorum is exact: if `quorum: 2`, two distinct members from the configured
approver pool must approve; a shorter pool cannot lower that requirement. An
approver also needs the `approve_protocol` permission, supplied on the agent or
through `governance.role_permissions`. Approval does not bypass
`proposal_review_delay_ticks` (protocol changes also use the protocol review
delay), and `approval_mode: agent` leaves decisions under review until those
agents act. `deadlock_behavior` accepts `wait`, `escalate`, or `reject`;
`wait` preserves the review, `escalate` records a governance event, and
`reject` closes the stale proposal. Retirement accepts `review` or `keep`:
`review` sends retirement through the ordinary proposal, evaluator, quorum, and
delay path, while `keep` disables retirement. `learning.parameters` supports
`deadlock_ticks` for the deadlock window.

Inspector's overview shows effective governance and feature switches; its
reflection, proposal, governance, and protocol panels expose safe structural
lineage. Private reflection text is never needed to follow those relationships.
